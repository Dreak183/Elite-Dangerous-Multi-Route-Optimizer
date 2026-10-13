# Crew Sync Core (Spec B1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Friends exchange Spec A's signed crew log and live status through a Cloudflare Worker relay
at `galroute.the-hub.cloud`, with a leader-signed roster, invites as text, link or Discord webhook
post, and a minimal crew dialog, inside Cloudflare's Free plan.

**Architecture:** The Python client gains a pure roster and protocol layer, a gated projector in
`crew_store`, a WebSocket thread that never writes the database, and a Tk-loop inbox that stores
received envelopes in batches. The relay is a TypeScript Worker with one SQLite-backed Durable Object
per group, using hibernatable WebSockets. It stores signed envelopes, keeps per-author heads and
daily quotas, and computes membership with the same rules as the client. Shared test vectors bind
the two implementations.

**Tech Stack:** Python 3.13, `cryptography` (Ed25519), `websockets` (sync client, BSD-3), pytest;
TypeScript, Cloudflare Workers + Durable Objects (SQLite backend), Wrangler,
Vitest with `@cloudflare/vitest-pool-workers`.

**Spec:** `docs/superpowers/specs/2026-10-13-crew-sync-core-design.md` (S1–S21). ADRs:
0005, 0006, 0007, 0008. Spec A: `docs/superpowers/specs/2026-10-12-crew-capture-design.md`.

## Global Constraints

- Upstream files get hook points only (ADR-0002): this plan touches exactly one upstream file,
  `edmrn/settings_manager.py`, with two lines. Upstream files are CRLF; edit them with the Edit tool,
  never `sed -i`, and check with `file`.
- Run Python with `.venv/Scripts/python.exe`; tests: `.venv/Scripts/python.exe -m pytest`.
- Tests never touch real folders or the network: `tests/conftest.py` redirects
  `HOME`/`USERPROFILE`/`APPDATA`/`LOCALAPPDATA` and makes sockets raise. Only the opt-in end-to-end
  test (Task 15) lifts the socket block, and only for itself.
- Every external HTTP request goes through `edmrn/ext/api.py` (`docs/architecture/patterns.md`).
  The crew WebSocket is the one non-HTTP network path, in `edmrn/ext/crew_sync.py` only.
- Code, comments, docs, commit messages and UI text are English.
- Stage explicit paths only. Never `git add -A`, `.` or `-u`.
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa
  ```
- New Python dependency: `websockets` (BSD-3) in `requirements-edmrn.txt`. New Node dev dependencies
  live only in `crew-relay/package.json` with exact versions.
- Numbers from the spec, verbatim:
  - frame ≤ 1 MiB;
  - `evs` ≤ 100 items and ≤ 512 KiB;
  - envelope ≤ 16 KB (16,384 bytes);
  - status ≤ 8 KiB;
  - EDSY link ≤ 6,000 characters;
  - status at most every 30 s;
  - client gathers own envelopes for at most 5 s;
  - backoff from 1 s doubling to 5 min, ±20 % jitter, 60 s after a 4000 close;
  - member quota 5,000 per UTC day, group quota 80,000 per UTC day, roster envelopes exempt;
  - invite expiry 1 h, 24 h (default) or 7 days;
  - roster names 1–64 printable ASCII;
  - received `created_at` not before `2014-12-16T00:00:00Z`, not more than 24 h ahead;
  - `PayloadError` messages ≤ 200 characters.
- Close codes: 4000 replaced, 4001 bad auth, 4003 not a member or removed, 4008 policy.
- Error codes: `quota`, `group_quota`, `not_member`, `removed`, `bad_sig`, `too_big`,
  `bad_frame`, `rate`.
- Default relay address: `wss://galroute.the-hub.cloud`.

## Review Focus

The five conditions most likely to bite a commander that no requirement's own test covers. Each line
names the expected behaviour and the task whose tests pin it.

1. **Two installations with the same commander key online at once** (desktop and laptop). The relay
   replaces the older connection with 4000. The replaced client must wait 60 s, not flap every
   second. Pinned in Task 6.
2. **The commander switches to an alt commander that is not the group member.** No status of the
   alt is ever sent; sync keeps running under the member identity. Pinned in Task 7.
3. **No internet at EDMRN start.** EDMRN starts normally; the connection fails in the background
   with backoff; nothing raises into Tk. Pinned in Task 9.
4. **A large first upload** (thousands of own envelopes after joining). Reads are paged by 100,
   frames stay ≤ 512 KiB, and envelopes go out per author in ascending `seq`. Pinned in Task 6.
5. **An invite pasted from Discord with decoration**: surrounding whitespace, `<…>` (Discord's
   embed suppression), backticks, or the whole link. All of these are accepted; anything else is
   refused with one sentence. Pinned in Task 2.

## File structure

| File | Responsibility |
|---|---|
| `edmrn/ext/crew_events.py` (modify) | S10 category whitelist; S12 message cut; roster and status validators; timestamp plausibility; `load_json` |
| `edmrn/ext/crew_roster.py` (create) | Membership (S2), roster envelope filter, invite string and link (S5), webhook URL check (S5a), `group.json` (S4) |
| `edmrn/ext/crew_identity.py` (modify) | `KeyRing.own_authors()`, `KeyRing.identity_for_author()` |
| `edmrn/ext/store.py` (modify) | Migration 3: `crew_log_kind` index, `crew_meta` with the rebuild marker |
| `edmrn/ext/crew_store.py` (modify) | Projection gate (S3), own-first system merge and plausibility (S11), batch insert, equivocation and rebuild summary (S12), `heads`, `envelopes_after`, `own_roster_envelope`, `rebuild_if_marked` |
| `edmrn/ext/crew_protocol.py` (create) | Frame build and parse, base64url, auth bytes, status bytes (S6, S8) |
| `edmrn/ext/crew_sync.py` (create) | `SocketClosed`, websockets adapter, `CrewBoard`, `Inbox`, `CrewSync` session, backoff, status sending, pause (S7–S9, S19) |
| `edmrn/ext/api.py` (modify) | `call(..., log_url=None)`: a redacted URL for log lines |
| `edmrn/ext/crew_discord.py` (create) | Invite message and webhook post (S5a) |
| `edmrn/ext/crew_group.py` (create) | Controller: create, join, invite, remove, leave, pause, webhook; status source; starts sync |
| `edmrn/ext/crew.py` (modify) | `CrewService.group`; `install` starts the group controller and the marked rebuild |
| `edmrn/ext/crew_dialog.py` (create) | Settings card and the Tk dialog (S20) |
| `edmrn/settings_manager.py` (modify, upstream) | Two-line hook: the crew settings card |
| `tests/vectors/make_crew_vectors.py` (create) | Deterministic generator of `crew-relay/test/vectors.json` |
| `crew-relay/` (create) | Worker: `src/index.ts`, `src/join.ts`, `src/b64.ts`, `src/canonical.ts`, `src/envelope.ts`, `src/roster.ts`, `src/group.ts`; `test/*.test.ts`; `wrangler.toml`, `package.json`, `tsconfig.json`, `vitest.config.ts` |
| `tests/test_crew_*.py` (create or extend), `tests/crew_sync_fakes.py` (create) | Tests |
| Docs (Task 15) | `docs/architecture/overview.md`, `patterns.md`, `docs/how-to/run.md`, `docs/how-to/deploy-relay.md` (create), `CLAUDE.md`, `../knowledge/group-sync-transport.md`, `docs/handover/HANDOVER.md` |

Task order: 1–5 pure Python layers, 6–10 client runtime and UI, 11–14 relay, 15 end to end and
docs. Tasks 11–14 depend only on Task 5's vectors and the spec.

---

### Task 1: Validator hardening, roster and status validators

**Files:**
- Modify: `edmrn/ext/crew_events.py`
- Test: `tests/test_crew_events.py` (extend)

**Interfaces:**
- Consumes: nothing new.
- Produces (all in `edmrn.ext.crew_events`):
  - `PayloadError(message)` cuts its message to 200 characters.
  - `text_allowed(value: str) -> bool`.
  - `ROSTER_KINDS = ('group', 'invite', 'join', 'remove')`, `ROSTER_NAME_MAX = 64`.
  - `is_author(value) -> bool`, `is_hex64(value) -> bool`, `is_roster_name(value) -> bool`.
  - `parse_stamp(value) -> Optional[datetime]`: aware UTC, `None` if the shape or the date is wrong.
  - `FLOOR: datetime` (2014-12-16 UTC), `plausible_received(created_at: str, now: datetime) -> bool`.
  - `ascii_name(text: str) -> str`.
  - `STATUS_FIELDS: tuple[str, ...]`, `validate_status(doc: Any) -> None` (raises `PayloadError`).
  - `load_json(data: bytes, limit: int, what: str) -> Any` (raises `EnvelopeError`).
  - `validate_payload` and `is_known` accept the four roster kinds at `v` 1.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_crew_events.py`)

```python
from datetime import datetime, timezone

import pytest

from edmrn.ext import crew_events as ev

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
AUTHOR = "A" * 43
UUID = "01920000-0000-7000-8000-000000000001"
UUID2 = "01920000-0000-7000-8000-000000000002"
HEX64 = "ab" * 32


@pytest.mark.parametrize("bad", [
    "a‎b", "a﻿b", "a؜b", "a b", "a b", "ab", "a\U000e0001b",
    "a\x00b", "a͸b", "a\ud800b", "a\nb", "a‮b",
])
def test_whitelist_refuses_every_excluded_category(bad):
    assert not ev.text_allowed(bad)
    with pytest.raises(ev.PayloadError):
        ev.validate_payload("note", 1, {"note_id": UUID, "system": None, "address": "1",
                                        "text": bad, "deleted": False})


@pytest.mark.parametrize("good", ["Ünïcödé", "Σ Sagittarii", "日本", "🚀 Mk II", "é", "a b", "x-y_z.(1)"])
def test_whitelist_accepts_letters_marks_numbers_punctuation_symbols_spaces(good):
    assert ev.text_allowed(good)


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
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_events.py -q`
Expected: failures with `AttributeError: module 'edmrn.ext.crew_events' has no attribute 'text_allowed'`
and similar.

- [ ] **Step 3: Implement**

In `edmrn/ext/crew_events.py`:

1. Add the imports `import unicodedata` and `from datetime import timedelta`.

2. Replace `PayloadError` with:

```python
class PayloadError(ValueError):
    """A payload that breaks its kind's schema (C12). The message is cut to 200 characters (B1 S12):
    a hostile peer's keys and values end up in it and must not flood the log."""

    MAX_MESSAGE = 200

    def __init__(self, message: Any) -> None:
        text = str(message)
        if len(text) > self.MAX_MESSAGE:
            text = text[:self.MAX_MESSAGE - 3] + '...'
        super().__init__(text)
```

3. Factor the JSON reading out of `parse_envelope`:

```python
def load_json(data: Any, limit: int, what: str) -> Any:
    """JSON from received bytes: at most `limit` bytes, UTF-8 without BOM, no duplicate keys, no NaN."""
    if not isinstance(data, (bytes, bytearray)):
        raise EnvelopeError(f'{what} must be bytes')
    if len(data) > limit:
        raise EnvelopeError(f'{what} larger than {limit // 1024} KB')
    try:
        return json.loads(bytes(data).decode('utf-8'), object_pairs_hook=_no_duplicate_keys,
                          parse_constant=_reject_constant)
    except EnvelopeError:
        raise
    except (ValueError, RecursionError) as e:
        raise EnvelopeError(f'{what} is not JSON: {e}') from e


def parse_envelope(data: Any) -> dict:
    """Envelope dict from received bytes; the bytes themselves are what the signature covers."""
    return check_structure(load_json(data, MAX_ENVELOPE_BYTES, 'envelope'))
```

   `bytes.decode('utf-8')` keeps a BOM as U+FEFF, and `json.loads` refuses it as a str. The test
   pins that.

4. Replace `_FORBIDDEN` and the check in `_string` with the whitelist:

```python
# S10: letters, marks, numbers, punctuation, symbols and space separators only.
_ALLOWED_CATEGORIES = frozenset({
    'Lu', 'Ll', 'Lt', 'Lm', 'Lo', 'Mn', 'Mc', 'Me', 'Nd', 'Nl', 'No',
    'Pc', 'Pd', 'Ps', 'Pe', 'Pi', 'Pf', 'Po', 'Sm', 'Sc', 'Sk', 'So', 'Zs'})


def text_allowed(value: str) -> bool:
    return all(unicodedata.category(ch) in _ALLOWED_CATEGORIES for ch in value)
```

   In `_string`, replace the `_FORBIDDEN.search(value)` branch with:

```python
        if not text_allowed(value):
            raise PayloadError(f'{path} contains a character outside letters, marks, numbers, '
                               'punctuation, symbols and spaces')
```

   Then search the tests for the old message (`grep -n "control, bidi" tests/`) and update any
   assertion on its wording.

5. Add the helpers and the roster and status validators after `_BODY_ID`:

```python
ROSTER_KINDS = ('group', 'invite', 'join', 'remove')
ROSTER_NAME_MAX = 64
_ROSTER_NAME = re.compile(r'[\x20-\x7e]{1,64}')
_HEX64 = re.compile(r'[0-9a-f]{64}')
FLOOR = datetime(2014, 12, 16, tzinfo=timezone.utc)
FUTURE_SLACK = timedelta(hours=24)


def is_author(value: Any) -> bool:
    return isinstance(value, str) and _AUTHOR.fullmatch(value) is not None


def is_hex64(value: Any) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def is_roster_name(value: Any) -> bool:
    return isinstance(value, str) and _ROSTER_NAME.fullmatch(value) is not None


def parse_stamp(value: Any) -> Optional[datetime]:
    """The stamp as an aware UTC datetime, or None if its shape or its calendar date is wrong."""
    if stamp(value) is None:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def plausible_received(created_at: Any, now: datetime) -> bool:
    """S11: a real time, not before the game's journal existed, not more than a day ahead."""
    moment = parse_stamp(created_at)
    return moment is not None and FLOOR <= moment <= now + FUTURE_SLACK


def ascii_name(text: str) -> str:
    """A roster name (S1) from any commander name: non-ASCII as '?', at most 64 characters."""
    cleaned = ''.join(ch if '\x20' <= ch <= '\x7e' else '?' for ch in str(text))
    cleaned = cleaned[:ROSTER_NAME_MAX].strip()
    return cleaned or 'CMDR'


def _roster_name(value: Any, path: str) -> None:
    if not is_roster_name(value):
        raise PayloadError(f'{path} must be 1-64 printable ASCII characters')


def _hex64(value: Any, path: str) -> None:
    if not is_hex64(value):
        raise PayloadError(f'{path} must be 64 lowercase hex characters')


def _author_field(value: Any, path: str) -> None:
    if not is_author(value):
        raise PayloadError(f'{path} must be 43 base64url characters')


def _real_stamp(value: Any, path: str) -> None:
    if parse_stamp(value) is None:
        raise PayloadError(f'{path} must be a real YYYY-MM-DDTHH:MM:SSZ time')


_GROUP = _object({'group_id': _uuid, 'name': _roster_name, 'commander': _roster_name})
_INVITE = _object({'group_id': _uuid, 'invite_id': _uuid, 'token_hash': _hex64,
                   'expires_at': _real_stamp, 'label': _nullable(_roster_name)})
_JOIN = _object({'group_id': _uuid, 'invite_id': _uuid, 'token': _hex64,
                 'commander': _roster_name})
_REMOVE = _object({'group_id': _uuid, 'member': _author_field,
                   'last_seq': _integer(0, MAX_SAFE_INT)})

STATUS_FIELDS = ('commander', 'system', 'address', 'coords', 'ship', 'ship_name', 'ship_ident',
                 'edsy_url', 'hull', 'fuel_main', 'fuel_reserve', 'fuel_capacity_main',
                 'fuel_capacity_reserve', 'paused')
EDSY_MAX = 6000
_EDSY = re.compile(r'https://edsy\.org/#/I=[A-Za-z0-9_%-]+')


def _edsy(value: Any, path: str) -> None:
    if not isinstance(value, str) or len(value) > EDSY_MAX or not _EDSY.fullmatch(value):
        raise PayloadError(f'{path} must be an EDSY import link of at most {EDSY_MAX} characters')


def _bounded(high: float) -> Checker:
    def check(value: Any, path: str) -> None:
        message = f'{path} must be a number from 0 to {high:g}'
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PayloadError(message)
        try:
            number = float(value)
        except OverflowError:
            raise PayloadError(message) from None
        if not (math.isfinite(number) and 0.0 <= number <= high):
            raise PayloadError(message)
    return check


_TONS = _nullable(_bounded(1e5))
_STATUS = _object({
    'commander': _NAME_OR_NULL, 'system': _NAME_OR_NULL, 'address': _nullable(_address),
    'coords': _nullable(_coords), 'ship': _NAME_OR_NULL, 'ship_name': _NAME_OR_NULL,
    'ship_ident': _NAME_OR_NULL, 'edsy_url': _nullable(_edsy), 'hull': _nullable(_bounded(1.0)),
    'fuel_main': _TONS, 'fuel_reserve': _TONS, 'fuel_capacity_main': _TONS,
    'fuel_capacity_reserve': _TONS, 'paused': _boolean})
_STATUS_DOC = _object({'author': _author_field, 'at': _real_stamp, 'status': _STATUS})


def validate_status(doc: Any) -> None:
    """S8: a signed status document {author, at, status}."""
    _STATUS_DOC(doc, 'status')
```

   These definitions use `_NAME_OR_NULL`, `_nullable`, `_object`, `_coords` and `_address`, so
   place them after those.

6. Register the roster kinds:

```python
_VALIDATORS: dict[tuple[str, int], Checker] = {
    ('visit', 1): _VISIT, ('honk', 1): _HONK, ('all_bodies', 1): _ALL_BODIES,
    ('body', 1): _body, ('signals', 1): _signals, ('find', 1): _find, ('note', 1): _NOTE,
    ('group', 1): _GROUP, ('invite', 1): _INVITE, ('join', 1): _JOIN, ('remove', 1): _REMOVE,
}
```

   `natural_key` already returns `None` for these kinds, so they are bound by UUIDv7 (ADR-0007).

- [ ] **Step 4: Run the tests and see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_events.py -q`, then the whole suite with
`.venv/Scripts/python.exe -m pytest -q`.
Expected: all pass. Fix any older test that asserted the replaced message.

- [ ] **Step 5: Commit**

```bash
git add edmrn/ext/crew_events.py tests/test_crew_events.py
git commit -m "feat(crew): category whitelist, roster and status validators, stamp plausibility (B1 S1 S8 S10-S12)"
```

(Append the two trailer lines from Global Constraints to every commit message.)

---

### Task 2: Roster, invite strings and `group.json`

**Files:**
- Create: `edmrn/ext/crew_roster.py`
- Test: `tests/test_crew_roster.py`

**Interfaces:**
- Consumes: Task 1's `ev.ROSTER_KINDS`, `ev.is_author`, `ev.is_hex64`, `ev.is_roster_name`,
  `ev.plausible_received`, `ev.load_json`, `ev.is_uuid7`, `ev.parse_envelope`, `ev.validate_payload`,
  `ev.canonical_bytes`.
- Produces (`edmrn.ext.crew_roster`):
  - `RosterEnvelope(author, seq, kind, created_at, payload)` (frozen dataclass), `.order`;
  - `parse_roster_envelope(data: bytes, own: frozenset, now: datetime) -> Optional[RosterEnvelope]`;
  - `Member(author, commander, leader, last_seq)`, `InviteInfo(invite_id, label, expires_at,
    redeemed_by)`;
  - `Roster(group_id, leader, name, members: dict[str, Member], invites: dict[str, InviteInfo])`
    with `.admits(author, seq) -> bool` and `.active() -> list[Member]`;
  - `compute_roster(envelopes, group_id, leader) -> Roster`;
  - `token_hash(token) -> str`, `new_token() -> str`;
  - `InviteError(ValueError)`, `InviteCode(url, group_id, invite_id, token, leader)`;
  - `check_relay_url(url) -> str`, `connection_url(base, group_id) -> str`;
  - `encode_invite(code) -> str`, `extract_invite(text) -> str`, `decode_invite(text) -> InviteCode`,
    `invite_link(code_text, relay_url) -> str`;
  - `check_webhook(url) -> str`, `redact_webhook(url) -> str`;
  - `GroupConfig(url, group_id, leader, member, webhook=None, paused=False)` with `.is_leader`;
  - `GroupConfigError`, `load_group(directory) -> Optional[GroupConfig]`, `save_group(directory,
    config)`, `delete_group(directory)`;
  - `INVITE_PREFIX = 'edmrn-crew:1:'`, `INVITE_HOURS = (1, 24, 168)`, `DEFAULT_RELAY`,
    `GROUP_FILE = 'group.json'`.

- [ ] **Step 1: Write the failing tests** (`tests/test_crew_roster.py`)

```python
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
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_roster.py -q`
Expected: `ModuleNotFoundError: No module named 'edmrn.ext.crew_roster'`.

- [ ] **Step 3: Implement `edmrn/ext/crew_roster.py`**

```python
"""Group roster, invite strings and group.json (crew sync core spec S1-S5a, ADR-0008).

Membership is a pure function of the roster envelopes and the leader's author. The relay computes it
the same way (crew-relay/src/roster.ts); crew-relay/test/vectors.json pins both implementations.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

from edmrn.ext import crew_events as ev

INVITE_PREFIX = 'edmrn-crew:1:'
INVITE_MAX_CHARS = 2000
INVITE_HOURS = (1, 24, 168)
GROUP_FILE = 'group.json'
DEFAULT_RELAY = 'wss://galroute.the-hub.cloud'
_LOCAL_HOSTS = ('localhost', '127.0.0.1')
_B64URL = re.compile(r'[A-Za-z0-9_-]+')
_WEBHOOK = re.compile(
    r'https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/([0-9]{1,20})/[A-Za-z0-9_-]{1,100}')
_DECORATION = ' \t\r\n<>`'


class InviteError(ValueError):
    """An invite, relay address or webhook URL that cannot be used. The message is one sentence."""


class GroupConfigError(ValueError):
    """group.json exists but cannot be used."""


@dataclass(frozen=True)
class RosterEnvelope:
    author: str
    seq: int
    kind: str
    created_at: str
    payload: dict

    @property
    def order(self) -> tuple:
        return (self.created_at, self.author, self.seq)


@dataclass(frozen=True)
class Member:
    author: str
    commander: Optional[str]
    leader: bool
    last_seq: Optional[int]


@dataclass(frozen=True)
class InviteInfo:
    invite_id: str
    label: Optional[str]
    expires_at: str
    redeemed_by: Optional[str]


@dataclass(frozen=True)
class Roster:
    group_id: str
    leader: str
    name: Optional[str]
    members: dict
    invites: dict

    def admits(self, author: str, seq: int) -> bool:
        member = self.members.get(author)
        return member is not None and (member.last_seq is None or seq <= member.last_seq)

    def active(self) -> list:
        """Members not removed, the leader first, then by commander name."""
        rest = sorted((m for m in self.members.values() if not m.leader and m.last_seq is None),
                      key=lambda m: ((m.commander or '').lower(), m.author))
        return [self.members[self.leader]] + rest


def token_hash(token: str) -> str:
    return hashlib.sha256(bytes.fromhex(token)).hexdigest()


def new_token() -> str:
    return secrets.token_hex(32)


def parse_roster_envelope(data: Any, own: frozenset, now: datetime) -> Optional[RosterEnvelope]:
    """A roster envelope that may count toward membership (S1, S2, ADR-0007), else None.

    Requires canonical bytes, a known roster kind at v 1, a UUIDv7 event id, a valid payload and,
    for other authors, a plausible created_at (S11). The relay applies the same rules."""
    try:
        env = ev.parse_envelope(data)
    except ev.EnvelopeError:
        return None
    if env['kind'] not in ev.ROSTER_KINDS or env['v'] != 1 or not ev.is_uuid7(env['event_id']):
        return None
    if ev.canonical_bytes(env) != bytes(data):
        return None
    try:
        ev.validate_payload(env['kind'], 1, env['payload'])
    except ev.PayloadError:
        return None
    if env['author'] not in own and not ev.plausible_received(env['created_at'], now):
        return None
    return RosterEnvelope(env['author'], env['seq'], env['kind'], env['created_at'], env['payload'])


def compute_roster(envelopes: Iterable[RosterEnvelope], group_id: str, leader: str) -> Roster:
    """S2. `envelopes` must already have passed parse_roster_envelope."""
    mine = sorted((e for e in envelopes if e.payload.get('group_id') == group_id),
                  key=lambda e: e.order)
    name: Optional[str] = None
    leader_commander: Optional[str] = None
    invites: dict[str, RosterEnvelope] = {}
    for e in mine:
        if e.author != leader:
            continue
        if e.kind == 'group' and name is None:
            name, leader_commander = e.payload['name'], e.payload['commander']
        elif e.kind == 'invite' and e.payload['invite_id'] not in invites:
            invites[e.payload['invite_id']] = e
    admitted: dict[str, str] = {}
    redeemed: dict[str, str] = {}
    for e in mine:
        if e.kind != 'join' or e.author == leader:
            continue
        invite_id = e.payload['invite_id']
        invite = invites.get(invite_id)
        if invite is None or invite_id in redeemed:
            continue
        if token_hash(e.payload['token']) != invite.payload['token_hash']:
            continue
        if e.created_at > invite.payload['expires_at']:
            continue
        redeemed[invite_id] = e.author
        admitted.setdefault(e.author, e.payload['commander'])
    caps: dict[str, int] = {}
    for e in mine:
        if e.kind == 'remove' and e.author == leader and e.payload['member'] != leader:
            member, last = e.payload['member'], e.payload['last_seq']
            caps[member] = min(caps.get(member, last), last)
    members = {leader: Member(leader, leader_commander, True, None)}
    for author, commander in admitted.items():
        members[author] = Member(author, commander, False, caps.get(author))
    infos = {invite_id: InviteInfo(invite_id, e.payload['label'], e.payload['expires_at'],
                                   redeemed.get(invite_id))
             for invite_id, e in invites.items()}
    return Roster(group_id, leader, name, members, infos)


@dataclass(frozen=True)
class InviteCode:
    url: str
    group_id: str
    invite_id: str
    token: str
    leader: str


