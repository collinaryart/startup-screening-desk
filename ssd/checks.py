"""Source checks. Plain code, no AI.

Every fact the model puts in a profile names the evidence it rests on. These checks open that evidence and
look for the fact's own figures and names in it:

  quote        the figure or name is in the quoted passage from the source
  cited        the source is cited, but the short quoted passage does not show the figure (open the source)
  check        the figure or name was not found in the cited evidence at all: shown with a warning and
               never used in the scheme checks or the sizing
  unsupported  no valid evidence was named: the item is dropped
"""
from __future__ import annotations

import re
from typing import Any

from .fields import FIELD_BY_KEY

# The terms of a round. They describe the round that is open now; a round that has closed is history.
ROUND_FIELDS = ("round_size", "lead_investor", "coinvestor_commitment", "first_institutional_round", "pre_money_valuation")

NUMBER_RX = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?")
WORD_RX = re.compile(r"[A-Za-z][A-Za-z\-']{3,}")
STOP = {"yes", "no", "with", "from", "that", "this", "have", "has", "been", "were", "their", "about", "into", "than",
        "over", "under", "year", "years", "full", "time", "staff", "people", "million", "billion", "thousand",
        "company", "start", "startup", "based", "also", "which", "will", "they", "them", "more", "most", "some",
        "approximately", "around", "roughly", "total", "each", "only", "both", "stated", "according"}


def numbers_in(text: str) -> set[float]:
    out: set[float] = set()
    for m in NUMBER_RX.findall(text or ""):
        try:
            out.add(float(m.replace(",", "")))
        except ValueError:
            continue
    return out


def words_in(text: str) -> set[str]:
    return {w.lower() for w in WORD_RX.findall(text or "") if w.lower() not in STOP}


SCALE_RX = re.compile(r"(?<![A-Za-z])(\d[\d,]*(?:\.\d+)?)\s*(billion|bn|million|mn|mil|m|thousand|k)?\b", re.I)
SCALES = {"billion": 1e9, "bn": 1e9, "million": 1e6, "mn": 1e6, "mil": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}


