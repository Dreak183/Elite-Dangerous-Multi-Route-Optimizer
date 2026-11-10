"""Catch-up, uploads, refusals and daily quotas of the relay engine."""
import json
import logging
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from edmrn_relay.engine import utc_day
from edmrn.ext import crew_protocol as proto
from edmrn_relay.store import LogRow
from relay_fakes import TOKEN, Harness, auth_frame, decode, items_of, key_for, new_group, strangers


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path / "relay.db")
    yield harness
    harness.store.close()


leader = key_for("leader")
L = leader.author
OVERSIZE = {"e": "A" * 21847, "s": "A" * 86}
BIG = {"e": "A" * 21846, "s": "A" * 86}


def evs(h, c, *items):
    h.send(c, {"t": "evs", "items": list(items)})


def have(h, c, heads):
    h.send(c, {"t": "have", "heads": heads})


def setup(h):
    g = new_group()
    return g, h.create_group_and_upload(g, leader)


def forged3(h):
    return {**h.visit(leader, 3), "s": h.visit(leader, 2)["s"]}


def test_have_right_behind_auth_is_answered_in_order(h):
    g = new_group()
    c = h.open(g)
    hello = c.next_of("hello")
    h.send(c, auth_frame(leader, g, hello["nonce"], genesis=h.genesis(leader, g)))
    have(h, c, {})
    frames = c.take()
    assert [f["t"] for f in frames] == ["have", "synced"]
    assert frames[0]["heads"] == {L: 0}


def test_unknown_type_then_have_gets_synced(h):
    g, c = setup(h)
    c.take()
    h.send(c, {"t": "from-the-future"})
    have(h, c, {})
    assert c.next_of("synced") == {"t": "synced"}
    assert c.take("err") == []


def test_gap_accepted_duplicate_ignored(h):
    g, c = setup(h)
    evs(h, c, h.visit(leader, 5))
    evs(h, c, h.visit(leader, 5))
    assert h.head(g, L) == 5
    assert h.log_seqs(g, L) == [1, 5]
    assert c.take("err") == []


def test_catch_up_sends_exactly_what_is_lacking(h):
    g, c = setup(h)
    evs(h, c, *[h.visit(leader, s) for s in range(2, 102)])
    evs(h, c, *[h.visit(leader, s) for s in range(102, 151)])
    o = h.authed(g, leader)
    o.next_of("have")
    o.take()
    have(h, o, {L: 120})
    frames = o.take()
    assert [decode(i)["seq"] for i in items_of(frames)] == list(range(121, 151))
    assert frames[-1] == {"t": "synced"}


def test_catch_up_pages_over_the_item_limit(h):
    g, c = setup(h)
    evs(h, c, *[h.visit(leader, s) for s in range(2, 102)])
    evs(h, c, *[h.visit(leader, s) for s in range(102, 151)])
    o = h.authed(g, leader)
    o.take()
    have(h, o, {})
    frames = o.take()
    assert [len(f["items"]) for f in frames if f["t"] == "evs"] == [100, 50]
    assert [decode(i)["seq"] for i in items_of(frames)] == list(range(1, 151))


def test_a_restarted_relay_serves_from_storage(h):
    g, c = setup(h)
    evs(h, c, h.visit(leader, 2))
    h2 = Harness(h.db_path, clock=h.clock)
    try:
        c2 = h2.authed(g, leader)
        assert c2.next_of("have")["heads"] == {L: 2}
        have(h2, c2, {L: 1})
        frames = c2.take()
        assert [decode(i)["seq"] for i in items_of(frames)] == [2]
        assert frames[-1] == {"t": "synced"}
        evs(h2, c2, h2.visit(leader, 3))
        assert h2.head(g, L) == 3
    finally:
        h2.store.close()


