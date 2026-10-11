# Neutron Route Save/Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every plotted neutron route is saved automatically to SQLite with its progress, and can be
resumed from a list in the Neutron tab. Resuming understands where the commander is now.

**Architecture:**
- `edmrn/ext/store.py` is the single SQLite store (ADR-0003).
- `edmrn/ext/neutron_resume.py` holds three things:
  - an autosave that diffs the upstream `NeutronRouter` every 2 s on the Tk loop;
  - a pure resume decision;
  - the saved-routes dialog.
- Upstream gets four small hooks: one line in `neutron.py`, two in `ui_components.py`, two in
  `app.py`.

**Tech Stack:** Python 3.13, stdlib `sqlite3`, customtkinter, pytest.

**Spec:** `docs/superpowers/specs/2026-10-11-neutron-route-resume-design.md` (Accepted). Read it with
this plan; the spec wins where they disagree.

## Global Constraints

- **Upstream files** (`edmrn/neutron.py`, `edmrn/ui_components.py`, `edmrn/app.py`):
  - Change only the lines a task names. No reformatting.
  - Keep CRLF line endings. Check with `file <path>` ("with CRLF line terminators") and
    `git diff --stat`.
- **Storage** (ADR-0003):
  - One connection, rollback journal (never `PRAGMA journal_mode=WAL`), `foreign_keys=ON`.
  - Every SQL value is a bound parameter. The only formatted SQL is `PRAGMA user_version = <int>`.
- **Threads:** all store access from the app happens on the Tk thread (autosave tick, dialog).
- **Tests:**
  - Never touch the real data dir; `tests/conftest.py` redirects home, and every DB path in tests
    lives under `tmp_path`.
  - No network in tests.
  - Run with `.venv/Scripts/python.exe -m pytest` from the repo root.
- **No new dependencies.**
- **Git:**
  - Stage explicit paths only.
  - Every commit message ends with:
    ```
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa
    ```
- **Documentation** goes in the same commit as the change it documents.

## Review Focus

1. **Upstream "Load from backup" restores an old `neutron_route.json`** whose waypoints carry an
   unexpected status (e.g. `skipped`) or a non-numeric distance. The autosave must save a
   normalised copy, not log an error every 2 s forever. Pinned in Task 3.
2. **A "new route from here" plot fails** (Spansh 400/502). A later, unrelated plot must not
   silently overwrite the saved route that asked for the replot. Pinned in Task 3: the replacement
   only applies when the destination matches.
3. **The database file is corrupt, or from a newer EDMRN.** The app must start, and the dialog must
   explain why it is unavailable. Pinned in Tasks 1 and 4.
4. **System names with quotes, semicolons or SQL fragments**, from Spansh or later from friends,
   must round-trip verbatim. Pinned in Task 1.
5. **Resuming twice**, or resuming the route that is already active, must not create a duplicate
   row. Pinned in Task 3.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `edmrn/ext/store.py` | SQLite connection, migrations, validated neutron-route CRUD | 1 |
| `tests/test_store.py` | Store tests | 1 |
| `edmrn/neutron.py` (upstream) | `last_request` on success | 2 |
| `tests/test_neutron_last_request.py` | Hook test | 2 |
| `edmrn/ext/neutron_resume.py` | `decide_resume`, `NeutronAutosave`, `resume_route`, `install`, dialog | 3, 4 |
| `tests/test_neutron_resume.py` | Decision, autosave and resume-flow tests | 3, 4 |
| `edmrn/ui_components.py` (upstream) | "Saved routes" button hook | 4 |
| `edmrn/app.py` (upstream) | `install` hook | 4 |
| `docs/architecture/overview.md` | Hook-point rows; neutron persistence data flow | 2, 4 |

---

### Task 1: SQLite store with neutron routes

**Files:**
- Create: `edmrn/ext/store.py`, `tests/test_store.py`

**Interfaces:**
- Produces:
  - `StoreError(Exception)`
  - `RouteMeta(from_system: str, to_system: str, range_ly: float, boost: str)`, a frozen dataclass.
  - `RouteSummary`, a frozen dataclass with the fields `id`, `from_system`, `to_system`, `range_ly`,
    `boost`, `current_index`, `state`, `updated_at`, `visited` and `total`.
  - `SavedRoute(id: int, meta: RouteMeta, current_index: int, state: str, waypoints: list[dict])`,
    a frozen dataclass.
  - `Store(path)` with:
    - `.open_default()` (classmethod) and `.close()`;
    - `.add_neutron_route(meta, waypoints, current_index=0) -> int`;
    - `.update_neutron_progress(route_id, current_index, statuses) -> None`;
    - `.replace_neutron_waypoints(route_id, meta, waypoints, current_index=0) -> None`;
    - `.list_neutron_routes() -> list[RouteSummary]`;
    - `.load_neutron_route(route_id) -> SavedRoute`;
    - `.delete_neutron_route(route_id) -> bool`.
  - Waypoint dicts have exactly the keys `system: str`, `type: str`, `distance: float`,
    `jumps: int` and `status: 'visited' | 'unvisited'`.
  - Errors:
    - invalid input raises `ValueError`;
    - an unknown `route_id` raises `KeyError` (`delete` returns `False` instead);
    - an unopenable database raises `StoreError`.

- [ ] **Step 1: Write the failing tests** (`tests/test_store.py`)

