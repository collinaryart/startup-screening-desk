"""Offline tests: no network, no key. Run with:  python -m pytest -q"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="ssd_test_")
os.environ["SSD_DB"] = str(Path(_tmp) / "test.db")
os.environ["SSD_UPLOADS"] = str(Path(_tmp) / "uploads")
os.environ["SSD_TODAY"] = "2026-11-16"

from ssd import checks, config, docs, llm, pipeline, rules, schemas, store  # noqa: E402

POLICY = rules.load_policy()
DEEP = POLICY["tracks"]["deep_tech"]
GENERAL = POLICY["tracks"]["general_tech"]


# ------------------------------------------------------------------ the published schedule

def test_first_round_two_to_one_then_one_to_one():
    r = rules.match_ceiling(DEEP, 3_000_000, 0, first_round=True)
    assert round(r["gov"]) == 3_500_000          # S$1m against S$0.5m, then S$2.5m against S$2.5m
    assert [s["ratio"] for s in r["steps"]] == ["2:1", "1:1"]
    assert round(r["co_unmatched"]) == 0


def test_not_first_round_first_tier_is_one_to_one():
    r = rules.match_ceiling(DEEP, 3_000_000, 0, first_round=False)
    assert round(r["gov"]) == 3_000_000
    assert r["steps"][0]["ratio"] == "1:1"


def test_full_cap_needs_23_5_million_from_co_investors():
    r = rules.match_ceiling(DEEP, 23_500_000, 0, first_round=True)
    assert round(r["gov"]) == 12_000_000 and r["cap_reached"]
    assert [s["ratio"] for s in r["steps"]] == ["2:1", "1:1", "1:2", "1:3"]
    more = rules.match_ceiling(DEEP, 40_000_000, 0, first_round=True)
    assert round(more["gov"]) == 12_000_000       # the cap holds
    assert round(more["co_unmatched"]) == 16_500_000


def test_earlier_government_equity_uses_up_the_generous_tiers():
    r = rules.match_ceiling(DEEP, 2_000_000, prior_gov=4_000_000, first_round=False)
    assert round(r["gov"]) == 1_000_000           # already past S$4m, so 1:2
    assert r["steps"][0]["ratio"] == "1:2"
    assert round(rules.match_ceiling(DEEP, 5_000_000, prior_gov=12_000_000)["gov"]) == 0


def test_general_tech_row_from_the_seeds_page():
    r = rules.match_ceiling(GENERAL, 1_000_000, 0)
    # 7:3 up to the first S$250,000, then 1:1
    assert round(r["gov"]) == round(250_000 + (1_000_000 - 250_000 * 3 / 7))
    assert round(rules.match_ceiling(GENERAL, 10_000_000, 0)["gov"]) == 2_000_000


def test_split_round_adds_up():
    split = rules.split_round(DEEP, 4_000_000, 0, first_round=True)
    assert abs(split["co"] + split["gov"] - 4_000_000) < 1
    assert round(split["co"]) == 1_750_000 and round(split["gov"]) == 2_250_000


def test_zero_and_negative_inputs_are_safe():
    assert rules.match_ceiling(DEEP, 0)["gov"] == 0
    assert rules.match_ceiling(DEEP, -5)["gov"] == 0


# ------------------------------------------------------------------ source checks

EVIDENCE = {
    "D1": {"claim": "Halcyra has 14 full-time staff.", "quotes": [{"quote": "14 full-time staff, 12 of them in Singapore."}]},
    "D2": {"claim": "Vantrel Climate Partners has committed S$3.0 million.",
           "quotes": [{"quote": "Lead investor: Vantrel Climate Partners has committed S$3.0 million"}]},
    "W1": {"claim": "One forecast puts the market at $38.21 billion by 2030 (Allied Market Research).",
           "quotes": [{"quote": "The global lithium-ion battery recycling market size was valued at $1.33billionin 2020, and is ..."}]},
}


def test_figure_in_the_quote_is_quote():
    assert checks.check_item("14 full-time staff", ["D1"], EVIDENCE)["status"] == "quote"


def test_figure_only_in_the_claim_is_cited():
    assert checks.check_item("US$38.21 billion by 2030", ["W1"], EVIDENCE)["status"] == "cited"


def test_changed_figure_is_flagged_not_trusted():
    r = checks.check_item("S$3.5 million", ["D2"], EVIDENCE)
    assert r["status"] == "check" and "3.5" in r["why"]


def test_no_evidence_is_unsupported():
    assert checks.check_item("120 staff", [], EVIDENCE)["status"] == "unsupported"
    assert checks.check_item("120 staff", ["Z9"], EVIDENCE)["status"] == "unsupported"


def test_name_must_appear_in_the_evidence():
    assert checks.check_item("", ["D2"], EVIDENCE, names=["Vantrel Climate Partners"])["status"] == "quote"
    assert checks.check_item("", ["D1"], EVIDENCE, names=["Redwood Materials"])["status"] == "check"


def test_normalised_number_must_match_its_text():
    assert checks.consistent(3_000_000, "S$3.0 million")
    assert checks.consistent(14, "14 full-time staff")
    assert checks.consistent(1_400_000, "S$1.4M ARR")
    assert not checks.consistent(30_000_000, "S$3.0 million")
    assert checks.consistent(None, "anything")


def test_summary_figures_must_come_from_the_data():
    assert checks.ungrounded_numbers("Scores 78 and raises S$6.5 million.", '{"score": 78, "round": "S$6.5 million"}') == []
    assert checks.ungrounded_numbers("It has 900 customers.", '{"customers": 420}') == ["900"]


def test_verify_profile_drops_unsourced_and_flags_mismatch():
    profile = {
        "facts": [
            {"field": "headcount", "value_text": "14 full-time staff", "value_number": 14, "unit": "people", "evidence_ids": ["D1"], "note": ""},
            {"field": "coinvestor_commitment", "value_text": "S$3.0 million", "value_number": 30_000_000, "unit": "SGD", "evidence_ids": ["D2"], "note": ""},
            {"field": "revenue", "value_text": "S$9 million", "value_number": 9_000_000, "unit": "SGD", "evidence_ids": [], "note": ""},
        ],
        "team": [], "products": [], "customers": [], "share_of_wallet": [], "risks": [],
        "competitors": [{"name": "Redwood Materials", "based_in": "US", "what_they_do": "", "how_startup_differs": "", "evidence_ids": ["D1"]}],
        "ratings": [{"criterion": "innovation_ip", "score": 5, "reason": "Strong.", "evidence_ids": []}],
    }
    counts = checks.verify_profile(profile, EVIDENCE)
    status = {f["field"]: f["status"] for f in profile["facts"]}
    assert status == {"headcount": "quote", "coinvestor_commitment": "check"}   # revenue dropped; wrong scale flagged
    assert counts["unsupported"] == 1 and counts["dropped"][0]["what"] == "revenue"
    assert profile["competitors"][0]["status"] == "check"
    assert profile["ratings"][0]["score"] == 0                                    # a rating with no evidence is "not rated", not "weak"
    assert "coinvestor_commitment" not in rules.fact_map(profile)                # flagged figures never reach the maths


# ------------------------------------------------------------------ scheme checks, score, options

def fact(field, text, number=None, unit="", status="quote"):
    return {"field": field, "value_text": text, "value_number": number, "unit": unit, "evidence_ids": ["D1"], "note": "", "status": status}


def strong_profile(**overrides):
    facts = {
        "singapore_registered": fact("singapore_registered", "Yes, private limited company registered in Singapore"),
        "year_incorporated": fact("year_incorporated", "2023", 2023, "year"),
        "innovation_in_singapore": fact("innovation_in_singapore", "Yes, R&D in Tuas"),
        "research_based_ip": fact("research_based_ip", "Yes, two patent families owned by the company"),
        "stage": fact("stage", "Seed"),
        "round_size": fact("round_size", "S$6.5 million", 6_500_000, "SGD"),
        "lead_investor": fact("lead_investor", "Vantrel Climate Partners"),
        "coinvestor_commitment": fact("coinvestor_commitment", "S$3.0 million", 3_000_000, "SGD"),
        "first_institutional_round": fact("first_institutional_round", "Yes, first institutional round"),
    }
    facts.update(overrides)
    return {"sector_domain": POLICY["focus_domains"][3], "facts": [f for f in facts.values() if f is not None],
            "ratings": [{"criterion": c["key"], "score": 4, "reason": "ok", "evidence_ids": ["D1"]} for c in POLICY["criteria"]],
            "team": [], "products": [], "customers": [], "share_of_wallet": [], "competitors": [], "risks": []}


def test_gates_all_pass_for_the_strong_case():
    gates = rules.evaluate_gates(strong_profile(), POLICY)
    assert {g["key"]: g["status"] for g in gates} == {
        "sg_registered": "pass", "age": "pass", "sg_innovation": "pass", "deep_tech": "pass",
        "focus_domain": "pass", "co_investor": "pass", "not_excluded": "pass"}


def test_score_scale():
    assert rules.score(strong_profile(), None, POLICY)["total"] == 75.0     # all 4s
    p = strong_profile()
    p["ratings"] = []
    assert rules.score(p, None, POLICY)["total"] == 0.0


def test_full_match_is_capped_by_what_is_left_in_the_round():
    p = strong_profile()
    gates = rules.evaluate_gates(p, POLICY)
    sizing = rules.size(p, gates, 78, 0.8, POLICY)
    assert sizing["ceiling"] == 3_500_000                # schedule allows S$3.5m and the round has S$3.5m left
    assert sizing["recommended"] == "full"
    assert [o["key"] for o in sizing["options"]] == ["full", "staged", "hold"]
    assert sizing["options"][1]["amount"] == 1_750_000
    p2 = strong_profile(round_size=fact("round_size", "S$5.0 million", 5_000_000, "SGD"))
    sizing2 = rules.size(p2, rules.evaluate_gates(p2, POLICY), 78, 0.8, POLICY)
    assert sizing2["ceiling"] == 2_000_000               # only S$2.0m of the round is left to fill


def test_middling_score_is_staged_and_low_score_holds():
    p = strong_profile()
    gates = rules.evaluate_gates(p, POLICY)
    assert rules.size(p, gates, 62, 0.8, POLICY)["recommended"] == "staged"
    assert rules.size(p, gates, 78, 0.4, POLICY)["recommended"] == "staged"   # thin evidence holds half back
    assert rules.size(p, gates, 40, 0.8, POLICY)["recommended"] == "hold"


def test_no_co_investor_means_nothing_is_matched_today():
    p = strong_profile(coinvestor_commitment=None, lead_investor=None,
                       round_size=fact("round_size", "S$4.0 million", 4_000_000, "SGD"))
    gates = rules.evaluate_gates(p, POLICY)
    assert next(g for g in gates if g["key"] == "co_investor")["status"] == "unknown"
    sizing = rules.size(p, gates, 70, 0.7, POLICY)
    assert sizing["ceiling"] == 0 and sizing["recommended"] == "conditional"
    assert sizing["scenario"] == {"co": 1_750_000, "gov": 2_250_000, "steps": sizing["scenario"]["steps"]}
    assert rules.meeting_recommendation(gates, 70, POLICY)["recommend"] is True


def test_no_research_ip_fails_the_deep_tech_requirement():
    p = strong_profile(research_based_ip=fact("research_based_ip", "No, built on third-party model APIs; no patents"))
    gates = rules.evaluate_gates(p, POLICY)
    assert next(g for g in gates if g["key"] == "deep_tech")["status"] == "fail"
    meeting = rules.meeting_recommendation(gates, 80, POLICY)
    assert meeting["recommend"] is False and "Fails a scheme requirement" in meeting["reasons"][0]
    sizing = rules.size(p, gates, 80, 0.9, POLICY)
    assert sizing["recommended"] == "hold" and all(o["amount"] == 0 for o in sizing["options"])
    assert sizing["track"] == "general_tech" and "Two official pages differ" in sizing["conflict"]


def test_age_limit_and_growth_stage_exception():
    old = strong_profile(year_incorporated=fact("year_incorporated", "2012", 2012, "year"))
    assert next(g for g in rules.evaluate_gates(old, POLICY) if g["key"] == "age")["status"] == "fail"
    growth = strong_profile(year_incorporated=fact("year_incorporated", "2012", 2012, "year"), stage=fact("stage", "Series C+"))
    assert next(g for g in rules.evaluate_gates(growth, POLICY) if g["key"] == "age")["status"] == "pass"
    sizing = rules.size(growth, rules.evaluate_gates(growth, POLICY), 80, 0.9, POLICY)
    assert sizing["modality"] == "direct" and sizing["options"][0]["amount"] is None   # no published formula, so no amount


def test_flagged_fact_is_not_used():
    p = strong_profile(coinvestor_commitment=fact("coinvestor_commitment", "S$3.0 million", 3_000_000, "SGD", status="check"))
    sizing = rules.size(p, rules.evaluate_gates(p, POLICY), 78, 0.8, POLICY)
    assert sizing["ceiling"] == 0


def test_a_round_that_has_closed_is_not_the_round_on_the_table():
    ev = {"W1": {"claim": "Cococart raised $4.2 million in seed funding led by Forerunner Ventures and Sequoia Capital, announced on 1 March 2022.",
                 "quotes": [{"quote": "Cococart raises $4.2M from Forerunner Ventures and Sequoia Capital"}]}}

    def web_fact(field, text, number=None, unit=""):
        return {"field": field, "value_text": text, "value_number": number, "unit": unit, "evidence_ids": ["W1"], "note": ""}

    facts = [web_fact("stage", "Seed"),
             web_fact("round_open", "No, the seed round was raised in March 2022"),
             web_fact("round_size", "$4.2 million", 4_200_000, "USD"),
             web_fact("lead_investor", "Forerunner Ventures and Sequoia Capital"),
             web_fact("coinvestor_commitment", "$4.2 million", 4_200_000, "USD"),
             web_fact("funding_to_date", "$4.2 million", 4_200_000, "USD")]
    profile = {"sector_domain": "", "facts": [dict(f) for f in facts], "ratings": [], "market": [],
               "team": [], "products": [], "customers": [], "share_of_wallet": [], "competitors": [], "risks": []}
    counts = checks.verify_profile(profile, ev)
    status = {f["field"]: f["status"] for f in profile["facts"]}
    assert status["round_open"] in ("quote", "cited")
    assert status["round_size"] == status["lead_investor"] == status["coinvestor_commitment"] == "check"   # shown with a warning, kept out of the maths
    assert status["funding_to_date"] in ("quote", "cited") and status["stage"] in ("quote", "cited")       # the history itself stays
    assert counts["check"] == 3
    used = rules.fact_map(profile)
    assert "round_size" not in used and "coinvestor_commitment" not in used
    gates = rules.evaluate_gates(profile, POLICY)
    assert next(g for g in gates if g["key"] == "co_investor")["status"] == "unknown"
    sizing = rules.size(profile, gates, 60, 0.6, POLICY)
    assert sizing["ceiling"] == 0 and sizing["recommended"] == "hold" and "already closed" in sizing["why"]
    assert all(not o["amount"] for o in sizing["options"])
    cov = rules.coverage(profile)
    assert cov["of"] == 17 and "round_size" in [m["key"] for m in cov["missing"]]      # the open round becomes a question to ask
    # the "No" counts even when the source check flags the wording around it (here a year the evidence does not give)
    shaky = {**profile, "facts": [dict(f) for f in facts if f["field"] != "round_open"] + [web_fact("round_open", "No, the round closed in 2019")]}
    checks.verify_profile(shaky, ev)
    by_field = {f["field"]: f["status"] for f in shaky["facts"]}
    assert by_field["round_open"] == "check" and by_field["round_size"] == "check" and "round_size" not in rules.fact_map(shaky)
    assert "already closed" in rules.size(shaky, rules.evaluate_gates(shaky, POLICY), 60, 0.6, POLICY)["why"]
    # an open round is left alone
    raising = {**profile, "facts": [dict(f) for f in facts if f["field"] != "round_open"] + [web_fact("round_open", "Yes, raising a seed round")]}
    checks.verify_profile(raising, ev)
    assert {f["field"]: f["status"] for f in raising["facts"]}["round_size"] in ("quote", "cited")
    assert "round_size" in rules.fact_map(raising)


def test_little_evidence_means_no_call_not_no():
    thin = {"sector_domain": POLICY["focus_domains"][5], "facts": [fact("year_incorporated", "2020", 2020, "year"), fact("stage", "Series B")],
            "ratings": [{"criterion": "market_potential", "score": 3, "reason": "A mid-sized market.", "evidence_ids": ["W1"]},
                        {"criterion": "management_team", "score": 0, "reason": "Only the founder is named.", "evidence_ids": []}],
            "team": [], "products": [], "customers": [], "share_of_wallet": [], "competitors": [], "risks": []}
    gates = rules.evaluate_gates(thin, POLICY)
    scores = rules.score(thin, None, POLICY)
    cov = rules.coverage(thin)
    assert scores["total"] == 50.0 and scores["rated_n"] == 1 and scores["rated_share"] == 0.1    # the score rests on one criterion
    m = rules.meeting_recommendation(gates, scores["total"], POLICY, cov, scores)
    assert m["recommend"] is None and "does not make a call" in m["reasons"][0] and "deck" in m["reasons"][1]
    sizing = rules.size(thin, gates, scores["total"], cov["value"], POLICY, decided=False)
    assert sizing["recommended"] in ("hold", "watch") and "Too little was found" in sizing["why"]
    # enough found for a call, but not everything: the call says what it rests on
    part = rules.meeting_recommendation(gates, 40, POLICY, {"value": 0.51, "found": 9, "of": 17}, {"rated_share": 0.65, "rated_n": 5, "criteria": [0] * 7})
    assert part["recommend"] is False and "rests on 9 of the 17 items" in part["reasons"][1] and "5 of the 7 criteria" in part["reasons"][1]
    whole = rules.meeting_recommendation(gates, 40, POLICY, {"value": 0.9, "found": 17, "of": 17}, {"rated_share": 1.0, "rated_n": 7, "criteria": [0] * 7})
    assert whole["recommend"] is False and len(whole["reasons"]) == 1
    # a hard failure is still a no, however little was found
    bad = dict(thin, facts=thin["facts"] + [fact("research_based_ip", "No, built on third-party APIs")])
    assert rules.meeting_recommendation(rules.evaluate_gates(bad, POLICY), 50, POLICY, rules.coverage(bad), rules.score(bad, None, POLICY))["recommend"] is False
    # the well-documented case is unaffected
    p = strong_profile()
    full = rules.score(p, None, POLICY)
    assert full["rated_share"] == 1.0
    assert rules.meeting_recommendation(rules.evaluate_gates(p, POLICY), full["total"], POLICY, {"value": 0.8, "found": 14, "of": 17}, full)["recommend"] is True


def test_plans_never_repeat_each_other_and_carry_the_no_call_state():
    def startup(sid, recommend, total, rec="hold"):
        return {"id": sid, "name": sid, "status": "ok", "scores": {"total": total}, "meeting": {"recommend": recommend},
                "sizing": {"recommended": rec, "options": [{"key": "staged", "label": "Half now", "amount": 1_000_000},
                                                           {"key": "hold", "label": "No capital yet", "amount": 0}]}}
    # nobody recommended, two with too little evidence: "follow the app" and "meet first" differ, "top only" has no one to back
    plans = rules.build_plans([startup("A", None, 31), startup("B", None, 25)])
    assert [p["key"] for p in plans] == ["recommended", "meet_first"]
    assert [i["meeting"] for i in plans[0]["items"]] == [None, None] and plans[0]["undecided"] == 2
    assert [i["meeting"] for i in plans[1]["items"]] == [True, True] and plans[1]["meetings"] == 2
    # everyone a firm no: all three would be the same decisions, so one plan is shown
    assert [p["key"] for p in rules.build_plans([startup("A", False, 20), startup("B", False, 10)])] == ["recommended"]
    # the full case keeps three distinct plans
    three = rules.build_plans([startup("A", True, 70, "staged"), startup("B", True, 55), startup("C", False, 40)])
    assert [p["key"] for p in three] == ["recommended", "meet_first", "top_only"] and three[2]["meetings"] == 1


def test_uncited_web_findings_get_one_more_chance_then_are_dropped():
    calls = []

    def model(role, model, system, content, tools, schema):
        calls.append(role)
        if role == "web_researcher":
            return {"blocks": [
                {"type": "text", "text": "[COMPANY] Acme was founded in 2020 by Jo Lim (Vulcan Post)."},
                {"type": "text", "text": "\n[FUNDING] Acme raised a $4.2 million seed in March 2022 (Dealroom)."},
                {"type": "text", "text": "\n[MARKET] A line the results do not support."},
                {"type": "text", "text": "\n[COMPETITOR] Shopify is a rival.",
                 "citations": [{"type": "web_search_result_location", "url": "https://c.example/1", "title": "C", "cited_text": "Shopify"}]},
            ]}
        assert role == "web_researcher_repair" and "[FUNDING] Acme raised a $4.2 million seed" in content and tools[0]["max_uses"] == 1
        return {"blocks": [
            {"type": "text", "text": "[COMPANY] Acme was founded in 2020 by Jo Lim.",
             "citations": [{"type": "web_search_result_location", "url": "https://v.example/a", "title": "V", "cited_text": "founded in 2020 by Jo Lim"}]},
            {"type": "text", "text": "\n[FUNDING] Acme raised a $4.2 million seed in March 2022.",
             "citations": [{"type": "web_search_result_location", "url": "https://d.example/b", "title": "D", "cited_text": "$4.2 million seed in March 2022"}]},
        ]}

    llm.set_stub(model)
    try:
        batch = store.save_batch({"sample": False, "startups": [{"id": "A", "name": "Acme", "website": "", "one_liner": "", "country": "", "docs": []}]})
        options = {"judge_model": "claude-opus-5-5", "reader_model": "claude-haiku-5-5", "web": True, "max_searches": 3}
        run_id = store.create_run(batch, options, "live")
        out = pipeline.research_web(run_id, batch["startups"][0], options, llm.Usage())
    finally:
        llm.set_stub(None)
    assert calls == ["web_researcher", "web_researcher_repair"]
    assert sorted(e["topic"] for e in out["evidence"]) == ["COMPANY", "COMPETITOR", "FUNDING"]
    assert out["audit"]["rescued"] == 2 and out["audit"]["dropped_lines"] == ["[MARKET] A line the results do not support."]
    assert "Country: not given" in pipeline.web_brief(batch["startups"][0])      # no country is assumed for the search


def test_same_statement_survives_a_dropped_source_name_but_not_a_changed_figure():
    assert checks.same_statement("[COMPANY] Acme was founded in 2020 by Jo Lim (Vulcan Post).", "Acme was founded in 2020 by Jo Lim.")
    assert not checks.same_statement("[FUNDING] Acme raised a $4.2 million seed in March 2022.", "Acme raised a $5.2 million seed in March 2022.")
    assert not checks.same_statement("[MARKET] The market is large.", "Acme was founded in 2020 by Jo Lim.")


def test_share_of_wallet_is_computed_by_code():
    p = strong_profile()
    p["share_of_wallet"] = [{"customer": "Customer 1", "category": "processing", "customer_annual_spend": 1_200_000,
                             "captured_by_startup": 180_000, "stated_pct": None, "status": "quote"}]
    assert rules.share_of_wallet(p)[0]["pct"] == 15.0


def test_coverage_lists_what_to_ask():
    cov = rules.coverage(strong_profile())
    labels = [m["label"] for m in cov["missing"]]
    assert "Headcount" in labels and "Share of wallet" in labels and 0 < cov["value"] < 1
    assert all(m["question"].endswith("?") for m in cov["missing"])


# ------------------------------------------------------------------ documents

def test_docx_pptx_xlsx_and_text_are_read(tmp_path):
    sample = ROOT / "data" / "samples"
    assert "310 archived bacterial isolates" in docs.extract_text(sample / "B" / "Orvelle_investor_brief.docx")
    xlsx = docs.extract_text(sample / "A" / "Halcyra_kpi_sheet.xlsx")
    assert "--- Sheet: Customers ---" in xlsx and "1200000" in xlsx and "180000" in xlsx
    pptx = tmp_path / "deck.pptx"
    slide = ('<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
             'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>Raising S$2 million</a:t></a:r></a:p></p:sld>')
    with zipfile.ZipFile(pptx, "w") as z:
        z.writestr("ppt/slides/slide1.xml", slide)
    assert docs.extract_text(pptx) == "--- Slide 1 ---\nRaising S$2 million"


def test_spreadsheet_rows_become_self_contained_passages():
    sample = ROOT / "data" / "samples"
    rows = docs.extract_passages(sample / "A" / "Halcyra_kpi_sheet.xlsx")
    assert "Sheet 'KPIs', row 3: Metric = Full-time staff; Value = 14" in rows
    customer = next(r for r in rows if "Customer 1" in r)
    assert "(S$) = 1200000" in customer and "(S$) = 180000" in customer          # each cell carries its column heading
    csv_rows = docs.extract_passages(sample / "C" / "Brevanta_customer_metrics.csv")
    assert any("Online fashion retailer" in r and "= 96000" in r and "= 31000" in r for r in csv_rows)
    with pytest.raises(docs.DocError):                                             # a path outside the uploads folder is refused
        docs.to_block({"kind": "office", "name": "x.xlsx", "path": str(sample / "A" / "Halcyra_kpi_sheet.xlsx")})
    meta = docs.store_upload("B-test", "A", "Halcyra_kpi_sheet.xlsx", (sample / "A" / "Halcyra_kpi_sheet.xlsx").read_bytes())
    block = docs.to_block(meta)
    assert block["source"]["type"] == "content" and block["citations"] == {"enabled": True}
    assert any("Customer 1" in b["text"] for b in block["source"]["content"])


def test_uncited_line_is_rescued_only_when_the_text_carries_its_figures():
    sample = ROOT / "data" / "samples"
    rows = docs.extract_passages(sample / "C" / "Brevanta_customer_metrics.csv")
    text_docs = [({"id": "x", "name": "Brevanta_customer_metrics.csv"}, rows)]
    line = ("Brevanta AI's largest customer, an online fashion retailer (name withheld), is a paying customer with a yearly "
            "customer-support software spend of S$96000 and paid Brevanta S$31000 in the last 12 months.")
    meta, passage = checks.find_passage(line, text_docs)
    assert "Online fashion retailer" in passage and "96000" in passage
    assert checks.find_passage(line.replace("96000", "99000"), text_docs) is None      # a changed figure finds nothing
    assert checks.find_passage("The company is the market leader in Asia.", text_docs) is None   # no figure, no rescue
    assert checks.find_passage("Revenue was S$31000 from a hospital laboratory in Germany last year.", text_docs) is None


def test_names_match_on_their_words_not_their_punctuation():
    ev = {"W1": {"claim": "Q-linea's ASTar system is cleared in the US; bioMerieux sells VITEK 2.", "quotes": [{"quote": "Q-linea ASTar"}]}}
    assert checks.check_item("", ["W1"], ev, names=["Q-linea (ASTar)"])["status"] == "quote"
    assert checks.check_item("", ["W1"], ev, names=["bioMérieux (VITEK 2)"])["status"] == "cited"
    assert checks.check_item("", ["W1"], ev, names=["Accelerate Diagnostics"])["status"] == "check"


def test_count_words_and_quotes():
    assert checks.consistent(2, "Two PCT patent applications filed (2024 and 2025), neither granted")
    assert docs.clean_quote("pilot line are in\r Tuas,\x02 Singapore.") == "pilot line are in Tuas, Singapore."


def test_upload_rules():
    meta = docs.store_upload("B-test", "A", "..\\..\\evil name?.txt", b"hello")
    assert meta["name"] == "evil name_.txt" and docs.resolve(meta).read_bytes() == b"hello"
    with pytest.raises(docs.DocError):
        docs.store_upload("B-test", "A", "tool.exe", b"MZ")
    with pytest.raises(docs.DocError):
        docs.store_upload("B-test", "A", "empty.txt", b"")
    pdf = (ROOT / "data" / "samples" / "A" / "Halcyra_seed_deck.pdf").read_bytes()
    assert docs.pdf_page_count(pdf) == 4


# ------------------------------------------------------------------ evidence parsing

def test_cited_lines_attach_citations_to_their_line():
    blocks = [
        {"type": "text", "text": "[MARKET] One forecast puts the market at $38.21 billion by 2030 "},
        {"type": "text", "text": "(Allied Market Research)", "citations": [{"type": "web_search_result_location", "url": "https://a.example/x", "title": "A", "cited_text": "valued at $1.33 billion"}]},
        {"type": "text", "text": ".\n[MARKET] Another puts 2030 revenue near "},
        {"type": "text", "text": "USD 10.98 billion", "citations": [{"type": "web_search_result_location", "url": "https://b.example/y", "title": "B", "cited_text": "expected to reach USD 10.98 billion by 2030"}]},
        {"type": "text", "text": ".\n[COMPANY] A line with no source.\n[NOTE] No public information was found about the company."},
    ]
    lines = pipeline.cited_lines(blocks)
    assert len(lines) == 4
    assert lines[0][1][0]["url"] == "https://a.example/x" and lines[1][1][0]["url"] == "https://b.example/y"
    assert lines[2][1] == [] and lines[3][0].startswith("[NOTE]")


# ------------------------------------------------------------------ the whole workflow, with a scripted model

def scripted_model(role, model, system, content, tools, schema):
    if role == "doc_reader":
        return {"blocks": [
            {"type": "text", "text": "Testco Pte. Ltd. is registered in Singapore and was incorporated in 2023.",
             "citations": [{"type": "char_location", "document_index": 0, "cited_text": "registered in Singapore, incorporated in 2023"}]},
            {"type": "text", "text": "\nTestco has 14 full-time staff.",
             "citations": [{"type": "char_location", "document_index": 0, "cited_text": "14 full-time staff"}]},
            {"type": "text", "text": "\nTestco owns two patent families from its own research in Singapore.",
             "citations": [{"type": "char_location", "document_index": 0, "cited_text": "two patent families owned by Testco; R&D in Singapore"}]},
            {"type": "text", "text": "\nLead investor Fundco has committed S$3.0 million to the S$6.5 million seed round, the first institutional round.",
             "citations": [{"type": "char_location", "document_index": 0, "cited_text": "Fundco has committed S$3.0 million of the S$6.5 million seed round; first institutional round"}]},
            {"type": "text", "text": "\nAn unsourced line the reader made up."},
        ]}
    if role == "doc_reader_repair":
        assert "An unsourced line the reader made up." in content[-1]["text"]
        return {"blocks": []}                     # the documents do not support it, so nothing comes back
    if role == "web_researcher":
        assert isinstance(content, str) and "SECRET" not in content, "document text must never reach the web researcher"
        return {"blocks": [
            {"type": "server_tool_use", "name": "web_search", "input": {"query": "battery recycling market size"}},
            {"type": "web_search_tool_result", "content": [{"type": "web_search_result", "title": "Report", "url": "https://r.example/report"}]},
            {"type": "text", "text": "[MARKET] The market is expected to reach USD 10.98 billion by 2030 (Report).",
             "citations": [{"type": "web_search_result_location", "url": "https://r.example/report", "title": "Report",
                            "cited_text": "expected to reach USD 10.98 billion by 2030"}]},
            {"type": "text", "text": "\n[NOTE] No public information was found about the company."},
        ]}
    if role in ("extractor_core", "extractor_lists"):
        assert schema in (schemas.core_schema(), schemas.lists_schema())
        profile = {
            "one_liner": "Recycles battery cathode material.", "sector_domain": POLICY["focus_domains"][3],
            "facts": [
                {"field": "singapore_registered", "value_text": "Yes, registered in Singapore", "value_number": None, "unit": "", "evidence_ids": ["D1"], "note": ""},
                {"field": "year_incorporated", "value_text": "2023", "value_number": 2023, "unit": "year", "evidence_ids": ["D1"], "note": ""},
                {"field": "headcount", "value_text": "14 full-time staff", "value_number": 14, "unit": "people", "evidence_ids": ["D2"], "note": ""},
                {"field": "research_based_ip", "value_text": "Yes, two patent families", "value_number": None, "unit": "", "evidence_ids": ["D3"], "note": ""},
                {"field": "innovation_in_singapore", "value_text": "Yes, research in Singapore", "value_number": None, "unit": "", "evidence_ids": ["D3"], "note": ""},
                {"field": "stage", "value_text": "Seed", "value_number": None, "unit": "", "evidence_ids": ["D4"], "note": ""},
                {"field": "round_size", "value_text": "S$6.5 million", "value_number": 6500000, "unit": "SGD", "evidence_ids": ["D4"], "note": ""},
                {"field": "lead_investor", "value_text": "Fundco", "value_number": None, "unit": "", "evidence_ids": ["D4"], "note": ""},
                {"field": "coinvestor_commitment", "value_text": "S$3.0 million", "value_number": 3000000, "unit": "SGD", "evidence_ids": ["D4"], "note": ""},
                {"field": "first_institutional_round", "value_text": "Yes, first institutional round", "value_number": None, "unit": "", "evidence_ids": ["D4"], "note": ""},
                {"field": "tam", "value_text": "USD 10.98 billion by 2030", "value_number": 10980000000, "unit": "USD", "evidence_ids": ["W1"], "note": ""},
                {"field": "revenue", "value_text": "S$50 million", "value_number": 50000000, "unit": "SGD", "evidence_ids": [], "note": "invented"},
            ],
            "team": [], "products": [], "customers": [], "share_of_wallet": [], "competitors": [], "risks": [],
            "ratings": [{"criterion": c["key"], "score": 4, "reason": "Evidence is good.", "evidence_ids": ["D3"]} for c in POLICY["criteria"]],
        }
        return {"blocks": [{"type": "text", "text": json.dumps(profile)}]}
    if role == "support_mapper":
        assert schema == schemas.support_schema()
        return {"blocks": [{"type": "text", "text": json.dumps({"needs": [
            {"workstream": "investors", "need": "Testco needs a co-investor for the rest of the S$6.5 million round.",
             "who_could_help": "venture funds that lead seed rounds", "evidence_ids": ["D4"]},
            {"workstream": "rnd_coe", "need": "A need the model made up.", "who_could_help": "labs", "evidence_ids": ["Z1"]},
        ]})}]}
    if role == "comparer":
        data = json.loads(content)
        return {"blocks": [{"type": "text", "text": json.dumps({
            "headline": "Testco leads.", "what_separates_them": ["Only one start-up."],
            "startups": [{"id": s["id"], "summary": "A recycler with 9999 customers.", "why_meet": ["Strong IP."], "why_not": [],
                          "meeting_questions": ["What is the revenue?"], "revisit_triggers": []} for s in data["startups"]],
            "plan_rationales": [{"plan": p["plan"], "rationale": "Pick when sure."} for p in data["plans"]]})}]}
    raise AssertionError(role)


def scripted_run() -> str:
    """One finished run on the scripted model: no network, no key. Returns the run id."""
    llm.set_stub(scripted_model)
    try:
        batch = store.save_batch({"sample": False, "startups": [
            {"id": "A", "name": "Testco", "website": "", "one_liner": "Battery recycling", "country": "Singapore", "docs": []}]})
        batch["startups"][0]["docs"].append(docs.store_upload(batch["id"], "A", "deck.txt", b"SECRET internal figures"))
        store.save_batch(batch)
        options = {"judge_model": "claude-opus-5-5", "reader_model": "claude-haiku-5-5", "web": True, "max_searches": 2}
        run_id = store.create_run(batch, options, "live")
        pipeline.execute(run_id)
        return run_id
    finally:
        llm.set_stub(None)


def test_whole_run_with_a_scripted_model():
    try:
        run_id = scripted_run()
        run = store.get_run(run_id)
        assert run["status"] == "done", run.get("error")
        s = run["result"]["startups"][0]
        assert s["status"] == "ok"
        assert [e["id"] for e in s["evidence"]] == ["D1", "D2", "D3", "D4", "W1"]      # the unsourced line never became evidence
        assert s["audit"]["docs"]["dropped"] == 1
        assert "revenue" not in [f["field"] for f in s["profile"]["facts"]]             # the invented fact was dropped
        assert s["audit"]["checks"]["unsupported"] == 1
        assert s["meeting"]["recommend"] is True and s["sizing"]["recommended"] in ("full", "staged")
        assert s["sizing"]["ceiling"] == 3_500_000
        assert any("9999" in f for f in run["result"]["comparison"]["flags"])           # an ungrounded figure in the briefing is flagged
        assert [p["key"] for p in run["result"]["plans"]] == ["recommended", "meet_first"]   # with one start-up, "top only" repeats the first plan
        assert [n["workstream"] for n in s["support"]["needs"]] == ["investors"] and s["support"]["dropped"] == 1
        assert s["support"]["who_to_bring"] == ["venture funds that lead seed rounds"] == s["pack"]["who_to_bring"]
        events = store.events_after(run_id, 0)
        assert any(e["step"] == "support" and e["status"] == "done" for e in events)
        assert any(e["step"] == "web" and e["text"].startswith("Search:") for e in events)
        assert events[-1]["status"] == "done"
        schema = schemas.extraction_schema(POLICY)
        assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
    finally:
        llm.set_stub(None)


# ------------------------------------------------------------------ the buttons, through the HTTP layer

def test_the_buttons_through_the_http_layer():
    from fastapi.testclient import TestClient

    from ssd import server
    sys.path.insert(0, str(ROOT / "scripts"))
    import refresh_recording

    client = TestClient(server.app)
    run_id = scripted_run()
    url = f"/api/runs/{run_id}"
    s = client.get(url).json()["result"]["startups"][0]
    assert s["meeting"]["recommend"] is True

    def press(**body):
        return client.post(url + "/decisions", json={"by": "Tester", **body})

    def mine(reply):
        return reply.json()["state"]["startups"]["A"]

    # No, on a start-up the app would meet: recorded, and marked as overruling the app.
    no = mine(press(kind="meeting", startup_id="A", value="no"))["meeting"]
    assert no["value"] == "no" and no["overrode"] is True and no["by"] == "Tester"
    # Yes: the latest press is the one that stands, and it agrees with the app.
    yes = mine(press(kind="meeting", startup_id="A", value="yes"))["meeting"]
    assert yes["value"] == "yes" and yes["overrode"] is False
    # Accept one investment option that is not the app's pick.
    other = next(o["key"] for o in s["sizing"]["options"] if o["key"] != s["sizing"]["recommended"])
    picked = mine(press(kind="investment", startup_id="A", value=other))["investment"]
    assert picked["value"] == other and picked["overrode"] is True
    # Accept a whole-batch plan: one click records the meeting and the capital decision for every start-up.
    reply = press(kind="plan", value="recommended")
    assert reply.json()["state"]["plan"] == "recommended"
    assert mine(reply)["meeting"]["value"] == "yes" and mine(reply)["investment"]["value"] == s["sizing"]["recommended"]
    assert len(reply.json()["log"]) == 6                     # three single presses, the plan, and its two decisions
    # A press that makes no sense is refused and leaves no trace.
    for bad in ({"kind": "meeting", "startup_id": "A", "value": "maybe"}, {"kind": "meeting", "startup_id": "Z", "value": "yes"},
                {"kind": "investment", "startup_id": "A", "value": "everything"}, {"kind": "plan", "value": "nope"},
                {"kind": "shrug", "value": "x"}):
        assert press(**bad).status_code == 400
    assert len(client.get(url).json()["decisions"]["log"]) == 6
    assert client.post("/api/runs/R-missing/decisions", json={"kind": "meeting", "startup_id": "A", "value": "yes"}).status_code == 404

    memo = client.get(url + "/memo")
    assert memo.status_code == 200 and "Plan accepted: Follow the app's read on each start-up." in memo.text
    assert "Reviewer's decision, first meeting: YES" in memo.text and "What it needs from the team:" in memo.text

    # The weights panel: the ratings stay, the rules run again, and no model is called.
    keys = [c["key"] for c in POLICY["criteria"]]
    result = store.get_run(run_id)["result"]
    for r in result["startups"][0]["profile"]["ratings"]:
        if r["criterion"] == keys[0]:
            r["score"] = 1                                   # one weak criterion, so the weights matter
    store.set_run(run_id, result=result)
    assert client.post(url + "/weights", json={"weights": {k: 0 for k in keys}}).status_code == 400
    even = client.post(url + "/weights", json={"weights": {}}).json()["result"]
    assert even["weights"] is None                           # the default weights are not stored as the reviewer's own
    assert even["startups"][0]["meeting"]["recommend"] is True
    heavy = client.post(url + "/weights", json={"weights": {keys[0]: 100, **{k: 1 for k in keys[1:]}}}).json()["result"]
    total = heavy["startups"][0]["scores"]["total"]
    assert heavy["weights"][keys[0]] == 100 and total < even["startups"][0]["scores"]["total"] < s["scores"]["total"]
    assert total < POLICY["decision_rules"]["meet_min_score"]
    assert heavy["startups"][0]["meeting"]["recommend"] is False        # the app's read follows the new score
    assert heavy["startups"][0]["sizing"]["recommended"] == "hold"
    assert heavy["ranking"] == ["A"] and [p["key"] for p in heavy["plans"]] == ["recommended"]   # one start-up, one way to act

    # A start-up with too little on file: the app makes no call, and the buttons still work.
    thin = store.get_run(run_id)["result"]
    thin["weights"] = None
    thin["startups"][0]["profile"]["facts"] = [f for f in thin["startups"][0]["profile"]["facts"]
                                               if f["field"] in ("singapore_registered", "research_based_ip")]
    thin = refresh_recording.refresh(thin, POLICY)
    assert thin["startups"][0]["meeting"]["recommend"] is None and thin["plans"][0]["undecided"] == 1
    assert "Before we set up a first meeting" in thin["startups"][0]["pack"]["request_draft"]
    thin_id = store.create_run(store.get_run(run_id)["batch"], {"judge_model": "x", "reader_model": "y"}, "live")
    store.set_run(thin_id, status="done", result=thin)
    reply = client.post(f"/api/runs/{thin_id}/decisions", json={"kind": "plan", "value": "recommended"})
    assert "meeting" not in reply.json()["state"]["startups"]["A"]      # no call from the app, so the plan leaves the meeting to the reviewer
    reply = client.post(f"/api/runs/{thin_id}/decisions", json={"kind": "meeting", "startup_id": "A", "value": "yes"})
    row = reply.json()["log"][-1]
    assert row["recommended"] == "none" and row["overrode"] == 0         # a yes where the app made no call overrules nothing
    assert "no call, too little evidence" in client.get(f"/api/runs/{thin_id}/memo").text


def test_upload_and_calculator_through_the_http_layer():
    from fastapi.testclient import TestClient

    from ssd import server

    client = TestClient(server.app)
    batch = client.post("/api/batch").json()
    assert [s["id"] for s in batch["startups"]] == ["A", "B", "C"] and batch["startups"][0]["country"] == ""
    up = client.post(f"/api/batch/{batch['id']}/startups/A/docs",
                     files=[("files", ("deck.txt", b"Testco has 14 full-time staff.", "text/plain")),
                            ("files", ("tool.exe", b"MZ", "application/octet-stream"))]).json()
    mine = up["batch"]["startups"][0]["docs"]
    assert [d["name"] for d in mine] == ["deck.txt"] and len(up["errors"]) == 1       # the wrong file type is refused, with a reason
    opened = client.get(f"/api/file/{batch['id']}/A/{mine[0]['id']}")
    assert opened.status_code == 200 and b"14 full-time staff" in opened.content      # a source link opens the uploaded file
    assert client.get(f"/api/file/{batch['id']}/A/nope").status_code == 404
    gone = client.delete(f"/api/batch/{batch['id']}/startups/A/docs/{mine[0]['id']}").json()
    assert gone["startups"][0]["docs"] == []
    named = client.put(f"/api/batch/{batch['id']}", json={"startups": [{"id": "A", "name": "  Testco  ", "one_liner": "Battery recycling"}]}).json()
    assert [s["name"] for s in named["startups"]] == ["Testco"] and named["startups"][0]["country"] == ""   # no country is assumed
    cap = client.post("/api/ceiling", json={"track": "deep_tech", "co_amount": 3_000_000, "prior_gov": 0, "first_round": True}).json()
    assert cap["gov"] == 3_500_000
    assert client.post("/api/ceiling", json={"track": "nope"}).status_code == 400
    assert client.post("/api/runs", json={"batch_id": batch["id"], "mode": "sideways"}).status_code == 400
    # a server keeps the code it started with: when the files on disk are newer, the screen is told to ask for a restart
    assert client.get("/api/status").json()["code"] == {"stale": False}
    started, server.STARTED_WITH = server.STARTED_WITH, 0.0
    try:
        assert client.get("/api/status").json()["code"]["stale"] is True
    finally:
        server.STARTED_WITH = started


def test_needs_are_kept_only_under_a_known_workstream_with_real_evidence():
    raw = {"needs": [
        {"workstream": "gtm_access", "need": "Wants introductions to battery cell makers.", "who_could_help": "battery cell makers", "evidence_ids": ["D1"]},
        {"workstream": "Go-to-market access", "need": "A second need, given by its label.", "who_could_help": "feedstock suppliers", "evidence_ids": ["D1"]},
        {"workstream": "gtm_access", "need": "A third under one heading is cut.", "who_could_help": "y", "evidence_ids": ["D1"]},
        {"workstream": "marketing", "need": "A heading the team does not have.", "who_could_help": "z", "evidence_ids": ["D1"]},
        {"workstream": "talent", "need": "Needs 40 engineers.", "who_could_help": "recruiters", "evidence_ids": []},
        {"workstream": "talent", "need": "Plans to grow the team to 26.", "who_could_help": "recruiters", "evidence_ids": ["D9"]},
    ]}
    needs = schemas.normalise_support(raw, POLICY)
    assert [n["workstream"] for n in needs] == ["gtm_access", "gtm_access", "talent", "talent"]
    assert needs[1]["workstream_label"] == "Go-to-market access"
    ev = {"D1": {"claim": "They asked for introductions to battery cell makers in the region.", "quotes": [{"quote": "introductions to battery cell makers"}]},
          "D9": {"claim": "Use of funds: growing the team to 26.", "quotes": [{"quote": "growing the team to 26"}]}}
    kept, dropped = checks.verify_support(needs, ev)
    assert dropped == 1 and [n["need"] for n in kept] == [needs[0]["need"], needs[1]["need"], "Plans to grow the team to 26."]
    assert kept[-1]["status"] == "quote"
    assert pipeline.who_to_bring(kept) == ["battery cell makers", "feedstock suppliers", "recruiters"]
    assert schemas.normalise_support({"needs": []}, POLICY) == [] and schemas.normalise_support({}, {"workstreams": []}) == []


def test_drafts_carry_no_square_bracket_blanks():
    r = {"name": "Testco", "coverage": {"missing": [{"label": "Revenue", "question": "What is the revenue?"}]}, "gates": [],
         "meeting": {"recommend": True, "reasons": ["Score clears the bar."]}, "narrative": {"meeting_questions": ["What is the revenue?"]},
         "profile": {"risks": [], "team": [{"name": "Dr Mei-Lin Tay", "role": "Chief Executive", "status": "quote"},
                                           {"name": "Arjun Velan", "role": "Chief Technology Officer", "status": "quote"}]}}
    pack = pipeline.meeting_pack(r)
    assert pack["invite_draft"].startswith("Subject: First meeting with Testco\n\nDear Dr Mei-Lin Tay,")
    assert "[" not in pack["invite_draft"] and "[" not in pack["decline_draft"]
    assert pack["invite_draft"].endswith("Kind regards,\n{{reviewer}}")        # the screen signs it with the reviewer's name
    assert "- What is the revenue?" in pack["request_draft"] and "[" not in pack["request_draft"]   # the request asks for exactly what is missing
    r["profile"]["team"] = []
    assert "\n\nHello,\n\n" in pipeline.meeting_pack(r)["invite_draft"]          # nobody named: no invented name, no blank


def test_web_brief_carries_public_fields_only():
    s = {"id": "A", "name": "Testco", "website": "testco.example", "one_liner": "Battery recycling", "country": "Singapore",
         "docs": [{"name": "SECRET.pdf"}]}
    brief = pipeline.web_brief(s)
    assert "Testco" in brief and "SECRET" not in brief


def test_sample_batch_says_it_is_invented_on_the_screen_and_in_the_memo():
    # 18 Nov 2026: after playing the recorded run he asked whether Halcyra, Orvelle and Brevanta are real companies.
    # No result screen and no memo said they are invented: only a toast when the batch is loaded, which a replay never
    # shows. The note now rides with the batch into every result and every memo of the sample batch.
    from fastapi.testclient import TestClient

    from ssd import server
    note = json.loads((ROOT / "data" / "samples" / "batch.json").read_text(encoding="utf-8"))["note"]
    assert "invented" in note and "real" in note
    rec = pipeline.load_recording()
    assert rec["batch"]["sample"] is True and rec["batch"]["note"] == note      # the replay carries the same words
    run_id = store.create_run(rec["batch"], rec["options"], "recorded")
    pipeline.replay(run_id, speed=1e9, max_gap=0.0)
    client = TestClient(server.app)
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "done" and run["batch"]["sample"] is True and run["batch"]["note"] == note
    memo = client.get(f"/api/runs/{run_id}/memo").text
    assert note in memo[:memo.index(rec["result"]["comparison"]["headline"])]  # at the top, before the headline
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    panel = app_js[app_js.index("function renderResults()"):]
    panel = panel[:panel.index("\nfunction ")]
    assert "run.batch.sample" in panel and "run.batch.note" in panel         # the result panel shows it, live or replayed
    # An edited batch is no longer the sample, so its memo says nothing about invented companies.
    stored = store.get_run(run_id)
    stored["batch"] = dict(stored["batch"], sample=False)
    assert note not in server.build_memo(stored) and server.SAMPLE_NOTE not in server.build_memo(stored)


def test_every_sample_company_has_a_dated_check_against_real_companies():
    # 18 Nov 2026: invented names are checked against Singapore's company register and the web, and the check is written
    # down with its date. A renamed or added sample company fails here until it has been checked.
    import re
    names = [s["name"] for s in json.loads((ROOT / "data" / "samples" / "batch.json").read_text(encoding="utf-8"))["startups"]]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    block = readme[readme.index("## What is real and what is invented"):]
    block = block[:block.index("\n## ", 5)]
    line = next((x for x in block.splitlines() if x.startswith("- **Checked against real companies")), "")
    assert re.search(r"\d{1,2} [A-Z][a-z]+ 20\d\d", line), "the check carries no date"
    assert "ACRA" in line
    for n in names:
        assert n.split()[0] in line, f"{n} has no recorded check against real companies"
