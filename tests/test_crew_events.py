import hashlib
import re

import pytest

from edmrn.ext import crew_events as ev

AUTHOR = "A" * 43
UUID7_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"


def envelope(**over):
    env = ev.build_envelope("0" * 32, AUTHOR, 1, "visit", "2026-10-12T10:00:00Z", {"a": 1})
    env.update(over)
    return env


def raw(env):
    return ev.canonical_bytes(env)


# --- canonical bytes and ids ----------------------------------------------------------

def test_canonical_bytes_are_sorted_compact_utf8():
    assert ev.canonical_bytes({"b": 1, "a": "Ä"}) == '{"a":"Ä","b":1}'.encode("utf-8")


def test_canonical_bytes_reject_nan():
    with pytest.raises(ValueError):
        ev.canonical_bytes({"x": float("nan")})


def test_deterministic_id_is_the_hash_of_the_canonical_array():
    expected = hashlib.sha256(
        ('["' + AUTHOR + '","body","123",5,"full"]').encode("utf-8")).hexdigest()[:32]
    assert ev.deterministic_id(AUTHOR, "body", "123", 5, "full") == expected


def test_deterministic_id_separates_kinds_authors_and_key_boundaries():
    base = ev.deterministic_id(AUTHOR, "body", "123", 5, "full")
    assert re.fullmatch(r"[0-9a-f]{32}", base)
    assert base != ev.deterministic_id(AUTHOR, "signals", "123", 5, "full")
    assert base != ev.deterministic_id("B" * 43, "body", "123", 5, "full")
    assert base != ev.deterministic_id(AUTHOR, "body", "1235", "full")


def test_uuid7_shape_order_and_uniqueness():
    ids = [ev.uuid7() for _ in range(3000)]
    assert all(re.fullmatch(UUID7_RE, i) for i in ids)
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert all(ev.is_uuid7(i) for i in ids[:10])
    assert not ev.is_uuid7("0" * 32)


def test_uuid7_encodes_the_millisecond_and_stays_monotonic_when_the_clock_steps_back():
    gen = ev.Uuid7Generator()
    first = gen.next(now_ms=1_700_000_000_000)
    assert int(first.replace("-", "")[:12], 16) == 1_700_000_000_000
    earlier = gen.next(now_ms=1_600_000_000_000)
    assert earlier > first


def test_uuid7_counter_overflow_moves_to_the_next_millisecond():
    gen = ev.Uuid7Generator()
    ids = [gen.next(now_ms=5000) for _ in range(5000)]
    assert ids == sorted(ids) and len(set(ids)) == 5000


def test_stamp_accepts_only_the_exact_shape():
    assert ev.stamp("2026-10-12T10:00:00Z") == "2026-10-12T10:00:00Z"
    for bad in ("2026-10-12 10:00:00", "2026-10-12T10:00:00.123Z", "2026-10-12T10:00:00Z\n", 5, None,
                "２０２６-10-12T10:00:00Z", "٢٠٢٦-10-12T10:00:00Z"):
        assert ev.stamp(bad) is None
    assert ev.stamp(ev.utc_now_stamp())


# --- envelopes -------------------------------------------------------------------------

def test_seal_and_parse_round_trip():
    data, sig = ev.seal(envelope(), lambda b: b"s" * 64)
    assert sig == b"s" * 64
    assert data == raw(envelope())
    assert ev.parse_envelope(data) == envelope()


def test_seal_rejects_an_envelope_over_16_kb():
    with pytest.raises(ev.EnvelopeError, match="16 KB"):
        ev.seal(envelope(payload={"text": "x" * 17_000}), lambda b: b"s" * 64)


def test_seal_checks_the_structure():
    with pytest.raises(ev.EnvelopeError):
        ev.seal(envelope(seq=0), lambda b: b"s" * 64)


@pytest.mark.parametrize("data", [
    b"x" * 16_385,
    b'{"v":1,"v":1}',
    b"[" * 5000 + b"]" * 5000,
    b"\xff\xfe",
    b"not json",
    b"[]",
    "a str, not bytes",
])
def test_parse_rejects_malformed_bytes(data):
    with pytest.raises(ev.EnvelopeError):
        ev.parse_envelope(data)


def test_parse_rejects_non_finite_numbers():
    data = raw(envelope()).replace(b'{"a":1}', b'{"a":NaN}')
    with pytest.raises(ev.EnvelopeError):
        ev.parse_envelope(data)


@pytest.mark.parametrize("over", [
    {"v": True}, {"v": 0}, {"seq": 0}, {"seq": True}, {"seq": 2**53}, {"seq": "1"},
    {"author": "A" * 42}, {"author": "A" * 42 + "="}, {"author": "A" * 42 + "+"},
    {"event_id": "0" * 31}, {"event_id": "G" * 32}, {"event_id": "0" * 32 + "\n"},
    {"kind": "Visit"}, {"kind": ""}, {"kind": "a" * 33},
    {"created_at": "2026-10-12 10:00:00"}, {"payload": []}, {"payload": None},
])
def test_parse_rejects_bad_fields(over):
    with pytest.raises(ev.EnvelopeError):
        ev.parse_envelope(raw(envelope(**over)))


def test_parse_rejects_missing_and_extra_keys():
    env = envelope()
    del env["kind"]
    with pytest.raises(ev.EnvelopeError):
        ev.parse_envelope(raw(env))
    with pytest.raises(ev.EnvelopeError):
        ev.parse_envelope(raw(envelope(extra=1)))


def test_parse_accepts_a_uuid7_event_id():
    env = envelope(event_id=ev.uuid7())
    assert ev.parse_envelope(raw(env))["event_id"] == env["event_id"]