```python
import sqlite3

import pytest

from edmrn.ext.store import RouteMeta, Store, StoreError

META = RouteMeta("Sol", "Colonia", 70.0, "x4")


def wp(system, status="unvisited", type_="Normal", distance=10.0, jumps=1):
    return {"system": system, "type": type_, "distance": distance, "jumps": jumps, "status": status}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "edmrn.db")
    yield s
    s.close()


def test_add_list_load_round_trip(store):
    rid = store.add_neutron_route(META, [wp("Sol", "visited"), wp("A", type_="Neutron"), wp("Colonia")], 1)
    [summary] = store.list_neutron_routes()
    assert (summary.id, summary.from_system, summary.to_system) == (rid, "Sol", "Colonia")
    assert (summary.visited, summary.total, summary.current_index, summary.state) == (1, 3, 1, "active")
    saved = store.load_neutron_route(rid)
    assert saved.meta == META
    assert [w["system"] for w in saved.waypoints] == ["Sol", "A", "Colonia"]
    assert saved.waypoints[1] == wp("A", type_="Neutron")


def test_progress_update_and_completion(store):
    rid = store.add_neutron_route(META, [wp("Sol"), wp("Colonia")])
    store.update_neutron_progress(rid, 1, ["visited", "unvisited"])
    assert store.load_neutron_route(rid).state == "active"
    store.update_neutron_progress(rid, 1, ["visited", "visited"])
    saved = store.load_neutron_route(rid)
    assert saved.state == "completed" and saved.current_index == 1


def test_progress_rejects_wrong_length_or_index(store):
    rid = store.add_neutron_route(META, [wp("Sol"), wp("Colonia")])
    with pytest.raises(ValueError):
        store.update_neutron_progress(rid, 0, ["visited"])
    with pytest.raises(ValueError):
        store.update_neutron_progress(rid, 5, ["visited", "visited"])
    with pytest.raises(KeyError):
        store.update_neutron_progress(999, 0, ["visited"])


def test_replace_waypoints(store):
    rid = store.add_neutron_route(META, [wp("Sol"), wp("Colonia")])
    store.replace_neutron_waypoints(rid, RouteMeta("Here", "Colonia", 70.0, "x6"),
                                    [wp("Here"), wp("B"), wp("Colonia")])
    saved = store.load_neutron_route(rid)
    assert saved.meta.from_system == "Here" and saved.meta.boost == "x6"
    assert len(saved.waypoints) == 3 and saved.current_index == 0


def test_delete_cascades(store, tmp_path):
    rid = store.add_neutron_route(META, [wp("Sol"), wp("Colonia")])
    assert store.delete_neutron_route(rid) is True
    assert store.delete_neutron_route(rid) is False
    raw = sqlite3.connect(tmp_path / "edmrn.db")
    assert raw.execute("SELECT COUNT(*) FROM neutron_waypoints").fetchone()[0] == 0
    raw.close()


@pytest.mark.parametrize("meta", [
    RouteMeta("", "Colonia", 70.0, "x4"),
    RouteMeta("Sol", "Colonia", 0.0, "x4"),
    RouteMeta("Sol", "Colonia", 70.0, "x5"),
    RouteMeta("Sol", None, 70.0, "x4"),
])
def test_invalid_meta_rejected(store, meta):
    with pytest.raises(ValueError):
        store.add_neutron_route(meta, [wp("Sol")])


@pytest.mark.parametrize("bad", [
    [],
    [{"system": "Sol"}],
    [wp("")],
    [wp("Sol", status="skipped")],
    [wp("Sol", distance="far")],
    [wp("Sol", jumps=True)],
    ["Sol"],
])
def test_invalid_waypoints_rejected(store, bad):
    with pytest.raises(ValueError):
        store.add_neutron_route(META, bad)


def test_hostile_names_round_trip(store):
    names = ["Robert'); DROP TABLE neutron_routes;--", 'Quote " Star', "Semi;colon"]
    rid = store.add_neutron_route(RouteMeta(names[0], names[1], 70.0, "x4"), [wp(n) for n in names])
    saved = store.load_neutron_route(rid)
    assert [w["system"] for w in saved.waypoints] == names
    assert saved.meta.from_system == names[0]


def test_reopen_keeps_data_and_schema(tmp_path):
    first = Store(tmp_path / "edmrn.db")
    rid = first.add_neutron_route(META, [wp("Sol")])
    first.close()
    second = Store(tmp_path / "edmrn.db")
    assert second.load_neutron_route(rid).meta == META
    second.close()


def test_newer_schema_raises(tmp_path):
    raw = sqlite3.connect(tmp_path / "edmrn.db")
    raw.execute("PRAGMA user_version = 99")
    raw.close()
    with pytest.raises(StoreError, match="schema 99"):
        Store(tmp_path / "edmrn.db")


def test_corrupt_file_raises(tmp_path):
    (tmp_path / "edmrn.db").write_bytes(b"this is not a database" * 100)
    with pytest.raises(StoreError):
        Store(tmp_path / "edmrn.db")


def test_open_default_uses_isolated_app_data_dir(isolated_home):
    s = Store.open_default()
    try:
        assert str(isolated_home) in s.path and s.path.endswith("edmrn.db")
    finally:
        s.close()


def test_rollback_journal_and_foreign_keys(store):
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_store.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'edmrn.ext.store'`.

- [ ] **Step 3: Implement `edmrn/ext/store.py`**

```python
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
```