def check_relay_url(url: Any) -> str:
    """S5: wss:// with a host and nothing else; ws:// only for localhost. Returns the normal form."""
    message = 'The relay address must look like wss://galroute.the-hub.cloud.'
    if not isinstance(url, str) or len(url) > 200:
        raise InviteError(message)
    try:
        parts = urlsplit(url.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        raise InviteError(message) from None
    if not host or parts.username or parts.password or parts.query or parts.fragment:
        raise InviteError(message)
    if parts.path not in ('', '/'):
        raise InviteError(message)
    if parts.scheme != 'wss' and not (parts.scheme == 'ws' and host in _LOCAL_HOSTS):
        raise InviteError(message)
    return f'{parts.scheme}://{host}' + (f':{port}' if port else '')


def connection_url(base: str, group_id: str) -> str:
    return f'{base}/v1/groups/{group_id}/ws'


def encode_invite(code: InviteCode) -> str:
    body = ev.canonical_bytes({'g': code.group_id, 'i': code.invite_id, 'k': code.token,
                               'l': code.leader, 'u': code.url})
    return INVITE_PREFIX + base64.urlsafe_b64encode(body).rstrip(b'=').decode('ascii')


def extract_invite(text: Any) -> str:
    """The bare invite from what a commander pasted: the string, the link, or either inside
    Discord decoration (<...>, backticks, whitespace)."""
    raw = str(text).strip(_DECORATION)
    if '#' in raw:
        raw = raw.split('#', 1)[1]
    return raw.strip(_DECORATION)


def decode_invite(text: Any) -> InviteCode:
    damaged = InviteError('The invite is damaged; ask the leader for a new one.')
    raw = extract_invite(text)
    if len(raw) > INVITE_MAX_CHARS or not raw.startswith(INVITE_PREFIX):
        raise InviteError('This is not an EDMRN crew invite.')
    body = raw[len(INVITE_PREFIX):]
    if not _B64URL.fullmatch(body):
        raise damaged
    try:
        data = base64.urlsafe_b64decode(body + '=' * (-len(body) % 4))
        obj = ev.load_json(data, INVITE_MAX_CHARS, 'invite')
    except (binascii.Error, ValueError):
        raise damaged from None
    if not isinstance(obj, dict) or set(obj) != {'g', 'i', 'k', 'l', 'u'}:
        raise damaged
    if not (ev.is_uuid7(obj['g']) and ev.is_uuid7(obj['i']) and ev.is_hex64(obj['k'])
            and ev.is_author(obj['l'])):
        raise damaged
    return InviteCode(check_relay_url(obj['u']), obj['g'], obj['i'], obj['k'], obj['l'])


def invite_link(code_text: str, relay_url: str) -> str:
    parts = urlsplit(relay_url)
    scheme = 'https' if parts.scheme == 'wss' else 'http'
    return f'{scheme}://{parts.netloc}/join#{code_text}'


def check_webhook(url: Any) -> str:
    if isinstance(url, str) and _WEBHOOK.fullmatch(url.strip()):
        return url.strip()
    raise InviteError('This is not a Discord webhook URL (https://discord.com/api/webhooks/...).')


def redact_webhook(url: str) -> str:
    match = _WEBHOOK.fullmatch(url) if isinstance(url, str) else None
    return f'https://discord.com/api/webhooks/{match.group(1)}/***' if match else '<webhook>'


@dataclass(frozen=True)
class GroupConfig:
    url: str
    group_id: str
    leader: str
    member: str
    webhook: Optional[str] = None
    paused: bool = False

    @property
    def is_leader(self) -> bool:
        return self.member == self.leader


_GROUP_KEYS = {'v', 'url', 'group_id', 'leader', 'member', 'webhook', 'paused'}


def load_group(directory: Any) -> Optional[GroupConfig]:
    path = Path(directory) / GROUP_FILE
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as e:
        raise GroupConfigError(f'{GROUP_FILE}: {e}') from e
    if (not isinstance(obj, dict) or set(obj) != _GROUP_KEYS or type(obj['v']) is not int
            or obj['v'] != 1):
        raise GroupConfigError(f'{GROUP_FILE}: unknown format')
    try:
        url = check_relay_url(obj['url'])
        webhook = None if obj['webhook'] is None else check_webhook(obj['webhook'])
    except InviteError as e:
        raise GroupConfigError(f'{GROUP_FILE}: {e}') from e
    if not (ev.is_uuid7(obj['group_id']) and ev.is_author(obj['leader'])
            and ev.is_author(obj['member']) and isinstance(obj['paused'], bool)):
        raise GroupConfigError(f'{GROUP_FILE}: unknown format')
    return GroupConfig(url, obj['group_id'], obj['leader'], obj['member'], webhook, obj['paused'])


def save_group(directory: Any, config: GroupConfig) -> None:
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / GROUP_FILE
    tmp = path.with_name(path.name + '.tmp')
    record = {'v': 1, 'url': config.url, 'group_id': config.group_id, 'leader': config.leader,
              'member': config.member, 'webhook': config.webhook, 'paused': config.paused}
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(record, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def delete_group(directory: Any) -> None:
    (Path(directory) / GROUP_FILE).unlink(missing_ok=True)
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_roster.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add edmrn/ext/crew_roster.py tests/test_crew_roster.py
git commit -m "feat(crew): roster membership, invite strings and links, webhook check, group.json (B1 S1-S5a)"
```

---

### Task 3: Gated projection, batch insert and the store queries for sync

**Files:**
- Modify: `edmrn/ext/store.py` (migration 3), `edmrn/ext/crew_store.py`,
  `edmrn/ext/crew_identity.py`
- Modify: `tests/test_crew_store.py` (fixture and schema version), `tests/crew_helpers.py`
- Test: `tests/test_crew_gate.py` (create)

**Interfaces:**
- Consumes: Task 1 (`ev.ROSTER_KINDS`, `ev.plausible_received`), Task 2 (`parse_roster_envelope`,
  `compute_roster`, `Roster`).
- Produces:
  - `CrewStore(store, *, clock=None, admit=None)`:
    - `clock() -> datetime` (aware UTC, default now);
    - `admit(author, seq) -> bool` replaces the roster check; only tests that predate the roster
      pass it.
  - `CrewStore.set_gate(own: frozenset, group_id: Optional[str] = None, leader: Optional[str] = None)
    -> None`, on the Tk thread. It recomputes the roster, projects newly admitted authors, and
    rebuilds if members were narrowed.
  - `CrewStore.roster -> Optional[Roster]`.
  - `CrewStore.insert_received_batch(items: list[tuple[bytes, bytes]]) -> ReceivedBatch(inserted:
    int, rejected: list[tuple[int, str]], rebuilt: bool)`.
  - `CrewStore.insert_received(envelope, signature) -> bool`, unchanged behaviour: it raises
    `EnvelopeError` on a bad structure or signature.
  - `CrewStore.heads() -> dict[str, int]`.
  - `CrewStore.envelopes_after(author, after_seq, limit=100) -> list[tuple[int, bytes, bytes]]`.
  - `CrewStore.own_roster_envelope(author, kind, group_id) -> Optional[tuple[bytes, bytes]]`.
  - `CrewStore.rebuild_if_marked() -> bool`.
  - `KeyRing.own_authors() -> frozenset[str]` and
    `KeyRing.identity_for_author(author) -> Optional[Identity]`.
  - `SCHEMA_VERSION == 3`.

- [ ] **Step 1: Adapt the existing fixture and helpers**

In `tests/test_crew_store.py`, change the `crew` fixture and the schema test:

```python
@pytest.fixture
def crew(store):
    # These tests predate the roster (Spec B1 S3); they admit every author explicitly.
    return CrewStore(store, admit=lambda author, seq: True)
```

```python
def test_schema_version_is_3():
    assert SCHEMA_VERSION == 3
```

Search `tests/` for other `CrewStore(` constructions (`grep -rn "CrewStore(" tests/`). Where a test
inserts friends' envelopes, pass `admit=lambda author, seq: True`. Where it goes through
`crew.install`, leave it alone: install sets the gate in Task 9, and own events are always
projected.

- [ ] **Step 2: Write the failing tests** (`tests/test_crew_gate.py`)

```python
import logging
from datetime import datetime, timezone

import pytest

from crew_helpers import ADDR, dump, sealed, visit
from edmrn.ext import crew_events as ev
from edmrn.ext.crew_identity import KeyRing
from edmrn.ext.crew_roster import token_hash
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.store import Store

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
G = "01920000-0000-7000-8000-0000000000a1"
INV = "01920000-0000-7000-8000-0000000000b1"
TOKEN = "11" * 32


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "edmrn.db")
    yield s
    s.close()


@pytest.fixture
def keys(tmp_path):
    ring = KeyRing(tmp_path / "other")
    return {name: ring.for_commander(name) for name in ("leader", "alice", "bob", "me")}


def roster_env(identity, seq, kind, at, **payload):
    payload.setdefault("group_id", G)
    env = ev.build_envelope(ev.uuid7(), identity.author, seq, kind, at, payload)
    return ev.seal(env, identity.sign)


def group_and_invite(leader):
    return [
        roster_env(leader, 1, "group", "2026-10-13T10:00:00Z", name="Crew", commander="Leader"),
        roster_env(leader, 2, "invite", "2026-10-13T10:01:00Z", invite_id=INV,
                   token_hash=token_hash(TOKEN), expires_at="2026-10-14T10:00:00Z", label=None),
    ]


def join_of(identity, seq=5):
    return roster_env(identity, seq, "join", "2026-10-13T10:02:00Z", invite_id=INV, token=TOKEN,
                      commander="Alice")


def systems(store):
    return dump(store)["crew_systems"]


def test_a_non_members_envelope_is_stored_but_not_projected(store, keys):
    crew = CrewStore(store, clock=lambda: NOW)
    crew.set_gate(frozenset({keys["me"].author}), G, keys["leader"].author)
    result = crew.insert_received_batch([sealed(keys["bob"], visit(at="2026-10-13T09:00:00Z"), 1)])
    assert result.inserted == 1 and systems(store) == []


def test_a_join_arriving_after_the_members_events_projects_them(store, keys):
    crew = CrewStore(store, clock=lambda: NOW)
    crew.set_gate(frozenset({keys["me"].author}), G, keys["leader"].author)
    crew.insert_received_batch(group_and_invite(keys["leader"]))
    crew.insert_received_batch([sealed(keys["alice"], visit(at="2026-10-13T09:00:00Z"), 1)])
    assert systems(store) == []
    crew.insert_received_batch([join_of(keys["alice"], seq=2)])
    assert len(systems(store)) == 1 and keys["alice"].author in crew.roster.members


def test_removal_withdraws_rows_above_last_seq(store, keys):
    crew = CrewStore(store, clock=lambda: NOW)
    crew.set_gate(frozenset({keys["me"].author}), G, keys["leader"].author)
    crew.insert_received_batch(group_and_invite(keys["leader"]) + [join_of(keys["alice"], seq=1)])
    crew.insert_received_batch([
        sealed(keys["alice"], visit(at="2026-10-13T09:00:00Z", address="1"), 2),
        sealed(keys["alice"], visit(at="2026-10-13T09:01:00Z", address="2"), 3),
    ])
    assert {row[0] for row in systems(store)} == {"1", "2"}
    result = crew.insert_received_batch([roster_env(keys["leader"], 3, "remove", "2026-10-13T11:00:00Z",
                                                    member=keys["alice"].author, last_seq=2)])
    assert result.rebuilt
    assert {row[0] for row in systems(store)} == {"1"}


@pytest.mark.parametrize("stamp", ["2014-12-15T23:59:59Z", "2026-10-14T12:00:01Z", "2026-02-30T00:00:00Z"])
def test_an_implausible_received_stamp_is_stored_not_projected(store, keys, stamp):
    crew = CrewStore(store, clock=lambda: NOW, admit=lambda a, s: True)
    assert crew.insert_received_batch([sealed(keys["bob"], visit(at=stamp), 1)]).inserted == 1
    assert systems(store) == []
    crew.insert_received_batch([sealed(keys["bob"], visit(at="2026-10-13T09:00:00Z", address="2"), 2)])
    assert [row[0] for row in systems(store)] == ["2"]


def test_own_reports_name_the_system_before_earlier_friend_reports(store, keys):
    crew = CrewStore(store, clock=lambda: NOW, admit=lambda a, s: True)
    crew.set_gate(frozenset({keys["me"].author}))
    crew.insert_received_batch([sealed(keys["bob"], visit(at="2026-10-13T08:00:00Z", system="Fake Name"), 1)])
    crew.insert_received_batch([sealed(keys["me"], visit(at="2026-10-13T09:00:00Z", system="Sol"), 1)])
    assert systems(store)[0][1] == "Sol"


def test_equivocation_warns_once_per_author(store, keys, caplog):
    crew = CrewStore(store, clock=lambda: NOW, admit=lambda a, s: True)
    first = sealed(keys["bob"], visit(at="2026-10-13T09:00:00Z"), 1)
    second = sealed(keys["bob"], visit(at="2026-10-13T09:05:00Z"), 1)
    third = sealed(keys["bob"], visit(at="2026-10-13T09:06:00Z"), 1)
    crew.insert_received_batch([first])
    with caplog.at_level(logging.WARNING):
        crew.insert_received_batch([second, third])
    assert sum("two different envelopes" in r.message for r in caplog.records) == 1


def test_rebuild_logs_one_summary(store, keys, caplog):
    crew = CrewStore(store, clock=lambda: NOW)
    crew.set_gate(frozenset())
    crew.insert_received_batch([sealed(keys["bob"], visit(at="2026-10-13T09:00:00Z", address=str(n)), n)
                                for n in range(1, 6)])
    with caplog.at_level(logging.WARNING):
        crew.rebuild_projections()
    lines = [r.message for r in caplog.records if "unprojected" in r.message]
    assert len(lines) == 1 and "5 author is not a member" in lines[0]


def test_heads_pages_and_own_roster_envelope(store, keys):
    crew = CrewStore(store, clock=lambda: NOW, admit=lambda a, s: True)
    crew.insert_received_batch([sealed(keys["bob"], visit(at="2026-10-13T09:00:00Z", address=str(n)), n)
                                for n in range(1, 251)])
    assert crew.heads() == {keys["bob"].author: 250}
    page = crew.envelopes_after(keys["bob"].author, 100, 100)
    assert [seq for seq, _, _ in page] == list(range(101, 201))
    crew.insert_received_batch([join_of(keys["bob"], seq=251)])
    data, sig = crew.own_roster_envelope(keys["bob"].author, "join", G)
    assert ev.parse_envelope(data)["seq"] == 251


def test_batch_rejects_bad_items_and_keeps_the_rest(store, keys):
    crew = CrewStore(store, clock=lambda: NOW, admit=lambda a, s: True)
    good = sealed(keys["bob"], visit(at="2026-10-13T09:00:00Z"), 1)
    result = crew.insert_received_batch([(b"not json", b"x" * 64), (good[0], b"\0" * 64), good])
    assert result.inserted == 1 and [i for i, _ in result.rejected] == [0, 1]


def test_migration_marks_a_rebuild_once(store, keys):
    crew = CrewStore(store, clock=lambda: NOW)
    assert crew.rebuild_if_marked() is True
    assert crew.rebuild_if_marked() is False


def test_keyring_lists_own_authors_and_finds_identities(tmp_path):
    ring = KeyRing(tmp_path / "crew_keys")
    a, b = ring.for_commander("A"), ring.for_commander("B")
    (tmp_path / "crew_keys" / "group.json").write_text("{}", encoding="utf-8")
    fresh = KeyRing(tmp_path / "crew_keys")
    assert fresh.own_authors() == frozenset({a.author, b.author})
    assert fresh.identity_for_author(b.author).author == b.author
    assert fresh.identity_for_author("Z" * 43) is None
```

- [ ] **Step 3: Run them and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_gate.py tests/test_crew_store.py -q`
Expected: `TypeError: CrewStore.__init__() got an unexpected keyword argument 'clock'` and
`schema` failures.

- [ ] **Step 4: Implement**

**`edmrn/ext/store.py`**: append a third migration to `MIGRATIONS`:

```python
    [
        "CREATE INDEX crew_log_kind ON crew_log (kind)",
        "CREATE TABLE crew_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        # B1 S10/S11 change what projects; re-derive once (spec B1 S11).
        "INSERT INTO crew_meta (key, value) VALUES ('rebuild', 'B1')",
    ],
```

**`edmrn/ext/crew_identity.py`**: add to `KeyRing`:

```python
    def _scan(self):
        if not self.directory.is_dir():
            return
        for path in sorted(self.directory.glob('cmdr-*.json')):
            try:
                key, high_water = _parse_record(path.read_text(encoding='utf-8'))
            except (OSError, UnicodeDecodeError, KeyFileError):
                continue
            yield path, key, high_water

    def own_authors(self) -> frozenset:
        """Every author with a usable key file here (spec B1 S3: own authors always project)."""
        authors = {identity.author for identity in self._cache.values()}
        authors.update(_author_of(key) for _, key, _ in self._scan())
        return frozenset(authors)

    def identity_for_author(self, author: str):
        """The identity whose public key is `author`, or None."""
        for identity in self._cache.values():
            if identity.author == author:
                return identity
        for path, key, high_water in self._scan():
            if _author_of(key) == author:
                return Identity(path, key, high_water)
        return None
```

**`edmrn/ext/crew_store.py`**:

1. Imports and types:

```python
import dataclasses
from collections import Counter
from datetime import datetime, timezone

from edmrn.ext.crew_roster import Roster, compute_roster, parse_roster_envelope

Projector = Callable[[sqlite3.Connection, dict, bool, bool], None]


@dataclass(frozen=True)
class Gate:
    own: frozenset = frozenset()
    group_id: Optional[str] = None
    leader: Optional[str] = None


@dataclass(frozen=True)
class _Context:
    own: frozenset
    admits: Callable[[str, int], bool]
    now: datetime


@dataclass(frozen=True)
class ReceivedBatch:
    inserted: int
    rejected: list
    rebuilt: bool
```

2. System name and coordinates prefer own reports (S11). Add:

```python
def _system_order(env: dict, own: bool) -> str:
    """Own reports sort first; then (created_at, author, seq) as before (S11)."""
    return ('0|' if own else '1|') + _order(env)
```

   Every projector gains a fourth parameter `own: bool`. The calls to `_name_system` and
   `_place_system` pass `_system_order(env, own)` instead of `_order(env)`. `_count_system`, the honk
   key and the body and find `report_key` keep `_order(env)`. Example, `_project_visit`:

```python
def _project_visit(conn, env: dict, first: bool, own: bool) -> None:
    p, key = env['payload'], _system_order(env, own)
    _name_system(conn, p['address'], p['system'], key)
    _place_system(conn, p['address'], p['coords'], key)
    conn.execute(
        'INSERT INTO crew_visits (author, address, first_at, last_at, visit_count) '
        'VALUES (?, ?, ?, ?, ?) ON CONFLICT (author, address) DO UPDATE SET '
        'first_at = min(first_at, excluded.first_at), last_at = max(last_at, excluded.last_at), '
        'visit_count = visit_count + ?',
        (env['author'], p['address'], env['created_at'], env['created_at'], int(first), int(first)))
```

   Apply the same change to `_project_honk`, `_project_all_bodies`, `_project_body` and
   `_project_signals` (they call `_name_system`). `_project_find` and `_project_note` only gain the
   parameter. Add:

```python
def _project_roster(conn, env: dict, first: bool, own: bool) -> None:
    """Roster envelopes change membership (crew_roster), not projection tables."""
```

   Register `'group'`, `'invite'`, `'join'` and `'remove'` with `_project_roster` in `_PROJECTORS`.

3. Replace `_project` so it returns why an envelope stays unprojected:

```python
def _project(conn: sqlite3.Connection, env: dict, ctx: _Context) -> Optional[str]:
    """Project one envelope if it passes every gate (Spec A C4, B1 S3, S11); else the reason."""
    kind, v = env['kind'], env['v']
    if not ev.is_known(kind, v):
        return 'unknown kind or version'
    try:
        ev.validate_payload(kind, v, env['payload'])
    except ev.PayloadError as e:
        return f'invalid payload: {e}'
    own = env['author'] in ctx.own
    if not own:
        if not ev.plausible_received(env['created_at'], ctx.now):
            return 'implausible created_at'
        if kind not in ev.ROSTER_KINDS and not ctx.admits(env['author'], env['seq']):
            return 'author is not a member'
    key = ev.natural_key(kind, env['created_at'], env['payload'])
    bound = (env['event_id'] == ev.deterministic_id(env['author'], kind, *key) if key is not None
             else ev.is_uuid7(env['event_id']))
    if not bound:
        return 'event_id does not match its author and key'
    first = conn.execute('INSERT OR IGNORE INTO crew_projected (author, event_id) VALUES (?, ?)',
                         (env['author'], env['event_id'])).rowcount == 1
    _PROJECTORS[kind](conn, env, first, own)
    return None


_QUIET_REASONS = ('unknown kind or version', 'author is not a member')


def _warn_unprojected(env: dict, reason: Optional[str]) -> None:
    if reason is not None and reason not in _QUIET_REASONS:
        logger.warning(f"Crew event {env['event_id']} kept unprojected: {reason}")
```

4. The `CrewStore` class changes:

```python
class CrewStore:
    def __init__(self, store: Store, *, clock: Optional[Callable[[], datetime]] = None,
                 admit: Optional[Callable[[str, int], bool]] = None) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._admit_override = admit
        self._gate = Gate()
        self._roster: Optional[Roster] = None
        self._equivocators: set[str] = set()

    @property
    def roster(self) -> Optional[Roster]:
        return self._roster

    def _context(self, roster: Optional[Roster], extra_own: frozenset = frozenset()) -> _Context:
        if self._admit_override is not None:
            admits = self._admit_override
        elif roster is not None:
            admits = roster.admits
        else:
            admits = lambda author, seq: False
        return _Context(self._gate.own | extra_own, admits, self._clock())

    def _compute_roster(self, conn) -> Optional[Roster]:
        gate = self._gate
        if gate.group_id is None or gate.leader is None:
            return None
        now = self._clock()
        rows = conn.execute("SELECT envelope FROM crew_log WHERE kind IN "
                            "('group', 'invite', 'join', 'remove')").fetchall()
        envelopes = [e for e in (parse_roster_envelope(bytes(r[0]), gate.own, now) for r in rows)
                     if e is not None]
        return compute_roster(envelopes, gate.group_id, gate.leader)

    @staticmethod
    def _membership_change(old: Optional[Roster], new: Optional[Roster]) -> tuple[set, bool]:
        """(newly admitted authors, whether any member lost rows: removed or capped lower)."""
        before = {} if old is None else old.members
        after = {} if new is None else new.members
        admitted = {a for a in after if a not in before}
        narrowed = False
        for author, member in before.items():
            now = after.get(author)
            if now is None or (now.last_seq is not None
                               and (member.last_seq is None or now.last_seq < member.last_seq)):
                narrowed = True
        return admitted, narrowed

    def _project_authors(self, conn, authors: set, ctx: _Context) -> None:
        for author in sorted(authors - ctx.own):
            rows = conn.execute('SELECT envelope FROM crew_log WHERE author = ? ORDER BY seq',
                                (author,)).fetchall()
            for (data,) in rows:
                try:
                    env = ev.parse_envelope(bytes(data))
                except ev.EnvelopeError:
                    continue
                _project(conn, env, ctx)

    def _apply_roster_change(self, conn) -> tuple[Optional[Roster], bool]:
        """Inside a transaction: recompute membership, project newly admitted authors.
        Returns the new roster and whether a rebuild must follow the commit."""
        new = self._compute_roster(conn)
        admitted, narrowed = self._membership_change(self._roster, new)
        if admitted and not narrowed:
            self._project_authors(conn, admitted, self._context(new))
        return new, narrowed

    def set_gate(self, own: frozenset, group_id: Optional[str] = None,
                 leader: Optional[str] = None) -> None:
        """Tk thread. Own authors and the group of this installation (spec B1 S3, S4)."""
        self._gate = Gate(frozenset(own), group_id, leader)
        with self.store.transaction() as conn:
            new, narrowed = self._apply_roster_change(conn)
        self._roster = new
        if narrowed:
            self.rebuild_projections()
```

5. `append_own` uses a context that counts its own identities as own, and refreshes the roster
   when it wrote a roster kind:

```python
    def append_own(self, items: list[tuple[Identity, ev.Draft]]) -> int:
        """Sign and log own drafts in one transaction (C4); the key files follow after the commit."""
        written = 0
        allocated: dict[str, tuple[Identity, int]] = {}
        new_own = frozenset(identity.author for identity, _ in items)
        if not new_own <= self._gate.own:
            self._gate = dataclasses.replace(self._gate, own=self._gate.own | new_own)
        roster_touched = False
        narrowed = False
        new_roster = self._roster
        with self.store.transaction(busy_timeout_ms=BUSY_TIMEOUT_MS) as conn:
            ctx = self._context(self._roster)
            for identity, draft in items:
                # ... unchanged body up to _insert(...) ...
                _insert(conn, env, data, signature)
                _warn_unprojected(env, _project(conn, env, ctx))
                roster_touched = roster_touched or draft.kind in ev.ROSTER_KINDS
                allocated[identity.author] = (identity, last + 1)
                written += 1
            if roster_touched:
                new_roster, narrowed = self._apply_roster_change(conn)
        self._roster = new_roster
        # ... unchanged key-file loop ...
        if narrowed:
            self.rebuild_projections()
        return written
```

6. The batch insert, with the single insert as a wrapper:

```python
    def insert_received_batch(self, items: list) -> ReceivedBatch:
        """Received envelopes in one transaction (B1 S19): structure and signature first, then
        INSERT OR IGNORE on (author, seq), the gated projection, and the roster refresh."""
        parsed, rejected = [], []
        for index, (data, signature) in enumerate(items):
            try:
                env = ev.parse_envelope(data)
            except ev.EnvelopeError as e:
                rejected.append((index, str(e)))
                continue
            if not verify(env['author'], signature, data):
                rejected.append((index, 'signature does not verify'))
                continue
            parsed.append((env, bytes(data), bytes(signature)))
        inserted = 0
        roster_touched = False
        narrowed = False
        new_roster = self._roster
        with self.store.transaction(busy_timeout_ms=BUSY_TIMEOUT_MS) as conn:
            ctx = self._context(self._roster)
            for env, data, signature in parsed:
                if _insert(conn, env, data, signature):
                    inserted += 1
                    roster_touched = roster_touched or env['kind'] in ev.ROSTER_KINDS
                    _warn_unprojected(env, _project(conn, env, ctx))
                else:
                    self._check_equivocation(conn, env, data)
            if roster_touched:
                new_roster, narrowed = self._apply_roster_change(conn)
        self._roster = new_roster
        if narrowed:
            self.rebuild_projections()
        return ReceivedBatch(inserted, rejected, narrowed)

    def insert_received(self, envelope: bytes, signature: bytes) -> bool:
        """One received envelope; raises EnvelopeError like Spec A's version did."""
        result = self.insert_received_batch([(envelope, signature)])
        if result.rejected:
            raise ev.EnvelopeError(result.rejected[0][1])
        return result.inserted == 1

    def _check_equivocation(self, conn, env: dict, data: bytes) -> None:
        if env['author'] in self._equivocators:
            return
        row = conn.execute('SELECT envelope FROM crew_log WHERE author = ? AND seq = ?',
                           (env['author'], env['seq'])).fetchone()
        if row is not None and bytes(row[0]) != data:
            self._equivocators.add(env['author'])
            logger.warning(f"Crew author {env['author'][:12]}... sent two different envelopes "
                           f"for seq {env['seq']}; the first one is kept")
```

7. Rebuild with one summary line, and the marked rebuild:

```python
    def rebuild_projections(self, clear_marker: bool = False) -> None:
        """Re-derive every projection from the log in (created_at, author, seq) order (C8)."""
        reasons: Counter = Counter()
        with self.store.transaction() as conn:
            for table in PROJECTION_TABLES:
                conn.execute(f'DELETE FROM {table}')
            roster = self._compute_roster(conn)
            ctx = self._context(roster)
            rows = conn.execute(
                'SELECT envelope FROM crew_log ORDER BY created_at, author, seq').fetchall()
            for (data,) in rows:
                try:
                    env = ev.parse_envelope(bytes(data))
                except ev.EnvelopeError:
                    reasons['unreadable'] += 1
                    continue
                reason = _project(conn, env, ctx)
                if reason is not None:
                    reasons[reason.split(':')[0]] += 1
            if clear_marker:
                conn.execute("DELETE FROM crew_meta WHERE key = 'rebuild'")
        self._roster = roster
        if reasons:
            summary = ', '.join(f'{count} {reason}' for reason, count in sorted(reasons.items()))
            logger.warning(f'Crew rebuild left envelopes unprojected: {summary}')

    def rebuild_if_marked(self) -> bool:
        with self.store.read() as conn:
            marked = conn.execute("SELECT 1 FROM crew_meta WHERE key = 'rebuild'").fetchone()
        if marked is None:
            return False
        self.rebuild_projections(clear_marker=True)
        return True
```

8. Queries for sync:

```python
    def heads(self) -> dict[str, int]:
        """Highest stored seq per author (B1 S7 heads)."""
        with self.store.read() as conn:
            rows = conn.execute('SELECT author, MAX(seq) FROM crew_log GROUP BY author').fetchall()
        return {author: seq for author, seq in rows}

    def envelopes_after(self, author: str, after_seq: int, limit: int = 100) -> list:
        with self.store.read() as conn:
            rows = conn.execute('SELECT seq, envelope, signature FROM crew_log WHERE author = ? '
                                'AND seq > ? ORDER BY seq LIMIT ?',
                                (author, after_seq, limit)).fetchall()
        return [(seq, bytes(data), bytes(sig)) for seq, data, sig in rows]

    def own_roster_envelope(self, author: str, kind: str, group_id: str) -> Optional[tuple]:
        """The latest envelope of `kind` by `author` for `group_id` (the auth genesis or join)."""
        with self.store.read() as conn:
            rows = conn.execute('SELECT envelope, signature FROM crew_log WHERE author = ? '
                                'AND kind = ? ORDER BY seq DESC', (author, kind)).fetchall()
        for data, signature in rows:
            try:
                env = ev.parse_envelope(bytes(data))
            except ev.EnvelopeError:
                continue
            if env['payload'].get('group_id') == group_id:
                return bytes(data), bytes(signature)
        return None
```

   Delete the old `insert_received` body and the per-row warning in the old `rebuild_projections`.

- [ ] **Step 5: Run the tests and see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_gate.py tests/test_crew_store.py -q`, then
`.venv/Scripts/python.exe -m pytest -q`.
Expected: all pass. A Spec A test that checked a per-envelope rebuild warning now expects the
summary line; update its assertion to look for `kept unprojected` (live path) or
`left envelopes unprojected` (rebuild).

- [ ] **Step 6: Commit**

```bash
git add edmrn/ext/store.py edmrn/ext/crew_store.py edmrn/ext/crew_identity.py tests/test_crew_gate.py tests/test_crew_store.py tests/crew_helpers.py
git commit -m "feat(crew): membership gate, own-first systems, batch insert, sync queries (B1 S3 S11 S12 S19)"
```

(Stage `tests/crew_helpers.py` only if you changed it.)

---
### Task 4: Protocol frames

**Files:**
- Create: `edmrn/ext/crew_protocol.py`
- Test: `tests/test_crew_protocol.py`

**Interfaces:**
- Consumes: `ev.load_json`, `ev.canonical_bytes`, `ev.validate_status`, `ev.is_author`,
  `ev.MAX_ENVELOPE_BYTES`, `ev.MAX_SAFE_INT`; `crew_identity.verify`, `crew_identity.b64url`.
- Produces (`edmrn.ext.crew_protocol`):
  - constants `PROTOCOL_VERSION`, `MAX_FRAME_BYTES`, `MAX_EVS_ITEMS`, `MAX_EVS_BYTES`,
    `MAX_STATUS_BYTES`, `MAX_HEADS`, `ERROR_CODES`, `CLOSE_REPLACED`, `CLOSE_BAD_AUTH`,
    `CLOSE_FORBIDDEN`, `CLOSE_POLICY`;
  - `FrameError(ValueError)`;
  - `b64(data) -> str` and `unb64(text, max_bytes) -> bytes`;
  - `encode(frame: dict) -> str` and `decode(text: str) -> dict`;
  - `auth_message(group_id, nonce) -> bytes`, `parse_hello(frame) -> str`,
    `auth_frame(group_id, author, signature, genesis=None, join=None) -> dict`;
  - `have_frame(heads) -> dict` and `parse_have(frame) -> dict[str, int]`;
  - `evs_frames(items) -> Iterator[dict]` and `parse_evs(frame) -> list[tuple[bytes, bytes]]`;
  - `status_bytes(doc: dict) -> bytes`, `status_frame(data, signature) -> dict`,
    `parse_status(frame) -> tuple[str, str, dict]` (author, at, status);
  - `parse_err(frame) -> tuple[str, Optional[str], Optional[int]]`.

- [ ] **Step 1: Write the failing tests** (`tests/test_crew_protocol.py`)

```python
import json

import pytest

from edmrn.ext import crew_events as ev
from edmrn.ext import crew_protocol as proto
from edmrn.ext.crew_identity import KeyRing

G = "01920000-0000-7000-8000-0000000000a1"


@pytest.fixture
def me(tmp_path):
    return KeyRing(tmp_path / "k").for_commander("C1")


def test_b64_round_trip_and_strictness():
    assert proto.unb64(proto.b64(b"\x00\xff" * 10), 20) == b"\x00\xff" * 10
    for bad in ("a=", "a+b", "ab c", "A" * 100, 5, None):
        with pytest.raises(proto.FrameError):
            proto.unb64(bad, 20)
    with pytest.raises(proto.FrameError):
        proto.unb64("AB", 1)  # non-canonical: trailing bits set


def test_decode_refuses_bad_frames():
    for text in ('{"t":"x","t":"y"}', "[]", '{"x":1}', '{"t":5}', "x" * (proto.MAX_FRAME_BYTES + 1), b"{}"):
        with pytest.raises(proto.FrameError):
            proto.decode(text)
    assert proto.decode('{"t":"synced"}') == {"t": "synced"}


def test_auth_message_and_frame(me):
    nonce = proto.b64(bytes(range(32)))
    assert proto.auth_message(G, nonce) == f"edmrn-crew-auth\n{G}\n{nonce}".encode("ascii")
    frame = proto.auth_frame(G, me.author, b"s" * 64, join=(b"env", b"sig"))
    assert frame == {"t": "auth", "group": G, "author": me.author, "sig": proto.b64(b"s" * 64),
                     "join": {"e": proto.b64(b"env"), "s": proto.b64(b"sig")}}
    assert proto.parse_hello({"t": "hello", "v": 1, "source": "", "nonce": nonce}) == nonce
    for bad in ({"t": "hello", "v": 2, "nonce": nonce}, {"t": "hello", "v": 1, "nonce": "abc"}):
        with pytest.raises(proto.FrameError):
            proto.parse_hello(bad)


def test_have_round_trip_and_refusals(me):
    heads = {me.author: 7, "B" * 43: 0}
    assert proto.parse_have(json.loads(proto.encode(proto.have_frame(heads)))) == heads
    for bad in ({"short": 1}, {me.author: -1}, {me.author: True}, {me.author: 2**53}):
        with pytest.raises(proto.FrameError):
            proto.parse_have({"t": "have", "heads": bad})


def test_evs_frames_split_by_count_and_bytes():
    small = [(b"e" * 10, b"s" * 64)] * 250
    frames = list(proto.evs_frames(small))
    assert [len(f["items"]) for f in frames] == [100, 100, 50]
    big = [(b"e" * 16000, b"s" * 64)] * 60
    frames = list(proto.evs_frames(big))
    assert all(len(proto.encode(f)) <= proto.MAX_EVS_BYTES + 64 for f in frames)
    assert sum(len(f["items"]) for f in frames) == 60
    assert proto.parse_evs(json.loads(proto.encode(frames[0])))[0] == big[0]


def test_parse_evs_refusals():
    good = {"e": proto.b64(b"e"), "s": proto.b64(b"s" * 64)}
    for items in ([good] * 101, [{"e": good["e"]}], [{"e": good["e"], "s": proto.b64(b"s" * 63)}],
                  [dict(good, x=1)], "nope"):
        with pytest.raises(proto.FrameError):
            proto.parse_evs({"t": "evs", "items": items})


def status_doc(author, **over):
    status = {name: None for name in ev.STATUS_FIELDS}
    status.update(paused=False, commander="Jameson", hull=0.5)
    status.update(over)
    return {"author": author, "at": "2026-10-13T12:00:00Z", "status": status}


def test_status_round_trip_verifies_signature_and_author(me, tmp_path):
    data = proto.status_bytes(status_doc(me.author))
    frame = proto.status_frame(data, me.sign(data))
    author, at, status = proto.parse_status(json.loads(proto.encode(frame)))
    assert (author, at, status["hull"]) == (me.author, "2026-10-13T12:00:00Z", 0.5)
    other = KeyRing(tmp_path / "o").for_commander("C2")
    with pytest.raises(proto.FrameError):
        proto.parse_status(proto.status_frame(data, other.sign(data)))
    bad = proto.status_bytes(status_doc(me.author, hull=7))
    with pytest.raises(proto.FrameError):
        proto.parse_status(proto.status_frame(bad, me.sign(bad)))


def test_status_bytes_size_cap(me):
    with pytest.raises(proto.FrameError):
        proto.status_bytes(status_doc(me.author, commander="x" * 9000))


def test_parse_err(me):
    assert proto.parse_err({"t": "err", "code": "quota", "author": me.author, "seq": 4}) == ("quota", me.author, 4)
    assert proto.parse_err({"t": "err", "code": "weird"}) == ("unknown", None, None)
    assert proto.parse_err({"t": "err", "code": "rate", "author": "x", "seq": "y"}) == ("rate", None, None)
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_protocol.py -q`
Expected: `ModuleNotFoundError: No module named 'edmrn.ext.crew_protocol'`.

- [ ] **Step 3: Implement `edmrn/ext/crew_protocol.py`**

```python
"""Frames of the crew relay protocol (crew sync core spec S6-S9). Pure: no I/O.

Envelopes and signatures travel as base64url of the exact signed bytes and are never re-encoded.
The relay's side of the same protocol is crew-relay/src/group.ts.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any, Iterable, Iterator, Optional

from edmrn.ext import crew_events as ev
from edmrn.ext.crew_identity import b64url, verify

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 1 << 20
MAX_EVS_ITEMS = 100
MAX_EVS_BYTES = 512 * 1024
MAX_STATUS_BYTES = 8 * 1024
MAX_HEADS = 1000
SIGNATURE_BYTES = 64
NONCE_BYTES = 32
ERROR_CODES = frozenset({'quota', 'group_quota', 'not_member', 'removed', 'bad_sig', 'too_big',
                         'bad_frame', 'rate'})
CLOSE_REPLACED, CLOSE_BAD_AUTH, CLOSE_FORBIDDEN, CLOSE_POLICY = 4000, 4001, 4003, 4008
_B64URL = re.compile(r'[A-Za-z0-9_-]*')
_ITEM_OVERHEAD = 16  # {"e":"","s":""}, per item


class FrameError(ValueError):
    """A frame that breaks the protocol."""


def b64(data: bytes) -> str:
    return b64url(bytes(data))


def unb64(text: Any, max_bytes: int) -> bytes:
    """Bytes from canonical unpadded base64url of at most `max_bytes` bytes."""
    if not isinstance(text, str) or not _B64URL.fullmatch(text) or len(text) > -(-max_bytes * 4 // 3):
        raise FrameError('not canonical base64url of the allowed size')
    try:
        data = base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise FrameError('not base64url') from None
    if len(data) > max_bytes or b64(data) != text:
        raise FrameError('not canonical base64url of the allowed size')
    return data


def encode(frame: dict) -> str:
    return json.dumps(frame, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def decode(text: Any) -> dict:
    if not isinstance(text, str):
        raise FrameError('frames are text')
    data = text.encode('utf-8', 'surrogatepass')
    try:
        frame = ev.load_json(data, MAX_FRAME_BYTES, 'frame')
    except ev.EnvelopeError as e:
        raise FrameError(str(e)) from None
    if not isinstance(frame, dict) or not isinstance(frame.get('t'), str):
        raise FrameError('a frame is an object with a string t')
    return frame


def auth_message(group_id: str, nonce: str) -> bytes:
    return f'edmrn-crew-auth\n{group_id}\n{nonce}'.encode('ascii')


def parse_hello(frame: dict) -> str:
    if frame.get('v') != PROTOCOL_VERSION or isinstance(frame.get('v'), bool):
        raise FrameError('unsupported relay protocol version')
    nonce = frame.get('nonce')
    if len(unb64(nonce, NONCE_BYTES)) != NONCE_BYTES:
        raise FrameError('the relay sent a bad nonce')
    return nonce


def _item(pair: Optional[tuple]) -> Optional[dict]:
    return None if pair is None else {'e': b64(pair[0]), 's': b64(pair[1])}


def auth_frame(group_id: str, author: str, signature: bytes, genesis: Optional[tuple] = None,
               join: Optional[tuple] = None) -> dict:
    frame = {'t': 'auth', 'group': group_id, 'author': author, 'sig': b64(signature)}
    if genesis is not None:
        frame['genesis'] = _item(genesis)
    if join is not None:
        frame['join'] = _item(join)
    return frame


def have_frame(heads: dict) -> dict:
    return {'t': 'have', 'heads': dict(heads)}


def parse_have(frame: dict) -> dict:
    heads = frame.get('heads')
    if not isinstance(heads, dict) or len(heads) > MAX_HEADS:
        raise FrameError('heads must be an object of at most 1000 authors')
    for author, seq in heads.items():
        if (not ev.is_author(author) or isinstance(seq, bool) or not isinstance(seq, int)
                or not 0 <= seq <= ev.MAX_SAFE_INT):
            raise FrameError('heads maps authors to sequence numbers')
    return dict(heads)


def evs_frames(items: Iterable[tuple]) -> Iterator[dict]:
    """`evs` frames of at most 100 items and 512 KiB of items, in the given order."""
    batch: list = []
    size = 0
    for data, signature in items:
        item = {'e': b64(data), 's': b64(signature)}
        item_size = len(item['e']) + len(item['s']) + _ITEM_OVERHEAD
        if batch and (len(batch) >= MAX_EVS_ITEMS or size + item_size > MAX_EVS_BYTES):
            yield {'t': 'evs', 'items': batch}
            batch, size = [], 0
        batch.append(item)
        size += item_size
    if batch:
        yield {'t': 'evs', 'items': batch}


def parse_evs(frame: dict) -> list:
    items = frame.get('items')
    if not isinstance(items, list) or len(items) > MAX_EVS_ITEMS:
        raise FrameError('items must be a list of at most 100')
    out = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {'e', 's'}:
            raise FrameError('an item is {e, s}')
        data = unb64(item['e'], ev.MAX_ENVELOPE_BYTES)
        signature = unb64(item['s'], SIGNATURE_BYTES)
        if len(signature) != SIGNATURE_BYTES:
            raise FrameError('a signature is 64 bytes')
        out.append((data, signature))
    return out


def status_bytes(doc: dict) -> bytes:
    data = ev.canonical_bytes(doc)
    if len(data) > MAX_STATUS_BYTES:
        raise FrameError('status larger than 8 KiB')
    return data


def status_frame(data: bytes, signature: bytes) -> dict:
    return {'t': 'st', 'e': b64(data), 's': b64(signature)}


def parse_status(frame: dict) -> tuple:
    data = unb64(frame.get('e'), MAX_STATUS_BYTES)
    signature = unb64(frame.get('s'), SIGNATURE_BYTES)
    try:
        doc = ev.load_json(data, MAX_STATUS_BYTES, 'status')
        ev.validate_status(doc)
    except (ev.EnvelopeError, ev.PayloadError) as e:
        raise FrameError(str(e)) from None
    if not verify(doc['author'], signature, data):
        raise FrameError('status signature does not verify')
    return doc['author'], doc['at'], doc['status']


def parse_err(frame: dict) -> tuple:
    code = frame.get('code')
    if code not in ERROR_CODES:
        return 'unknown', None, None
    author, seq = frame.get('author'), frame.get('seq')
    if not ev.is_author(author) or isinstance(seq, bool) or not isinstance(seq, int):
        return code, None, None
    return code, author, seq
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_protocol.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add edmrn/ext/crew_protocol.py tests/test_crew_protocol.py
git commit -m "feat(crew): relay protocol frames (B1 S6-S9)"
```

---

### Task 5: Shared test vectors

**Files:**
- Create: `tests/vectors/make_crew_vectors.py`, `crew-relay/test/vectors.json` (generated)
- Test: `tests/test_crew_vectors.py`

**Interfaces:**
- Consumes: Tasks 1–4 (the Python implementation under test). The generator itself imports only
  `cryptography` and the standard library, so the expected values do not come from the code under
  test.
- Produces: `crew-relay/test/vectors.json` with the top-level keys `version`, `now`, `group_id`,
  `keys`, `envelopes`, `structure_cases`, `signature_cases`, `canonical_cases`,
  `roster_payload_cases`, `plausibility_cases`, `roster_cases`, `auth`, `status`. Tasks 12–14 read
  it.
  - `keys`: name → `{seed, author}`, with `seed` as 64 hex characters.
  - `envelopes`: name → `{e, s, author, seq, kind}`, with `e` and `s` base64url.
  - `structure_cases` and `signature_cases`: `[{name, e, s?, author?, valid}]`.
  - `canonical_cases`: `[{name, e, s, canonical}]`.
  - `roster_payload_cases`: `[{kind, payload, valid}]`.
  - `plausibility_cases`: `[{created_at, plausible}]`, judged against `now`.
  - `roster_cases`: `[{name, leader, envelopes: [names], members: {author: last_seq|null}, name_of_group}]`.
  - `auth`: `{group, nonce, author, sig}`.
  - `status`: `{e, s, author}`.

- [ ] **Step 1: Write the generator** (`tests/vectors/make_crew_vectors.py`)

```python
"""Generate crew-relay/test/vectors.json, the cases client and relay must agree on (spec B1, Testing).

Deterministic: fixed seeds, ids and times; Ed25519 signatures are deterministic (RFC 8032).
Independent of edmrn: it builds bytes with the standard library and `cryptography` only.

    .venv/Scripts/python.exe tests/vectors/make_crew_vectors.py        # rewrite the file
tests/test_crew_vectors.py fails when the committed file differs from a fresh generation.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

OUT = Path(__file__).resolve().parents[2] / 'crew-relay' / 'test' / 'vectors.json'
NOW = '2026-10-13T12:00:00Z'
G = '01920000-0000-7000-8000-0000000000a1'
OTHER_G = '01920000-0000-7000-8000-0000000000ff'
TOKENS = {1: '11' * 32, 2: '22' * 32, 3: '33' * 32}


def uid(n: int) -> str:
    return f'01920000-0000-7000-8000-{n:012x}'


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def sha(hex_token: str) -> str:
    return hashlib.sha256(bytes.fromhex(hex_token)).hexdigest()


class Key:
    def __init__(self, name: str) -> None:
        self.seed = hashlib.sha256(b'edmrn-vector-' + name.encode()).digest()
        self.key = Ed25519PrivateKey.from_private_bytes(self.seed)
        self.author = b64(self.key.public_key().public_bytes(serialization.Encoding.Raw,
                                                             serialization.PublicFormat.Raw))

    def sign(self, data: bytes) -> bytes:
        return self.key.sign(data)


KEYS = {name: Key(name) for name in ('leader', 'alice', 'bob', 'carol', 'mallory')}
L, A, B, C, M = (KEYS[n] for n in ('leader', 'alice', 'bob', 'carol', 'mallory'))


def envelope(key: Key, seq: int, kind: str, at: str, payload: dict, event_id: str) -> dict:
    return {'v': 1, 'event_id': event_id, 'author': key.author, 'seq': seq, 'kind': kind,
            'created_at': at, 'payload': payload}


def roster(group=G, **payload) -> dict:
    return {'group_id': group, **payload}


SPECS = {
    'group': (L, 1, 'group', '2026-10-13T10:00:00Z', roster(name='Crew', commander='Leader'), uid(1)),
    'invite1': (L, 2, 'invite', '2026-10-13T10:01:00Z',
                roster(invite_id=uid(101), token_hash=sha(TOKENS[1]),
                       expires_at='2026-10-14T10:00:00Z', label='Alice'), uid(2)),
    'invite2': (L, 3, 'invite', '2026-10-13T10:01:30Z',
                roster(invite_id=uid(102), token_hash=sha(TOKENS[2]),
                       expires_at='2026-10-14T10:00:00Z', label=None), uid(3)),
    'invite3_expiring': (L, 4, 'invite', '2026-10-13T10:02:00Z',
                         roster(invite_id=uid(103), token_hash=sha(TOKENS[3]),
                                expires_at='2026-10-13T10:05:00Z', label=None), uid(4)),
    'remove_alice_5': (L, 5, 'remove', '2026-10-13T11:00:00Z',
                       roster(member=A.author, last_seq=5), uid(5)),
    'remove_alice_3': (L, 6, 'remove', '2026-10-13T11:01:00Z',
                       roster(member=A.author, last_seq=3), uid(6)),
    'remove_leader': (L, 7, 'remove', '2026-10-13T11:02:00Z',
                      roster(member=L.author, last_seq=0), uid(7)),
    'join_leader_own': (L, 8, 'join', '2026-10-13T10:01:45Z',
                        roster(invite_id=uid(102), token=TOKENS[2], commander='Leader'), uid(8)),
    'join_alice': (A, 1, 'join', '2026-10-13T10:02:00Z',
                   roster(invite_id=uid(101), token=TOKENS[1], commander='Alice'), uid(10)),
    'invite_by_alice': (A, 2, 'invite', '2026-10-13T10:02:30Z',
                        roster(invite_id=uid(120), token_hash=sha(TOKENS[1]),
                               expires_at='2026-10-14T10:00:00Z', label=None), uid(20)),
    'join_bob_same_invite': (B, 1, 'join', '2026-10-13T10:03:00Z',
                             roster(invite_id=uid(101), token=TOKENS[1], commander='Bob'), uid(11)),
    'join_bob_wrong_token': (B, 2, 'join', '2026-10-13T10:03:30Z',
                             roster(invite_id=uid(102), token='44' * 32, commander='Bob'), uid(12)),
    'join_bob_invite2': (B, 3, 'join', '2026-10-13T10:04:00Z',
                         roster(invite_id=uid(102), token=TOKENS[2], commander='Bob'), uid(13)),
    'join_bob_alice_invite': (B, 4, 'join', '2026-10-13T10:05:00Z',
                              roster(invite_id=uid(120), token=TOKENS[1], commander='Bob'), uid(21)),
    'join_bob_other_group': (B, 5, 'join', '2026-10-13T10:02:30Z',
                             roster(group=OTHER_G, invite_id=uid(101), token=TOKENS[1],
                                    commander='Bob'), uid(31)),
    'join_carol_expired': (C, 1, 'join', '2026-10-13T10:06:00Z',
                           roster(invite_id=uid(103), token=TOKENS[3], commander='Carol'), uid(14)),
    'join_carol_implausible': (C, 2, 'join', '2010-01-01T00:00:00Z',
                               roster(invite_id=uid(102), token=TOKENS[2], commander='Carol'), uid(15)),
    'group_by_mallory': (M, 1, 'group', '2014-12-16T00:00:00Z',
                         roster(name='Pwned', commander='M'), uid(30)),
    'visit_alice': (A, 3, 'visit', '2026-10-13T10:10:00Z',
                    {'commander': 'Alice', 'system': 'Sol', 'address': '10477373803',
                     'coords': [0.0, 0.0, 0.0]}, 'ab' * 16),
}


def sealed(name: str) -> tuple:
    key, seq, kind, at, payload, event_id = SPECS[name]
    data = canonical(envelope(key, seq, kind, at, payload, event_id))
    return key, seq, kind, data, key.sign(data)


def build() -> dict:
    envs = {}
    for name in SPECS:
        key, seq, kind, data, sig = sealed(name)
        envs[name] = {'e': b64(data), 's': b64(sig), 'author': key.author, 'seq': seq, 'kind': kind}

    group_obj = envelope(*SPECS['group'][:5], SPECS['group'][5])

    def signed_text(text: bytes, key: Key = L) -> dict:
        return {'e': b64(text), 's': b64(key.sign(text))}

    structure = []
    for name, mutate in [
        ('valid', lambda e: e),
        ('missing_payload', lambda e: {k: v for k, v in e.items() if k != 'payload'}),
        ('extra_key', lambda e: {**e, 'x': 1}),
        ('seq_zero', lambda e: {**e, 'seq': 0}),
        ('seq_too_big', lambda e: {**e, 'seq': 2 ** 53}),
        ('v_1001', lambda e: {**e, 'v': 1001}),
        ('author_42', lambda e: {**e, 'author': e['author'][:42]}),
        ('stamp_shape', lambda e: {**e, 'created_at': '2026-10-13 12:00:00'}),
        ('payload_list', lambda e: {**e, 'payload': []}),
        ('event_id_upper', lambda e: {**e, 'event_id': 'AB' * 16}),
        ('kind_upper', lambda e: {**e, 'kind': 'Visit'}),
    ]:
        structure.append({'name': name, 'e': b64(canonical(mutate(dict(group_obj)))),
                          'valid': name == 'valid'})

    _, _, _, group_data, group_sig = sealed('group')
    tampered = bytearray(group_data)
    tampered[-3] ^= 1
    small_order_env = canonical({**group_obj, 'author': b64(bytes(32))})
    signatures = [
        {'name': 'valid', 'e': b64(group_data), 's': b64(group_sig), 'author': L.author, 'valid': True},
        {'name': 'tampered', 'e': b64(bytes(tampered)), 's': b64(group_sig), 'author': L.author,
         'valid': False},
        {'name': 'wrong_key', 'e': b64(group_data), 's': b64(A.sign(group_data)), 'author': L.author,
         'valid': False},
        {'name': 'small_order_author', 'e': b64(small_order_env), 's': b64(bytes(64)),
         'author': b64(bytes(32)), 'valid': False},
    ]

    spaced = json.dumps(group_obj, sort_keys=True, separators=(', ', ': ')).encode()
    duplicate = group_data.replace(b'"kind":"group"', b'"kind":"group","kind":"group"', 1)
    float_seq = group_data.replace(b'"seq":1', b'"seq":1.0', 1)
    escaped = group_data.replace(b'"name":"Crew"', b'"name":"\\u0043rew"', 1)
    canonical_cases = [
        {'name': 'canonical', **signed_text(group_data), 'canonical': True},
        {'name': 'spaces', **signed_text(spaced), 'canonical': False},
        {'name': 'duplicate_key', **signed_text(duplicate), 'canonical': False},
        {'name': 'float_seq', **signed_text(float_seq), 'canonical': False},
        {'name': 'escaped_ascii', **signed_text(escaped), 'canonical': False},
    ]

    base_invite = roster(invite_id=uid(101), token_hash=sha(TOKENS[1]),
                         expires_at='2026-10-14T10:00:00Z', label='Alice')
    payload_cases = [
        {'kind': 'group', 'payload': roster(name='Crew', commander='Leader'), 'valid': True},
        {'kind': 'group', 'payload': roster(name='Ünïcode', commander='Leader'), 'valid': False},
        {'kind': 'group', 'payload': roster(name='x' * 65, commander='Leader'), 'valid': False},
        {'kind': 'group', 'payload': roster(name='', commander='Leader'), 'valid': False},
        {'kind': 'group', 'payload': roster(name='Crew', commander='Leader', extra=1), 'valid': False},
        {'kind': 'invite', 'payload': base_invite, 'valid': True},
        {'kind': 'invite', 'payload': {**base_invite, 'label': None}, 'valid': True},
        {'kind': 'invite', 'payload': {**base_invite, 'expires_at': '2026-02-30T00:00:00Z'}, 'valid': False},
        {'kind': 'invite', 'payload': {**base_invite, 'token_hash': 'AB' * 32}, 'valid': False},
        {'kind': 'join', 'payload': roster(invite_id=uid(101), token=TOKENS[1], commander='Bob'), 'valid': True},
        {'kind': 'join', 'payload': roster(invite_id='nope', token=TOKENS[1], commander='Bob'), 'valid': False},
        {'kind': 'remove', 'payload': roster(member=A.author, last_seq=0), 'valid': True},
        {'kind': 'remove', 'payload': roster(member=A.author, last_seq=-1), 'valid': False},
        {'kind': 'remove', 'payload': roster(member=A.author, last_seq=True), 'valid': False},
        {'kind': 'remove', 'payload': roster(member=A.author[:40], last_seq=1), 'valid': False},
    ]

    plausibility = [{'created_at': s, 'plausible': ok} for s, ok in [
        ('2026-10-13T12:00:00Z', True), ('2014-12-16T00:00:00Z', True),
        ('2014-12-15T23:59:59Z', False), ('2026-10-14T12:00:00Z', True),
        ('2026-10-14T12:00:01Z', False), ('2026-02-30T00:00:00Z', False),
        ('0000-01-01T00:00:00Z', False), ('2026-10-13T12:00:60Z', False),
        ('2026-10-13T24:00:00Z', False),
    ]]

    def case(name, names, members, group_name='Crew'):
        return {'name': name, 'leader': L.author, 'envelopes': names,
                'members': {KEYS[k].author: v for k, v in members.items()}, 'name_of_group': group_name}

    removal = ['group', 'invite1', 'join_alice', 'remove_alice_5', 'remove_alice_3', 'remove_leader']
    roster_cases = [
        case('basic', ['group', 'invite1', 'join_alice'], {'leader': None, 'alice': None}),
        case('second_join_same_invite', ['group', 'invite1', 'join_alice', 'join_bob_same_invite'],
             {'leader': None, 'alice': None}),
        case('wrong_token_does_not_block', ['group', 'invite2', 'join_bob_wrong_token', 'join_bob_invite2'],
             {'leader': None, 'bob': None}),
        case('expired', ['group', 'invite3_expiring', 'join_carol_expired'], {'leader': None}),
        case('remove_lowest_never_leader', removal, {'leader': None, 'alice': 3}),
        case('shuffled', list(reversed(removal)), {'leader': None, 'alice': 3}),
        case('non_leader_invite', ['group', 'invite_by_alice', 'join_bob_alice_invite'], {'leader': None}),
        case('foreign_group_envelope', ['group_by_mallory', 'group', 'invite1', 'join_alice'],
             {'leader': None, 'alice': None}),
        case('other_group_join', ['group', 'invite1', 'join_bob_other_group'], {'leader': None}),
        case('implausible_join_ignored', ['group', 'invite2', 'join_carol_implausible', 'join_bob_invite2'],
             {'leader': None, 'bob': None}),
        case('leader_join_ignored', ['group', 'invite2', 'join_leader_own', 'join_bob_invite2'],
             {'leader': None, 'bob': None}),
        case('no_group_envelope', ['invite1', 'join_alice'], {'leader': None, 'alice': None}, None),
    ]

    nonce = b64(bytes(range(32)))
    auth_bytes = f'edmrn-crew-auth\n{G}\n{nonce}'.encode('ascii')
    status_doc = canonical({'author': A.author, 'at': '2026-10-13T12:00:00Z', 'status': {
        'commander': 'Alice', 'system': 'Sol', 'address': '10477373803', 'coords': [0.0, 0.0, 0.0],
        'ship': 'anaconda', 'ship_name': None, 'ship_ident': None, 'edsy_url': None, 'hull': 0.5,
        'fuel_main': 10.0, 'fuel_reserve': 0.5, 'fuel_capacity_main': 32.0,
        'fuel_capacity_reserve': 0.63, 'paused': False}})

    return {
        'version': 1, 'now': NOW, 'group_id': G,
        'keys': {name: {'seed': key.seed.hex(), 'author': key.author} for name, key in KEYS.items()},
        'envelopes': envs, 'structure_cases': structure, 'signature_cases': signatures,
        'canonical_cases': canonical_cases, 'roster_payload_cases': payload_cases,
        'plausibility_cases': plausibility, 'roster_cases': roster_cases,
        'auth': {'group': G, 'nonce': nonce, 'author': L.author, 'sig': b64(L.sign(auth_bytes))},
        'status': {'e': b64(status_doc), 's': b64(A.sign(status_doc)), 'author': A.author},
    }


def render() -> str:
    return json.dumps(build(), indent=1, sort_keys=True, ensure_ascii=True) + '\n'


if __name__ == '__main__':
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding='utf-8', newline='\n')
    sys.stdout.write(f'wrote {OUT}\n')
```

- [ ] **Step 2: Write the failing test** (`tests/test_crew_vectors.py`)

```python
import base64
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from edmrn.ext import crew_events as ev
from edmrn.ext import crew_protocol as proto
from edmrn.ext.crew_identity import verify
from edmrn.ext.crew_roster import compute_roster, parse_roster_envelope

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
        try:
            ev.parse_envelope(raw(case["e"]))
            ok = True
        except ev.EnvelopeError:
            ok = False
        assert ok is case["valid"], case["name"]


def test_signature_cases(v):
    for case in v["signature_cases"]:
        assert verify(case["author"], raw(case["s"]), raw(case["e"])) is case["valid"], case["name"]


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


def test_auth_and_status(v):
    a = v["auth"]
    assert verify(a["author"], raw(a["sig"]), proto.auth_message(a["group"], a["nonce"]))
    author, _, status = proto.parse_status({"t": "st", "e": v["status"]["e"], "s": v["status"]["s"]})
    assert author == v["status"]["author"] and status["hull"] == 0.5
```

- [ ] **Step 3: Run the test and see it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_vectors.py -q`
Expected: `FileNotFoundError` for `crew-relay/test/vectors.json`.

- [ ] **Step 4: Generate the file and run again**

Run: `.venv/Scripts/python.exe tests/vectors/make_crew_vectors.py`, then
`.venv/Scripts/python.exe -m pytest tests/test_crew_vectors.py -q`.
Expected: all pass.

If a roster or canonical case fails, the Python implementation disagrees with the spec as written
in the generator. Fix the implementation (Task 2 or 3), not the expected value. Change an expected
value only when the spec says so, and quote the spec line in the commit message.

- [ ] **Step 5: Commit**

```bash
git add tests/vectors/make_crew_vectors.py tests/test_crew_vectors.py crew-relay/test/vectors.json
git commit -m "test(crew): shared client/relay vectors for envelopes, signatures, roster and plausibility"
```

---

### Task 6: Sync connection, inbox and catch-up

**Files:**
- Create: `edmrn/ext/crew_sync.py`, `tests/crew_sync_fakes.py`
- Modify: `requirements-edmrn.txt` (add `websockets`)
- Test: `tests/test_crew_sync.py`

**Interfaces:**
- Consumes:
  - Task 3: `CrewStore.heads`, `envelopes_after`, `own_roster_envelope`, `insert_received_batch`;
  - Task 4: all of `crew_protocol`;
  - Task 2: `GroupConfig`, `connection_url`;
  - Spec A: `Identity.sign` and `.author`.
- Produces (`edmrn.ext.crew_sync`):
  - `SocketClosed(code, reason)` and `websockets_connect(url)`, which returns an object with
    `send(text)`, `recv(timeout) -> str` (raises `TimeoutError` or `SocketClosed`) and
    `close(code=1000, reason='')`;
  - `BoardEntry(author, at, status)` and `CrewBoard` with `.update(author, at, status) -> bool`,
    `.snapshot() -> dict`, `.subscribe(observer)`;
  - `Inbox(crew_store, root)` with `.put(items, stop)`, `.drain(limit=None) -> int`, `.start()`,
    `.stop()`, `.pending`;
  - `CrewSync(crew_store, config, identity, inbox, board, status_source, *, connect, clock, wall,
    rand)`. `CrewStore.own_roster_envelope(author, kind, group_id)` works for any author despite its
    name; the re-seed offer uses it for friends' joins. It has `.start()`, `.stop()`, `.connect_once() -> str` ('live' | 'failed' | 'replaced' |
    'refused'), `.set_paused(bool)`, `.status_changed(*_)`, `.subscribe(observer(state, error))`,
    and the attributes `.state`, `.last_error`, `.paused`;
  - `PAUSED_STATUS`.

