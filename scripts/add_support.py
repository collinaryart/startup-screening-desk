"""Add the 'what it needs from the team' section to the stored recording, from the evidence it already holds.

The recording is a live run kept for playback. When a step is added to the workflow after that run, this script
runs only the new step on the stored evidence (a few cents on the reader model), and stamps the recording so the
screen says the section was added later.

    python scripts/add_support.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ssd import config, llm, pipeline, rules, store  # noqa: E402


def main() -> None:
    config.ensure_api_key()
    path = config.RECORDED_DIR / pipeline.RECORDING
    rec = json.loads(path.read_text(encoding="utf-8"))
    policy = rules.load_policy(refresh=True)
    usage = llm.Usage()
    options = dict(rec["options"])
    for s in rec["result"]["startups"]:
        if s["status"] != "ok":
            continue
        s["support"] = pipeline.map_support(None, s, s["evidence"], s.get("notes", []), options, usage, policy)
        s["pack"] = pipeline.meeting_pack(s)
        print(f"\n{s['id']} {s['name']}: {len(s['support']['needs'])} need(s), dropped {s['support']['dropped']}")
        for n in s["support"]["needs"]:
            print(f"   [{n['status']:<5}] {n['workstream_label']}: {n['need']}  <- {n['evidence_ids']}\n           who could help: {n['who_could_help']}")
        print("   who to bring:", s["support"]["who_to_bring"])
    rec["result"]["policy"]["workstreams"] = policy["workstreams"]
    rec["result"]["policy"]["workstreams_basis"] = policy["workstreams_basis"]
    stamp = {"step": "support", "at": store.now(), "model": options["reader_model"], "usd": round(usage.usd, 4),
             "note": f"The section on what each company needs from the team was added on {store.now()[:10]} by reading this run's stored evidence again."}
    rec["result"]["added_later"] = [a for a in rec["result"].get("added_later", []) if a.get("step") != "support"] + [stamp]
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    run = store.get_run(rec["run_id"])
    if run and run.get("result"):
        store.set_run(rec["run_id"], result=rec["result"])
    print("\nusage", json.dumps(usage.to_dict()))


if __name__ == "__main__":
    main()