- [ ] **Step 4: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add edmrn/ext/store.py tests/test_store.py
git commit -m "feat(store): SQLite store with validated neutron route records (ADR-0003)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 2: `NeutronRouter.last_request` hook

**Files:**
- Modify: `edmrn/neutron.py`. Add one line in `calculate_route`, directly before the success
  `return {` that contains `"success": True`.
- Test: `tests/test_neutron_last_request.py`
- Docs: `docs/architecture/overview.md` (append to the existing `edmrn/neutron.py` row's change
  text: "; records `last_request` (from, to, range, boost) on success")

**Interfaces:**
- Produces: after a successful `calculate_route`, `router.last_request` is
  `{"from": <start actually sent>, "to": to_system, "range": jump_range, "boost": "x4"|"x6"}`.
  After a failure the attribute is unchanged.

- [ ] **Step 1: Write the failing test**

```python
from types import SimpleNamespace

import edmrn.neutron as neutron
from edmrn.ext.spansh import RouteSubmission


def _fake_submit(status, from_used="Sol"):
    body = {"job": "J1"} if status == 202 else {"error": "Could not find starting system"}
    response = SimpleNamespace(status_code=status, json=lambda: body, text="", reason="")
    return lambda *a, **k: RouteSubmission(response, None, from_used)


def test_last_request_set_on_success(monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job", _fake_submit(202, "Nearest"))
    router = neutron.NeutronRouter()
    monkeypatch.setattr(router, "_wait_for_result", lambda job, cb=None: {
        "success": True, "data": {"result": {"system_jumps": [
            {"system": "Nearest", "distance_jumped": 0, "jumps": 0, "neutron_star": False},
            {"system": "Colonia", "distance_jumped": 100, "jumps": 2, "neutron_star": True}]}}})

    result = router.calculate_route("Unknown", "Colonia", 70.0, "x6")

    assert result["success"]
    assert router.last_request == {"from": "Nearest", "to": "Colonia", "range": 70.0, "boost": "x6"}


def test_last_request_untouched_on_failure(monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job", _fake_submit(400))
    router = neutron.NeutronRouter()
    router.last_request = {"keep": True}

    assert not router.calculate_route("Sool", "Colonia", 70.0)["success"]
    assert router.last_request == {"keep": True}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_neutron_last_request.py -v`
Expected: `test_last_request_set_on_success` FAILS with `AttributeError: 'NeutronRouter' object has no
attribute 'last_request'`.

Before implementing, read `calculate_route` as it is now. The API-hardening work may have changed
it. If the names `used_from`, `to_system`, `jump_range` or `fsd_boost` differ, use the current
names for the same values, and report the difference.

- [ ] **Step 3: Insert the line** (CRLF, same indentation as the surrounding `return`)

```python
            self.last_request = {"from": used_from, "to": to_system, "range": jump_range, "boost": fsd_boost}
```

- [ ] **Step 4: Run all tests.** Expected: all pass. `git diff --stat edmrn/neutron.py` shows
  `1 insertion(+)`.

- [ ] **Step 5: Docs** (see Files), then commit:

```bash
git add edmrn/neutron.py tests/test_neutron_last_request.py docs/architecture/overview.md
git commit -m "feat(neutron): remember the last successful route request" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 3: Resume decision, autosave and resume flow (no UI)

**Files:**
- Create: `edmrn/ext/neutron_resume.py`, `tests/test_neutron_resume.py`

**Interfaces:**
- Consumes: Task 1's `Store`, `RouteMeta`, `SavedRoute`; Task 2's `router.last_request`.
- Uses these upstream app attributes:
  - `app.root.after(ms, fn)` and `app.neutron_router`, with `.last_route` (list of waypoint dicts)
    and `.current_waypoint_index`;
  - `app.neutron_range_var` and `app.neutron_boost_var` (`get`/`set`; boost values `"x4 (Normal)"`
    and `"x6 (Caspian)"`);
  - `app.neutron_from_autocomplete` and `app.neutron_to_autocomplete` (`get`/`set`);
  - `app.neutron_manager`, with `.update_neutron_navigation()`, `.handle_neutron_system_jump(name)`,
    `.copy_current_neutron_system(auto=True)` and `.get_current_system_from_journal()`;
  - `app._calculate_neutron_route()`.
- Produces:
  - `Decision(kind: 'continue'|'advance'|'ask', index: int)`
  - `decide_resume(systems: list[str], saved_index: int, current: str | None) -> Decision`
  - `NeutronAutosave(app, store)` with:
    - `.start()`, `.stop()`, `.check()`;
    - `.adopt(route_obj, route_id)`;
    - `.request_replace(route_id, to_system)`.
  - `resume_route(app, store, autosave, route_id, ask) -> Decision`, where
    `ask(app, saved, current) -> 'back' | 'replot' | None`.

- [ ] **Step 1: Write the failing tests** (`tests/test_neutron_resume.py`)

```python
from types import SimpleNamespace

import pytest

from edmrn.ext.neutron_resume import Decision, NeutronAutosave, decide_resume, resume_route
from edmrn.ext.store import RouteMeta, Store

SYSTEMS = ["Sol", "A", "B", "C", "Colonia"]