- [ ] **Step 1: Add the dependency and install it**

Append to `requirements-edmrn.txt`:

```
# Crew relay WebSocket client (spec B1 S19); BSD-3-Clause.
websockets>=15
```

Run: `uv pip install --python .venv/Scripts/python.exe "websockets>=15"` (or
`.venv/Scripts/python.exe -m pip install "websockets>=15"`).

Then check the API this plan relies on:
`.venv/Scripts/python.exe -c "import websockets, inspect; from websockets.sync.client import connect, ClientConnection; print(websockets.__version__); print(inspect.signature(ClientConnection.recv))"`.
Expected: a version ≥ 15, and `recv` with a `timeout` parameter. If the signature differs, adapt
`_WebSocketsAdapter.recv` and say so in the task report.

- [ ] **Step 2: Write the fakes** (`tests/crew_sync_fakes.py`)

```python
"""A scripted relay socket for crew sync tests: no threads, no network.

`FakeSocket.recv` hands out queued frames; when the queue is empty it raises SocketClosed(1006), so
CrewSync.connect_once() returns in the test thread. `on_send(sock, frame)` may queue replies.
"""
import json
from collections import deque

from edmrn.ext import crew_protocol as proto
from edmrn.ext.crew_sync import SocketClosed

NONCE = proto.b64(bytes(range(32)))


class FakeSocket:
    def __init__(self, on_send=None, frames=()):
        self.incoming = deque(proto.encode(f) for f in frames)
        self.sent = []
        self.closed = None
        self.on_send = on_send

    def push(self, frame):
        self.incoming.append(proto.encode(frame))

    def push_close(self, code, reason=""):
        self.incoming.append(SocketClosed(code, reason))

    def send(self, text):
        frame = json.loads(text)
        self.sent.append(frame)
        if self.on_send:
            self.on_send(self, frame)

    def recv(self, timeout):
        if not self.incoming:
            raise SocketClosed(1006, "script ended")
        item = self.incoming.popleft()
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self, code=1000, reason=""):
        self.closed = code

    def sent_of(self, t):
        return [f for f in self.sent if f["t"] == t]


def relay(heads=None, synced=True, extra=()):
    """on_send that answers auth like the relay: have (relay heads), synced, then `extra` frames."""
    def on_send(sock, frame):
        if frame["t"] == "auth":
            sock.push({"t": "have", "heads": dict(heads or {})})
            for f in extra:
                sock.push(f)
            if synced:
                sock.push({"t": "synced"})
    return on_send


def hello():
    return {"t": "hello", "v": 1, "source": "", "nonce": NONCE}


class Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


class RecordingStop:
    """Stands in for CrewSync._stop: records waits and stops after `limit` of them."""

    def __init__(self, limit):
        self.limit, self.waits = limit, []

    def is_set(self):
        return len(self.waits) >= self.limit

    def set(self):
        self.limit = 0

    def clear(self):
        pass

    def wait(self, seconds):
        self.waits.append(seconds)
        return self.is_set()
```

- [ ] **Step 3: Write the failing tests** (`tests/test_crew_sync.py`)

