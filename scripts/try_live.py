"""Run the sample batch (or part of it) from the command line and print what happened.

    python scripts/try_live.py --only A --judge claude-haiku-5-5
    python scripts/try_live.py                       (all three, default models)
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ssd import config, pipeline, server, store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="letters to keep, e.g. A or AB")
    ap.add_argument("--judge", default=config.JUDGE_MODEL)
    ap.add_argument("--reader", default=config.READER_MODEL)
    ap.add_argument("--searches", type=int, default=config.MAX_SEARCHES)
    ap.add_argument("--no-web", action="store_true")
    ap.add_argument("--dump", default="", help="write the full result JSON here")
    args = ap.parse_args()

    config.ensure_api_key()
    batch = server.sample_batch()
    if args.only:
        batch["startups"] = [s for s in batch["startups"] if s["id"] in args.only]
        batch["sample"] = False
        store.save_batch(batch)
    options = {"judge_model": args.judge, "reader_model": args.reader, "web": not args.no_web, "max_searches": args.searches}
    run_id = store.create_run(batch, options, "live")
    t = threading.Thread(target=pipeline.execute, args=(run_id,))
    t0 = time.time()
    t.start()
    seen = 0
    while t.is_alive() or True:
        for ev in store.events_after(run_id, seen):
            seen = ev["seq"]
            print(f"{time.time() - t0:6.1f}s  [{ev['lane']:>3}] {ev['step']:<8} {ev['status']:<5} {ev['text']}")
        if not t.is_alive():
            break
        time.sleep(0.4)
    run = store.get_run(run_id)
    print("\nSTATUS", run["status"], run.get("error") or "", "| usage", json.dumps(run["usage"]))
    if run.get("result"):
        res = run["result"]
        if args.dump:
            Path(args.dump).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print("HEADLINE:", res["comparison"]["headline"])
        for s in res["startups"]:
            if s["status"] != "ok":
                print(s["id"], s["name"], "->", s["status"], s.get("error", ""))
                continue
            print(f"\n== {s['id']} {s['name']}: score {s['scores']['total']}, coverage {s['coverage']['value']}, "
                  f"meeting {'YES' if s['meeting']['recommend'] else 'NO'}, capital {s['sizing']['recommended']} "
                  f"(ceiling {s['sizing']['ceiling']:,})")
            print("   checks:", {k: v for k, v in s["audit"]["checks"].items() if k != "dropped"}, "dropped:", s["audit"]["checks"]["dropped"])
            print("   notes:", s["notes"])
            for e in s["evidence"]:
                if e["kind"] == "web":
                    print(f"   {e['id']} [{e['topic']}] {e['claim'][:150]}  <- {[q['label'] for q in e['quotes']]}")
            for f in s["profile"]["facts"]:
                print(f"   {f['status']:<6} {f['field']:<26} {f['value_text'][:70]}  {f['evidence_ids']}")
            print("   gates:", [(g["key"], g["status"]) for g in s["gates"]])
            print("   ratings:", [(c["key"], c["score"]) for c in s["scores"]["criteria"]])
            print("   competitors:", [(c["name"], c["status"]) for c in s["profile"]["competitors"]])
            print("   sow:", [(w.get("customer", "")[:30], w["pct"]) for w in s["share_of_wallet"]])
            print("   missing:", [m["label"] for m in s["coverage"]["missing"]])
            print("   options:", [(o["key"], o["amount"]) for o in s["sizing"]["options"]], "|", s["sizing"]["why"])
            print("   summary:", s["narrative"].get("summary", "")[:300])
            print("   questions:", s["narrative"].get("meeting_questions", [])[:3])
        print("\nflags:", res["comparison"]["flags"])
        print("plans:", [(p["key"], p["meetings"], p["total"]) for p in res["plans"]])


if __name__ == "__main__":
    main()
