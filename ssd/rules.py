"""Rules that are code, not AI: the co-investment schedule, the eligibility checks, the score and the options.

The model reads and fills in facts. Everything that turns facts into a number or a recommendation happens
here, from data/policy.json, so a reviewer can see the rule and change it.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import config
from .fields import FIELDS, LISTS

_policy_cache: dict[str, Any] | None = None


def load_policy(refresh: bool = False) -> dict[str, Any]:
    global _policy_cache
    if _policy_cache is None or refresh:
        _policy_cache = json.loads(config.POLICY_PATH.read_text(encoding="utf-8"))
    return _policy_cache


# ------------------------------------------------------------------ co-investment schedule

def _tier_ratio(tier: dict[str, Any], first_round: bool) -> tuple[float, float]:
    if tier.get("first_round_only") and not first_round:
        other = tier["otherwise"]
        return float(other["gov"]), float(other["co"])
    return float(tier["gov"]), float(tier["co"])


def match_ceiling(track: dict[str, Any], co_amount: float, prior_gov: float = 0.0,
                  first_round: bool = False) -> dict[str, Any]:
    """The most the government side can put in against `co_amount` of independent co-investor money.

    Tier limits are the government's own cumulative investment in the start-up, so earlier government
    equity (`prior_gov`) uses up the generous tiers first.
    """
    cap = float(track["cap"])
    g = max(0.0, float(prior_gov))
    remaining = max(0.0, float(co_amount))
    steps: list[dict[str, Any]] = []
    for tier in track["tiers"]:
        upto = min(float(tier["upto"]), cap)
        if g >= upto - 1e-9 or remaining <= 1e-9:
            continue
        gov, co = _tier_ratio(tier, first_round)
        per_co_dollar = gov / co
        take = min(upto - g, remaining * per_co_dollar)
        used = take / per_co_dollar
        steps.append({"ratio": f"{gov:g}:{co:g}", "upto": upto, "gov": take, "co": used, "from": g, "to": g + take})
        g += take
        remaining -= used
    total = g - max(0.0, float(prior_gov))
    return {
        "gov": total,
        "co_used": max(0.0, float(co_amount)) - remaining,
        "co_unmatched": max(0.0, remaining),
        "steps": steps,
        "cumulative_gov": g,
        "cap": cap,
        "cap_reached": g >= cap - 1e-6,
    }


def split_round(track: dict[str, Any], round_size: float, prior_gov: float = 0.0,
                first_round: bool = False) -> dict[str, Any]:
    """How a round of `round_size` splits when the government side takes the most the schedule allows.

    Finds the co-investor amount c where c + ceiling(c) = round_size.
    """
    round_size = max(0.0, float(round_size))
    lo, hi = 0.0, round_size
    for _ in range(80):
        mid = (lo + hi) / 2
        if mid + match_ceiling(track, mid, prior_gov, first_round)["gov"] < round_size:
            lo = mid
        else:
            hi = mid
    result = match_ceiling(track, hi, prior_gov, first_round)
    return {"co": hi, "gov": result["gov"], "steps": result["steps"], "cap_reached": result["cap_reached"]}


def round_amount(value: float, policy: dict[str, Any] | None = None) -> int:
    step = int((policy or load_policy())["decision_rules"]["round_to"])
    return int(round(value / step) * step)


def money(value: float | int | None) -> str:
    if value is None:
        return "not known"
    v = float(value)
    if abs(v) >= 1_000_000:
        text = f"{v / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"S${text} million"
    if abs(v) >= 1_000:
        return f"S${v / 1_000:,.0f}k"
    return f"S${v:,.0f}"


# ------------------------------------------------------------------ reading facts

def fact_map(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """field key -> the fact to use (a fact whose quote shows the value wins over one that is only cited)."""
    rank = {"quote": 0, "cited": 1}
    best: dict[str, dict[str, Any]] = {}
    for fact in profile.get("facts", []):
        if fact.get("status") not in rank:
            continue
        key = fact.get("field")
        if key not in best or rank[fact["status"]] < rank[best[key]["status"]]:
            best[key] = fact
    return best


def yes_no(fact: dict[str, Any] | None) -> bool | None:
    if not fact:
        return None
    text = str(fact.get("value_text", "")).strip().lower()
    if re.match(r"^(yes|true|y\b)", text):
        return True
    if re.match(r"^(no\b|false|not\b|none\b)", text):
        return False
    return None


def round_closed(profile: dict[str, Any]) -> bool:
    """True when the evidence says the latest round has already closed, so there is no round on the table."""
    return any(f.get("field") == "round_open" and yes_no(f) is False for f in profile.get("facts", []))


def number_of(fact: dict[str, Any] | None) -> float | None:
    if not fact:
        return None
    value = fact.get("value_number")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def is_sgd(fact: dict[str, Any] | None) -> bool:
    if not fact:
        return False
    unit = str(fact.get("unit", "")).upper().replace(" ", "")
    return unit in ("SGD", "S$", "SG$", "")


# ------------------------------------------------------------------ eligibility checks

def evaluate_gates(profile: dict[str, Any], policy: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    policy = policy or load_policy()
    facts = fact_map(profile)
    this_year = config.today().year
    out: list[dict[str, Any]] = []

    def add(key: str, status: str, detail: str, fact_keys: list[str]) -> None:
        spec = next(g for g in policy["gates"] if g["key"] == key)
        evidence: list[str] = []
        for fk in fact_keys:
            evidence += facts.get(fk, {}).get("evidence_ids", [])
        out.append({"key": key, "label": spec["label"], "hard": spec["hard"], "status": status, "detail": detail,
                    "evidence_ids": evidence, "source": spec.get("source", ""), "note": spec.get("note", "")})

    def from_bool(key: str, field: str, yes: str, no: str, unknown: str, invert: bool = False) -> None:
        value = yes_no(facts.get(field))
        if value is None:
            add(key, "unknown", unknown, [field])
        elif value != invert:
            add(key, "pass", yes, [field])
        else:
            add(key, "fail", no, [field])

    from_bool("sg_registered", "singapore_registered", "Singapore-registered private limited company.",
              "Not a Singapore-registered private limited company.", "Registration not found in the documents or on the web.")

    year = number_of(facts.get("year_incorporated"))
    stage = stage_of(profile)
    if year is None:
        add("age", "unknown", "Year of incorporation not found.", ["year_incorporated"])
    else:
        age = this_year - int(year)
        if age <= 10:
            add("age", "pass", f"Incorporated {int(year)}: {age} year{'' if age == 1 else 's'} old.", ["year_incorporated"])
        elif modality_of(stage, policy) == "direct":
            add("age", "pass", f"Incorporated {int(year)} ({age} years); the 10-year limit does not extend to growth-stage start-ups.",
                ["year_incorporated"])
        else:
            add("age", "fail", f"Incorporated {int(year)}: {age} years old, over the 10-year limit.", ["year_incorporated"])

    from_bool("sg_innovation", "innovation_in_singapore", "Innovation is developed or held in Singapore.",
              "Innovation is not developed or held in Singapore.", "Not stated where the R&D is done or the IP is held.")
    from_bool("deep_tech", "research_based_ip", "Owns research-based IP.",
              "No research-based IP of its own was found.", "Not clear whether the company owns research-based IP.")

    domain = str(profile.get("sector_domain", "") or "")
    if domain and domain in policy["focus_domains"]:
        add("focus_domain", "pass", domain + ".", [])
    elif domain:
        add("focus_domain", "fail", "Outside the six listed focus areas.", [])
    else:
        add("focus_domain", "unknown", "Sector not established.", [])

    commitment = number_of(facts.get("coinvestor_commitment"))
    lead = facts.get("lead_investor")
    if commitment and commitment > 0:
        who = str(lead.get("value_text", "")) if lead else "an independent investor"
        add("co_investor", "pass", f"{money(commitment) if is_sgd(facts.get('coinvestor_commitment')) else facts['coinvestor_commitment'].get('value_text', '')} committed by {who}.",
            ["lead_investor", "coinvestor_commitment"])
    elif lead and yes_no(lead) is not False:
        add("co_investor", "unknown", f"{lead.get('value_text', 'An investor')} is named, with no committed amount.", ["lead_investor"])
    else:
        add("co_investor", "unknown", "No independent co-investor has committed yet.", ["lead_investor", "coinvestor_commitment"])

    excluded = yes_no(facts.get("excluded_activity"))
    if excluded is True:
        add("not_excluded", "fail", "The business is in an excluded activity.", ["excluded_activity"])
    else:
        add("not_excluded", "pass", "Not in an excluded activity.", ["excluded_activity"])
    return out


def stage_of(profile: dict[str, Any]) -> str:
    fact = fact_map(profile).get("stage")
    return str(fact.get("value_text", "")).strip() if fact else ""


def modality_of(stage: str, policy: dict[str, Any] | None = None) -> str:
    policy = policy or load_policy()
    s = stage.strip().lower()
    if not s:
        return "unclear"
    for word in policy["stage_modality"]["direct"]:
        if s.startswith(word):
            return "direct"
    for word in policy["stage_modality"]["co_investment"]:
        if s.startswith(word):
            return "co_investment"
    return "unclear"


# ------------------------------------------------------------------ score and coverage

def score(profile: dict[str, Any], weights: dict[str, float] | None = None,
          policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Weighted score over the criteria that could be rated.

    A rating of 0 means "the evidence does not let me rate this": it is left out of the score instead of
    counting as weakness, and `rated_share` says how much of the weight the score actually rests on.
    """
    policy = policy or load_policy()
    ratings = {r.get("criterion"): r for r in profile.get("ratings", [])}
    rows: list[dict[str, Any]] = []
    all_w = rated_w = total = 0.0
    for c in policy["criteria"]:
        w = float((weights or {}).get(c["key"], c["weight"]))
        r = ratings.get(c["key"])
        s = min(5, max(0, int(r.get("score", 0)))) if r is not None else 0
        rated = s >= 1
        rows.append({"key": c["key"], "label": c["label"], "weight": w, "score": s, "rated": rated,
                     "reason": (r or {}).get("reason", "") or "Not rated: nothing in the evidence bears on it.",
                     "evidence_ids": (r or {}).get("evidence_ids", []), "basis": c["basis"], "source": c.get("source", "")})
        all_w += w
        if rated:
            rated_w += w
            total += w * (s - 1) / 4
    return {"criteria": rows, "total": round(100 * total / rated_w, 1) if rated_w else 0.0,
            "rated_share": round(rated_w / all_w, 3) if all_w else 0.0, "rated_n": sum(1 for x in rows if x["rated"])}


