"""Start the Startup Screening Desk on this machine.

    python run_server.py            -> http://127.0.0.1:8770
    python run_server.py --port 9000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="Startup Screening Desk")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    import uvicorn

    from ssd import config, llm

    config.ensure_api_key()
    live = llm.available()
    print(f"Startup Screening Desk: http://{args.host}:{args.port}")
    print("Live runs: " + ("on" if live["ok"] else "off (" + live.get("reason", "") + ")"))
    uvicorn.run("ssd.server:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
