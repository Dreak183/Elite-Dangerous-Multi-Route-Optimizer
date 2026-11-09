"""Statuses of the relay engine: rate, checks, hand-over at auth, persistence."""
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from edmrn.ext import crew_protocol as proto
from edmrn.ext import crew_roster
from relay_fakes import TOKEN, Harness, key_for, uuid7

ROOT = Path(__file__).resolve().parent.parent

leader = key_for("leader")
L = leader.author
alice = key_for("alice")
A = alice.author


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path / "relay.db")
    yield harness
    harness.store.close()


def doc(frame):
    return json.loads(proto.unb64(frame["e"], 8192))


def stored(h, g):
    return [a for a, _, _ in h.store.statuses(g)]


def test_status_is_forwarded_rate_limited_and_checked(h):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.5))
    assert doc(lc.next_of("st"))["status"]["hull"] == 0.5
    h.send(ac, h.status(alice, 0.4))
    assert ac.next_of("err")["code"] == "rate"
    h.send(lc, h.status(leader, 1, author=A))
    assert lc.next_of("err")["code"] == "bad_frame"


def test_oversize_status_and_bad_signature(h):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, {"t": "st", "e": proto.b64(bytes(8193)), "s": "A" * 86})
    assert ac.next_of("err")["code"] == "too_big"
    foreign = h.status(alice, 0.8, author=L)
    forged = dict(h.status(leader, 0.8), s=foreign["s"])
    h.send(lc, forged)
    assert lc.next_of("err")["code"] == "bad_sig"
    assert lc.take("st") == [] and ac.take("st") == []


@pytest.mark.parametrize("frame", [
    {"t": "st", "e": 5, "s": "A" * 86},
    {"t": "st", "e": "AAAA"},
    {"t": "st", "e": "!!", "s": "A" * 86},
    {"t": "st", "e": "AAAA", "s": "A" * 86},        # not JSON
])
def test_malformed_status_frames_answer_bad_frame(h, frame):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, frame)
    assert ac.next_of("err")["code"] == "bad_frame"
    assert lc.take("st") == []


@pytest.mark.parametrize("body", [
    [1, 2],
    {"author": "someone else", "at": "t", "status": {}},
    {"at": "t", "status": {}},
    {"author": "ME", "at": 5, "status": {}},
    {"author": "ME", "at": "t", "status": []},
])
def test_status_document_shape_is_checked(h, body):
    g, _, _, lc, ac = h.two_members()
    if isinstance(body, dict) and body.get("author") == "ME":
        body = dict(body, author=A)
    data = json.dumps(body).encode()
    h.send(ac, proto.status_frame(data, alice.sign(data)))
    assert ac.next_of("err")["code"] == "bad_frame"
    assert lc.take("st") == []


def test_a_signature_of_the_wrong_length_answers_bad_frame_not_bad_sig(h):
    g, _, _, lc, ac = h.two_members()
    frame = h.status(alice, 0.5)
    h.send(ac, dict(frame, s=proto.b64(bytes(3))))
    assert ac.next_of("err")["code"] == "bad_frame"


def test_a_refused_status_does_not_open_the_rate_window(h):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.5, author=L))
    assert ac.next_of("err")["code"] == "bad_frame"
    h.send(ac, h.status(alice, 0.6))
    assert doc(lc.next_of("st"))["status"]["hull"] == 0.6


def test_last_status_of_a_member_who_left_reaches_a_new_connection(h):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.3))
    lc.next_of("st")
    h.close(ac)
    assert stored(h, g) == [A]
    again = h.authed(g, leader)
    assert doc(again.next_of("st"))["author"] == A


def test_stored_status_of_a_removed_member_is_not_sent(h):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.3))
    lc.next_of("st")
    h.close(ac)
    h.send(lc, {"t": "evs", "items": [h.envelope(
        leader, 3, "remove", {"group_id": g, "member": A, "last_seq": 1})]})
    again = h.authed(g, leader)
    assert again.take("st") == []


def test_live_status_reaches_a_new_connection(h):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.3))
    again = h.authed(g, leader)
    assert doc(again.next_of("st"))["author"] == A
    assert h.store.statuses(g) == []


