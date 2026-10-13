import logging
from datetime import datetime, timezone

import pytest

from crew_helpers import dump, sealed, visit
from edmrn.ext import crew_events as ev
from edmrn.ext.crew_identity import KeyRing
from edmrn.ext.crew_roster import token_hash
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.store import Store

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
G = "01920000-0000-7000-8000-0000000000a1"
INV = "01920000-0000-7000-8000-0000000000b1"
TOKEN = "11" * 32


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "edmrn.db")
    yield s
    s.close()


@pytest.fixture
def keys(tmp_path):
    ring = KeyRing(tmp_path / "other")
    return {name: ring.for_commander(name) for name in ("leader", "alice", "bob", "me")}


def roster_env(identity, seq, kind, at, **payload):
    payload.setdefault("group_id", G)
    env = ev.build_envelope(ev.uuid7(), identity.author, seq, kind, at, payload)
    return ev.seal(env, identity.sign)


def group_and_invite(leader):
    return [
        roster_env(leader, 1, "group", "2026-10-13T10:00:00Z", name="Crew", commander="Leader"),
        roster_env(leader, 2, "invite", "2026-10-13T10:01:00Z", invite_id=INV,
                   token_hash=token_hash(TOKEN), expires_at="2026-10-14T10:00:00Z", label=None),
    ]


def join_of(identity, seq=5):
    return roster_env(identity, seq, "join", "2026-10-13T10:02:00Z", invite_id=INV, token=TOKEN,
                      commander="Alice")


def systems(store):
    return dump(store)["crew_systems"]


def marker(store):
    with store.read() as conn:
        return conn.execute("SELECT 1 FROM crew_meta WHERE key = 'rebuild'").fetchone() is not None


def test_a_non_members_envelope_is_stored_but_not_projected(store, keys):
    crew = CrewStore(store, clock=lambda: NOW)
    crew.set_gate(frozenset({keys["me"].author}), G, keys["leader"].author)
    result = crew.insert_received_batch([sealed(keys["bob"], visit(at="2026-10-13T09:00:00Z"), 1)])
    assert result.inserted == 1 and systems(store) == []


def test_a_join_arriving_after_the_members_events_projects_them(store, keys):
    crew = CrewStore(store, clock=lambda: NOW)
    crew.set_gate(frozenset({keys["me"].author}), G, keys["leader"].author)
    crew.insert_received_batch(group_and_invite(keys["leader"]))
    crew.insert_received_batch([sealed(keys["alice"], visit(at="2026-10-13T09:00:00Z"), 1)])
    assert systems(store) == []
    crew.insert_received_batch([join_of(keys["alice"], seq=2)])
    assert len(systems(store)) == 1 and keys["alice"].author in crew.roster.members


def test_removal_withdraws_rows_above_last_seq(store, keys):
    crew = CrewStore(store, clock=lambda: NOW)
    crew.set_gate(frozenset({keys["me"].author}), G, keys["leader"].author)
    crew.insert_received_batch(group_and_invite(keys["leader"]) + [join_of(keys["alice"], seq=1)])
    crew.insert_received_batch([
        sealed(keys["alice"], visit(at="2026-10-13T09:00:00Z", address="1"), 2),
        sealed(keys["alice"], visit(at="2026-10-13T09:01:00Z", address="2"), 3),
    ])
    assert {row[0] for row in systems(store)} == {"1", "2"}
    result = crew.insert_received_batch([roster_env(keys["leader"], 3, "remove", "2026-10-13T11:00:00Z",
                                                    member=keys["alice"].author, last_seq=2)])
    assert result.rebuilt
    assert {row[0] for row in systems(store)} == {"1"}


def an_earlier_run(store, keys):
    """A process that gated, admitted alice and projected her visit; its projections persist."""
    run = CrewStore(store, clock=lambda: NOW)
    run.set_gate(frozenset({keys["me"].author}), G, keys["leader"].author)
    run.insert_received_batch(group_and_invite(keys["leader"]) + [join_of(keys["alice"], seq=1)])
    run.insert_received_batch([sealed(keys["alice"], visit(at="2026-10-13T09:00:00Z"), 2)])
    assert len(systems(store)) == 1


def test_the_first_set_gate_per_process_projects_nothing(store, keys):
    # Spec B1 S2: projections persist, so the first gate of a process is only a baseline.
    an_earlier_run(store, keys)
    with store.transaction() as conn:
        conn.execute("DELETE FROM crew_systems")
    crew = CrewStore(store, clock=lambda: NOW)
    crew.set_gate(frozenset({keys["me"].author}), G, keys["leader"].author)
    assert systems(store) == [] and keys["alice"].author in crew.roster.members


