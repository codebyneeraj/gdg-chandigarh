"""SQLite storage. One table: a report and the ticket drafted from it.

Ticket lifecycle: pending_review -> approved -> collected, or pending_review -> rejected.
Only a human action moves a ticket out of pending_review.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    sector          INTEGER NOT NULL,
    lat             REAL NOT NULL,
    lon             REAL NOT NULL,
    gps_from_user   INTEGER NOT NULL DEFAULT 0,
    image_file      TEXT,
    image_blurred   INTEGER NOT NULL DEFAULT 0,
    synthetic       INTEGER NOT NULL DEFAULT 0,
    vision_status   TEXT NOT NULL,              -- ok | needs_review
    vision_model    TEXT,
    vision_error    TEXT,
    waste_type      TEXT,
    kind            TEXT,
    severity        INTEGER,
    confidence      REAL,
    reason          TEXT,
    ticket_lang     TEXT,
    ticket_title    TEXT,
    ticket_body     TEXT,
    ticket_model    TEXT,
    status          TEXT NOT NULL DEFAULT 'pending_review',
    reviewed_by     TEXT,
    reviewed_at     TEXT,
    review_note     TEXT,
    collected_at    TEXT
);
"""

COLUMNS = ["created_at", "sector", "lat", "lon", "gps_from_user", "image_file", "image_blurred", "synthetic",
           "vision_status", "vision_model", "vision_error", "waste_type", "kind", "severity", "confidence",
           "reason", "ticket_lang", "ticket_title", "ticket_body", "ticket_model", "status"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def insert(self, **fields) -> int:
        cols = [k for k in COLUMNS if k in fields]
        with self.conn() as c:
            cur = c.execute(f"INSERT INTO reports ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            [fields[k] for k in cols])
            return int(cur.lastrowid)

    def get(self, rid: int) -> dict | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone()
            return dict(row) if row else None

    def all(self) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM reports ORDER BY id DESC")]

    def update(self, rid: int, **fields) -> None:
        if not fields:
            return
        with self.conn() as c:
            c.execute(f"UPDATE reports SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                      [*fields.values(), rid])

    def clear(self) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM reports")
