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
            conn.execute("INSERT INTO crew_projected (author, event_id) VALUES ('a', 'x')")
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


# --- event ids are bound to their author ------------------------------------------------

def test_squatted_deterministic_id_is_not_projected_and_does_not_block_the_owner(crew, store, ring, tmp_path):
    me = ring.for_commander("F1")
    mallory = KeyRing(tmp_path / "mallory").for_commander("F7")
    draft = visit()
    my_id = draft.resolve_id(me.author)
    env = ev.build_envelope(my_id, mallory.author, 1, "visit", draft.created_at, draft.payload)
    assert crew.insert_received(*ev.seal(env, mallory.sign)) is True
    assert projected_count(store) == 0
    assert crew.append_own([(me, visit())]) == 1
    assert projected_count(store) == 1


def test_manual_event_with_a_non_uuid7_id_is_stored_not_projected(crew, store, friend):
    bad = ev.Draft("note", "F9", ts(0), note(cmdr="F9").payload, event_id="0" * 32)
    assert crew.insert_received(*sealed(friend, bad, 1)) is True
    assert projected_count(store) == 0


def test_note_copied_by_another_author_is_projected_for_both(crew, store, friend, tmp_path):
    other = KeyRing(tmp_path / "other").for_commander("F8")
    original = note(cmdr="F9")
    crew.insert_received(*sealed(friend, original, 1))
    crew.insert_received(*sealed(other, original, 1))
    assert projected_count(store) == 2


def test_unusable_key_file_after_commit_is_logged_not_raised(crew, store, ring):
    ident = ring.for_commander("F1")
    ident.path.write_text("garbage", encoding="utf-8")
    assert crew.append_own([(ident, visit())]) == 1
    assert len(log_rows(store)) == 1


def test_own_events_are_projected(crew, ring, store):
    ident = ring.for_commander("F1")
    crew.append_own([(ident, visit()), (ident, honk(at=ts(1))), (ident, all_bodies(at=ts(2)))])
    assert projected_count(store) == 3


# --- projections (C7, C8) ----------------------------------------------------------------

def test_two_authors_in_one_system_give_one_system_row(crew, ring):
    a, b = ring.for_commander("F1"), ring.for_commander("F2")
    crew.append_own([(a, visit()), (b, visit("F2", at=ts(5)))])
    row = crew.system(ADDR)
    assert row.name == "Sol" and row.coords == (0.0, 0.0, 0.0)
    with crew.store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crew_systems").fetchone()[0] == 1


def test_system_name_and_coords_are_the_earliest_and_body_count_the_latest(crew, friend, ring):
    me = ring.for_commander("F1")
    crew.append_own([(me, visit(at=ts(5), system="Sol", coords=(1.0, 2.0, 3.0))),
                     (me, honk(at=ts(6), bodies=10))])
    crew.insert_received(*sealed(friend, visit("F9", at=ts(1), system="SOL", coords=(9.0, 9.0, 9.0)), 1))
    crew.insert_received(*sealed(friend, all_bodies("F9", at=ts(7), count=12), 2))
    row = crew.system(ADDR)
    assert row.name == "SOL" and row.coords == (9.0, 9.0, 9.0) and row.body_count == 12
    assert crew.system("1") is None


def test_visits_track_first_last_and_count(crew, ring):
    me = ring.for_commander("F1")
    crew.append_own([(me, visit(at=ts(3))), (me, visit(at=ts(1))), (me, visit(at=ts(2)))])
    [v] = crew.visits(me.author)
    assert (v.address, v.first_at, v.last_at, v.count) == (ADDR, ts(1), ts(3), 3)


