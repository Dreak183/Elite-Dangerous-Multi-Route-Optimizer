import hashlib
import re
from datetime import datetime, timezone

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


# --- validators (C12) ------------------------------------------------------------------

ADDR = "10477373803"
NOTE_ID = "01890a5d-ac96-774b-bcce-b302099a8057"


def good(kind):
    return {
        "visit": {"commander": "Jameson", "system": "Sol", "address": ADDR, "coords": [0.0, 0, -1.5]},
        "honk": {"system": "Sol", "address": ADDR, "body_count": 12, "non_body_count": 30},
        "all_bodies": {"system": "Sol", "address": ADDR, "count": 12},
        "body": {"system": "Sol", "address": ADDR, "body_id": 3, "name": "Earth", "tier": "full",
                 "star_type": None, "planet_class": "Earthlike body", "moon": False, "landable": False,
                 "terraform": "", "was_discovered": True, "was_mapped": True, "scan_type": "Detailed"},
        "signals": {"system": "Sol", "address": ADDR, "body_id": 3, "body_name": "Earth", "source": "saa",
                    "counts": {"biological": 2, "human": 1},
                    "genera": ["$Codex_Ent_Bacterial_Genus_Name;"]},
        "find": {"mode": "auto", "find_kind": "codex", "system": "Sol", "address": ADDR,
                 "body_name": "Earth", "body_id": 3,
                 "detail": {"entry_id": 2420101, "name": "$Codex_Ent_Gas_Clds_Yellow_Name;",
                            "category": "$Codex_Category_Biology;",
                            "subcategory": "$Codex_SubCategory_Organic_Structures;", "new_entry": False}},
        "note": {"note_id": NOTE_ID, "system": "Sol", "address": ADDR, "text": "Nice rings", "deleted": False},
    }[kind]


MANUAL_FIND = {"mode": "manual", "find_id": NOTE_ID, "find_kind": "other", "system": None, "address": ADDR,
               "body_name": None, "body_id": None, "note": "Wreck", "deleted": False}


@pytest.mark.parametrize("kind", ["visit", "honk", "all_bodies", "body", "signals", "find", "note"])
def test_good_payloads_pass(kind):
    ev.validate_payload(kind, 1, good(kind))
    assert ev.is_known(kind, 1)


def test_manual_find_passes_and_other_auto_finds_pass():
    ev.validate_payload("find", 1, MANUAL_FIND)
    for find_kind, detail, body_id in [("biological", {"count": 3}, 4),
                                       ("stellar_phenomenon", {"signal": "$Fixed_Event_Life_Cloud;"}, None),
                                       ("valuable_body", {"planet_class": "Water world", "terraform": "Terraformable"}, 4)]:
        payload = dict(good("find"), find_kind=find_kind, detail=detail, body_id=body_id)
        ev.validate_payload("find", 1, payload)


def test_unknown_kind_or_version():
    assert not ev.is_known("visit", 2)
    assert not ev.is_known("chat", 1)
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("visit", 2, good("visit"))


HOSTILE_STRINGS = ["", "a\x00b", "a\nb", "a\x1bb", "a\x7fb", "a\x85b", "a\u202eb", "a\u2066b",
                   "a\ud800b", "x" * 129]


@pytest.mark.parametrize("value", HOSTILE_STRINGS)
def test_hostile_names_are_rejected(value):
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("visit", 1, dict(good("visit"), system=value))


def test_text_limit_is_2000_and_empty_text_is_allowed():
    ev.validate_payload("note", 1, dict(good("note"), text=""))
    ev.validate_payload("note", 1, dict(good("note"), text="x" * 2000))
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("note", 1, dict(good("note"), text="x" * 2001))
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("note", 1, dict(good("note"), text="x" * 10_000))


@pytest.mark.parametrize("kind,key,value", [
    ("honk", "body_count", True),
    ("honk", "body_count", 1.0),
    ("honk", "body_count", -1),
    ("honk", "body_count", 10_001),
    ("honk", "body_count", 2**53 + 1),
    ("visit", "coords", [0, 0, float("nan")]),
    ("visit", "coords", [0, 0, float("inf")]),
    ("visit", "coords", [0, 0]),
    ("visit", "coords", [0, 0, True]),
    ("visit", "coords", [10**400, 0, 0]),
    ("visit", "address", 10477373803),
    ("visit", "address", "-1"),
    ("visit", "address", "1" * 21),
    ("visit", "address", str(2**64)),
    ("visit", "address", "12a"),
    ("visit", "address", "010477373803"),
    ("body", "moon", 0),
    ("body", "tier", "great"),
    ("body", "scan_type", "NavBeacon"),
    ("signals", "source", "eddn"),
    ("signals", "counts", {"biological": True}),
    ("signals", "counts", {"weird": 1}),
    ("signals", "genera", "not a list"),
    ("signals", "genera", ["ok"] * 65),
    ("note", "note_id", "not-a-uuid"),
    ("note", "deleted", "no"),
])
def test_bad_values_are_rejected(kind, key, value):
    with pytest.raises(ev.PayloadError):
        ev.validate_payload(kind, 1, dict(good(kind), **{key: value}))