@pytest.mark.parametrize("current,expected", [
    (None, Decision("continue", 2)),
    ("  ", Decision("continue", 2)),
    ("A", Decision("continue", 2)),
    ("b", Decision("continue", 2)),
    (" C ", Decision("advance", 3)),
    ("Colonia", Decision("advance", 4)),
    ("Sol", Decision("ask", 2)),
    ("Elsewhere", Decision("ask", 2)),
])
def test_decide_resume(current, expected):
    assert decide_resume(SYSTEMS, 2, current) == expected


def test_decide_resume_at_start_index_zero():
    assert decide_resume(SYSTEMS, 0, "Sol") == Decision("continue", 0)


class Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def waypoint(system, status="unvisited"):
    return {"system": system, "type": "Normal", "distance": 10.0, "jumps": 1, "status": status}


def make_app(route=None, index=0, last_request=None):
    router = SimpleNamespace(last_route=route or [], current_waypoint_index=index)
    if last_request is not None:
        router.last_request = last_request
    calls = []
    manager = SimpleNamespace(
        update_neutron_navigation=lambda: calls.append("nav"),
        handle_neutron_system_jump=lambda name: calls.append(("jump", name)),
        copy_current_neutron_system=lambda auto=False: calls.append("copy"),
        get_current_system_from_journal=lambda: None)
    app = SimpleNamespace(
        root=SimpleNamespace(after=lambda ms, fn: calls.append(("after", ms))),
        neutron_router=router, neutron_manager=manager,
        neutron_range_var=Var("70"), neutron_boost_var=Var("x4 (Normal)"),
        neutron_from_autocomplete=Var(), neutron_to_autocomplete=Var(),
        _calculate_neutron_route=lambda: calls.append("calculate"))
    return app, calls


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "edmrn.db")
    yield s
    s.close()


def test_new_route_inserted_once_with_last_request(store):
    route = [waypoint("Nearest"), waypoint("Colonia")]
    app, _ = make_app(route, last_request={"from": "Nearest", "to": "Colonia", "range": 65.5, "boost": "x6"})
    autosave = NeutronAutosave(app, store)
    autosave.check()
    autosave.check()
    [summary] = store.list_neutron_routes()
    assert (summary.from_system, summary.to_system, summary.range_ly, summary.boost) == ("Nearest", "Colonia", 65.5, "x6")


def test_meta_falls_back_to_waypoints_and_tab_fields(store):
    app, _ = make_app([waypoint("Sol"), waypoint("Colonia")])
    app.neutron_range_var.set("48.2")
    app.neutron_boost_var.set("x6 (Caspian)")
    NeutronAutosave(app, store).check()
    [summary] = store.list_neutron_routes()
    assert (summary.from_system, summary.to_system, summary.range_ly, summary.boost) == ("Sol", "Colonia", 48.2, "x6")


def test_stale_last_request_for_other_destination_is_ignored(store):
    app, _ = make_app([waypoint("Sol"), waypoint("Colonia")],
                      last_request={"from": "X", "to": "Beagle Point", "range": 30.0, "boost": "x4"})
    NeutronAutosave(app, store).check()
    assert store.list_neutron_routes()[0].to_system == "Colonia"


def test_progress_change_updates_row_and_completes(store):
    route = [waypoint("Sol"), waypoint("Colonia")]
    app, _ = make_app(route)
    autosave = NeutronAutosave(app, store)
    autosave.check()
    route[0]["status"] = "visited"
    app.neutron_router.current_waypoint_index = 1
    autosave.check()
    rid = store.list_neutron_routes()[0].id
    assert store.load_neutron_route(rid).current_index == 1
    route[1]["status"] = "visited"
    autosave.check()
    assert store.load_neutron_route(rid).state == "completed"
    assert len(store.list_neutron_routes()) == 1


def test_upstream_backup_with_odd_values_is_normalised(store):
    route = [{"system": "Sol", "type": "Neutron", "distance": "n/a", "jumps": None, "status": "skipped"},
             {"system": "Colonia", "status": "visited"}]
    app, _ = make_app(route)
    NeutronAutosave(app, store).check()
    saved = store.load_neutron_route(store.list_neutron_routes()[0].id)
    assert [w["status"] for w in saved.waypoints] == ["unvisited", "visited"]
    assert saved.waypoints[0]["distance"] == 0.0 and saved.waypoints[0]["jumps"] == 0


def test_unsaveable_route_is_logged_once_not_retried(store, monkeypatch):
    app, _ = make_app([waypoint("Sol")])
    app.neutron_range_var.set("not a number")
    autosave = NeutronAutosave(app, store)
    errors = []
    monkeypatch.setattr("edmrn.ext.neutron_resume.logger.error", lambda msg: errors.append(msg))
    autosave.check()
    autosave.check()
    assert store.list_neutron_routes() == [] and len(errors) == 1


def test_store_failure_is_retried_next_tick(store, monkeypatch):
    app, _ = make_app([waypoint("Sol"), waypoint("Colonia")])
    autosave = NeutronAutosave(app, store)
    original = store.add_neutron_route
    attempts = []

    def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            import sqlite3
            raise sqlite3.OperationalError("database is locked")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "add_neutron_route", flaky)
    autosave._tick()
    autosave._tick()
    assert len(store.list_neutron_routes()) == 1


def test_tick_reschedules_itself(store):
    app, calls = make_app()
    autosave = NeutronAutosave(app, store)
    autosave.start()
    autosave._tick()
    assert calls.count(("after", 2000)) == 2


