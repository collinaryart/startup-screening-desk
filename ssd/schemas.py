"""JSON schemas for the two structured answers the judge model gives (profile, comparison).

Written by hand in the form strict structured outputs accept: every property required and
no additional properties.

The API compiles a schema into a grammar and refuses one that is too large. Enumerations and
nullable unions are what make it large, so the profile schema uses plain strings for both:
a number travels as a string ("3000000", or "" when there is none) and the allowed values are
named in the prompt. normalise() turns the answer back into typed values and drops anything
outside the allowed lists, so the rest of the app never sees a value the schema could not promise.
"""
from __future__ import annotations

import re
from typing import Any

from .fields import CUSTOMER_RELATION, FIELD_KEYS, PRODUCT_STATUS

STR: dict[str, Any] = {"type": "string"}
INT: dict[str, Any] = {"type": "integer"}
IDS: dict[str, Any] = {"type": "array", "items": {"type": "string"}}


def obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def arr(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item}


def core_schema() -> dict[str, Any]:
    """Part 1 of the profile: the single-value fields, the ratings and the risks."""
    return obj({
        "one_liner": STR,
        "sector_domain": STR,
        "facts": arr(obj({"field": STR, "value_text": STR, "value_number": STR, "unit": STR, "evidence_ids": IDS, "note": STR})),
        "ratings": arr(obj({"criterion": STR, "score": INT, "reason": STR, "evidence_ids": IDS})),
        "risks": arr(obj({"risk": STR, "evidence_ids": IDS})),
    })


def lists_schema() -> dict[str, Any]:
    """Part 2 of the profile: the lists (people, products, customers, share of wallet, competitors, market)."""
    return obj({
        "team": arr(obj({"name": STR, "role": STR, "background": STR, "evidence_ids": IDS})),
        "products": arr(obj({"name": STR, "description": STR, "status": STR, "evidence_ids": IDS})),
        "customers": arr(obj({"name": STR, "relationship": STR, "detail": STR, "evidence_ids": IDS})),
        "share_of_wallet": arr(obj({"customer": STR, "category": STR, "as_written": STR, "customer_annual_spend": STR,
                                    "captured_by_startup": STR, "stated_pct": STR, "currency": STR, "evidence_ids": IDS})),
        "competitors": arr(obj({"name": STR, "based_in": STR, "what_they_do": STR, "how_startup_differs": STR,
                                "evidence_ids": IDS})),
        "market": arr(obj({"source": STR, "scope": STR, "figure": STR, "value_number": STR, "unit": STR, "evidence_ids": IDS})),
    })


PARTS = {
    "core": (core_schema, "Return part 1 only: one_liner, sector_domain, facts, ratings and risks. Another call returns the lists."),
    "lists": (lists_schema, "Return part 2 only: team, products, customers, share_of_wallet, competitors and market. Another call returns the facts and ratings."),
}