@pytest.mark.parametrize("frame", [
    {"t": "evs", "items": [{"e": "A", "s": "A"}] * 101},
    {"t": "evs", "items": "x"},
    {"t": "have", "heads": []},
    {"t": "have", "heads": {"x": 1}},
])
def test_bad_evs_and_have_frames_answer_bad_frame(h, frame):
    g, c = setup(h)
    c.take()
    changes = h.store.db.total_changes
    h.send(c, frame)
    assert c.take() == [{"t": "err", "code": "bad_frame"}]
    assert c.closed is None
    assert h.store.db.total_changes == changes


def test_member_quota_refuses_the_rest_of_the_author(h):
    g, c = setup(h)
    h.set_quota(g, L, 4999)
    evs(h, c, h.visit(leader, 2), h.visit(leader, 3), h.visit(leader, 4))
    assert c.take("err") == [{"t": "err", "code": "quota", "author": L, "seq": 3}]
    assert h.head(g, L) == 2


def test_quotas_come_from_the_configuration(tmp_path):
    hm = Harness(tmp_path / "a.db", member_quota=2)
    hg = Harness(tmp_path / "b.db", group_quota=1)
    try:
        g, c = setup(hm)
        evs(hm, c, hm.visit(leader, 2), hm.visit(leader, 3), hm.visit(leader, 4))
        assert c.take("err") == [{"t": "err", "code": "quota", "author": L, "seq": 4}]
        assert hm.head(g, L) == 3
        g, c = setup(hg)
        evs(hg, c, hg.visit(leader, 2), hg.visit(leader, 3))
        assert c.take("err") == [{"t": "err", "code": "group_quota", "author": L, "seq": 3}]
        assert hg.head(g, L) == 2
    finally:
        hm.store.close()
        hg.store.close()


def test_group_quota_refuses_and_roster_envelopes_are_exempt(h):
    g, c = setup(h)
    h.set_quota(g, "X" * 43, 80000)
    evs(h, c, h.visit(leader, 2))
    assert c.take("err") == [{"t": "err", "code": "group_quota", "author": L, "seq": 2}]
    assert h.head(g, L) == 1
    s2 = h.authed(g, leader)
    h.invite(s2, g, leader, 2)
    assert s2.take("err") == []
    assert h.roster_seqs(g, L) == [1, 2]
    assert h.head(g, L) == 2


def test_exempt_roster_envelopes_do_not_count_toward_the_quota(h):
    g, c = setup(h)
    h.invite(c, g, leader, 2)
    h.invite(c, g, leader, 3)
    assert h.store.quota_used(g, utc_day(h.clock.now), L) == 0


def test_past_quota_days_are_logged_once_then_deleted(h, caplog):
    caplog.set_level(logging.INFO, logger="edmrn_relay")
    g, c = setup(h)
    D = utc_day(h.clock.now)
    h.set_quota(g, L, 7, D - 1)
    h.set_quota(g, "Y" * 43, 5, D - 1)
    h.clock.next_utc_day()
    evs(h, c, h.visit(leader, 2))
    evs(h, c, h.visit(leader, 3))
    day = datetime.fromtimestamp((D - 1) * 86400, timezone.utc).strftime("%Y-%m-%d")
    assert [r.getMessage() for r in caplog.records if "crew-daily-total" in r.getMessage()] == [
        json.dumps({"event": "crew-daily-total", "group": g, "day": day, "envelopes": 12},
                   separators=(",", ":"))]
    assert h.store.db.execute("SELECT COUNT(*) FROM quota WHERE group_id = ? AND day < ?",
                              (g, D + 1)).fetchone()[0] == 0


def test_non_member_envelope_is_refused_naming_author_and_seq(h):
    g, c = setup(h)
    bob = key_for("bob")
    changes = h.store.db.total_changes
    evs(h, c, h.visit(bob, 4))
    assert c.take("err") == [{"t": "err", "code": "not_member", "author": bob.author, "seq": 4}]
    assert h.head(g, bob.author) is None
    assert h.store.db.total_changes == changes