```python
import sqlite3
from datetime import datetime, timezone

import pytest

from crew_helpers import sealed, visit
from crew_sync_fakes import NONCE, Clock, FakeSocket, RecordingStop, hello, relay
from edmrn.ext import crew_events as ev
from edmrn.ext import crew_protocol as proto
from edmrn.ext.crew_identity import KeyRing, verify
from edmrn.ext.crew_roster import GroupConfig
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.crew_sync import CrewBoard, CrewSync, Inbox, SocketClosed
from edmrn.ext.store import Store

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
G = "01920000-0000-7000-8000-0000000000a1"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "edmrn.db")
    yield s
    s.close()


@pytest.fixture
def crew(store):
    return CrewStore(store, clock=lambda: NOW, admit=lambda a, s: True)


@pytest.fixture
def me(tmp_path):
    return KeyRing(tmp_path / "crew_keys").for_commander("ME")


@pytest.fixture
def friend(tmp_path):
    return KeyRing(tmp_path / "friend").for_commander("FR")


class Root:
    def after(self, ms, fn):
        return None


def make_sync(crew, me, sock, leader=None, clock=None, status=lambda: None, paused=False):
    config = GroupConfig("wss://galroute.the-hub.cloud", G, leader or "L" * 43, me.author, None, paused)
    inbox = Inbox(crew, Root())
    sync = CrewSync(crew, config, me, inbox, CrewBoard(), status, connect=lambda url: sock,
                    clock=clock or Clock(), wall=lambda: "2026-10-13T12:00:00Z", rand=lambda: 0.5)
    return sync, inbox


def own_visits(crew, me, count):
    crew.insert_received_batch([sealed(me, visit(at=f"2026-10-13T09:{n // 60:02d}:{n % 60:02d}Z",
                                                 address=str(n)), n) for n in range(1, count + 1)])


def test_auth_signs_the_nonce_then_sends_have(crew, me):
    sock = FakeSocket(relay(), [hello()])
    sync, _ = make_sync(crew, me, sock)
    assert sync.connect_once() == "live"
    auth, have = sock.sent[0], sock.sent[1]
    assert auth["t"] == "auth" and auth["group"] == G and auth["author"] == me.author
    assert verify(me.author, proto.unb64(auth["sig"], 64), proto.auth_message(G, NONCE))
    assert have == {"t": "have", "heads": {}}
    assert "join" not in auth and "genesis" not in auth


def test_a_member_auth_carries_its_join_and_a_leader_auth_its_genesis(crew, me):
    join = ev.build_envelope(ev.uuid7(), me.author, 1, "join", "2026-10-13T10:00:00Z",
                             {"group_id": G, "invite_id": ev.uuid7(), "token": "11" * 32, "commander": "Me"})
    crew.insert_received_batch([ev.seal(join, me.sign)])
    sock = FakeSocket(relay(), [hello()])
    make_sync(crew, me, sock)[0].connect_once()
    assert "join" in sock.sent[0]
    genesis = ev.build_envelope(ev.uuid7(), me.author, 2, "group", "2026-10-13T10:00:00Z",
                                {"group_id": G, "name": "Crew", "commander": "Me"})
    crew.insert_received_batch([ev.seal(genesis, me.sign)])
    sock = FakeSocket(relay(), [hello()])
    make_sync(crew, me, sock, leader=me.author)[0].connect_once()
    assert "genesis" in sock.sent[0] and "join" not in sock.sent[0]


def test_catch_up_uploads_what_the_relay_lacks_in_pages_and_order(crew, me):
    own_visits(crew, me, 250)
    sock = FakeSocket(relay(heads={me.author: 100}), [hello()])
    sync, _ = make_sync(crew, me, sock)
    sync.connect_once()
    frames = sock.sent_of("evs")
    seqs = [ev.parse_envelope(d)["seq"] for f in frames for d, _ in proto.parse_evs(f)]
    assert seqs == list(range(101, 251))
    assert all(len(proto.encode(f)) <= proto.MAX_EVS_BYTES + 64 for f in frames)


def test_only_authors_the_relay_lists_are_uploaded(crew, me, friend):
    own_visits(crew, me, 3)
    crew.insert_received_batch([sealed(friend, visit(at="2026-10-13T09:00:00Z"), 1)])
    sock = FakeSocket(relay(heads={me.author: 0}), [hello()])
    make_sync(crew, me, sock)[0].connect_once()
    authors = {ev.parse_envelope(d)["author"] for f in sock.sent_of("evs") for d, _ in proto.parse_evs(f)}
    assert authors == {me.author}


def test_an_emptied_relay_is_offered_the_joins_of_unlisted_members(crew, me, friend):
    join = ev.build_envelope(ev.uuid7(), friend.author, 4, "join", "2026-10-13T10:00:00Z",
                             {"group_id": G, "invite_id": ev.uuid7(), "token": "11" * 32, "commander": "Fr"})
    crew.insert_received_batch([ev.seal(join, friend.sign)])
    sock = FakeSocket(relay(heads={me.author: 0}), [hello()])
    make_sync(crew, me, sock)[0].connect_once()
    offered = [ev.parse_envelope(d) for f in sock.sent_of("evs") for d, _ in proto.parse_evs(f)]
    assert [(e["author"], e["kind"]) for e in offered] == [(friend.author, "join")]


def test_a_fresh_have_uploads_a_newly_listed_author_without_resending(crew, me, friend):
    from crew_helpers import sealed, visit
    crew.insert_received_batch([sealed(friend, visit(at="2026-10-13T09:00:00Z", address=str(n)), n)
                                for n in (1, 2)])
    own_visits(crew, me, 2)

    def on_send(sock, frame):
        if frame["t"] == "auth":
            sock.push({"t": "have", "heads": {me.author: 0}})
            sock.push({"t": "have", "heads": {me.author: 0, friend.author: 0}})
    sock = FakeSocket(on_send, [hello()])
    make_sync(crew, me, sock)[0].connect_once()
    sent = [(e["author"], e["seq"]) for f in sock.sent_of("evs")
            for e in (ev.parse_envelope(d) for d, _ in proto.parse_evs(f))]
    assert sent.count((me.author, 1)) == 1 and (friend.author, 2) in sent


def test_an_error_for_an_author_stops_its_uploads_for_the_connection(crew, me):
    own_visits(crew, me, 3)
    clock = Clock()

    def on_send(sock, frame):
        if frame["t"] == "auth":
            sock.push({"t": "err", "code": "quota", "author": me.author, "seq": 1})
            sock.push({"t": "have", "heads": {me.author: 0}})
    sock = FakeSocket(on_send, [hello()])
    make_sync(crew, me, sock, clock=clock)[0].connect_once()
    assert sock.sent_of("evs") == []


def test_received_evs_reach_the_store_through_the_inbox(crew, me, friend):
    data, sig = sealed(friend, visit(at="2026-10-13T09:00:00Z"), 1)
    sock = FakeSocket(relay(extra=[{"t": "evs", "items": [{"e": proto.b64(data), "s": proto.b64(sig)}]}]),
                      [hello()])
    sync, inbox = make_sync(crew, me, sock)
    sync.connect_once()
    assert inbox.pending == 1 and crew.heads() == {}
    assert inbox.drain() == 1 and crew.heads() == {friend.author: 1}


def test_inbox_requeues_on_a_locked_store(me, friend):
    class Locked:
        def insert_received_batch(self, items):
            raise sqlite3.OperationalError("database is locked")
    inbox = Inbox(Locked(), Root())
    item = sealed(friend, visit(), 1)
    inbox._queue.extend([item, item])
    assert inbox.drain() == 0 and inbox.pending == 2


def test_backoff_doubles_to_five_minutes_and_has_jitter(crew, me):
    sync, _ = make_sync(crew, me, None)
    sync._connect = lambda url: (_ for _ in ()).throw(OSError("offline"))
    sync._stop = RecordingStop(limit=11)
    sync._run()
    assert sync._stop.waits == [1, 2, 4, 8, 16, 32, 64, 128, 256, 300, 300]
    sync._rand = lambda: 1.0
    sync._stop = RecordingStop(limit=1)
    sync._run()
    assert sync._stop.waits == [pytest.approx(1.2)]


def test_a_refusal_stops_reconnecting(crew, me):
    sock = FakeSocket(None, [hello()])
    sock.push_close(proto.CLOSE_FORBIDDEN, "not_member")
    sync, _ = make_sync(crew, me, sock)
    sync._stop = RecordingStop(limit=5)
    sync._run()
    assert sync.state == "refused" and sync._stop.waits == []


def test_a_replaced_connection_waits_a_minute(crew, me):
    sock = FakeSocket(relay(), [hello()])
    sock.on_send = None
    sock.push_close(proto.CLOSE_REPLACED, "replaced")
    sync, _ = make_sync(crew, me, sock)
    sync._stop = RecordingStop(limit=1)
    sync._run()
    assert sync._stop.waits == [60]


def test_a_bad_hello_fails_the_attempt(crew, me):
    sock = FakeSocket(None, [{"t": "hello", "v": 2, "nonce": NONCE}])
    sync, _ = make_sync(crew, me, sock)
    assert sync.connect_once() == "failed" and "version" in sync.last_error
```

- [ ] **Step 4: Run them and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_sync.py -q`
Expected: `ModuleNotFoundError: No module named 'edmrn.ext.crew_sync'`.

- [ ] **Step 5: Implement `edmrn/ext/crew_sync.py`**

```python
"""Crew sync: the relay connection, the Tk-loop inbox and the status board (spec B1 S6-S9, S19).

The connection thread never writes edmrn.db. Received envelopes go to the Inbox, which the Tk loop
drains through CrewStore.insert_received_batch. Uploads read the log in pages through Store.read(),
so the Tk thread never waits long for the store lock.
"""
from __future__ import annotations

import random
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Optional

from edmrn.ext import crew_events as ev
from edmrn.ext import crew_protocol as proto
from edmrn.ext.crew_roster import GroupConfig, connection_url
from edmrn.logger import get_logger

logger = get_logger('CrewSync')

USER_AGENT = 'EDMRN crew sync'
PAUSED_STATUS = {**{name: None for name in ev.STATUS_FIELDS}, 'paused': True}
_STOPPING_ERRORS = ('quota', 'group_quota', 'not_member', 'removed', 'bad_sig', 'too_big')


class SocketClosed(Exception):
    def __init__(self, code: Optional[int] = None, reason: str = '') -> None:
        super().__init__(f'closed ({code}) {reason}'.strip())
        self.code = code
        self.reason = reason


def _close_info(exc: Exception) -> tuple:
    received = getattr(exc, 'rcvd', None)
    return getattr(received, 'code', None), getattr(received, 'reason', '') or ''


class _WebSocketsAdapter:
    def __init__(self, ws: Any, closed_type: type) -> None:
        self._ws = ws
        self._closed = closed_type

    def send(self, text: str) -> None:
        try:
            self._ws.send(text)
        except self._closed as e:
            raise SocketClosed(*_close_info(e)) from None

    def recv(self, timeout: float) -> str:
        try:
            message = self._ws.recv(timeout=timeout)
        except self._closed as e:
            raise SocketClosed(*_close_info(e)) from None
        if not isinstance(message, str):
            raise proto.FrameError('the relay sent a binary frame')
        return message

    def close(self, code: int = 1000, reason: str = '') -> None:
        try:
            self._ws.close(code, reason)
        except Exception:
            pass


def websockets_connect(url: str) -> _WebSocketsAdapter:
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect
    ws = connect(url, open_timeout=10, close_timeout=5, max_size=proto.MAX_FRAME_BYTES,
                 ping_interval=30, ping_timeout=30, user_agent_header=USER_AGENT)
    return _WebSocketsAdapter(ws, ConnectionClosed)


@dataclass(frozen=True)
class BoardEntry:
    author: str
    at: str
    status: dict


class CrewBoard:
    """Friends' latest statuses, in memory only (S8). Observers run on the sync thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, BoardEntry] = {}
        self._observers: list = []

    def update(self, author: str, at: str, status: dict) -> bool:
        with self._lock:
            held = self._entries.get(author)
            if held is not None and held.at > at:
                return False
            entry = BoardEntry(author, at, status)
            self._entries[author] = entry
        for observer in list(self._observers):
            try:
                observer(entry)
            except Exception as e:
                logger.error(f'Crew board observer failed: {e}')
        return True

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._entries)

    def subscribe(self, observer: Callable[[BoardEntry], None]) -> None:
        self._observers.append(observer)


class Inbox:
    """Received envelopes waiting for the Tk loop (S19); the writer's pattern from Spec A."""

    INTERVAL_MS = 250
    BATCH = 200
    CAP = 5000

    def __init__(self, crew_store: Any, root: Any) -> None:
        self._crew_store = crew_store
        self._root = root
        self._queue: deque = deque()
        self._lock = threading.Lock()
        self._space = threading.Condition(self._lock)
        self._running = False
        self._locked = False
        self._rejected_logged = False

    @property
    def pending(self) -> int:
        return len(self._queue)

    def put(self, items: list, stop: threading.Event) -> None:
        """Sync thread. Waits while the inbox is full and the applier runs: socket backpressure."""
        with self._space:
            while self._running and len(self._queue) >= self.CAP and not stop.is_set():
                self._space.wait(0.5)
            self._queue.extend(items)

    def start(self) -> None:
        self._running = True
        self._schedule()

    def stop(self) -> None:
        self._running = False
        with self._space:
            self._space.notify_all()

    def _schedule(self) -> None:
        if not self._running:
            return
        try:
            self._root.after(self.INTERVAL_MS, self._tick)
        except Exception as e:  # the Tk loop is gone: the app is closing
            self._running = False
            logger.debug(f'Crew inbox stopped: {e}')

    def _tick(self) -> None:
        try:
            self.drain(self.BATCH)
        except Exception as e:
            logger.error(f'Crew inbox tick failed: {e}')
        self._schedule()

    def drain(self, limit: Optional[int] = None) -> int:
        """Store queued envelopes now (Tk thread). Returns how many were new."""
        with self._space:
            count = len(self._queue) if limit is None else min(limit, len(self._queue))
            batch = [self._queue.popleft() for _ in range(count)]
            self._space.notify_all()
        if not batch:
            return 0
        try:
            result = self._crew_store.insert_received_batch(batch)
        except sqlite3.OperationalError as e:
            if any(word in str(e).lower() for word in ('locked', 'busy')):
                with self._lock:
                    self._queue.extendleft(reversed(batch))
                if not self._locked:
                    self._locked = True
                    logger.warning(f'Crew store is locked ({e}); {len(batch)} received envelopes wait')
                return 0
            return self._one_by_one(batch, e)
        except Exception as e:
            return self._one_by_one(batch, e)
        self._locked = False
        if result.rejected and not self._rejected_logged:
            self._rejected_logged = True
            logger.warning(f'Crew sync refused {len(result.rejected)} received envelopes, '
                           f'for example: {result.rejected[0][1]}')
        return result.inserted

    def _one_by_one(self, batch: list, error: Exception) -> int:
        logger.warning(f'Crew inbox batch of {len(batch)} failed ({error}); retrying one by one')
        inserted = 0
        for item in batch:
            try:
                inserted += self._crew_store.insert_received_batch([item]).inserted
            except Exception as e:
                logger.error(f'Crew received envelope dropped: {e}')
        return inserted


class _Session:
    """One connection: hello, auth, have, then frames until the socket closes or sync stops."""

    def __init__(self, sync: 'CrewSync', ws: Any) -> None:
        self.sync = sync
        self.ws = ws
        self.relay_heads: Optional[dict] = None
        self.blocked: set[str] = set()
        self.logged: set[str] = set()
        self.reached_live = False
        self.last_flush = float('-inf')
        self.last_status_at = float('-inf')
        self.last_status: Optional[dict] = None
        self.offered: set[str] = set()

    def send(self, frame: dict) -> None:
        self.ws.send(proto.encode(frame))

    def log_once(self, key: str, message: str) -> None:
        if key not in self.logged:
            self.logged.add(key)
            logger.warning(f'Crew sync: {message}')

    def run(self) -> None:
        sync = self.sync
        frame = proto.decode(self.ws.recv(sync.HELLO_TIMEOUT))
        if frame['t'] != 'hello':
            raise proto.FrameError('the relay did not start with hello')
        nonce = proto.parse_hello(frame)
        config, identity, store = sync._config, sync._identity, sync._store
        signature = identity.sign(proto.auth_message(config.group_id, nonce))
        genesis = join = None
        if config.is_leader:
            genesis = store.own_roster_envelope(identity.author, 'group', config.group_id)
        else:
            join = store.own_roster_envelope(identity.author, 'join', config.group_id)
        self.send(proto.auth_frame(config.group_id, identity.author, signature, genesis, join))
        self.send(proto.have_frame(store.heads()))
        sync._set_state('syncing')
        while not sync._stop.is_set():
            try:
                text = self.ws.recv(sync.RECV_TIMEOUT)
            except TimeoutError:
                text = None
            if text is not None:
                self.handle(proto.decode(text))
            self.tick()

    def handle(self, frame: dict) -> None:
        kind = frame['t']
        if kind == 'have':
            heads = proto.parse_have(frame)
            if self.relay_heads is None:
                self.relay_heads = heads
            else:  # a fresh have after an admission (S7): keep what this session already sent
                for author, seq in heads.items():
                    self.relay_heads[author] = max(seq, self.relay_heads.get(author, 0))
            self.upload()
        elif kind == 'evs':
            self.sync._inbox.put(proto.parse_evs(frame), self.sync._stop)
        elif kind == 'synced':
            self.reached_live = True
            self.sync._set_state('live')
        elif kind == 'st':
            self.receive_status(frame)
        elif kind == 'err':
            code, author, seq = proto.parse_err(frame)
            detail = f' for {author[:12]}... seq {seq}' if author else ''
            self.log_once(code, f'the relay answered {code}{detail}')
            if author is not None and code in _STOPPING_ERRORS:
                self.blocked.add(author)

    def tick(self) -> None:
        if self.relay_heads is None:
            return
        now = self.sync._clock()
        if now - self.last_flush >= self.sync.FLUSH_INTERVAL:
            self.upload()
        self.maybe_status(now)

    def upload(self) -> None:
        """Send, per author in ascending seq, what the relay lacks (S7). Paused: nothing own."""
        sync = self.sync
        self.last_flush = sync._clock()
        if self.relay_heads is None:
            return
        local = sync._store.heads()
        member = sync._identity.author
        for author in sorted(self.relay_heads):
            if author in self.blocked or (sync._paused and author == member):
                continue
            head = self.relay_heads[author]
            while local.get(author, 0) > head and not sync._stop.is_set():
                rows = sync._store.envelopes_after(author, head, proto.MAX_EVS_ITEMS)
                if not rows:
                    break
                for frame in proto.evs_frames([(data, sig) for _, data, sig in rows]):
                    self.send(frame)
                head = rows[-1][0]
                self.relay_heads[author] = head
        self.offer_joins(local)

    def offer_joins(self, local: dict) -> None:
        """Re-seed (S7): once per connection, offer the join of every author the relay does not list."""
        sync = self.sync
        for author in sorted(set(local) - set(self.relay_heads) - self.offered):
            self.offered.add(author)
            pair = sync._store.own_roster_envelope(author, 'join', sync._config.group_id)
            if pair is not None:
                self.send({'t': 'evs', 'items': [{'e': proto.b64(pair[0]), 's': proto.b64(pair[1])}]})

    def receive_status(self, frame: dict) -> None:
        """Task 7 fills this in."""

    def maybe_status(self, now: float) -> None:
        """Task 7 fills this in."""


class CrewSync:
    RECV_TIMEOUT = 1.0
    HELLO_TIMEOUT = 10.0
    FLUSH_INTERVAL = 5.0
    STATUS_INTERVAL = 30.0
    BACKOFF_FIRST = 1.0
    BACKOFF_MAX = 300.0
    REPLACED_WAIT = 60.0
    JITTER = 0.2

    def __init__(self, crew_store: Any, config: GroupConfig, identity: Any, inbox: Inbox,
                 board: CrewBoard, status_source: Callable[[], Optional[dict]], *,
                 connect: Callable[[str], Any] = websockets_connect,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], str] = ev.utc_now_stamp,
                 rand: Callable[[], float] = random.random) -> None:
        self._store = crew_store
        self._config = config
        self._identity = identity
        self._inbox = inbox
        self._board = board
        self._status_source = status_source
        self._connect = connect
        self._clock = clock
        self._wall = wall
        self._rand = rand
        self._paused = config.paused
        self._status_dirty = True
        self._stop: Any = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ws: Any = None
        self._observers: list = []
        self.state = 'stopped'
        self.last_error: Optional[str] = None

    @property
    def paused(self) -> bool:
        return self._paused

    def subscribe(self, observer: Callable[[str, Optional[str]], None]) -> None:
        self._observers.append(observer)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='edmrn-crew-sync', daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            ws.close(1000, 'bye')
        if self._thread is not None:
            self._thread.join(timeout)

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        self._status_dirty = True

    def status_changed(self, *_: Any) -> None:
        self._status_dirty = True

    def _set_state(self, state: str, error: Optional[str] = None) -> None:
        self.state = state
        if error is not None:
            self.last_error = error
        for observer in list(self._observers):
            try:
                observer(state, self.last_error)
            except Exception as e:
                logger.error(f'Crew sync observer failed: {e}')

    def _note(self, message: str) -> None:
        self.last_error = message
        logger.info(f'Crew sync: {message}')

    def _run(self) -> None:
        delay = self.BACKOFF_FIRST
        while not self._stop.is_set():
            self._set_state('connecting')
            outcome = self.connect_once()
            if outcome == 'refused' or self._stop.is_set():
                break
            if outcome == 'live':
                delay = self.BACKOFF_FIRST
            if outcome == 'replaced':
                wait = self.REPLACED_WAIT
            else:
                wait = delay * (1 + self.JITTER * (2 * self._rand() - 1))
            self._set_state('waiting')
            if self._stop.wait(wait):
                break
            if outcome != 'live':
                delay = min(delay * 2, self.BACKOFF_MAX)
        if self.state != 'refused':
            self._set_state('stopped')

    def connect_once(self) -> str:
        """One connection attempt: 'live', 'failed', 'replaced' or 'refused' (S9)."""
        try:
            ws = self._connect(connection_url(self._config.url, self._config.group_id))
        except Exception as e:
            self._note(f'cannot reach the relay: {e}')
            return 'failed'
        self._ws = ws
        session = _Session(self, ws)
        try:
            session.run()
        except SocketClosed as e:
            if e.code in (proto.CLOSE_BAD_AUTH, proto.CLOSE_FORBIDDEN):
                self._set_state('refused', f'The relay refused this member ({e.reason or e.code}).')
                return 'refused'
            if e.code == proto.CLOSE_REPLACED:
                self._note('another EDMRN with this commander took over the connection')
                return 'replaced'
            self._note(f'the connection closed ({e.code})')
        except (proto.FrameError, TimeoutError, OSError) as e:
            self._note(f'the connection failed: {e}')
        finally:
            self._ws = None
            ws.close()
        return 'live' if session.reached_live else 'failed'
```

   The `test_backoff…` test replaces `_connect` and `_stop` on the instance. Keep those attribute
   names.

- [ ] **Step 6: Run the tests and see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_sync.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add edmrn/ext/crew_sync.py tests/crew_sync_fakes.py tests/test_crew_sync.py requirements-edmrn.txt
git commit -m "feat(crew): relay connection thread, inbox and head-based catch-up (B1 S7 S9 S19)"
```

---

### Task 7: Status sharing and pause

**Files:**
- Modify: `edmrn/ext/crew_sync.py`
- Test: `tests/test_crew_sync_status.py`

**Interfaces:**
- Consumes: Task 6's `_Session` and `CrewSync`; `proto.status_bytes`, `proto.status_frame`,
  `proto.parse_status`; `ev.validate_status`, `ev.EDSY_MAX`.
- Produces:
  - `_Session.maybe_status(now)` and `_Session.receive_status(frame)`, implemented;
  - `status_from(location, ship) -> dict`, the S8 fields without `paused`;
  - `make_status_source(service, member_path) -> Callable[[], Optional[dict]]`. It returns `None`
    unless the active commander's key file is the member's. `service` needs `.commander`,
    `.status()` and `.keyring.path_for(commander)`.

- [ ] **Step 1: Write the failing tests** (`tests/test_crew_sync_status.py`)

```python
import json
from types import SimpleNamespace

import pytest

from crew_sync_fakes import Clock, FakeSocket, hello, relay
from edmrn.ext import crew_protocol as proto
from edmrn.ext.crew_identity import KeyRing
from edmrn.ext.crew_roster import GroupConfig
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.crew_sync import (PAUSED_STATUS, CrewBoard, CrewSync, Inbox, make_status_source,
                                 status_from)
from edmrn.ext.store import Store

G = "01920000-0000-7000-8000-0000000000a1"
STATUS = {"commander": "Me", "system": "Sol", "address": "10477373803", "coords": [0.0, 0.0, 0.0],
          "ship": "anaconda", "ship_name": None, "ship_ident": None, "edsy_url": None, "hull": 1.0,
          "fuel_main": 32.0, "fuel_reserve": 0.6, "fuel_capacity_main": 32.0,
          "fuel_capacity_reserve": 0.63}


@pytest.fixture
def crew(tmp_path):
    s = Store(tmp_path / "edmrn.db")
    yield CrewStore(s, admit=lambda a, q: True)
    s.close()


@pytest.fixture
def me(tmp_path):
    return KeyRing(tmp_path / "crew_keys").for_commander("ME")


class Root:
    def after(self, ms, fn):
        return None


class Ticking(FakeSocket):
    """Advances the clock by `step` seconds on every recv, so the session's timers run."""

    def __init__(self, clock, step, ticks, **kw):
        super().__init__(**kw)
        self.clock, self.step, self.ticks = clock, step, ticks

    def recv(self, timeout):
        self.clock.now += self.step
        if not self.incoming and self.ticks > 0:
            self.ticks -= 1
            raise TimeoutError
        return super().recv(timeout)


def run(crew, me, sock, clock, source, paused=False):
    config = GroupConfig("wss://h", G, "L" * 43, me.author, None, paused)
    board = CrewBoard()
    sync = CrewSync(crew, config, me, Inbox(crew, Root()), board, source, connect=lambda u: sock,
                    clock=clock, wall=lambda: "2026-10-13T12:00:00Z", rand=lambda: 0.5)
    sync.connect_once()
    return sync, board


def statuses(sock):
    return [json.loads(proto.unb64(f["e"], 8192))["status"] for f in sock.sent_of("st")]


def test_status_goes_out_at_start_then_only_on_change_and_at_most_every_30s(crew, me):
    clock = Clock()
    current = dict(STATUS)
    holder = {}
    answer = relay()

    def on_send(sock, frame):
        answer(sock, frame)
        if frame["t"] == "st" and len(sock.sent_of("st")) == 1:
            current["hull"] = 0.5          # the ship changes right after the first status
            holder["sync"].status_changed()

    sock = Ticking(clock, 10, ticks=8, on_send=on_send, frames=[hello()])
    config = GroupConfig("wss://h", G, "L" * 43, me.author, None, False)
    sync = CrewSync(crew, config, me, Inbox(crew, Root()), CrewBoard(), lambda: dict(current),
                    connect=lambda u: sock, clock=clock, wall=lambda: "2026-10-13T12:00:00Z",
                    rand=lambda: 0.5)
    holder["sync"] = sync
    sync.connect_once()
    sent = statuses(sock)
    assert [s["hull"] for s in sent] == [1.0, 0.5]
    assert all(s["paused"] is False for s in sent)


def test_pause_sends_one_paused_status_and_no_own_envelopes(crew, me):
    from crew_helpers import sealed, visit
    crew.insert_received_batch([sealed(me, visit(at="2026-10-13T09:00:00Z"), 1)])
    clock = Clock()
    sock = Ticking(clock, 40, ticks=4, on_send=relay(heads={me.author: 0}), frames=[hello()])
    run(crew, me, sock, clock, lambda: dict(STATUS), paused=True)
    assert statuses(sock) == [PAUSED_STATUS]
    assert sock.sent_of("evs") == []


def test_an_alt_commander_never_sends_status(tmp_path):
    ring = KeyRing(tmp_path / "keys")
    member = ring.for_commander("Main")
    ship = SimpleNamespace(commander="Alt", ship="sidewinder", ship_name=None, ship_ident=None,
                           edsy_url=None, hull=1.0, fuel_main=2.0, fuel_reserve=0.3,
                           fuel_capacity_main=2.0, fuel_capacity_reserve=0.3)
    service = SimpleNamespace(commander="Alt", keyring=ring, status=lambda: (None, ship))
    assert make_status_source(service, member.path)() is None
    service.commander = "Main"
    ship.commander = "Main"
    assert make_status_source(service, member.path)()["ship"] == "sidewinder"


def test_status_from_maps_location_and_ship():
    location = SimpleNamespace(commander="Me", system="Sol", system_address=10477373803,
                               coords=(1.5, -2.0, 3.25))
    ship = SimpleNamespace(commander="Me", ship="anaconda", ship_name="Mule", ship_ident="MU-01",
                           edsy_url="https://edsy.org/#/I=" + "a" * 7000, hull=0.9, fuel_main=10.0,
                           fuel_reserve=0.5, fuel_capacity_main=32.0, fuel_capacity_reserve=0.63)
    status = status_from(location, ship)
    assert status["address"] == "10477373803" and status["coords"] == [1.5, -2.0, 3.25]
    assert status["edsy_url"] is None
    assert status_from(None, None)["system"] is None


def test_received_status_updates_the_board_and_older_ones_are_dropped(crew, me, tmp_path):
    friend = KeyRing(tmp_path / "f").for_commander("FR")

    def st(at, hull):
        doc = {"author": friend.author, "at": at, "status": {**STATUS, "hull": hull, "paused": False}}
        data = proto.status_bytes(doc)
        return proto.status_frame(data, friend.sign(data))
    clock = Clock()
    sock = FakeSocket(relay(extra=[st("2026-10-13T12:00:10Z", 0.7), st("2026-10-13T12:00:05Z", 0.1)]),
                      [hello()])
    _, board = run(crew, me, sock, clock, lambda: None)
    assert board.snapshot()[friend.author].status["hull"] == 0.7
```

  The first test is deliberately explicit about the timeline: the relay's `have` arrives, the
  first status goes out, the source changes, `status_changed()` marks it dirty, and the next
  status may go out only 30 s after the first. The `Ticking` socket advances the fake clock 10 s
  per receive.

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_sync_status.py -q`
Expected: `ImportError: cannot import name 'make_status_source'`.

- [ ] **Step 3: Implement** in `edmrn/ext/crew_sync.py`

Replace the two placeholder methods of `_Session`:

```python
    def receive_status(self, frame: dict) -> None:
        try:
            author, at, status = proto.parse_status(frame)
        except proto.FrameError as e:
            self.log_once('st', f'a status was dropped: {e}')
            return
        if author != self.sync._identity.author:
            self.sync._board.update(author, at, status)

    def maybe_status(self, now: float) -> None:
        """S8: on change, at most every 30 s; paused sends PAUSED_STATUS once."""
        sync = self.sync
        if now - self.last_status_at < sync.STATUS_INTERVAL:
            return
        if sync._paused:
            status = PAUSED_STATUS
        else:
            if not sync._status_dirty and self.last_status is not None:
                return
            sync._status_dirty = False
            built = sync._status_source()
            if built is None:
                return
            status = {**built, 'paused': False}
        if status == self.last_status:
            return
        doc = {'author': sync._identity.author, 'at': sync._wall(), 'status': status}
        try:
            ev.validate_status(doc)
            data = proto.status_bytes(doc)
        except (ev.PayloadError, proto.FrameError) as e:
            self.log_once('own-status', f'own status not sent: {e}')
            return
        self.send(proto.status_frame(data, sync._identity.sign(data)))
        self.last_status = status
        self.last_status_at = now
```

Add the module-level helpers:

```python
_SHIP_FIELDS = ('ship', 'ship_name', 'ship_ident', 'hull', 'fuel_main', 'fuel_reserve',
                'fuel_capacity_main', 'fuel_capacity_reserve')


def status_from(location: Any, ship: Any) -> dict:
    """The S8 status fields, without `paused`, from a LocationState and a ShipStatus (either None)."""
    address = getattr(location, 'system_address', None)
    coords = getattr(location, 'coords', None)
    edsy = getattr(ship, 'edsy_url', None)
    status = {
        'commander': getattr(ship, 'commander', None) or getattr(location, 'commander', None),
        'system': getattr(location, 'system', None),
        'address': None if address is None else str(address),
        'coords': None if coords is None else [float(c) for c in coords],
        'edsy_url': edsy if isinstance(edsy, str) and len(edsy) <= ev.EDSY_MAX else None,
    }
    for name in _SHIP_FIELDS:
        status[name] = getattr(ship, name, None)
    return status


def make_status_source(service: Any, member_path: Any) -> Callable[[], Optional[dict]]:
    """Status of the active commander, but only if it is the group member (Review Focus 2)."""
    def source() -> Optional[dict]:
        commander = service.commander
        if commander is None or service.keyring.path_for(commander) != member_path:
            return None
        location, ship = service.status()
        return status_from(location, ship)
    return source
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_sync_status.py tests/test_crew_sync.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add edmrn/ext/crew_sync.py tests/test_crew_sync_status.py
git commit -m "feat(crew): signed status sharing, pause, board of friends' statuses (B1 S8)"
```

---
### Task 8: Redacted request logging and the Discord invite post

**Files:**
- Modify: `edmrn/ext/api.py`
- Create: `edmrn/ext/crew_discord.py`
- Test: `tests/test_api.py` (extend), `tests/test_crew_discord.py`

**Interfaces:**
- Consumes: `api.call`, `api.failure_from_response`, `api.ApiError`; Task 2's `check_webhook` and
  `redact_webhook`.
- Produces:
  - `api.call(..., log_url: Optional[str] = None)`: when given, log lines show `log_url` instead of
    `url`;
  - `crew_discord.invite_message(group_name, label, expires_at, link) -> dict`;
  - `crew_discord.post_invite(webhook_url, message, *, call=api.call, sleep=time.sleep) -> None`,
    which raises `api.ApiError` or `crew_roster.InviteError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_call_logs_the_redacted_url_only(caplog):
    import logging
    import requests
    from edmrn.ext import api

    def offline(*a, **k):
        raise requests.exceptions.ConnectionError("down")
    secret = "https://discord.com/api/webhooks/1/SECRET-TOKEN"
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(api.ApiError):
            api.call("POST", secret, service="Discord", timeout=1, retries=0, transport=offline,
                     log_url="https://discord.com/api/webhooks/1/***")
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "SECRET-TOKEN" not in text and "webhooks/1/***" in text
```

(If `pytest` is not yet imported at the top of `tests/test_api.py`, it is; the file already uses
it.)

`tests/test_crew_discord.py`:

```python
import pytest

from edmrn.ext import api
from edmrn.ext.crew_discord import invite_message, post_invite
from edmrn.ext.crew_roster import InviteError
from fakes import FakeResponse, Script

HOOK = "https://discord.com/api/webhooks/123/SECRET-TOKEN"
LINK = "https://galroute.the-hub.cloud/join#edmrn-crew:1:abc"


def test_message_never_pings_and_carries_the_link():
    message = invite_message("Crew @everyone", "Alice", "2026-10-14T10:00:00Z", LINK)
    assert message["allowed_mentions"] == {"parse": []}
    assert LINK in message["content"] and "Alice" in message["content"]
    assert "2026-10-14 10:00:00 UTC" in message["content"] and len(message["content"]) <= 2000


def test_post_sends_wait_true_through_api_call_with_a_redacted_log_url():
    call = Script(FakeResponse(200, {"id": "1"}))
    post_invite(HOOK, {"content": "x", "allowed_mentions": {"parse": []}}, call=call)
    (args, kwargs), = call.calls
    assert args == ("POST", HOOK) and kwargs["params"] == {"wait": "true"}
    assert kwargs["service"] == "Discord" and "SECRET-TOKEN" not in kwargs["log_url"]
    assert kwargs["json"]["allowed_mentions"] == {"parse": []}


def test_a_429_is_retried_once_after_retry_after():
    slept = []
    call = Script(FakeResponse(429, {"retry_after": 1.5}), FakeResponse(204))
    post_invite(HOOK, {"content": "x"}, call=call, sleep=slept.append)
    assert slept == [1.5] and len(call.calls) == 2


@pytest.mark.parametrize("responses", [
    [FakeResponse(429, {"retry_after": 60})],
    [FakeResponse(429, {"retry_after": 1}), FakeResponse(429, {"retry_after": 1})],
    [FakeResponse(401, {"message": "Invalid Webhook Token"})],
])
def test_failures_raise_one_sentence_without_the_token(responses):
    with pytest.raises(api.ApiError) as info:
        post_invite(HOOK, {"content": "x"}, call=Script(*responses), sleep=lambda s: None)
    assert "SECRET-TOKEN" not in str(info.value) and str(info.value).endswith(".")