def test_a_stored_status_of_a_present_member_is_not_sent_twice(h):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.3))
    h.store.save_status(g, A, b"stale", bytes(64))  # an older row; the live status wins
    again = h.authed(g, leader)
    frames = again.take("st")
    assert len(frames) == 1 and doc(frames[0])["status"]["hull"] == 0.3


def test_a_member_is_not_sent_its_own_status_at_auth(h):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.3))
    ac2 = h.authed(g, alice)  # the old connection is still live while the new one authenticates
    assert ac2.take("st") == []
    h.close(ac2)
    ac3 = h.authed(g, alice)  # now only the stored copy exists
    assert ac3.take("st") == []


def test_status_rate_is_per_member_across_reconnects(h):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.3))
    lc.take()
    ac2 = h.authed(g, alice)
    assert ac.closed[0] == 4000
    ac2.take()
    h.send(ac2, h.status(alice, 0.2))
    assert ac2.next_of("err")["code"] == "rate"
    h.clock.advance(30)
    h.send(ac2, h.status(alice, 0.1))
    assert doc(lc.next_of("st"))["status"]["hull"] == 0.1


def test_a_replaced_connections_status_is_persisted(h):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.3))
    h.authed(g, alice)
    assert stored(h, g) == [A]


def test_a_dropped_group_keeps_its_members_statuses(h):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.3))
    original = h.relay.reload_roster
    failed = []

    def flaky(group, leader=None):
        if not failed:
            failed.append(1)
            raise RuntimeError("boom")
        return original(group, leader)

    h.relay.reload_roster = flaky
    item = h.envelope(leader, 3, "invite", {
        "group_id": g, "invite_id": uuid7(), "token_hash": crew_roster.token_hash(TOKEN),
        "expires_at": h.stamp(3600), "label": None})
    actions = h.relay.receive(h.session(lc), proto.encode({"t": "evs", "items": [item]}))
    # before any Close is applied (so before any disconnect): the drop itself saved the status
    assert stored(h, g) == [A]
    assert g not in h.relay.groups
    h._apply(actions)
    assert ac.closed == (1011, "internal error")
    assert h.session(ac).status is None


def test_status_content_is_never_validated(h, monkeypatch):
    import edmrn.ext.crew_events
    import edmrn.ext.crew_protocol

    def boom(*a, **k):
        raise AssertionError("the relay must not validate status content")

    monkeypatch.setattr(edmrn.ext.crew_events, "validate_status", boom)
    monkeypatch.setattr(edmrn.ext.crew_protocol, "parse_status", boom)
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 5.0, edsy_url="https://edsy.org/#/I=AAAA"))
    assert doc(lc.next_of("st"))["status"]["hull"] == 5.0


def test_the_relay_never_imports_the_clients_status_code():
    code = ("import sys, edmrn_relay.engine, edmrn_relay.store; "
            "bad = [m for m in ('edmrn.ext.crew_status', 'edmrn.logger') if m in sys.modules]; "
            "print(bad); sys.exit(1 if bad else 0)")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "relay")]))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                       cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_shutdown_persists_live_statuses_and_closes_1001(h):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.3))
    actions = h.relay.shutdown()
    assert stored(h, g) == [A]  # persisted by shutdown itself, before the closes are applied
    h._apply(actions)
    assert ac.closed == lc.closed == (1001, "relay shutting down")
    assert stored(h, g) == [A]


def test_shutdown_closes_sessions_that_have_not_authenticated(h):
    g, _, _, lc, ac = h.two_members()
    pending = h.open(g)
    h.shutdown()
    assert pending.closed == (1001, "relay shutting down")


def _spy(h):
    """Every nonce the relay sent and every `e` any client sent, collected as they pass."""
    seen = {"nonces": [], "es": []}
    apply, send = h._apply, h.send

    def spy_apply(actions):
        for a in actions:
            if hasattr(a, "text"):
                f = json.loads(a.text)
                if f.get("t") == "hello":
                    seen["nonces"].append(f["nonce"])
        apply(actions)

    def spy_send(conn, frame):
        if isinstance(frame, dict):
            for item in frame.get("items", []) + [frame, frame.get("join"), frame.get("genesis")]:
                if isinstance(item, dict) and isinstance(item.get("e"), str):
                    seen["es"].append(item["e"])
        send(conn, frame)

    h._apply, h.send = spy_apply, spy_send
    return seen


