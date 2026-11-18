"""The workflow behind the Start button.

For each start-up, at the same time:
  1. read the uploaded documents        (reader model, citations on)
  2. search the public web              (reader model, web search tool; it is given public details only)
then
  3. build a structured profile         (judge model, from the numbered evidence of steps 1 and 2)
     and, at the same time, map what the company needs to the team's workstreams (reader model, same evidence)
  4. check every fact against its source (code)
  5. scheme checks, score, options      (code, from data/policy.json)
and once for the batch
  6. compare and write the briefing     (judge model, wording only)
  7. up to three plans to choose from   (code)

Every step writes an event, so the screen shows the work as it happens and a recorded run can be replayed.
"""
from __future__ import annotations

import json
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

from . import checks, config, docs, llm, rules, schemas, store
from .fields import FIELD_BY_KEY

TAG_RX = re.compile(r"^\[(COMPANY|FUNDING|MARKET|COMPETITOR|NOTE)\]\s*(.*)$", re.I | re.S)
RECORDING = "sample_batch.json"
SIGN_OFF = "{{reviewer}}"  # the screen puts the reviewer's own name here when it shows or copies a draft


def prompt(name: str, **values: Any) -> str:
    text = (config.PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def count(n: int, word: str, plural: str = "") -> str:
    """'1 plan', '3 plans': the progress lines are read aloud in a demo, so they are plain English."""
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def emit(run_id: str, lane: str, step: str, status: str, text: str, data: dict[str, Any] | None = None) -> None:
    store.add_event(run_id, lane, step, status, text, data)


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return url
    return host[4:] if host.startswith("www.") else host


# ------------------------------------------------------------------ evidence from cited text

def cited_lines(blocks: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Join the model's text blocks, split into lines, and give each line the citations that fall inside it."""
    full = ""
    spans: list[tuple[int, int, list[dict[str, Any]]]] = []
    for b in blocks:
        if b.get("type") != "text":
            continue
        text = b.get("text", "") or ""
        start = len(full)
        full += text
        if b.get("citations"):
            spans.append((start, len(full), b["citations"]))
    out: list[tuple[str, list[dict[str, Any]]]] = []
    pos = 0
    for raw in full.split("\n"):
        ls, le = pos, pos + len(raw)
        pos = le + 1
        line = re.sub(r"^[\s\-\*•\d\.\)]*(?=\[|[A-Za-z])", "", raw).strip() if raw.strip() else ""
        if not line:
            continue
        cites: list[dict[str, Any]] = []
        for s, e, cs in spans:
            if s < le and e > ls or (s == e == le):
                cites += cs
        out.append((line, cites))
    return out


def _dedupe(quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for q in quotes:
        key = (q.get("quote"), q.get("url"), q.get("doc_id"), q.get("page"))
        if key not in seen:
            seen.add(key)
            out.append(q)
    return out


# ------------------------------------------------------------------ step 1: documents

def read_documents(run_id: str, s: dict[str, Any], options: dict[str, Any], usage: llm.Usage) -> dict[str, Any]:
    sid = s["id"]
    metas = s.get("docs", [])
    if not metas:
        emit(run_id, sid, "docs", "skip", "No documents uploaded.")
        return {"evidence": [], "notes": [], "audit": {"documents": 0, "dropped": 0}}
    emit(run_id, sid, "docs", "start", "Reading " + ", ".join(docs.describe(m) for m in metas) + ".")
    try:
        content: list[dict[str, Any]] = [docs.to_block(m) for m in metas]
        content.append({"type": "text", "text": f"Company: {s['name']}. List the facts from these documents, one per line."})
        out = llm.run("doc_reader", options["reader_model"], prompt("doc_reader"), content, usage=usage,
                      effort=config.EFFORT_DOCS, max_tokens=config.MAX_TOKENS_READER)
    except (llm.LLMError, docs.DocError) as e:
        emit(run_id, sid, "docs", "error", f"Documents could not be read: {e}")
        return {"evidence": [], "notes": [], "audit": {"documents": len(metas), "dropped": 0, "error": str(e)}}
    evidence, notes = [], []

    def quotes_of(cites: list[dict[str, Any]]) -> list[dict[str, Any]]:
        quotes = []
        for c in cites:
            idx = c.get("document_index")
            if not isinstance(idx, int) or not 0 <= idx < len(metas):
                continue
            meta = metas[idx]
            page = c.get("start_page_number")
            quotes.append({"quote": docs.clean_quote(c.get("cited_text") or ""), "doc_id": meta["id"], "doc": meta["name"],
                           "page": page, "label": meta["name"] + (f" p.{page}" if page else "")})
        return _dedupe(quotes)

    def take(blocks: list[dict[str, Any]]) -> list[str]:
        """Keep the lines that carry a citation; return the ones that do not."""
        loose = []
        for line, cites in cited_lines(blocks):
            if line.upper().startswith("[NOTE]"):
                notes.append(line[6:].strip())
                continue
            quotes = quotes_of(cites)
            if quotes:
                evidence.append({"kind": "doc", "topic": "DOCUMENT", "claim": line, "quotes": quotes})
            else:
                loose.append(line)
        return loose

    loose = take(out["blocks"])
    rescued = 0
    if loose:
        # A reader sometimes states a true fact without citing it. Rather than lose the fact or trust it
        # unseen, the desk (1) looks for it in the text itself and (2) asks once more for the passage.
        text_docs = [(m, docs.extract_passages(docs.resolve(m))) for m in metas if m["kind"] != "pdf"]
        still = []
        for line in loose:
            found = checks.find_passage(line, text_docs)
            if found:
                meta, passage = found
                evidence.append({"kind": "doc", "topic": "DOCUMENT", "claim": line, "matched_by": "code",
                                 "quotes": [{"quote": docs.clean_quote(passage), "doc_id": meta["id"], "doc": meta["name"],
                                             "page": None, "label": meta["name"]}]})
                rescued += 1
            else:
                still.append(line)
        # First the reader model is asked again; whatever it still cannot cite goes to the judge model.
        askers = [options["reader_model"]] + ([options["judge_model"]] if options["judge_model"] != options["reader_model"] else [])
        for attempt, model in enumerate(askers):
            if not still:
                break
            emit(run_id, sid, "docs", "info",
                 f"{count(len(still), 'line')} came back with no source; asking {'the judge model ' if attempt else ''}for the passage behind each.")
            try:
                ask = ("These statements were written without a citation. For each one the documents support, repeat it on "
                       "its own line and cite the passage. Leave out any the documents do not support.\n\n"
                       + "\n".join(f"- {x}" for x in still))
                again = llm.run("doc_reader_repair", model, prompt("doc_reader"), content[:-1] + [{"type": "text", "text": ask}],
                                usage=usage, effort=config.EFFORT_DOCS, max_tokens=config.MAX_TOKENS_READER)
                before = len(evidence)
                take(again["blocks"])
                repaired = [e["claim"] for e in evidence[before:]]
                rescued += len(repaired)
                # what is still unsourced stays dropped
                still = [line for line in still if not any(checks.same_statement(line, r) for r in repaired)]
            except llm.LLMError:
                break
        loose = still
    emit(run_id, sid, "docs", "done",
         f"{len(evidence)} facts, each tied to a quoted passage"
         + (f"; {count(len(loose), 'line')} with no source dropped." if loose else "."),
         {"facts": len(evidence), "dropped": len(loose), "rescued": rescued})
    return {"evidence": evidence, "notes": notes,
            "audit": {"documents": len(metas), "dropped": len(loose), "dropped_lines": loose, "rescued": rescued,
                      "truncated": out["truncated"]}}


# ------------------------------------------------------------------ step 2: public web

def web_brief(s: dict[str, Any]) -> str:
    """What the web researcher is told. Public details only: nothing from the uploaded documents goes in here."""
    return (f"Company: {s['name']}\n"
            f"Website: {s.get('website') or 'not given'}\n"
            f"What it does: {s.get('one_liner') or 'not given'}\n"
            f"Country: {s.get('country') or 'not given'}")


def research_web(run_id: str, s: dict[str, Any], options: dict[str, Any], usage: llm.Usage) -> dict[str, Any]:
    sid = s["id"]
    if not options.get("web", True):
        emit(run_id, sid, "web", "skip", "Web research is switched off for this run.")
        return {"evidence": [], "notes": [], "audit": {"searches": [], "sources": [], "dropped": 0}}
    max_searches = int(options.get("max_searches", config.MAX_SEARCHES))
    emit(run_id, sid, "web", "start", f"Searching the public web (up to {max_searches} searches). Only the name, website and one-line description are sent.")
    searches: list[str] = []
    sources: list[dict[str, str]] = []

    def on_event(ev: dict[str, Any]) -> None:
        if ev["kind"] == "search":
            searches.append(ev["query"])
            emit(run_id, sid, "web", "info", f"Search: {ev['query']}", {"query": ev["query"]})
        elif ev["kind"] == "results":
            names = []
            for r in ev["sources"]:
                d = domain_of(r["url"])
                sources.append({"title": r["title"], "url": r["url"], "domain": d})
                if d not in names:
                    names.append(d)
            emit(run_id, sid, "web", "info", f"{ev['count']} results: " + ", ".join(names[:6]) + ("..." if len(names) > 6 else ""))
        elif ev["kind"] == "search_error":
            emit(run_id, sid, "web", "warn", f"A search failed ({ev['code']}).")

    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches}]
    system = prompt("web_researcher", max_searches=max_searches)
    try:
        out = llm.run("web_researcher", options["reader_model"], system, web_brief(s), usage=usage, tools=tools,
                      effort=config.EFFORT_WEB, max_tokens=config.MAX_TOKENS_READER, on_event=on_event)
    except llm.LLMError as e:
        emit(run_id, sid, "web", "error", f"Web research failed: {e}")
        return {"evidence": [], "notes": [], "audit": {"searches": searches, "sources": sources, "dropped": 0, "error": str(e)}}
    evidence, notes = [], []

    def take(blocks: list[dict[str, Any]]) -> list[str]:
        """Keep the tagged findings that carry a citation; return the ones that do not."""
        loose = []
        for line, cites in cited_lines(blocks):
            m = TAG_RX.match(line)
            if not m:
                continue  # connecting text between findings
            tag, text = m.group(1).upper(), m.group(2).strip()
            if tag == "NOTE":
                if text not in notes:
                    notes.append(text)
                continue
            quotes = [{"quote": docs.clean_quote(c.get("cited_text") or ""), "url": c.get("url", ""), "title": c.get("title", ""),
                       "label": domain_of(c.get("url", ""))}
                      for c in cites if c.get("type") == "web_search_result_location" and c.get("url")]
            if quotes:
                evidence.append({"kind": "web", "topic": tag, "claim": text, "quotes": _dedupe(quotes)})
            else:
                loose.append(f"[{tag}] {text}")
        return loose

    loose = take(out["blocks"])
    rescued = 0
    if loose:
        # The reader named its sources in words and attached no citation, so nothing ties those lines to a page.
        # It is asked once more, in the same conversation (the search results are still in front of it), to repeat
        # each line with its citation. A line that still has none is dropped and listed on screen.
        emit(run_id, sid, "web", "info", f"{count(len(loose), 'finding')} came back with no citation; asking for the page behind each.")
        ask = ("These findings were written without a citation, so the desk cannot use them. Repeat each one on its own line, "
               "keeping its tag, and cite the search result it comes from. Do not search again unless a line cannot be cited "
               "from the results above. Leave out any line the results do not support.\n\n" + "\n".join(loose))
        try:
            again = llm.run("web_researcher_repair", options["reader_model"], system, ask, usage=usage,
                            tools=[{**tools[0], "max_uses": 1}], effort=config.EFFORT_WEB, max_tokens=config.MAX_TOKENS_READER,
                            on_event=on_event,
                            history=[{"role": "user", "content": web_brief(s)}, {"role": "assistant", "content": out["raw"]}])
            before = len(evidence)
            take(again["blocks"])
            repaired = [e["claim"] for e in evidence[before:]]
            rescued = len(repaired)
            loose = [line for line in loose if not any(checks.same_statement(line, r) for r in repaired)]
        except llm.LLMError as e:
            emit(run_id, sid, "web", "warn", f"The second pass failed ({e}); the uncited findings stay dropped.")
    dropped, dropped_lines = len(loose), loose
    seen, unique_sources = set(), []
    for src in sources:
        if src["url"] not in seen:
            seen.add(src["url"])
            unique_sources.append(src)
    emit(run_id, sid, "web", "done",
         f"{len(evidence)} findings from {len({q['url'] for e in evidence for q in e['quotes']})} cited pages"
         + (f"; {count(dropped, 'finding')} with no source dropped." if dropped else "."),
         {"findings": len(evidence), "dropped": dropped})
    return {"evidence": evidence, "notes": notes,
            "audit": {"searches": searches, "sources": unique_sources, "dropped": dropped, "dropped_lines": dropped_lines,
                      "rescued": rescued, "truncated": out["truncated"]}}


# ------------------------------------------------------------------ step 3: profile

def evidence_listing(evidence: list[dict[str, Any]], notes: list[str]) -> str:
    def block(kind: str, title: str) -> str:
        rows = []
        for e in evidence:
            if e["kind"] != kind:
                continue
            where = "; ".join(sorted({q["label"] for q in e["quotes"]}))
            quotes = " / ".join('"' + q["quote"].replace("\n", " ")[:600] + '"' for q in e["quotes"][:3])
            tag = f"[{e['topic']}] " if kind == "web" else ""
            rows.append(f"[{e['id']}] {tag}({where}) Claim: {e['claim']}\n      Quote: {quotes}")
        return f"{title}\n" + ("\n".join(rows) if rows else "(none)")

    text = block("doc", "EVIDENCE FROM THE COMPANY'S UPLOADED DOCUMENTS") + "\n\n" + block("web", "EVIDENCE FROM THE PUBLIC WEB")
    if notes:
        text += "\n\nNOTES FROM THE RESEARCH\n" + "\n".join(f"- {n}" for n in notes)
    return text


def build_profile(run_id: str, s: dict[str, Any], evidence: list[dict[str, Any]], notes: list[str],
                  options: dict[str, Any], usage: llm.Usage, policy: dict[str, Any]) -> dict[str, Any]:
    sid = s["id"]
    emit(run_id, sid, "profile", "start", f"Building the profile from {len(evidence)} pieces of evidence.")
    criteria = "\n".join(f"- {c['key']}: {c['label']}. {c['basis']}\n    " + "; ".join(c.get("anchors", []))
                         for c in policy["criteria"])
    user = (f"Start-up: {s['name']}\nWebsite: {s.get('website') or 'not given'}\n"
            f"Today's date: {config.today().isoformat()}\n\n{evidence_listing(evidence, notes)}")
    system = prompt("extractor", criteria=criteria, allowed=schemas.schema_notes(policy))

    def part(name: str) -> dict[str, Any]:
        make_schema, instruction = schemas.PARTS[name]
        # the lists are plain reading, so they run at the lighter effort; the facts and ratings are judgement
        effort = config.EFFORT_READER if name == "lists" else options.get("judge_effort", config.EFFORT_JUDGE)
        out = llm.run("extractor_" + name, options["judge_model"], system, user + "\n\n" + instruction, usage=usage,
                      schema=make_schema(), effort=effort, max_tokens=config.MAX_TOKENS_JUDGE)
        if out["truncated"]:
            raise llm.LLMError("The profile was cut off at the token limit.")
        return llm.parse_json(out["text"])

    # The profile is asked for in two parts at the same time: one schema for all of it is too large
    # a grammar for the API, and two smaller answers arrive sooner than one long one.
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_core, f_lists = pool.submit(part, "core"), pool.submit(part, "lists")
        merged = {**f_core.result(), **f_lists.result()}
    profile = schemas.normalise(merged, policy)
    emit(run_id, sid, "profile", "done",
         f"{count(len(profile.get('facts', [])), 'field')} filled, {count(len(profile.get('competitors', [])), 'competitor')}, "
         f"{count(len(profile.get('products', [])), 'product')}.")
    return profile


# ------------------------------------------------------------------ step 3b: what the company needs from the team

def who_to_bring(needs: list[dict[str, Any]], limit: int = 3) -> list[str]:
    """The kinds of people worth having in a first meeting: who could help with the top needs, each once."""
    out: list[str] = []
    for n in needs:
        who = n.get("who_could_help", "").strip()
        if who and who.lower() not in (x.lower() for x in out):
            out.append(who)
    return out[:limit]


def map_support(run_id: str | None, s: dict[str, Any], evidence: list[dict[str, Any]], notes: list[str],
                options: dict[str, Any], usage: llm.Usage, policy: dict[str, Any]) -> dict[str, Any]:
    """Read the same evidence a second way: not 'is it investable' but 'what would it need from us'."""
    sid = s["id"]

    def say(status: str, text: str) -> None:
        if run_id:
            emit(run_id, sid, "support", status, text)

    workstreams = policy.get("workstreams", [])
    if not workstreams:
        return {"needs": [], "who_to_bring": [], "dropped": 0}
    say("start", "Mapping what the company needs to the team's workstreams.")
    headings = "\n".join(f"- {w['key']}: {w['label']} ({w['covers']})" for w in workstreams)
    user = f"Start-up: {s['name']}\n\n{evidence_listing(evidence, notes)}"
    try:
        out = llm.run("support_mapper", options.get("support_model") or options["reader_model"],
                      prompt("support_mapper", workstreams=headings), user, usage=usage,
                      schema=schemas.support_schema(), effort=config.EFFORT_DOCS, max_tokens=config.MAX_TOKENS_READER)
        needs = schemas.normalise_support(llm.parse_json(out["text"]), policy)
    except llm.LLMError as e:
        say("error", f"The needs could not be mapped: {e}")
        return {"needs": [], "who_to_bring": [], "dropped": 0, "error": str(e)}
    kept, dropped = checks.verify_support(needs, {e["id"]: e for e in evidence})
    order = {w["key"]: i for i, w in enumerate(workstreams)}
    kept.sort(key=lambda n: order.get(n["workstream"], 99))
    say("done", (f"{count(len(kept), 'need')} across {count(len({n['workstream'] for n in kept}), 'workstream')}" if kept
                 else "Nothing in the evidence names a need from the team")
                + (f"; {dropped} with no source dropped." if dropped else "."))
    return {"needs": kept, "who_to_bring": who_to_bring(kept), "dropped": dropped}


# ------------------------------------------------------------------ one start-up, start to finish

def process_startup(run_id: str, s: dict[str, Any], options: dict[str, Any], usage: llm.Usage,
                    policy: dict[str, Any]) -> dict[str, Any]:
    sid = s["id"]
    base = {"id": sid, "name": s["name"], "website": s.get("website", ""), "one_liner_input": s.get("one_liner", ""),
            "docs": [{k: m.get(k) for k in ("id", "name", "kind", "size", "pages", "chars")} for m in s.get("docs", [])]}
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_docs = pool.submit(read_documents, run_id, s, options, usage)
            f_web = pool.submit(research_web, run_id, s, options, usage)
            from_docs, from_web = f_docs.result(), f_web.result()
        evidence = []
        for n, e in enumerate(from_docs["evidence"], 1):
            evidence.append({"id": f"D{n}", **e})
        for n, e in enumerate(from_web["evidence"], 1):
            evidence.append({"id": f"W{n}", **e})
        notes = from_docs["notes"] + from_web["notes"]
        audit = {"docs": from_docs["audit"], "web": from_web["audit"]}
        if not evidence:
            emit(run_id, sid, "profile", "error", "No evidence was found in documents or on the web, so there is nothing to profile.")
            return {**base, "status": "no_evidence", "notes": notes, "audit": audit, "evidence": []}

        # The profile and the needs are two readings of the same evidence, so they run at the same time.
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_profile = pool.submit(build_profile, run_id, s, evidence, notes, options, usage, policy)
            f_support = pool.submit(map_support, run_id, s, evidence, notes, options, usage, policy)
            profile, support = f_profile.result(), f_support.result()

        emit(run_id, sid, "check", "start", "Checking every fact against its quoted source (code, not AI).")
        by_id = {e["id"]: e for e in evidence}
        counts = checks.verify_profile(profile, by_id)
        audit["checks"] = counts
        emit(run_id, sid, "check", "done",
             f"{counts['quote']} shown in the quote, {counts['cited']} cited, {counts['check']} flagged, "
             f"{counts['unsupported']} dropped for having no source.", counts)

        gates = rules.evaluate_gates(profile, policy)
        scores = rules.score(profile, None, policy)
        cov = rules.coverage(profile)
        meeting = rules.meeting_recommendation(gates, scores["total"], policy, cov, scores)
        sizing = rules.size(profile, gates, scores["total"], cov["value"], policy, decided=meeting["recommend"] is not None)
        return {**base, "status": "ok", "profile": profile, "evidence": evidence, "notes": notes, "audit": audit,
                "gates": gates, "scores": scores, "coverage": cov, "share_of_wallet": rules.share_of_wallet(profile),
                "meeting": meeting, "sizing": sizing, "support": support, "narrative": {}}
    except llm.LLMError as e:
        emit(run_id, sid, "profile", "error", str(e))
        return {**base, "status": "failed", "error": str(e), "evidence": [], "notes": []}
    except Exception as e:  # one start-up failing must not take the batch down
        emit(run_id, sid, "profile", "error", f"Unexpected error: {type(e).__name__}: {e}")
        return {**base, "status": "failed", "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:],
                "evidence": [], "notes": []}


# ------------------------------------------------------------------ step 6: compare and brief

def comparison_payload(results: list[dict[str, Any]], plans: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for r in results:
        if r["status"] != "ok":
            continue
        p = r["profile"]
        facts = rules.fact_map(p)
        opts = {o["key"]: o for o in r["sizing"]["options"]}
        rec = opts.get(r["sizing"]["recommended"], r["sizing"]["options"][-1])
        rows.append({
            "id": r["id"], "name": r["name"], "what_it_does": p.get("one_liner", ""), "sector": p.get("sector_domain", ""),
            "facts": {FIELD_BY_KEY[k]["label"]: (f["value_text"] + (f" ({f['note']})" if f.get("note") else ""))
                      for k, f in facts.items() if k in FIELD_BY_KEY},
            "team": [f"{t['name']}, {t['role']}: {t['background']}" for t in p.get("team", [])],
            "products": [f"{x['name']} ({x.get('maturity', 'unknown')}): {x['description']}" for x in p.get("products", [])],
            "customers": [f"{c['name']} ({c['relationship']}): {c['detail']}" for c in p.get("customers", [])],
            "share_of_wallet": [f"{w.get('customer', '')}: {w['pct']}% of its {w.get('category', 'category')} spend"
                                for w in r["share_of_wallet"] if w.get("pct") is not None],
            "competitors": [f"{c['name']} ({c['based_in']}): {c['how_startup_differs']}" for c in p.get("competitors", [])],
            "market_estimates": [f"{m['source']}: {m['figure']} ({m['scope']})" for m in p.get("market", [])],
            "risks": [x["risk"] for x in p.get("risks", [])],
            "needs_from_the_team": [f"{n['workstream_label']}: {n['need']}" for n in r.get("support", {}).get("needs", [])],
            "scheme_checks": [{"check": g["label"], "status": g["status"], "detail": g["detail"]} for g in r["gates"]],
            "score_out_of_100": r["scores"]["total"],
            "ratings": [{"criterion": c["label"], "score_1_to_5": c["score"], "reason": c["reason"]} for c in r["scores"]["criteria"]],
            "evidence_coverage_pct": round(r["coverage"]["value"] * 100),
            "missing": [m["label"] for m in r["coverage"]["missing"]],
            "first_meeting": {"recommended": {True: "yes", False: "no"}.get(r["meeting"]["recommend"], "no call: too little evidence, get documents first"),
                              "reasons": r["meeting"]["reasons"]},
            "criteria_rated": f"{r['scores'].get('rated_n', len(r['scores']['criteria']))} of {len(r['scores']['criteria'])}",
            "investment": {"recommended_option": rec["label"], "amount": rules.money(rec["amount"]) if rec["amount"] is not None else "not sized",
                           "why": r["sizing"]["why"],
                           "options": [{"option": o["label"], "amount": rules.money(o["amount"]) if o["amount"] is not None else "not sized",
                                        "basis": o["basis"]} for o in r["sizing"]["options"]]},
        })
    return {"startups": rows,
            "plans": [{"plan": pl["key"], "label": pl["label"], "total": rules.money(pl["total"]),
                       "items": [{"name": i["name"], "first_meeting": {True: "yes", False: "no"}.get(i["meeting"], "get documents first"),
                                  "capital": i["option_label"], "amount": rules.money(i["amount"]) if i["amount"] is not None else "not sized"}
                                 for i in pl["items"]]} for pl in plans]}


def fallback_narrative(r: dict[str, Any]) -> dict[str, Any]:
    return {"summary": r["profile"].get("one_liner", ""), "why_meet": [x for x in r["meeting"]["reasons"] if r["meeting"]["recommend"]],
            "why_not": [x for x in r["meeting"]["reasons"] if r["meeting"]["recommend"] is False] + [x["risk"] for x in r["profile"].get("risks", [])][:3],
            "meeting_questions": [m["question"] for m in r["coverage"]["missing"] if m["question"]][:6],
            "revisit_triggers": [], "written_by": "rules"}


def compare(run_id: str, results: list[dict[str, Any]], plans: list[dict[str, Any]], options: dict[str, Any],
            usage: llm.Usage) -> dict[str, Any]:
    ok = [r for r in results if r["status"] == "ok"]
    payload = comparison_payload(results, plans)
    emit(run_id, "all", "compare", "start", f"Comparing {count(len(ok), 'start-up')} and writing the briefing.")
    comparison = {"headline": "", "what_separates_them": [], "flags": []}
    try:
        out = llm.run("comparer", options["judge_model"], prompt("comparer"), json.dumps(payload, ensure_ascii=False, indent=1),
                      usage=usage, schema=schemas.comparison_schema([r["id"] for r in ok], [p["key"] for p in plans]),
                      effort=options.get("judge_effort", config.EFFORT_JUDGE), max_tokens=config.MAX_TOKENS_JUDGE)
        written = llm.parse_json(out["text"])
    except llm.LLMError as e:
        emit(run_id, "all", "compare", "warn", f"The briefing could not be written ({e}); the rules-based text is shown instead.")
        for r in ok:
            r["narrative"] = fallback_narrative(r)
        return comparison

    allowed = json.dumps(payload, ensure_ascii=False)
    flags: list[str] = []

    def vet(text: str, where: str) -> str:
        loose = checks.ungrounded_numbers(text, allowed)
        if loose:
            flags.append(f"{where}: {'figure' if len(loose) == 1 else 'figures'} {', '.join(loose)} "
                         f"{'is' if len(loose) == 1 else 'are'} not in the verified data.")
        return text

    comparison["headline"] = vet(written.get("headline", ""), "Headline")
    comparison["what_separates_them"] = [vet(x, "Comparison") for x in written.get("what_separates_them", [])][:5]
    by_id = {x["id"]: x for x in written.get("startups", [])}
    for r in ok:
        w = by_id.get(r["id"])
        if not w:
            r["narrative"] = fallback_narrative(r)
            continue
        r["narrative"] = {
            "summary": vet(w.get("summary", ""), r["name"]),
            "why_meet": [vet(x, r["name"]) for x in w.get("why_meet", [])][:3],
            "why_not": [vet(x, r["name"]) for x in w.get("why_not", [])][:3],
            "meeting_questions": [vet(x, r["name"]) for x in w.get("meeting_questions", [])][:6]
                                 or fallback_narrative(r)["meeting_questions"],
            "revisit_triggers": [vet(x, r["name"]) for x in w.get("revisit_triggers", [])][:3],
            "written_by": "model",
        }
    given = written.get("plan_rationales", [])
    rationale = {str(x.get("plan", "")).strip().lower(): x.get("rationale", "") for x in given}
    for n, p in enumerate(plans):
        text = rationale.get(p["key"]) or rationale.get(p["label"].lower()) or (given[n].get("rationale", "") if n < len(given) and len(given) == len(plans) else "")
        p["rationale"] = vet(text, "Plan")
    comparison["flags"] = flags
    emit(run_id, "all", "compare", "done",
         "Briefing written." + (f" {count(len(flags), 'sentence')} flagged for a figure that is not in the verified data." if flags else
                               " Every figure in it traces to the verified data."))
    return comparison


# ------------------------------------------------------------------ meeting pack (templates, no AI)

def meeting_pack(r: dict[str, Any]) -> dict[str, Any]:
    name = r["name"]
    questions = r.get("narrative", {}).get("meeting_questions", []) or [m["question"] for m in r["coverage"]["missing"]][:6]
    open_checks = [g["label"] for g in r["gates"] if g["status"] == "unknown"]
    agenda = [
        "5 min: introductions and what the team wants from the meeting",
        "10 min: product, technology and the IP behind it",
        "10 min: customers, traction and this round",
        "5 min: open questions and next steps",
    ]
    # Addressed to the chief executive or founder when the profile names one; the screen signs it with the reviewer's name.
    lead = next((t["name"] for t in r["profile"].get("team", [])
                 if t.get("status") in ("quote", "cited") and re.search(r"chief executive|\bceo\b|founder", t.get("role", ""), re.I)), "")
    greeting = f"Dear {lead}," if lead else "Hello,"
    invite = (f"Subject: First meeting with {name}\n\n"
              f"{greeting}\n\n"
              f"Thank you for sharing the materials on {name}. We would like to arrange a 30-minute first meeting with our team. "
              f"Could you offer two or three times over the next two weeks?\n\n"
              f"To make the time count, it would help to cover:\n"
              + "\n".join(f"- {q}" for q in questions[:4])
              + "\n\nKind regards,\n" + SIGN_OFF)
    hard = [g for g in r["gates"] if g["hard"] and g["status"] == "fail"]
    if hard:
        detail = hard[0]["detail"]
        reasons = [detail[:1].lower() + detail[1:] + " This is a requirement of the scheme we invest under."]
    elif r["meeting"]["recommend"] is False:
        reasons = ["what we could find does not yet meet our bar for a first meeting."]
    else:
        reasons = []  # the reviewer is passing against the app's read: the reason is theirs to write
    triggers = [t.strip().rstrip(".") for t in r.get("narrative", {}).get("revisit_triggers", []) if t.strip()]
    decline = (f"Subject: {name}\n\n"
               f"{greeting}\n\n"
               f"Thank you for sharing the materials on {name}, and for your time. We will not be taking this forward at this stage."
               + (f" The main reason: {reasons[0]}" if reasons else "")
               + ("\n\nWe would be glad to look again if:\n" + "\n".join(f"- {t}" for t in triggers) if triggers else "")
               + "\n\nKind regards,\n" + SIGN_OFF)
    # When too little was found to make a call, the useful next step is a request for exactly what is missing.
    missing = [m["question"] for m in r["coverage"]["missing"] if m.get("question")][:8]
    request = (f"Subject: {name}: a few details before we meet\n\n"
               f"{greeting}\n\n"
               f"Thank you for getting in touch about {name}. Before we set up a first meeting, could you share your latest deck "
               f"and, where the deck does not cover them, answers to these?\n\n"
               + "\n".join(f"- {m}" for m in missing)
               + "\n\nKind regards,\n" + SIGN_OFF) if missing else ""
    return {"agenda": agenda, "questions": questions, "open_checks": open_checks, "invite_draft": invite, "decline_draft": decline,
            "request_draft": request, "who_to_bring": r.get("support", {}).get("who_to_bring", [])}


# ------------------------------------------------------------------ the run

def execute(run_id: str) -> None:
    run = store.get_run(run_id)
    if run is None:
        return
    batch, options = run["batch"], run["options"]
    usage = llm.Usage()
    t0 = time.time()
    try:
        policy = rules.load_policy(refresh=True)
        startups = [s for s in batch["startups"] if s.get("name", "").strip()]
        if not startups:
            raise llm.LLMError("Add at least one start-up before pressing Start.")
        emit(run_id, "all", "run", "start",
             f"Screening {count(len(startups), 'start-up')}. Reader: {options['reader_model']}. Judge: {options['judge_model']}.")
        with ThreadPoolExecutor(max_workers=len(startups)) as pool:
            results = list(pool.map(lambda s: process_startup(run_id, s, options, usage, policy), startups))
        ok = [r for r in results if r["status"] == "ok"]
        if not ok:
            raise llm.LLMError("None of the start-ups could be profiled. " + "; ".join(r.get("error", r["status"]) for r in results))
        plans = rules.build_plans(results)
        comparison = compare(run_id, results, plans, options, usage)
        for r in ok:
            r["pack"] = meeting_pack(r)
        ranking = [r["id"] for r in sorted(ok, key=lambda r: r["scores"]["total"], reverse=True)]
        emit(run_id, "all", "plans", "done", f"{count(len(plans), 'plan')} to choose from. Ranking by score: {', '.join(ranking)}.")
        result = {"startups": results, "comparison": comparison, "plans": plans, "ranking": ranking, "policy": policy,
                  "duration_s": round(time.time() - t0, 1), "today": config.today().isoformat()}
        store.set_run(run_id, status="done", finished_at=store.now(), usage=usage.to_dict(), result=result)
        emit(run_id, "all", "run", "done",
             f"Done in {result['duration_s']:.0f} s. {usage.searches} web searches, {usage.calls} model calls, about US${usage.usd:.2f}.")
        if batch.get("sample") and run["mode"] == "live":
            save_recording(run_id)
    except llm.LLMError as e:
        store.set_run(run_id, status="failed", finished_at=store.now(), usage=usage.to_dict(), error=str(e))
        emit(run_id, "all", "run", "error", str(e))
    except Exception as e:
        store.set_run(run_id, status="failed", finished_at=store.now(), usage=usage.to_dict(),
                      error=f"{type(e).__name__}: {e}")
        emit(run_id, "all", "run", "error", f"Unexpected error: {type(e).__name__}: {e}")


# ------------------------------------------------------------------ recorded run (fallback when there is no network or key)

def save_recording(run_id: str) -> None:
    run = store.get_run(run_id)
    if not run or run["status"] != "done":
        return
    config.RECORDED_DIR.mkdir(parents=True, exist_ok=True)
    body = {"recorded_at": run["finished_at"], "run_id": run_id, "options": run["options"], "usage": run["usage"],
            "batch": run["batch"], "events": store.events_after(run_id, 0), "result": run["result"]}
    (config.RECORDED_DIR / RECORDING).write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")


def load_recording() -> dict[str, Any] | None:
    path = config.RECORDED_DIR / RECORDING
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def replay(run_id: str, speed: float = 6.0, max_gap: float = 1.2) -> None:
    """Play the recorded run back through the same event stream, faster than it happened."""
    rec = load_recording()
    if rec is None:
        store.set_run(run_id, status="failed", finished_at=store.now(), error="No recorded run is stored yet.")
        emit(run_id, "all", "run", "error", "No recorded run is stored yet.")
        return
    try:
        previous = None
        for ev in rec["events"]:
            if previous is not None:
                time.sleep(max(0.03, min(max_gap, (ev["ts"] - previous) / speed)))
            previous = ev["ts"]
            store.add_event(run_id, ev["lane"], ev["step"], ev["status"], ev["text"], ev.get("data"))
        result = dict(rec["result"])
        result["recorded"] = {"recorded_at": rec["recorded_at"], "options": rec["options"], "usage": rec["usage"]}
        store.set_run(run_id, status="done", finished_at=store.now(), usage=rec["usage"], result=result)
    except Exception as e:
        store.set_run(run_id, status="failed", finished_at=store.now(), error=f"{type(e).__name__}: {e}")
        emit(run_id, "all", "run", "error", f"Replay failed: {type(e).__name__}: {e}")
