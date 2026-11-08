"""SQLite store of the crew relay (ADR-0009).

One connection, used on one thread. Every write method is one transaction. The store never parses
envelopes; it keeps the bytes the engine hands it.
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping, Sequence
from typing import NamedTuple

SCHEMA_VERSION = 1

_SCHEMA = (
    "CREATE TABLE groups (group_id TEXT PRIMARY KEY, leader TEXT NOT NULL,"
    " created_at INTEGER NOT NULL)",
    "CREATE TABLE log (group_id TEXT NOT NULL, author TEXT NOT NULL, seq INTEGER NOT NULL,"
    " e BLOB NOT NULL, s BLOB NOT NULL, PRIMARY KEY (group_id, author, seq))",
    "CREATE TABLE heads (group_id TEXT NOT NULL, author TEXT NOT NULL, seq INTEGER NOT NULL,"
    " PRIMARY KEY (group_id, author))",
    "CREATE TABLE roster (group_id TEXT NOT NULL, author TEXT NOT NULL, seq INTEGER NOT NULL,"
    " e BLOB NOT NULL, PRIMARY KEY (group_id, author, seq))",
    "CREATE TABLE quota (group_id TEXT NOT NULL, day INTEGER NOT NULL, author TEXT NOT NULL,"
    " count INTEGER NOT NULL, PRIMARY KEY (group_id, day, author))",
    "CREATE TABLE status (group_id TEXT NOT NULL, author TEXT NOT NULL, e BLOB NOT NULL,"
    " s BLOB NOT NULL, PRIMARY KEY (group_id, author))",
)


class RelayStoreError(Exception):
    pass


class LogRow(NamedTuple):
    author: str
    seq: int
    e: bytes
    s: bytes


class LoadedGroup(NamedTuple):
    leader: str
    heads: dict[str, int]
    roster: list[bytes]


class _Tx:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def __enter__(self) -> None:
        self.db.execute("BEGIN IMMEDIATE")

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.db.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


class RelayStore:
    def __init__(self, path: str | os.PathLike) -> None:
        # isolation_level=None: transactions are explicit (see _Tx).
        self.db = sqlite3.connect(path, isolation_level=None)
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RelayStoreError(
                    f"database schema version {version} is newer than this relay "
                    f"supports ({SCHEMA_VERSION})")
            if version == 0:
                with _Tx(self.db):
                    for stmt in _SCHEMA:
                        self.db.execute(stmt)
                    self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except BaseException:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def _tx(self) -> _Tx:
        return _Tx(self.db)

    def load_group(self, group_id: str) -> LoadedGroup | None:
        r = self.db.execute("SELECT leader FROM groups WHERE group_id = ?", (group_id,)).fetchone()
        if r is None:
            return None
        heads = dict(self.db.execute(
            "SELECT author, seq FROM heads WHERE group_id = ?", (group_id,)))
        return LoadedGroup(r[0], heads, self.roster_bytes(group_id))

    def roster_bytes(self, group_id: str) -> list[bytes]:
        return [r[0] for r in self.db.execute(
            "SELECT e FROM roster WHERE group_id = ? ORDER BY author, seq", (group_id,))]

    def create_group(self, group_id: str, leader: str, created_at: int, genesis: LogRow) -> None:
        with self._tx():
            self.db.execute("INSERT INTO groups (group_id, leader, created_at) VALUES (?, ?, ?)",
                            (group_id, leader, created_at))
            self._insert_log(group_id, [genesis])
            self._insert_roster(group_id, [genesis])

    def add_roster_envelope(self, group_id: str, row: LogRow) -> None:
        with self._tx():
            self._insert_roster(group_id, [row])

    def apply_frame(self, group_id: str, day: int, log_rows: Sequence[LogRow],
                    roster_rows: Sequence[LogRow], heads: Mapping[str, int],
                    counts: Mapping[str, int]) -> bool:
        with self._tx():
            self._insert_log(group_id, log_rows)
            new_roster = self._insert_roster(group_id, roster_rows)
            for author, seq in heads.items():
                self.db.execute(
                    "INSERT INTO heads (group_id, author, seq) VALUES (?, ?, ?) "
                    "ON CONFLICT (group_id, author) DO UPDATE SET seq = excluded.seq",
                    (group_id, author, seq))
            for author, n in counts.items():
                self.db.execute(
                    "INSERT INTO quota (group_id, day, author, count) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (group_id, day, author) "
                    "DO UPDATE SET count = count + excluded.count",
                    (group_id, day, author, n))
            return new_roster

    def _insert_log(self, group_id: str, rows: Sequence[LogRow]) -> None:
        self.db.executemany(
            "INSERT OR IGNORE INTO log (group_id, author, seq, e, s) VALUES (?, ?, ?, ?, ?)",
            [(group_id, r.author, r.seq, r.e, r.s) for r in rows])

    def _insert_roster(self, group_id: str, rows: Sequence[LogRow]) -> bool:
        new = False
        for r in rows:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO roster (group_id, author, seq, e) VALUES (?, ?, ?, ?)",
                (group_id, r.author, r.seq, r.e))
            new = new or cur.rowcount > 0
        return new

    def log_after(self, group_id: str, author: str, after: int, until: int,
                  limit: int) -> list[LogRow]:
        return [LogRow(*r) for r in self.db.execute(
            "SELECT author, seq, e, s FROM log WHERE group_id = ? AND author = ? "
            "AND seq > ? AND seq <= ? ORDER BY seq LIMIT ?",
            (group_id, author, after, until, limit))]

    def quota_used(self, group_id: str, day: int, author: str) -> int:
        r = self.db.execute(
            "SELECT count FROM quota WHERE group_id = ? AND day = ? AND author = ?",
            (group_id, day, author)).fetchone()
        return r[0] if r else 0

    def group_quota_used(self, group_id: str, day: int) -> int:
        return self.db.execute(
            "SELECT COALESCE(SUM(count), 0) FROM quota WHERE group_id = ? AND day = ?",
            (group_id, day)).fetchone()[0]

    def take_past_quota(self, group_id: str, day: int) -> list[tuple[int, int]]:
        with self._tx():
            rows = self.db.execute(
                "SELECT day, SUM(count) FROM quota WHERE group_id = ? AND day < ? "
                "GROUP BY day ORDER BY day", (group_id, day)).fetchall()
            self.db.execute("DELETE FROM quota WHERE group_id = ? AND day < ?", (group_id, day))
        return [(d, t) for d, t in rows]

    def statuses(self, group_id: str) -> list[tuple[str, bytes, bytes]]:
        return [(a, e, s) for a, e, s in self.db.execute(
            "SELECT author, e, s FROM status WHERE group_id = ? ORDER BY author", (group_id,))]

    def save_status(self, group_id: str, author: str, e: bytes, s: bytes) -> None:
        with self._tx():
            self.db.execute(
                "INSERT INTO status (group_id, author, e, s) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (group_id, author) DO UPDATE SET e = excluded.e, s = excluded.s",
                (group_id, author, e, s))
