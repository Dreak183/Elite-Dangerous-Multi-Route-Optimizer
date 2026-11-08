"""The relay's own paths against the shared vectors (ports of vectors.test.ts)."""
import json

import pytest

from edmrn_relay.engine import BadSig, Checked, decode_item, roster_entry
from relay_fakes import Clock, Harness, auth_frame
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
    assert isinstance(decode_item({"e": good["e"], "s": good["s"], "x": 1}), Checked)