def test_a_full_scan_after_a_basic_one_wins_and_basic_after_full_does_not(crew, ring):
    me = ring.for_commander("F1")
    crew.append_own([(me, body(at=ts(1), body_id=1, tier="basic")),
                     (me, body(at=ts(2), body_id=1, tier="full", landable=True)),
                     (me, body(at=ts(1), body_id=2, tier="full", landable=False)),
                     (me, body(at=ts(2), body_id=2, tier="basic"))])
    merged = {b.body_id: b for b in crew.bodies(ADDR)}
    assert merged[1].tier == "full" and merged[1].landable is True
    assert merged[2].tier == "full" and merged[2].landable is False


def test_merged_body_is_the_earliest_full_report_and_flags_stay_per_author(crew, ring, friend):
    me = ring.for_commander("F1")
    crew.append_own([(me, body(at=ts(5), body_id=3, discovered=True))])
    crew.insert_received(*sealed(friend, body("F9", at=ts(2), body_id=3, discovered=False,
                                              name="Sol 3 friend"), 1))
    [merged] = crew.bodies(ADDR)
    assert merged.name == "Sol 3 friend" and merged.author == friend.author
    reports = {r.author: r for r in crew.body_reports(ADDR)}
    assert reports[friend.author].was_discovered is False
    assert reports[me.author].was_discovered is True


def test_explored_from_all_bodies(crew, ring):
    me = ring.for_commander("F1")
    crew.append_own([(me, all_bodies())])
    assert crew.explored(me.author) == {ADDR}


def test_explored_when_scanned_bodies_reach_the_latest_honk_count(crew, ring):
    me = ring.for_commander("F1")
    crew.append_own([(me, honk(at=ts(1), bodies=3)),
                     (me, body(at=ts(2), body_id=0, star_type="G", name="Sol")),
                     (me, body(at=ts(3), body_id=1, tier="basic"))])
    assert crew.explored(me.author) == set()
    crew.append_own([(me, body(at=ts(4), body_id=2))])
    assert crew.explored(me.author) == {ADDR}
    crew.append_own([(me, honk(at=ts(9), bodies=5))])
    assert crew.explored(me.author) == set()


def test_a_visit_or_a_honk_alone_never_counts(crew, ring):
    me = ring.for_commander("F1")
    crew.append_own([(me, visit()), (me, honk(at=ts(1), bodies=0))])
    assert crew.explored() == set()


def test_explored_is_per_author_and_the_group_is_the_union(crew, ring, friend):
    me = ring.for_commander("F1")
    crew.append_own([(me, all_bodies())])
    crew.insert_received(*sealed(friend, all_bodies("F9", address="42", system="Other"), 1))
    assert crew.explored(me.author) == {ADDR}
    assert crew.explored(friend.author) == {"42"}
    assert crew.explored() == {ADDR, "42"}


def test_signals_take_the_highest_count_and_the_union_of_genera(crew, ring, friend):
    me = ring.for_commander("F1")
    crew.append_own([(me, signals(source="fss", counts={"biological": 2, "geological": 1}))])
    crew.insert_received(*sealed(friend, signals("F9", source="saa", counts={"biological": 1, "human": 1},
                                                 genera=["$Codex_Ent_Bacterial_Genus_Name;"]), 1))
    crew.insert_received(*sealed(friend, signals("F9", at=ts(3), source="fss",
                                                 counts={"biological": 3}), 2))
    s = crew.signals(ADDR)[1]
    assert s.counts == {"biological": 3, "geological": 1, "human": 1}
    assert s.genera == ("$Codex_Ent_Bacterial_Genus_Name;",)
    assert s.sources == frozenset({"fss", "saa"}) and s.body_name == "Sol 1"


def test_auto_finds_are_one_row_per_event(crew, ring, friend):
    me = ring.for_commander("F1")
    crew.append_own([(me, bio_find(at=ts(1)))])
    crew.insert_received(*sealed(friend, bio_find("F9", at=ts(2)), 1))
    finds = crew.finds()
    assert [f.author for f in finds] == [friend.author, me.author]
    assert finds[0].detail == {"count": 2} and finds[0].mode == "auto" and finds[0].find_id is None


