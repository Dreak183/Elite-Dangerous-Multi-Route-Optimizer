"""Hello, auth and frame checks of the relay engine (ports of group.test.ts, ADR-0009)."""
import pytest

from edmrn.ext import crew_protocol as proto
from edmrn_relay import engine
from relay_fakes import (Harness, auth_frame, key_for, new_group, strangers)


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path / "relay.db")
    yield harness
    harness.store.close()


leader = key_for("leader")


def test_hello_then_bad_frame_before_auth(h):
    g = new_group()
    c = h.open(g)
    hello = c.next_of("hello")
    assert hello["v"] == 1
    assert hello["source"] == "https://example.invalid/edmrn"
    assert len(proto.unb64(hello["nonce"], 32)) == 32
    h.send(c, "not json")
    assert c.next_of("err") == {"t": "err", "code": "bad_frame"}
    assert c.closed is None


def test_empty_group_refuses_auth_without_genesis(h):
    assert h.authed(new_group(), leader).closed == (4003, "no group")


def test_genesis_by_an_author_not_listed_is_refused(h):
    g = new_group()
    alice = key_for("alice")
    assert h.authed(g, alice, genesis=h.genesis(alice, g)).closed == (4003, "no group")


def test_genesis_for_another_group_is_refused(h):
    g = new_group()
    assert h.authed(g, leader, genesis=h.genesis(leader, new_group())).closed == (4003, "no group")


def test_nothing_is_stored_before_a_valid_genesis(h):
    g = new_group()
    alice = key_for("alice")
    changes = h.store.db.total_changes
    assert h.authed(g, leader).closed == (4003, "no group")
    assert h.authed(g, alice, genesis=h.genesis(alice, g)).closed == (4003, "no group")
    assert h.authed(g, leader, genesis=h.genesis(leader, new_group())).closed == (4003, "no group")
    assert h.authed(g, leader, sig="A" * 86).closed == (4001, "bad signature")
    assert h.store.db.total_changes == changes
    assert set(h.table_counts(g).values()) == {0}
    assert g not in h.relay.groups
    h.create_group(g, leader)
    assert h.roster_seqs(g, leader.author) == [1]
    row = h.store.db.execute("SELECT leader FROM groups WHERE group_id = ?", (g,)).fetchone()
    assert row[0] == leader.author
    assert h.head(g, leader.author) is None


def test_unknown_groups_leave_nothing_behind(h):
    for _ in range(100):
        c = h.open(new_group())
        h.close(c)
    assert h.relay.groups == {}
    assert h.store.db.execute("SELECT COUNT(*) FROM groups").fetchone()[0] == 0
    g = new_group()
    c = h.create_group(g, leader)
    h.close(c)
    assert g in h.relay.groups


def test_bad_signature_closes_4001(h):
    g = new_group()
    assert h.authed(g, leader, sig="A" * 86).closed == (4001, "bad signature")
    c = h.open(g)
    hello = c.next_of("hello")
    frame = auth_frame(leader, g, hello["nonce"])
    del frame["sig"]
    h.send(c, frame)
    assert c.closed == (4001, "bad signature")


def test_signature_over_another_nonce_or_group_closes_4001(h):
    g = new_group()
    c = h.open(g)
    c.next_of("hello")
    h.send(c, auth_frame(leader, g, proto.b64(bytes(32))))
    assert c.closed == (4001, "bad signature")

    c = h.open(g)
    hello = c.next_of("hello")
    h.send(c, auth_frame(leader, g, hello["nonce"], group=new_group()))
    assert c.closed == (4001, "bad auth")

    assert h.authed(g, leader, author="x").closed == (4001, "bad auth")


def test_first_frame_other_than_auth_closes_4001(h):
    c = h.open(new_group())
    h.send(c, {"t": "have", "heads": {}})
    assert c.closed == (4001, "auth first")


def test_genesis_creates_the_group_without_moving_the_head(h):
    g = new_group()
    c = h.create_group(g, leader)
    assert c.next_of("have") == {"t": "have", "heads": {leader.author: 0}}
    assert h.log_seqs(g, leader.author) == [1]
    assert h.head(g, leader.author) is None


def test_non_member_without_join_is_refused(h):
    g = new_group()
    h.create_group(g, leader)
    assert h.authed(g, key_for("bob")).closed == (4003, "not_member")


def test_second_connection_replaces_the_first(h):
    g = new_group()
    first = h.create_group(g, leader)
    second = h.authed(g, leader)
    second.next_of("have")
    assert first.closed == (4000, "replaced")
    assert second.closed is None