def test_a_bad_webhook_url_is_refused_before_any_request():
    with pytest.raises(InviteError):
        post_invite("https://evil.example/api/webhooks/1/x", {"content": "x"}, call=Script())
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_discord.py tests/test_api.py -q`
Expected: `ModuleNotFoundError: No module named 'edmrn.ext.crew_discord'`, and the api test fails
with `TypeError: call() got an unexpected keyword argument 'log_url'`.

- [ ] **Step 3: Implement**

In `edmrn/ext/api.py`, add the parameter and use it in the three log lines of `call`:

```python
def call(method, url, *, service, params=None, json=None, timeout, retries=2, delays=(2.0, 5.0),
         on_retry=None, transport=requests.request, sleep=time.sleep, log_url=None):
    """Send one request; retry 502/503/504, timeouts and connection errors.

    Responses below 500 are returned; the caller decides what a 4xx means. Everything else ends in
    ApiError. on_retry(attempt, failure) is called before each retry. `log_url` replaces `url` in
    log lines when the URL itself is a secret (a Discord webhook, crew sync spec B1 S5a).
    """
    headers = {"User-Agent": USER_AGENT}
    shown = log_url or url
```

Replace each of the three `{url}` occurrences in the log calls of `call` with `{shown}`.

`edmrn/ext/crew_discord.py`:

```python
"""Posting a crew invite through the leader's Discord webhook (crew sync core spec S5a).

The webhook URL is a secret: it goes through api.call with a redacted log URL and never into an
error text. Discord answers rate limits with 429 and a retry_after in seconds.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from edmrn.ext import api
from edmrn.ext.crew_roster import check_webhook, redact_webhook

SERVICE = 'Discord'
MAX_RETRY_AFTER = 10.0
CONTENT_MAX = 2000


def invite_message(group_name: str, label: Optional[str], expires_at: str, link: str) -> dict:
    whom = f' for {label}' if label else ''
    expiry = expires_at.replace('T', ' ').replace('Z', ' UTC')
    content = (f'EDMRN crew invite{whom} to the group "{group_name}". It works once and expires '
               f'{expiry}.\nOpen the link, copy the code, then in EDMRN: Settings > Crew... > '
               f'Join with invite.\n{link}')
    return {'content': content[:CONTENT_MAX], 'allowed_mentions': {'parse': []}}


def _retry_after(response: Any) -> Optional[float]:
    try:
        value = response.json().get('retry_after')
    except Exception:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 < value <= MAX_RETRY_AFTER else None


def post_invite(webhook_url: str, message: dict, *, call=api.call, sleep=time.sleep) -> None:
    url = check_webhook(webhook_url)
    shown = redact_webhook(url)
    for attempt in (1, 2):
        response = call('POST', url, service=SERVICE, params={'wait': 'true'}, json=message,
                        timeout=10, retries=1, log_url=shown)
        if response.status_code in (200, 204):
            return
        if response.status_code == 429 and attempt == 1:
            delay = _retry_after(response)
            if delay is not None:
                sleep(delay)
                continue
        raise api.ApiError(api.failure_from_response(response, SERVICE))
```

`failure_from_response` reads only `error` and `msg` from the body. Discord's `message` is not
copied into the sentence, so the error text never repeats what Discord echoes.

- [ ] **Step 4: Run the tests and see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_discord.py tests/test_api.py tests/test_http_path.py -q`
Expected: all pass. `test_http_path.py` still passes, because `crew_discord` sends nothing
directly.

- [ ] **Step 5: Commit**

```bash
git add edmrn/ext/api.py edmrn/ext/crew_discord.py tests/test_api.py tests/test_crew_discord.py
git commit -m "feat(crew): Discord invite post through api.call with a redacted URL (B1 S5a)"
```

---

### Task 9: Group controller and install wiring

**Files:**
- Create: `edmrn/ext/crew_group.py`
- Modify: `edmrn/ext/crew.py` (`CrewService.group`, `install`), `edmrn/ext/crew_store.py`
  (`has_event`)
- Test: `tests/test_crew_group.py`, `tests/test_crew_install.py` (extend)

**Interfaces:**
- Consumes:
  - Tasks 2, 3, 6, 7 and 8;
  - Spec A's `Writer.enqueue` and `Writer.drain`, `CrewService.commander`, `.identity()`,
    `.status()`, `.keyring`, `.store` and `.ship.subscribe`;
  - `app.location_tracker.subscribe`.
- Produces:
  - `CrewStore.has_event(author, event_id) -> bool`;
  - `crew_group.GroupError`;
  - `crew_group.CreatedInvite(code, link, expires_at, label)`;
  - `crew_group.CrewGroup(service, app, *, sync_factory=CrewSync, connect=websockets_connect,
    post=crew_discord.post_invite, clock=None)` with:
    - lifecycle: `.start()`, `.stop()`;
    - attributes and views: `.config`, `.problem`, `.board`, `.sync`, `.roster`, `.state`;
    - actions: `.create_group(name, relay=DEFAULT_RELAY)`, `.join(text)`,
      `.create_invite(label=None, hours=24) -> CreatedInvite`, `.remove(member_author)`, `.leave()`,
      `.set_paused(bool)`, `.set_webhook(url_or_none)`, `.post_invite(invite, done)`.
  - `app.crew.group` (`None` when sync is unavailable).

- [ ] **Step 1: Write the failing tests** (`tests/test_crew_group.py`)

```python
import json
from types import SimpleNamespace

import pytest

from edmrn.ext import crew_events as ev
from edmrn.ext.api import ApiError, ApiFailure
from edmrn.ext.crew import Writer
from edmrn.ext.crew_group import CrewGroup, GroupError
from edmrn.ext.crew_identity import KeyRing
from edmrn.ext.crew_roster import decode_invite, load_group, save_group, token_hash, GroupConfig
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.store import Store


class Root:
    """Runs only immediate callbacks (the Discord result); periodic ticks (inbox, writer) wait."""

    def after(self, ms, fn, *args):
        if ms == 0:
            fn(*args)


class FakeSync:
    instances = []

    def __init__(self, store, config, identity, inbox, board, source, connect=None):
        self.config, self.identity, self.source = config, identity, source
        self.started = self.stopped = False
        self.paused = None
        self.dirty = 0
        FakeSync.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def set_paused(self, paused):
        self.paused = paused

    def status_changed(self, *_):
        self.dirty += 1

    state = "live"


class Ship:
    def __init__(self):
        self.observers = []
        self.state = SimpleNamespace(commander=None)

    def subscribe(self, fn):
        self.observers.append(fn)


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path / "edmrn.db")
    crew = CrewStore(store)
    ring = KeyRing(tmp_path / "crew_keys")
    writer = Writer(crew, ring, Root())
    service = SimpleNamespace(store=crew, keyring=ring, writer=writer, ship=Ship(), commander="Leader",
                              status=lambda: (None, None), group=None)
    service.identity = lambda: ring.for_commander(service.commander) if service.commander else None
    app = SimpleNamespace(root=Root(), location_tracker=SimpleNamespace(subscribe=lambda fn: None))
    FakeSync.instances = []
    group = CrewGroup(service, app, sync_factory=FakeSync)
    group.start()
    yield SimpleNamespace(store=store, crew=crew, ring=ring, service=service, group=group,
                          dir=tmp_path / "crew_keys", tmp=tmp_path)
    store.close()


def test_create_group_records_genesis_saves_config_and_starts_sync(world):
    world.group.create_group("Deep Space Crew")
    cfg = load_group(world.dir)
    leader = world.ring.for_commander("Leader").author
    assert cfg.leader == cfg.member == leader and cfg.url == "wss://galroute.the-hub.cloud"
    assert world.crew.own_roster_envelope(leader, "group", cfg.group_id) is not None
    assert world.group.roster.name == "Deep Space Crew"
    assert FakeSync.instances[-1].started


@pytest.mark.parametrize("name", ["", "x" * 65, "Ünïcode"])
def test_create_group_refuses_bad_names(world, name):
    with pytest.raises(GroupError):
        world.group.create_group(name)


def test_no_commander_yet_is_a_sentence(world):
    world.service.commander = None
    with pytest.raises(GroupError, match="No commander"):
        world.group.create_group("Crew")


def test_invite_round_trip_admits_the_joiner_on_the_leaders_side(world, tmp_path):
    world.group.create_group("Crew")
    invite = world.group.create_invite("Alice", 24)
    code = decode_invite(invite.link)
    assert code.leader == world.ring.for_commander("Leader").author
    info = world.group.roster.invites[code.invite_id]
    assert info.label == "Alice" and info.redeemed_by is None
    with pytest.raises(GroupError):
        world.group.create_invite("Alice", 5)


def test_join_records_a_join_and_saves_the_leader_from_the_invite(world, tmp_path):
    leader_store = Store(tmp_path / "leader.db")
    leader_ring = KeyRing(tmp_path / "leader_keys")
    leader_crew = CrewStore(leader_store)
    leader_service = SimpleNamespace(store=leader_crew, keyring=leader_ring,
                                     writer=Writer(leader_crew, leader_ring, Root()), ship=Ship(),
                                     commander="Leader", status=lambda: (None, None))
    leader_service.identity = lambda: leader_ring.for_commander("Leader")
    leader = CrewGroup(leader_service, SimpleNamespace(root=Root()), sync_factory=FakeSync)
    leader.start()
    leader.create_group("Crew")
    invite = leader.create_invite(None, 1)
    world.service.commander = "Alice"
    world.group.join(f"<{invite.link}>")
    cfg = load_group(world.dir)
    assert cfg.leader == leader_ring.for_commander("Leader").author
    assert cfg.member == world.ring.for_commander("Alice").author and not cfg.is_leader
    leader_store.close()


def test_joining_your_own_invite_is_refused(world):
    world.group.create_group("Crew")
    invite = world.group.create_invite(None, 24)
    world.group.leave()
    with pytest.raises(GroupError, match="own invite"):
        world.group.join(invite.code)


def test_only_the_leader_invites_removes_and_sets_the_webhook(world):
    world.group.create_group("Crew")
    world.service.commander = "Alt"
    for action in (lambda: world.group.create_invite(None, 24),
                   lambda: world.group.set_webhook("https://discord.com/api/webhooks/1/x"),
                   lambda: world.group.remove("A" * 43)):
        with pytest.raises(GroupError, match="leader"):
            action()


def test_remove_records_the_members_last_seq(world):
    world.group.create_group("Crew")
    with pytest.raises(GroupError):
        world.group.remove(world.ring.for_commander("Leader").author)


def test_leave_pause_and_webhook_persist(world):
    world.group.create_group("Crew")
    world.group.set_paused(True)
    assert load_group(world.dir).paused and FakeSync.instances[-1].paused is True
    world.group.set_webhook("https://discord.com/api/webhooks/1/x")
    assert load_group(world.dir).webhook.endswith("/1/x")
    with pytest.raises(GroupError):
        world.group.set_webhook("https://evil.example/x")
    sync = FakeSync.instances[-1]
    world.group.leave()
    assert load_group(world.dir) is None and sync.stopped and world.group.roster is None


def test_post_invite_reports_through_root_after(world):
    world.group.create_group("Crew")
    world.group.set_webhook("https://discord.com/api/webhooks/1/x")
    invite = world.group.create_invite(None, 24)
    results = []
    world.group._post = lambda url, message: (_ for _ in ()).throw(ApiError(ApiFailure("Discord", "rejected", 401)))
    world.group.post_invite(invite, results.append)
    for thread in __import__("threading").enumerate():
        if thread.name == "edmrn-crew-discord":
            thread.join(5)
    assert results == ["Discord rejected the request (HTTP 401)."]


def test_start_with_a_broken_group_file_sets_a_problem_and_no_sync(world):
    world.group.leave()
    (world.dir / "group.json").write_text("{nope", encoding="utf-8")
    FakeSync.instances = []
    world.group.start()
    assert world.group.problem and world.group.config is None and FakeSync.instances == []


def test_without_websockets_sync_does_not_start(world, monkeypatch):
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "websockets" else real(name, *a))
    FakeSync.instances = []
    world.group.create_group("Crew")
    assert FakeSync.instances == [] and "websockets" in world.group.problem


def test_status_changes_reach_the_sync(world):
    world.group.create_group("Crew")
    for observer in world.service.ship.observers:
        observer(None)
    assert FakeSync.instances[-1].dirty >= 1
```

Append to `tests/test_crew_install.py` (Review Focus 3). It uses the file's own `store`
fixture, `listeners` fixture and `make_app` helper:

```python
def test_install_with_a_group_and_no_network_starts_and_keeps_running(store, tmp_path, listeners, monkeypatch):
    from edmrn.ext import crew_group
    from edmrn.ext.crew_roster import GroupConfig, save_group
    calls = []

    def offline(url):
        calls.append(url)
        raise OSError("no route to host")
    monkeypatch.setattr(crew_group, "websockets_connect", offline)
    keys = Path(store.path).parent / "crew_keys"
    me = KeyRing(keys).for_commander("Jameson")
    save_group(keys, GroupConfig("wss://galroute.the-hub.cloud", "01920000-0000-7000-8000-0000000000a1",
                                 me.author, me.author))
    service = install(make_app(store, tmp_path / "journals"))
    try:
        assert service is not None and service.group is not None
        assert wait_for(lambda: calls)
        assert service.group.sync.state in ("connecting", "waiting")
    finally:
        service.group.stop()
```

The test must stop the sync thread before it ends. Otherwise, after `monkeypatch` restores the
real connect and the socket guard, the background thread could try the real network. Also
add a guard to `tests/conftest.py`, an autouse fixture that fails a test if a thread named
`edmrn-crew-sync` is still alive at its end:

```python
@pytest.fixture(autouse=True)
def no_leftover_sync_threads():
    yield
    import threading
    alive = [t.name for t in threading.enumerate() if t.name == "edmrn-crew-sync" and t.is_alive()]
    assert not alive, "a test left the crew sync thread running; call group.stop()"
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_group.py tests/test_crew_install.py -q`
Expected: `ModuleNotFoundError: No module named 'edmrn.ext.crew_group'`.

- [ ] **Step 3: Implement**

Add to `CrewStore` in `edmrn/ext/crew_store.py`:

```python
    def has_event(self, author: str, event_id: str) -> bool:
        with self.store.read() as conn:
            return conn.execute('SELECT 1 FROM crew_log WHERE author = ? AND event_id = ? LIMIT 1',
                                (author, event_id)).fetchone() is not None
```

`edmrn/ext/crew_group.py`:

```python
"""The crew group controller behind the dialog (crew sync core spec S4, S5, S5a, S20).

Every method runs on the Tk thread, like Spec A's manual API: the KeyRing and the writer's drain are
not locked. The Discord post runs in a worker thread and reports back through root.after.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from edmrn.ext import crew_discord
from edmrn.ext import crew_events as ev
from edmrn.ext.api import ApiError
from edmrn.ext.crew_roster import (DEFAULT_RELAY, INVITE_HOURS, GroupConfig, GroupConfigError,
                                   InviteCode, InviteError, check_relay_url, check_webhook,
                                   decode_invite, delete_group, encode_invite, invite_link,
                                   load_group, new_token, save_group, token_hash)
from edmrn.ext.crew_sync import CrewBoard, CrewSync, Inbox, make_status_source, websockets_connect
from edmrn.logger import get_logger

logger = get_logger('CrewGroup')


class GroupError(Exception):
    """A refused group action; the message is one sentence for the dialog."""


@dataclass(frozen=True)
class CreatedInvite:
    code: str
    link: str
    expires_at: str
    label: Optional[str]


class CrewGroup:
    def __init__(self, service: Any, app: Any, *, sync_factory: Callable = CrewSync,
                 connect: Optional[Callable] = None, post: Callable = crew_discord.post_invite,
                 clock: Optional[Callable[[], datetime]] = None) -> None:
        self.service = service
        self.app = app
        self.root = getattr(app, 'root', None)
        self.directory = service.keyring.directory
        self.board = CrewBoard()
        self.inbox = Inbox(service.store, self.root)
        self.sync: Any = None
        self.config: Optional[GroupConfig] = None
        self.problem: Optional[str] = None
        self._sync_factory = sync_factory
        self._connect = connect
        self._post = post
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._subscribed = False

    # --- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        try:
            self.config = load_group(self.directory)
        except GroupConfigError as e:
            self.problem = f'The group file cannot be used ({e}); leave the group to start over.'
            logger.error(self.problem)
        self._apply_gate()
        self.inbox.start()
        if self.config is not None:
            self._start_sync()

    def stop(self) -> None:
        if self.sync is not None:
            self.sync.stop()
        self.inbox.stop()

    @property
    def roster(self):
        return self.service.store.roster

    @property
    def state(self) -> str:
        if self.config is None:
            return 'no group'
        return self.sync.state if self.sync is not None else 'stopped'

    def _apply_gate(self) -> None:
        cfg = self.config
        self.service.store.set_gate(self.service.keyring.own_authors(),
                                    cfg.group_id if cfg else None, cfg.leader if cfg else None)

    def _start_sync(self) -> None:
        if importlib.util.find_spec('websockets') is None:
            self.problem = 'Crew sync needs the websockets package (pip install websockets).'
            logger.error(self.problem)
            return
        identity = self.service.keyring.identity_for_author(self.config.member)
        if identity is None:
            self.problem = 'The key of this group membership is missing from crew_keys.'
            logger.error(self.problem)
            return
        self.problem = None
        connect = self._connect or websockets_connect
        self.sync = self._sync_factory(self.service.store, self.config, identity, self.inbox,
                                       self.board, make_status_source(self.service, identity.path),
                                       connect=connect)
        if not self._subscribed:
            self._subscribed = True
            self.service.ship.subscribe(self._status_changed)
            tracker = getattr(self.app, 'location_tracker', None)
            if tracker is not None:
                tracker.subscribe(self._status_changed)
        self.sync.start()

    def _status_changed(self, *_: Any) -> None:
        sync = self.sync
        if sync is not None:
            sync.status_changed()

    # --- helpers -----------------------------------------------------------------------

    def _me(self) -> tuple:
        commander, identity = self.service.commander, self.service.identity()
        if commander is None or identity is None:
            raise GroupError('No commander is known yet; start Elite Dangerous or wait until '
                             'EDMRN has read the journal.')
        return commander, identity

    def _leader(self) -> tuple:
        if self.config is None:
            raise GroupError('This EDMRN is not in a group.')
        commander, identity = self._me()
        if not self.config.is_leader or identity.author != self.config.leader:
            raise GroupError('Only the group leader can do this; switch to the commander who '
                             'created the group.')
        return commander, identity

    def _record(self, kind: str, commander: str, author: str, payload: dict) -> None:
        draft = ev.Draft(kind, commander, ev.utc_now_stamp(), payload, event_id=ev.uuid7())
        self.service.writer.enqueue(draft)
        self.service.writer.drain()
        if not self.service.store.has_event(author, draft.event_id):
            raise GroupError('EDMRN could not record this; the log file says why.')

    def _enter(self, config: GroupConfig) -> None:
        save_group(self.directory, config)
        self.config = config
        self._apply_gate()
        self._start_sync()

    @staticmethod
    def _relay(relay: str) -> str:
        try:
            return check_relay_url(relay)
        except InviteError as e:
            raise GroupError(str(e)) from None

    # --- actions -----------------------------------------------------------------------

    def create_group(self, name: str, relay: str = DEFAULT_RELAY) -> None:
        if self.config is not None:
            raise GroupError('This EDMRN is already in a group; leave it first.')
        name = name.strip() if isinstance(name, str) else ''
        if not ev.is_roster_name(name):
            raise GroupError('A group name is 1 to 64 plain ASCII characters.')
        url = self._relay(relay)
        commander, identity = self._me()
        group_id = ev.uuid7()
        self._record('group', commander, identity.author,
                     {'group_id': group_id, 'name': name, 'commander': ev.ascii_name(commander)})
        self._enter(GroupConfig(url, group_id, identity.author, identity.author))

    def join(self, text: str) -> None:
        if self.config is not None:
            raise GroupError('This EDMRN is already in a group; leave it first.')
        try:
            code = decode_invite(text)
        except InviteError as e:
            raise GroupError(str(e)) from None
        commander, identity = self._me()
        if identity.author == code.leader:
            raise GroupError('This is your own invite; send it to a friend.')
        self._record('join', commander, identity.author,
                     {'group_id': code.group_id, 'invite_id': code.invite_id, 'token': code.token,
                      'commander': ev.ascii_name(commander)})
        self._enter(GroupConfig(code.url, code.group_id, code.leader, identity.author))

    def create_invite(self, label: Optional[str] = None, hours: int = 24) -> CreatedInvite:
        commander, identity = self._leader()
        if hours not in INVITE_HOURS:
            raise GroupError('An invite lasts 1 hour, 24 hours or 7 days.')
        label = label.strip() if isinstance(label, str) and label.strip() else None
        if label is not None and not ev.is_roster_name(label):
            raise GroupError('A label is 1 to 64 plain ASCII characters.')
        cfg = self.config
        token, invite_id = new_token(), ev.uuid7()
        expires = (self._clock() + timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
        self._record('invite', commander, identity.author,
                     {'group_id': cfg.group_id, 'invite_id': invite_id,
                      'token_hash': token_hash(token), 'expires_at': expires, 'label': label})
        code = encode_invite(InviteCode(cfg.url, cfg.group_id, invite_id, token, cfg.leader))
        return CreatedInvite(code, invite_link(code, cfg.url), expires, label)

    def remove(self, member: str) -> None:
        commander, identity = self._leader()
        roster = self.roster
        if (member == self.config.leader or roster is None or member not in roster.members
                or roster.members[member].last_seq is not None):
            raise GroupError('Only a current member other than the leader can be removed.')
        self._record('remove', commander, identity.author,
                     {'group_id': self.config.group_id, 'member': member,
                      'last_seq': self.service.store.max_seq(member)})

    def leave(self) -> None:
        if self.sync is not None:
            self.sync.stop()
            self.sync = None
        delete_group(self.directory)
        self.config = None
        self.problem = None
        self._apply_gate()

    def set_paused(self, paused: bool) -> None:
        if self.config is None:
            raise GroupError('This EDMRN is not in a group.')
        self.config = dataclasses.replace(self.config, paused=bool(paused))
        save_group(self.directory, self.config)
        if self.sync is not None:
            self.sync.set_paused(bool(paused))

    def set_webhook(self, url: Optional[str]) -> None:
        self._leader()
        webhook = None
        if isinstance(url, str) and url.strip():
            try:
                webhook = check_webhook(url)
            except InviteError as e:
                raise GroupError(str(e)) from None
        self.config = dataclasses.replace(self.config, webhook=webhook)
        save_group(self.directory, self.config)

    def post_invite(self, invite: CreatedInvite, done: Callable[[Optional[str]], None]) -> None:
        """Post through the webhook in a worker thread; `done(None or sentence)` on the Tk loop."""
        if self.config is None or self.config.webhook is None:
            raise GroupError('Set a Discord webhook first.')
        webhook = self.config.webhook
        roster = self.roster
        name = roster.name if roster is not None and roster.name else 'crew'
        message = crew_discord.invite_message(name, invite.label, invite.expires_at, invite.link)

        def work() -> None:
            try:
                self._post(webhook, message)
                result = None
            except (ApiError, InviteError) as e:
                result = str(e)
            except Exception:
                logger.exception('Crew invite post failed')
                result = 'The post to Discord failed; the log file says why.'
            try:
                self.root.after(0, lambda: done(result))
            except Exception as e:  # the Tk loop is gone: the app is closing
                logger.debug(f'Crew invite post result dropped: {e}')

        threading.Thread(target=work, name='edmrn-crew-discord', daemon=True).start()
```

In `edmrn/ext/crew.py`:
- `CrewService.__init__` gains `self.group: Any = None`.
- After `writer.start()` in `install`, add `_install_group(service, app)` and define:

```python
def _install_group(service: CrewService, app: Any) -> None:
    """Spec B1: the roster gate, the inbox and, with a group.json, the relay connection."""
    try:
        from edmrn.ext.crew_group import CrewGroup
        group = CrewGroup(service, app)
        group.start()
        service.group = group
    except Exception as e:  # the hook must never abort EDMRN_App.__init__
        logger.error(f'Crew sync disabled: {e}')
        service.group = None
    try:
        service.store.rebuild_if_marked()
    except Exception as e:
        logger.error(f'Crew projection rebuild failed: {e}')
```

`CrewGroup.__init__` reads `crew_group.websockets_connect` at call time (`self._connect or
websockets_connect`, a module-level name), so the install test's `monkeypatch` of
`crew_group.websockets_connect` takes effect. Keep it that way.

- [ ] **Step 4: Run the tests and see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_group.py tests/test_crew_install.py -q`, then
`.venv/Scripts/python.exe -m pytest -q`.
Expected: all pass, and no test leaves `edmrn-crew-sync` alive.

- [ ] **Step 5: Commit**

```bash
git add edmrn/ext/crew_group.py edmrn/ext/crew.py edmrn/ext/crew_store.py tests/test_crew_group.py tests/test_crew_install.py tests/conftest.py
git commit -m "feat(crew): group controller - create, join, invite, remove, leave, pause, webhook (B1 S4 S5 S5a)"
```

---

### Task 10: Crew dialog and the Settings hook

**Files:**
- Create: `edmrn/ext/crew_dialog.py`
- Modify (upstream, CRLF, Edit tool only): `edmrn/settings_manager.py`
- Test: `tests/test_crew_dialog.py`

**Interfaces:**
- Consumes: Task 9's `CrewGroup` (or any object with the same attributes and methods),
  `GroupError`, `CreatedInvite`; `DEFAULT_RELAY`, `INVITE_HOURS`.
- Produces:
  - `crew_dialog.add_settings_card(app, parent, colors) -> CTkFrame`;
  - `crew_dialog.open_dialog(app) -> Optional[CrewDialog]`;
  - `crew_dialog.CrewDialog(master, group, *, commander, author, copy=None)`, whose widgets the
    tests reach as attributes: `name_entry`, `relay_entry`, `create_button`, `invite_box`,
    `join_button`, `pause_switch`, `leave_button`, `label_entry`, `expiry`, `invite_button`,
    `code_entry`, `link_entry`, `post_button`, `webhook_entry`, `webhook_button`,
    `remove_buttons` (dict author → button) and `message` (StringVar).

- [ ] **Step 1: Write the failing tests** (`tests/test_crew_dialog.py`)

```python
from types import SimpleNamespace

import pytest

ctk = pytest.importorskip("customtkinter")

from edmrn.ext.crew_dialog import CrewDialog
from edmrn.ext.crew_group import CreatedInvite, GroupError
from edmrn.ext.crew_roster import GroupConfig, Member, Roster

LEADER, ALICE = "L" * 43, "A" * 43
G = "01920000-0000-7000-8000-0000000000a1"


@pytest.fixture(scope="module")
def _tk_root():
    try:
        r = ctk.CTk()
    except Exception as e:
        pytest.skip(f"no Tk display: {e}")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except Exception:
        pass


@pytest.fixture
def root(_tk_root):
    yield _tk_root
    for child in _tk_root.winfo_children():
        if isinstance(child, ctk.CTkToplevel):
            child.destroy()


class FakeGroup:
    def __init__(self, config=None, roster=None):
        self.config, self._roster, self.problem, self.calls = config, roster, None, []
        self.state = "live" if config else "no group"
        self.sync = SimpleNamespace(last_error=None)

    @property
    def roster(self):
        return self._roster

    def create_group(self, name, relay):
        self.calls.append(("create", name, relay))
        if name == "bad":
            raise GroupError("A group name is 1 to 64 plain ASCII characters.")

    def join(self, text):
        self.calls.append(("join", text))

    def create_invite(self, label, hours):
        self.calls.append(("invite", label, hours))
        return CreatedInvite("edmrn-crew:1:abc", "https://h/join#edmrn-crew:1:abc",
                             "2026-10-14T10:00:00Z", label)

    def post_invite(self, invite, done):
        self.calls.append(("post", invite.code))
        done(None)

    def remove(self, member):
        self.calls.append(("remove", member))

    def leave(self):
        self.calls.append(("leave",))

    def set_paused(self, paused):
        self.calls.append(("pause", paused))

    def set_webhook(self, url):
        self.calls.append(("webhook", url))


def leader_group(webhook=None):
    roster = Roster(G, LEADER, "Crew", {LEADER: Member(LEADER, "Leader", True, None),
                                       ALICE: Member(ALICE, "Alice", False, None)}, {})
    return FakeGroup(GroupConfig("wss://h", G, LEADER, LEADER, webhook), roster)


def test_no_group_view_creates_and_joins(root):
    group = FakeGroup()
    dialog = CrewDialog(root, group, commander="Leader", author=LEADER, copy=[].append)
    dialog.name_entry.insert(0, "Crew")
    dialog.create_button.invoke()
    assert group.calls[0] == ("create", "Crew", "wss://galroute.the-hub.cloud")
    dialog.invite_box.insert("1.0", "edmrn-crew:1:abc")
    dialog.join_button.invoke()
    assert group.calls[-1] == ("join", "edmrn-crew:1:abc")


def test_a_refusal_shows_its_sentence(root):
    dialog = CrewDialog(root, FakeGroup(), commander="Leader", author=LEADER, copy=[].append)
    dialog.name_entry.insert(0, "bad")
    dialog.create_button.invoke()
    assert dialog.message.get() == "A group name is 1 to 64 plain ASCII characters."


def test_leader_view_invites_copies_posts_and_removes(root):
    copied = []
    group = leader_group(webhook="https://discord.com/api/webhooks/1/x")
    dialog = CrewDialog(root, group, commander="Leader", author=LEADER, copy=copied.append)
    dialog.label_entry.insert(0, "Alice")
    dialog.expiry.set("24 hours")
    dialog.invite_button.invoke()
    assert group.calls[-1] == ("invite", "Alice", 24)
    assert dialog.code_entry.get() == "edmrn-crew:1:abc" and dialog.link_entry.get().endswith("abc")
    dialog.copy_code_button.invoke()
    assert copied[-1] == "edmrn-crew:1:abc"
    dialog.post_button.invoke()
    assert group.calls[-1] == ("post", "edmrn-crew:1:abc")
    assert set(dialog.remove_buttons) == {ALICE}
    dialog.remove_buttons[ALICE].invoke()
    assert group.calls[-1] == ("remove", ALICE)


def test_member_view_has_no_leader_controls_and_pauses(root):
    roster = leader_group().roster
    group = FakeGroup(GroupConfig("wss://h", G, LEADER, ALICE), roster)
    dialog = CrewDialog(root, group, commander="Alice", author=ALICE, copy=[].append)
    assert dialog.invite_button is None and dialog.remove_buttons == {}
    dialog.pause_switch.select()
    dialog.pause_switch.invoke()
    assert ("pause", False) in group.calls or ("pause", True) in group.calls


def test_post_button_needs_a_webhook(root):
    dialog = CrewDialog(root, leader_group(), commander="Leader", author=LEADER, copy=[].append)
    dialog.invite_button.invoke()
    assert dialog.post_button is None
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_dialog.py -q`
Expected: `ModuleNotFoundError: No module named 'edmrn.ext.crew_dialog'` (or a skip without a
display; then run it on a machine with a display before claiming the task done).

- [ ] **Step 3: Implement `edmrn/ext/crew_dialog.py`**

```python
"""The crew settings card and dialog (crew sync core spec S20). Tk thread only.

The dialog renders from the group controller (crew_group.CrewGroup) and re-renders once a second
when what it shows has changed. Every action goes through the controller; a GroupError's sentence
appears under the dialog.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import customtkinter as ctk

from edmrn.ext.crew_group import CreatedInvite, GroupError
from edmrn.ext.crew_roster import DEFAULT_RELAY

EXPIRY = {'1 hour': 1, '24 hours': 24, '7 days': 168}
STATE_TEXT = {'connecting': 'Connecting...', 'syncing': 'Catching up...', 'live': 'Connected',
              'waiting': 'Offline, retrying', 'refused': 'Refused by the relay',
              'stopped': 'Not connected', 'no group': 'Not in a group'}
WEBHOOK_WARNING = ('A webhook posts into a channel, not a DM: whoever reads the channel first can '
                   'use the invite. Use a private channel and a short expiry.')


def add_settings_card(app: Any, parent: Any, colors: dict) -> Any:
    frame = ctk.CTkFrame(parent, corner_radius=10, fg_color=colors['frame'],
                         border_color=colors['border'], border_width=1)
    frame.grid(row=1, column=2, padx=5, pady=5, sticky='nsew')
    ctk.CTkLabel(frame, text='Crew', font=ctk.CTkFont(size=13, weight='bold')).pack(pady=(8, 6))
    ctk.CTkButton(frame, text='Crew...', command=lambda: open_dialog(app)).pack(
        pady=(0, 8), padx=10, fill='x')
    return frame


def open_dialog(app: Any) -> Optional['CrewDialog']:
    crew = getattr(app, 'crew', None)
    existing = getattr(app, '_edmrn_crew_dialog', None)
    if existing is not None and existing.winfo_exists():
        existing.focus()
        return existing
    identity = crew.identity() if crew is not None else None
    dialog = CrewDialog(app.root, getattr(crew, 'group', None),
                        commander=getattr(crew, 'commander', None),
                        author=identity.author if identity is not None else None)
    app._edmrn_crew_dialog = dialog
    return dialog


class CrewDialog(ctk.CTkToplevel):
    REFRESH_MS = 1000

    def __init__(self, master: Any, group: Any, *, commander: Optional[str], author: Optional[str],
                 copy: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(master)
        self.title('Crew')
        self.geometry('560x640')
        self.group = group
        self.commander = commander
        self.author = author
        self._copy = copy or self._clipboard
        self.message = ctk.StringVar(value='')
        self.invite: Optional[CreatedInvite] = None
        self.body = ctk.CTkScrollableFrame(self)
        self.body.pack(fill='both', expand=True, padx=10, pady=(10, 4))
        ctk.CTkLabel(self, textvariable=self.message, wraplength=520, justify='left').pack(
            fill='x', padx=10, pady=(0, 10))
        self._shown = None
        self.render()
        self.after(self.REFRESH_MS, self._tick)

    # --- plumbing ----------------------------------------------------------------------

    def _clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)

    def _view_key(self) -> tuple:
        g = self.group
        if g is None:
            return ('none',)
        roster = g.roster
        members = () if roster is None else tuple(sorted(
            (m.author, m.commander, m.last_seq) for m in roster.members.values()))
        cfg = g.config
        return (cfg, g.problem, g.state, members, getattr(g.sync, 'last_error', None))

    def _tick(self) -> None:
        if not self.winfo_exists():
            return
        if self._view_key() != self._shown:
            self.render()
        self.after(self.REFRESH_MS, self._tick)

    def _do(self, action: Callable[[], Any], done: str = '') -> Any:
        try:
            result = action()
        except GroupError as e:
            self.message.set(str(e))
            return None
        self.message.set(done)
        self.render()
        return result

    def _section(self, title: str) -> Any:
        frame = ctk.CTkFrame(self.body)
        frame.pack(fill='x', pady=6)
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=13, weight='bold')).pack(
            anchor='w', padx=8, pady=(6, 2))
        return frame

    def _copy_row(self, parent: Any, value: str, label: str) -> tuple:
        row = ctk.CTkFrame(parent, fg_color='transparent')
        row.pack(fill='x', padx=8, pady=2)
        entry = ctk.CTkEntry(row)
        entry.insert(0, value)
        entry.configure(state='readonly')
        entry.pack(side='left', fill='x', expand=True)
        button = ctk.CTkButton(row, text=label, width=70, command=lambda: self._copy(value))
        button.pack(side='left', padx=(6, 0))
        return entry, button

    # --- views -------------------------------------------------------------------------

    def render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self.name_entry = self.relay_entry = self.create_button = None
        self.invite_box = self.join_button = None
        self.pause_switch = self.leave_button = None
        self.label_entry = self.expiry = self.invite_button = None
        self.code_entry = self.link_entry = self.copy_code_button = self.post_button = None
        self.webhook_entry = self.webhook_button = None
        self.remove_buttons = {}
        self._shown = self._view_key()
        if self.group is None:
            ctk.CTkLabel(self.body, text='Crew features are unavailable; the log file says why.',
                         wraplength=500).pack(anchor='w', padx=8, pady=8)
            return
        self._render_identity()
        if self.group.problem:
            ctk.CTkLabel(self.body, text=self.group.problem, wraplength=500,
                         text_color='orange').pack(anchor='w', padx=8, pady=4)
        if self.group.config is None:
            self._render_outside()
        else:
            self._render_inside()

    def _render_identity(self) -> None:
        frame = self._section('You')
        ctk.CTkLabel(frame, text=f'Commander: {self.commander or "not known yet"}').pack(
            anchor='w', padx=8)
        if self.author:
            ctk.CTkLabel(frame, text='Your crew key (public; the relay lists leaders by it):').pack(
                anchor='w', padx=8, pady=(4, 0))
            self._copy_row(frame, self.author, 'Copy')

    def _render_outside(self) -> None:
        frame = self._section('Create a group')
        self.name_entry = ctk.CTkEntry(frame, placeholder_text='Group name')
        self.name_entry.pack(fill='x', padx=8, pady=2)
        self.relay_entry = ctk.CTkEntry(frame)
        self.relay_entry.insert(0, DEFAULT_RELAY)
        self.relay_entry.pack(fill='x', padx=8, pady=2)
        self.create_button = ctk.CTkButton(frame, text='Create group', command=lambda: self._do(
            lambda: self.group.create_group(self.name_entry.get(), self.relay_entry.get()),
            'Group created.'))
        self.create_button.pack(anchor='e', padx=8, pady=(2, 8))

        frame = self._section('Join with invite')
        self.invite_box = ctk.CTkTextbox(frame, height=70)
        self.invite_box.pack(fill='x', padx=8, pady=2)
        self.join_button = ctk.CTkButton(frame, text='Join', command=lambda: self._do(
            lambda: self.group.join(self.invite_box.get('1.0', 'end').strip()), 'Joined.'))
        self.join_button.pack(anchor='e', padx=8, pady=(2, 8))

    def _render_inside(self) -> None:
        g = self.group
        cfg = g.config
        roster = g.roster
        frame = self._section(f'Group: {roster.name if roster and roster.name else "crew"}')
        state = STATE_TEXT.get(g.state, g.state)
        error = getattr(g.sync, 'last_error', None)
        ctk.CTkLabel(frame, text=f'Connection: {state}' + (f' ({error})' if error and
                     g.state != 'live' else ''), wraplength=500).pack(anchor='w', padx=8)
        self.pause_switch = ctk.CTkSwitch(frame, text='Pause sharing', command=lambda: self._do(
            lambda: g.set_paused(bool(self.pause_switch.get()))))
        if cfg.paused:
            self.pause_switch.select()
        self.pause_switch.pack(anchor='w', padx=8, pady=4)

        members = self._section('Members')
        for member in (roster.active() if roster else []):
            row = ctk.CTkFrame(members, fg_color='transparent')
            row.pack(fill='x', padx=8, pady=1)
            name = member.commander or member.author[:12]
            ctk.CTkLabel(row, text=name + (' (leader)' if member.leader else '')).pack(side='left')
            if cfg.is_leader and not member.leader:
                button = ctk.CTkButton(row, text='Remove', width=70, command=lambda a=member.author:
                                       self._do(lambda: g.remove(a), 'Member removed.'))
                button.pack(side='right')
                self.remove_buttons[member.author] = button

        if cfg.is_leader:
            self._render_invite(cfg)

        self.leave_button = ctk.CTkButton(self.body, text='Leave group', fg_color='gray30',
                                          command=lambda: self._do(g.leave, 'Left the group.'))
        self.leave_button.pack(anchor='e', padx=8, pady=8)

    def _render_invite(self, cfg: Any) -> None:
        g = self.group
        frame = self._section('Invite')
        self.label_entry = ctk.CTkEntry(frame, placeholder_text='For whom (optional)')
        self.label_entry.pack(fill='x', padx=8, pady=2)
        self.expiry = ctk.CTkSegmentedButton(frame, values=list(EXPIRY))
        self.expiry.set('24 hours')
        self.expiry.pack(anchor='w', padx=8, pady=2)

        def create() -> None:
            invite = self._do(lambda: g.create_invite(self.label_entry.get(),
                                                      EXPIRY[self.expiry.get()]),
                              'Invite created; it works once.')
            if invite is not None:
                self.invite = invite
                self.render()
        self.invite_button = ctk.CTkButton(frame, text='Create invite', command=create)
        self.invite_button.pack(anchor='e', padx=8, pady=2)
        if self.invite is not None:
            self.code_entry, self.copy_code_button = self._copy_row(frame, self.invite.code, 'Copy')
            self.link_entry, _ = self._copy_row(frame, self.invite.link, 'Copy link')
            ctk.CTkLabel(frame, text=f'Expires {self.invite.expires_at.replace("T", " ")}').pack(
                anchor='w', padx=8)
            if cfg.webhook:
                invite = self.invite
                self.post_button = ctk.CTkButton(frame, text='Post to Discord', command=lambda:
                                                 self._do(lambda: g.post_invite(invite, self._posted)))
                self.post_button.pack(anchor='e', padx=8, pady=2)
                ctk.CTkLabel(frame, text=WEBHOOK_WARNING, wraplength=500).pack(anchor='w', padx=8)

        hook = self._section('Discord webhook (leader only, stays on this PC)')
        self.webhook_entry = ctk.CTkEntry(hook, placeholder_text='https://discord.com/api/webhooks/...',
                                          show='*')
        if cfg.webhook:
            self.webhook_entry.insert(0, cfg.webhook)
        self.webhook_entry.pack(fill='x', padx=8, pady=2)
        self.webhook_button = ctk.CTkButton(hook, text='Save webhook', command=lambda: self._do(
            lambda: g.set_webhook(self.webhook_entry.get()), 'Webhook saved.'))
        self.webhook_button.pack(anchor='e', padx=8, pady=(2, 8))

    def _posted(self, error: Optional[str]) -> None:
        self.message.set(error or 'Invite posted to Discord.')
```

   The webhook entry uses `show='*'` because the URL is a secret (S5a).

- [ ] **Step 4: Add the upstream hook** (Edit tool; the file is CRLF)

In `edmrn/settings_manager.py`, directly after `self.create_fuel_settings_card(fuel_frame)` in
`create_settings_tab`, add two lines at the same indentation:

```python
        from edmrn.ext.crew_dialog import add_settings_card  # EDMRN fork hook (ADR-0002)
        add_settings_card(self.app, scroll_frame, colors)
```

Then run `file edmrn/settings_manager.py` and confirm `with CRLF line terminators` is still the
only line-ending form reported.

- [ ] **Step 5: Run the tests and see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_dialog.py -q`, then the full suite.
Expected: all pass. The GUI tests may skip with "no Tk display" or Tk's intermittent "can't find
a usable init.tcl"; rerun once before reporting a skip.

- [ ] **Step 6: Commit**

```bash
git add edmrn/ext/crew_dialog.py edmrn/settings_manager.py tests/test_crew_dialog.py
git commit -m "feat(crew): crew dialog and Settings card hook (B1 S20)"
```

---
### Task 11: Relay scaffold, routes and the invite page

**Files:**
- Create:
  - `crew-relay/package.json`, `crew-relay/package-lock.json` (generated), `crew-relay/tsconfig.json`;
  - `crew-relay/wrangler.toml`, `crew-relay/vitest.config.ts`;
  - `crew-relay/src/index.ts`, `crew-relay/src/join.ts`, `crew-relay/src/group.ts` (a stub this
    task), `crew-relay/LICENSE` (a copy of the repository's AGPL-3.0 `LICENSE`);
  - `crew-relay/README.md` (three lines: what it is, the spec link, `docs/how-to/deploy-relay.md`).
- Modify: `.gitignore` (add `crew-relay/node_modules/` and `crew-relay/.wrangler/`).
- Modify (hub repository): `../knowledge/group-sync-transport.md`, adding the toolchain facts.
- Test: `crew-relay/test/routes.test.ts`.

**Interfaces:**
- Consumes: nothing from Python.
- Produces:
  - `src/index.ts`: `export interface Env { GROUPS: DurableObjectNamespace; SOURCE_URL: string;
    CREW_LEADERS: string }`, `export { GroupRelay } from './group'`, and the default `fetch`
    handler;
  - `src/join.ts`: `JOIN_SCRIPT`, `JOIN_STYLE`, `joinPage(): Promise<Response>`;
  - `GROUP_PATH` (the RegExp), exported from `src/index.ts`.

- [ ] **Step 1: Install the toolchain with exact versions**

```bash
mkdir -p crew-relay/src crew-relay/test
cd crew-relay
npm init -y
npm view @cloudflare/vitest-pool-workers version peerDependencies
```

Install the newest `@cloudflare/vitest-pool-workers`, plus the `vitest` version its
`peerDependencies` accept:

```bash
npm install --save-dev --save-exact wrangler typescript @cloudflare/workers-types @cloudflare/vitest-pool-workers vitest@<version the peer range accepts>
```

Then read `node_modules/@cloudflare/vitest-pool-workers/README.md` (and its docs link if the
README is short). Write down:
1. the version numbers;
2. the config form (`defineWorkersConfig` from `@cloudflare/vitest-pool-workers/config`, or what
   replaced it);
3. whether WebSockets to Durable Objects need `isolatedStorage: false`;
4. the import names in tests (`SELF`, `env`, `runInDurableObject` from `cloudflare:test`).

These four facts go into `../knowledge/group-sync-transport.md` §1.1 as **[community]** facts with
today's date, committed to the hub repository with `git -C ..`.

Edit `package.json` to:

```json
{
  "name": "edmrn-crew-relay",
  "version": "1.0.0",
  "private": true,
  "license": "AGPL-3.0-only",
  "type": "module",
  "scripts": {
    "test": "vitest run",
    "typecheck": "tsc --noEmit",
    "dev": "wrangler dev",
    "deploy": "wrangler deploy"
  },
  "devDependencies": { "...": "kept exactly as npm wrote them" }
}
```

`tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "es2022",
    "module": "es2022",
    "moduleResolution": "bundler",
    "lib": ["es2022"],
    "types": ["@cloudflare/workers-types", "@cloudflare/vitest-pool-workers"],
    "strict": true,
    "noEmit": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "skipLibCheck": true
  },
  "include": ["src", "test", "vitest.config.ts"]
}
```

`wrangler.toml` (set `compatibility_date` to the newest date the installed Wrangler accepts
without a warning, not later than today):

```toml
name = "edmrn-crew-relay"
main = "src/index.ts"
compatibility_date = "2026-09-01"
workers_dev = false
routes = [{ pattern = "galroute.the-hub.cloud", custom_domain = true }]

