"""HTTP API and static files. Local only: it binds to 127.0.0.1."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, config, docs, llm, pipeline, rules, store
from .fields import FIELDS

app = FastAPI(title="Startup Screening Desk", version=__version__, docs_url=None, redoc_url=None)
LETTERS = "ABCDEF"
SAMPLE_NOTE = "Sample batch: the three companies, their people, investors, figures and documents are invented for this demo."


def code_stamp() -> float:
    """When the Python files behind this server last changed. A running server keeps the code it started with,
    so files newer than the start mean the screen is showing an older version than the one on disk."""
    here = Path(__file__).resolve().parent
    files = list(here.glob("*.py")) + [here.parent / "run_server.py"]
    return max((f.stat().st_mtime for f in files if f.exists()), default=0.0)


STARTED_WITH = code_stamp()


# ------------------------------------------------------------------ helpers

def blank_startup(letter: str) -> dict[str, Any]:
    return {"id": letter, "name": "", "website": "", "one_liner": "", "country": "", "docs": []}


def need_batch(batch_id: str) -> dict[str, Any]:
    batch = store.get_batch(batch_id)
    if batch is None:
        raise HTTPException(404, "Batch not found.")
    return batch


def need_startup(batch: dict[str, Any], sid: str) -> dict[str, Any]:
    for s in batch["startups"]:
        if s["id"] == sid:
            return s
    raise HTTPException(404, "Start-up not found in this batch.")


def need_run(run_id: str, with_result: bool = True) -> dict[str, Any]:
    run = store.get_run(run_id, with_result)
    if run is None:
        raise HTTPException(404, "Run not found.")
    return run


def decision_state(run: dict[str, Any]) -> dict[str, Any]:
    """The reviewer's current choices: the latest decision per start-up and kind."""
    rows = store.decisions(run["id"])
    state: dict[str, Any] = {"plan": None, "startups": {}}
    for d in rows:
        if d["kind"] == "plan":
            state["plan"] = d["value"]
        else:
            state["startups"].setdefault(d["startup_id"], {})[d["kind"]] = {
                "value": d["value"], "label": d["label"], "amount": d["amount"], "overrode": bool(d["overrode"]),
                "ts": d["ts"], "by": d["by"]}
    return {"log": rows, "state": state}


# ------------------------------------------------------------------ status and policy

@app.get("/api/status")
def status() -> dict[str, Any]:
    rec = pipeline.load_recording()
    policy = rules.load_policy()
    return {
        "version": __version__,
        "live": llm.available(),
        "models": config.MODEL_CHOICES,
        "defaults": {"judge_model": config.JUDGE_MODEL, "reader_model": config.READER_MODEL,
                     "max_searches": config.MAX_SEARCHES, "web": True},
        "recording": None if rec is None else {
            "recorded_at": rec["recorded_at"], "usd": rec["usage"].get("usd"), "options": rec["options"],
            "names": [s["name"] for s in rec["batch"]["startups"]]},
        "sample": (config.SAMPLE_DIR / "batch.json").exists(),
        "policy": {"scheme": policy["scheme"], "read_on": policy["read_on"], "sources": policy["sources"],
                   "tracks": policy["tracks"], "assumptions": policy["assumptions"], "criteria": policy["criteria"],
                   "decision_rules": policy["decision_rules"], "focus_domains": policy["focus_domains"],
                   "modalities": policy["modalities"]},
        "limits": {"upload_mb": config.MAX_UPLOAD_BYTES // (1024 * 1024), "types": sorted(config.ALLOWED_EXTS),
                   "startups": config.MAX_STARTUPS},
        "fields": [{"key": f["key"], "label": f["label"], "group": f["group"]} for f in FIELDS],
        "today": config.today().isoformat(),
        "code": {"stale": code_stamp() > STARTED_WITH + 1.0},
    }


class CeilingIn(BaseModel):
    track: str = "deep_tech"
    co_amount: float = 0
    prior_gov: float = 0
    first_round: bool = False


