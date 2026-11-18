"""SQLite storage: batches, runs, the step-by-step events of each run, and the reviewer's decisions."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, created_at TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, batch_id TEXT, created_at TEXT, finished_at TEXT, status TEXT, mode TEXT,
  options TEXT, batch TEXT, usage TEXT, result TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS events (
  run_id TEXT, seq INTEGER, ts REAL, lane TEXT, step TEXT, status TEXT, text TEXT, data TEXT,
  PRIMARY KEY (run_id, seq));
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts TEXT, kind TEXT, startup_id TEXT, value TEXT,
  label TEXT, amount INTEGER, recommended TEXT, overrode INTEGER, by TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


def reset() -> None:
    """Close the connection (tests point SSD_DB at a fresh file)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def x(sql: str, args: list[Any] | tuple[Any, ...] = ()) -> None:
    with _lock:
        c = conn()
        c.execute(sql, args)
        c.commit()


def q(sql: str, args: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args: list[Any] | tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = q(sql, args)
    return rows[0] if rows else None


# ------------------------------------------------------------------ batches

def save_batch(batch: dict[str, Any]) -> dict[str, Any]:
    if not batch.get("id"):
        batch["id"] = new_id("B")
    body = json.dumps(batch, ensure_ascii=False)
    x("INSERT INTO batches (id, created_at, body) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
      [batch["id"], now(), body])
    return batch


def get_batch(batch_id: str) -> dict[str, Any] | None:
    row = one("SELECT body FROM batches WHERE id=?", [batch_id])
    return json.loads(row["body"]) if row else None


# ------------------------------------------------------------------ runs and events

def create_run(batch: dict[str, Any], options: dict[str, Any], mode: str) -> str:
    run_id = new_id("R")
    x("INSERT INTO runs (id, batch_id, created_at, status, mode, options, batch, usage) VALUES (?,?,?,?,?,?,?,?)",
      [run_id, batch["id"], now(), "running", mode, json.dumps(options), json.dumps(batch, ensure_ascii=False), "{}"])
    return run_id


def add_event(run_id: str, lane: str, step: str, status: str, text: str, data: dict[str, Any] | None = None,
              ts: float | None = None) -> None:
    import time

    with _lock:
        c = conn()
        row = c.execute("SELECT COALESCE(MAX(seq), 0) AS n FROM events WHERE run_id=?", [run_id]).fetchone()
        c.execute("INSERT INTO events (run_id, seq, ts, lane, step, status, text, data) VALUES (?,?,?,?,?,?,?,?)",
                  [run_id, int(row["n"]) + 1, ts if ts is not None else time.time(), lane, step, status, text,
                   json.dumps(data or {}, ensure_ascii=False)])
        c.commit()


def events_after(run_id: str, after: int = 0) -> list[dict[str, Any]]:
    rows = q("SELECT seq, ts, lane, step, status, text, data FROM events WHERE run_id=? AND seq>? ORDER BY seq",
             [run_id, after])
    for r in rows:
        r["data"] = json.loads(r["data"] or "{}")
    return rows


def set_run(run_id: str, **fields: Any) -> None:
    cols, args = [], []
    for key, value in fields.items():
        cols.append(f"{key}=?")
        args.append(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)
    x(f"UPDATE runs SET {', '.join(cols)} WHERE id=?", args + [run_id])


def get_run(run_id: str, with_result: bool = True) -> dict[str, Any] | None:
    row = one("SELECT * FROM runs WHERE id=?", [run_id])
    if not row:
        return None
    for key in ("options", "batch", "usage", "result"):
        row[key] = json.loads(row[key]) if row.get(key) else None
    if not with_result:
        row.pop("result", None)
    return row


def list_runs(limit: int = 25) -> list[dict[str, Any]]:
    rows = q("SELECT id, batch_id, created_at, finished_at, status, mode, options, batch, usage FROM runs "
             "ORDER BY created_at DESC LIMIT ?", [limit])
    out = []
    for r in rows:
        batch = json.loads(r["batch"]) if r["batch"] else {}
        usage = json.loads(r["usage"]) if r["usage"] else {}
        out.append({"id": r["id"], "created_at": r["created_at"], "finished_at": r["finished_at"], "status": r["status"],
                    "mode": r["mode"], "names": [s["name"] for s in batch.get("startups", []) if s.get("name", "").strip()],
                    "usd": usage.get("usd", 0), "options": json.loads(r["options"]) if r["options"] else {}})
    return out


# ------------------------------------------------------------------ decisions

def add_decision(run_id: str, kind: str, startup_id: str, value: str, label: str, amount: int | None,
                 recommended: str, overrode: bool, by: str) -> None:
    x("INSERT INTO decisions (run_id, ts, kind, startup_id, value, label, amount, recommended, overrode, by) "
      "VALUES (?,?,?,?,?,?,?,?,?,?)",
      [run_id, now(), kind, startup_id, value, label, amount, recommended, 1 if overrode else 0, by])


def decisions(run_id: str) -> list[dict[str, Any]]:
    return q("SELECT id, ts, kind, startup_id, value, label, amount, recommended, overrode, by FROM decisions "
             "WHERE run_id=? ORDER BY id", [run_id])


def clear_decisions(run_id: str) -> None:
    x("DELETE FROM decisions WHERE run_id=?", [run_id])