[observability]
enabled = true

[[durable_objects.bindings]]
name = "GROUPS"
class_name = "GroupRelay"

[[migrations]]
tag = "v1"
new_sqlite_classes = ["GroupRelay"]

[vars]
# Public crew keys (EDMRN: Settings > Crew... > Your crew key) allowed to create groups; comma-separated.
CREW_LEADERS = ""
# Where the relay's source is published (AGPL-3.0); empty until the GitHub fork exists.
SOURCE_URL = ""
```

`vitest.config.ts`, in the form the README documents. For the 0.8 line it is:

```ts
import { defineWorkersConfig } from '@cloudflare/vitest-pool-workers/config';
import vectors from './test/vectors.json';

export default defineWorkersConfig({
  test: {
    poolOptions: {
      workers: {
        singleWorker: true,
        isolatedStorage: false, // WebSockets to Durable Objects; tests use a fresh group id each
        wrangler: { configPath: './wrangler.toml' },
        miniflare: {
          bindings: { CREW_LEADERS: vectors.keys.leader.author, SOURCE_URL: 'https://example.invalid/edmrn' },
        },
      },
    },
  },
});
```

- [ ] **Step 2: Write the failing tests** (`crew-relay/test/routes.test.ts`)

```ts
import { SELF } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';
import { JOIN_SCRIPT, JOIN_STYLE } from '../src/join';

async function sha(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return 'sha256-' + btoa(String.fromCharCode(...new Uint8Array(digest)));
}

describe('routes', () => {
  it('greets with the source URL', async () => {
    const res = await SELF.fetch('https://relay.test/');
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toContain('text/plain');
    expect(await res.text()).toContain('https://example.invalid/edmrn');
  });

  it('serves the invite page with a hash-only CSP', async () => {
    const res = await SELF.fetch('https://relay.test/join');
    const csp = res.headers.get('content-security-policy')!;
    expect(csp).toContain(`script-src '${await sha(JOIN_SCRIPT)}'`);
    expect(csp).toContain(`style-src '${await sha(JOIN_STYLE)}'`);
    expect(csp).toContain("default-src 'none'");
    const html = await res.text();
    expect(html).toContain(`<script>${JOIN_SCRIPT}</script>`);
    expect(JOIN_SCRIPT).toContain('textContent');
    expect(JOIN_SCRIPT).not.toContain('innerHTML');
  });

  it('refuses unknown paths, bad group ids and plain GETs on the socket path', async () => {
    expect((await SELF.fetch('https://relay.test/nope')).status).toBe(404);
    expect((await SELF.fetch('https://relay.test/v1/groups/not-a-uuid/ws', { headers: { Upgrade: 'websocket' } })).status).toBe(404);
    expect((await SELF.fetch('https://relay.test/v1/groups/01920000-0000-7000-8000-0000000000a1/ws')).status).toBe(426);
  });
});
```

- [ ] **Step 3: Run them and see them fail**

Run: `cd crew-relay && npx vitest run test/routes.test.ts`
Expected: module-not-found failures for `../src/join` and `src/index.ts`.

- [ ] **Step 4: Implement**

`src/join.ts`:

```ts
// The invite landing page (spec B1 S5). The invite lives in the URL fragment, which browsers never
// send to the server; the script shows it as text, never as markup.
export const JOIN_SCRIPT =
  "(function(){var code='';try{code=decodeURIComponent(location.hash.slice(1));}catch(e){}" +
  "var ok=/^edmrn-crew:1:[A-Za-z0-9_-]{1,1990}$/.test(code);var out=document.getElementById('code');" +
  "out.textContent=ok?code:'This link holds no valid EDMRN crew invite.';var b=document.getElementById('copy');" +
  "b.hidden=!ok;b.addEventListener('click',function(){navigator.clipboard.writeText(code).then(function(){b.textContent='Copied';});});})();";

export const JOIN_STYLE =
  'body{font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;background:#111;color:#eee}' +
  '#code{word-break:break-all;background:#222;padding:.75rem;border-radius:.4rem;font-family:monospace}' +
  'button{margin-top:.75rem;padding:.5rem 1rem}';

async function hash(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return 'sha256-' + btoa(String.fromCharCode(...new Uint8Array(digest)));
}

export async function joinPage(): Promise<Response> {
  const html =
    '<!doctype html><html lang="en"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1"><title>EDMRN crew invite</title>' +
    `<style>${JOIN_STYLE}</style></head><body><h1>EDMRN crew invite</h1>` +
    '<p>Copy this code, then in EDMRN open Settings &gt; Crew... &gt; Join with invite and paste it.</p>' +
    `<div id="code"></div><button id="copy" hidden>Copy</button><script>${JOIN_SCRIPT}</script></body></html>`;
  const csp =
    `default-src 'none'; script-src '${await hash(JOIN_SCRIPT)}'; style-src '${await hash(JOIN_STYLE)}'; ` +
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'";
  return new Response(html, {
    headers: {
      'content-type': 'text/html; charset=utf-8',
      'content-security-policy': csp,
      'referrer-policy': 'no-referrer',
      'cache-control': 'no-store',
    },
  });
}
```

`src/index.ts`:

```ts
// EDMRN crew relay (spec B1 S17): routing only; each group is one Durable Object (src/group.ts).
import { joinPage } from './join';

export { GroupRelay } from './group';

export interface Env {
  GROUPS: DurableObjectNamespace;
  SOURCE_URL: string;
  CREW_LEADERS: string;
}

export const GROUP_PATH =
  /^\/v1\/groups\/([0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/ws$/;

function greeting(env: Env): string {
  const source = env.SOURCE_URL ? env.SOURCE_URL : 'not yet published';
  return `EDMRN crew relay. Licence: AGPL-3.0. Source: ${source}\n`;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === 'GET' && url.pathname === '/') {
      return new Response(greeting(env), { headers: { 'content-type': 'text/plain; charset=utf-8' } });
    }
    if (request.method === 'GET' && url.pathname === '/join') {
      return joinPage();
    }
    const match = GROUP_PATH.exec(url.pathname);
    if (match && request.method === 'GET') {
      if (request.headers.get('Upgrade')?.toLowerCase() !== 'websocket') {
        return new Response('expected a WebSocket upgrade\n', { status: 426 });
      }
      const stub = env.GROUPS.get(env.GROUPS.idFromName(match[1]));
      return stub.fetch(request);
    }
    return new Response('not found\n', { status: 404 });
  },
} satisfies ExportedHandler<Env>;
```

`src/group.ts` (stub, replaced in Task 13):

```ts
import { DurableObject } from 'cloudflare:workers';

export class GroupRelay extends DurableObject {
  async fetch(): Promise<Response> {
    return new Response('not implemented\n', { status: 501 });
  }
}
```

Copy the repository's `LICENSE` to `crew-relay/LICENSE`.

- [ ] **Step 5: Run the tests and the type check**

Run: `cd crew-relay && npx vitest run test/routes.test.ts && npx tsc --noEmit`
Expected: 3 passed; no type errors. If `import vectors from './test/vectors.json'` in the config
needs an import attribute (`with { type: 'json' }`) for the installed Vite, add it.

- [ ] **Step 6: Commit** (two repositories)

```bash
git add .gitignore crew-relay/package.json crew-relay/package-lock.json crew-relay/tsconfig.json crew-relay/wrangler.toml crew-relay/vitest.config.ts crew-relay/src/index.ts crew-relay/src/join.ts crew-relay/src/group.ts crew-relay/test/routes.test.ts crew-relay/LICENSE crew-relay/README.md
git commit -m "feat(relay): Worker scaffold, routes and invite page (B1 S5 S17)"
git -C .. add knowledge/group-sync-transport.md
git -C .. commit -m "knowledge: vitest-pool-workers and Wrangler toolchain facts"
```

---

### Task 12: Relay envelope checks and roster

**Files:**
- Create: `crew-relay/src/b64.ts`, `crew-relay/src/canonical.ts`, `crew-relay/src/envelope.ts`,
  `crew-relay/src/roster.ts`
- Test: `crew-relay/test/vectors.test.ts`

**Interfaces:**
- Consumes: `crew-relay/test/vectors.json` (Task 5).
- Produces:
  - `b64.ts`: `b64urlEncode(bytes: Uint8Array): string` and
    `b64urlDecode(text: unknown, maxBytes: number): Uint8Array | null`.
  - `canonical.ts`: `canonicalJson(value: unknown): string`.
  - `envelope.ts`:
    - the constants `MAX_ENVELOPE_BYTES`, `UUID7`, `AUTHOR`, `STAMP_SHAPE`;
    - the type `Envelope`;
    - `isInt(v, lo, hi): v is number`;
    - `checkStructure(value: unknown): Envelope | null`;
    - `parseEnvelope(bytes: Uint8Array): { env: Envelope; text: string } | null`;
    - `isSmallOrder(raw: Uint8Array): boolean`;
    - `verifySignature(author: string, sig: Uint8Array, data: Uint8Array): Promise<boolean>`.
  - `roster.ts`:
    - `ROSTER_KINDS`, `FLOOR_MS`;
    - the types `RosterEntry`, `Member`, `Roster`;
    - `realStamp(value: unknown): number | null`, `plausible(createdAt: unknown, nowMs: number):
      boolean`;
    - `validRosterPayload(kind: string, payload: unknown): boolean`;
    - `rosterEntry(env: Envelope, text: string, nowMs: number): RosterEntry | null`;
    - `computeRoster(entries: RosterEntry[], group: string, leader: string): Promise<Roster>`;
    - `admits(roster: Roster | null, author: string, seq: number): boolean`.

- [ ] **Step 1: Write the failing tests** (`crew-relay/test/vectors.test.ts`)

```ts
import { describe, expect, it } from 'vitest';
import vectors from './vectors.json';
import { b64urlDecode, b64urlEncode } from '../src/b64';
import { parseEnvelope, verifySignature } from '../src/envelope';
import { computeRoster, plausible, realStamp, rosterEntry, validRosterPayload } from '../src/roster';

const v = vectors as any;
const NOW = realStamp(v.now)!;
const raw = (text: string) => b64urlDecode(text, 1 << 20)!;

describe('shared vectors (tests/vectors/make_crew_vectors.py)', () => {
  it('base64url is canonical', () => {
    expect(b64urlDecode(b64urlEncode(new Uint8Array([0, 255, 7])), 3)).toEqual(new Uint8Array([0, 255, 7]));
    for (const bad of ['a=', 'a+b', 'AB', 'A'.repeat(10)]) expect(b64urlDecode(bad, 2)).toBeNull();
  });

  it('structure cases', () => {
    for (const c of v.structure_cases) expect(parseEnvelope(raw(c.e)) !== null, c.name).toBe(c.valid);
  });

  it('signature cases', async () => {
    for (const c of v.signature_cases) {
      expect(await verifySignature(c.author, raw(c.s), raw(c.e)), c.name).toBe(c.valid);
    }
  });

  it('canonical cases', () => {
    for (const c of v.canonical_cases) {
      const parsed = parseEnvelope(raw(c.e));
      const entry = parsed ? rosterEntry(parsed.env, parsed.text, NOW) : null;
      expect(entry !== null, c.name).toBe(c.canonical);
    }
  });

  it('roster payload cases', () => {
    for (const c of v.roster_payload_cases) expect(validRosterPayload(c.kind, c.payload), JSON.stringify(c)).toBe(c.valid);
  });

  it('plausibility cases', () => {
    for (const c of v.plausibility_cases) expect(plausible(c.created_at, NOW), c.created_at).toBe(c.plausible);
  });

  it('roster cases', async () => {
    for (const c of v.roster_cases) {
      const entries = c.envelopes
        .map((name: string) => {
          const parsed = parseEnvelope(raw(v.envelopes[name].e))!;
          return rosterEntry(parsed.env, parsed.text, NOW);
        })
        .filter((e: unknown) => e !== null);
      const roster = await computeRoster(entries, v.group_id, c.leader);
      const members = Object.fromEntries([...roster.members].map(([a, m]) => [a, m.lastSeq]));
      expect(members, c.name).toEqual(c.members);
      expect(roster.name, c.name).toBe(c.name_of_group);
    }
  });

  it('auth signature', async () => {
    const a = v.auth;
    const msg = new TextEncoder().encode(`edmrn-crew-auth\n${a.group}\n${a.nonce}`);
    expect(await verifySignature(a.author, raw(a.sig), msg)).toBe(true);
  });
});
```

- [ ] **Step 2: Run them and see them fail**

Run: `cd crew-relay && npx vitest run test/vectors.test.ts`
Expected: module-not-found failures for `../src/b64`, `../src/envelope` and `../src/roster`.

- [ ] **Step 3: Implement**

`src/b64.ts`:

```ts
export function b64urlEncode(bytes: Uint8Array): string {
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** Bytes from canonical unpadded base64url of at most maxBytes bytes, else null. */
export function b64urlDecode(text: unknown, maxBytes: number): Uint8Array | null {
  if (typeof text !== 'string' || !/^[A-Za-z0-9_-]*$/.test(text) || text.length > Math.ceil((maxBytes * 4) / 3)) {
    return null;
  }
  let binary: string;
  try {
    binary = atob(text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (text.length % 4)) % 4));
  } catch {
    return null;
  }
  const out = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  if (out.length > maxBytes || b64urlEncode(out) !== text) return null;
  return out;
}
```

`src/canonical.ts`:

```ts
/** Sorted keys, no spaces; equal to Python's json.dumps(sort_keys, separators=(',', ':'),
 *  ensure_ascii=False) for the ASCII strings and safe integers of roster envelopes. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonicalJson).join(',') + ']';
  const record = value as Record<string, unknown>;
  return '{' + Object.keys(record).sort().map((k) => JSON.stringify(k) + ':' + canonicalJson(record[k])).join(',') + '}';
}
```

`src/envelope.ts`:

```ts
// Envelope structure and signatures (Spec A C2, crew sync core S18), mirroring
// edmrn/ext/crew_events.py check_structure and crew_identity.verify.
import { b64urlDecode } from './b64';

export const MAX_ENVELOPE_BYTES = 16 * 1024;
export const UUID7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const AUTHOR = /^[A-Za-z0-9_-]{43}$/;
export const STAMP_SHAPE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/;
const HEX_ID = /^[0-9a-f]{32}$/;
const KIND = /^[a-z_]{1,32}$/;
const KEYS = 'author,created_at,event_id,kind,payload,seq,v';

export interface Envelope {
  v: number;
  event_id: string;
  author: string;
  seq: number;
  kind: string;
  created_at: string;
  payload: Record<string, unknown>;
}

export function isInt(v: unknown, lo: number, hi: number): v is number {
  return typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi;
}

export function checkStructure(value: unknown): Envelope | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const e = value as Record<string, unknown>;
  if (Object.keys(e).sort().join(',') !== KEYS) return null;
  if (!isInt(e.v, 1, 1000)) return null;
  if (typeof e.event_id !== 'string' || !(HEX_ID.test(e.event_id) || UUID7.test(e.event_id))) return null;
  if (typeof e.author !== 'string' || !AUTHOR.test(e.author)) return null;
  if (!isInt(e.seq, 1, Number.MAX_SAFE_INTEGER)) return null;
  if (typeof e.kind !== 'string' || !KIND.test(e.kind)) return null;
  if (typeof e.created_at !== 'string' || !STAMP_SHAPE.test(e.created_at)) return null;
  if (typeof e.payload !== 'object' || e.payload === null || Array.isArray(e.payload)) return null;
  return e as unknown as Envelope;
}

// ignoreBOM keeps a leading U+FEFF, which JSON.parse then refuses, as Python does.
const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });

export function parseEnvelope(bytes: Uint8Array): { env: Envelope; text: string } | null {
  if (bytes.length > MAX_ENVELOPE_BYTES) return null;
  let text: string;
  let value: unknown;
  try {
    text = decoder.decode(bytes);
    value = JSON.parse(text);
  } catch {
    return null;
  }
  const env = checkStructure(value);
  return env ? { env, text } : null;
}

// libsodium's small-order blocklist, compared with the top bit of the last byte cleared
// (edmrn/ext/crew_identity.py _SMALL_ORDER).
const SMALL_ORDER = [
  '0000000000000000000000000000000000000000000000000000000000000000',
  '0100000000000000000000000000000000000000000000000000000000000000',
  '26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05',
  'c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a',
  'ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f',
  'edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f',
  'eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f',
];

export function isSmallOrder(raw: Uint8Array): boolean {
  const key = raw.slice();
  key[31] &= 0x7f;
  const hex = [...key].map((b) => b.toString(16).padStart(2, '0')).join('');
  return SMALL_ORDER.includes(hex);
}

const keyCache = new Map<string, CryptoKey>();

export async function verifySignature(author: string, sig: Uint8Array, data: Uint8Array): Promise<boolean> {
  const raw = b64urlDecode(author, 32);
  if (!raw || raw.length !== 32 || sig.length !== 64 || isSmallOrder(raw)) return false;
  try {
    let key = keyCache.get(author);
    if (!key) {
      key = await crypto.subtle.importKey('raw', raw, { name: 'Ed25519' }, false, ['verify']);
      if (keyCache.size > 1000) keyCache.clear();
      keyCache.set(author, key);
    }
    return await crypto.subtle.verify({ name: 'Ed25519' }, key, sig, data);
  } catch {
    return false;
  }
}
```

`src/roster.ts`:

```ts
// Group membership (crew sync core S1, S2, ADR-0008), mirroring edmrn/ext/crew_roster.py.
// The shared vectors in test/vectors.json pin both implementations to the same results.
import { canonicalJson } from './canonical';
import { AUTHOR, UUID7, isInt, type Envelope } from './envelope';

export const ROSTER_KINDS = new Set(['group', 'invite', 'join', 'remove']);
export const FLOOR_MS = Date.UTC(2014, 11, 16);
const SLACK_MS = 24 * 3600 * 1000;
const NAME = /^[\x20-\x7e]{1,64}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const STAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$/;

export interface RosterEntry {
  author: string;
  seq: number;
  kind: string;
  created_at: string;
  payload: Record<string, any>;
}

export interface Member {
  author: string;
  commander: string | null;
  leader: boolean;
  lastSeq: number | null;
}

export interface Roster {
  group: string;
  leader: string;
  name: string | null;
  members: Map<string, Member>;
}

/** Milliseconds of a real calendar time in the envelope's stamp shape, else null. */
export function realStamp(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  const m = STAMP.exec(value);
  if (!m) return null;
  const [y, mo, d, h, mi, s] = m.slice(1).map(Number);
  if (y < 1) return null;
  const date = new Date(0);
  date.setUTCFullYear(y, mo - 1, d);
  date.setUTCHours(h, mi, s, 0);
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d ||
      date.getUTCHours() !== h || date.getUTCMinutes() !== mi || date.getUTCSeconds() !== s) {
    return null;
  }
  return date.getTime();
}