@app.post("/api/ceiling")
def ceiling(body: CeilingIn) -> dict[str, Any]:
    policy = rules.load_policy()
    if body.track not in policy["tracks"]:
        raise HTTPException(400, "Unknown track.")
    return rules.match_ceiling(policy["tracks"][body.track], body.co_amount, body.prior_gov, body.first_round)


# ------------------------------------------------------------------ batches and documents

@app.post("/api/batch")
def new_batch() -> dict[str, Any]:
    return store.save_batch({"sample": False, "startups": [blank_startup(x) for x in "ABC"]})


@app.post("/api/batch/sample")
def sample_batch() -> dict[str, Any]:
    spec_path = config.SAMPLE_DIR / "batch.json"
    if not spec_path.exists():
        raise HTTPException(404, "The sample batch is not installed.")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    batch = store.save_batch({"sample": True, "note": spec.get("note", ""), "startups": []})
    for s in spec["startups"]:
        entry = {"id": s["id"], "name": s["name"], "website": s.get("website", ""), "one_liner": s.get("one_liner", ""),
                 "country": s.get("country", "Singapore"), "docs": []}
        for rel in s.get("files", []):
            path = config.SAMPLE_DIR / rel
            entry["docs"].append(docs.store_upload(batch["id"], s["id"], path.name, path.read_bytes()))
        batch["startups"].append(entry)
    return store.save_batch(batch)