def test_a_roster_changed_while_closed_rebuilds_at_the_first_set_gate_only(store, keys):
    an_earlier_run(store, keys)
    other_group = "01920000-0000-7000-8000-0000000000c1"
    crew = CrewStore(store, clock=lambda: NOW)
    crew.set_gate(frozenset({keys["me"].author}), other_group, keys["leader"].author)
    assert systems(store) == [] and keys["alice"].author not in crew.roster.members
    again = CrewStore(store, clock=lambda: NOW)
    again.rebuild_projections = lambda: pytest.fail("an unchanged roster must not rebuild")
    again.set_gate(frozenset({keys["me"].author}), other_group, keys["leader"].author)


def test_friends_envelopes_stored_before_any_gate_are_projected_at_the_first_set_gate(store, keys):
    before = CrewStore(store, clock=lambda: NOW)
    before.insert_received_batch(group_and_invite(keys["leader"]) + [join_of(keys["alice"], seq=1)])
    before.insert_received_batch([sealed(keys["alice"], visit(at="2026-10-13T09:00:00Z"), 2)])
    assert systems(store) == []
    CrewStore(store, clock=lambda: NOW).set_gate(frozenset({keys["me"].author}), G, keys["leader"].author)
    assert len(systems(store)) == 1


def test_rebuilds_before_set_gate_keep_own_rows_and_the_marker(store, keys):
    # A rebuild without a gate cannot tell own authors from friends and would wipe own rows.
    CrewStore(store, clock=lambda: NOW).append_own([(keys["me"], visit(cmdr="me", at="2026-10-13T09:00:00Z"))])
    assert len(systems(store)) == 1 and marker(store)
    crew = CrewStore(store, clock=lambda: NOW)
    assert crew.rebuild_if_marked() is False
    crew.rebuild_projections()
    assert len(systems(store)) == 1 and marker(store)
    crew.set_gate(frozenset({keys["me"].author}))
    assert crew.rebuild_if_marked() is True
    assert len(systems(store)) == 1 and not marker(store)


def test_a_rebuild_that_failed_after_the_commit_is_retried_at_the_next_call(store, keys):
    crew = CrewStore(store, clock=lambda: NOW)
    crew.set_gate(frozenset({keys["me"].author}), G, keys["leader"].author)
    crew.insert_received_batch(group_and_invite(keys["leader"]) + [join_of(keys["alice"], seq=1)])
    crew.insert_received_batch([sealed(keys["alice"], visit(at="2026-10-13T09:00:00Z", address="2"), 2)])
    real = crew.rebuild_projections

    def crash(*args, **kwargs):
        raise RuntimeError("rebuild failed")

    crew.rebuild_projections = crash
    with pytest.raises(RuntimeError):
        crew.insert_received_batch([roster_env(keys["leader"], 3, "remove", "2026-10-13T11:00:00Z",
                                               member=keys["alice"].author, last_seq=1)])
    crew.rebuild_projections = real
    assert len(systems(store)) == 1
    crew.insert_received_batch([])
    assert systems(store) == [] and not marker(store)


def test_an_implausible_stamp_warns_once_per_author(store, keys, caplog):
    crew = CrewStore(store, clock=lambda: NOW, admit=lambda a, s: True)
    batch = [sealed(keys["bob"], visit(at="2014-12-15T23:59:59Z", address=str(n)), n) for n in range(1, 4)]
    with caplog.at_level(logging.WARNING):
        crew.insert_received_batch(batch)
    assert sum("implausible" in r.message for r in caplog.records) == 1


def test_narrowing_writes_the_rebuild_marker_in_the_same_transaction(store, keys):
    # Spec B1 S2: a crash between the commit and the rebuild is repaired at the next start.
    crew = CrewStore(store, clock=lambda: NOW)
    crew.set_gate(frozenset({keys["me"].author}), G, keys["leader"].author)
    crew.rebuild_if_marked()
    crew.insert_received_batch(group_and_invite(keys["leader"]) + [join_of(keys["alice"], seq=1)])
    crew.insert_received_batch([sealed(keys["alice"], visit(at="2026-10-13T09:00:00Z", address="2"), 2)])
    assert not marker(store) and len(systems(store)) == 1

    def crash(*args, **kwargs):
        raise RuntimeError("crash before the rebuild")

    crew.rebuild_projections = crash
    with pytest.raises(RuntimeError):
        crew.insert_received_batch([roster_env(keys["leader"], 3, "remove", "2026-10-13T11:00:00Z",
                                               member=keys["alice"].author, last_seq=1)])
    assert marker(store) and len(systems(store)) == 1
    restarted = CrewStore(store, clock=lambda: NOW)
    restarted.set_gate(frozenset({keys["me"].author}), G, keys["leader"].author)
    assert restarted.rebuild_if_marked() is True
    assert systems(store) == [] and not marker(store)


def test_a_completed_narrowing_leaves_no_marker(store, keys):
    crew = CrewStore(store, clock=lambda: NOW)
    crew.set_gate(frozenset({keys["me"].author}), G, keys["leader"].author)
    crew.rebuild_if_marked()
    crew.insert_received_batch(group_and_invite(keys["leader"]) + [join_of(keys["alice"], seq=1)])
    crew.insert_received_batch([roster_env(keys["leader"], 3, "remove", "2026-10-13T11:00:00Z",
                                           member=keys["alice"].author, last_seq=1)])
    assert not marker(store)