export function plausible(createdAt: unknown, nowMs: number): boolean {
  const t = realStamp(createdAt);
  return t !== null && t >= FLOOR_MS && t <= nowMs + SLACK_MS;
}

function exactKeys(p: Record<string, unknown>, keys: string): boolean {
  return Object.keys(p).sort().join(',') === keys;
}

const isName = (v: unknown) => typeof v === 'string' && NAME.test(v);
const isUuid = (v: unknown) => typeof v === 'string' && UUID7.test(v);
const isHex64 = (v: unknown) => typeof v === 'string' && HEX64.test(v);

export function validRosterPayload(kind: string, payload: unknown): boolean {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return false;
  const p = payload as Record<string, unknown>;
  switch (kind) {
    case 'group':
      return exactKeys(p, 'commander,group_id,name') && isUuid(p.group_id) && isName(p.name) && isName(p.commander);
    case 'invite':
      return exactKeys(p, 'expires_at,group_id,invite_id,label,token_hash') && isUuid(p.group_id) &&
        isUuid(p.invite_id) && isHex64(p.token_hash) && realStamp(p.expires_at) !== null &&
        (p.label === null || isName(p.label));
    case 'join':
      return exactKeys(p, 'commander,group_id,invite_id,token') && isUuid(p.group_id) &&
        isUuid(p.invite_id) && isHex64(p.token) && isName(p.commander);
    case 'remove':
      return exactKeys(p, 'group_id,last_seq,member') && isUuid(p.group_id) &&
        typeof p.member === 'string' && AUTHOR.test(p.member) && isInt(p.last_seq, 0, Number.MAX_SAFE_INTEGER);
    default:
      return false;
  }
}

/** A roster envelope that may count toward membership, else null (S1, S18). */
export function rosterEntry(env: Envelope, text: string, nowMs: number): RosterEntry | null {
  if (!ROSTER_KINDS.has(env.kind) || env.v !== 1 || !UUID7.test(env.event_id)) return null;
  if (canonicalJson(env) !== text) return null;
  if (!validRosterPayload(env.kind, env.payload)) return null;
  if (!plausible(env.created_at, nowMs)) return null;
  return { author: env.author, seq: env.seq, kind: env.kind, created_at: env.created_at, payload: env.payload };
}

async function sha256hex(hex: string): Promise<string> {
  const bytes = new Uint8Array(hex.match(/../g)!.map((h) => parseInt(h, 16)));
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return [...digest].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function byOrder(a: RosterEntry, b: RosterEntry): number {
  if (a.created_at !== b.created_at) return a.created_at < b.created_at ? -1 : 1;
  if (a.author !== b.author) return a.author < b.author ? -1 : 1;
  return a.seq - b.seq;
}

export async function computeRoster(entries: RosterEntry[], group: string, leader: string): Promise<Roster> {
  const mine = entries.filter((e) => e.payload.group_id === group).sort(byOrder);
  let name: string | null = null;
  let leaderCommander: string | null = null;
  const invites = new Map<string, RosterEntry>();
  for (const e of mine) {
    if (e.author !== leader) continue;
    if (e.kind === 'group' && name === null) {
      name = e.payload.name;
      leaderCommander = e.payload.commander;
    } else if (e.kind === 'invite' && !invites.has(e.payload.invite_id)) {
      invites.set(e.payload.invite_id, e);
    }
  }
  const admitted = new Map<string, string>();
  const redeemed = new Set<string>();
  for (const e of mine) {
    if (e.kind !== 'join' || e.author === leader) continue;
    const invite = invites.get(e.payload.invite_id);
    if (!invite || redeemed.has(e.payload.invite_id)) continue;
    if ((await sha256hex(e.payload.token)) !== invite.payload.token_hash) continue;
    if (e.created_at > invite.payload.expires_at) continue;
    redeemed.add(e.payload.invite_id);
    if (!admitted.has(e.author)) admitted.set(e.author, e.payload.commander);
  }
  const caps = new Map<string, number>();
  for (const e of mine) {
    if (e.kind === 'remove' && e.author === leader && e.payload.member !== leader) {
      const last = e.payload.last_seq as number;
      caps.set(e.payload.member, Math.min(caps.get(e.payload.member) ?? last, last));
    }
  }
  const members = new Map<string, Member>([[leader, { author: leader, commander: leaderCommander, leader: true, lastSeq: null }]]);
  for (const [author, commander] of admitted) {
    members.set(author, { author, commander, leader: false, lastSeq: caps.get(author) ?? null });
  }
  return { group, leader, name, members };
}

export function admits(roster: Roster | null, author: string, seq: number): boolean {
  const member = roster?.members.get(author);
  return member !== undefined && (member.lastSeq === null || seq <= member.lastSeq);
}
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `cd crew-relay && npx vitest run && npx tsc --noEmit`
Expected: all pass. A failing vector means the two implementations disagree. Find which side
contradicts the spec, fix that side, and never edit `vectors.json` by hand.

- [ ] **Step 5: Commit**

```bash
git add crew-relay/src/b64.ts crew-relay/src/canonical.ts crew-relay/src/envelope.ts crew-relay/src/roster.ts crew-relay/test/vectors.test.ts
git commit -m "feat(relay): envelope checks, Ed25519 verification and roster, pinned by shared vectors (B1 S2 S18)"
```

---

### Task 13: The group Durable Object: auth, heads, catch-up, quotas, removal

**Files:**
- Modify (replace the stub): `crew-relay/src/group.ts`
- Create: `crew-relay/test/helpers.ts`
- Test: `crew-relay/test/group.test.ts`

**Interfaces:**
- Consumes: Task 12 (all exports), Task 11's `Env`.
- Produces (`src/group.ts`):
  - the constants `MAX_FRAME_CHARS`, `MAX_EVS_ITEMS`, `MAX_EVS_CHARS`, `MAX_STATUS_BYTES`,
    `MEMBER_DAILY`, `GROUP_DAILY`, `STATUS_INTERVAL_MS`, `AUTH_GRACE_MS`, `CLOSE_REPLACED`,
    `CLOSE_BAD_AUTH`, `CLOSE_FORBIDDEN`, `CLOSE_POLICY`;
  - `utcDay(ms)`;
  - `class GroupRelay` with:
    - the public `writes: number` (SQLite rows written since the instance started) and
      `dailyTotals: string[]`;
    - `exec<T>(query, ...bindings): T[]`, which tests use for setup;
    - the hibernation handlers `fetch`, `webSocketMessage`, `webSocketClose` and
      `webSocketError`.
  - `test/helpers.ts`:
    - `keyFor(name)`, `envelope(key, seq, kind, payload, createdAt?, eventId?)`;
    - `stamp(offsetMs)`, `newGroup()`, `open(group)`, `authed(group, key, extra?)`;
    - the class `Client` with `next`, `nextOf`, `send` and `closed`;
    - `relayFor(group)`, which returns the stub for `runInDurableObject`.

- [ ] **Step 1: Write the test helpers** (`crew-relay/test/helpers.ts`)

```ts
import { SELF, env } from 'cloudflare:test';
import vectors from './vectors.json';
import { b64urlEncode } from '../src/b64';
import { canonicalJson } from '../src/canonical';

const v = vectors as any;
const PKCS8_ED25519_PREFIX = [0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20];

export interface Key {
  author: string;
  sign(data: Uint8Array): Promise<Uint8Array>;
}

export async function keyFor(name: 'leader' | 'alice' | 'bob' | 'carol' | 'mallory'): Promise<Key> {
  const seed = new Uint8Array(v.keys[name].seed.match(/../g).map((h: string) => parseInt(h, 16)));
  const pkcs8 = new Uint8Array([...PKCS8_ED25519_PREFIX, ...seed]);
  const key = await crypto.subtle.importKey('pkcs8', pkcs8, { name: 'Ed25519' }, false, ['sign']);
  return {
    author: v.keys[name].author,
    sign: async (data) => new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, key, data)),
  };
}

let counter = 0x1000;
export function uuid7(): string {
  counter += 1;
  return `01920000-0000-7000-8000-${counter.toString(16).padStart(12, '0')}`;
}

export function newGroup(): string {
  return uuid7();
}

/** A stamp `offsetMs` from now, in the envelope shape. */
export function stamp(offsetMs = 0): string {
  return new Date(Date.now() + offsetMs).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

export interface Item { e: string; s: string; }

export async function envelope(key: Key, seq: number, kind: string, payload: Record<string, unknown>,
                               createdAt = stamp(-60_000), eventId = uuid7()): Promise<Item> {
  const env = { v: 1, event_id: eventId, author: key.author, seq, kind, created_at: createdAt, payload };
  const bytes = new TextEncoder().encode(canonicalJson(env));
  return { e: b64urlEncode(bytes), s: b64urlEncode(await key.sign(bytes)) };
}

export async function sha256hex(hex: string): Promise<string> {
  const bytes = new Uint8Array(hex.match(/../g)!.map((h) => parseInt(h, 16)));
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return [...digest].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export class Client {
  private queue: any[] = [];
  private waiters: ((m: any) => void)[] = [];
  readonly closed: Promise<{ code: number; reason: string }>;

  constructor(readonly ws: WebSocket) {
    ws.addEventListener('message', (event) => {
      const message = JSON.parse(event.data as string);
      const waiter = this.waiters.shift();
      if (waiter) waiter(message);
      else this.queue.push(message);
    });
    this.closed = new Promise((resolve) =>
      ws.addEventListener('close', (event) => resolve({ code: event.code, reason: event.reason })));
  }

  next(timeoutMs = 2000): Promise<any> {
    const queued = this.queue.shift();
    if (queued) return Promise.resolve(queued);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('no message')), timeoutMs);
      this.waiters.push((m) => { clearTimeout(timer); resolve(m); });
    });
  }

  async nextOf(t: string, timeoutMs = 2000): Promise<any> {
    for (;;) {
      const message = await this.next(timeoutMs);
      if (message.t === t) return message;
    }
  }

  /** Every message that arrives within `ms`. */
  async drain(ms = 200): Promise<any[]> {
    await new Promise((r) => setTimeout(r, ms));
    const out = this.queue;
    this.queue = [];
    return out;
  }

  send(frame: unknown): void {
    this.ws.send(JSON.stringify(frame));
  }
}

export async function open(group: string): Promise<Client> {
  const res = await SELF.fetch(`https://relay.test/v1/groups/${group}/ws`, { headers: { Upgrade: 'websocket' } });
  const ws = res.webSocket!;
  ws.accept();
  return new Client(ws);
}

export async function authed(group: string, key: Key, extra: Record<string, unknown> = {}): Promise<Client> {
  const client = await open(group);
  const hello = await client.nextOf('hello');
  const sig = await key.sign(new TextEncoder().encode(`edmrn-crew-auth\n${group}\n${hello.nonce}`));
  client.send({ t: 'auth', group, author: key.author, sig: b64urlEncode(sig), ...extra });
  return client;
}

export function relayFor(group: string): DurableObjectStub {
  return env.GROUPS.get(env.GROUPS.idFromName(group));
}

export async function invite(group: string, leader: Client, leaderKey: Key, seq: number, token: string,
                             expiresInMs = 3_600_000): Promise<string> {
  const inviteId = uuid7();
  leader.send({ t: 'evs', items: [await envelope(leaderKey, seq, 'invite', {
    group_id: group, invite_id: inviteId, token_hash: await sha256hex(token),
    expires_at: stamp(expiresInMs), label: null,
  })] });
  return inviteId;
}
```

   `createGroupAndUpload` stores the genesis at auth, which leaves the leader's head at 0 (S7), and then
   uploads it through `evs`, as the real client does: Task 6 uploads every envelope above the
   relay's head.

```ts
export async function createGroupAndUpload(group: string, leader: Key): Promise<{ client: Client; genesis: Item }> {
  const genesis = await envelope(leader, 1, 'group', { group_id: group, name: 'Crew', commander: 'Leader' });
  const client = await authed(group, leader, { genesis });
  await client.nextOf('have');
  client.send({ t: 'have', heads: {} });
  await client.nextOf('synced');
  client.send({ t: 'evs', items: [genesis] });
  return { client, genesis };
}
```

- [ ] **Step 2: Write the failing tests** (`crew-relay/test/group.test.ts`)

```ts
import { runInDurableObject } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';
import { b64urlDecode } from '../src/b64';
import { GROUP_DAILY, MEMBER_DAILY, utcDay, type GroupRelay } from '../src/group';
import {
  authed, createGroupAndUpload, envelope, invite, keyFor, newGroup, open, relayFor, sha256hex, stamp, type Key,
} from './helpers';

const TOKEN = '11'.repeat(32);
const decode = (item: { e: string }) => JSON.parse(new TextDecoder().decode(b64urlDecode(item.e, 1 << 20)!));
const items = (messages: any[]) => messages.filter((m) => m.t === 'evs').flatMap((m) => m.items);
const visit = (key: Key, seq: number) =>
  envelope(key, seq, 'visit', { commander: 'A', system: 'Sol', address: String(seq), coords: null });

async function groupWithInvite(group: string) {
  const leader = await keyFor('leader');
  const alice = await keyFor('alice');
  const { client: leaderClient } = await createGroupAndUpload(group, leader);
  const inviteId = await invite(group, leaderClient, leader, 2, TOKEN);
  await leaderClient.drain();
  return { leader, alice, leaderClient, inviteId };
}

async function joinEnvelope(group: string, alice: Key, inviteId: string, seq: number) {
  return envelope(alice, seq, 'join', { group_id: group, invite_id: inviteId, token: TOKEN, commander: 'Alice' });
}

describe('auth', () => {
  it('an empty group refuses auth without a genesis', async () => {
    const client = await authed(newGroup(), await keyFor('leader'));
    expect((await client.closed).code).toBe(4003);
  });

  it('a genesis by an author not in CREW_LEADERS is refused', async () => {
    const group = newGroup();
    const alice = await keyFor('alice');
    const genesis = await envelope(alice, 1, 'group', { group_id: group, name: 'Crew', commander: 'A' });
    expect((await (await authed(group, alice, { genesis })).closed).code).toBe(4003);
  });

  it('a bad signature closes with 4001', async () => {
    const group = newGroup();
    const client = await open(group);
    await client.nextOf('hello');
    client.send({ t: 'auth', group, author: (await keyFor('leader')).author, sig: 'A'.repeat(86) });
    expect((await client.closed).code).toBe(4001);
  });

  it('a join admits; a non-member without a join is refused', async () => {
    const group = newGroup();
    const { alice, inviteId } = await groupWithInvite(group);
    expect((await (await authed(group, await keyFor('bob'))).closed).code).toBe(4003);
    const client = await authed(group, alice, { join: await joinEnvelope(group, alice, inviteId, 1) });
    expect((await client.nextOf('have')).heads[alice.author]).toBe(0);
  });

  it('an invite past its expiry on the relay clock does not admit', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const alice = await keyFor('alice');
    const { client: leaderClient } = await createGroupAndUpload(group, leader);
    const inviteId = await invite(group, leaderClient, leader, 2, TOKEN, -1000);
    await leaderClient.drain();
    const join = await envelope(alice, 1, 'join', { group_id: group, invite_id: inviteId, token: TOKEN, commander: 'Alice' }, stamp(-120_000));
    expect((await (await authed(group, alice, { join })).closed).code).toBe(4003);
  });

  it('a second connection of the same author replaces the first', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client: first } = await createGroupAndUpload(group, leader);
    const second = await authed(group, leader);
    await second.nextOf('have');
    expect((await first.closed).code).toBe(4000);
  });

  it('an unauthenticated socket older than 10 s is closed on the next activity', async () => {
    const group = newGroup();
    const idle = await open(group);
    await idle.nextOf('hello');
    await runInDurableObject(relayFor(group), async (_instance: GroupRelay, state) => {
      for (const ws of state.getWebSockets()) {
        const att = ws.deserializeAttachment();
        if (att.state === 'hello') ws.serializeAttachment({ ...att, openedAt: Date.now() - 11_000 });
      }
    });
    await createGroupAndUpload(group, await keyFor('leader'));
    expect((await idle.closed).code).toBe(4001);
  });
});

describe('heads and catch-up', () => {
  it('a join at auth does not move the head; earlier envelopes still upload; nothing above the head is served', async () => {
    const group = newGroup();
    const { leader, alice, inviteId } = await groupWithInvite(group);
    const join = await joinEnvelope(group, alice, inviteId, 3);
    const client = await authed(group, alice, { join });
    expect((await client.nextOf('have')).heads[alice.author]).toBe(0);
    const watcher = await authed(group, leader);
    await watcher.nextOf('have');
    watcher.send({ t: 'have', heads: {} });
    expect(items(await watcher.drain(300)).some((i: any) => decode(i).author === alice.author)).toBe(false);
    client.send({ t: 'evs', items: [await visit(alice, 1), await visit(alice, 2), join] });
    expect(items(await watcher.drain(300)).map((i: any) => decode(i).seq)).toEqual([1, 2, 3]);
  });

  it('a gap in seq is accepted and duplicates are ignored', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    const item = await visit(leader, 5);
    client.send({ t: 'evs', items: [item] });
    client.send({ t: 'evs', items: [item] });
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(instance.exec<{ seq: number }>('SELECT seq FROM heads WHERE author = ?', leader.author)[0].seq).toBe(5);
      expect(instance.exec('SELECT 1 FROM log WHERE author = ?', leader.author).length).toBe(2);
    });
  });

  it('catch-up sends exactly what the client lacks, in ascending seq', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    const all = [];
    for (let seq = 2; seq <= 150; seq++) all.push(await visit(leader, seq));
    client.send({ t: 'evs', items: all.slice(0, 100) });
    client.send({ t: 'evs', items: all.slice(100) });
    await client.drain();
    const other = await authed(group, leader);
    await other.nextOf('have');
    other.send({ t: 'have', heads: { [leader.author]: 120 } });
    expect(items(await other.drain(300)).map((i: any) => decode(i).seq)).toEqual(Array.from({ length: 30 }, (_, i) => 121 + i));
  });

  it('a re-seed by the leader admits the members again from their stored joins', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const alice = await keyFor('alice');
    const inviteId = '01920000-0000-7000-8000-00000000beef';
    const genesis = await envelope(leader, 1, 'group', { group_id: group, name: 'Crew', commander: 'Leader' });
    const inviteEnv = await envelope(leader, 2, 'invite', {
      group_id: group, invite_id: inviteId, token_hash: await sha256hex(TOKEN), expires_at: stamp(3_600_000), label: null,
    });
    const join = await joinEnvelope(group, alice, inviteId, 1);
    const leaderClient = await authed(group, leader, { genesis });
    await leaderClient.nextOf('have');
    leaderClient.send({ t: 'evs', items: [genesis, inviteEnv] });
    leaderClient.send({ t: 'evs', items: [join] }); // the leader offers alice's join from its replica
    const fresh = await leaderClient.nextOf('have');
    expect(fresh.heads[alice.author]).toBe(1);
    const aliceClient = await authed(group, alice);
    expect((await aliceClient.nextOf('have')).heads[alice.author]).toBe(1);
  });
});

describe('quotas and removal', () => {
  it('the member quota refuses the rest of that author in the frame', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      instance.exec('INSERT INTO quota (day, author, n) VALUES (?, ?, ?) ON CONFLICT (day, author) DO UPDATE SET n = excluded.n',
        utcDay(Date.now()), leader.author, MEMBER_DAILY - 1);
    });
    client.send({ t: 'evs', items: [await visit(leader, 2), await visit(leader, 3)] });
    expect(await client.nextOf('err')).toMatchObject({ code: 'quota', author: leader.author, seq: 3 });
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(instance.exec<{ seq: number }>('SELECT seq FROM heads WHERE author = ?', leader.author)[0].seq).toBe(2);
    });
  });

  it('the group quota refuses; roster envelopes are exempt', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      instance.exec('INSERT INTO quota (day, author, n) VALUES (?, ?, ?)', utcDay(Date.now()), 'X'.repeat(43), GROUP_DAILY);
    });
    client.send({ t: 'evs', items: [await visit(leader, 2)] });
    expect((await client.nextOf('err')).code).toBe('group_quota');
    await invite(group, client, leader, 2, TOKEN);
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(instance.exec('SELECT 1 FROM roster WHERE kind = ?', 'invite').length).toBe(1);
    });
  });

  it('removal closes the member and caps what is accepted and served', async () => {
    const group = newGroup();
    const { leader, alice, leaderClient, inviteId } = await groupWithInvite(group);
    const join = await joinEnvelope(group, alice, inviteId, 1);
    const aliceClient = await authed(group, alice, { join });
    await aliceClient.nextOf('have');
    aliceClient.send({ t: 'evs', items: [join, await visit(alice, 2), await visit(alice, 3)] });
    await aliceClient.drain();
    leaderClient.send({ t: 'evs', items: [await envelope(leader, 3, 'remove', { group_id: group, member: alice.author, last_seq: 2 })] });
    expect((await aliceClient.closed).code).toBe(4003);
    const watcher = await authed(group, leader);
    await watcher.nextOf('have');
    watcher.send({ t: 'have', heads: {} });
    const served = items(await watcher.drain(300)).map(decode).filter((e: any) => e.author === alice.author).map((e: any) => e.seq);
    expect(served).toEqual([1, 2]);
  });

  it('bad frames answer bad_frame; oversize frames close with 4008', async () => {
    const group = newGroup();
    const { client } = await createGroupAndUpload(group, await keyFor('leader'));
    await client.drain();
    client.ws.send('not json');
    expect((await client.nextOf('err')).code).toBe('bad_frame');
    client.send({ t: 'evs', items: new Array(101).fill({ e: 'A', s: 'A' }) });
    expect((await client.nextOf('err')).code).toBe('bad_frame');
    client.ws.send('x'.repeat((1 << 20) + 1));
    expect((await client.closed).code).toBe(4008);
  });
});
```

- [ ] **Step 3: Run them and see them fail**

Run: `cd crew-relay && npx vitest run test/group.test.ts`
Expected: failures, because the stub answers 501.

- [ ] **Step 4: Implement `src/group.ts`**

```ts
// One Durable Object per crew group (spec B1 S7, S8, S13, S14, S18). SQLite storage, hibernatable
// WebSockets. Rows written are the Free plan's binding limit: the log is WITHOUT ROWID with one key,
// heads and quota are written once per frame and author, status once per closed socket.
import { DurableObject } from 'cloudflare:workers';
import type { Env } from './index';
import { b64urlDecode } from './b64';
import { AUTHOR, MAX_ENVELOPE_BYTES, parseEnvelope, verifySignature, type Envelope } from './envelope';
import { ROSTER_KINDS, admits, computeRoster, realStamp, rosterEntry, type Roster, type RosterEntry } from './roster';

export const MAX_FRAME_CHARS = 1 << 20;
export const MAX_EVS_ITEMS = 100;
export const MAX_EVS_CHARS = 512 * 1024;
export const MAX_STATUS_BYTES = 8 * 1024;
export const MEMBER_DAILY = 5000;
export const GROUP_DAILY = 80000;
export const STATUS_INTERVAL_MS = 30_000;
export const AUTH_GRACE_MS = 10_000;
export const CLOSE_REPLACED = 4000;
export const CLOSE_BAD_AUTH = 4001;
export const CLOSE_FORBIDDEN = 4003;
export const CLOSE_POLICY = 4008;
const DAY_MS = 86_400_000;

export function utcDay(ms: number): number {
  return Math.floor(ms / DAY_MS);
}

interface Item { e: string; s: string; }

interface Attachment {
  state: 'hello' | 'member';
  group: string;
  nonce: string;
  openedAt: number;
  author?: string;
  statusAt?: number;
  status?: Item;
}

const SCHEMA = [
  'CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID',
  'CREATE TABLE log (author TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, env BLOB NOT NULL, sig BLOB NOT NULL, PRIMARY KEY (author, seq)) WITHOUT ROWID',
  'CREATE TABLE heads (author TEXT PRIMARY KEY, seq INTEGER NOT NULL) WITHOUT ROWID',
  'CREATE TABLE quota (day INTEGER NOT NULL, author TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (day, author)) WITHOUT ROWID',
  'CREATE TABLE roster (author TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (author, seq)) WITHOUT ROWID',
  'CREATE TABLE status (author TEXT PRIMARY KEY, e TEXT NOT NULL, s TEXT NOT NULL) WITHOUT ROWID',
];

const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });

export class GroupRelay extends DurableObject<Env> {
  private sql: SqlStorage;
  private roster: Roster | null = null;
  private rosterLoaded = false;
  private cleanedDay = -1;
  writes = 0;
  dailyTotals: string[] = [];

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    try {
      this.sql.exec('SELECT 1 FROM meta LIMIT 1');
    } catch {
      for (const statement of SCHEMA) this.exec(statement);
    }
  }

  exec<T = Record<string, SqlStorageValue>>(query: string, ...bindings: unknown[]): T[] {
    const cursor = this.sql.exec(query, ...bindings);
    const rows = cursor.toArray() as T[];
    this.writes += cursor.rowsWritten;
    return rows;
  }

  // --- WebSocket entry -----------------------------------------------------------------

  async fetch(request: Request): Promise<Response> {
    const group = new URL(request.url).pathname.split('/')[3];
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.ctx.acceptWebSocket(server);
    const nonce = b64(crypto.getRandomValues(new Uint8Array(32)));
    const attachment: Attachment = { state: 'hello', group, nonce, openedAt: Date.now() };
    server.serializeAttachment(attachment);
    server.send(JSON.stringify({ t: 'hello', v: 1, source: this.env.SOURCE_URL || '', nonce }));
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    this.sweep();
    if (typeof message !== 'string' || message.length > MAX_FRAME_CHARS) {
      ws.close(CLOSE_POLICY, 'too_big');
      return;
    }
    let frame: any;
    try {
      frame = JSON.parse(message);
    } catch {
      return this.error(ws, 'bad_frame');
    }
    if (typeof frame !== 'object' || frame === null || typeof frame.t !== 'string') return this.error(ws, 'bad_frame');
    const att = ws.deserializeAttachment() as Attachment;
    if (att.state === 'hello') {
      if (frame.t === 'auth') await this.auth(ws, att, frame);
      else ws.close(CLOSE_BAD_AUTH, 'auth first');
      return;
    }
    if (frame.t === 'have') await this.catchUp(ws, frame);
    else if (frame.t === 'evs') await this.receive(ws, frame);
    else if (frame.t === 'st') await this.status(ws, att, frame);
  }

  async webSocketClose(ws: WebSocket, code: number, reason: string): Promise<void> {
    this.persistStatus(ws);
    try {
      ws.close(code === 1005 || code === 1006 ? 1000 : code, reason);
    } catch {
      // already closed
    }
  }

  async webSocketError(ws: WebSocket): Promise<void> {
    this.persistStatus(ws);
  }

  // --- helpers ---------------------------------------------------------------------------

  private error(ws: WebSocket, code: string, author?: string, seq?: number): void {
    const frame: Record<string, unknown> = { t: 'err', code };
    if (author !== undefined) frame.author = author;
    if (seq !== undefined) frame.seq = seq;
    try {
      ws.send(JSON.stringify(frame));
    } catch {
      // the socket closed meanwhile
    }
  }

  private members(): WebSocket[] {
    return this.ctx.getWebSockets().filter((ws) => (ws.deserializeAttachment() as Attachment)?.state === 'member');
  }

  private broadcast(except: WebSocket | null, frame: unknown): void {
    const text = JSON.stringify(frame);
    for (const ws of this.members()) {
      if (ws === except) continue;
      try {
        ws.send(text);
      } catch {
        // closing socket
      }
    }
  }

  /** `evs` frames of at most 100 items and 512 KiB of items. */
  private sendItems(send: (frame: unknown) => void, items: Item[]): void {
    let batch: Item[] = [];
    let size = 0;
    for (const item of items) {
      const itemSize = item.e.length + item.s.length + 16;
      if (batch.length && (batch.length >= MAX_EVS_ITEMS || size + itemSize > MAX_EVS_CHARS)) {
        send({ t: 'evs', items: batch });
        batch = [];
        size = 0;
      }
      batch.push(item);
      size += itemSize;
    }
    if (batch.length) send({ t: 'evs', items: batch });
  }

  private sweep(): void {
    const now = Date.now();
    for (const ws of this.ctx.getWebSockets()) {
      const att = ws.deserializeAttachment() as Attachment;
      if (att?.state === 'hello' && now - att.openedAt > AUTH_GRACE_MS) ws.close(CLOSE_BAD_AUTH, 'auth timeout');
    }
  }

  private meta(): { group: string; leader: string } | null {
    const rows = this.exec<{ k: string; v: string }>('SELECT k, v FROM meta');
    const map = Object.fromEntries(rows.map((r) => [r.k, r.v]));
    return map.group && map.leader ? { group: map.group, leader: map.leader } : null;
  }

  private leaders(): Set<string> {
    return new Set((this.env.CREW_LEADERS || '').split(/[\s,]+/).filter(Boolean));
  }

  private rosterEntries(): RosterEntry[] {
    return this.exec<{ author: string; seq: number; kind: string; created_at: string; payload: string }>(
      'SELECT author, seq, kind, created_at, payload FROM roster',
    ).map((r) => ({ author: r.author, seq: r.seq, kind: r.kind, created_at: r.created_at, payload: JSON.parse(r.payload) }));
  }

  private async currentRoster(): Promise<Roster | null> {
    if (this.rosterLoaded) return this.roster;
    const meta = this.meta();
    this.roster = meta ? await computeRoster(this.rosterEntries(), meta.group, meta.leader) : null;
    this.rosterLoaded = true;
    return this.roster;
  }

  private insertRoster(entry: RosterEntry): boolean {
    const before = this.writes;
    this.exec('INSERT OR IGNORE INTO roster (author, seq, kind, created_at, payload) VALUES (?, ?, ?, ?, ?)',
      entry.author, entry.seq, entry.kind, entry.created_at, JSON.stringify(entry.payload));
    return this.writes > before;
  }

  private heads(): Map<string, number> {
    return new Map(this.exec<{ author: string; seq: number }>('SELECT author, seq FROM heads').map((r) => [r.author, r.seq]));
  }

  // --- auth ------------------------------------------------------------------------------

  /** A genesis or join presented with auth: stored in the log without moving the head (S7). */
  private async authEnvelope(item: any, author: string, kind: string, group: string, leader: string): Promise<RosterEntry | null> {
    const e = b64urlDecode(item?.e, MAX_ENVELOPE_BYTES);
    const s = b64urlDecode(item?.s, 64);
    if (!e || !s || s.length !== 64) return null;
    const parsed = parseEnvelope(e);
    if (!parsed || parsed.env.author !== author || parsed.env.kind !== kind) return null;
    const now = Date.now();
    const entry = rosterEntry(parsed.env, parsed.text, now);
    if (!entry || entry.payload.group_id !== group) return null;
    if (!(await verifySignature(author, s, e))) return null;
    if (kind === 'join') {
      const invite = this.rosterEntries().find((r) => r.kind === 'invite' && r.author === leader && r.payload.invite_id === entry.payload.invite_id);
      const expires = invite ? realStamp(invite.payload.expires_at) : null;
      if (expires === null || now > expires) return null;
      const candidate = await computeRoster([...this.rosterEntries(), entry], group, leader);
      if (!candidate.members.has(author)) return null;
    }
    this.exec('INSERT OR IGNORE INTO log (author, seq, kind, env, sig) VALUES (?, ?, ?, ?, ?)', author, parsed.env.seq, kind, e, s);
    this.insertRoster(entry);
    this.rosterLoaded = false;
    return entry;
  }

  private async auth(ws: WebSocket, att: Attachment, frame: any): Promise<void> {
    const { group, author } = frame;
    if (group !== att.group || typeof author !== 'string' || !AUTHOR.test(author)) return ws.close(CLOSE_BAD_AUTH, 'bad auth');
    const sig = b64urlDecode(frame.sig, 64);
    const message = encoder.encode(`edmrn-crew-auth\n${group}\n${att.nonce}`);
    if (!sig || sig.length !== 64 || !(await verifySignature(author, sig, message))) return ws.close(CLOSE_BAD_AUTH, 'bad signature');
    let meta = this.meta();
    if (!meta) {
      if (!this.leaders().has(author) || !(await this.authEnvelope(frame.genesis, author, 'group', group, author))) {
        return ws.close(CLOSE_FORBIDDEN, 'no group');
      }
      this.exec('INSERT INTO meta (k, v) VALUES (?, ?), (?, ?)', 'group', group, 'leader', author);
      meta = { group, leader: author };
      this.rosterLoaded = false;
    }
    const before = new Set((await this.currentRoster())?.members.keys() ?? []);
    let joined = false;
    if (frame.join && meta.leader !== author && !before.has(author)) {
      joined = (await this.authEnvelope(frame.join, author, 'join', group, meta.leader)) !== null;
    }
    const roster = await this.currentRoster();
    const member = roster?.members.get(author);
    if (!member) return ws.close(CLOSE_FORBIDDEN, 'not_member');
    if (member.lastSeq !== null) return ws.close(CLOSE_FORBIDDEN, 'removed');
    for (const other of this.members()) {
      if (other !== ws && (other.deserializeAttachment() as Attachment).author === author) other.close(CLOSE_REPLACED, 'replaced');
    }
    ws.serializeAttachment({ ...att, state: 'member', author, statusAt: 0 } satisfies Attachment);
    if (joined) this.broadcast(ws, { t: 'have', heads: this.listedHeads(roster!) });
    ws.send(JSON.stringify({ t: 'have', heads: this.listedHeads(roster!) }));
    this.sendStatuses(ws, author);
  }

  /** Heads of every member, 0 for a member the relay holds nothing of (S7). */
  private listedHeads(roster: Roster): Record<string, number> {
    const heads = this.heads();
    const listed: Record<string, number> = {};
    for (const a of roster.members.keys()) listed[a] = heads.get(a) ?? 0;
    return listed;
  }

  /** The roster with `env` (a non-member's join) added, if that join admits its author (S7). */
  private async withJoin(env: Envelope, text: string, now: number, roster: Roster): Promise<Roster | null> {
    const entry = rosterEntry(env, text, now);
    if (!entry || entry.payload.group_id !== roster.group) return null;
    const candidate = await computeRoster([...this.rosterEntries(), entry], roster.group, roster.leader);
    return candidate.members.has(env.author) ? candidate : null;
  }

  // --- catch-up ----------------------------------------------------------------------------

  private async catchUp(ws: WebSocket, frame: any): Promise<void> {
    const theirs = frame.heads;
    if (typeof theirs !== 'object' || theirs === null || Array.isArray(theirs) || Object.keys(theirs).length > 1000) {
      return this.error(ws, 'bad_frame');
    }
    const roster = await this.currentRoster();
    const send = (f: unknown) => ws.send(JSON.stringify(f));
    for (const [author, relayHead] of this.heads()) {
      const cap = roster?.members.get(author)?.lastSeq;
      const until = cap === null || cap === undefined ? relayHead : Math.min(relayHead, cap);
      let after = Number.isInteger(theirs[author]) && theirs[author] >= 0 ? theirs[author] : 0;
      while (after < until) {
        const rows = this.exec<{ seq: number; env: ArrayBuffer; sig: ArrayBuffer }>(
          'SELECT seq, env, sig FROM log WHERE author = ? AND seq > ? AND seq <= ? ORDER BY seq LIMIT ?',
          author, after, until, MAX_EVS_ITEMS);
        if (!rows.length) break;
        this.sendItems(send, rows.map((r) => ({ e: b64(new Uint8Array(r.env)), s: b64(new Uint8Array(r.sig)) })));
        after = rows[rows.length - 1].seq;
      }
    }
    send({ t: 'synced' });
  }

  // --- receiving envelopes ----------------------------------------------------------------

  private cleanup(day: number): void {
    if (this.cleanedDay === day) return;
    this.cleanedDay = day;
    const past = this.exec<{ day: number; n: number }>('SELECT day, SUM(n) AS n FROM quota WHERE day < ? GROUP BY day', day);
    if (!past.length) return;
    const group = this.meta()?.group ?? '';
    for (const row of past) {
      const line = JSON.stringify({ event: 'crew-daily-total', group, day: new Date(row.day * DAY_MS).toISOString().slice(0, 10), envelopes: row.n });
      console.log(line);
      this.dailyTotals.push(line);
    }
    this.exec('DELETE FROM quota WHERE day < ?', day);
  }

  private async receive(ws: WebSocket, frame: any): Promise<void> {
    const items = frame.items;
    if (!Array.isArray(items) || items.length > MAX_EVS_ITEMS) return this.error(ws, 'bad_frame');
    let roster = await this.currentRoster();
    if (!roster) return;
    const membersBefore = new Set(roster.members.keys());
    const now = Date.now();
    const day = utcDay(now);
    this.cleanup(day);
    let groupLeft = GROUP_DAILY - (this.exec<{ n: number }>('SELECT COALESCE(SUM(n), 0) AS n FROM quota WHERE day = ?', day)[0]?.n ?? 0);
    const heads = this.heads();
    const advanced = new Map<string, number>();
    const counts = new Map<string, number>();
    const used = new Map<string, number>();
    const stopped = new Set<string>();
    const forward: Item[] = [];
    let rosterChanged = false;
    for (const item of items) {
      const e = b64urlDecode(item?.e, MAX_ENVELOPE_BYTES);
      const s = b64urlDecode(item?.s, 64);
      const parsed = e ? parseEnvelope(e) : null;
      if (!e || !s || s.length !== 64 || !parsed) {
        this.error(ws, 'bad_frame');
        continue;
      }
      const { env, text } = parsed;
      const author = env.author;
      if (stopped.has(author)) continue;
      let member = roster.members.get(author);
      if (!member && env.kind === 'join') {
        // Re-seed (S7): a non-member's join that admits its author is accepted; check the signature first.
        if (!(await verifySignature(author, s, e))) { this.error(ws, 'bad_sig', author, env.seq); stopped.add(author); continue; }
        const candidate = await this.withJoin(env, text, now, roster);
        if (candidate) {
          roster = candidate;
          member = candidate.members.get(author);
        }
      }
      if (!member) { this.error(ws, 'not_member', author, env.seq); stopped.add(author); continue; }
      if (!admits(roster, author, env.seq)) { this.error(ws, 'removed', author, env.seq); stopped.add(author); continue; }
      const head = advanced.get(author) ?? heads.get(author) ?? 0;
      if (env.seq <= head) continue;
      if (!(await verifySignature(author, s, e))) { this.error(ws, 'bad_sig', author, env.seq); stopped.add(author); continue; }
      const isRoster = ROSTER_KINDS.has(env.kind);
      if (!isRoster) {
        if (!used.has(author)) {
          used.set(author, this.exec<{ n: number }>('SELECT n FROM quota WHERE day = ? AND author = ?', day, author)[0]?.n ?? 0);
        }
        if (used.get(author)! + (counts.get(author) ?? 0) >= MEMBER_DAILY) { this.error(ws, 'quota', author, env.seq); stopped.add(author); continue; }
        if (groupLeft <= 0) { this.error(ws, 'group_quota', author, env.seq); stopped.add(author); continue; }
      }
      this.exec('INSERT OR IGNORE INTO log (author, seq, kind, env, sig) VALUES (?, ?, ?, ?, ?)', author, env.seq, env.kind, e, s);
      advanced.set(author, env.seq);
      if (isRoster) {
        const entry = rosterEntry(env, text, now);
        if (entry && this.insertRoster(entry)) rosterChanged = true;
      } else {
        counts.set(author, (counts.get(author) ?? 0) + 1);
        groupLeft -= 1;
      }
      forward.push({ e: item.e, s: item.s });
    }
    for (const [author, seq] of advanced) {
      this.exec('INSERT INTO heads (author, seq) VALUES (?, ?) ON CONFLICT (author) DO UPDATE SET seq = excluded.seq', author, seq);
    }
    for (const [author, n] of counts) {
      this.exec('INSERT INTO quota (day, author, n) VALUES (?, ?, ?) ON CONFLICT (day, author) DO UPDATE SET n = n + excluded.n', day, author, n);
    }
    if (forward.length) this.sendItems((f) => this.broadcast(ws, f), forward);
    if (rosterChanged) {
      this.rosterLoaded = false;
      const updated = await this.currentRoster();
      for (const other of this.members()) {
        const a = (other.deserializeAttachment() as Attachment).author!;
        const m = updated?.members.get(a);
        if (!m || m.lastSeq !== null) other.close(CLOSE_FORBIDDEN, 'removed');
      }
      if (updated && [...updated.members.keys()].some((a) => !membersBefore.has(a))) {
        this.broadcast(null, { t: 'have', heads: this.listedHeads(updated) });
      }
    }
  }

  // --- status (Task 14) ---------------------------------------------------------------------

  private async status(ws: WebSocket, att: Attachment, frame: any): Promise<void> {
    void ws; void att; void frame;
  }

  private sendStatuses(ws: WebSocket, author: string): void {
    void ws; void author;
  }

  private persistStatus(ws: WebSocket): void {
    void ws;
  }
}

function b64(bytes: Uint8Array): string {
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
```

   The relay's join path checks the invite's expiry against the relay clock as well as S2's
   check against the claimed `created_at`. Spec S7 already states both rules: this expiry check
   and the re-seed offer of joins.

   Use the helper from `src/b64.ts` (`b64urlEncode`) instead of the local `b64` if you prefer; the
   local copy only avoids an import cycle that does not exist. Pick one and delete the other.

- [ ] **Step 5: Run the tests and see them pass**

Run: `cd crew-relay && npx vitest run && npx tsc --noEmit`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add crew-relay/src/group.ts crew-relay/test/helpers.ts crew-relay/test/group.test.ts
git commit -m "feat(relay): group Durable Object - auth, heads, catch-up, quotas, removal (B1 S7 S13 S14 S18)"
```

---

### Task 14: Relay status, budget and daily totals

**Files:**
- Modify: `crew-relay/src/group.ts` (the three status methods)
- Test: `crew-relay/test/status.test.ts`, `crew-relay/test/budget.test.ts`

**Interfaces:**
- Consumes: Task 13.
- Produces: the relay's `st` handling (S8), measured row-write costs (S13), and `crew-daily-total`
  log lines (S18).

- [ ] **Step 1: Write the failing tests**

`crew-relay/test/status.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { b64urlEncode } from '../src/b64';
import { canonicalJson } from '../src/canonical';
import { authed, createGroupAndUpload, envelope, invite, keyFor, newGroup, stamp, type Key } from './helpers';

const TOKEN = '22'.repeat(32);

async function statusFrame(key: Key, hull: number, author = key.author) {
  const doc = { author, at: stamp(), status: { commander: 'X', system: 'Sol', address: '1', coords: null, ship: null, ship_name: null, ship_ident: null, edsy_url: null, hull, fuel_main: null, fuel_reserve: null, fuel_capacity_main: null, fuel_capacity_reserve: null, paused: false } };
  const bytes = new TextEncoder().encode(canonicalJson(doc));
  return { t: 'st', e: b64urlEncode(bytes), s: b64urlEncode(await key.sign(bytes)) };
}

async function twoMembers() {
  const group = newGroup();
  const leader = await keyFor('leader');
  const alice = await keyFor('alice');
  const { client: lc } = await createGroupAndUpload(group, leader);
  const inviteId = await invite(group, lc, leader, 2, TOKEN);
  await lc.drain();
  const join = await envelope(alice, 1, 'join', { group_id: group, invite_id: inviteId, token: TOKEN, commander: 'Alice' });
  const ac = await authed(group, alice, { join });
  await ac.nextOf('have');
  return { group, leader, alice, lc, ac };
}

describe('status', () => {
  it('is forwarded, rate-limited, and checked for author and signature', async () => {
    const { leader, alice, lc, ac } = await twoMembers();
    ac.send(await statusFrame(alice, 0.5));
    const got = await lc.nextOf('st');
    expect(JSON.parse(atob(got.e.replace(/-/g, '+').replace(/_/g, '/'))).status.hull).toBe(0.5);
    ac.send(await statusFrame(alice, 0.4));
    expect((await ac.nextOf('err')).code).toBe('rate');
    lc.send(await statusFrame(leader, 1, alice.author));
    expect((await lc.nextOf('err')).code).toBe('bad_frame');
  });

  it('the last status of a member who left reaches a new connection', async () => {
    const { group, leader, alice, lc, ac } = await twoMembers();
    ac.send(await statusFrame(alice, 0.3));
    await lc.nextOf('st');
    ac.ws.close(1000, 'bye');
    await ac.closed;
    await new Promise((r) => setTimeout(r, 100));
    const again = await authed(group, leader);
    const st = await again.nextOf('st');
    expect(JSON.parse(atob(st.e.replace(/-/g, '+').replace(/_/g, '/'))).author).toBe(alice.author);
  });
});
```

`crew-relay/test/budget.test.ts`:

```ts
import { runInDurableObject } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';
import { utcDay, type GroupRelay } from '../src/group';
import { createGroupAndUpload, envelope, keyFor, newGroup, relayFor } from './helpers';

async function writes(group: string): Promise<number> {
  let n = 0;
  await runInDurableObject(relayFor(group), async (instance: GroupRelay) => { n = instance.writes; });
  return n;
}

const visit = (key: any, seq: number) => envelope(key, seq, 'visit', { commander: 'A', system: 'Sol', address: String(seq), coords: null });

describe('row-write budget (spec B1 S13)', () => {
  it('one envelope in a frame costs log + heads + quota = 3 rows', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    const before = await writes(group);
    client.send({ t: 'evs', items: [await visit(leader, 2)] });
    await client.drain();
    expect((await writes(group)) - before).toBe(3);
  });

  it('a frame of 100 envelopes costs 102 rows', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    const items = [];
    for (let seq = 2; seq < 102; seq++) items.push(await visit(leader, seq));
    const before = await writes(group);
    client.send({ t: 'evs', items });
    await client.drain(400);
    expect((await writes(group)) - before).toBe(102);
  });

  it('a duplicate envelope costs nothing', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    const item = await visit(leader, 2);
    client.send({ t: 'evs', items: [item] });
    await client.drain();
    const before = await writes(group);
    client.send({ t: 'evs', items: [item] });
    await client.drain();
    expect((await writes(group)) - before).toBe(0);
  });

  it('a status costs no SQLite row until its socket closes, then one', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    const { b64urlEncode } = await import('../src/b64');
    const { canonicalJson } = await import('../src/canonical');
    const doc = { author: leader.author, at: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'), status: { commander: null, system: null, address: null, coords: null, ship: null, ship_name: null, ship_ident: null, edsy_url: null, hull: null, fuel_main: null, fuel_reserve: null, fuel_capacity_main: null, fuel_capacity_reserve: null, paused: true } };
    const bytes = new TextEncoder().encode(canonicalJson(doc));
    const before = await writes(group);
    client.send({ t: 'st', e: b64urlEncode(bytes), s: b64urlEncode(await leader.sign(bytes)) });
    await client.drain();
    expect((await writes(group)) - before).toBe(0);
    client.ws.close(1000, 'bye');
    await client.closed;
    await new Promise((r) => setTimeout(r, 100));
    expect((await writes(group)) - before).toBe(1);
  });

  it('past quota days are logged once as daily totals and deleted', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    const today = utcDay(Date.now());
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      instance.exec('INSERT INTO quota (day, author, n) VALUES (?, ?, ?), (?, ?, ?)', today - 1, leader.author, 7, today - 2, leader.author, 3);
      (instance as any).cleanedDay = -1;
    });
    client.send({ t: 'evs', items: [await visit(leader, 2)] });
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(instance.dailyTotals.length).toBe(2);
      expect(instance.exec('SELECT 1 FROM quota WHERE day < ?', today).length).toBe(0);
    });
  });
});
```

   If the local runtime counts rows differently from these expectations, do not adjust the
   numbers to match. The spec's budget (S13) rests on them. Report what `rowsWritten` showed,
   record it in `../knowledge/group-sync-transport.md` as **[probe]**, and ask the orchestrator.
   The duplicate test answers the open question in that file's §6 for the local runtime; record
   the answer the same way.

- [ ] **Step 2: Run them and see them fail**

Run: `cd crew-relay && npx vitest run test/status.test.ts test/budget.test.ts`
Expected: the status tests fail (no `st` handling); the budget status test fails at the close row.

- [ ] **Step 3: Implement the three status methods** in `src/group.ts`

```ts
  private async status(ws: WebSocket, att: Attachment, frame: any): Promise<void> {
    const now = Date.now();
    if (now - (att.statusAt ?? 0) < STATUS_INTERVAL_MS) return this.error(ws, 'rate');
    const data = b64urlDecode(frame.e, MAX_STATUS_BYTES);
    const sig = b64urlDecode(frame.s, 64);
    if (!data || !sig || sig.length !== 64) return this.error(ws, 'bad_frame');
    let doc: any;
    try {
      doc = JSON.parse(decoder.decode(data));
    } catch {
      return this.error(ws, 'bad_frame');
    }
    if (typeof doc !== 'object' || doc === null || doc.author !== att.author || typeof doc.at !== 'string' ||
        typeof doc.status !== 'object' || doc.status === null) {
      return this.error(ws, 'bad_frame');
    }
    if (!(await verifySignature(att.author!, sig, data))) return this.error(ws, 'bad_sig');
    const item = { e: frame.e as string, s: frame.s as string };
    ws.serializeAttachment({ ...att, statusAt: now, status: item } satisfies Attachment);
    this.broadcast(ws, { t: 'st', ...item });
  }

  private sendStatuses(ws: WebSocket, author: string): void {
    const live = new Set<string>();
    for (const other of this.members()) {
      const a = other.deserializeAttachment() as Attachment;
      if (other === ws || !a.author || a.author === author) continue;
      live.add(a.author);
      if (a.status) ws.send(JSON.stringify({ t: 'st', ...a.status }));
    }
    for (const row of this.exec<{ author: string; e: string; s: string }>('SELECT author, e, s FROM status')) {
      if (row.author !== author && !live.has(row.author)) ws.send(JSON.stringify({ t: 'st', e: row.e, s: row.s }));
    }
  }

  private persistStatus(ws: WebSocket): void {
    const att = ws.deserializeAttachment() as Attachment | null;
    if (att?.state !== 'member' || !att.status || !att.author) return;
    this.exec('INSERT INTO status (author, e, s) VALUES (?, ?, ?) ON CONFLICT (author) DO UPDATE SET e = excluded.e, s = excluded.s',
      att.author, att.status.e, att.status.s);
    ws.serializeAttachment({ ...att, status: undefined });
  }
```

   `persistStatus` clears the attachment's status after writing, so a close followed by an error
   event never writes twice.

- [ ] **Step 4: Run all relay tests and the type check**

Run: `cd crew-relay && npx vitest run && npx tsc --noEmit`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add crew-relay/src/group.ts crew-relay/test/status.test.ts crew-relay/test/budget.test.ts
git commit -m "feat(relay): status forwarding and persistence, measured row budget, daily totals (B1 S8 S13 S18)"
```

If you recorded a **[probe]** fact, commit it in the hub repository as well (`git -C ..`).

---

### Task 15: End-to-end test, documentation and handover

**Files:**
- Create: `tests/test_relay_e2e.py`, `docs/how-to/deploy-relay.md`
- Modify:
  - `tests/conftest.py` (lift the socket block for `relay_e2e` only) and `pytest.ini` (register
    the marker);
  - `docs/architecture/overview.md`, `docs/architecture/patterns.md`, `docs/how-to/run.md`;
  - `docs/README.md` (map entry for `deploy-relay.md`), `CLAUDE.md` (Stack),
    `docs/handover/HANDOVER.md`.
- Modify (hub): `../knowledge/group-sync-transport.md`, only if earlier tasks left facts to
  record.

**Interfaces:**
- Consumes: everything above.
- Produces: an opt-in end-to-end proof, and documentation that describes the system as built.

- [ ] **Step 1: Let the opt-in test reach localhost**

In `tests/conftest.py`, the `no_network` fixture gains `request` and returns early for the marker:

```python
@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    if request.node.get_closest_marker("relay_e2e"):
        return  # the opt-in end-to-end test talks to wrangler dev on 127.0.0.1 (spec B1 Testing)
    import socket
    ...
```

In `pytest.ini`, add under `[pytest]` (or extend an existing `markers` list):

```ini
markers =
    relay_e2e: end-to-end test against wrangler dev on localhost; opt-in with EDMRN_RELAY_E2E=1
```

- [ ] **Step 2: Write the end-to-end test** (`tests/test_relay_e2e.py`)

```python
"""End to end: two EDMRN crew clients through `wrangler dev` on localhost (spec B1, Testing).

Opt-in, never in the default run:
    cd crew-relay && npm ci && cd ..
    EDMRN_RELAY_E2E=1 .venv/Scripts/python.exe -m pytest tests/test_relay_e2e.py -q
Needs Node; no Cloudflare account. Everything else is real: signing, the relay, the protocol.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from crew_helpers import visit
from edmrn.ext.crew import Writer
from edmrn.ext.crew_group import CrewGroup
from edmrn.ext.crew_identity import KeyRing
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.store import Store

pytestmark = [pytest.mark.relay_e2e,
              pytest.mark.skipif(os.environ.get("EDMRN_RELAY_E2E") != "1", reason="opt-in: EDMRN_RELAY_E2E=1")]

ROOT = Path(__file__).resolve().parents[1]
PORT = 8799
RELAY = f"ws://127.0.0.1:{PORT}"


class Root:
    def after(self, ms, fn, *args):
        if ms == 0:
            fn(*args)


class Ship:
    def __init__(self):
        self.state = SimpleNamespace(commander=None)

    def subscribe(self, fn):
        pass


def wait_for(predicate, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError("condition not met in time")


def world(tmp, name, commander, status=None):
    store = Store(tmp / f"{name}.db")
    crew = CrewStore(store)
    ring = KeyRing(tmp / f"{name}_keys")
    service = SimpleNamespace(store=crew, keyring=ring, writer=Writer(crew, ring, Root()), ship=Ship(),
                              commander=commander, status=status or (lambda: (None, None)), group=None)
    service.identity = lambda: ring.for_commander(service.commander)
    group = CrewGroup(service, SimpleNamespace(root=Root()))
    group.start()
    return SimpleNamespace(store=store, crew=crew, ring=ring, service=service, group=group)


@pytest.fixture
def relay(tmp_path):
    leader_ring = KeyRing(tmp_path / "leader_keys")
    leader_author = leader_ring.for_commander("Leader").author
    npx = "npx.cmd" if sys.platform == "win32" else "npx"
    proc = subprocess.Popen(
        [npx, "wrangler", "dev", "--ip", "127.0.0.1", "--port", str(PORT),
         "--var", f"CREW_LEADERS:{leader_author}", "--persist-to", str(tmp_path / "wrangler-state")],
        cwd=ROOT / "crew-relay", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def up():
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
            return True
        except OSError:
            return False
    try:
        wait_for(up, timeout=90)
        yield
    finally:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.terminate()
        proc.wait(10)


def test_two_clients_exchange_a_visit_and_a_status(relay, tmp_path):
    ship = SimpleNamespace(commander="Friend", ship="anaconda", ship_name=None, ship_ident=None,
                           edsy_url=None, hull=0.75, fuel_main=10.0, fuel_reserve=0.5,
                           fuel_capacity_main=32.0, fuel_capacity_reserve=0.63)
    leader = world(tmp_path, "leader", "Leader")
    friend = world(tmp_path, "friend", "Friend", status=lambda: (None, ship))
    try:
        leader.group.create_group("E2E", RELAY)
        wait_for(lambda: leader.group.state == "live")
        invite = leader.group.create_invite("Friend", 1)
        friend.group.join(invite.link.replace(f"http://127.0.0.1:{PORT}", f"http://127.0.0.1:{PORT}"))
        wait_for(lambda: friend.group.state == "live")
        friend.service.writer.enqueue(visit(cmdr="Friend", at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                            address="42", system="E2E System"))
        friend.service.writer.drain()

        def leader_has_it():
            leader.group.inbox.drain()
            row = leader.crew.system("42")
            return row is not None and row.name == "E2E System"
        wait_for(leader_has_it)
        friend_author = friend.ring.for_commander("Friend").author
        wait_for(lambda: friend_author in leader.group.board.snapshot())
        assert leader.group.board.snapshot()[friend_author].status["hull"] == 0.75
        assert friend_author in leader.group.roster.members
    finally:
        leader.group.stop()
        friend.group.stop()
        leader.store.close()
        friend.store.close()
```

   The leader's key must exist before `wrangler dev` starts, because its author goes into
   `CREW_LEADERS`. The fixture creates it in `leader_keys`, and the `world(..., "leader", ...)`
   helper uses the same directory name. Check that both point at
   `tmp_path / "leader_keys"`.

- [ ] **Step 3: Run it**

Run: `cd crew-relay && npm ci && cd .. && EDMRN_RELAY_E2E=1 .venv/Scripts/python.exe -m pytest tests/test_relay_e2e.py -q`
(PowerShell: `$env:EDMRN_RELAY_E2E='1'; .venv/Scripts/python.exe -m pytest tests/test_relay_e2e.py -q`)
Expected: 1 passed. Without the variable, `.venv/Scripts/python.exe -m pytest -q` reports it as
skipped, and the rest of the suite passes.

If `wrangler dev` refuses `--var` or `--persist-to`, check `npx wrangler dev --help` for the
installed version and adapt the command. Record the working form in `docs/how-to/run.md`.

- [ ] **Step 4: Documentation** (rules: `docs/README.md`; timeless wording; one home per fact)

1. `docs/architecture/overview.md`:
   - Add the row `crew-relay/` → "EDMRN (TypeScript relay, AGPL-3.0)" to the fork layout table.
   - Add the hook-point row: `edmrn/settings_manager.py` (crew) | Two lines after
     `create_fuel_settings_card(fuel_frame)` add the crew settings card
     (`edmrn.ext.crew_dialog.add_settings_card`) | Opens the crew dialog (crew sync core spec
     S20) | no.
   - Add a section "Crew sync data flow (crew sync core spec, ADR-0005, ADR-0008)" with these
     numbered steps:
     1. `CrewGroup.start` (from `crew.install`) reads `crew_keys/group.json`, sets the projection
        gate (own authors, group, leader), starts the Tk-loop `Inbox` and, with a group, the
        `edmrn-crew-sync` thread.
     2. The thread connects to the relay, signs the relay's nonce, and sends its heads. It
        uploads per author in ascending `seq` what the relay lacks, read in pages of 100 through
        `Store.read()`. It never writes `edmrn.db`.
     3. Received envelopes go to the `Inbox`; the Tk loop stores them with
        `CrewStore.insert_received_batch`, one transaction per batch, and the projector applies
        the membership gate.
     4. Statuses travel signed and in memory only: `CrewStatus` and `LocationTracker` observers →
        the sender (on change, at most every 30 s) → the relay's socket attachment → friends'
        `CrewBoard`.
     5. The relay (`crew-relay/`, one Durable Object per group) stores signed envelopes of
        members, keeps per-author heads and daily quotas, and serves only up to each author's
        head.

     Then the invariants:
     - Membership is computed from roster envelopes, identically by client and relay (shared
       vectors in `crew-relay/test/vectors.json`, generated by
       `tests/vectors/make_crew_vectors.py`).
     - The leader's author comes only from the invite or `group.json`.
     - A `genesis` or `join` presented with `auth` never moves the relay's head.
     - The webhook URL never leaves the leader's `group.json` except in the request to Discord.
2. `docs/architecture/patterns.md`:
   - In "All external HTTP goes through `edmrn/ext/api.py`", add: "The crew relay WebSocket
     (`edmrn.ext.crew_sync.websockets_connect`) is the one other network path. A secret URL is
     logged through `call(..., log_url=...)` (crew sync spec S5a)."
   - Add a pattern "Background threads never write `edmrn.db`": the rule; the defect (a
     database written from two threads on one connection under a Tk-thread writer would make
     the writer's locked-retry and the store lock fight; Spec A's writer and B1's inbox both
     drain on the Tk loop); and how to apply it (queue, then drain on `root.after`).
3. `docs/how-to/run.md`: a section "Crew relay". It covers:
   - `cd crew-relay && npm ci`, `npm test`, `npm run typecheck`;
   - regenerating vectors: `.venv/Scripts/python.exe tests/vectors/make_crew_vectors.py`, and when
     to do it: only when the spec changes a shared rule;
   - the opt-in end-to-end command from Step 3;
   - `npx wrangler dev` for manual tries.
4. `docs/how-to/deploy-relay.md` (new), with this content:

```markdown
# Deploying the crew relay

The relay is the Worker in `crew-relay/` (crew sync core spec S17). It runs on the commander's
Cloudflare account at `galroute.the-hub.cloud`. On the Free plan nothing is charged; over a daily
limit operations fail until 00:00 UTC (`../../../knowledge/group-sync-transport.md` §1.1).

## Before the first deploy

- The zone `the-hub.cloud` is listed as Active in the Cloudflare dashboard (Websites).
- Node 20 or newer: `node --version`.
- Dependencies and tests: `cd crew-relay`, `npm ci`, `npm test`.

## First deploy

1. Sign in: `npx wrangler login`. A browser tab opens on Cloudflare; choose Allow. Check with
   `npx wrangler whoami`, which names the account.
2. Leader key: in EDMRN open Settings > Crew... and copy "Your crew key". In
   `crew-relay/wrangler.toml` set `CREW_LEADERS = "<key>"` (several keys: comma-separated). The
   key is public; commit the change.
3. Source URL: set `SOURCE_URL` to the GitHub fork's URL once it exists; leave it empty until then.
4. Deploy: `npx wrangler deploy`. Wrangler creates the Worker, the Durable Object class (migration
   `v1`), and the DNS record and certificate for `galroute.the-hub.cloud`.
5. Check: open `https://galroute.the-hub.cloud/`; it answers with a one-line greeting. A first
   certificate can take a few minutes.
6. In EDMRN: Settings > Crew... > Create group. The relay address is prefilled. The connection
   shows "Connected".

## Operating

- Live logs: `npx wrangler tail`. Each past day appears once as a `crew-daily-total` line.
- Usage: dashboard > Workers & Pages > `edmrn-crew-relay` > Metrics, and the Durable Objects
  usage (rows written) in the account's billing usage view.
- Update: change the code, `npm test`, `npx wrangler deploy`. Groups keep their data.
- Another leader: add the key to `CREW_LEADERS` and deploy.
- Every member holds a full replica, so an emptied relay is re-seeded by the leader's next
  connection (spec S7).
```

5. `docs/README.md`: add `how-to/deploy-relay.md` to the map.
6. `CLAUDE.md`, Stack: add one sentence. "The crew relay is TypeScript in `crew-relay/`
   (Cloudflare Workers, Durable Objects, Wrangler, Vitest); the client uses `websockets`."
7. `docs/handover/HANDOVER.md`: run the milestone sweep (global rule 7) and rewrite "Where things
   stand" and "Start here" with:
   - test counts from real runs (pytest and vitest);
   - what is not verified on real Cloudflare yet;
   - the deploy session with the commander as the next step;
   - B1 follow-ups deferred from reviews;
   - B2 and C next.

- [ ] **Step 5: Full verification**

Run, and paste the summaries into the task report:
- `.venv/Scripts/python.exe -m pytest -q`
- `cd crew-relay && npx vitest run && npx tsc --noEmit`
- `git status --short`: only intended files are changed.

- [ ] **Step 6: Commit**

```bash
git add tests/test_relay_e2e.py tests/conftest.py pytest.ini docs/how-to/deploy-relay.md docs/how-to/run.md docs/architecture/overview.md docs/architecture/patterns.md docs/README.md CLAUDE.md docs/handover/HANDOVER.md
git commit -m "docs(crew): sync data flow, relay how-tos, e2e test, handover after B1"
```

---

## Self-review notes (for the executor)

- Spec coverage:
  - S1 → Tasks 1 and 12;
  - S2 → Tasks 2, 5 and 12;
  - S3 → Task 3;
  - S4, S5, S5a → Tasks 2, 8 and 9;
  - S6 → Task 4;
  - S7 → Tasks 6 and 13;
  - S8 → Tasks 1, 7 and 14;
  - S9 → Task 6;
  - S10–S12 → Tasks 1 and 3;
  - S13–S16 → Tasks 13 and 14; S16's estimates are checked in use, by the daily totals;
  - S17, S18 → Tasks 11, 13 and 14;
  - S19 → Tasks 6 and 9;
  - S20 → Task 10;
  - S21 → every task; the opt-in end-to-end test is the only one with network, on localhost.
- The relay's expiry check at `auth` and the re-seed offer of joins are in spec S7.
- Rulings made while planning (recorded in the spec, 2026-10-13):
  - roster names in ASCII;
  - `group` carries `commander`;
  - pause persisted in `group.json`;
  - `ws://` only for localhost;
  - the 60 s wait after 4000;
  - quota rows of past days logged and deleted;
  - membership in memory on the relay;
  - canonical roster bytes.
