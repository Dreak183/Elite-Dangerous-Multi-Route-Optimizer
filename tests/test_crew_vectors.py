import base64
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from edmrn.ext import crew_events as ev
from edmrn.ext import crew_protocol as proto
from edmrn.ext.crew_identity import verify
from edmrn.ext.crew_roster import compute_roster, decode_invite, parse_roster_envelope

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "crew-relay" / "test" / "vectors.json"


def _generator():
    spec = importlib.util.spec_from_file_location("make_crew_vectors", ROOT / "tests" / "vectors" / "make_crew_vectors.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def v():
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def raw(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def now(v):
    return datetime.strptime(v["now"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def test_committed_vectors_match_a_fresh_generation():
    assert VECTORS.read_text(encoding="utf-8") == _generator().render()


def test_structure_cases(v):
    for case in v["structure_cases"]:
        if case.get("layer") == "roster":
            ok = parse_roster_envelope(raw(case["e"]), frozenset(), now(v)) is not None
        else:
            try:
                ev.parse_envelope(raw(case["e"]))
                ok = True
            except ev.EnvelopeError:
                ok = False
        assert ok is case.get("python_valid", case["valid"]), case["name"]


def test_signature_cases(v):
    for case in v["signature_cases"]:
        assert verify(case["author"], raw(case["s"]), raw(case["e"])) is case["valid"], case["name"]


def test_small_order_cases_pass_plain_verification(v):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    cases = [c for c in v["signature_cases"] if c["name"].startswith("small_order_")]
    assert len(cases) == 15
    for case in cases:
        Ed25519PublicKey.from_public_bytes(raw(case["author"])).verify(raw(case["s"]), raw(case["e"]))


def test_canonical_cases(v):
    for case in v["canonical_cases"]:
        parsed = parse_roster_envelope(raw(case["e"]), frozenset(), now(v))
        assert (parsed is not None) is case["canonical"], case["name"]


def test_roster_payload_cases(v):
    for case in v["roster_payload_cases"]:
        try:
            ev.validate_payload(case["kind"], 1, case["payload"])
            ok = True
        except ev.PayloadError:
            ok = False
        assert ok is case["valid"], case


def test_plausibility_cases(v):
    for case in v["plausibility_cases"]:
        assert ev.plausible_received(case["created_at"], now(v)) is case["plausible"], case


def test_roster_cases(v):
    for case in v["roster_cases"]:
        entries = [parse_roster_envelope(raw(v["envelopes"][n]["e"]), frozenset(), now(v))
                   for n in case["envelopes"]]
        roster = compute_roster([e for e in entries if e is not None], v["group_id"], case["leader"])
        assert {a: m.last_seq for a, m in roster.members.items()} == case["members"], case["name"]
        assert roster.name == case["name_of_group"], case["name"]
        assert {a: {"commander": m.commander, "leader": m.leader} for a, m in roster.members.items()}             == case["member_details"], case["name"]


def test_auth_and_status(v):
    a = v["auth"]
    assert verify(a["author"], raw(a["sig"]), proto.auth_message(a["group"], a["nonce"]))
    author, _, status = proto.parse_status({"t": "st", "e": v["status"]["e"], "s": v["status"]["s"]})
    assert author == v["status"]["author"] and status["hull"] == 0.5


def test_invite_decodes_to_the_vector_code(v):
    code = decode_invite(v["invite"]["text"])
    assert {"url": code.url, "group_id": code.group_id, "invite_id": code.invite_id,
            "token": code.token, "leader": code.leader} == v["invite"]["code"]
    assert decode_invite(v["invite"]["link"]) == code


def test_whitelist_refusals(v):
    assert v["whitelist_refusals"]
    for text in v["whitelist_refusals"]:
        assert ev.text_allowed(text) is False, ascii(text)
    assert ev.text_allowed("Sol Über-Welt (★)")


def test_auth_and_status_negatives(v):
    for case in v["auth_negative"]:
        assert not verify(case["author"], raw(case["sig"]), proto.auth_message(case["group"], case["nonce"])), case["name"]
    for case in v["status_negative"]:
        with pytest.raises(proto.FrameError):
            proto.parse_status({"t": "st", "e": case["e"], "s": case["s"]})