@pytest.mark.parametrize("stamp", ["2014-12-15T23:59:59Z", "2026-10-14T12:00:01Z", "2026-02-30T00:00:00Z"])
def test_an_implausible_received_stamp_is_stored_not_projected(store, keys, stamp):
    crew = CrewStore(store, clock=lambda: NOW, admit=lambda a, s: True)
    assert crew.insert_received_batch([sealed(keys["bob"], visit(at=stamp), 1)]).inserted == 1
    assert systems(store) == []
    crew.insert_received_batch([sealed(keys["bob"], visit(at="2026-10-13T09:00:00Z", address="2"), 2)])
    assert [row[0] for row in systems(store)] == ["2"]


def test_own_reports_name_the_system_before_earlier_friend_reports(store, keys):
    crew = CrewStore(store, clock=lambda: NOW, admit=lambda a, s: True)
    crew.set_gate(frozenset({keys["me"].author}))
    crew.insert_received_batch([sealed(keys["bob"], visit(at="2026-10-13T08:00:00Z", system="Fake Name"), 1)])
    crew.insert_received_batch([sealed(keys["me"], visit(at="2026-10-13T09:00:00Z", system="Sol"), 1)])
    assert systems(store)[0][1] == "Sol"


def test_equivocation_warns_once_per_author(store, keys, caplog):
    crew = CrewStore(store, clock=lambda: NOW, admit=lambda a, s: True)
    first = sealed(keys["bob"], visit(at="2026-10-13T09:00:00Z"), 1)
    second = sealed(keys["bob"], visit(at="2026-10-13T09:05:00Z"), 1)
    third = sealed(keys["bob"], visit(at="2026-10-13T09:06:00Z"), 1)
    crew.insert_received_batch([first])
    with caplog.at_level(logging.WARNING):
        crew.insert_received_batch([second, third])
    assert sum("two different envelopes" in r.message for r in caplog.records) == 1


def test_rebuild_logs_one_summary(store, keys, caplog):
    crew = CrewStore(store, clock=lambda: NOW)
    crew.set_gate(frozenset())
    crew.insert_received_batch([sealed(keys["bob"], visit(at="2026-10-13T09:00:00Z", address=str(n)), n)
                                for n in range(1, 6)])
    with caplog.at_level(logging.WARNING):
        crew.rebuild_projections()
    lines = [r.message for r in caplog.records if "unprojected" in r.message]
    assert len(lines) == 1 and "5 author is not a member" in lines[0]


def test_heads_pages_and_roster_envelope(store, keys):
    crew = CrewStore(store, clock=lambda: NOW, admit=lambda a, s: True)
    crew.insert_received_batch([sealed(keys["bob"], visit(at="2026-10-13T09:00:00Z", address=str(n)), n)
                                for n in range(1, 251)])
    assert crew.heads() == {keys["bob"].author: 250}
    page = crew.envelopes_after(keys["bob"].author, 100, 100)
    assert [seq for seq, _, _ in page] == list(range(101, 201))
    crew.insert_received_batch([join_of(keys["bob"], seq=251)])
    data, sig = crew.roster_envelope(keys["bob"].author, "join", G)
    assert ev.parse_envelope(data)["seq"] == 251
    assert crew.roster_envelope(keys["bob"].author, "join", "other-group") is None


def test_batch_rejects_bad_items_and_keeps_the_rest(store, keys):
    crew = CrewStore(store, clock=lambda: NOW, admit=lambda a, s: True)
    good = sealed(keys["bob"], visit(at="2026-10-13T09:00:00Z"), 1)
    result = crew.insert_received_batch([(b"not json", b"x" * 64), (good[0], b"\0" * 64), good])
    assert result.inserted == 1 and [i for i, _ in result.rejected] == [0, 1]


def test_insert_received_still_raises_on_a_bad_structure(store, keys):
    crew = CrewStore(store, clock=lambda: NOW, admit=lambda a, s: True)
    with pytest.raises(ev.EnvelopeError):
        crew.insert_received(b"not json", b"x" * 64)


def test_migration_marks_a_rebuild_once(store, keys):
    crew = CrewStore(store, clock=lambda: NOW)
    crew.set_gate(frozenset({keys["me"].author}))
    assert crew.rebuild_if_marked() is True
    assert crew.rebuild_if_marked() is False


def test_keyring_lists_own_authors_and_finds_identities(tmp_path):
    ring = KeyRing(tmp_path / "crew_keys")
    a, b = ring.for_commander("A"), ring.for_commander("B")
    (tmp_path / "crew_keys" / "group.json").write_text("{}", encoding="utf-8")
    fresh = KeyRing(tmp_path / "crew_keys")
    assert fresh.own_authors() == frozenset({a.author, b.author})
    assert fresh.identity_for_author(b.author).author == b.author
    assert fresh.identity_for_author("Z" * 43) is None
