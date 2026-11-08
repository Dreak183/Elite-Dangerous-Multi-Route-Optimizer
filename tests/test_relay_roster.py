"""Joins, re-seed and removal of the relay engine (ports of group.test.ts)."""
import logging

import pytest

from edmrn.ext import crew_roster
from relay_fakes import Harness, decode, items_of, key_for, new_group

INVITE_ID = "01920000-0000-7000-8000-00000000beef"


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path / "relay.db")
    yield harness
    harness.store.close()


leader = key_for("leader")
L = leader.author
alice = key_for("alice")
A = alice.author


def evs(h, c, *items):
    h.send(c, {"t": "evs", "items": list(items)})


def have(h, c, heads):
    h.send(c, {"t": "have", "heads": heads})


def remove(h, g, seq, last):
    return h.envelope(leader, seq, "remove", {"group_id": g, "member": A, "last_seq": last})


def served(h, c):
    have(h, c, {})
    return [decode(i)["seq"] for i in items_of(c.take()) if decode(i)["author"] == A]


def test_a_join_admits(h, caplog):
    caplog.set_level(logging.INFO, logger="edmrn_relay")
    g = new_group()
    _, _, lc, inv = h.group_with_invite(g)
    ac = h.authed(g, alice, join=h.join_envelope(alice, g, inv, 1))
    assert ac.next_of("have")["heads"][A] == 0
    assert h.log_seqs(g, A) == [1]
    assert h.head(g, A) is None
    assert any("admitted" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)


def test_a_non_member_without_a_join_is_refused(h):
    g = new_group()
    h.group_with_invite(g)
    assert h.authed(g, alice).closed == (4003, "not_member")


def test_admission_sends_a_fresh_have_to_connected_members(h):
    g = new_group()
    _, _, lc, inv = h.group_with_invite(g)
    h.authed(g, alice, join=h.join_envelope(alice, g, inv, 1))
    assert lc.next_of("have")["heads"] == {L: 2, A: 0}


def test_invite_expired_on_the_relay_clock_does_not_admit(h):
    g = new_group()
    lc = h.create_group_and_upload(g, leader)
    inv = h.invite(lc, g, leader, 2, expires_in=-1)
    ac = h.authed(g, alice, join=h.join_envelope(alice, g, inv, 1, created_at=h.stamp(-120)))
    assert ac.closed == (4003, "not_member")
    assert h.log_seqs(g, A) == []


def test_join_of_a_member_at_auth_is_ignored(h):
    g, _, _, lc, ac = h.two_members()
    inv = h.relay.groups[g].roster.invites
    h.close(ac)
    ac2 = h.authed(g, alice, join=h.join_envelope(alice, g, next(iter(inv)), 9))
    assert ac2.next_of("have")
    assert h.log_seqs(g, A) == [1]


def test_join_at_auth_moves_no_head_and_nothing_above_the_head_is_served(h):
    g = new_group()
    _, _, lc, inv = h.group_with_invite(g)
    join = h.join_envelope(alice, g, inv, 3)
    ac = h.authed(g, alice, join=join)
    assert ac.next_of("have")["heads"][A] == 0
    w = h.authed(g, leader)
    w.take()
    have(h, w, {})
    assert [i for i in items_of(w.take()) if decode(i)["author"] == A] == []
    evs(h, ac, h.visit(alice, 1), h.visit(alice, 2), join)
    assert [decode(i)["seq"] for i in items_of(w.take())] == [1, 2, 3]


def test_forwards_to_other_members_not_back(h):
    g, _, _, lc, ac = h.two_members()
    evs(h, ac, h.visit(alice, 2))
    assert [decode(i)["seq"] for i in items_of(lc.take())] == [2]
    assert items_of(ac.take()) == []


def _reseed(h, expires_at=None, join_at=None):
    g = new_group()
    lc = h.create_group(g, leader)
    lc.next_of("have")
    evs(h, lc, h.genesis(leader, g), h.envelope(leader, 2, "invite", {
        "group_id": g, "invite_id": INVITE_ID, "token_hash": crew_roster.token_hash("11" * 32),
        "expires_at": expires_at or h.stamp(3600), "label": None}))
    evs(h, lc, h.join_envelope(alice, g, INVITE_ID, 1, created_at=join_at))
    assert lc.next_of("have")["heads"][A] == 0
    assert h.head(g, A) is None
    assert h.authed(g, alice).next_of("have")["heads"][A] == 0


def test_reseed_admits_members_from_their_stored_joins(h):
    _reseed(h)


def test_reseed_admits_a_join_whose_invite_expired_on_the_relay_clock(h):
    _reseed(h, expires_at=h.stamp(-1), join_at=h.stamp(-120))


def _removed(h, last):
    g, _, _, lc, ac = h.two_members()
    evs(h, ac, h.join_envelope(alice, g, next(iter(h.relay.groups[g].roster.invites)), 1),
        h.visit(alice, 2), h.visit(alice, 3))
    evs(h, lc, remove(h, g, 3, last))
    assert ac.closed == (4003, "removed")
    w = h.authed(g, leader)
    return g, w


def test_removal_closes_the_member_and_caps_what_is_served(h):
    g, w = _removed(h, 2)
    assert served(h, w) == [1, 2]
    assert h.authed(g, alice).closed == (4003, "removed")


def test_removed_member_cannot_upload_above_the_cap(h):
    g, lc, ac = None, None, None
    g, w = _removed(h, 1)
    w.take()
    evs(h, w, h.visit(alice, 2))
    assert w.take("err") == [{"t": "err", "code": "removed", "author": A, "seq": 2}]


def test_have_lists_a_removed_members_head_uncapped(h):
    g, w = _removed(h, 2)
    assert w.next_of("have")["heads"][A] == 3
    assert served(h, w) == [1, 2]


def test_members_invite_kind_envelopes_count_and_write_no_roster_row(h):
    g, _, _, lc, ac = h.two_members()
    h.set_quota(g, A, 5000)
    before = h.store.db.total_changes
    evs(h, ac, *[h.envelope(alice, s, "invite", {
        "group_id": g, "invite_id": f"01920000-0000-7000-8000-{s:012x}",
        "token_hash": "11" * 32, "expires_at": h.stamp(3600), "label": None})
        for s in range(2, 101)])
    assert ac.take("err")[0] == {"t": "err", "code": "quota", "author": A, "seq": 2}
    assert h.store.db.total_changes == before
    assert h.roster_seqs(g, A) == [1]


def test_a_members_own_join_is_not_stored_in_the_roster_again(h):
    g, _, _, lc, ac = h.two_members()
    inv = next(iter(h.relay.groups[g].roster.invites))
    evs(h, ac, h.join_envelope(alice, g, inv, 9))
    assert h.log_seqs(g, A) == [1, 9]
    assert h.roster_seqs(g, A) == [1]
