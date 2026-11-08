"""RelayStore: schema, migration and the engine's queries (self-hosted relay, ADR-0009)."""
import sqlite3

import pytest

from edmrn_relay.store import LoadedGroup, LogRow, RelayStore, RelayStoreError

A = "A" * 43
B = "B" * 43
G1 = "01920000-0000-7000-8000-000000000001"
G2 = "01920000-0000-7000-8000-000000000002"


def row(author, seq):
    return LogRow(author, seq, f"e{seq}".encode(), bytes(64))


@pytest.fixture
def store(tmp_path):
    s = RelayStore(tmp_path / "relay.db")
    yield s
    s.close()


@pytest.fixture
def seeded(store):
    store.create_group(G1, A, 100, row(A, 1))
    return store


def test_new_database_is_version_1_in_wal_mode(store):
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert store.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"groups", "log", "heads", "roster", "quota", "status"}
    for t in tables:
        cols = {r[1] for r in store.db.execute(f"PRAGMA table_info({t})")}
        assert "group_id" in cols


def test_reopening_keeps_data(tmp_path):
    path = tmp_path / "relay.db"
    s = RelayStore(path)
    s.create_group(G1, A, 100, row(A, 1))
    s.close()
    s = RelayStore(path)
    try:
        assert s.load_group(G1) == LoadedGroup(A, {}, [b"e1"])
    finally:
        s.close()


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "relay.db"
    s = RelayStore(path)
    s.db.execute("PRAGMA user_version = 2")
    s.close()
    with pytest.raises(RelayStoreError, match="newer"):
        RelayStore(path)


def test_unknown_group_is_none_and_writes_nothing(store):
    assert store.load_group(G1) is None
    assert store.db.total_changes == 0


def test_create_group_moves_no_head(seeded):
    assert seeded.load_group(G1).heads == {}
    assert seeded.log_after(G1, A, 0, 10, 100) == [row(A, 1)]
    assert seeded.roster_bytes(G1) == [b"e1"]


def test_groups_are_isolated(store):
    store.create_group(G1, A, 100, LogRow(A, 1, b"one", bytes(64)))
    store.create_group(G2, A, 100, LogRow(A, 1, b"two", bytes(64)))
    assert store.roster_bytes(G1) == [b"one"]
    assert store.roster_bytes(G2) == [b"two"]
    assert [r.e for r in store.log_after(G1, A, 0, 9, 9)] == [b"one"]
    assert [r.e for r in store.log_after(G2, A, 0, 9, 9)] == [b"two"]


def test_apply_frame_writes_everything_and_reports_new_roster_rows(seeded):
    args = (G1, 5, [row(A, 2)], [row(A, 2)], {A: 2}, {A: 2})
    assert seeded.apply_frame(*args) is True
    assert seeded.apply_frame(*args) is False
    assert seeded.load_group(G1).heads == {A: 2}
    assert seeded.quota_used(G1, 5, A) == 4
    assert seeded.group_quota_used(G1, 5) == 4


def test_apply_frame_is_one_transaction(seeded):
    seeded.db.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON quota BEGIN SELECT RAISE(ABORT, 'boom'); END")
    with pytest.raises(sqlite3.DatabaseError):
        seeded.apply_frame(G1, 5, [row(A, 2)], [row(A, 2)], {A: 2}, {A: 1})
    assert seeded.log_after(G1, A, 1, 9, 9) == []
    assert seeded.load_group(G1).heads == {}
    assert seeded.roster_bytes(G1) == [b"e1"]


def test_log_after_pages_ascending_up_to_until(seeded):
    rows = [row(A, n) for n in range(1, 6)]
    seeded.apply_frame(G1, 5, rows, [], {A: 5}, {A: 5})
    assert [r.seq for r in seeded.log_after(G1, A, 1, 4, 2)] == [2, 3]


