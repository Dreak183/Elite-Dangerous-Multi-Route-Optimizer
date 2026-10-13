from datetime import datetime, timezone

import pytest

from edmrn.ext import crew_events as ev
from edmrn.ext import crew_roster as cr
from edmrn.ext.crew_identity import KeyRing

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
G = "01920000-0000-7000-8000-0000000000a1"
INV = "01920000-0000-7000-8000-0000000000b1"
INV2 = "01920000-0000-7000-8000-0000000000b2"
TOKEN = "11" * 32
LEADER, ALICE, BOB = "L" * 43, "A" * 43, "B" * 43


def rv(author, seq, kind, at, **payload):
    payload.setdefault("group_id", G)
    return cr.RosterEnvelope(author, seq, kind, at, payload)


def base():
    return [
        rv(LEADER, 1, "group", "2026-10-13T10:00:00Z", name="Crew", commander="Leader"),
        rv(LEADER, 2, "invite", "2026-10-13T10:01:00Z", invite_id=INV, token_hash=cr.token_hash(TOKEN),
           expires_at="2026-10-14T10:00:00Z", label="Alice"),
    ]


def test_invite_admits_the_first_valid_join():
    roster = cr.compute_roster(base() + [
        rv(BOB, 1, "join", "2026-10-13T10:03:00Z", invite_id=INV, token=TOKEN, commander="Bob"),
        rv(ALICE, 4, "join", "2026-10-13T10:02:00Z", invite_id=INV, token=TOKEN, commander="Alice"),
    ], G, LEADER)
    assert set(roster.members) == {LEADER, ALICE}
    assert roster.invites[INV].redeemed_by == ALICE
    assert roster.name == "Crew" and roster.members[LEADER].commander == "Leader"


def test_a_wrong_token_does_not_block_a_later_valid_join():
    roster = cr.compute_roster(base() + [
        rv(BOB, 1, "join", "2026-10-13T10:02:00Z", invite_id=INV, token="22" * 32, commander="Bob"),
        rv(ALICE, 1, "join", "2026-10-13T10:03:00Z", invite_id=INV, token=TOKEN, commander="Alice"),
    ], G, LEADER)
    assert set(roster.members) == {LEADER, ALICE}


def test_expired_unknown_foreign_and_other_group_joins_do_not_admit():
    roster = cr.compute_roster(base() + [
        rv(ALICE, 1, "join", "2026-10-14T10:00:01Z", invite_id=INV, token=TOKEN, commander="Late"),
        rv(BOB, 1, "join", "2026-10-13T10:02:00Z", invite_id=INV2, token=TOKEN, commander="NoInvite"),
        rv(ALICE, 2, "invite", "2026-10-13T10:01:00Z", invite_id=INV2, token_hash=cr.token_hash(TOKEN),
           expires_at="2026-10-14T10:00:00Z", label=None),
        rv(BOB, 2, "join", "2026-10-13T10:02:00Z", group_id="01920000-0000-7000-8000-0000000000ff",
           invite_id=INV, token=TOKEN, commander="Other"),
    ], G, LEADER)
    assert set(roster.members) == {LEADER}


def test_remove_caps_at_the_lowest_last_seq_and_never_removes_the_leader():
    roster = cr.compute_roster(base() + [
        rv(ALICE, 1, "join", "2026-10-13T10:02:00Z", invite_id=INV, token=TOKEN, commander="Alice"),
        rv(LEADER, 3, "remove", "2026-10-13T11:00:00Z", member=ALICE, last_seq=5),
        rv(LEADER, 4, "remove", "2026-10-13T11:01:00Z", member=ALICE, last_seq=3),
        rv(LEADER, 5, "remove", "2026-10-13T11:02:00Z", member=LEADER, last_seq=0),
        rv(BOB, 1, "remove", "2026-10-13T11:03:00Z", member=LEADER, last_seq=0),
    ], G, LEADER)
    assert roster.members[ALICE].last_seq == 3
    assert roster.members[LEADER].last_seq is None
    assert roster.admits(ALICE, 3) and not roster.admits(ALICE, 4)
    assert [m.author for m in roster.active()] == [LEADER]


def test_membership_is_order_independent():
    items = base() + [rv(ALICE, 1, "join", "2026-10-13T10:02:00Z", invite_id=INV, token=TOKEN,
                         commander="Alice")]
    assert cr.compute_roster(items, G, LEADER) == cr.compute_roster(list(reversed(items)), G, LEADER)


def test_a_non_leader_group_envelope_does_not_rename_the_group():
    roster = cr.compute_roster(base() + [rv(BOB, 1, "group", "2014-12-16T00:00:00Z", name="Pwned",
                                            commander="Bob")], G, LEADER)
    assert roster.name == "Crew"


def seal_roster(identity, seq, kind, at, payload, event_id=None):
    env = ev.build_envelope(event_id or ev.uuid7(), identity.author, seq, kind, at, payload)
    return ev.seal(env, identity.sign)[0]