def test_note_revisions_highest_seq_wins_and_delete_hides(crew, ring):
    me = ring.for_commander("F1")
    note_id = ev.uuid7()
    crew.append_own([(me, note(note_id=note_id, text="first"))])
    crew.append_own([(me, note(note_id=note_id, text="second", at=ts(1)))])
    assert [n.text for n in crew.notes(ADDR)] == ["second"]
    crew.append_own([(me, note(note_id=note_id, text="second", deleted=True, at=ts(2)))])
    assert crew.notes() == []
    assert crew.note(me.author, note_id).deleted is True


def test_a_note_revision_by_another_author_is_ignored(crew, ring, friend):
    me = ring.for_commander("F1")
    note_id = ev.uuid7()
    crew.append_own([(me, note(note_id=note_id, text="mine"))])
    crew.insert_received(*sealed(friend, note("F9", note_id=note_id, text="overwritten", deleted=True), 99))
    assert crew.note(me.author, note_id).text == "mine"
    assert crew.note(me.author, note_id).deleted is False


def test_manual_find_revisions(crew, ring):
    me = ring.for_commander("F1")
    find_id = ev.uuid7()
    crew.append_own([(me, manual_find(find_id=find_id, note_text="wreck"))])
    crew.append_own([(me, manual_find(find_id=find_id, note_text="wreck, two pods", at=ts(1)))])
    [f] = crew.finds()
    assert (f.mode, f.find_id, f.note) == ("manual", find_id, "wreck, two pods")
    crew.append_own([(me, manual_find(find_id=find_id, deleted=True, at=ts(2)))])
    assert crew.finds() == []
    assert crew.manual_find(me.author, find_id).deleted is True


def _mixed_envelopes(tmp_path):
    a = KeyRing(tmp_path / "a").for_commander("FA")
    b = KeyRing(tmp_path / "b").for_commander("FB")
    note_id = ev.uuid7()
    drafts_a = [visit("FA", at=ts(1)), honk("FA", at=ts(2), bodies=2),
                body("FA", at=ts(3), body_id=0, star_type="G"), body("FA", at=ts(4), body_id=1, tier="basic"),
                signals("FA", at=ts(5), counts={"biological": 1}), bio_find("FA", at=ts(5)),
                note("FA", at=ts(6), note_id=note_id, text="one"),
                note("FA", at=ts(7), note_id=note_id, text="two")]
    drafts_b = [visit("FB", at=ts(2), system="SOL", coords=(5.0, 5.0, 5.0)), body("FB", at=ts(3), body_id=1),
                signals("FB", at=ts(4), source="saa", counts={"biological": 2}, genera=["$G;"]),
                all_bodies("FB", at=ts(8), count=2), bio_find("FB", at=ts(4))]
    envelopes = [sealed(a, d, i + 1) for i, d in enumerate(drafts_a)]
    envelopes += [sealed(b, d, i + 1) for i, d in enumerate(drafts_b)]
    envelopes.append(sealed(b, drafts_b[0], 99))
    return envelopes


def test_the_merge_is_independent_of_arrival_order(tmp_path):
    envelopes = _mixed_envelopes(tmp_path)
    dumps = []
    for attempt in range(5):
        shuffled = envelopes[:]
        random.Random(attempt).shuffle(shuffled)
        s = Store(tmp_path / f"order{attempt}.db")
        try:
            crew_store = CrewStore(s)
            for data, signature in shuffled:
                crew_store.insert_received(data, signature)
            dumps.append(dump(s))
        finally:
            s.close()
    assert all(d == dumps[0] for d in dumps)
    assert dumps[0]["crew_explored"]


def test_rebuild_equals_the_incremental_result(tmp_path, crew, store, ring):
    for data, signature in _mixed_envelopes(tmp_path):
        crew.insert_received(data, signature)
    crew.append_own([(ring.for_commander("F1"), visit(at=ts(30)))])
    before = dump(store)
    crew.rebuild_projections()
    assert dump(store) == before


