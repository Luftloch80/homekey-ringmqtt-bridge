"""SQLite storage for known tags/HomeKey endpoints and the access log."""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('nfc', 'homekey')),
    identifier TEXT NOT NULL,        -- NFC: UID hex; HomeKey: endpointId hex
    name TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_used_at REAL,
    use_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(kind, identifier)
);

CREATE TABLE IF NOT EXISTS access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,              -- nfc | homekey
    identifier TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    granted INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT ''  -- welche Aktion(en) ausgeloest wurden
);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    # -- credentials -----------------------------------------------------
    def list_credentials(self) -> list[dict]:
        with self._lock, self._connect() as con:
            rows = con.execute(
                "SELECT * FROM credentials ORDER BY kind, name COLLATE NOCASE"
            ).fetchall()
            return [dict(r) for r in rows]

    def add_credential(self, kind: str, identifier: str, name: str) -> dict:
        identifier = identifier.strip().upper()
        with self._lock, self._connect() as con:
            cur = con.execute(
                "INSERT INTO credentials (kind, identifier, name, enabled, created_at) "
                "VALUES (?, ?, ?, 1, ?)",
                (kind, identifier, name.strip(), time.time()),
            )
            row = con.execute(
                "SELECT * FROM credentials WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)

    def update_credential(self, cred_id: int, name: str | None = None, enabled: bool | None = None):
        fields = []
        values = []
        if name is not None:
            fields.append("name = ?")
            values.append(name.strip())
        if enabled is not None:
            fields.append("enabled = ?")
            values.append(1 if enabled else 0)
        if not fields:
            return
        values.append(cred_id)
        with self._lock, self._connect() as con:
            con.execute(f"UPDATE credentials SET {', '.join(fields)} WHERE id = ?", values)

    def delete_credential(self, cred_id: int):
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM credentials WHERE id = ?", (cred_id,))

    def find_credential(self, kind: str, identifier: str) -> dict | None:
        identifier = identifier.strip().upper()
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT * FROM credentials WHERE kind = ? AND identifier = ?",
                (kind, identifier),
            ).fetchone()
            return dict(row) if row else None

    def mark_used(self, cred_id: int):
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE credentials SET last_used_at = ?, use_count = use_count + 1 WHERE id = ?",
                (time.time(), cred_id),
            )

    # -- access log --------------------------------------------------------
    def add_log(self, kind: str, identifier: str, name: str, granted: bool, reason: str, action: str):
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO access_log (ts, kind, identifier, name, granted, reason, action) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), kind, identifier, name, 1 if granted else 0, reason, action),
            )
            con.execute(
                "DELETE FROM access_log WHERE id NOT IN "
                "(SELECT id FROM access_log ORDER BY id DESC LIMIT 500)"
            )

    def recent_log(self, limit: int = 100) -> list[dict]:
        with self._lock, self._connect() as con:
            rows = con.execute(
                "SELECT * FROM access_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