def test_parse_roster_envelope_filters(tmp_path):
    me = KeyRing(tmp_path / "k").for_commander("C1")
    good = seal_roster(me, 1, "group", "2026-10-13T10:00:00Z", {"group_id": G, "name": "Crew", "commander": "C"})
    assert cr.parse_roster_envelope(good, frozenset(), NOW).kind == "group"
    future = seal_roster(me, 2, "group", "2026-10-15T10:00:00Z", {"group_id": G, "name": "Crew", "commander": "C"})
    assert cr.parse_roster_envelope(future, frozenset(), NOW) is None
    assert cr.parse_roster_envelope(future, frozenset({me.author}), NOW) is not None
    hex_id = seal_roster(me, 3, "group", "2026-10-13T10:00:00Z",
                         {"group_id": G, "name": "Crew", "commander": "C"}, event_id="ab" * 16)
    assert cr.parse_roster_envelope(hex_id, frozenset(), NOW) is None
    spaced = good.replace(b'","', b'", "', 1)
    assert cr.parse_roster_envelope(spaced, frozenset(), NOW) is None
    visit = seal_roster(me, 4, "visit", "2026-10-13T10:00:00Z", {"commander": "C", "system": "Sol",
                                                                 "address": "1", "coords": None})
    assert cr.parse_roster_envelope(visit, frozenset(), NOW) is None


CODE = cr.InviteCode("wss://galroute.the-hub.cloud", G, INV, TOKEN, LEADER)


def test_invite_round_trip_and_link():
    text = cr.encode_invite(CODE)
    assert text.startswith("edmrn-crew:1:")
    assert cr.decode_invite(text) == CODE
    link = cr.invite_link(text, CODE.url)
    assert link == f"https://galroute.the-hub.cloud/join#{text}"
    assert cr.decode_invite(link) == CODE


@pytest.mark.parametrize("wrap", ["  {}\n", "<{}>", "`{}`", "```{}```", "<https://galroute.the-hub.cloud/join#{}>"])
def test_invite_survives_discord_decoration(wrap):
    assert cr.decode_invite(wrap.format(cr.encode_invite(CODE))) == CODE


@pytest.mark.parametrize("bad", [
    "", "hello", "edmrn-crew:2:abc", "edmrn-crew:1:!!!", "edmrn-crew:1:" + "A" * 3000,
    "edmrn-crew:1:e30",  # {}
    "edmrn-crew:1:<b>x</b>", "edmrn-crew:1:<script>alert(1)</script>", "<script>", "<b>x</b>",
    "edmrn-crew:1:" + "A" * 10000, "x" * 10000,
])
def test_invite_refuses_garbage_with_one_sentence(bad):
    with pytest.raises(cr.InviteError) as info:
        cr.decode_invite(bad)
    assert str(info.value).endswith(".")


@pytest.mark.parametrize("url", [
    "https://galroute.the-hub.cloud", "ws://galroute.the-hub.cloud", "wss://user@host",
    "wss://host/path", "wss://host?x=1", "wss://host#f", "wss://", "ftp://host",
])
def test_relay_url_refuses(url):
    with pytest.raises(cr.InviteError):
        cr.check_relay_url(url)


def test_relay_url_accepts_and_normalises():
    assert cr.check_relay_url("wss://Galroute.The-Hub.Cloud/") == "wss://galroute.the-hub.cloud"
    assert cr.check_relay_url("ws://localhost:8799") == "ws://localhost:8799"
    assert cr.check_relay_url("ws://127.0.0.1:8799") == "ws://127.0.0.1:8799"
    assert cr.connection_url("wss://h", G) == f"wss://h/v1/groups/{G}/ws"


@pytest.mark.parametrize("url", [
    "https://discord.com/api/webhooks/123/abc_DEF-9", "https://discordapp.com/api/webhooks/1/x",
    "https://ptb.discord.com/api/webhooks/1/x", "https://canary.discord.com/api/webhooks/1/x",
])
def test_webhook_accepts(url):
    assert cr.check_webhook(url) == url
    assert "***" in cr.redact_webhook(url) and url.rsplit("/", 1)[1] not in cr.redact_webhook(url)


@pytest.mark.parametrize("url", [
    "http://discord.com/api/webhooks/1/x", "https://discord.com.evil/api/webhooks/1/x",
    "https://discord.com/api/webhooks/1/x/extra", "https://evil.com/api/webhooks/1/x",
    "https://discord.com/api/webhooks/abc/x",
])
def test_webhook_refuses(url):
    with pytest.raises(cr.InviteError):
        cr.check_webhook(url)


def test_group_json_round_trip_and_refusals(tmp_path):
    assert cr.load_group(tmp_path) is None
    cfg = cr.GroupConfig("wss://galroute.the-hub.cloud", G, LEADER, LEADER,
                         "https://discord.com/api/webhooks/1/x", True)
    cr.save_group(tmp_path, cfg)
    assert cr.load_group(tmp_path) == cfg and cfg.is_leader
    (tmp_path / "group.json").write_text('{"v": true}', encoding="utf-8")
    with pytest.raises(cr.GroupConfigError):
        cr.load_group(tmp_path)
    cr.delete_group(tmp_path)
    assert cr.load_group(tmp_path) is None