def test_an_author_beyond_last_seq_is_refused_removed(h):
    g, c = setup(h)
    roster = h.relay.groups[g].roster
    roster.members[L] = replace(roster.members[L], last_seq=1)
    changes = h.store.db.total_changes
    evs(h, c, h.visit(leader, 2))
    assert c.take("err") == [{"t": "err", "code": "removed", "author": L, "seq": 2}]
    assert h.head(g, L) == 1
    assert h.store.db.total_changes == changes


def test_bad_signature_stops_the_author_in_the_frame(h):
    g, c = setup(h)
    evs(h, c, forged3(h), h.visit(leader, 4))
    assert c.take("err") == [{"t": "err", "code": "bad_sig", "author": L, "seq": 3}]
    assert h.head(g, L) == 1


def test_oversize_item_keeps_what_precedes_it(h):
    g, c = setup(h)
    evs(h, c, h.visit(leader, 2), OVERSIZE, h.visit(leader, 3))
    assert c.take("err") == [{"t": "err", "code": "too_big"}]
    assert c.closed == (4008, "too_big")
    assert h.head(g, L) == 2


def test_undecodable_item_ends_the_session_after_the_items_before_it(h):
    g, c = setup(h)
    evs(h, c, h.visit(leader, 2), {"e": "A", "s": "A"})
    assert c.take("err") == [{"t": "err", "code": "bad_frame"}]
    assert c.closed == (4008, "bad item")
    assert h.head(g, L) == 2


def test_broken_signature_field_answers_bad_sig_and_refuses_onward(h):
    g, c = setup(h)
    evs(h, c, h.visit(leader, 2), {**h.visit(leader, 3), "s": "A"})
    assert c.take("err") == [{"t": "err", "code": "bad_sig", "author": L, "seq": 3}]
    evs(h, c, h.visit(leader, 4))
    assert c.take() == []
    assert h.head(g, L) == 2


def test_evs_items_over_512_kib_close_4008(h):
    g, c = setup(h)
    changes = h.store.db.total_changes
    evs(h, c, *[BIG] * 25)
    assert c.take("err") == [{"t": "err", "code": "too_big"}]
    assert c.closed == (4008, "too_big")
    assert h.head(g, L) == 1
    assert h.store.db.total_changes == changes


def test_quota_refusal_also_refuses_a_later_roster_envelope(h):
    g, c = setup(h)
    h.set_quota(g, L, 4999)
    evs(h, c, h.visit(leader, 2), h.visit(leader, 3), h.visit(leader, 4))
    evs(h, c, h.envelope(leader, 5, "invite", {
        "group_id": g, "invite_id": "01920000-0000-7000-8000-00000000f001",
        "token_hash": "11" * 32, "expires_at": h.stamp(3600), "label": None}),
        h.visit(leader, 6))
    assert h.log_seqs(g, L) == [1, 2]
    assert h.head(g, L) == 2


def test_bad_signature_refuses_later_frames(h):
    g, c = setup(h)
    evs(h, c, h.visit(leader, 2), forged3(h))
    h.invite(c, g, leader, 5)
    evs(h, c, h.visit(leader, 6))
    assert h.log_seqs(g, L) == [1, 2]


def test_daily_refusal_ends_with_the_utc_day(h):
    g, c = setup(h)
    h.set_quota(g, L, 4999)
    evs(h, c, h.visit(leader, 2), h.visit(leader, 3))
    assert c.take("err")[0]["code"] == "quota"
    h.clock.next_utc_day()
    evs(h, c, h.visit(leader, 3))
    assert h.log_seqs(g, L) == [1, 2, 3]


def test_a_frame_behind_a_4008_close_is_not_processed(h):
    g, c = setup(h)
    evs(h, c, h.visit(leader, 2), OVERSIZE)
    evs(h, c, h.visit(leader, 4))
    assert c.closed[0] == 4008
    assert h.log_seqs(g, L) == [1, 2]
    assert h.head(g, L) == 2