@app.get("/api/batch/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    return need_batch(batch_id)


class StartupIn(BaseModel):
    id: str
    name: str = ""
    website: str = ""
    one_liner: str = ""
    country: str = ""


class BatchIn(BaseModel):
    startups: list[StartupIn]


@app.put("/api/batch/{batch_id}")
def update_batch(batch_id: str, body: BatchIn) -> dict[str, Any]:
    batch = need_batch(batch_id)
    if not 1 <= len(body.startups) <= config.MAX_STARTUPS:
        raise HTTPException(400, f"A batch holds 1 to {config.MAX_STARTUPS} start-ups.")
    existing = {s["id"]: s for s in batch["startups"]}
    merged = []
    for s in body.startups:
        if s.id not in LETTERS or len(s.id) != 1:
            raise HTTPException(400, "Start-up ids are single letters A to F.")
        old = existing.get(s.id, blank_startup(s.id))
        changed = (old.get("name"), old.get("website"), old.get("one_liner")) != (s.name.strip(), s.website.strip(), s.one_liner.strip())
        merged.append({"id": s.id, "name": s.name.strip()[:120], "website": s.website.strip()[:200],
                       "one_liner": s.one_liner.strip()[:300], "country": (s.country or "").strip()[:60],
                       "docs": old.get("docs", [])})
        if changed and batch.get("sample"):
            batch["sample"] = False  # an edited batch is no longer the sample, so it never overwrites the recording
    kept = {s["id"] for s in merged}
    for sid, old in existing.items():
        if sid not in kept:
            for meta in old.get("docs", []):
                docs.resolve(meta).unlink(missing_ok=True)
    batch["startups"] = merged
    return store.save_batch(batch)


@app.post("/api/batch/{batch_id}/startups/{sid}/docs")
async def upload_docs(batch_id: str, sid: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    batch = need_batch(batch_id)
    startup = need_startup(batch, sid)
    errors: list[str] = []
    for f in files:
        data = await f.read()
        try:
            startup["docs"].append(docs.store_upload(batch_id, sid, f.filename or "document", data))
        except docs.DocError as e:
            errors.append(str(e))
    batch["sample"] = False
    store.save_batch(batch)
    return {"batch": batch, "errors": errors}


@app.delete("/api/batch/{batch_id}/startups/{sid}/docs/{doc_id}")
def delete_doc(batch_id: str, sid: str, doc_id: str) -> dict[str, Any]:
    batch = need_batch(batch_id)
    startup = need_startup(batch, sid)
    keep = []
    for meta in startup["docs"]:
        if meta["id"] == doc_id:
            docs.resolve(meta).unlink(missing_ok=True)
        else:
            keep.append(meta)
    startup["docs"] = keep
    batch["sample"] = False
    return store.save_batch(batch)


@app.get("/api/file/{batch_id}/{sid}/{doc_id}")
def get_file(batch_id: str, sid: str, doc_id: str, name: str = "") -> FileResponse:
    folder = (config.UPLOAD_DIR / docs.safe_name(batch_id) / docs.safe_name(sid)).resolve()
    if folder.is_relative_to(config.UPLOAD_DIR.resolve()) and folder.exists():
        for path in folder.iterdir():
            if path.name.startswith(docs.safe_name(doc_id) + "_"):
                return FileResponse(path, filename=path.name.split("_", 1)[1], content_disposition_type="inline")
    # A recorded run opened on another machine has no uploads folder: serve the sample document of that name.
    if name:
        sample = (config.SAMPLE_DIR / docs.safe_name(sid) / docs.safe_name(name)).resolve()
        if sample.is_relative_to(config.SAMPLE_DIR.resolve()) and sample.is_file():
            return FileResponse(sample, filename=sample.name, content_disposition_type="inline")
    raise HTTPException(404, "File not found.")


# ------------------------------------------------------------------ runs

class RunIn(BaseModel):
    batch_id: str = ""
    mode: str = "live"
    judge_model: str = ""
    reader_model: str = ""
    web: bool = True
    max_searches: int = 0


@app.post("/api/runs")
def start_run(body: RunIn) -> dict[str, Any]:
    allowed = {m["id"] for m in config.MODEL_CHOICES}
    if body.mode == "recorded":
        rec = pipeline.load_recording()
        if rec is None:
            raise HTTPException(409, "No recorded run is stored yet. Run the sample batch live once to record one.")
        run_id = store.create_run(rec["batch"], rec["options"], "recorded")
        threading.Thread(target=pipeline.replay, args=(run_id,), daemon=True).start()
        return {"run_id": run_id, "mode": "recorded"}
    if body.mode != "live":
        raise HTTPException(400, "Mode is live or recorded.")
    live = llm.available()
    if not live["ok"]:
        raise HTTPException(409, live["reason"])
    batch = need_batch(body.batch_id)
    if not any(s.get("name", "").strip() for s in batch["startups"]):
        raise HTTPException(400, "Name at least one start-up, or load the sample batch.")
    judge = body.judge_model or config.JUDGE_MODEL
    reader = body.reader_model or config.READER_MODEL
    if judge not in allowed or reader not in allowed:
        raise HTTPException(400, "Unknown model.")
    options = {"judge_model": judge, "reader_model": reader, "web": bool(body.web),
               "max_searches": min(8, max(1, body.max_searches or config.MAX_SEARCHES))}
    run_id = store.create_run(batch, options, "live")
    threading.Thread(target=pipeline.execute, args=(run_id,), daemon=True).start()
    return {"run_id": run_id, "mode": "live"}


@app.get("/api/runs")
def runs() -> list[dict[str, Any]]:
    return store.list_runs()


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str, after: int = 0) -> dict[str, Any]:
    run = need_run(run_id, with_result=False)
    return {"status": run["status"], "mode": run["mode"], "error": run.get("error"), "usage": run.get("usage") or {},
            "events": store.events_after(run_id, after)}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    run = need_run(run_id)
    return {**run, "decisions": decision_state(run)}


class DecisionIn(BaseModel):
    kind: str
    startup_id: str = ""
    value: str
    by: str = ""


@app.post("/api/runs/{run_id}/decisions")
def decide(run_id: str, body: DecisionIn) -> dict[str, Any]:
    run = need_run(run_id)
    if run["status"] != "done" or not run.get("result"):
        raise HTTPException(409, "The run has not finished.")
    result = run["result"]
    by = body.by.strip()[:60] or "Reviewer"
    startups = {s["id"]: s for s in result["startups"] if s["status"] == "ok"}

    def log_meeting(sid: str, value: str) -> None:
        s = startups[sid]
        rec = {True: "yes", False: "no"}.get(s["meeting"]["recommend"], "none")
        store.add_decision(run_id, "meeting", sid, value, "First meeting: " + value.capitalize(), None, rec,
                           rec != "none" and value != rec, by)

    def log_investment(sid: str, key: str) -> None:
        s = startups[sid]
        opt = next((o for o in s["sizing"]["options"] if o["key"] == key), None)
        if opt is None:
            raise HTTPException(400, "That option does not exist for this start-up.")
        rec = s["sizing"]["recommended"]
        store.add_decision(run_id, "investment", sid, key, opt["label"], opt["amount"], rec, key != rec, by)

    if body.kind == "plan":
        plan = next((p for p in result["plans"] if p["key"] == body.value), None)
        if plan is None:
            raise HTTPException(400, "Unknown plan.")
        store.add_decision(run_id, "plan", "", plan["key"], plan["label"], plan["total"], "recommended",
                           plan["key"] != "recommended", by)
        for item in plan["items"]:
            if item["meeting"] is not None:  # None = the app makes no call; that start-up's meeting stays the reviewer's to decide
                log_meeting(item["id"], "yes" if item["meeting"] else "no")
            log_investment(item["id"], item["option"])
    elif body.kind == "meeting":
        if body.startup_id not in startups or body.value not in ("yes", "no"):
            raise HTTPException(400, "Meeting decisions are yes or no, for a start-up in this run.")
        log_meeting(body.startup_id, body.value)
    elif body.kind == "investment":
        if body.startup_id not in startups:
            raise HTTPException(400, "Unknown start-up.")
        log_investment(body.startup_id, body.value)
    elif body.kind == "reset":
        store.clear_decisions(run_id)
    else:
        raise HTTPException(400, "Unknown decision kind.")
    return decision_state(run)


class WeightsIn(BaseModel):
    weights: dict[str, float] = {}


@app.post("/api/runs/{run_id}/weights")
def reweigh(run_id: str, body: WeightsIn) -> dict[str, Any]:
    """Re-score with the reviewer's weights. No model call: the ratings stay, the rules run again."""
    run = need_run(run_id)
    if run["status"] != "done" or not run.get("result"):
        raise HTTPException(409, "The run has not finished.")
    result = run["result"]
    policy = result["policy"]
    weights = {c["key"]: min(100.0, max(0.0, float(body.weights.get(c["key"], c["weight"])))) for c in policy["criteria"]}
    if sum(weights.values()) <= 0:
        raise HTTPException(400, "At least one weight must be above zero.")
    is_default = all(abs(weights[c["key"]] - c["weight"]) < 1e-9 for c in policy["criteria"])
    for s in result["startups"]:
        if s["status"] != "ok":
            continue
        s["scores"] = rules.score(s["profile"], weights, policy)
        s["meeting"] = rules.meeting_recommendation(s["gates"], s["scores"]["total"], policy, s["coverage"], s["scores"])
        s["sizing"] = rules.size(s["profile"], s["gates"], s["scores"]["total"], s["coverage"]["value"], policy,
                                 decided=s["meeting"]["recommend"] is not None)
    old = {p["key"]: p.get("rationale", "") for p in result["plans"]}
    result["plans"] = rules.build_plans(result["startups"])
    for p in result["plans"]:
        p["rationale"] = old.get(p["key"], "")
    ok = [s for s in result["startups"] if s["status"] == "ok"]
    result["ranking"] = [s["id"] for s in sorted(ok, key=lambda s: s["scores"]["total"], reverse=True)]
    result["weights"] = None if is_default else weights
    store.set_run(run_id, result=result)
    run = need_run(run_id)
    return {**run, "decisions": decision_state(run)}


@app.get("/api/runs/{run_id}/memo", response_class=PlainTextResponse)
def memo(run_id: str) -> PlainTextResponse:
    run = need_run(run_id)
    if not run.get("result"):
        raise HTTPException(409, "The run has not finished.")
    text = build_memo(run)
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="screening-memo-{run_id}.md"'})


def build_memo(run: dict[str, Any]) -> str:
    result = run["result"]
    state = decision_state(run)["state"]
    policy = result["policy"]
    lines = [f"# Screening memo, run {run['id']}", "",
             f"Date: {result.get('today', '')}. Mode: {run['mode']}. "
             f"Judge model: {run['options'].get('judge_model', '')}. Reader model: {run['options'].get('reader_model', '')}.", ""]
    if (run.get("batch") or {}).get("sample"):
        # 18 Nov 2026: a memo of the sample batch read like a memo on three real companies; it now says they are invented.
        lines += [run["batch"].get("note") or SAMPLE_NOTE, ""]
    if result["comparison"].get("headline"):
        lines += [result["comparison"]["headline"], ""]
    if result.get("weights"):
        lines += ["Scores in this memo use the reviewer's own weights. The summaries were written for the default weights.", ""]
    if state["plan"]:
        plan = next((p for p in result["plans"] if p["key"] == state["plan"]), None)
        if plan:
            lines += [f"Plan accepted: {plan['label']}.", ""]
    for s in result["startups"]:
        lines += [f"## {s['id']}. {s['name']}", ""]
        if s["status"] != "ok":
            lines += [f"Not profiled: {s.get('error', s['status'])}", ""]
            continue
        mine = state["startups"].get(s["id"], {})
        meeting = mine.get("meeting")
        invest = mine.get("investment")
        rec_opt = next(o for o in s["sizing"]["options"] if o["key"] == s["sizing"]["recommended"])
        lines += [
            f"- Reviewer's decision, first meeting: {meeting['value'].upper() if meeting else 'not decided'}"
            + (" (overrides the app)" if meeting and meeting["overrode"] else ""),
            f"- Reviewer's decision, capital: {invest['label'] + (' , ' + rules.money(invest['amount']) if invest.get('amount') else '') if invest else 'not decided'}"
            + (" (overrides the app)" if invest and invest["overrode"] else ""),
            f"- App's read: first meeting { {True: 'yes', False: 'no'}.get(s['meeting']['recommend'], 'no call, too little evidence') }; capital: {rec_opt['label']}"
            + (f", {rules.money(rec_opt['amount'])}" if rec_opt.get("amount") else "") + f". {s['sizing']['why']}",
            f"- Score: {s['scores']['total']:g} of 100. Evidence coverage: {round(s['coverage']['value'] * 100)}%.",
            "",
            s.get("narrative", {}).get("summary", ""), "",
            "Scheme checks:",
        ]
        lines += [f"- {g['status'].upper()}: {g['label']}. {g['detail']}" for g in s["gates"]]
        needs = s.get("support", {}).get("needs", [])
        if needs:
            lines += ["", "What it needs from the team:"]
            lines += [f"- {n['workstream_label']}: {n['need']}" + (f" Who could help: {n['who_could_help']}." if n.get("who_could_help") else "")
                      for n in needs]
        questions = s.get("narrative", {}).get("meeting_questions", [])
        if questions:
            lines += ["", "Questions for the first meeting:"] + [f"{i}. {q}" for i, q in enumerate(questions, 1)]
        lines.append("")
    lines += ["## Basis", "",
              f"Co-investment figures follow the published {policy['scheme']} schedule as read on {policy['read_on']}:"]
    lines += [f"- {src['title']} ({src['publisher']}, page updated {src['page_updated']}): {src['url']}" for src in policy["sources"]]
    lines += ["", "Assumptions:"] + [f"- {a}" for a in policy["assumptions"]]
    lines += ["", "This memo is a first-pass screen produced by a prototype. Amounts are ceilings under the published "
              "schedule, not valuations or commitments. A person made every decision recorded above."]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ static

@app.get("/")
def index() -> FileResponse:
    return FileResponse(Path(config.WEB_DIR) / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=str(config.WEB_DIR)), name="static")
