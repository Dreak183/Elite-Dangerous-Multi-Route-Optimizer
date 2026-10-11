import hashlib
import os
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
    RouteMeta("Sol", "Colonia", float("nan"), "x4"),
    RouteMeta("Sol", "Colonia", float("inf"), "x4"),
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
    [wp("Sol", distance=float("nan"))],
    [wp("Sol", distance=float("inf"))],
    [wp("Sol", jumps=2**70)],
    [wp("Sol", jumps=-1)],
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
    os.remove(tmp_path / "edmrn.db")


def test_corrupt_file_raises(tmp_path):
    path = tmp_path / "edmrn.db"
    path.write_bytes(b"this is not a database" * 100)
    before = _sha(path)
    with pytest.raises(StoreError):
        Store(path)
    assert _sha(path) == before
    os.remove(path)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_damaged_body_with_intact_header_raises(tmp_path):
    path = tmp_path / "edmrn.db"
    s = Store(path)
    s.add_neutron_route(META, [wp("Sol")])
    s.close()
    data = path.read_bytes()
    path.write_bytes(data[:100] + bytes([255]) * (len(data) - 100))
    before = _sha(path)
    with pytest.raises(StoreError):
        Store(path)
    assert _sha(path) == before
    os.remove(path)


def test_version1_file_without_tables_raises(tmp_path):
    path = tmp_path / "edmrn.db"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 1")
    raw.close()
    with pytest.raises(StoreError):
        Store(path)
    os.remove(path)


def test_open_default_uses_isolated_app_data_dir(isolated_home):
    s = Store.open_default()
    try:
        assert str(isolated_home) in s.path and s.path.endswith("edmrn.db")
    finally:
        s.close()


def test_rollback_journal_and_foreign_keys(store):
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_commit_time_lock_rolls_back_and_store_recovers(tmp_path):
    path = tmp_path / "edmrn.db"
    store = Store(path)
    store._conn.execute("PRAGMA busy_timeout = 50")
    reader = sqlite3.connect(str(path), isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM neutron_routes").fetchall()
    try:
        with pytest.raises(sqlite3.OperationalError):
            store.add_neutron_route(META, [wp("Sol")])
        assert store._conn.in_transaction is False
    finally:
        reader.execute("COMMIT")
        reader.close()
    rid = store.add_neutron_route(META, [wp("Colonia")])
    store.close()
    reopened = Store(path)
    try:
        assert [s.id for s in reopened.list_neutron_routes()] == [rid]
    finally:
        reopened.close()