def test_same_after_the_aggregate_size_close(h):
    g, c = setup(h)
    evs(h, c, *[BIG] * 25)
    evs(h, c, h.visit(leader, 2))
    assert h.log_seqs(g, L) == [1]


def test_beyond_the_refusal_cap_the_session_ends(h):
    g, c = setup(h)
    h.set_quota(g, L, 5000)
    evs(h, c, h.visit(leader, 2), *[h.visit(k, 1) for k in strangers(65)])
    assert c.closed == (4008, "too many refusals")
    assert len(h.session(c).refused) == 66
    h.invite(c, g, leader, 3)
    assert h.log_seqs(g, L) == [1]


def test_exactly_the_refusal_cap_keeps_the_session(h):
    g, c = setup(h)
    evs(h, c, *[h.visit(k, 1) for k in strangers(64)])
    assert c.closed is None
    evs(h, c, h.visit(strangers(1)[0], 1))
    assert c.closed == (4008, "too many refusals")


def test_more_than_eight_refused_authors_do_not_end_the_session(h):
    g, c = setup(h)
    c.take()
    evs(h, c, *[h.visit(k, 1) for k in strangers(12)])
    assert [f["code"] for f in c.take("err")] == ["not_member"] * 12
    assert c.closed is None
    evs(h, c, h.visit(leader, 2))
    assert h.log_seqs(g, L) == [1, 2]


def test_expired_daily_refusals_are_pruned(h):
    g, c = setup(h)
    c.take()
    day = utc_day(h.clock.now)
    h.session(c).refused = {f"{i:02d}" + "Z" * 41: (5, day - 1) for i in range(70)}
    stranger = strangers(1)[0]
    evs(h, c, h.visit(stranger, 1))
    assert [f["code"] for f in c.take("err")] == ["not_member"]
    assert c.closed is None
    assert list(h.session(c).refused) == [stranger.author]


def test_writes_of_a_frame_are_one_transaction(h, caplog):
    g, c = setup(h)
    h.store.db.execute("CREATE TRIGGER boom BEFORE INSERT ON quota "
                       "BEGIN SELECT RAISE(ABORT, 'boom'); END")
    evs(h, c, h.visit(leader, 2), h.visit(leader, 3))
    assert c.closed == (1011, "internal error")
    assert h.log_seqs(g, L) == [1]
    assert h.head(g, L) == 1
    assert h.relay.groups[g].heads == {L: 1}
    assert any(r.levelno == logging.ERROR and r.exc_info for r in caplog.records)


def add_member(h, c, g, name):
    invite_id = h.invite(c, g, leader, h.head(g, L) + 1)
    key = key_for(name)
    join = h.envelope(key, 1, "join", {"group_id": g, "invite_id": invite_id, "token": TOKEN,
                                       "commander": name})
    h.store.add_roster_envelope(g, LogRow(key.author, 1, proto.unb64(join["e"], 1 << 20),
                                          proto.unb64(join["s"], 64)))
    h.relay.reload_roster(h.relay.groups[g])
    return key


def test_forwarding_reaches_other_members_in_order_and_not_the_sender(h):
    g, c = setup(h)
    b = h.authed(g, add_member(h, c, g, "bob"))
    assert b.closed is None
    b.take()
    c.take()
    evs(h, c, h.visit(leader, 3), h.visit(leader, 4))
    assert [decode(i)["seq"] for i in items_of(b.take())] == [3, 4]
    assert items_of(c.take()) == []


def test_only_the_leaders_roster_envelopes_are_exempt(h):
    g, c = setup(h)
    bob = add_member(h, c, g, "bob")
    h.set_quota(g, bob.author, 5000)
    b = h.authed(g, bob)
    b.take()
    h.invite(b, g, bob, 1)
    assert b.take("err") == [{"t": "err", "code": "quota", "author": bob.author, "seq": 1}]


