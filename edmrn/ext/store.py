"""SQLite store for EDMRN fork data (ADR-0003: one connection, rollback journal, bound values)."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class StoreError(Exception):
    """The database cannot be opened or migrated."""


MIGRATIONS: list[list[str]] = [
    [
        """CREATE TABLE neutron_routes (
            id            INTEGER PRIMARY KEY,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            from_system   TEXT NOT NULL,
            to_system     TEXT NOT NULL,
            range_ly      REAL NOT NULL CHECK (range_ly > 0),
            boost         TEXT NOT NULL CHECK (boost IN ('x4', 'x6')),
            current_index INTEGER NOT NULL CHECK (current_index >= 0),
            state         TEXT NOT NULL CHECK (state IN ('active', 'completed'))
        )""",
        """CREATE TABLE neutron_waypoints (
            route_id INTEGER NOT NULL REFERENCES neutron_routes(id) ON DELETE CASCADE,
            seq      INTEGER NOT NULL,
            system   TEXT NOT NULL,
            type     TEXT NOT NULL,
            distance REAL NOT NULL,
            jumps    INTEGER NOT NULL,
            status   TEXT NOT NULL CHECK (status IN ('visited', 'unvisited')),
            PRIMARY KEY (route_id, seq)
        )""",
    ],
]
SCHEMA_VERSION = len(MIGRATIONS)
BOOSTS = ('x4', 'x6')
STATUSES = ('visited', 'unvisited')


@dataclass(frozen=True)
class RouteMeta:
    from_system: str
    to_system: str
    range_ly: float
    boost: str


@dataclass(frozen=True)
class RouteSummary:
    id: int
    from_system: str
    to_system: str
    range_ly: float
    boost: str
    current_index: int
    state: str
    updated_at: str
    visited: int
    total: int


@dataclass(frozen=True)
class SavedRoute:
    id: int
    meta: RouteMeta
    current_index: int
    state: str
    waypoints: list


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_meta(meta: Any) -> RouteMeta:
    if not isinstance(meta, RouteMeta):
        raise ValueError("meta must be a RouteMeta")
    for name in ('from_system', 'to_system'):
        value = getattr(meta, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
    if not _is_number(meta.range_ly) or meta.range_ly <= 0:
        raise ValueError("range_ly must be a positive number")
    if meta.boost not in BOOSTS:
        raise ValueError(f"boost must be one of {BOOSTS}")
    return meta


def _validate_waypoints(waypoints: Any) -> list[tuple]:
    if not isinstance(waypoints, list) or not waypoints:
        raise ValueError("waypoints must be a non-empty list")
    rows = []
    for seq, wp in enumerate(waypoints):
        if not isinstance(wp, dict):
            raise ValueError(f"waypoint {seq} is not a dict")
        system, type_, distance = wp.get('system'), wp.get('type'), wp.get('distance')
        jumps, status = wp.get('jumps'), wp.get('status')
        if not isinstance(system, str) or not system.strip():
            raise ValueError(f"waypoint {seq}: system must be a non-empty string")
        if not isinstance(type_, str):
            raise ValueError(f"waypoint {seq}: type must be a string")
        if not _is_number(distance):
            raise ValueError(f"waypoint {seq}: distance must be a number")
        if not isinstance(jumps, int) or isinstance(jumps, bool):
            raise ValueError(f"waypoint {seq}: jumps must be an integer")
        if status not in STATUSES:
            raise ValueError(f"waypoint {seq}: status must be one of {STATUSES}")
        rows.append((seq, system, type_, float(distance), jumps, status))
    return rows


def _validate_index(index: Any, count: int) -> int:
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < count:
        raise ValueError(f"current_index must be within 0..{count - 1}")
    return index


def _state_for(statuses: list[str]) -> str:
    return 'completed' if statuses and statuses[-1] == 'visited' else 'active'


class Store:
    def __init__(self, path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            self._conn.execute('PRAGMA foreign_keys = ON')
            self._migrate()
        except sqlite3.DatabaseError as e:
            raise StoreError(f"Cannot open {self.path}: {e}") from e

    @classmethod
    def open_default(cls) -> 'Store':
        from edmrn.config import AppConfig
        return cls(Path(AppConfig.get_app_data_path()) / 'edmrn.db')

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._conn.execute('BEGIN IMMEDIATE')
            try:
                yield self._conn
            except BaseException:
                self._conn.execute('ROLLBACK')
                raise
            self._conn.execute('COMMIT')

    def _migrate(self) -> None:
        version = self._conn.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            raise StoreError(f"{self.path} has schema {version}; this EDMRN knows {SCHEMA_VERSION}")
        for index in range(version, SCHEMA_VERSION):
            with self._transaction() as conn:
                for statement in MIGRATIONS[index]:
                    conn.execute(statement)
                conn.execute(f'PRAGMA user_version = {int(index + 1)}')

    def _require_route(self, conn, route_id: int) -> None:
        if conn.execute('SELECT 1 FROM neutron_routes WHERE id = ?', (route_id,)).fetchone() is None:
            raise KeyError(route_id)

    def _insert_waypoints(self, conn, route_id: int, rows: list[tuple]) -> None:
        conn.executemany(
            'INSERT INTO neutron_waypoints (route_id, seq, system, type, distance, jumps, status) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)', [(route_id, *row) for row in rows])

    def add_neutron_route(self, meta: RouteMeta, waypoints: list, current_index: int = 0) -> int:
        meta = _validate_meta(meta)
        rows = _validate_waypoints(waypoints)
        _validate_index(current_index, len(rows))
        now = _now()
        with self._transaction() as conn:
            cursor = conn.execute(
                'INSERT INTO neutron_routes (created_at, updated_at, from_system, to_system, range_ly, '
                'boost, current_index, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (now, now, meta.from_system, meta.to_system, float(meta.range_ly), meta.boost,
                 current_index, _state_for([r[5] for r in rows])))
            route_id = cursor.lastrowid
            self._insert_waypoints(conn, route_id, rows)
        return route_id

    def update_neutron_progress(self, route_id: int, current_index: int, statuses: list) -> None:
        if not isinstance(statuses, list) or any(s not in STATUSES for s in statuses):
            raise ValueError(f"statuses must be a list of {STATUSES}")
        with self._transaction() as conn:
            self._require_route(conn, route_id)
            count = conn.execute('SELECT COUNT(*) FROM neutron_waypoints WHERE route_id = ?',
                                 (route_id,)).fetchone()[0]
            if count != len(statuses):
                raise ValueError(f"route {route_id} has {count} waypoints, got {len(statuses)} statuses")
            _validate_index(current_index, count)
            conn.executemany('UPDATE neutron_waypoints SET status = ? WHERE route_id = ? AND seq = ?',
                             [(status, route_id, seq) for seq, status in enumerate(statuses)])
            conn.execute('UPDATE neutron_routes SET current_index = ?, state = ?, updated_at = ? '
                         'WHERE id = ?', (current_index, _state_for(statuses), _now(), route_id))

    def replace_neutron_waypoints(self, route_id: int, meta: RouteMeta, waypoints: list,
                                  current_index: int = 0) -> None:
        meta = _validate_meta(meta)
        rows = _validate_waypoints(waypoints)
        _validate_index(current_index, len(rows))
        with self._transaction() as conn:
            self._require_route(conn, route_id)
            conn.execute('DELETE FROM neutron_waypoints WHERE route_id = ?', (route_id,))
            self._insert_waypoints(conn, route_id, rows)
            conn.execute(
                'UPDATE neutron_routes SET from_system = ?, to_system = ?, range_ly = ?, boost = ?, '
                'current_index = ?, state = ?, updated_at = ? WHERE id = ?',
                (meta.from_system, meta.to_system, float(meta.range_ly), meta.boost, current_index,
                 _state_for([r[5] for r in rows]), _now(), route_id))

    def list_neutron_routes(self) -> list[RouteSummary]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT r.id, r.from_system, r.to_system, r.range_ly, r.boost, r.current_index, "
                "r.state, r.updated_at, COALESCE(SUM(w.status = 'visited'), 0), COUNT(w.seq) "
                "FROM neutron_routes r LEFT JOIN neutron_waypoints w ON w.route_id = r.id "
                "GROUP BY r.id ORDER BY r.updated_at DESC, r.id DESC").fetchall()
        return [RouteSummary(*row) for row in rows]

    def load_neutron_route(self, route_id: int) -> SavedRoute:
        with self._lock:
            route = self._conn.execute(
                'SELECT id, from_system, to_system, range_ly, boost, current_index, state '
                'FROM neutron_routes WHERE id = ?', (route_id,)).fetchone()
            if route is None:
                raise KeyError(route_id)
            waypoints = self._conn.execute(
                'SELECT system, type, distance, jumps, status FROM neutron_waypoints '
                'WHERE route_id = ? ORDER BY seq', (route_id,)).fetchall()
        return SavedRoute(
            id=route[0], meta=RouteMeta(route[1], route[2], route[3], route[4]),
            current_index=route[5], state=route[6],
            waypoints=[{'system': s, 'type': t, 'distance': d, 'jumps': j, 'status': st}
                       for s, t, d, j, st in waypoints])

    def delete_neutron_route(self, route_id: int) -> bool:
        with self._transaction() as conn:
            return conn.execute('DELETE FROM neutron_routes WHERE id = ?', (route_id,)).rowcount > 0