def test_auth_timer_closes_an_unauthenticated_socket(h):
    g = new_group()
    c = h.open(g)
    h.expire(c)
    assert c.closed == (4001, "auth timeout")
    conn = h.create_group(new_group(), leader)
    h.expire(conn)
    assert conn.closed is None


def test_a_stale_socket_that_sends_auth_is_closed_and_replaces_nothing(h):
    g = new_group()
    live = h.create_group(g, leader)
    stale = h.open(g)
    hello = stale.next_of("hello")
    h.clock.advance(11)
    h.send(stale, auth_frame(leader, g, hello["nonce"]))
    assert stale.closed == (4001, "auth timeout")
    assert live.closed is None


def test_auth_timeout_is_read_at_call_time(h, monkeypatch):
    monkeypatch.setattr(engine, "AUTH_TIMEOUT", 1.0)
    c = h.open(new_group())
    h.clock.advance(2)
    h.send(c, "not json")
    assert c.closed == (4001, "auth timeout")


def test_oversize_frames_close_4008(h):
    c = h.create_group(new_group(), leader)
    h.send(c, "not json")
    assert c.next_of("err") == {"t": "err", "code": "bad_frame"}
    h.send(c, "x" * ((1 << 20) + 1))
    assert c.closed == (4008, "too_big")
    assert c.take("err") == []


def test_frame_size_counts_utf8_bytes(h):
    c = h.create_group(new_group(), leader)
    h.send(c, "é" * 600_000)
    assert c.closed == (4008, "too_big")


def test_binary_frames_close_with_reason_binary(h):
    c = h.create_group(new_group(), leader)
    h.send(c, bytes([1, 2, 3]))
    assert c.closed == (4008, "binary")


def test_unknown_frame_type_is_ignored(h):
    c = h.create_group(new_group(), leader)
    c.take()
    h.send(c, {"t": "from-the-future"})
    assert c.take() == []
    assert c.closed is None
    h.send(c, {"t": "x" * 33})
    assert c.next_of("err") == {"t": "err", "code": "bad_frame"}


def test_frames_after_a_close_are_not_processed(h):
    g = new_group()
    c = h.open(g)
    hello = c.next_of("hello")
    h.send(c, auth_frame(leader, g, hello["nonce"], group=new_group()))
    assert c.closed == (4001, "bad auth")
    before = h.table_counts(g)
    h.send(c, auth_frame(leader, g, hello["nonce"], genesis=h.genesis(leader, g)))
    assert c.frames == []
    assert h.table_counts(g) == before


def test_connect_refuses_a_group_id_that_is_not_a_uuid7(h):
    with pytest.raises(ValueError):
        h.open("not-a-group")


def test_strangers_are_distinct_valid_authors():
    a, b = strangers(2)
    assert a.author != b.author and len(a.sign(b"x")) == 64


def test_genesis_checks_each_refuse_without_writing(h):
    g = new_group()
    alice = key_for("alice")
    good = h.genesis(leader, g)
    forged = {"e": good["e"], "s": h.genesis(leader, g)["s"]}
    # signed by the leader over bytes that name alice as author: only the author check refuses it
    other_author = h.genesis(alice, g)
    other_author["s"] = proto.b64(leader.sign(proto.unb64(other_author["e"], 16384)))
    # a valid invite by the leader for this group: only the kind check refuses it
    wrong_kind = h.envelope(leader, 1, "invite", {
        "group_id": g, "invite_id": new_group(), "token_hash": "0" * 64,
        "expires_at": h.stamp(3600), "label": None})
    changes = h.store.db.total_changes
    for genesis in (forged, other_author, wrong_kind):
        assert h.authed(g, leader, genesis=genesis).closed == (4003, "no group")
    assert h.store.db.total_changes == changes
    assert g not in h.relay.groups


def test_closing_session_ignores_everything_before_disconnect(h):
    g = new_group()
    c = h.open(g)
    session = h.session(c)
    actions = h.relay.receive(session, proto.encode({"t": "have", "heads": {}}))
    assert [type(a).__name__ for a in actions] == ["Close"] and session.closing
    assert h.relay.receive(session, "not json") == []
    assert h.relay.expire_auth(session) == []


def test_replace_skips_a_member_session_already_closing(h):
    g = new_group()
    first = h.create_group(g, leader)
    old = h.session(first)
    old.closing = True
    second = h.open(g)
    hello = second.next_of("hello")
    actions = h.relay.receive(h.session(second), proto.encode(auth_frame(leader, g, hello["nonce"])))
    assert not any(type(a).__name__ == "Close" for a in actions)
