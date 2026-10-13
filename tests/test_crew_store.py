import random
import sqlite3

import pytest

from crew_helpers import (ADDR, all_bodies, bio_find, body, dump, honk, manual_find, note, sealed,
                          signals, ts, visit)
from edmrn.ext import crew_events as ev
from edmrn.ext.crew_identity import KeyRing, verify
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.store import MIGRATIONS, SCHEMA_VERSION, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "edmrn.db")
    yield s
    s.close()


@pytest.fixture
def crew(store):
    return CrewStore(store)


@pytest.fixture
def ring(tmp_path):
    return KeyRing(tmp_path / "crew_keys")


@pytest.fixture
def friend(tmp_path):
    return KeyRing(tmp_path / "friend_keys").for_commander("F9")


def log_rows(store):
    with store.read() as conn:
        return conn.execute("SELECT author, seq, event_id, kind FROM crew_log ORDER BY author, seq").fetchall()


def projected_count(store):
    with store.read() as conn:
        return conn.execute("SELECT COUNT(*) FROM crew_projected").fetchone()[0]


# --- store access and migration ---------------------------------------------------------

def test_schema_version_is_2():
    assert SCHEMA_VERSION == 2


def test_version_1_database_migrates_and_keeps_neutron_routes(tmp_path):
    path = tmp_path / "edmrn.db"
    raw = sqlite3.connect(path)
    for statement in MIGRATIONS[0]:
        raw.execute(statement)
    raw.execute("INSERT INTO neutron_routes (created_at, updated_at, from_system, to_system, range_ly, "
                "boost, current_index, state) VALUES ('t', 't', 'Sol', 'Colonia', 70, 'x4', 0, 'active')")
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()
    s = Store(path)
    try:
        assert [r.from_system for r in s.list_neutron_routes()] == ["Sol"]
        with s.read() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
            assert conn.execute("SELECT COUNT(*) FROM crew_log").fetchone()[0] == 0
    finally:
        s.close()


def test_transaction_rolls_back_on_error(store):
    with pytest.raises(RuntimeError):
        with store.transaction() as conn:
            conn.execute("INSERT INTO crew_projected (event_id) VALUES ('x')")
            raise RuntimeError("boom")
    assert projected_count(store) == 0


# --- own events (C4) --------------------------------------------------------------------

def test_own_events_get_contiguous_seqs_and_verify(crew, ring, store):
    ident = ring.for_commander("F1")
    assert crew.append_own([(ident, visit()), (ident, honk(at=ts(1))), (ident, all_bodies(at=ts(2)))]) == 3
    assert [r[1] for r in log_rows(store)] == [1, 2, 3]
    assert ident.high_water == 3
    with store.read() as conn:
        for data, signature in conn.execute("SELECT envelope, signature FROM crew_log"):
            assert verify(ident.author, signature, data)
            assert ev.parse_envelope(data)["author"] == ident.author


def test_appending_the_same_drafts_again_adds_nothing(crew, ring, store):
    ident = ring.for_commander("F1")
    drafts = [visit(), honk(at=ts(1))]
    crew.append_own([(ident, d) for d in drafts])
    assert crew.append_own([(ident, d) for d in drafts]) == 0
    assert len(log_rows(store)) == 2


def test_duplicate_drafts_in_one_batch_are_written_once(crew, ring):
    ident = ring.for_commander("F1")
    assert crew.append_own([(ident, visit()), (ident, visit())]) == 1


def test_two_authors_in_one_system_give_two_events(crew, ring, store):
    a, b = ring.for_commander("F1"), ring.for_commander("F2")
    crew.append_own([(a, visit()), (b, visit("F2"))])
    rows = log_rows(store)
    assert {r[0] for r in rows} == {a.author, b.author}
    assert [r[1] for r in rows] == [1, 1]


def test_seq_continues_above_the_key_file_after_the_database_is_lost(tmp_path, ring):
    ident = ring.for_commander("F1")
    first = Store(tmp_path / "a.db")
    CrewStore(first).append_own([(ident, visit()), (ident, honk(at=ts(1)))])
    first.close()
    fresh = Store(tmp_path / "b.db")
    try:
        reloaded = KeyRing(tmp_path / "crew_keys").for_commander("F1")
        CrewStore(fresh).append_own([(reloaded, all_bodies(at=ts(2)))])
        assert [r[1] for r in log_rows(fresh)] == [3]
    finally:
        fresh.close()


def test_invalid_own_payload_raises_and_writes_nothing(crew, ring, store):
    ident = ring.for_commander("F1")
    bad = ev.Draft("visit", "F1", ts(0), {"system": "Sol"})
    with pytest.raises(ev.PayloadError):
        crew.append_own([(ident, visit(at=ts(5))), (ident, bad)])
    assert log_rows(store) == []
    assert ident.high_water == 0


# --- received events (C4) ---------------------------------------------------------------

def test_received_envelope_is_verified_and_stored_once(crew, store, friend):
    data, signature = sealed(friend, visit("F9"), 7)
    assert crew.insert_received(data, signature) is True
    assert crew.insert_received(data, signature) is False
    assert [(r[0], r[1]) for r in log_rows(store)] == [(friend.author, 7)]


@pytest.mark.parametrize("mutation", ["changed_byte", "short_signature", "signature_of_another_author"])
def test_received_envelope_with_bad_signature_is_rejected(crew, store, friend, tmp_path, mutation):
    data, signature = sealed(friend, visit("F9"), 1)
    if mutation == "changed_byte":
        data = data.replace(b"Sol", b"Sum")
    elif mutation == "short_signature":
        signature = signature[:10]
    else:
        stranger = KeyRing(tmp_path / "stranger").for_commander("F8")
        data, _ = sealed(stranger, visit("F8"), 1)
    with pytest.raises(ev.EnvelopeError):
        crew.insert_received(data, signature)
    assert log_rows(store) == []


@pytest.mark.parametrize("data", [b"[" * 5000, b"{}", b"x" * 20_000, "text"])
def test_received_malformed_bytes_are_rejected(crew, store, data):
    with pytest.raises(ev.EnvelopeError):
        crew.insert_received(data, b"s" * 64)
    assert log_rows(store) == []


def test_repeated_id_under_a_new_seq_is_stored_but_projected_once(crew, store, friend):
    draft = visit("F9")
    crew.insert_received(*sealed(friend, draft, 1))
    crew.insert_received(*sealed(friend, draft, 2))
    assert len(log_rows(store)) == 2
    assert projected_count(store) == 1


def test_unknown_kind_and_newer_version_are_stored_not_projected(crew, store, friend):
    chat = ev.Draft("chat", "F9", ts(0), {"text": "hi"}, event_id=ev.uuid7())
    crew.insert_received(*sealed(friend, chat, 1))
    crew.insert_received(*sealed(friend, visit("F9"), 2, v=2))
    assert len(log_rows(store)) == 2
    assert projected_count(store) == 0


def test_received_invalid_payload_is_stored_not_projected(crew, store, friend):
    hostile = ev.Draft("visit", "F9", ts(0), {"commander": "x", "system": "Sol‮", "address": "1",
                                                "coords": None}, event_id="0" * 32)
    assert crew.insert_received(*sealed(friend, hostile, 1)) is True
    assert projected_count(store) == 0
