"""Re-run the code-only steps on the stored recording after a rule change: score, first-meeting read, options, plans, drafts.

No model is called: the ratings, facts and evidence stay exactly as the live run produced them.

    python scripts/refresh_recording.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ssd import config, pipeline, rules, store  # noqa: E402


def refresh(result: dict, policy: dict) -> dict:
    for s in result["startups"]:
        if s["status"] != "ok":
            continue
        s["scores"] = rules.score(s["profile"], result.get("weights"), policy)
        s["coverage"] = rules.coverage(s["profile"])
        s["meeting"] = rules.meeting_recommendation(s["gates"], s["scores"]["total"], policy, s["coverage"], s["scores"])
        s["sizing"] = rules.size(s["profile"], s["gates"], s["scores"]["total"], s["coverage"]["value"], policy,
                                 decided=s["meeting"]["recommend"] is not None)
        s["pack"] = pipeline.meeting_pack(s)
    old = {p["key"]: p.get("rationale", "") for p in result.get("plans", [])}
    result["plans"] = rules.build_plans(result["startups"])
    for p in result["plans"]:
        p["rationale"] = old.get(p["key"], "")
    ok = [s for s in result["startups"] if s["status"] == "ok"]
    result["ranking"] = [s["id"] for s in sorted(ok, key=lambda s: s["scores"]["total"], reverse=True)]
    result["policy"] = policy
    return result


def main() -> None:
    policy = rules.load_policy(refresh=True)
    path = config.RECORDED_DIR / pipeline.RECORDING
    rec = json.loads(path.read_text(encoding="utf-8"))
    before = [(s["id"], s["scores"]["total"], s["meeting"]["recommend"], s["sizing"]["recommended"]) for s in rec["result"]["startups"] if s["status"] == "ok"]
    rec["result"] = refresh(rec["result"], policy)
    after = [(s["id"], s["scores"]["total"], s["meeting"]["recommend"], s["sizing"]["recommended"]) for s in rec["result"]["startups"] if s["status"] == "ok"]
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    run = store.get_run(rec["run_id"])
    if run and run.get("result"):
        store.set_run(rec["run_id"], result=rec["result"])
    print("before:", before)
    print("after: ", after)
    print("plans: ", [(p["key"], p["meetings"], p["total"]) for p in rec["result"]["plans"]])
    print("same outcomes:", before == after)


if __name__ == "__main__":
    main()