def _saved(store, index=1):
    return store.add_neutron_route(RouteMeta("Sol", "Colonia", 70.0, "x4"),
                                   [waypoint("Sol", "visited"), waypoint("A"), waypoint("Colonia")], index)


def test_resume_continue_does_not_duplicate(store):
    rid = _saved(store)
    app, calls = make_app()
    autosave = NeutronAutosave(app, store)
    decision = resume_route(app, store, autosave, rid, ask=lambda *a: pytest.fail("no ask expected"))
    autosave.check()
    resume_route(app, store, autosave, rid, ask=lambda *a: None)
    autosave.check()
    assert decision.kind == "continue" and "nav" in calls
    assert app.neutron_router.current_waypoint_index == 1
    assert len(store.list_neutron_routes()) == 1


def test_resume_advance_uses_upstream_jump_handler(store):
    rid = _saved(store)
    app, calls = make_app()
    app.neutron_manager.get_current_system_from_journal = lambda: "Colonia"
    resume_route(app, store, NeutronAutosave(app, store), rid, ask=lambda *a: pytest.fail("no ask"))
    assert ("jump", "Colonia") in calls


def test_resume_ask_back_copies_waypoint(store):
    rid = _saved(store)
    app, calls = make_app()
    app.neutron_manager.get_current_system_from_journal = lambda: "Elsewhere"
    resume_route(app, store, NeutronAutosave(app, store), rid, ask=lambda *a: "back")
    assert "copy" in calls and "calculate" not in calls


def test_resume_ask_replot_replaces_saved_route(store):
    rid = _saved(store)
    app, calls = make_app()
    app.neutron_manager.get_current_system_from_journal = lambda: "Elsewhere"
    autosave = NeutronAutosave(app, store)
    resume_route(app, store, autosave, rid, ask=lambda *a: "replot")
    assert "calculate" in calls
    assert (app.neutron_from_autocomplete.get(), app.neutron_to_autocomplete.get()) == ("Elsewhere", "Colonia")
    assert app.neutron_range_var.get() == "70" and app.neutron_boost_var.get() == "x4 (Normal)"

    app.neutron_router.last_route = [waypoint("Elsewhere"), waypoint("B"), waypoint("Colonia")]
    app.neutron_router.current_waypoint_index = 0
    app.neutron_router.last_request = {"from": "Elsewhere", "to": "Colonia", "range": 70.0, "boost": "x4"}
    autosave.check()

    [summary] = store.list_neutron_routes()
    assert summary.id == rid and summary.from_system == "Elsewhere" and summary.total == 3


def test_failed_replot_does_not_hijack_unrelated_route(store):
    rid = _saved(store)
    app, _ = make_app()
    app.neutron_manager.get_current_system_from_journal = lambda: "Elsewhere"
    autosave = NeutronAutosave(app, store)
    resume_route(app, store, autosave, rid, ask=lambda *a: "replot")
    app.neutron_router.last_route = [waypoint("Sol"), waypoint("Beagle Point")]
    autosave.check()
    assert len(store.list_neutron_routes()) == 2
    assert store.load_neutron_route(rid).meta.to_system == "Colonia"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_neutron_resume.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'edmrn.ext.neutron_resume'`.

- [ ] **Step 3: Implement `edmrn/ext/neutron_resume.py`** (non-UI part)

```python
"""Save neutron routes automatically and resume them (spec 2026-10-11-neutron-route-resume-design)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from edmrn.ext.store import RouteMeta, SavedRoute, Store, StoreError
from edmrn.logger import get_logger

logger = get_logger('NeutronResume')

BOOST_LABELS = {'x4': 'x4 (Normal)', 'x6': 'x6 (Caspian)'}


@dataclass(frozen=True)
class Decision:
    kind: str
    index: int


def _key(name: Any) -> str:
    return name.strip().casefold() if isinstance(name, str) else ''


def decide_resume(systems: list, saved_index: int, current: Optional[str]) -> Decision:
    """Where to continue a saved route, given the commander's current system."""
    key = _key(current)
    if not key:
        return Decision('continue', saved_index)
    for i, system in enumerate(systems):
        if i >= saved_index - 1 and _key(system) == key:
            if i <= saved_index:
                return Decision('continue', saved_index)
            return Decision('advance', i)
    return Decision('ask', saved_index)


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _normalise(route: list) -> list:
    """Copy of upstream waypoint dicts in the store's shape; odd upstream values are coerced."""
    result = []
    for wp in route:
        wp = wp if isinstance(wp, dict) else {}
        jumps = wp.get('jumps')
        result.append({
            'system': wp.get('system') if isinstance(wp.get('system'), str) else '',
            'type': wp.get('type') if isinstance(wp.get('type'), str) else 'Normal',
            'distance': _number(wp.get('distance'), 0.0),
            'jumps': jumps if isinstance(jumps, int) and not isinstance(jumps, bool) else 0,
            'status': 'visited' if wp.get('status') == 'visited' else 'unvisited',
        })
    return result