def test_a_duplicate_of_a_stored_head_costs_no_quota(h):
    g, c = setup(h)
    evs(h, c, h.visit(leader, 5))
    evs(h, c, h.visit(leader, 5))
    assert h.store.quota_used(g, utc_day(h.clock.now), L) == 1


def test_catch_up_stops_at_the_last_seq_of_a_removed_member(h):
    g, c = setup(h)
    evs(h, c, h.visit(leader, 2), h.visit(leader, 3), h.visit(leader, 4))
    roster = h.relay.groups[g].roster
    roster.members[L] = replace(roster.members[L], last_seq=2)
    c.take()
    have(h, c, {})
    assert [decode(i)["seq"] for i in items_of(c.take())] == [1, 2]


def test_the_daily_cleanup_runs_once_per_day(h):
    g, c = setup(h)
    calls = []
    original = h.store.take_past_quota
    h.store.take_past_quota = lambda *a: calls.append(a) or original(*a)
    evs(h, c, h.visit(leader, 2))
    evs(h, c, h.visit(leader, 3))
    assert calls == []
    h.clock.next_utc_day()
    evs(h, c, h.visit(leader, 4))
    evs(h, c, h.visit(leader, 5))
    assert len(calls) == 1


def test_a_later_bad_signature_keeps_the_first_refused_seq(h):
    g, c = setup(h)
    h.set_quota(g, L, 4999)
    evs(h, c, h.visit(leader, 2), h.visit(leader, 3))
    evs(h, c, {**h.visit(leader, 5), "s": "A"})
    c.take()
    evs(h, c, h.visit(leader, 4))
    assert c.take() == []
    assert h.session(c).refused[L][0] == 3


def test_a_leaders_join_is_exempt_and_never_enters_the_roster_table(h):
    g, c = setup(h)
    invite_id = h.invite(c, g, leader, 2)
    evs(h, c, h.join_envelope(leader, g, invite_id, 3))
    assert c.take("err") == []
    assert h.log_seqs(g, L) == [1, 2, 3]
    assert h.roster_seqs(g, L) == [1, 2]
    assert h.store.quota_used(g, utc_day(h.clock.now), L) == 0


def _junk(sizes):
    """Undecodable items whose counted sizes (as the client batches them) are `sizes`."""
    return [{"e": "A" * (n - 86 - proto._ITEM_OVERHEAD), "s": "A" * 86} for n in sizes]


def test_the_evs_size_limit_is_inclusive(h):
    g, c = setup(h)
    sizes = [20102] * 24 + [20920] * 2
    assert sum(sizes) == proto.MAX_EVS_BYTES
    evs(h, c, *_junk(sizes))
    assert c.closed == (4008, "bad item")
    g, c = setup(h)
    sizes[-1] += 1
    evs(h, c, *_junk(sizes))
    assert c.closed == (4008, "too_big")