NUMBER_WORDS = {w: float(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}


def scaled_numbers(text: str) -> set[float]:
    """Numbers in a text with their scale word applied: 'S$3.0 million' -> 3000000 (and 3.0); 'two' -> 2."""
    out: set[float] = {NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", (text or "").lower()) if w in NUMBER_WORDS}
    for digits, scale in SCALE_RX.findall(text or ""):
        try:
            base = float(digits.replace(",", ""))
        except ValueError:
            continue
        out.add(base)
        if scale:
            out.add(base * SCALES[scale.lower()])
    return out


def consistent(value: Any, text: str) -> bool:
    """True when a normalised number agrees with the text it was read from (within half a percent)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return True
    candidates = scaled_numbers(text)
    if not candidates:
        return True
    return any(abs(value - c) <= 0.005 * max(abs(c), 1e-9) for c in candidates)


def fold(text: str) -> str:
    """Lower case, accents removed, so 'bioMérieux' matches 'biomerieux'."""
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c)).lower()


def name_tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", fold(name)) if len(t) >= 2]


def find_passage(line: str, text_docs: list[tuple[dict[str, Any], list[str]]]) -> tuple[dict[str, Any], str] | None:
    """A passage that carries every figure of an uncited line and most of its words, or None.

    Used only for a line the reader wrote without a citation. It needs at least one figure, every
    figure present in one passage, and six in ten of its words there too: enough to tie a spreadsheet
    row to the sentence about it, strict enough that a line about something else finds nothing.
    """
    nums = numbers_in(line)
    wanted = words_in(line)
    if not nums or not wanted:
        return None
    best: tuple[float, dict[str, Any], str] | None = None
    for meta, passages in text_docs:
        for passage in passages:
            if not nums <= numbers_in(passage):
                continue
            hit = len(wanted & words_in(passage)) / len(wanted)
            if hit >= 0.6 and (best is None or hit > best[0]):
                best = (hit, meta, passage)
    return (best[1], best[2]) if best else None


def same_statement(a: str, b: str) -> bool:
    """Whether two sentences say the same thing: the same figures, and most of the shorter one's words in the other.

    Used to tell which uncited lines came back with a citation on the second pass, when the wording has shifted
    a little (a source's name dropped from the sentence, a tag removed).
    """
    a, b = re.sub(r"^\s*\[[A-Z]+\]\s*", "", a or ""), re.sub(r"^\s*\[[A-Z]+\]\s*", "", b or "")
    na, nb = numbers_in(a), numbers_in(b)
    if (na or nb) and na != nb:
        return False
    wa, wb = words_in(a), words_in(b)
    if not wa or not wb:
        return bool(na) and na == nb
    return len(wa & wb) >= 0.6 * min(len(wa), len(wb))


def _evidence_text(ids: list[str], evidence: dict[str, dict[str, Any]]) -> tuple[list[str], str, str]:
    valid = [i for i in ids if i in evidence]
    quotes = " \n".join(q.get("quote", "") for i in valid for q in evidence[i].get("quotes", []))
    claims = " \n".join(evidence[i].get("claim", "") for i in valid)
    return valid, quotes, claims


def check_item(text: str, ids: list[str], evidence: dict[str, dict[str, Any]], names: list[str] | None = None) -> dict[str, Any]:
    """Status for one fact or list entry. `text` carries the figures; `names` are proper names that must appear."""
    valid, quotes, claims = _evidence_text(ids or [], evidence)
    if not valid:
        return {"status": "unsupported", "evidence_ids": [], "why": "No evidence was named."}
    both = quotes + " \n" + claims
    nums = numbers_in(text)
    required_names = [n.strip() for n in (names or []) if n and n.strip()]

    def names_in(haystack: str) -> bool:
        # every word of the name must be there; "Q-linea (ASTar)" matches "Q-linea's ASTar system"
        have = set(re.findall(r"[a-z0-9]+", fold(haystack)))
        return all(set(name_tokens(n)) <= have for n in required_names)

    if nums or required_names:
        nums_quote, nums_both = numbers_in(quotes), numbers_in(both)
        if nums <= nums_quote and names_in(quotes):
            return {"status": "quote", "evidence_ids": valid, "why": "Shown in the quoted passage."}
        if nums <= nums_both and names_in(both):
            return {"status": "cited", "evidence_ids": valid, "why": "Cited; the short quoted passage does not show it. Open the source."}
        missing = sorted(nums - nums_both)
        what = ", ".join(f"{m:g}" for m in missing) if missing else "the name"
        return {"status": "check", "evidence_ids": valid, "why": f"Not found in the cited evidence: {what}."}

    wanted = words_in(text)
    if not wanted:
        return {"status": "cited", "evidence_ids": valid, "why": "Cited."}
    hit_quote = len(wanted & words_in(quotes)) / len(wanted)
    if hit_quote >= 0.5:
        return {"status": "quote", "evidence_ids": valid, "why": "Shown in the quoted passage."}
    return {"status": "cited", "evidence_ids": valid, "why": "Cited; the short quoted passage does not show the wording. Open the source."}


def _strip_yes_no(text: str) -> str:
    return re.sub(r"^\s*(yes|no)\b[\s:;,.\-]*", "", text or "", flags=re.I)


def verify_profile(profile: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Stamp every fact, list entry and rating with a status; drop what has no evidence. Returns the counts."""
    counts = {"quote": 0, "cited": 0, "check": 0, "unsupported": 0}
    dropped: list[dict[str, Any]] = []

    def stamp(item: dict[str, Any], text: str, names: list[str] | None, label: str,
              normalised: list[Any] | None = None) -> bool:
        result = check_item(text, item.get("evidence_ids", []), evidence, names)
        if result["status"] in ("quote", "cited") and not all(consistent(v, text) for v in (normalised or [])):
            result = {**result, "status": "check", "why": "The normalised number does not match the text it was read from."}
        counts[result["status"]] += 1
        if result["status"] == "unsupported":
            dropped.append({"what": label, "value": (text or " ".join(names or []))[:160]})
            return False
        item["status"] = result["status"]
        item["status_why"] = result["why"]
        item["evidence_ids"] = result["evidence_ids"]
        return True

    numeric_kinds = ("money", "number", "year", "percent")
    profile["facts"] = [f for f in profile.get("facts", [])
                        if stamp(f, _strip_yes_no(str(f.get("value_text", ""))), None, f.get("field", "fact"),
                                 [f.get("value_number")]
                                 if FIELD_BY_KEY.get(f.get("field"), {}).get("kind") in numeric_kinds else None)]
    # A round the evidence says has closed is not the round on the table. Its terms are flagged: they stay on
    # screen with a warning and never reach the scheme checks or the sizing.
    # (The "No" counts even when the source check flagged the wording around it: holding money back is the safe side.)
    closed = any(f.get("field") == "round_open" and re.match(r"\s*no\b", str(f.get("value_text", "")), re.I)
                 for f in profile["facts"])
    if closed:
        for f in profile["facts"]:
            if f.get("field") in ROUND_FIELDS and f.get("status") in ("quote", "cited"):
                counts[f["status"]] -= 1
                counts["check"] += 1
                f["status"] = "check"
                f["status_why"] = "The evidence says the latest round has already closed, so this is not a round on the table."
    profile["market"] = [m for m in profile.get("market", [])
                         if stamp(m, m.get("figure", ""), None, "market estimate")]
    profile["team"] = [t for t in profile.get("team", [])
                       if stamp(t, "", [t.get("name", "")], "team member")]
    profile["products"] = [p for p in profile.get("products", [])
                           if stamp(p, p.get("description", ""), None, "product")]
    profile["customers"] = [c for c in profile.get("customers", [])
                            if stamp(c, c.get("detail", ""), None, "customer")]
    profile["competitors"] = [c for c in profile.get("competitors", [])
                              if stamp(c, "", [c.get("name", "")], "competitor")]
    profile["risks"] = [r for r in profile.get("risks", [])
                        if stamp(r, "", None, "risk")]

    def sow_text(item: dict[str, Any]) -> str:
        parts = [item.get("customer_annual_spend"), item.get("captured_by_startup"), item.get("stated_pct")]
        return " ".join(_plain(v) for v in parts if isinstance(v, (int, float)))

    profile["share_of_wallet"] = [
        s for s in profile.get("share_of_wallet", [])
        if stamp(s, s.get("as_written", "") or sow_text(s), None, "share of wallet",
                 [s.get("customer_annual_spend"), s.get("captured_by_startup"), s.get("stated_pct")]
                 if s.get("as_written") else None)]

    ratings = []
    for r in profile.get("ratings", []):
        valid = [i for i in r.get("evidence_ids", []) if i in evidence]
        r["evidence_ids"] = valid
        if not valid and int(r.get("score", 0)) >= 1:
            r["reason"] = (r.get("reason", "") + " (No evidence was named, so this criterion is not rated.)").strip()
            r["score"] = 0
        ratings.append(r)
    profile["ratings"] = ratings
    return {**counts, "dropped": dropped}


def verify_support(needs: list[dict[str, Any]], evidence: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Keep a need only when it names real evidence; stamp it like any other fact. Returns (kept, dropped)."""
    kept, dropped = [], 0
    for n in needs:
        result = check_item(n.get("need", ""), n.get("evidence_ids", []), evidence)
        if result["status"] == "unsupported":
            dropped += 1
            continue
        kept.append({**n, "status": result["status"], "status_why": result["why"], "evidence_ids": result["evidence_ids"]})
    return kept, dropped


def _plain(value: float) -> str:
    return f"{value:.10g}"


def ungrounded_numbers(text: str, allowed_text: str) -> list[str]:
    """Figures in a written summary that do not appear anywhere in what the writer was given."""
    allowed = numbers_in(allowed_text)
    return sorted({f"{n:g}" for n in numbers_in(text) if n not in allowed})