class NeutronAutosave:
    INTERVAL_MS = 2000

    def __init__(self, app, store: Store) -> None:
        self.app = app
        self.store = store
        self._route_obj = None
        self._route_id: Optional[int] = None
        self._snapshot = None
        self._pending: Optional[tuple[int, str]] = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._schedule()

    def stop(self) -> None:
        self._running = False

    def _schedule(self) -> None:
        try:
            self.app.root.after(self.INTERVAL_MS, self._tick)
        except Exception as e:
            logger.debug(f"Neutron autosave not rescheduled: {e}")

    def _tick(self) -> None:
        try:
            self.check()
        except Exception as e:
            logger.warning(f"Neutron autosave will retry: {e}")
        finally:
            if self._running:
                self._schedule()

    def adopt(self, route_obj: list, route_id: int) -> None:
        """Map a route the user resumed to its row, so it is not inserted again."""
        self._route_obj, self._route_id = route_obj, route_id
        self._snapshot = self._snap(route_obj)
        self._pending = None

    def request_replace(self, route_id: int, to_system: str) -> None:
        """The next new route to `to_system` replaces the waypoints of `route_id`."""
        self._pending = (route_id, _key(to_system))

    def _snap(self, route: list) -> tuple:
        router = self.app.neutron_router
        return (router.current_waypoint_index,
                tuple(wp.get('status') if isinstance(wp, dict) else None for wp in route))

    def _index(self, count: int) -> int:
        index = self.app.neutron_router.current_waypoint_index
        if not isinstance(index, int) or isinstance(index, bool):
            return 0
        return min(max(index, 0), count - 1)

    def _meta(self, waypoints: list) -> RouteMeta:
        request = getattr(self.app.neutron_router, 'last_request', None)
        to_system = waypoints[-1]['system']
        if isinstance(request, dict) and _key(request.get('to')) == _key(to_system):
            return RouteMeta(request.get('from') or waypoints[0]['system'], to_system,
                             request.get('range'), request.get('boost'))
        try:
            range_ly = float(self.app.neutron_range_var.get())
        except (TypeError, ValueError):
            range_ly = 0.0
        boost = 'x6' if 'x6' in str(self.app.neutron_boost_var.get()) else 'x4'
        return RouteMeta(waypoints[0]['system'], to_system, range_ly, boost)

    def check(self) -> None:
        route = self.app.neutron_router.last_route
        if not route:
            return
        snapshot = self._snap(route)
        if route is not self._route_obj:
            self._save_new(route, snapshot)
        elif self._route_id is not None and snapshot != self._snapshot:
            waypoints = _normalise(route)
            self.store.update_neutron_progress(self._route_id, self._index(len(waypoints)),
                                               [wp['status'] for wp in waypoints])
            self._snapshot = snapshot

    def _save_new(self, route: list, snapshot: tuple) -> None:
        waypoints = _normalise(route)
        index = self._index(len(waypoints))
        try:
            meta = self._meta(waypoints)
            if self._pending and self._pending[1] == _key(meta.to_system):
                route_id = self._pending[0]
                self.store.replace_neutron_waypoints(route_id, meta, waypoints, index)
            else:
                route_id = self.store.add_neutron_route(meta, waypoints, index)
        except (ValueError, KeyError) as e:
            logger.error(f"Neutron route not saved: {e}")
            self._route_obj, self._route_id, self._snapshot = route, None, snapshot
            return
        self._pending = None
        self._route_obj, self._route_id, self._snapshot = route, route_id, snapshot


Ask = Callable[[Any, SavedRoute, Optional[str]], Optional[str]]


def resume_route(app, store: Store, autosave: NeutronAutosave, route_id: int, ask: Ask) -> Decision:
    """Load a saved route into the router and continue according to decide_resume."""
    saved = store.load_neutron_route(route_id)
    router = app.neutron_router
    route = [dict(wp) for wp in saved.waypoints]
    router.last_route = route
    router.current_waypoint_index = saved.current_index
    autosave.adopt(route, saved.id)
    app.neutron_manager.update_neutron_navigation()
    current = app.neutron_manager.get_current_system_from_journal()
    decision = decide_resume([wp['system'] for wp in route], saved.current_index, current)
    if decision.kind == 'advance':
        app.neutron_manager.handle_neutron_system_jump(current)
    elif decision.kind == 'ask':
        choice = ask(app, saved, current)
        if choice == 'back':
            app.neutron_manager.copy_current_neutron_system(auto=True)
        elif choice == 'replot':
            autosave.request_replace(saved.id, saved.meta.to_system)
            app.neutron_from_autocomplete.set(current)
            app.neutron_to_autocomplete.set(saved.meta.to_system)
            app.neutron_range_var.set(f"{saved.meta.range_ly:g}")
            app.neutron_boost_var.set(BOOST_LABELS.get(saved.meta.boost, BOOST_LABELS['x4']))
            app._calculate_neutron_route()
    return decision
```

`StoreError` is imported for Task 4.

- [ ] **Step 4: Run all tests.** Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add edmrn/ext/neutron_resume.py tests/test_neutron_resume.py
git commit -m "feat(neutron): autosave neutron routes and resume decision" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 4: Install, dialog, upstream hooks, smoke test

**Files:**
- Modify: `edmrn/ext/neutron_resume.py`. Add `install`, `add_saved_routes_button`,
  `open_saved_routes_dialog` and `ask_resume_choice`.
- Modify: `edmrn/ui_components.py`. Add two lines in `create_neutron_tab`, directly after
  `self.app.neutron_next_btn.pack(side="left", padx=(5, 0))`.
- Modify: `edmrn/app.py`. Add two lines directly after
  `self.journal_monitor = JournalMonitor(self._journal_callback)` in `EDMRN_App.__init__`.
- Modify: `docs/superpowers/plans/2026-10-11-journal-location.md`, Task 6 Step 5. Its two lines now
  go directly after the neutron install lines, not directly after the `JournalMonitor(...)` line.
- Test: `tests/test_neutron_resume.py` (append)
- Docs: `docs/architecture/overview.md`

**Interfaces:**
- Produces:
  - `install(app) -> NeutronAutosave | None` is idempotent. It sets `app.edmrn_store`,
    `app.neutron_autosave` and, on failure, `app.edmrn_store_error: str`.
  - `add_saved_routes_button(app, parent) -> CTkButton`.
  - `open_saved_routes_dialog(app) -> None`.
  - `ask_resume_choice(app, saved, current) -> 'back' | 'replot' | None`.

- [ ] **Step 1: Write the failing tests** (append)

```python
from edmrn.ext.neutron_resume import install