def extraction_schema(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """The whole profile in one schema. Kept for reference and tests: the API refuses it as too large
    a grammar, which is why the desk asks for it in two parts (PARTS) at the same time."""
    return obj({
        "one_liner": STR,
        "sector_domain": STR,
        "facts": arr(obj({"field": STR, "value_text": STR, "value_number": STR, "unit": STR, "evidence_ids": IDS, "note": STR})),
        "team": arr(obj({"name": STR, "role": STR, "background": STR, "evidence_ids": IDS})),
        "products": arr(obj({"name": STR, "description": STR, "status": STR, "evidence_ids": IDS})),
        "customers": arr(obj({"name": STR, "relationship": STR, "detail": STR, "evidence_ids": IDS})),
        "share_of_wallet": arr(obj({"customer": STR, "category": STR, "as_written": STR, "customer_annual_spend": STR,
                                    "captured_by_startup": STR, "stated_pct": STR, "currency": STR, "evidence_ids": IDS})),
        "competitors": arr(obj({"name": STR, "based_in": STR, "what_they_do": STR, "how_startup_differs": STR,
                                "evidence_ids": IDS})),
        "market": arr(obj({"source": STR, "scope": STR, "figure": STR, "value_number": STR, "unit": STR, "evidence_ids": IDS})),
        "risks": arr(obj({"risk": STR, "evidence_ids": IDS})),
        "ratings": arr(obj({"criterion": STR, "score": INT, "reason": STR, "evidence_ids": IDS})),
    })


def schema_notes(policy: dict[str, Any]) -> str:
    """The allowed values, spelled out for the prompt because the schema itself carries no enumerations."""
    return "\n".join([
        "Allowed values",
        "- facts[].field: " + ", ".join(FIELD_KEYS),
        "- sector_domain: " + " | ".join(policy["focus_domains"] + ["Other"]),
        "- products[].status: " + ", ".join(PRODUCT_STATUS),
        "- customers[].relationship: " + ", ".join(CUSTOMER_RELATION),
        "- ratings[].criterion: " + ", ".join(c["key"] for c in policy["criteria"]) + " (one rating for each)",
        '- value_number, customer_annual_spend, captured_by_startup, stated_pct: a plain number written as text, '
        'with no commas, currency or units ("3000000", "14", "94.2"), or "" when there is no number.',
    ])


def to_number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value or "").strip().replace(",", "").replace("_", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?([eE][+-]?\d+)?", text):
        return None
    number = float(text)
    return int(number) if number.is_integer() and abs(number) < 1e15 else number


def _ids(value: Any) -> list[str]:
    return [str(v).strip().strip("[]") for v in value] if isinstance(value, list) else []


def normalise(profile: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Typed, validated copy of the model's profile. Unknown fields, criteria and statuses do not survive."""
    domains = policy["focus_domains"]
    sector = str(profile.get("sector_domain", "")).strip()
    match = next((d for d in domains if d.lower() == sector.lower()), None)
    criteria = {c["key"] for c in policy["criteria"]}

    facts = []
    for f in profile.get("facts", []) or []:
        key = str(f.get("field", "")).strip()
        if key not in FIELD_KEYS or not str(f.get("value_text", "")).strip():
            continue
        facts.append({"field": key, "value_text": str(f["value_text"]).strip(), "value_number": to_number(f.get("value_number")),
                      "unit": str(f.get("unit", "")).strip(), "evidence_ids": _ids(f.get("evidence_ids")),
                      "note": str(f.get("note", "")).strip()})

    def pick(value: Any, allowed: list[str]) -> str:
        v = str(value or "").strip().lower()
        return v if v in allowed else "unknown"

    ratings, seen = [], set()
    for r in profile.get("ratings", []) or []:
        key = str(r.get("criterion", "")).strip()
        if key not in criteria or key in seen:
            continue
        seen.add(key)
        try:
            score = int(r.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        ratings.append({"criterion": key, "score": min(5, max(0, score)), "reason": str(r.get("reason", "")).strip(),
                        "evidence_ids": _ids(r.get("evidence_ids"))})

    def text(item: dict[str, Any], *keys: str) -> dict[str, Any]:
        return {k: str(item.get(k, "") or "").strip() for k in keys}

    return {
        "one_liner": str(profile.get("one_liner", "")).strip(),
        "sector_domain": match or ("Other" if sector else ""),
        "facts": facts,
        "team": [{**text(t, "name", "role", "background"), "evidence_ids": _ids(t.get("evidence_ids"))}
                 for t in profile.get("team", []) or [] if str(t.get("name", "")).strip()],
        # the model's "status" (how far along the product is) is stored as "maturity": "status" is the
        # name the source check uses for its own verdict on every item
        "products": [{**text(p, "name", "description"), "maturity": pick(p.get("status"), PRODUCT_STATUS),
                      "evidence_ids": _ids(p.get("evidence_ids"))}
                     for p in profile.get("products", []) or [] if str(p.get("name", "")).strip()],
        "customers": [{**text(c, "name", "detail"), "relationship": pick(c.get("relationship"), CUSTOMER_RELATION),
                       "evidence_ids": _ids(c.get("evidence_ids"))}
                      for c in profile.get("customers", []) or [] if str(c.get("name", "")).strip()],
        "share_of_wallet": [{**text(w, "customer", "category", "as_written", "currency"),
                             "customer_annual_spend": to_number(w.get("customer_annual_spend")),
                             "captured_by_startup": to_number(w.get("captured_by_startup")),
                             "stated_pct": to_number(w.get("stated_pct")), "evidence_ids": _ids(w.get("evidence_ids"))}
                            for w in profile.get("share_of_wallet", []) or []],
        "competitors": [{**text(c, "name", "based_in", "what_they_do", "how_startup_differs"),
                         "evidence_ids": _ids(c.get("evidence_ids"))}
                        for c in profile.get("competitors", []) or [] if str(c.get("name", "")).strip()],
        "market": [{**text(m, "source", "scope", "figure", "unit"), "value_number": to_number(m.get("value_number")),
                    "evidence_ids": _ids(m.get("evidence_ids"))}
                   for m in profile.get("market", []) or [] if str(m.get("figure", "")).strip()],
        "risks": [{**text(r, "risk"), "evidence_ids": _ids(r.get("evidence_ids"))}
                  for r in profile.get("risks", []) or [] if str(r.get("risk", "")).strip()],
        "ratings": ratings,
    }


def support_schema() -> dict[str, Any]:
    """What the company needs that a portfolio growth team could help with."""
    return obj({"needs": arr(obj({"workstream": STR, "need": STR, "who_could_help": STR, "evidence_ids": IDS}))})


def normalise_support(raw: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Needs under a known workstream only, at most two a heading and six in all."""
    by_key = {w["key"]: w for w in policy.get("workstreams", [])}
    by_label = {w["label"].lower(): w for w in policy.get("workstreams", [])}
    out: list[dict[str, Any]] = []
    per: dict[str, int] = {}
    for n in (raw or {}).get("needs", []) or []:
        tag = str(n.get("workstream", "")).strip()
        w = by_key.get(tag) or by_label.get(tag.lower())
        need = str(n.get("need", "")).strip()
        if not w or not need or per.get(w["key"], 0) >= 2 or len(out) >= 6:
            continue
        per[w["key"]] = per.get(w["key"], 0) + 1
        out.append({"workstream": w["key"], "workstream_label": w["label"], "need": need,
                    "who_could_help": str(n.get("who_could_help", "")).strip(), "evidence_ids": _ids(n.get("evidence_ids"))})
    return out


def comparison_schema(startup_ids: list[str] | None = None, plan_keys: list[str] | None = None) -> dict[str, Any]:
    return obj({
        "headline": STR,
        "what_separates_them": arr(STR),
        "startups": arr(obj({"id": STR, "summary": STR, "why_meet": arr(STR), "why_not": arr(STR),
                             "meeting_questions": arr(STR), "revisit_triggers": arr(STR)})),
        "plan_rationales": arr(obj({"plan": STR, "rationale": STR})),
    })