@pytest.mark.parametrize("kind", ["visit", "honk", "all_bodies", "body", "signals", "find", "note"])
def test_unknown_and_missing_keys_are_rejected(kind):
    with pytest.raises(ev.PayloadError):
        ev.validate_payload(kind, 1, dict(good(kind), extra=1))
    payload = good(kind)
    payload.pop(next(iter(payload)))
    with pytest.raises(ev.PayloadError):
        ev.validate_payload(kind, 1, payload)


def test_address_zero_is_accepted():
    ev.validate_payload("visit", 1, dict(good("visit"), address="0"))


def test_payload_must_be_an_object():
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("visit", 1, ["Sol"])


def test_body_cross_field_rules():
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("body", 1, dict(good("body"), star_type="G"))
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("body", 1, dict(good("body"), planet_class=None))
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("body", 1, dict(good("body"), scan_type="Basic"))
    ev.validate_payload("body", 1, dict(good("body"), scan_type="Basic", tier="basic",
                                        landable=None, terraform=None))


def test_fss_signals_carry_no_genera():
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("signals", 1, dict(good("signals"), source="fss"))


def test_find_rules():
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("find", 1, dict(good("find"), mode="maybe"))
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("find", 1, dict(good("find"), detail={"count": 1}))
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("find", 1, dict(good("find"), body_id=None))
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("find", 1, dict(good("find"), find_kind="other"))
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("find", 1, dict(MANUAL_FIND, detail={}))
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("find", 1, dict(MANUAL_FIND, note="x" * 2001))


# --- natural keys and drafts -----------------------------------------------------------

def test_natural_keys():
    t = "2026-10-12T10:00:00Z"
    assert ev.natural_key("visit", t, good("visit")) == (ADDR, t)
    assert ev.natural_key("honk", t, good("honk")) == (ADDR, t)
    assert ev.natural_key("all_bodies", t, good("all_bodies")) == (ADDR,)
    assert ev.natural_key("body", t, good("body")) == (ADDR, 3, "full")
    assert ev.natural_key("signals", t, good("signals")) == (ADDR, 3, "saa")
    assert ev.natural_key("find", t, good("find")) == (ADDR, "codex", 2420101, 3)
    nsp = dict(good("find"), find_kind="stellar_phenomenon", body_id=None,
               detail={"signal": "$Fixed_Event_Life_Cloud;"})
    assert ev.natural_key("find", t, nsp) == (ADDR, "stellar_phenomenon")
    bio = dict(good("find"), find_kind="biological", detail={"count": 2})
    assert ev.natural_key("find", t, bio) == (ADDR, "biological", 3)
    assert ev.natural_key("find", t, MANUAL_FIND) is None
    assert ev.natural_key("note", t, good("note")) is None


def test_draft_resolves_a_deterministic_or_preset_id():
    t = "2026-10-12T10:00:00Z"
    d = ev.Draft("all_bodies", "F1", t, good("all_bodies"))
    assert d.resolve_id(AUTHOR) == ev.deterministic_id(AUTHOR, "all_bodies", ADDR)
    assert d.resolve_id(AUTHOR) != d.resolve_id("B" * 43)
    manual = ev.Draft("note", "F1", t, good("note"), event_id=NOTE_ID)
    assert manual.resolve_id(AUTHOR) == NOTE_ID
    with pytest.raises(ev.PayloadError):
        ev.Draft("note", "F1", t, good("note")).resolve_id(AUTHOR)


# --- Task 1 (B1 S1, S8, S10-S12): whitelist, roster and status validators -----------------

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
UUID = "01920000-0000-7000-8000-000000000001"
UUID2 = "01920000-0000-7000-8000-000000000002"
HEX64 = "ab" * 32


@pytest.mark.parametrize("bad", [
    "a‎b", "a﻿b", "a؜b", "a b", "a b", "a­b", "a\U000e0001b",
    "a\x00b", "a͸b", "a\ud800b", "a\nb", "a‮b", "a\u0085b", "ab",
])
def test_whitelist_refuses_every_excluded_category(bad):
    assert not ev.text_allowed(bad)
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("note", 1, {"note_id": UUID, "system": None, "address": "1",
                                        "text": bad, "deleted": False})


@pytest.mark.parametrize("good", ["Ünïcödé", "Σ Sagittarii", "日本", "🚀 Mk II", "é", "a b",
                                  "x-y_z.(1)", "<b>x</b>", "<script>alert(1)</script>"])
def test_whitelist_accepts_letters_marks_numbers_punctuation_symbols_spaces(good):
    # Markup is inert data here: the UI shows it as text, never as markup (spec S2).
    assert ev.text_allowed(good)


def test_a_10000_character_text_is_refused_by_the_length_limit():
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("note", 1, {"note_id": UUID, "system": None, "address": "1",
                                        "text": "x" * 10000, "deleted": False})
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("group", 1, group(name="x" * 10000))