def test_install_opens_default_store_and_starts(isolated_home):
    app, calls = make_app()
    autosave = install(app)
    try:
        assert autosave is app.neutron_autosave and install(app) is autosave
        assert str(isolated_home) in app.edmrn_store.path
        assert ("after", 2000) in calls
    finally:
        app.edmrn_store.close()


def test_install_with_corrupt_database_disables_feature(isolated_home, monkeypatch):
    from edmrn.config import AppConfig
    from pathlib import Path
    data_dir = Path(AppConfig.get_app_data_path())
    (data_dir / "edmrn.db").write_bytes(b"garbage" * 200)
    app, _ = make_app()
    assert install(app) is None
    assert app.neutron_autosave is None and "edmrn.db" in app.edmrn_store_error
```

- [ ] **Step 2: Run to verify failure.** Expected: `ImportError: cannot import name 'install'`.

- [ ] **Step 3: Implement** (append to `edmrn/ext/neutron_resume.py`)

```python
def install(app) -> Optional[NeutronAutosave]:
    """Open the store and start the autosave. Called once from EDMRN_App.__init__."""
    existing = getattr(app, 'neutron_autosave', None)
    if isinstance(existing, NeutronAutosave):
        return existing
    try:
        store = Store.open_default()
    except StoreError as e:
        logger.error(f"Saved neutron routes unavailable: {e}")
        app.edmrn_store, app.neutron_autosave, app.edmrn_store_error = None, None, str(e)
        return None
    autosave = NeutronAutosave(app, store)
    app.edmrn_store, app.neutron_autosave, app.edmrn_store_error = store, autosave, None
    autosave.start()
    return autosave


def add_saved_routes_button(app, parent):
    import customtkinter as ctk
    button = ctk.CTkButton(parent, text="Saved routes", height=30, width=200,
                           command=lambda: open_saved_routes_dialog(app))
    try:
        app.theme_manager.apply_button_theme(button, "secondary")
    except Exception:
        pass
    button.pack(pady=(5, 0))
    return button


def ask_resume_choice(app, saved: SavedRoute, current: Optional[str]) -> Optional[str]:
    """Modal choice when the commander is not on the saved route."""
    import customtkinter as ctk
    target = saved.waypoints[saved.current_index]['system']
    result = {'choice': None}
    dialog = ctk.CTkToplevel(app.root)
    dialog.title("Resume neutron route")
    dialog.transient(app.root)
    ctk.CTkLabel(dialog, justify="left", wraplength=420, text=(
        f"You are in {current}, which is not on this route.\n\n"
        f"Back to the last waypoint: '{target}' is copied to the clipboard; set it as your target in game.\n"
        f"New route from here: plot from {current} to {saved.meta.to_system} "
        f"({saved.meta.range_ly:g} ly, {saved.meta.boost}); it replaces this saved route.")
    ).pack(padx=16, pady=(16, 10))
    row = ctk.CTkFrame(dialog, fg_color="transparent")
    row.pack(padx=16, pady=(0, 16))

    def choose(value):
        result['choice'] = value
        dialog.destroy()

    ctk.CTkButton(row, text="Back to last waypoint", command=lambda: choose('back')).pack(side="left", padx=4)
    ctk.CTkButton(row, text="New route from here", command=lambda: choose('replot')).pack(side="left", padx=4)
    ctk.CTkButton(row, text="Cancel", command=lambda: choose(None)).pack(side="left", padx=4)
    dialog.grab_set()
    app.root.wait_window(dialog)
    return result['choice']