def test_take_past_quota_returns_each_past_day_once(seeded):
    seeded.apply_frame(G1, 3, [], [], {}, {A: 7, B: 5})
    seeded.apply_frame(G1, 4, [], [], {}, {A: 3})
    seeded.apply_frame(G1, 5, [], [], {}, {A: 4})
    assert seeded.take_past_quota(G1, 5) == [(3, 12), (4, 3)]
    assert seeded.take_past_quota(G1, 5) == []
    assert seeded.quota_used(G1, 5, A) == 4


def test_status_upsert(seeded):
    seeded.save_status(G1, A, b"e1", b"s1")
    seeded.save_status(G1, A, b"e2", b"s2")
    assert seeded.statuses(G1) == [(A, b"e2", b"s2")]


def test_failed_commit_leaves_no_transaction_and_no_writes(seeded):
    db = seeded.db
    db.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    db.execute("CREATE TABLE child (p INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)")
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("CREATE TRIGGER dangle AFTER INSERT ON quota BEGIN INSERT INTO child VALUES (99); END")
    with pytest.raises(sqlite3.IntegrityError):
        seeded.apply_frame(G1, 5, [row(A, 2)], [], {A: 2}, {A: 1})
    assert not db.in_transaction
    assert seeded.log_after(G1, A, 1, 9, 9) == []
    assert seeded.load_group(G1).heads == {}
    db.execute("DROP TRIGGER dangle")
    seeded.save_status(G1, A, b"e", b"s")
    assert seeded.statuses(G1) == [(A, b"e", b"s")]


def test_self_rolled_back_transaction_reports_the_original_error(seeded):
    seeded.db.execute(
        "CREATE TRIGGER rb BEFORE INSERT ON quota BEGIN SELECT RAISE(ROLLBACK, 'rb'); END")
    with pytest.raises(sqlite3.IntegrityError, match="rb"):
        seeded.apply_frame(G1, 5, [row(A, 2)], [], {A: 2}, {A: 1})
    assert not seeded.db.in_transaction
    assert seeded.log_after(G1, A, 1, 9, 9) == []


def test_null_bytes_raise_and_roll_back_the_frame(seeded):
    with pytest.raises(sqlite3.IntegrityError):
        seeded.apply_frame(G1, 5, [LogRow(A, 2, None, bytes(64))], [], {A: 2}, {})
    assert seeded.load_group(G1).heads == {}


def test_log_after_rejects_a_non_positive_limit(seeded):
    for limit in (0, -1):
        with pytest.raises(ValueError):
            seeded.log_after(G1, A, 0, 10, limit)


def test_refused_newer_database_is_not_modified(tmp_path):
    path = tmp_path / "relay.db"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 2")
    raw.close()
    with pytest.raises(RelayStoreError, match="newer"):
        RelayStore(path)
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        raw.close()


def test_add_roster_envelope_inserts_once(seeded):
    seeded.add_roster_envelope(G1, row(B, 1))
    seeded.add_roster_envelope(G1, LogRow(B, 1, b"other", bytes(64)))
    assert seeded.roster_bytes(G1) == [b"e1", b"e1"]
    assert seeded.db.execute("SELECT COUNT(*) FROM roster WHERE group_id = ?", (G1,)).fetchone()[0] == 2


def test_heads_quota_and_status_are_isolated_per_group(store):
    store.create_group(G1, A, 100, row(A, 1))
    store.create_group(G2, A, 100, row(A, 1))
    store.apply_frame(G1, 5, [row(A, 2)], [], {A: 2}, {A: 3})
    store.save_status(G1, A, b"e", b"s")
    assert store.load_group(G2).heads == {}
    assert store.quota_used(G2, 5, A) == 0
    assert store.group_quota_used(G2, 5) == 0
    assert store.statuses(G2) == []
    store.apply_frame(G2, 5, [], [], {A: 9}, {A: 1})
    assert store.load_group(G1).heads == {A: 2}
    assert store.quota_used(G1, 5, A) == 3
    assert store.take_past_quota(G2, 6) == [(5, 1)]
    assert store.quota_used(G1, 5, A) == 3