def test_forwards_survive_a_failing_roster_reload(h):
    g, _, _, lc, ac = h.two_members()
    original = h.relay.reload_roster
    calls = []

    def flaky(group, leader=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return original(group, leader)

    h.relay.reload_roster = flaky
    h.invite(lc, g, leader, 3)
    assert [decode(i)["seq"] for i in items_of(ac.take())] == [3]
    assert lc.closed == (1011, "internal error")
    assert h.log_seqs(g, L) == [1, 2, 3]


def test_a_failing_roster_reload_at_group_creation_drops_the_group(h):
    g = new_group()
    original = h.relay.reload_roster
    calls = []

    def flaky(group, leader=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return original(group, leader)

    h.relay.reload_roster = flaky
    bystander = h.open(g)  # unauthenticated: holds no group state, so the drop does not touch it
    c = h.create_group(g, leader)
    assert c.closed == (1011, "internal error")
    assert bystander.closed is None
    assert g not in h.relay.groups
    c2 = h.authed(g, leader)
    assert c2.closed is None
    assert c2.next_of("have")["heads"] == {L: 0}
    assert h.relay.groups[g].leader == L


# -- paged catch-up ----------------------------------------------------------------------------

T2 = "22" * 32
BULK = 250  # envelopes per author; more than two pages
_BULK_ROWS = {}


def bulk_group(h, n=BULK):
    """(g, lc, alice, bob, bc): leader, alice and bob in one group, every author's log stored up
    to seq `n` (the leader's first three are genesis and two invites, the others' first is a join)."""
    g = new_group()
    alice, bob = key_for("alice"), key_for("bob")
    lc = h.create_group_and_upload(g, leader)
    first = h.invite(lc, g, leader, 2)
    second = h.invite(lc, g, leader, 3, token=T2)
    h.authed(g, alice, join=h.join_envelope(alice, g, first, 1))
    bc = h.authed(g, bob, join=h.join_envelope(bob, g, second, 1, token=T2))
    for key, start in ((leader, 4), (alice, 2), (bob, 2)):
        rows = []
        for seq in range(start, n + 1):
            if (key.author, seq) not in _BULK_ROWS:  # signing 750 envelopes per test is slow
                item = h.visit(key, seq)
                _BULK_ROWS[key.author, seq] = (proto.unb64(item["e"], 1 << 20),
                                               proto.unb64(item["s"], 64))
            rows.append((g, key.author, seq, *_BULK_ROWS[key.author, seq]))
        h.store.db.executemany("INSERT INTO log (group_id, author, seq, e, s) VALUES (?,?,?,?,?)",
                               rows)
        h.store.db.execute("INSERT OR REPLACE INTO heads (group_id, author, seq) VALUES (?,?,?)",
                           (g, key.author, n))
        h.relay.groups[g].heads[key.author] = n
    lc.take()
    bc.take()
    return g, lc, alice, bob, bc


def seqs_by_author(frames):
    got = {}
    for item in items_of(frames):
        env = decode(item)
        got.setdefault(env["author"], []).append(env["seq"])
    return got


def test_a_catch_up_is_served_one_page_per_step(h):
    g, lc, alice, bob, bc = bulk_group(h)
    h.auto_pump = False
    ac = h.authed(g, alice)
    ac.take()
    have(h, ac, {})
    assert ac.take() == []  # nothing is built until the server pumps
    per_step = []
    while h.pump(ac):
        per_step.append(sum(len(f["items"]) for f in ac.take() if f["t"] == "evs"))
    assert per_step[:3] == [100, 100, 50] and sum(per_step) == 3 * BULK - 0
    assert [f["t"] for f in ac.take()] == ["synced"]
    assert ac.session.catchup is None


def test_an_upload_during_a_paged_catch_up_reaches_the_receiver_without_a_gap(h):
    """B1 S7: the receiver's highest stored seq per author must never pass an envelope it lacks.
    Forwards are withheld while the catch-up runs and the cursor reads them from the store."""
    g, lc, alice, bob, bc = bulk_group(h)
    h.auto_pump = False
    ac = h.authed(g, alice)
    ac.take()
    have(h, ac, {})
    received = []
    top = {leader.author: BULK, bob.author: BULK}
    uploaders = {leader.author: (leader, lc), bob.author: (bob, bc)}
    steps = 0
    while True:
        more = h.pump(ac)
        received += ac.take()
        steps += 1
        if more and steps <= 4:  # live uploads by two authors while the catch-up is under way
            for author, (key, conn) in uploaders.items():
                top[author] += 1
                evs(h, conn, h.visit(key, top[author]))
            assert ac.take() == []  # withheld, not sent ahead of the pages
        if not more:
            break
    assert received[-1]["t"] == "synced"
    got = seqs_by_author(received)
    assert got[leader.author] == list(range(1, top[leader.author] + 1))
    assert got[bob.author] == list(range(1, top[bob.author] + 1))
    assert got[alice.author] == list(range(1, BULK + 1))
    assert top[leader.author] == BULK + 4 and top[bob.author] == BULK + 4
    # after synced the session is live: an upload is forwarded at once
    evs(h, lc, h.visit(leader, BULK + 5))
    assert [decode(i)["seq"] for i in items_of(ac.take())] == [BULK + 5]


def test_closing_mid_catch_up_drops_the_cursor(h):
    g, lc, alice, bob, bc = bulk_group(h)
    h.auto_pump = False
    ac = h.authed(g, alice)
    ac.take()
    have(h, ac, {})
    assert h.pump(ac)
    ac.take()
    h.close(ac)
    assert ac.session.catchup is None
    assert h.relay.pump(ac.session) == []
    assert ac.take() == []


def test_replacing_a_session_mid_catch_up_drops_its_cursor(h):
    g, lc, alice, bob, bc = bulk_group(h)
    h.auto_pump = False
    old = h.authed(g, alice)
    old.take()
    have(h, old, {})
    assert h.pump(old)
    old.take()
    new = h.authed(g, alice)
    assert old.closed == (4000, "replaced") and old.session.catchup is None
    assert h.relay.pump(old.session) == []
    assert old.take() == []
    assert new.session.catchup is None and h.pump(new) is False


def test_a_second_have_replaces_the_catch_up_in_flight(h):
    g, lc, alice, bob, bc = bulk_group(h)
    h.auto_pump = False
    ac = h.authed(g, alice)
    ac.take()
    have(h, ac, {})
    assert h.pump(ac)
    ac.take()
    have(h, ac, {leader.author: BULK, alice.author: BULK, bob.author: BULK - 3})
    while h.pump(ac):
        pass
    frames = ac.take()
    assert [f["t"] for f in frames].count("synced") == 1
    assert sum(len(f["items"]) for f in frames if f["t"] == "evs") == 3
    assert ac.session.catchup is None


def test_the_group_dropped_by_a_failed_reload_ends_the_catch_up(h):
    g, lc, alice, bob, bc = bulk_group(h)
    h.auto_pump = False
    ac = h.authed(g, alice)
    ac.take()
    have(h, ac, {})
    assert h.pump(ac)
    ac.take()
    original = h.relay.reload_roster

    def broken(group, leader=None):
        raise RuntimeError("boom")

    h.relay.reload_roster = broken
    h.invite(lc, g, leader, BULK + 1)
    h.relay.reload_roster = original
    assert ac.closed == (1011, "internal error") and ac.session.catchup is None
    assert h.relay.pump(ac.session) == []
    assert ac.take() == []


def test_a_failing_page_read_closes_the_session_with_1011(h):
    g, lc, alice, bob, bc = bulk_group(h)
    h.auto_pump = False
    ac = h.authed(g, alice)
    ac.take()
    have(h, ac, {})

    def boom(*args):
        raise RuntimeError("disk")

    h.store.log_after = boom
    h.pump(ac)
    assert ac.closed == (1011, "internal error") and ac.session.catchup is None


def test_a_failing_cleanup_is_retried_with_the_next_batch(h, caplog):
    caplog.set_level(logging.INFO, logger="edmrn_relay")
    g, c = setup(h)
    D = utc_day(h.clock.now)
    h.set_quota(g, L, 7, D - 1)
    h.clock.next_utc_day()
    original = h.store.take_past_quota
    failures = []

    def once(*a):
        if not failures:
            failures.append(a)
            raise RuntimeError("busy")
        return original(*a)

    h.store.take_past_quota = once
    evs(h, c, h.visit(leader, 2))
    assert c.closed == (1011, "internal error")
    c2 = h.authed(g, leader)
    evs(h, c2, h.visit(leader, 2))
    assert sum("crew-daily-total" in r.getMessage() for r in caplog.records) == 1
    assert h.store.db.execute("SELECT COUNT(*) FROM quota WHERE group_id = ? AND day < ?",
                              (g, D + 1)).fetchone()[0] == 0