def open_saved_routes_dialog(app) -> None:
    import customtkinter as ctk
    dialog = ctk.CTkToplevel(app.root)
    dialog.title("Saved neutron routes")
    dialog.geometry("640x420")
    dialog.transient(app.root)
    store = getattr(app, 'edmrn_store', None)
    autosave = getattr(app, 'neutron_autosave', None)
    if store is None or autosave is None:
        reason = getattr(app, 'edmrn_store_error', None) or "store not initialised"
        ctk.CTkLabel(dialog, text=f"Saved routes are unavailable:\n{reason}",
                     wraplength=580, justify="left").pack(padx=16, pady=16)
        return
    body = ctk.CTkScrollableFrame(dialog)
    body.pack(fill="both", expand=True, padx=12, pady=12)

    def refresh():
        for child in body.winfo_children():
            child.destroy()
        try:
            routes = store.list_neutron_routes()
        except Exception as e:
            ctk.CTkLabel(body, text=f"Could not read saved routes: {e}").pack(anchor="w")
            return
        if not routes:
            ctk.CTkLabel(body, text="No saved neutron routes yet. Plot one in this tab.").pack(anchor="w")
            return
        for summary in routes:
            row = ctk.CTkFrame(body)
            row.pack(fill="x", pady=3)
            state = "done" if summary.state == 'completed' else f"{summary.visited}/{summary.total}"
            ctk.CTkLabel(row, anchor="w", text=(
                f"{summary.from_system} → {summary.to_system}   {state}   "
                f"{summary.range_ly:g} ly {summary.boost}   {summary.updated_at}")
            ).pack(side="left", padx=8, fill="x", expand=True)
            ctk.CTkButton(row, text="Delete", width=70,
                          command=lambda rid=summary.id: delete(rid)).pack(side="right", padx=4)
            ctk.CTkButton(row, text="Resume", width=80,
                          command=lambda rid=summary.id: resume(rid)).pack(side="right", padx=4)

    def resume(route_id):
        try:
            resume_route(app, store, autosave, route_id, ask=ask_resume_choice)
        except Exception as e:
            logger.error(f"Resume failed: {e}")
            ctk.CTkLabel(body, text=f"Resume failed: {e}").pack(anchor="w")
            return
        dialog.destroy()

    def delete(route_id):
        try:
            store.delete_neutron_route(route_id)
        except Exception as e:
            logger.error(f"Delete failed: {e}")
        refresh()

    refresh()
```

- [ ] **Step 4: Upstream hooks** (CRLF; exactly these lines)

`edmrn/ui_components.py`, after `self.app.neutron_next_btn.pack(side="left", padx=(5, 0))`:

```python
        from edmrn.ext.neutron_resume import add_saved_routes_button
        add_saved_routes_button(self.app, button_frame)
```

`edmrn/app.py`, after `self.journal_monitor = JournalMonitor(self._journal_callback)`:

```python
        from edmrn.ext.neutron_resume import install as install_edmrn_neutron_resume
        install_edmrn_neutron_resume(self)
```

Check: `git diff --stat edmrn/ui_components.py edmrn/app.py` shows 2 insertions each; `file` still
reports CRLF.

- [ ] **Step 5: Update the location plan.** In
  `docs/superpowers/plans/2026-10-11-journal-location.md`, Task 6 Step 5, the location lines now go
  directly after `install_edmrn_neutron_resume(self)`. Update the step text and its `git diff --stat`
  expectation (still 2 insertions).

- [ ] **Step 6: Run all tests.** Expected: all pass.

- [ ] **Step 7: Isolated smoke start with a seeded database** (Git Bash, repo root)

```bash
H="$(mktemp -d)"; mkdir -p "$H/EDMRN_Route_Data"
HOME="$H" USERPROFILE="$H" APPDATA="$H" LOCALAPPDATA="$H" .venv/Scripts/python.exe -I -c "
import sys; sys.path.insert(0, '.')
from edmrn.ext.store import Store, RouteMeta
s = Store.open_default()
wp = lambda n, st='unvisited': {'system': n, 'type': 'Normal', 'distance': 10.0, 'jumps': 1, 'status': st}
s.add_neutron_route(RouteMeta('Sol', 'Colonia', 70.0, 'x4'), [wp('Sol', 'visited'), wp('A'), wp('Colonia')], 1)
print(s.path); s.close()"
HOME="$H" USERPROFILE="$H" APPDATA="$H" LOCALAPPDATA="$H" timeout 25 .venv/Scripts/python.exe -u main.py > "$H/smoke.log" 2>&1; echo "exit $?"
grep -iE "traceback|NeutronResume|Saved neutron" "$H/smoke.log" "$H"/EDMRN_Route_Data/logs/*.log
```

Expected:
- The seed prints a path under `$H` ending in `edmrn.db`.
- `exit 124`.
- No `Traceback` and no `NeutronResume` error.

If someone can look at the window: in the Neutron tab, "Saved routes" lists
`Sol → Colonia 1/3 …`, and Resume loads it. Report honestly whether this was seen.

- [ ] **Step 8: Docs.** Add three rows to the hook-point table in `docs/architecture/overview.md`:

```markdown
| `edmrn/ui_components.py` | Two lines in `create_neutron_tab` add the "Saved routes" button (`edmrn.ext.neutron_resume`) | Neutron resume spec N3 | no |
| `edmrn/app.py` | Two lines after the first `JournalMonitor(...)` in `EDMRN_App.__init__`: `edmrn.ext.neutron_resume.install(self)` | Opens `edmrn.db` and starts the neutron autosave | no |
```

The third row is the `neutron.py` change from Task 2, already added. Then append:

```markdown
## Neutron route persistence (neutron resume spec, ADR-0003)

- `NeutronAutosave` polls upstream's `NeutronRouter` every 2 s on the Tk loop. It compares the
  route object, the current index and the statuses.
- A new route object becomes a row in `edmrn.db`, or replaces a row that asked for a replot to the
  same destination. A change to an existing route updates that row's progress.
- Resume loads a row back into the router. `decide_resume` chooses whether to continue, to advance
  through upstream's jump handler, or to ask the commander.
- All writes happen on the Tk thread.
```

- [ ] **Step 9: Commit**

```bash
git add edmrn/ext/neutron_resume.py edmrn/ui_components.py edmrn/app.py tests/test_neutron_resume.py docs/architecture/overview.md docs/superpowers/plans/2026-10-11-journal-location.md
git commit -m "feat(neutron): saved routes list with resume, wired into the Neutron tab" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```