def coverage(profile: dict[str, Any]) -> dict[str, Any]:
    """How much of what the desk looks for was found, and how much of that shows its figure in a quote."""
    facts = fact_map(profile)
    found = quote = 0
    missing: list[dict[str, str]] = []
    core_fields = [f for f in FIELDS if f["core"]]
    for f in core_fields:
        fact = facts.get(f["key"])
        if fact:
            found += 1
            quote += 1 if fact["status"] == "quote" else 0
        else:
            missing.append({"key": f["key"], "label": f["label"], "question": f["question"]})
    core_lists = [x for x in LISTS if x["core"]]
    for x in core_lists:
        items = [i for i in profile.get(x["key"], []) if i.get("status") in ("quote", "cited")]
        if items:
            found += 1
            quote += 1 if any(i["status"] == "quote" for i in items) else 0
        else:
            missing.append({"key": x["key"], "label": x["label"], "question": x["question"]})
    n = len(core_fields) + len(core_lists)
    value = (quote + 0.6 * (found - quote)) / n if n else 0.0
    return {"value": round(value, 3), "found": found, "quote": quote, "of": n, "missing": missing}


# ------------------------------------------------------------------ share of wallet

def share_of_wallet(profile: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in profile.get("share_of_wallet", []):
        if item.get("status") not in ("quote", "cited"):
            continue
        spend, captured, stated = item.get("customer_annual_spend"), item.get("captured_by_startup"), item.get("stated_pct")
        pct = None
        how = ""
        if isinstance(spend, (int, float)) and isinstance(captured, (int, float)) and spend > 0:
            pct = 100.0 * captured / spend
            how = f"{captured:,.0f} / {spend:,.0f}"
        elif isinstance(stated, (int, float)):
            pct = float(stated)
            how = "as stated in the source"
        rows.append({**item, "pct": None if pct is None else round(pct, 1), "how": how})
    return rows


# ------------------------------------------------------------------ meeting and sizing

def enough_evidence(cov: dict[str, Any] | None, scores: dict[str, Any] | None, policy: dict[str, Any]) -> bool:
    """Whether there is enough on file for the app to make a call at all."""
    rules = policy["decision_rules"]
    if cov is not None and cov.get("value", 1.0) < rules.get("decide_min_coverage", 0.0):
        return False
    if scores is not None and scores.get("rated_share", 1.0) < rules.get("decide_min_rated", 0.0):
        return False
    return True


def meeting_recommendation(gates: list[dict[str, Any]], total: float, policy: dict[str, Any] | None = None,
                           cov: dict[str, Any] | None = None, scores: dict[str, Any] | None = None) -> dict[str, Any]:
    """Yes, No, or None. None means the app does not make a call: too little was found, and little evidence
    is not the same as a weak company. The reviewer's next step is then to ask for documents."""
    policy = policy or load_policy()
    rules = policy["decision_rules"]
    hard_fails = [g for g in gates if g["hard"] and g["status"] == "fail"]
    open_items = [g for g in gates if g["status"] == "unknown"]
    reasons: list[str] = []
    if hard_fails:
        reasons = [f"Fails a scheme requirement: {g['label']}. {g['detail']}" for g in hard_fails]
        return {"recommend": False, "reasons": reasons, "rule": "No, when a hard scheme requirement fails."}
    if not enough_evidence(cov, scores, policy):
        found = f"{cov['found']} of the {cov['of']} items the desk looks for" if cov else "too little"
        rated = (f" and only {scores['rated_n']} of {len(scores['criteria'])} criteria could be rated"
                 if scores and scores["rated_n"] < len(scores["criteria"]) else "")
        reasons = [f"Only {found} were found{rated}, so the app does not make a call: little public evidence is not the same as a weak company.",
                   "Ask the company for its deck and figures, upload them and press Start again, or take a short call to collect them."]
        return {"recommend": None, "reasons": reasons,
                "rule": f"No call, when under {round(rules.get('decide_min_coverage', 0) * 100)}% of the items were found or under "
                        f"{round(rules.get('decide_min_rated', 0) * 100)}% of the score's weight could be rated."}
    # A call made on partial evidence says so: the reviewer should know what the read rests on.
    basis = ""
    if cov and scores and (cov.get("value", 1.0) < rules.get("full_match_min_confidence", 0.0)
                           or scores["rated_n"] < len(scores["criteria"])):
        basis = (f"This read rests on {cov['found']} of the {cov['of']} items the desk looks for and {scores['rated_n']} of the "
                 f"{len(scores['criteria'])} criteria, so the company's own documents could change it.")
    if total < rules["meet_min_score"]:
        reasons.append(f"Score {total:g} is under the meeting threshold of {rules['meet_min_score']}.")
        if basis:
            reasons.append(basis)
        return {"recommend": False, "reasons": reasons, "rule": f"No, when the score is under {rules['meet_min_score']}."}
    reasons.append(f"Score {total:g} clears the meeting threshold of {rules['meet_min_score']}; no hard requirement fails.")
    if basis:
        reasons.append(basis)
    if open_items:
        n = len(open_items)
        reasons.append(("1 requirement is" if n == 1 else f"{n} requirements are") + " still open and can be settled in the meeting.")
    return {"recommend": True, "reasons": reasons,
            "rule": f"Yes, when no hard requirement fails and the score is {rules['meet_min_score']} or more."}


def size(profile: dict[str, Any], gates: list[dict[str, Any]], total: float, cov: float,
         policy: dict[str, Any] | None = None, decided: bool = True) -> dict[str, Any]:
    """The investment options for one start-up. Every amount comes from the schedule in policy.json.

    `decided` is False when too little was found for the app to make a call: the options are still shown
    (the schedule does not depend on the score) but the app picks none of the capital ones.
    """
    base = _size(profile, gates, total, cov, policy or load_policy())
    if not decided:
        keys = [o["key"] for o in base["options"]]
        base["recommended"] = "hold" if "hold" in keys else keys[-1]
        base["why"] = "Too little was found to suggest an amount. Get the company's documents first."
    return base


def _size(profile: dict[str, Any], gates: list[dict[str, Any]], total: float, cov: float,
          policy: dict[str, Any]) -> dict[str, Any]:
    rules = policy["decision_rules"]
    facts = fact_map(profile)
    gate = {g["key"]: g for g in gates}
    hard_fails = [g for g in gates if g["hard"] and g["status"] == "fail"]
    stage = stage_of(profile)
    modality = modality_of(stage, policy)

    deep = gate["deep_tech"]["status"] != "fail"
    track_key = "deep_tech" if deep else "general_tech"
    track = policy["tracks"][track_key]

    commitment_fact = facts.get("coinvestor_commitment")
    round_fact = facts.get("round_size")
    co = number_of(commitment_fact) or 0.0
    round_size = number_of(round_fact)
    currency_ok = (not commitment_fact or is_sgd(commitment_fact)) and (not round_fact or is_sgd(round_fact))
    prior = number_of(facts.get("prior_government_equity")) or 0.0
    first_round_known = yes_no(facts.get("first_institutional_round"))
    first_round = bool(first_round_known)

    assumptions: list[str] = []
    if facts.get("prior_government_equity") is None:
        assumptions.append("No earlier government equity was found, so none is assumed.")
    if first_round_known is None:
        assumptions.append("The documents do not say whether this is a first institutional round, so the first tier is taken at 1:1.")
    if not currency_ok:
        assumptions.append("The round is quoted in a currency other than Singapore dollars; amounts are not converted.")

    base = {"modality": modality, "stage": stage or "not found", "track": track_key, "track_label": track["label"],
            "co_commitment": co, "round_size": round_size, "prior_gov": prior, "first_round": first_round,
            "first_round_known": first_round_known is not None, "assumptions": assumptions,
            "conflict": track.get("conflict", ""), "ceiling": 0, "steps": [], "options": [], "recommended": "hold",
            "why": "", "scenario": None}

    def option(key: str, label: str, amount: float | None, basis: str) -> dict[str, Any]:
        return {"key": key, "label": label, "amount": None if amount is None else round_amount(amount, policy), "basis": basis}

    if hard_fails:
        names = "; ".join(g["label"] for g in hard_fails)
        base["options"] = [
            option("hold", "No capital", 0, f"Not eligible as read: {names}."),
            option("watch", "No capital now, keep on the watchlist", 0,
                   "Revisit if the failed requirement changes (for example, the company files research-based IP of its own)."),
        ]
        base["recommended"] = "hold"
        base["why"] = "A hard scheme requirement fails, so no capital is suggested under Startup SG Equity."
        return base

    if modality == "direct":
        base["options"] = [
            option("terms", "Ask for round terms, size after the first meeting", None,
                   "Growth stage: SG Growth Capital can lead or co-lead in a round with at least one other institutional investor. No ratio is published, so the app does not suggest an amount."),
            option("hold", "No capital yet", 0, "Wait for evidence of product-market fit and the other institutional investor."),
        ]
        base["recommended"] = "terms" if total >= rules["staged_min_score"] else "hold"
        base["why"] = "Growth-stage direct investment has no published formula; the amount is the team's call after terms are known."
        return base

    if not currency_ok:
        base["options"] = [option("hold", "No capital yet", 0, "Convert the round to Singapore dollars first; use the calculator below.")]
        base["why"] = "The round is not in Singapore dollars."
        return base

    if co > 0:
        result = match_ceiling(track, co, prior, first_round)
        ceiling = result["gov"]
        limit_note = ""
        if round_size and round_size > co:
            balance = round_size - co
            if balance < ceiling:
                ceiling = balance
                limit_note = f" The round has {money(balance)} left to fill, which is under the schedule's ceiling of {money(result['gov'])}."
        base["ceiling"] = round_amount(ceiling, policy)
        base["steps"] = result["steps"]
        staged = ceiling * float(rules["staged_fraction"])
        basis = f"Against {money(co)} from the co-investor, the published {track['label'].lower()} schedule allows up to {money(result['gov'])}.{limit_note}"
        base["options"] = [
            option("full", "Match in full", ceiling, basis),
            option("staged", "Half now, half on a milestone", staged,
                   f"{money(staged)} at close; the balance when the next milestone in the plan is met."),
            option("hold", "No capital yet", 0, "Take the first meeting and close the open questions before any capital."),
        ]
        open_gates = [g for g in gates if g["status"] == "unknown"]
        if total >= rules["full_match_min_score"] and cov >= rules["full_match_min_confidence"] and not open_gates:
            base["recommended"] = "full"
            base["why"] = (f"Score {total:g} is {rules['full_match_min_score']} or more, evidence coverage is "
                           f"{round(cov * 100)}% and every requirement passes.")
        elif total >= rules["staged_min_score"]:
            base["recommended"] = "staged"
            why = f"Score {total:g} clears {rules['staged_min_score']}"
            if open_gates:
                why += f", with {len(open_gates)} requirement{'' if len(open_gates) == 1 else 's'} still open"
            elif total < rules["full_match_min_score"]:
                why += f" but is under {rules['full_match_min_score']}"
            else:
                why += f" but evidence coverage is {round(cov * 100)}%, under {round(rules['full_match_min_confidence'] * 100)}%"
            base["why"] = why + ", so half is held back for a milestone."
        else:
            base["recommended"] = "hold"
            base["why"] = f"Score {total:g} is under {rules['staged_min_score']}."
        if track_key == "general_tech":
            base["recommended"] = "hold"
            base["why"] = "The general tech row appears on one official page and not the other, so no capital is suggested on it."
        return base

    # No co-investor commitment yet: nothing can be matched today.
    if round_size and round_size > 0:
        split = split_round(track, round_size, prior, first_round)
        base["scenario"] = {"co": round_amount(split["co"], policy), "gov": round_amount(split["gov"], policy), "steps": split["steps"]}
        base["options"] = [
            option("conditional", "Offer in principle, subject to a co-investor", split["gov"],
                   f"If an independent co-investor commits {money(split['co'])}, the schedule allows up to {money(split['gov'])} of the {money(round_size)} round."),
            option("hold", "No capital yet", 0, "The scheme needs a matching commitment from an independent co-investor first."),
        ]
        base["recommended"] = "conditional" if total >= rules["staged_min_score"] else "hold"
        base["why"] = ("No independent co-investor has committed, so nothing can be matched today. "
                       + ("The score supports an offer in principle." if base["recommended"] == "conditional"
                          else f"Score {total:g} is under {rules['staged_min_score']}."))
    else:
        base["options"] = [
            option("hold", "No capital yet", 0, "No co-investor commitment and no round size were found."),
            option("watch", "No capital now, keep on the watchlist", 0, "Ask for the round size and the co-investor in the first meeting."),
        ]
        base["why"] = ("The latest round in the evidence has already closed, so there is no round on the table to match."
                       if round_closed(profile) else "There is no round on the table yet.")
    return base


def build_plans(startups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Three ways to act on the whole batch. The reviewer accepts one; each can still be changed per start-up."""
    def item(s: dict[str, Any], meet: bool, option_key: str) -> dict[str, Any]:
        opt = next((o for o in s["sizing"]["options"] if o["key"] == option_key), s["sizing"]["options"][-1])
        return {"id": s["id"], "name": s["name"], "meeting": meet, "option": opt["key"], "option_label": opt["label"],
                "amount": opt["amount"]}

    def hold_key(s: dict[str, Any]) -> str:
        keys = [o["key"] for o in s["sizing"]["options"]]
        return "hold" if "hold" in keys else keys[-1]

    ok = [s for s in startups if s.get("status") == "ok"]
    ranked = sorted(ok, key=lambda s: s["scores"]["total"], reverse=True)
    top = next((s for s in ranked if s["meeting"]["recommend"]), None)

    # meeting is True, False or None. None = the app makes no call (too little found): "get documents first".
    # In "meet first" a short meeting is how the missing facts get collected, so those start-ups are met.
    candidates = [
        {"key": "recommended", "label": "Follow the app's read on each start-up",
         "items": [item(s, s["meeting"]["recommend"], s["sizing"]["recommended"]) for s in ok]},
        {"key": "meet_first", "label": "Meet first, decide capital afterwards",
         "items": [item(s, s["meeting"]["recommend"] is not False, hold_key(s)) for s in ok]},
        {"key": "top_only", "label": "Put the time into the top-ranked start-up only",
         "items": [item(s, top is not None and s["id"] == top["id"],
                        s["sizing"]["recommended"] if top is not None and s["id"] == top["id"] else hold_key(s)) for s in ok]},
    ]
    plans, seen = [], set()
    for p in candidates:
        if p["key"] == "top_only" and top is None:
            continue  # nobody is recommended for a meeting, so there is no top-ranked start-up to back
        signature = tuple((i["id"], i["meeting"], i["option"]) for i in p["items"])
        if signature in seen:
            continue  # the same decisions as a plan already listed: showing it twice is not a choice
        seen.add(signature)
        p["total"] = sum(i["amount"] or 0 for i in p["items"])
        p["meetings"] = sum(1 for i in p["items"] if i["meeting"])
        p["undecided"] = sum(1 for i in p["items"] if i["meeting"] is None)
        p["rationale"] = ""
        plans.append(p)
    return plans