def test_logs_never_carry_envelope_contents(h, caplog):
    caplog.set_level(logging.DEBUG, "edmrn_relay")
    seen = _spy(h)
    g, _, _, lc, ac = h.two_members()
    secret = h.envelope(alice, 2, "visit", {"commander": "A", "system": "Secret Sol",
                                            "address": "2", "coords": None})
    h.send(ac, {"t": "evs", "items": [secret]})
    st = h.status(alice, 0.3, system="Secret Sol")
    h.send(ac, st)
    h.send(lc, {"t": "evs", "items": [h.envelope(
        leader, 3, "remove", {"group_id": g, "member": A, "last_seq": 2})]})
    h.shutdown()
    # genesis, invite, join, visit, status, remove: all seen; two connections, two nonces
    assert len(seen["es"]) >= 6 and len(seen["nonces"]) >= 2 and len(set(seen["es"])) >= 6
    text = " ".join(r.getMessage() for r in caplog.records)
    assert caplog.records
    for needle in ["Secret Sol", TOKEN, *seen["es"], *seen["nonces"]]:
        assert needle not in text


def test_a_present_member_without_a_live_status_is_served_from_the_store(h):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.3))
    lc.take()
    ac2 = h.authed(g, alice)  # replaces ac; ac2 holds no status of its own
    ac2.take()
    h.send(ac2, h.status(alice, 0.2))
    assert ac2.next_of("err")["code"] == "rate"
    again = h.authed(g, leader)
    assert doc(again.next_of("st"))["status"]["hull"] == 0.3


def test_a_replaced_sessions_late_disconnect_does_not_overwrite_a_newer_status(h):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.3))
    old = h.session(ac)
    # the replacement is applied without the old connection's disconnect (it arrives late)
    ac2 = h.open(g)
    hello = ac2.next_of("hello")
    from relay_fakes import auth_frame
    actions = h.relay.receive(h.session(ac2), proto.encode(auth_frame(alice, g, hello["nonce"])))
    h._apply([a for a in actions if h.session is not None and a.session is not old])
    assert stored(h, g) == [A]
    h.clock.advance(30)
    h.send(ac2, h.status(alice, 0.1))
    h.close(ac2)
    h.relay.disconnect(old)  # the late one
    fresh = h.authed(g, leader)
    assert doc(fresh.next_of("st"))["status"]["hull"] == 0.1


class _Boom:
    def __init__(self, store):
        self.store, self.fail = store, True

    def __getattr__(self, name):
        return getattr(self.store, name)

    def save_status(self, *a):
        if self.fail:
            raise RuntimeError("disk full")
        return self.store.save_status(*a)


def test_a_failing_save_in_disconnect_does_not_raise_and_removes_the_session(h, caplog):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.3))
    h.relay.store = _Boom(h.store)
    caplog.set_level(logging.ERROR, "edmrn_relay")
    s = h.session(ac)
    h.close(ac)
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert s.state == "closed" and s not in h.relay.groups[g].sessions
    assert stored(h, g) == []


def test_a_failing_save_in_shutdown_still_closes_everyone_and_disconnect_retries(h, caplog):
    g, _, _, lc, ac = h.two_members()
    h.send(ac, h.status(alice, 0.3))
    boom = h.relay.store = _Boom(h.store)
    caplog.set_level(logging.ERROR, "edmrn_relay")
    actions = h.relay.shutdown()
    assert sorted(a.session.author for a in actions) == sorted([L, A])
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert stored(h, g) == []
    boom.fail = False
    h._apply(actions)  # the closes are applied, each disconnect retries the save
    assert stored(h, g) == [A]


def test_shutdown_skips_sessions_already_closing_and_logs_each_close(h, caplog):
    g, _, _, lc, ac = h.two_members()
    caplog.set_level(logging.INFO, "edmrn_relay")
    h.relay._close(h.session(ac), 4000, "replaced")  # closing, disconnect not yet applied
    caplog.clear()
    actions = h.relay.shutdown()
    assert [a.session for a in actions] == [h.session(lc)]
    lines = [r.getMessage() for r in caplog.records if "close" in r.getMessage()]
    assert lines == [f"close 1001 relay shutting down author={L[:12]}"]
