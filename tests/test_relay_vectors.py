"""The relay's own paths against the shared vectors (ports of vectors.test.ts)."""
import json

import pytest

from edmrn.ext import crew_events as ev
from edmrn_relay.config import RelayConfig
from edmrn_relay.engine import BadSig, Checked, Relay, decode_item, roster_entry
from edmrn_relay.store import LogRow, RelayStore
from relay_fakes import Clock, Harness
from test_crew_vectors import VECTORS, now as vectors_now, raw

v = json.loads(VECTORS.read_text(encoding="utf-8"))


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path / "r.db", token=lambda: bytes(range(32)),
                      clock=Clock(vectors_now(v).timestamp()))
    yield harness
    harness.store.close()


def _auth(h, case):
    c = h.open(v["group_id"])
    c.next_of("hello")
    h.send(c, {"t": "auth", "group": case["group"], "author": case["author"], "sig": case["sig"]})
    return c


def test_auth_vectors(h):
    assert v["auth"]["nonce"] == h.open(v["group_id"]).frames[0]["nonce"]
    assert _auth(h, {**v["auth"]}).closed == (4003, "no group")
    for case in v["auth_negative"]:
        assert _auth(h, case).closed == (4001, "bad signature"), case["name"]


def test_structure_cases_through_decode_item(h):
    for c in v["structure_cases"]:
        r = decode_item({"e": c["e"], "s": "A" * 86})
        expected = c.get("python_valid", c["valid"])
        if c.get("layer") == "roster":
            assert (isinstance(r, Checked) and roster_entry(r, h.clock.now) is not None) is expected, c["name"]
        else:
            assert isinstance(r, Checked) is expected, c["name"]


def test_decode_item_classes():
    good = v["envelopes"]["group"]
    assert decode_item(None) == "undecodable"
    assert decode_item({"e": "A", "s": "A"}) == "undecodable"
    assert decode_item({"e": "A" * 21847, "s": "A" * 86}) == "oversize"
    assert decode_item({"e": good["e"], "s": "A" * 87}) == "oversize"
    env = json.loads(raw(good["e"]))
    assert decode_item({"e": good["e"], "s": "A"}) == BadSig(env["author"], env["seq"])
    assert decode_item({"e": good["e"], "s": "AA"}) == BadSig(env["author"], env["seq"])
    assert isinstance(decode_item({"e": good["e"], "s": good["s"], "x": 1}), Checked)


def test_roster_cases_through_the_relays_store(tmp_path):
    for i, case in enumerate(v["roster_cases"]):
        store = RelayStore(tmp_path / f"c{i}.db")
        try:
            store.db.execute("INSERT INTO groups VALUES (?, ?, 0)", (v["group_id"], case["leader"]))
            for name in case["envelopes"]:
                e = raw(v["envelopes"][name]["e"])
                env = ev.parse_envelope(e)
                store.add_roster_envelope(v["group_id"],
                                          LogRow(env["author"], env["seq"], e, bytes(64)))
            relay = Relay(store, RelayConfig(), now=lambda: vectors_now(v).timestamp())
            roster = relay.group(v["group_id"]).roster
            assert {a: m.last_seq for a, m in roster.members.items()} == case["members"], case["name"]
            assert roster.name == case["name_of_group"], case["name"]
            assert {a: {"commander": m.commander, "leader": m.leader}
                    for a, m in roster.members.items()} == case["member_details"], case["name"]
        finally:
            store.close()


def test_status_vectors(h):
    g, leader, alice, lc, ac = h.two_members()
    bad = v["status_negative"][0]
    h.send(ac, {"t": "st", "e": bad["e"], "s": bad["s"]})
    assert ac.next_of("err")["code"] == "bad_sig"
    h.send(ac, {"t": "st", "e": v["status"]["e"], "s": v["status"]["s"]})
    assert lc.next_of("st") == {"t": "st", "e": v["status"]["e"], "s": v["status"]["s"]}