# --- repeated event ids and surviving-mutant guards ---------------------------------------

def _duplicate_pairs(friend):
    """Same author, same deterministic id, two seqs, differing payload (a replay after data loss)."""
    pairs = {
        "body": [(body("F9", at=ts(1), body_id=4, mapped=False), 2),
                 (body("F9", at=ts(40), body_id=4, mapped=True), 7)],
        "signals": [(signals("F9", at=ts(1), counts={"biological": 1}), 2),
                    (signals("F9", at=ts(40), counts={"biological": 4}), 7)],
        "all_bodies": [(all_bodies("F9", at=ts(1), count=3), 2),
                       (all_bodies("F9", at=ts(40), count=9), 7)],
        "find": [(bio_find("F9", at=ts(1), count=2), 2), (bio_find("F9", at=ts(40), count=5), 7)],
    }
    return {name: [sealed(friend, draft, seq) for draft, seq in items] for name, items in pairs.items()}


@pytest.mark.parametrize("name", ["body", "signals", "all_bodies", "find"])
def test_a_repeated_event_id_projects_the_same_in_every_order(tmp_path, friend, name):
    first, second = _duplicate_pairs(friend)[name]
    dumps = []
    for attempt, order in enumerate(([first, second], [second, first])):
        s = Store(tmp_path / f"dup{attempt}.db")
        try:
            crew_store = CrewStore(s)
            for data, signature in order:
                assert crew_store.insert_received(data, signature)
            incremental = dump(s)
            crew_store.rebuild_projections()
            assert dump(s) == incremental
            dumps.append(incremental)
        finally:
            s.close()
    assert dumps[0] == dumps[1]


def test_a_repeated_visit_counts_once(crew, friend):
    draft = visit("F9", at=ts(1))
    crew.insert_received(*sealed(friend, draft, 2))
    crew.insert_received(*sealed(friend, draft, 7))
    [v] = crew.visits(friend.author)
    assert v.count == 1


def test_an_older_honk_arriving_late_does_not_replace_the_latest(crew, friend):
    crew.insert_received(*sealed(friend, body("F9", at=ts(1), body_id=0, star_type="G"), 1))
    crew.insert_received(*sealed(friend, body("F9", at=ts(2), body_id=1, tier="basic"), 2))
    crew.insert_received(*sealed(friend, honk("F9", at=ts(9), bodies=5), 3))
    assert crew.explored(friend.author) == set()
    crew.insert_received(*sealed(friend, honk("F9", at=ts(3), bodies=2), 4))
    assert crew.explored(friend.author) == set()


def test_manual_find_revision_with_a_lower_seq_arriving_late_is_ignored(crew, friend):
    find_id = ev.uuid7()
    crew.insert_received(*sealed(friend, manual_find("F9", at=ts(2), find_id=find_id, note_text="new"), 5))
    crew.insert_received(*sealed(friend, manual_find("F9", at=ts(1), find_id=find_id, note_text="old"), 2))
    assert crew.manual_find(friend.author, find_id).note == "new"


def test_signal_source_body_name_is_the_smallest_whatever_the_arrival(crew, friend):
    crew.insert_received(*sealed(friend, signals("F9", at=ts(1), body_name="Zed 1", source="fss"), 1))
    crew.insert_received(*sealed(friend, signals("F9", at=ts(2), body_name="Alpha 1", source="fss"), 2))
    assert crew.signals(ADDR)[1].body_name == "Alpha 1"


def test_notes_by_address_hide_deleted_notes(crew, ring):
    me = ring.for_commander("F1")
    note_id = ev.uuid7()
    crew.append_own([(me, note(note_id=note_id, text="x")), (me, note(note_id=note_id, text="x",
                                                                       deleted=True, at=ts(1)))])
    assert crew.notes(ADDR) == [] and crew.notes() == []