def test_payload_error_message_is_cut_to_200_characters():
    with pytest.raises(ev.PayloadError) as info:
        ev.validate_payload("visit", 1, {"x" * 5000: 1})
    assert len(str(info.value)) <= 200


def group(**over):
    p = {"group_id": UUID, "name": "Deep Space Crew", "commander": "Jameson"}
    p.update(over)
    return p


def invite(**over):
    p = {"group_id": UUID, "invite_id": UUID2, "token_hash": HEX64,
         "expires_at": "2026-10-14T12:00:00Z", "label": "Alice"}
    p.update(over)
    return p


def join(**over):
    p = {"group_id": UUID, "invite_id": UUID2, "token": HEX64, "commander": "Alice"}
    p.update(over)
    return p


def remove(**over):
    p = {"group_id": UUID, "member": AUTHOR, "last_seq": 7}
    p.update(over)
    return p


@pytest.mark.parametrize("kind,payload", [
    ("group", group()), ("invite", invite()), ("invite", invite(label=None)), ("join", join()),
    ("remove", remove()), ("remove", remove(last_seq=0)),
])
def test_roster_payloads_accept_the_spec_shapes(kind, payload):
    assert ev.is_known(kind, 1)
    ev.validate_payload(kind, 1, payload)


@pytest.mark.parametrize("kind,payload", [
    ("group", group(name="Ünïcode")),
    ("group", group(name="x" * 65)),
    ("group", group(name="")),
    ("group", group(commander="tab\there")),
    ("invite", invite(token_hash="AB" * 32)),
    ("invite", invite(expires_at="2026-02-30T00:00:00Z")),
    ("invite", invite(label="")),
    ("join", join(token="ab" * 31)),
    ("join", join(group_id="not-a-uuid")),
    ("remove", remove(member="short")),
    ("remove", remove(last_seq=-1)),
    ("remove", remove(last_seq=True)),
    ("group", dict(group(), extra=1)),
])
def test_roster_payloads_refuse_hostile_values(kind, payload):
    with pytest.raises(ev.PayloadError):
        ev.validate_payload(kind, 1, payload)


@pytest.mark.parametrize("stamp,ok", [
    ("2026-10-13T12:00:00Z", True),
    ("2014-12-16T00:00:00Z", True),
    ("2014-12-15T23:59:59Z", False),
    ("2026-10-14T12:00:00Z", True),
    ("2026-10-14T12:00:01Z", False),
    ("2026-02-30T00:00:00Z", False),
    ("0000-01-01T00:00:00Z", False),
    ("9999-12-31T23:59:59Z", False),
    ("2026-10-13T12:00:60Z", False),
    ("2026-10-13 12:00:00", False),
])
def test_plausible_received(stamp, ok):
    assert ev.plausible_received(stamp, NOW) is ok


def test_ascii_name_replaces_and_cuts():
    assert ev.ascii_name("Jämeson") == "J?meson"
    assert ev.ascii_name("x" * 100) == "x" * 64
    assert ev.ascii_name("   ") == "CMDR"
    assert ev.is_roster_name(ev.ascii_name("日本"))


def status(**over):
    s = {name: None for name in ev.STATUS_FIELDS}
    s["paused"] = False
    s.update(over)
    return {"author": AUTHOR, "at": "2026-10-13T12:00:00Z", "status": s}


def test_status_accepts_a_full_and_an_empty_status():
    ev.validate_status(status())
    ev.validate_status(status(commander="Jameson", system="Sol", address="10477373803",
                              coords=[0.0, 0.0, 0.0], ship="anaconda", ship_name="Mule",
                              ship_ident="MU-01", edsy_url="https://edsy.org/#/I=H4sIAAAA%3D",
                              hull=0.87, fuel_main=12.5, fuel_reserve=0.5, fuel_capacity_main=32,
                              fuel_capacity_reserve=0.63))


@pytest.mark.parametrize("over", [
    {"hull": 1.5}, {"hull": True}, {"fuel_main": float("nan")}, {"fuel_main": 1e6},
    {"edsy_url": "https://evil.example/#/I=x"}, {"edsy_url": "https://edsy.org/#/I=" + "a" * 6000},
    {"paused": None}, {"commander": "a‮b"}, {"address": "007"},
])
def test_status_refuses_hostile_values(over):
    with pytest.raises(ev.PayloadError):
        ev.validate_status(status(**over))


def test_status_refuses_unknown_keys():
    doc = status()
    doc["status"]["extra"] = 1
    with pytest.raises(ev.PayloadError):
        ev.validate_status(doc)


def test_load_json_refuses_duplicates_bom_nan_and_size():
    for data in (b'{"a":1,"a":2}', b'\xef\xbb\xbf{}', b'{"a":NaN}', b"x" * 20):
        with pytest.raises(ev.EnvelopeError):
            ev.load_json(data, 10 if data == b"x" * 20 else 1000, "thing")
    assert ev.load_json(b'{"a":1}', 100, "thing") == {"a": 1}
