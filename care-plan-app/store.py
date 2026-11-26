"""The app's own small database: remembered care plans, alerts and the audit log, in one SQLite file.

Care plans used to live in process memory (lost on every restart, so the text assistant and "call me" broke) and alerts in a JSON
file. SQLite in WAL mode needs no server, copes with several readers and one writer, and keeps a restart harmless. The file holds
patient names and medicines: keep it on a protected disk and out of git (data/ is ignored). SANJEEVANI_DB moves it elsewhere.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

PLAN_TTL_S = int(os.getenv("PLAN_TTL_DAYS", "7")) * 86400

_lock = threading.Lock()
_ready: set = set()


def _path() -> Path:
    return Path(os.getenv("SANJEEVANI_DB") or Path(__file__).parent / "data" / "sanjeevani.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, at REAL NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, key TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS alerts_key ON alerts (key, created_at);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
                                  subject TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS audit_at ON audit (at);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT, detail TEXT);
"""


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    p = _path()
    with _lock:
        if str(p) not in _ready:
            p.parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(p, timeout=10)
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            c.commit()
            c.close()
            _ready.add(str(p))
        c = sqlite3.connect(p, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


# ---------------------------------------------------------------- care plans
def put_plan(plan_id: str, plan: Dict[str, Any]) -> None:
    now = time.time()
    with conn() as c:
        c.execute("DELETE FROM plans WHERE at < ?", (now - PLAN_TTL_S,))
        c.execute("INSERT OR REPLACE INTO plans (id, at, body) VALUES (?, ?, ?)", (plan_id, now, json.dumps(plan, ensure_ascii=False)))


def get_plan(plan_id: str) -> Optional[Dict[str, Any]]:
    with conn() as c:
        row = c.execute("SELECT at, body FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if not row or time.time() - row["at"] > PLAN_TTL_S:
        return None
    return json.loads(row["body"])


# ---------------------------------------------------------------- alerts
def put_alert(a: Dict[str, Any]) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO alerts (id, key, created_at, status, body) VALUES (?, ?, ?, ?, ?)",
                  (a["id"], a["key"], a["created_at"], a["status"], json.dumps(a, ensure_ascii=False)))


def get_alert(alert_id: str) -> Optional[Dict[str, Any]]:
    with conn() as c:
        row = c.execute("SELECT body FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    return json.loads(row["body"]) if row else None


def list_alerts(limit: int = 200) -> List[Dict[str, Any]]:
    with conn() as c:
        rows = c.execute("SELECT body FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [json.loads(r["body"]) for r in rows]


def recent_alert_exists(key: str, since_iso: str) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM alerts WHERE key = ? AND created_at >= ? AND status != 'dismissed' LIMIT 1", (key, since_iso)).fetchone() is not None


# ---------------------------------------------------------------- audit log (append only)
def put_audit(at: str, actor: str, action: str, subject: Optional[str], detail: Optional[Dict[str, Any]]) -> None:
    with conn() as c:
        c.execute("INSERT INTO audit (at, actor, action, subject, detail) VALUES (?, ?, ?, ?, ?)",
                  (at, actor, action, subject, json.dumps(detail, ensure_ascii=False) if detail else None))


def list_audit(limit: int = 200, action: Optional[str] = None) -> List[Dict[str, Any]]:
    with conn() as c:
        if action:
            rows = c.execute("SELECT * FROM audit WHERE action = ? ORDER BY id DESC LIMIT ?", (action, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(r), "detail": json.loads(r["detail"]) if r["detail"] else None} for r in rows]


# ---------------------------------------------------------------- events (what the page polls for)
KEEP_EVENTS = 1000


def put_event(kind: str, ref: Optional[str], detail: Optional[Dict[str, Any]]) -> int:
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with conn() as c:
        cur = c.execute("INSERT INTO events (at, kind, ref, detail) VALUES (?, ?, ?, ?)",
                        (at, kind, ref, json.dumps(detail, ensure_ascii=False) if detail else None))
        c.execute("DELETE FROM events WHERE id <= ?", (cur.lastrowid - KEEP_EVENTS,))
        return cur.lastrowid


def last_event_id() -> int:
    with conn() as c:
        return c.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]


def list_events(since: int, limit: int = 100) -> List[Dict[str, Any]]:
    with conn() as c:
        rows = c.execute("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (since, limit)).fetchall()
    return [{**dict(r), "detail": json.loads(r["detail"]) if r["detail"] else None} for r in rows]
