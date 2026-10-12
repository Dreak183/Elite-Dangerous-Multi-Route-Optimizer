# Crew Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** EDMRN records what the commander explores and finds as signed, deduplicated events in
`edmrn.db`, derives the explored-systems, bodies, signals, finds and notes tables from them, and
keeps the commander's live ship, hull and fuel status.

**Architecture:**
- Phase 1 (Tasks 1–5) builds the log:
  - pure envelope and validator code (`crew_events.py`);
  - per-commander Ed25519 key files (`crew_identity.py`);
  - the signed log, its deterministic projections and their queries (`crew_store.py`), on
    `store.py`'s single connection (ADR-0006).
- Phase 2 (Tasks 6–12) feeds it:
  - a stateful journal mapper with a startup replay (`crew_capture.py`);
  - the ship status (`crew_status.py`);
  - a `Status.json` listener hook in upstream's fuel tracker;
  - a Tk-loop writer, and `install(app)` behind a two-line hook in `app.py` (`crew.py`).

Listeners and the replay never write. They queue unsigned drafts; only the writer signs and inserts,
on the Tk thread.

**Tech Stack:** Python 3.13, sqlite3 (upsert, window functions), `cryptography` (Ed25519), pytest.

**Spec:** `docs/superpowers/specs/2026-10-12-crew-capture-design.md` (Accepted). Read it with this
plan; the spec wins where they disagree. Decisions: ADR-0001, ADR-0002, ADR-0004, ADR-0005,
ADR-0006 (`docs/architecture/decisions/`). Journal facts: `../knowledge/exploration-events.md` in
the hub (`X:\Projects\EDProjects\knowledge\exploration-events.md`).

## Global Constraints

- **Upstream diffs are minimal** (ADR-0002). Only `edmrn/app.py` (Task 11, two lines) and
  `edmrn/fuel_tracker.py` (Task 9) change among upstream files. Change only the lines the task
  names. No reformatting.
- **CRLF.** Every file in the working tree is CRLF (git `autocrlf`), ours included. New files are
  normalised by git on commit. For the upstream files check `file edmrn/app.py edmrn/fuel_tracker.py`
  ("with CRLF line terminators") and `git diff --stat` (only the intended line counts).
- **Own code** lives in `edmrn/ext/`, `tests/`, `requirements-edmrn.txt`.
- **Journal and `Status.json` data enter only through upstream's layer** (ADR-0001):
  `JournalMonitor.event_listeners` and the new `FuelTracker.status_listeners`. The only file reads of
  our own are the startup replay of journal files (C10) and key files.
- **One connection** (ADR-0006). `crew_store.py` never opens a connection. It writes only inside
  `Store.transaction()` and reads only inside `Store.read()`. SQL takes values as parameters only.
  Table names in f-strings come only from the constant `PROJECTION_TABLES`.
- **Tests never touch real folders** (C13). `tests/conftest.py` redirects the home folders.
  Journals, key files and databases live in `tmp_path`. There is no network. Run tests only as
  `.venv/Scripts/python.exe -m pytest` from the repository root.
- **New dependency:** `cryptography>=42` (Apache-2.0 or BSD-3-Clause, AGPL-compatible), added to
  `requirements-edmrn.txt` only. Install it with
  `uv pip install --python .venv/Scripts/python.exe -r requirements-edmrn.txt`.
- **Exact values from the spec:**
  - envelope `v` = 1;
  - maximum envelope size 16 KB = 16384 bytes;
  - names at most 128 characters, notes and details at most 2,000;
  - forbidden characters: C0/C1 controls, DEL, lone surrogates, and U+202A–U+202E and
    U+2066–U+2069;
  - integers above 2^53 travel as strings;
  - `created_at` is `YYYY-MM-DDTHH:MM:SSZ`;
  - replay of 5 journal files;
  - writer: ≤ 500 drafts per tick, about 1 s per tick, queue cap 10,000;
  - Status.json in-ship flag bit 24.
- **Staging:**
  - Stage explicit paths. Never use `git add -A`, `git add .` or `git add -u`.
  - End every commit message with the two lines
    `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
    `Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa`.
- **Documentation is part of the change**, in the same commit (`docs/README.md`). Each task names
  the docs it updates. No status wording outside `docs/handover/HANDOVER.md`.

## Rulings made while planning (spec gaps, recorded here so reviewers do not re-open them)

1. **Payloads carry `address`** in every kind, and `system` (nullable) in `body`, `signals` and
   finds. The spec's C5 payload column omits them for `body` and `signals`. The projector needs the
   system address, and the natural key is computed from the payload, so the key and the projection
   cannot disagree.
2. **All payload keys are always present**; absent values are `null`. A fixed shape makes "unknown
   keys are rejected" exact.
3. **`systems.body_count`** takes the latest of honk `body_count` and `all_bodies` `count`. 45 of
   298 completed visits had no honk (`exploration-events.md` §1), and those systems would otherwise
   show no body count.
4. **System name and coordinates** come from the earliest (`created_at`, `author`, `event_id`)
   report carrying them, from journal kinds only. Notes and finds never name a system row.
5. **Status replay seeding** compares journal times, not the last change. `ShipStatus.journal_at`
   is the time of the last journal-derived change. `adopt` takes the replay's journal-derived fields
   when they are newer, and keeps the live `Status.json` fuel readings. Otherwise the fuel tracker's
   once-a-second ticks would always be newer than the replay, and the startup `Loadout` would be
   lost, which is exactly what C10 exists to prevent.
6. **One `CrewStatus` for the active commander.** A change of commander resets ship, hull and fuel,
   as the location tracker does.
7. **Live events before the replay finishes are buffered** (up to 5,000). The monitor reads the
   newest journal from its end and never sees that session's `LoadGame`. Without a seed, C11 would
   drop every live event until the game restarts. The replay hands its final mapper state to the live
   mapper, and then the buffer is mapped. Deterministic ids remove the overlap.
8. **Unknown `ScanType`** (the documented `NavBeacon`, never observed) is skipped, not guessed into
   a tier.
9. **Note and manual-find text obey C12 strictly**, so a newline is rejected. Spec C decides whether
   multi-line notes need a `v` 2 rule.
10. **The manual API looks up the current revision**: `edit_find`, `delete_find` and `delete_note`
    take only the id, flush the writer, and read the own author's current revision from the store.

## Review Focus

1. **Live monitor starts at the end of the newest journal.** The live mapper never sees that
   session's `LoadGame`, so capture would stay silent until a game restart. Expected: live events
   are captured from app start. Pinned in Task 10 (`test_live_events_before_seed_are_buffered_and_attributed`).
2. **The fuel tracker ticks before the replay finishes.** The startup `Loadout` must still reach the
   status. Pinned in Task 8 (`test_adopt_keeps_live_fuel_and_takes_newer_journal_fields`).
3. **Hostile received bytes:**
   - deep nesting, duplicate keys, `1e400`;
   - an author string in non-canonical base64 that decodes to the same key;
   - a signature of the wrong length.

   Expected: rejected with `EnvelopeError`, with no crash and no row. Pinned in Tasks 1, 3 and 4.
4. **Two commanders on one PC** alternate in one journal sequence. Expected: two key files, two
   authors, each event attributed to the commander active when it was written, and status reset on
   switch. Pinned in Tasks 3, 6 and 8.
5. **The app closes while the writer is scheduled.** `root.after` raises "main thread is not in main
   loop". Expected: the writer stops quietly and no exception reaches Tk or a listener thread. Pinned
   in Task 11 (`test_writer_stops_when_root_is_gone`).

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `requirements-edmrn.txt` | add `cryptography>=42` | 1 |
| `edmrn/ext/crew_events.py` | canonical bytes, ids, UUIDv7, envelope build/seal/parse (1); validators, `natural_key`, `Draft` (2) | 1, 2 |
| `edmrn/ext/crew_identity.py` | key files, `KeyRing`, `Identity`, `verify`, export/import | 3 |
| `edmrn/ext/store.py` | `transaction()`, `read()`, crew migration | 4 |
| `edmrn/ext/crew_store.py` | `CrewStore`: own-event writer, `insert_received` (4); projectors, `rebuild_projections`, queries (5) | 4, 5 |
| `edmrn/ext/crew_capture.py` | `JournalMapper` (6, 7), manual draft builders (7), `LiveCapture`, `replay` (10) | 6, 7, 10 |
| `edmrn/ext/crew_status.py` | `ShipStatus`, `CrewStatus`, `edsy_url` | 8 |
| `edmrn/fuel_tracker.py` | `status_listeners` hook (upstream) | 9 |
| `edmrn/ext/crew.py` | `Writer`, `CrewService`, `install` | 11 |
| `edmrn/ext/location.py` | `journal_dir_for(app)` shared by both installs | 11 |
| `edmrn/app.py` | two-line install hook (upstream) | 11 |
| `tests/crew_helpers.py` | draft builders, sealing, projection dump | 4 |
| `tests/test_crew_events.py` | Tasks 1, 2 | 1, 2 |
| `tests/test_crew_identity.py` | Task 3 | 3 |
| `tests/test_crew_store.py` | Tasks 4, 5 | 4, 5 |
| `tests/test_crew_capture.py` | Tasks 6, 7, 10 | 6, 7, 10 |
| `tests/test_crew_status.py` | Task 8 | 8 |
| `tests/test_fuel_tracker_hook.py` | Task 9 | 9 |
| `tests/test_crew_install.py` | Tasks 11, 12 | 11, 12 |

Docs touched: `docs/architecture/overview.md` (Tasks 9, 11), `docs/knowledge/upstream-baseline.md`
(Task 9), `docs/how-to/run.md` (Task 12).

---

# Phase 1 — the log

### Task 1: Dependency and envelope core

**Files:**
- Modify: `requirements-edmrn.txt`
- Create: `edmrn/ext/crew_events.py`
- Test: `tests/test_crew_events.py`

**Interfaces:**
- Produces (later tasks rely on these exact names):
  - `ENVELOPE_VERSION = 1`, `MAX_ENVELOPE_BYTES = 16384`, `MAX_SAFE_INT = 2**53 - 1`
  - `class EnvelopeError(ValueError)`, `class PayloadError(ValueError)`
  - `canonical_bytes(value) -> bytes`
  - `deterministic_id(author: str, kind: str, *key) -> str` (32 lowercase hex)
  - `class Uuid7Generator` with `next(now_ms: int | None = None) -> str`; module function `uuid7() -> str`
  - `utc_now_stamp() -> str`, `stamp(value) -> str | None`
  - `is_uuid7(value) -> bool`
  - `build_envelope(event_id, author, seq, kind, created_at, payload) -> dict`
  - `check_structure(env) -> dict` (raises `EnvelopeError`)
  - `seal(envelope: dict, sign: Callable[[bytes], bytes]) -> tuple[bytes, bytes]`
  - `parse_envelope(data: bytes) -> dict` (raises `EnvelopeError`)

- [ ] **Step 1: Add the dependency and install it**

Append to `requirements-edmrn.txt`:

```
# Ed25519 signatures for the crew event log (ADR-0005); Apache-2.0 OR BSD-3-Clause.
cryptography>=42
```

Run: `uv pip install --python .venv/Scripts/python.exe -r requirements-edmrn.txt`
Then: `.venv/Scripts/python.exe -c "import cryptography; print(cryptography.__version__)"`
Expected: a version ≥ 42.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_crew_events.py`:

```python
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
    for bad in ("2026-10-12 10:00:00", "2026-10-12T10:00:00.123Z", "2026-10-12T10:00:00Z\n", 5, None):
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_events.py -q`
Expected: collection error, `ImportError: cannot import name 'crew_events'`.

- [ ] **Step 4: Write the implementation**

Create `edmrn/ext/crew_events.py`:

```python
"""Crew event envelopes: canonical bytes, ids and parsing.

Spec: docs/superpowers/specs/2026-10-12-crew-capture-design.md (C2, C3). Pure: no I/O, no Tk.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

ENVELOPE_VERSION = 1
MAX_ENVELOPE_BYTES = 16 * 1024
MAX_SAFE_INT = 2**53 - 1
ENVELOPE_KEYS = frozenset({'v', 'event_id', 'author', 'seq', 'kind', 'created_at', 'payload'})

_STAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')
_HEX_ID = re.compile(r'[0-9a-f]{32}')
_UUID7 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}')
_AUTHOR = re.compile(r'[A-Za-z0-9_-]{43}')
_KIND = re.compile(r'[a-z_]{1,32}')


class EnvelopeError(ValueError):
    """Bytes or a dict that are not a well-formed envelope."""


class PayloadError(ValueError):
    """A payload that breaks its kind's schema (C12)."""


def canonical_bytes(value: Any) -> bytes:
    """UTF-8 JSON with sorted keys, no spaces and no ASCII escaping (C2)."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def deterministic_id(author: str, kind: str, *key: Any) -> str:
    """Id of a journal-derived event: SHA-256 over [author, kind, key...], 32 hex (C3)."""
    return hashlib.sha256(canonical_bytes([author, kind, *key])).hexdigest()[:32]


class Uuid7Generator:
    """UUIDv7 strings that sort in creation order within one process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_ms = -1
        self._counter = 0

    def next(self, now_ms: Optional[int] = None) -> str:
        with self._lock:
            ms = int(time.time() * 1000) if now_ms is None else now_ms
            if ms <= self._last_ms:
                ms, counter = self._last_ms, self._counter + 1
                if counter > 0xFFF:
                    ms, counter = self._last_ms + 1, 0
            else:
                counter = 0
            self._last_ms, self._counter = ms, counter
        rand_b = int.from_bytes(os.urandom(8), 'big') & ((1 << 62) - 1)
        value = ((ms & ((1 << 48) - 1)) << 80) | (0x7 << 76) | (counter << 64) | (0b10 << 62) | rand_b
        h = f'{value:032x}'
        return f'{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}'


_uuid7 = Uuid7Generator()


def uuid7() -> str:
    return _uuid7.next()


def is_uuid7(value: Any) -> bool:
    return isinstance(value, str) and _UUID7.fullmatch(value) is not None


def utc_now_stamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def stamp(value: Any) -> Optional[str]:
    """The value if it has the envelope's exact timestamp shape, else None."""
    return value if isinstance(value, str) and _STAMP.fullmatch(value) else None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def build_envelope(event_id: str, author: str, seq: int, kind: str, created_at: str,
                   payload: dict) -> dict:
    return {'v': ENVELOPE_VERSION, 'event_id': event_id, 'author': author, 'seq': seq,
            'kind': kind, 'created_at': created_at, 'payload': payload}


def check_structure(env: Any) -> dict:
    """The envelope itself if its keys and field types are right; the payload is not checked."""
    if not isinstance(env, dict) or set(env) != ENVELOPE_KEYS:
        raise EnvelopeError('envelope keys must be exactly ' + ', '.join(sorted(ENVELOPE_KEYS)))
    if not _is_int(env['v']) or not 1 <= env['v'] <= 1000:
        raise EnvelopeError('v must be an integer from 1 to 1000')
    event_id = env['event_id']
    if not isinstance(event_id, str) or not (_HEX_ID.fullmatch(event_id) or _UUID7.fullmatch(event_id)):
        raise EnvelopeError('event_id must be 32 hex characters or a UUIDv7')
    if not isinstance(env['author'], str) or not _AUTHOR.fullmatch(env['author']):
        raise EnvelopeError('author must be 43 base64url characters')
    if not _is_int(env['seq']) or not 1 <= env['seq'] <= MAX_SAFE_INT:
        raise EnvelopeError('seq must be an integer from 1 to 2^53-1')
    if not isinstance(env['kind'], str) or not _KIND.fullmatch(env['kind']):
        raise EnvelopeError('kind must be 1-32 characters a-z or _')
    if stamp(env['created_at']) is None:
        raise EnvelopeError('created_at must be YYYY-MM-DDTHH:MM:SSZ')
    if not isinstance(env['payload'], dict):
        raise EnvelopeError('payload must be an object')
    return env


def seal(envelope: dict, sign: Callable[[bytes], bytes]) -> tuple[bytes, bytes]:
    """Canonical bytes of an own envelope and their signature."""
    check_structure(envelope)
    try:
        data = canonical_bytes(envelope)
    except (ValueError, TypeError) as e:
        raise EnvelopeError(f'envelope cannot be encoded: {e}') from e
    if len(data) > MAX_ENVELOPE_BYTES:
        raise EnvelopeError('envelope larger than 16 KB')
    return data, sign(data)


def _no_duplicate_keys(pairs: list) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise EnvelopeError(f'duplicate key {key!r}')
        obj[key] = value
    return obj


def _reject_constant(name: str) -> Any:
    raise EnvelopeError(f'non-finite number {name}')


def parse_envelope(data: Any) -> dict:
    """Envelope dict from received bytes; the bytes themselves are what the signature covers."""
    if not isinstance(data, (bytes, bytearray)):
        raise EnvelopeError('envelope must be bytes')
    if len(data) > MAX_ENVELOPE_BYTES:
        raise EnvelopeError('envelope larger than 16 KB')
    try:
        env = json.loads(bytes(data).decode('utf-8'), object_pairs_hook=_no_duplicate_keys,
                         parse_constant=_reject_constant)
    except EnvelopeError:
        raise
    except (ValueError, RecursionError) as e:
        raise EnvelopeError(f'envelope is not JSON: {e}') from e
    return check_structure(env)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_events.py -q`
Expected: all pass.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass (the 5 Tk tests in `test_neutron_resume_gui.py` may skip with "can't find usable
init.tcl"; that is the environment).

- [ ] **Step 7: Commit**

```bash
git add requirements-edmrn.txt edmrn/ext/crew_events.py tests/test_crew_events.py
git commit -m "feat(crew): envelope core - canonical bytes, ids, UUIDv7, parse and seal

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 2: Payload validators, natural keys and drafts

**Files:**
- Modify: `edmrn/ext/crew_events.py` (append)
- Test: `tests/test_crew_events.py` (append)

**Interfaces:**
- Consumes: Task 1's `PayloadError`, `deterministic_id`, `is_uuid7`, `MAX_SAFE_INT`.
- Produces:
  - constants `SIGNAL_TYPES`, `SCAN_TYPES`, `AUTO_FINDS`, `MANUAL_FINDS`
  - `is_known(kind: str, v: int) -> bool`
  - `validate_payload(kind: str, v: int, payload) -> None` (raises `PayloadError`)
  - `natural_key(kind: str, created_at: str, payload: dict) -> tuple | None`
  - `@dataclass(frozen=True) class Draft(kind: str, commander: str, created_at: str, payload: dict, event_id: str | None = None)` with `resolve_id(author: str) -> str`

Payload shapes (v 1). Every key is required, `null` only where marked "or null":

| Kind | Keys |
|---|---|
| `visit` | `commander` name, `system` name, `address`, `coords` [x, y, z] or null |
| `honk` | `system` name, `address`, `body_count` int 0–10000, `non_body_count` int 0–100000 |
| `all_bodies` | `system` name, `address`, `count` int 0–10000 |
| `body` | `system` name or null, `address`, `body_id` int 0–65535, `name` name, `tier` `full`/`basic`, `star_type` name or null, `planet_class` name or null, `moon` bool, `landable` bool or null, `terraform` name (empty allowed) or null, `was_discovered` bool, `was_mapped` bool, `scan_type` one of `SCAN_TYPES` |
| `signals` | `system` name or null, `address`, `body_id`, `body_name` name, `source` `fss`/`saa`, `counts` {signal type: int 0–1000}, `genera` [name, ≤ 64] |
| `find` auto | `mode` `auto`, `find_kind` one of `AUTO_FINDS`, `system` name or null, `address`, `body_name` name or null, `body_id` or null, `detail` (per find kind below) |
| `find` manual | `mode` `manual`, `find_id` UUIDv7, `find_kind` one of `MANUAL_FINDS`, `system` name or null, `address`, `body_name` name or null, `body_id` or null, `note` text (empty allowed), `deleted` bool |
| `note` | `note_id` UUIDv7, `system` name or null, `address`, `text` text (empty allowed), `deleted` bool |

Auto find `detail`: biological `{count}` (1–1000); stellar_phenomenon `{signal}`; codex
`{entry_id, name, category, subcategory, new_entry}`; valuable_body `{planet_class, terraform}`.
Cross-field rules:
- `body`: exactly one of `star_type` and `planet_class` is set, and `tier` is `basic` exactly when
  `scan_type` is `Basic`.
- `signals`: an `fss` event has no genera.
- Auto finds of kind biological, codex and valuable_body have a `body_id`.

`address` is a decimal string of 1–20 digits with a value below 2^64. A name is a non-empty string
of at most 128 characters. Text is at most 2,000 characters. Both forbid the C12 characters.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crew_events.py`:

```python
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
    ("visit", "address", 10477373803),
    ("visit", "address", "-1"),
    ("visit", "address", "1" * 21),
    ("visit", "address", str(2**64)),
    ("visit", "address", "12a"),
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_events.py -q`
Expected: the new tests fail with `AttributeError: module 'edmrn.ext.crew_events' has no attribute 'validate_payload'` (and similar).

- [ ] **Step 3: Write the implementation**

In `edmrn/ext/crew_events.py`, add `import math` and `from dataclasses import dataclass` to the
imports (alphabetical, in their groups). Update the module docstring's first line to
`"""Crew event envelopes: canonical bytes, ids, parsing and payload validation.` and its spec
reference to `(C2, C3, C12)`. Then append:

```python
# --- payload validation (C12) -----------------------------------------------------------

SIGNAL_TYPES = ('biological', 'geological', 'human', 'guardian', 'thargoid', 'other', 'mining')
SCAN_TYPES = ('AutoScan', 'Detailed', 'NavBeaconDetail', 'Basic')
AUTO_FINDS = ('biological', 'stellar_phenomenon', 'codex', 'valuable_body')
MANUAL_FINDS = AUTO_FINDS + ('other',)

NAME_MAX = 128
TEXT_MAX = 2000
_FORBIDDEN = re.compile('[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069\ud800-\udfff]')
_DIGITS = re.compile(r'[0-9]{1,20}')

Checker = Callable[[Any, str], None]


def _string(max_len: int, allow_empty: bool) -> Checker:
    def check(value: Any, path: str) -> None:
        if not isinstance(value, str):
            raise PayloadError(f'{path} must be a string')
        if not value and not allow_empty:
            raise PayloadError(f'{path} must not be empty')
        if len(value) > max_len:
            raise PayloadError(f'{path} is longer than {max_len} characters')
        if _FORBIDDEN.search(value):
            raise PayloadError(f'{path} contains a control, bidi or surrogate character')
    return check


def _integer(lo: int, hi: int) -> Checker:
    def check(value: Any, path: str) -> None:
        if not _is_int(value) or not lo <= value <= hi:
            raise PayloadError(f'{path} must be an integer from {lo} to {hi}')
    return check


def _boolean(value: Any, path: str) -> None:
    if not isinstance(value, bool):
        raise PayloadError(f'{path} must be true or false')


def _choice(*values: str) -> Checker:
    def check(value: Any, path: str) -> None:
        if not isinstance(value, str) or value not in values:
            raise PayloadError(f'{path} must be one of {", ".join(values)}')
    return check


def _address(value: Any, path: str) -> None:
    if not isinstance(value, str) or not _DIGITS.fullmatch(value) or int(value) >= 2**64:
        raise PayloadError(f'{path} must be a decimal string below 2^64')


def _uuid(value: Any, path: str) -> None:
    if not is_uuid7(value):
        raise PayloadError(f'{path} must be a UUIDv7')


def _coords(value: Any, path: str) -> None:
    if (not isinstance(value, list) or len(value) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
            or not all(math.isfinite(v) and abs(v) <= 1e7 for v in value)):
        raise PayloadError(f'{path} must be three finite numbers')


def _nullable(inner: Checker) -> Checker:
    def check(value: Any, path: str) -> None:
        if value is not None:
            inner(value, path)
    return check


def _counts(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise PayloadError(f'{path} must be an object')
    for key, count in value.items():
        if key not in SIGNAL_TYPES:
            raise PayloadError(f'{path} has unknown signal type {key!r}')
        _integer(0, 1000)(count, f'{path}.{key}')


def _list_of(inner: Checker, max_items: int) -> Checker:
    def check(value: Any, path: str) -> None:
        if not isinstance(value, list) or len(value) > max_items:
            raise PayloadError(f'{path} must be a list of at most {max_items} items')
        for index, item in enumerate(value):
            inner(item, f'{path}[{index}]')
    return check


def _any_object(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise PayloadError(f'{path} must be an object')


def _object(fields: dict) -> Checker:
    def check(value: Any, path: str) -> None:
        if not isinstance(value, dict):
            raise PayloadError(f'{path} must be an object')
        if set(value) != set(fields):
            unknown, missing = set(value) - set(fields), set(fields) - set(value)
            raise PayloadError(f'{path}: unknown keys {sorted(map(str, unknown))}, '
                               f'missing keys {sorted(missing)}')
        for key, inner in fields.items():
            inner(value[key], f'{path}.{key}')
    return check


_NAME = _string(NAME_MAX, allow_empty=False)
_NAME_OR_NULL = _nullable(_NAME)
_TEXT = _string(TEXT_MAX, allow_empty=True)
_BODY_ID = _integer(0, 65535)

_VISIT = _object({'commander': _NAME, 'system': _NAME, 'address': _address,
                  'coords': _nullable(_coords)})
_HONK = _object({'system': _NAME, 'address': _address, 'body_count': _integer(0, 10000),
                 'non_body_count': _integer(0, 100000)})
_ALL_BODIES = _object({'system': _NAME, 'address': _address, 'count': _integer(0, 10000)})
_BODY_FIELDS = _object({
    'system': _NAME_OR_NULL, 'address': _address, 'body_id': _BODY_ID, 'name': _NAME,
    'tier': _choice('full', 'basic'), 'star_type': _NAME_OR_NULL, 'planet_class': _NAME_OR_NULL,
    'moon': _boolean, 'landable': _nullable(_boolean),
    'terraform': _nullable(_string(NAME_MAX, allow_empty=True)),
    'was_discovered': _boolean, 'was_mapped': _boolean, 'scan_type': _choice(*SCAN_TYPES)})
_SIGNALS_FIELDS = _object({
    'system': _NAME_OR_NULL, 'address': _address, 'body_id': _BODY_ID, 'body_name': _NAME,
    'source': _choice('fss', 'saa'), 'counts': _counts, 'genera': _list_of(_NAME, 64)})
_FIND_DETAILS = {
    'biological': _object({'count': _integer(1, 1000)}),
    'stellar_phenomenon': _object({'signal': _NAME}),
    'codex': _object({'entry_id': _integer(0, 2**31 - 1), 'name': _NAME, 'category': _NAME,
                      'subcategory': _NAME, 'new_entry': _boolean}),
    'valuable_body': _object({'planet_class': _NAME,
                              'terraform': _string(NAME_MAX, allow_empty=True)}),
}
_AUTO_FIND = _object({
    'mode': _choice('auto'), 'find_kind': _choice(*AUTO_FINDS), 'system': _NAME_OR_NULL,
    'address': _address, 'body_name': _NAME_OR_NULL, 'body_id': _nullable(_BODY_ID),
    'detail': _any_object})
_MANUAL_FIND = _object({
    'mode': _choice('manual'), 'find_id': _uuid, 'find_kind': _choice(*MANUAL_FINDS),
    'system': _NAME_OR_NULL, 'address': _address, 'body_name': _NAME_OR_NULL,
    'body_id': _nullable(_BODY_ID), 'note': _TEXT, 'deleted': _boolean})
_NOTE = _object({'note_id': _uuid, 'system': _NAME_OR_NULL, 'address': _address, 'text': _TEXT,
                 'deleted': _boolean})


def _body(value: Any, path: str) -> None:
    _BODY_FIELDS(value, path)
    if (value['star_type'] is None) == (value['planet_class'] is None):
        raise PayloadError(f'{path}: exactly one of star_type and planet_class must be set')
    if (value['tier'] == 'basic') != (value['scan_type'] == 'Basic'):
        raise PayloadError(f'{path}: tier basic belongs to scan_type Basic only')


def _signals(value: Any, path: str) -> None:
    _SIGNALS_FIELDS(value, path)
    if value['source'] == 'fss' and value['genera']:
        raise PayloadError(f'{path}: FSS signals carry no genera')


def _find(value: Any, path: str) -> None:
    mode = value.get('mode') if isinstance(value, dict) else None
    if mode == 'auto':
        _AUTO_FIND(value, path)
        _FIND_DETAILS[value['find_kind']](value['detail'], f'{path}.detail')
        if value['find_kind'] != 'stellar_phenomenon' and value['body_id'] is None:
            raise PayloadError(f'{path}: a {value["find_kind"]} find needs a body_id')
    elif mode == 'manual':
        _MANUAL_FIND(value, path)
    else:
        raise PayloadError(f'{path}.mode must be auto or manual')


_VALIDATORS: dict[tuple[str, int], Checker] = {
    ('visit', 1): _VISIT, ('honk', 1): _HONK, ('all_bodies', 1): _ALL_BODIES,
    ('body', 1): _body, ('signals', 1): _signals, ('find', 1): _find, ('note', 1): _NOTE,
}


def is_known(kind: str, v: int) -> bool:
    return (kind, v) in _VALIDATORS


def validate_payload(kind: str, v: int, payload: Any) -> None:
    check = _VALIDATORS.get((kind, v))
    if check is None:
        raise PayloadError(f'unknown kind {kind!r} version {v!r}')
    check(payload, 'payload')


# --- natural keys and drafts (C3, C5) ----------------------------------------------------

def natural_key(kind: str, created_at: str, payload: dict) -> Optional[tuple]:
    """Key parts of a journal-derived event, from its payload; None for manual events."""
    if kind in ('visit', 'honk'):
        return (payload['address'], created_at)
    if kind == 'all_bodies':
        return (payload['address'],)
    if kind == 'body':
        return (payload['address'], payload['body_id'], payload['tier'])
    if kind == 'signals':
        return (payload['address'], payload['body_id'], payload['source'])
    if kind == 'find' and payload.get('mode') == 'auto':
        find_kind = payload['find_kind']
        if find_kind == 'stellar_phenomenon':
            return (payload['address'], find_kind)
        if find_kind == 'codex':
            return (payload['address'], find_kind, payload['detail']['entry_id'], payload['body_id'])
        return (payload['address'], find_kind, payload['body_id'])
    return None


@dataclass(frozen=True)
class Draft:
    """An unsigned own event waiting for the writer. `commander` is the local key, never sent."""
    kind: str
    commander: str
    created_at: str
    payload: dict
    event_id: Optional[str] = None

    def resolve_id(self, author: str) -> str:
        if self.event_id is not None:
            return self.event_id
        key = natural_key(self.kind, self.created_at, self.payload)
        if key is None:
            raise PayloadError(f'{self.kind} draft has neither a natural key nor an event_id')
        return deterministic_id(author, self.kind, *key)
```

Note: `abs(v) <= 1e7` in `_coords` bounds galactic coordinates (the galaxy spans about ±70,000 ly);
it rejects absurd values that are still finite.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_events.py -q`
Expected: all pass. If `test_hostile_names_are_rejected[a\ud800b]` errors in pytest's id
generation, give that parametrize list explicit `ids=` (`range(len(HOSTILE_STRINGS))`).

- [ ] **Step 5: Mutation check**

Temporarily change `_FORBIDDEN`'s range `\u202a-\u202e` to `\u202a-\u202d`. Run the tests. Expected:
`test_hostile_names_are_rejected` fails for `a\u202eb`. Revert.

- [ ] **Step 6: Commit**

```bash
git add edmrn/ext/crew_events.py tests/test_crew_events.py
git commit -m "feat(crew): payload validators, natural keys and drafts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 3: Per-commander identities

**Files:**
- Create: `edmrn/ext/crew_identity.py`
- Test: `tests/test_crew_identity.py`

**Interfaces:**
- Consumes: Task 1's `MAX_SAFE_INT`.
- Produces:
  - `class KeyFileError(Exception)`
  - `b64url(data: bytes) -> str`
  - `verify(author: str, signature: bytes, data: bytes) -> bool`
  - `class Identity` with attributes `author: str`, `path: Path`, `high_water: int` and methods
    `sign(data: bytes) -> bytes`, `raise_high_water(seq: int) -> None`, `export_text() -> str`
  - `class KeyRing(directory)` with `path_for(commander: str) -> Path`,
    `for_commander(commander: str) -> Identity`, `export_text(commander: str) -> str`,
    `import_text(commander: str, text: str, *, replace: bool = False) -> Identity`

Key file format (JSON, one per commander, `crew_keys/cmdr-<24 hex of SHA-256(commander)>.json`):
`{"v": 1, "author": <base64url public key>, "private_key": <base64url raw 32 bytes>, "high_water": <int>}`.
Writes go to `<name>.tmp`, then `os.replace`. A file that exists but cannot be used raises
`KeyFileError` and is never overwritten (spec, "Key file unreadable").

- [ ] **Step 1: Write the failing tests**

Create `tests/test_crew_identity.py`:

```python
import json

import pytest

from edmrn.ext.crew_identity import KeyFileError, KeyRing, b64url, verify


@pytest.fixture
def ring(tmp_path):
    return KeyRing(tmp_path / "crew_keys")


def test_new_commander_gets_a_key_file_and_a_stable_author(ring, tmp_path):
    ident = ring.for_commander("F123")
    assert len(ident.author) == 43 and ident.high_water == 0
    assert ident.path.parent == tmp_path / "crew_keys"
    assert ident.path.name.startswith("cmdr-") and "F123" not in ident.path.name
    again = KeyRing(tmp_path / "crew_keys").for_commander("F123")
    assert again.author == ident.author


def test_two_commanders_get_two_identities(ring):
    assert ring.for_commander("F1").author != ring.for_commander("F2").author
    assert ring.path_for("F1") != ring.path_for("F2")


def test_sign_and_verify(ring):
    ident = ring.for_commander("F1")
    sig = ident.sign(b"hello")
    assert len(sig) == 64
    assert verify(ident.author, sig, b"hello")
    assert not verify(ident.author, sig, b"hellp")
    tampered = bytes([sig[0] ^ 1]) + sig[1:]
    assert not verify(ident.author, tampered, b"hello")
    assert not verify(ring.for_commander("F2").author, sig, b"hello")


@pytest.mark.parametrize("author,sig", [
    ("A" * 43, b"s" * 64),
    ("short", b"s" * 64),
    ("!" * 43, b"s" * 64),
    (None, b"s" * 64),
])
def test_verify_never_raises(author, sig):
    assert verify(author, sig, b"x") is False


def test_verify_rejects_wrong_signature_length_and_non_canonical_author(ring):
    ident = ring.for_commander("F1")
    sig = ident.sign(b"x")
    assert not verify(ident.author, sig[:63], b"x")
    assert not verify(ident.author, sig + b"\x00", b"x")
    # The last base64url character carries 2 unused bits; flipping one of them decodes to the
    # same key bytes. A second spelling of one author must not verify.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    last = alphabet.index(ident.author[-1])
    twin = ident.author[:-1] + alphabet[last ^ 1]
    assert not verify(twin, sig, b"x")


def test_high_water_survives_reload(ring, tmp_path):
    ident = ring.for_commander("F1")
    ident.raise_high_water(41)
    ident.raise_high_water(7)
    assert ident.high_water == 41
    assert KeyRing(tmp_path / "crew_keys").for_commander("F1").high_water == 41
    assert not ident.path.with_name(ident.path.name + ".tmp").exists()


@pytest.mark.parametrize("content", [
    "not json",
    "[]",
    '{"v": 1}',
    '{"v": 2, "author": "x", "private_key": "x", "high_water": 0}',
])
def test_unusable_key_file_raises_and_is_kept(ring, content):
    path = ring.path_for("F1")
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(KeyFileError):
        ring.for_commander("F1")
    assert path.read_text(encoding="utf-8") == content


def test_key_file_whose_author_does_not_match_raises(ring, tmp_path):
    record = json.loads(ring.export_text("F1"))
    record["author"] = ring.for_commander("F2").author
    other = KeyRing(tmp_path / "other")
    with pytest.raises(KeyFileError, match="author"):
        other.import_text("F9", json.dumps(record))


def test_bad_high_water_raises(ring):
    record = json.loads(ring.export_text("F1"))
    for bad in (-1, True, 2**53, "3"):
        with pytest.raises(KeyFileError):
            KeyRing(ring.directory / "x").import_text("F1", json.dumps(dict(record, high_water=bad)))


def test_export_import_moves_the_identity(ring, tmp_path):
    ident = ring.for_commander("F1")
    ident.raise_high_water(5)
    text = ring.export_text("F1")
    other = KeyRing(tmp_path / "elsewhere")
    imported = other.import_text("F1", text)
    assert imported.author == ident.author and imported.high_water == 5
    assert verify(imported.author, imported.sign(b"m"), b"m")


def test_import_refuses_to_replace_an_existing_key_unless_asked(ring):
    ring.for_commander("F1")
    text = KeyRing(ring.directory / "x").export_text("F2")
    with pytest.raises(KeyFileError):
        ring.import_text("F1", text)
    assert ring.import_text("F1", text, replace=True).author == json.loads(text)["author"]
    assert KeyRing(ring.directory).for_commander("F1").author == json.loads(text)["author"]


def test_b64url_has_no_padding():
    assert b64url(b"\x00" * 32).endswith("A") and "=" not in b64url(b"\x00" * 32)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_identity.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'edmrn.ext.crew_identity'`.

- [ ] **Step 3: Write the implementation**

Create `edmrn/ext/crew_identity.py`:

```python
"""Per-commander Ed25519 identities in key files next to edmrn.db (Spec A C1, C4).

Key files live outside the database so that deleting an unusable database (ADR-0004) keeps the
identity. A key file that exists but cannot be used is never replaced: a new key is a new member.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from edmrn.ext.crew_events import MAX_SAFE_INT

KEY_FILE_VERSION = 1
_KEY_FIELDS = {'v', 'author', 'private_key', 'high_water'}
_B64URL_43 = re.compile(r'[A-Za-z0-9_-]{43}')


class KeyFileError(Exception):
    """A key file exists but cannot be used."""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _raw32(text: Any) -> bytes:
    """32 bytes from canonical unpadded base64url; ValueError otherwise."""
    if not isinstance(text, str) or not _B64URL_43.fullmatch(text):
        raise ValueError('not 43 base64url characters')
    try:
        raw = base64.urlsafe_b64decode(text + '=')
    except binascii.Error as e:
        raise ValueError(str(e)) from e
    if len(raw) != 32 or b64url(raw) != text:
        raise ValueError('not the canonical spelling of 32 bytes')
    return raw


def _author_of(key: Ed25519PrivateKey) -> str:
    return b64url(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                serialization.PublicFormat.Raw))


def verify(author: Any, signature: Any, data: Any) -> bool:
    """True if `signature` is the author's Ed25519 signature over `data`. Never raises."""
    try:
        if not isinstance(signature, (bytes, bytearray)) or len(signature) != 64:
            return False
        Ed25519PublicKey.from_public_bytes(_raw32(author)).verify(bytes(signature), bytes(data))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def _record(key: Ed25519PrivateKey, high_water: int) -> dict:
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())
    return {'v': KEY_FILE_VERSION, 'author': _author_of(key), 'private_key': b64url(raw),
            'high_water': high_water}


def _parse_record(text: str) -> tuple[Ed25519PrivateKey, int]:
    try:
        record = json.loads(text)
    except (ValueError, RecursionError) as e:
        raise KeyFileError(f'key file is not JSON: {e}') from e
    if not isinstance(record, dict) or set(record) != _KEY_FIELDS or record['v'] != KEY_FILE_VERSION:
        raise KeyFileError('unknown key file format')
    high_water = record['high_water']
    if not isinstance(high_water, int) or isinstance(high_water, bool) or not 0 <= high_water <= MAX_SAFE_INT:
        raise KeyFileError('high_water must be an integer from 0 to 2^53-1')
    try:
        key = Ed25519PrivateKey.from_private_bytes(_raw32(record['private_key']))
    except ValueError as e:
        raise KeyFileError(f'private key unusable: {e}') from e
    if _author_of(key) != record['author']:
        raise KeyFileError('author does not match the private key')
    return key, high_water


def _write(path: Path, key: Ed25519PrivateKey, high_water: int) -> None:
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(_record(key, high_water), f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Identity:
    def __init__(self, path: Path, key: Ed25519PrivateKey, high_water: int) -> None:
        self.path = path
        self._key = key
        self.author = _author_of(key)
        self.high_water = high_water

    def sign(self, data: bytes) -> bytes:
        return self._key.sign(data)

    def raise_high_water(self, seq: int) -> None:
        """Persist the highest allocated seq; called after the log commit (C4)."""
        if seq <= self.high_water:
            return
        self.high_water = seq
        _write(self.path, self._key, seq)

    def export_text(self) -> str:
        return json.dumps(_record(self._key, self.high_water))


class KeyRing:
    def __init__(self, directory) -> None:
        self.directory = Path(directory)
        self._cache: dict[str, Identity] = {}

    def path_for(self, commander: str) -> Path:
        digest = hashlib.sha256(commander.encode('utf-8', 'surrogatepass')).hexdigest()[:24]
        return self.directory / f'cmdr-{digest}.json'

    def for_commander(self, commander: str) -> Identity:
        cached = self._cache.get(commander)
        if cached is not None:
            return cached
        path = self.path_for(commander)
        if path.exists():
            try:
                text = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError) as e:
                raise KeyFileError(f'{path.name}: {e}') from e
            try:
                key, high_water = _parse_record(text)
            except KeyFileError as e:
                raise KeyFileError(f'{path.name}: {e}') from e
        else:
            self.directory.mkdir(parents=True, exist_ok=True)
            key, high_water = Ed25519PrivateKey.generate(), 0
            _write(path, key, high_water)
        identity = Identity(path, key, high_water)
        self._cache[commander] = identity
        return identity

    def export_text(self, commander: str) -> str:
        return self.for_commander(commander).export_text()

    def import_text(self, commander: str, text: str, *, replace: bool = False) -> Identity:
        key, high_water = _parse_record(text)
        path = self.path_for(commander)
        if path.exists() and not replace:
            raise KeyFileError('a key file already exists for this commander')
        self.directory.mkdir(parents=True, exist_ok=True)
        _write(path, key, high_water)
        identity = Identity(path, key, high_water)
        self._cache[commander] = identity
        return identity
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_identity.py -q`
Expected: all pass.

- [ ] **Step 5: Mutation check**

Temporarily delete `or b64url(raw) != text` in `_raw32`. Run the tests. Expected:
`test_verify_rejects_wrong_signature_length_and_non_canonical_author` fails. Revert.

- [ ] **Step 6: Commit**

```bash
git add edmrn/ext/crew_identity.py tests/test_crew_identity.py
git commit -m "feat(crew): per-commander Ed25519 key files with seq high-water mark

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 4: Store access for feature modules, crew schema and the signed log

**Files:**
- Modify: `edmrn/ext/store.py` (migration list, two methods)
- Create: `edmrn/ext/crew_store.py`
- Create: `tests/crew_helpers.py`
- Test: `tests/test_crew_store.py`

**Interfaces:**
- Consumes:
  - from Task 1: `build_envelope`, `seal`, `parse_envelope`, `EnvelopeError`;
  - from Task 2: `validate_payload`, `is_known`, `Draft`, `PayloadError`, `ENVELOPE_VERSION`;
  - from Task 3: `Identity`, `verify`, `KeyRing`.
- Produces:
  - `Store.transaction()`, a context manager yielding the connection inside `BEGIN IMMEDIATE … COMMIT`
    (rollback on any exception);
  - `Store.read()`, a context manager yielding the connection under the store lock;
  - `store.SCHEMA_VERSION == 2`;
  - `crew_store.PROJECTION_TABLES: tuple[str, ...]`;
  - `crew_store._PROJECTORS: dict[str, Callable[[sqlite3.Connection, dict], None]]`, empty here and
    filled in Task 5;
  - `class CrewStore(store: Store)` with
    `append_own(items: list[tuple[Identity, Draft]]) -> int` (number written) and
    `insert_received(envelope: bytes, signature: bytes) -> bool` (True if a new log row; raises
    `EnvelopeError` on bad bytes or signature);
  - in `tests/crew_helpers.py`: `ADDR`, `T0`, `ts(minute, second=0)`, `visit`, `honk`, `all_bodies`,
    `body`, `signals`, `bio_find`, `note`, `manual_find`, `sealed(identity, draft, seq, **over)` and
    `dump(store)`.

- [ ] **Step 1: Write the test helpers**

Create `tests/crew_helpers.py`:

```python
"""Builders for the crew tests: drafts, sealed envelopes and a dump of the projections.

Imported by test modules only, after tests/conftest.py redirected the home folders.
"""
from edmrn.ext import crew_events as ev
from edmrn.ext.crew_store import PROJECTION_TABLES

ADDR = "10477373803"
T0 = "2026-10-12T10:00:00Z"


def ts(minute, second=0):
    return f"2026-10-12T10:{minute:02d}:{second:02d}Z"


def visit(cmdr="F1", at=T0, address=ADDR, system="Sol", name="Jameson", coords=(0.0, 0.0, 0.0)):
    return ev.Draft("visit", cmdr, at, {"commander": name, "system": system, "address": address,
                                         "coords": list(coords) if coords is not None else None})


def honk(cmdr="F1", at=T0, address=ADDR, system="Sol", bodies=3, non_bodies=0):
    return ev.Draft("honk", cmdr, at, {"system": system, "address": address, "body_count": bodies,
                                        "non_body_count": non_bodies})


def all_bodies(cmdr="F1", at=T0, address=ADDR, system="Sol", count=3):
    return ev.Draft("all_bodies", cmdr, at, {"system": system, "address": address, "count": count})


def body(cmdr="F1", at=T0, address=ADDR, body_id=1, name="Sol 1", tier="full", planet_class="Rocky body",
         star_type=None, landable=True, terraform="", moon=False, discovered=True, mapped=False, system="Sol"):
    scan_type = "Basic" if tier == "basic" else "Detailed"
    if tier == "basic":
        landable, terraform = None, None
    if star_type is not None:
        planet_class, landable, terraform = None, None, None
    return ev.Draft("body", cmdr, at, {
        "system": system, "address": address, "body_id": body_id, "name": name, "tier": tier,
        "star_type": star_type, "planet_class": planet_class, "moon": moon, "landable": landable,
        "terraform": terraform, "was_discovered": discovered, "was_mapped": mapped, "scan_type": scan_type})


def signals(cmdr="F1", at=T0, address=ADDR, body_id=1, body_name="Sol 1", source="fss", counts=None,
            genera=(), system="Sol"):
    return ev.Draft("signals", cmdr, at, {
        "system": system, "address": address, "body_id": body_id, "body_name": body_name,
        "source": source, "counts": dict(counts or {}), "genera": list(genera)})


def bio_find(cmdr="F1", at=T0, address=ADDR, body_id=1, body_name="Sol 1", count=2, system="Sol"):
    return ev.Draft("find", cmdr, at, {
        "mode": "auto", "find_kind": "biological", "system": system, "address": address,
        "body_name": body_name, "body_id": body_id, "detail": {"count": count}})


def note(cmdr="F1", at=T0, note_id=None, text="hello", deleted=False, address=ADDR, system="Sol"):
    return ev.Draft("note", cmdr, at, {
        "note_id": note_id or ev.uuid7(), "system": system, "address": address, "text": text,
        "deleted": deleted}, event_id=ev.uuid7())


def manual_find(cmdr="F1", at=T0, find_id=None, note_text="wreck", deleted=False, address=ADDR,
                find_kind="other"):
    return ev.Draft("find", cmdr, at, {
        "mode": "manual", "find_id": find_id or ev.uuid7(), "find_kind": find_kind, "system": "Sol",
        "address": address, "body_name": None, "body_id": None, "note": note_text,
        "deleted": deleted}, event_id=ev.uuid7())


def sealed(identity, draft, seq, **over):
    """Envelope bytes and signature, as a friend's client would send them."""
    env = ev.build_envelope(draft.resolve_id(identity.author), identity.author, seq, draft.kind,
                            draft.created_at, draft.payload)
    env.update(over)
    return ev.seal(env, identity.sign)


def dump(store):
    with store.read() as conn:
        return {table: sorted(conn.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
                for table in PROJECTION_TABLES}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_crew_store.py`:

```python
import random
import sqlite3

import pytest

from crew_helpers import (ADDR, all_bodies, bio_find, body, dump, honk, manual_find, note, sealed,
                          signals, ts, visit)
from edmrn.ext import crew_events as ev
from edmrn.ext.crew_identity import KeyRing, verify
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.store import MIGRATIONS, SCHEMA_VERSION, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "edmrn.db")
    yield s
    s.close()


@pytest.fixture
def crew(store):
    return CrewStore(store)


@pytest.fixture
def ring(tmp_path):
    return KeyRing(tmp_path / "crew_keys")


@pytest.fixture
def friend(tmp_path):
    return KeyRing(tmp_path / "friend_keys").for_commander("F9")


def log_rows(store):
    with store.read() as conn:
        return conn.execute("SELECT author, seq, event_id, kind FROM crew_log ORDER BY author, seq").fetchall()


def projected_count(store):
    with store.read() as conn:
        return conn.execute("SELECT COUNT(*) FROM crew_projected").fetchone()[0]


# --- store access and migration ---------------------------------------------------------

def test_schema_version_is_2():
    assert SCHEMA_VERSION == 2


def test_version_1_database_migrates_and_keeps_neutron_routes(tmp_path):
    path = tmp_path / "edmrn.db"
    raw = sqlite3.connect(path)
    for statement in MIGRATIONS[0]:
        raw.execute(statement)
    raw.execute("INSERT INTO neutron_routes (created_at, updated_at, from_system, to_system, range_ly, "
                "boost, current_index, state) VALUES ('t', 't', 'Sol', 'Colonia', 70, 'x4', 0, 'active')")
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()
    s = Store(path)
    try:
        assert [r.from_system for r in s.list_neutron_routes()] == ["Sol"]
        with s.read() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
            assert conn.execute("SELECT COUNT(*) FROM crew_log").fetchone()[0] == 0
    finally:
        s.close()


def test_transaction_rolls_back_on_error(store):
    with pytest.raises(RuntimeError):
        with store.transaction() as conn:
            conn.execute("INSERT INTO crew_projected (event_id) VALUES ('x')")
            raise RuntimeError("boom")
    assert projected_count(store) == 0


# --- own events (C4) --------------------------------------------------------------------

def test_own_events_get_contiguous_seqs_and_verify(crew, ring, store):
    ident = ring.for_commander("F1")
    assert crew.append_own([(ident, visit()), (ident, honk(at=ts(1))), (ident, all_bodies(at=ts(2)))]) == 3
    assert [r[1] for r in log_rows(store)] == [1, 2, 3]
    assert ident.high_water == 3
    with store.read() as conn:
        for data, signature in conn.execute("SELECT envelope, signature FROM crew_log"):
            assert verify(ident.author, signature, data)
            assert ev.parse_envelope(data)["author"] == ident.author


def test_appending_the_same_drafts_again_adds_nothing(crew, ring, store):
    ident = ring.for_commander("F1")
    drafts = [visit(), honk(at=ts(1))]
    crew.append_own([(ident, d) for d in drafts])
    assert crew.append_own([(ident, d) for d in drafts]) == 0
    assert len(log_rows(store)) == 2


def test_duplicate_drafts_in_one_batch_are_written_once(crew, ring):
    ident = ring.for_commander("F1")
    assert crew.append_own([(ident, visit()), (ident, visit())]) == 1


def test_two_authors_in_one_system_give_two_events(crew, ring, store):
    a, b = ring.for_commander("F1"), ring.for_commander("F2")
    crew.append_own([(a, visit()), (b, visit("F2"))])
    rows = log_rows(store)
    assert {r[0] for r in rows} == {a.author, b.author}
    assert [r[1] for r in rows] == [1, 1]


def test_seq_continues_above_the_key_file_after_the_database_is_lost(tmp_path, ring):
    ident = ring.for_commander("F1")
    first = Store(tmp_path / "a.db")
    CrewStore(first).append_own([(ident, visit()), (ident, honk(at=ts(1)))])
    first.close()
    fresh = Store(tmp_path / "b.db")
    try:
        reloaded = KeyRing(tmp_path / "crew_keys").for_commander("F1")
        CrewStore(fresh).append_own([(reloaded, all_bodies(at=ts(2)))])
        assert [r[1] for r in log_rows(fresh)] == [3]
    finally:
        fresh.close()


def test_invalid_own_payload_raises_and_writes_nothing(crew, ring, store):
    ident = ring.for_commander("F1")
    bad = ev.Draft("visit", "F1", ts(0), {"system": "Sol"})
    with pytest.raises(ev.PayloadError):
        crew.append_own([(ident, visit(at=ts(5))), (ident, bad)])
    assert log_rows(store) == []
    assert ident.high_water == 0


# --- received events (C4) ---------------------------------------------------------------

def test_received_envelope_is_verified_and_stored_once(crew, store, friend):
    data, signature = sealed(friend, visit("F9"), 7)
    assert crew.insert_received(data, signature) is True
    assert crew.insert_received(data, signature) is False
    assert [(r[0], r[1]) for r in log_rows(store)] == [(friend.author, 7)]


@pytest.mark.parametrize("mutation", ["changed_byte", "short_signature", "signature_of_another_author"])
def test_received_envelope_with_bad_signature_is_rejected(crew, store, friend, tmp_path, mutation):
    data, signature = sealed(friend, visit("F9"), 1)
    if mutation == "changed_byte":
        data = data.replace(b"Sol", b"Sum")
    elif mutation == "short_signature":
        signature = signature[:10]
    else:
        stranger = KeyRing(tmp_path / "stranger").for_commander("F8")
        data, _ = sealed(stranger, visit("F8"), 1)
    with pytest.raises(ev.EnvelopeError):
        crew.insert_received(data, signature)
    assert log_rows(store) == []


@pytest.mark.parametrize("data", [b"[" * 5000, b"{}", b"x" * 20_000, "text"])
def test_received_malformed_bytes_are_rejected(crew, store, data):
    with pytest.raises(ev.EnvelopeError):
        crew.insert_received(data, b"s" * 64)
    assert log_rows(store) == []


def test_repeated_id_under_a_new_seq_is_stored_but_projected_once(crew, store, friend):
    draft = visit("F9")
    crew.insert_received(*sealed(friend, draft, 1))
    crew.insert_received(*sealed(friend, draft, 2))
    assert len(log_rows(store)) == 2
    assert projected_count(store) == 1


def test_unknown_kind_and_newer_version_are_stored_not_projected(crew, store, friend):
    chat = ev.Draft("chat", "F9", ts(0), {"text": "hi"}, event_id=ev.uuid7())
    crew.insert_received(*sealed(friend, chat, 1))
    crew.insert_received(*sealed(friend, visit("F9"), 2, v=2))
    assert len(log_rows(store)) == 2
    assert projected_count(store) == 0


def test_received_invalid_payload_is_stored_not_projected(crew, store, friend):
    hostile = ev.Draft("visit", "F9", ts(0), {"commander": "x", "system": "Sol‮", "address": "1",
                                                "coords": None}, event_id="0" * 32)
    assert crew.insert_received(*sealed(friend, hostile, 1)) is True
    assert projected_count(store) == 0
```

(The imports of `random`, `ADDR`, `bio_find`, `body`, `dump`, `manual_find`, `note` and `signals`
are used by Task 5's tests in this file; keep them.)

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_store.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'edmrn.ext.crew_store'`.

- [ ] **Step 4: Add the migration and the two methods to `store.py`**

In `edmrn/ext/store.py`, append a second entry to `MIGRATIONS`, after the neutron entry's closing
`],` and before the list's closing `]`:

```python
    [
        """CREATE TABLE crew_log (
            author     TEXT NOT NULL,
            seq        INTEGER NOT NULL CHECK (seq >= 1),
            event_id   TEXT NOT NULL,
            kind       TEXT NOT NULL,
            v          INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            envelope   BLOB NOT NULL,
            signature  BLOB NOT NULL,
            PRIMARY KEY (author, seq)
        )""",
        "CREATE INDEX crew_log_event_id ON crew_log (event_id)",
        "CREATE TABLE crew_projected (event_id TEXT PRIMARY KEY)",
        """CREATE TABLE crew_systems (
            address        TEXT PRIMARY KEY,
            name           TEXT,
            name_key       TEXT,
            x              REAL,
            y              REAL,
            z              REAL,
            coords_key     TEXT,
            body_count     INTEGER,
            body_count_key TEXT
        )""",
        """CREATE TABLE crew_visits (
            author      TEXT NOT NULL,
            address     TEXT NOT NULL,
            first_at    TEXT NOT NULL,
            last_at     TEXT NOT NULL,
            visit_count INTEGER NOT NULL,
            PRIMARY KEY (author, address)
        )""",
        """CREATE TABLE crew_honks (
            author     TEXT NOT NULL,
            address    TEXT NOT NULL,
            body_count INTEGER NOT NULL,
            honk_key   TEXT NOT NULL,
            PRIMARY KEY (author, address)
        )""",
        "CREATE TABLE crew_all_bodies (author TEXT NOT NULL, address TEXT NOT NULL, PRIMARY KEY (author, address))",
        "CREATE TABLE crew_explored (author TEXT NOT NULL, address TEXT NOT NULL, PRIMARY KEY (author, address))",
        """CREATE TABLE crew_body_reports (
            event_id       TEXT PRIMARY KEY,
            author         TEXT NOT NULL,
            created_at     TEXT NOT NULL,
            address        TEXT NOT NULL,
            body_id        INTEGER NOT NULL,
            tier           TEXT NOT NULL,
            name           TEXT NOT NULL,
            star_type      TEXT,
            planet_class   TEXT,
            moon           INTEGER NOT NULL,
            landable       INTEGER,
            terraform      TEXT,
            was_discovered INTEGER NOT NULL,
            was_mapped     INTEGER NOT NULL,
            scan_type      TEXT NOT NULL
        )""",
        "CREATE INDEX crew_body_reports_body ON crew_body_reports (address, body_id)",
        """CREATE TABLE crew_signals (
            address      TEXT NOT NULL,
            body_id      INTEGER NOT NULL,
            type         TEXT NOT NULL,
            signal_count INTEGER NOT NULL,
            PRIMARY KEY (address, body_id, type)
        )""",
        """CREATE TABLE crew_genera (
            address TEXT NOT NULL,
            body_id INTEGER NOT NULL,
            genus   TEXT NOT NULL,
            PRIMARY KEY (address, body_id, genus)
        )""",
        """CREATE TABLE crew_signal_sources (
            address   TEXT NOT NULL,
            body_id   INTEGER NOT NULL,
            source    TEXT NOT NULL,
            body_name TEXT NOT NULL,
            PRIMARY KEY (address, body_id, source)
        )""",
        """CREATE TABLE crew_finds (
            event_id   TEXT PRIMARY KEY,
            author     TEXT NOT NULL,
            created_at TEXT NOT NULL,
            find_kind  TEXT NOT NULL,
            system     TEXT,
            address    TEXT NOT NULL,
            body_name  TEXT,
            body_id    INTEGER,
            detail     TEXT NOT NULL
        )""",
        """CREATE TABLE crew_manual_finds (
            author     TEXT NOT NULL,
            find_id    TEXT NOT NULL,
            seq        INTEGER NOT NULL,
            event_id   TEXT NOT NULL,
            created_at TEXT NOT NULL,
            find_kind  TEXT NOT NULL,
            system     TEXT,
            address    TEXT NOT NULL,
            body_name  TEXT,
            body_id    INTEGER,
            note       TEXT NOT NULL,
            deleted    INTEGER NOT NULL,
            PRIMARY KEY (author, find_id)
        )""",
        """CREATE TABLE crew_notes (
            author     TEXT NOT NULL,
            note_id    TEXT NOT NULL,
            seq        INTEGER NOT NULL,
            event_id   TEXT NOT NULL,
            created_at TEXT NOT NULL,
            system     TEXT,
            address    TEXT NOT NULL,
            text       TEXT NOT NULL,
            deleted    INTEGER NOT NULL,
            PRIMARY KEY (author, note_id)
        )""",
    ],
```

Then add these two methods to `class Store`, directly after `_transaction`:

```python
    def transaction(self):
        """BEGIN IMMEDIATE ... COMMIT under the store lock, for feature modules (ADR-0006)."""
        return self._transaction()

    @contextmanager
    def read(self):
        """The connection under the store lock, for feature modules' queries (ADR-0006)."""
        with self._lock:
            yield self._conn
```

Update the module docstring to
`"""SQLite store for EDMRN fork data (ADR-0004, ADR-0006: one connection, rollback journal, bound values)."""`.

- [ ] **Step 5: Write `crew_store.py` (log part)**

Create `edmrn/ext/crew_store.py`:

```python
"""Crew event log and its projections (Spec A C4, C7, C8; store layer per ADR-0006).

Writes run inside Store.transaction(); the writer in crew.py calls them on the Tk thread. Reads take
Store.read() and may come from any thread. Values reach SQL only as parameters.
"""
from __future__ import annotations

import sqlite3
from typing import Callable

from edmrn.ext import crew_events as ev
from edmrn.ext.crew_identity import Identity, verify
from edmrn.ext.store import Store
from edmrn.logger import get_logger

logger = get_logger('CrewStore')

PROJECTION_TABLES = (
    'crew_projected', 'crew_systems', 'crew_visits', 'crew_honks', 'crew_all_bodies',
    'crew_explored', 'crew_body_reports', 'crew_signals', 'crew_genera', 'crew_signal_sources',
    'crew_finds', 'crew_manual_finds', 'crew_notes',
)

Projector = Callable[[sqlite3.Connection, dict], None]
_PROJECTORS: dict[str, Projector] = {}


def _insert(conn: sqlite3.Connection, env: dict, data: bytes, signature: bytes) -> bool:
    cursor = conn.execute(
        'INSERT OR IGNORE INTO crew_log (author, seq, event_id, kind, v, created_at, envelope, '
        'signature) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (env['author'], env['seq'], env['event_id'], env['kind'], env['v'], env['created_at'],
         data, signature))
    return cursor.rowcount == 1


def _project(conn: sqlite3.Connection, env: dict) -> None:
    """Apply an envelope once per event_id, if its kind, v and payload are known-good (C4)."""
    kind, v = env['kind'], env['v']
    if not ev.is_known(kind, v):
        return
    try:
        ev.validate_payload(kind, v, env['payload'])
    except ev.PayloadError as e:
        logger.warning(f"Crew event {env['event_id']} kept unprojected: {e}")
        return
    if conn.execute('INSERT OR IGNORE INTO crew_projected (event_id) VALUES (?)',
                    (env['event_id'],)).rowcount == 0:
        return
    projector = _PROJECTORS.get(kind)
    if projector is not None:
        projector(conn, env)


class CrewStore:
    def __init__(self, store: Store) -> None:
        self.store = store

    def append_own(self, items: list[tuple[Identity, ev.Draft]]) -> int:
        """Sign and log own drafts in one transaction (C4); the key files follow after the commit."""
        written = 0
        allocated: dict[str, tuple[Identity, int]] = {}
        with self.store.transaction() as conn:
            for identity, draft in items:
                ev.validate_payload(draft.kind, ev.ENVELOPE_VERSION, draft.payload)
                event_id = draft.resolve_id(identity.author)
                if conn.execute('SELECT 1 FROM crew_log WHERE event_id = ? LIMIT 1',
                                (event_id,)).fetchone():
                    continue
                if identity.author in allocated:
                    last = allocated[identity.author][1]
                else:
                    row = conn.execute('SELECT MAX(seq) FROM crew_log WHERE author = ?',
                                       (identity.author,)).fetchone()
                    last = max(row[0] or 0, identity.high_water)
                env = ev.build_envelope(event_id, identity.author, last + 1, draft.kind,
                                        draft.created_at, draft.payload)
                data, signature = ev.seal(env, identity.sign)
                _insert(conn, env, data, signature)
                _project(conn, env)
                allocated[identity.author] = (identity, last + 1)
                written += 1
        for identity, seq in allocated.values():
            try:
                identity.raise_high_water(seq)
            except OSError as e:
                logger.error(f"Crew key file not updated ({identity.path.name}): {e}")
        return written

    def insert_received(self, envelope: bytes, signature: bytes) -> bool:
        """A received envelope: structure, signature, then INSERT OR IGNORE on (author, seq)."""
        env = ev.parse_envelope(envelope)
        if not verify(env['author'], signature, envelope):
            raise ev.EnvelopeError('signature does not verify')
        with self.store.transaction() as conn:
            inserted = _insert(conn, env, bytes(envelope), bytes(signature))
            if inserted:
                _project(conn, env)
        return inserted
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_store.py tests/test_store.py -q`
Expected: all pass. `test_store.py` must stay green. `test_version1_file_without_tables_raises` now
runs migration 2 and then fails the probe, which still raises `StoreError`.

- [ ] **Step 7: Mutation check**

In `append_own`, temporarily replace `max(row[0] or 0, identity.high_water)` with `row[0] or 0`.
Run the tests. Expected: `test_seq_continues_above_the_key_file_after_the_database_is_lost` fails.
Revert.

- [ ] **Step 8: Commit**

```bash
git add edmrn/ext/store.py edmrn/ext/crew_store.py tests/crew_helpers.py tests/test_crew_store.py
git commit -m "feat(crew): signed event log on the store's connection (own and received events)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 5: Projections, rebuild and queries

**Files:**
- Modify: `edmrn/ext/crew_store.py`
- Test: `tests/test_crew_store.py` (append)

**Interfaces:**
- Consumes: Task 4's `_PROJECTORS`, `_project`, `PROJECTION_TABLES` and `CrewStore`; Task 1's
  `canonical_bytes` and `parse_envelope`.
- Produces (Spec C and Task 11 rely on these):
  - row dataclasses (all `frozen=True`): `SystemRow`, `VisitRow`, `BodyRow`, `BodyReport`,
    `BodySignals`, `FindRow` and `NoteRow`, with the fields shown in the code below;
  - `CrewStore` gains:
    - `rebuild_projections() -> None`;
    - `system(address: str) -> SystemRow | None`;
    - `explored(author: str | None = None) -> set[str]` (addresses; `None` means the whole group);
    - `visits(author: str) -> list[VisitRow]`;
    - `bodies(address: str) -> list[BodyRow]` (merged, one per body id) and
      `body_reports(address: str) -> list[BodyReport]` (every author);
    - `signals(address: str) -> dict[int, BodySignals]`;
    - `finds() -> list[FindRow]` (auto plus undeleted manual finds, newest first) and
      `manual_find(author: str, find_id: str) -> FindRow | None` (deleted ones too);
    - `notes(address: str | None = None) -> list[NoteRow]` (undeleted) and
      `note(author: str, note_id: str) -> NoteRow | None` (deleted ones too).

Merge rules (C8; every one is independent of arrival order):

| Table | Rule |
|---|---|
| `crew_systems` | `name` from the smallest order key, `x/y/z` from the smallest key carrying coords, `body_count` from the largest key (honk or `all_bodies`) |
| `crew_visits` | `first_at` = min, `last_at` = max, `visit_count` += 1 per new visit event |
| `crew_honks` | per author and system, the largest key wins |
| `crew_explored` | recomputed after every honk, `all_bodies` and `body`: an `all_bodies` row, or distinct body ids ≥ latest honk `body_count` ≥ 1 |
| `crew_body_reports` | every report; `bodies()` picks best tier, then the smallest (`created_at`, `author`, `event_id`) |
| `crew_signals` | per type, the maximum count |
| `crew_genera` | union |
| `crew_signal_sources` | per source; `body_name` = min |
| `crew_finds` | one row per auto-find `event_id` |
| `crew_manual_finds`, `crew_notes` | per (`author`, id), the highest `seq` wins |

The order key is `created_at|author|event_id`. All three parts have fixed shapes and no `|`, so
string order is the tuple order.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crew_store.py`:

```python
# --- projections (C7, C8) ----------------------------------------------------------------

def test_two_authors_in_one_system_give_one_system_row(crew, ring):
    a, b = ring.for_commander("F1"), ring.for_commander("F2")
    crew.append_own([(a, visit()), (b, visit("F2", at=ts(5)))])
    row = crew.system(ADDR)
    assert row.name == "Sol" and row.coords == (0.0, 0.0, 0.0)
    with crew.store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crew_systems").fetchone()[0] == 1


def test_system_name_and_coords_are_the_earliest_and_body_count_the_latest(crew, friend, ring):
    me = ring.for_commander("F1")
    crew.append_own([(me, visit(at=ts(5), system="Sol", coords=(1.0, 2.0, 3.0))),
                     (me, honk(at=ts(6), bodies=10))])
    crew.insert_received(*sealed(friend, visit("F9", at=ts(1), system="SOL", coords=(9.0, 9.0, 9.0)), 1))
    crew.insert_received(*sealed(friend, all_bodies("F9", at=ts(7), count=12), 2))
    row = crew.system(ADDR)
    assert row.name == "SOL" and row.coords == (9.0, 9.0, 9.0) and row.body_count == 12
    assert crew.system("1") is None


def test_visits_track_first_last_and_count(crew, ring):
    me = ring.for_commander("F1")
    crew.append_own([(me, visit(at=ts(3))), (me, visit(at=ts(1))), (me, visit(at=ts(2)))])
    [v] = crew.visits(me.author)
    assert (v.address, v.first_at, v.last_at, v.count) == (ADDR, ts(1), ts(3), 3)


def test_a_full_scan_after_a_basic_one_wins_and_basic_after_full_does_not(crew, ring):
    me = ring.for_commander("F1")
    crew.append_own([(me, body(at=ts(1), body_id=1, tier="basic")),
                     (me, body(at=ts(2), body_id=1, tier="full", landable=True)),
                     (me, body(at=ts(1), body_id=2, tier="full", landable=False)),
                     (me, body(at=ts(2), body_id=2, tier="basic"))])
    merged = {b.body_id: b for b in crew.bodies(ADDR)}
    assert merged[1].tier == "full" and merged[1].landable is True
    assert merged[2].tier == "full" and merged[2].landable is False


def test_merged_body_is_the_earliest_full_report_and_flags_stay_per_author(crew, ring, friend):
    me = ring.for_commander("F1")
    crew.append_own([(me, body(at=ts(5), body_id=3, discovered=True))])
    crew.insert_received(*sealed(friend, body("F9", at=ts(2), body_id=3, discovered=False,
                                              name="Sol 3 friend"), 1))
    [merged] = crew.bodies(ADDR)
    assert merged.name == "Sol 3 friend" and merged.author == friend.author
    reports = {r.author: r for r in crew.body_reports(ADDR)}
    assert reports[friend.author].was_discovered is False
    assert reports[me.author].was_discovered is True


def test_explored_from_all_bodies(crew, ring):
    me = ring.for_commander("F1")
    crew.append_own([(me, all_bodies())])
    assert crew.explored(me.author) == {ADDR}


def test_explored_when_scanned_bodies_reach_the_latest_honk_count(crew, ring):
    me = ring.for_commander("F1")
    crew.append_own([(me, honk(at=ts(1), bodies=3)),
                     (me, body(at=ts(2), body_id=0, star_type="G", name="Sol")),
                     (me, body(at=ts(3), body_id=1, tier="basic"))])
    assert crew.explored(me.author) == set()
    crew.append_own([(me, body(at=ts(4), body_id=2))])
    assert crew.explored(me.author) == {ADDR}
    crew.append_own([(me, honk(at=ts(9), bodies=5))])
    assert crew.explored(me.author) == set()


def test_a_visit_or_a_honk_alone_never_counts(crew, ring):
    me = ring.for_commander("F1")
    crew.append_own([(me, visit()), (me, honk(at=ts(1), bodies=0))])
    assert crew.explored() == set()


def test_explored_is_per_author_and_the_group_is_the_union(crew, ring, friend):
    me = ring.for_commander("F1")
    crew.append_own([(me, all_bodies())])
    crew.insert_received(*sealed(friend, all_bodies("F9", address="42", system="Other"), 1))
    assert crew.explored(me.author) == {ADDR}
    assert crew.explored(friend.author) == {"42"}
    assert crew.explored() == {ADDR, "42"}


def test_signals_take_the_highest_count_and_the_union_of_genera(crew, ring, friend):
    me = ring.for_commander("F1")
    crew.append_own([(me, signals(source="fss", counts={"biological": 2, "geological": 1}))])
    crew.insert_received(*sealed(friend, signals("F9", source="saa", counts={"biological": 1, "human": 1},
                                                 genera=["$Codex_Ent_Bacterial_Genus_Name;"]), 1))
    crew.insert_received(*sealed(friend, signals("F9", at=ts(3), source="fss",
                                                 counts={"biological": 3}), 2))
    s = crew.signals(ADDR)[1]
    assert s.counts == {"biological": 3, "geological": 1, "human": 1}
    assert s.genera == ("$Codex_Ent_Bacterial_Genus_Name;",)
    assert s.sources == frozenset({"fss", "saa"}) and s.body_name == "Sol 1"


def test_auto_finds_are_one_row_per_event(crew, ring, friend):
    me = ring.for_commander("F1")
    crew.append_own([(me, bio_find(at=ts(1)))])
    crew.insert_received(*sealed(friend, bio_find("F9", at=ts(2)), 1))
    finds = crew.finds()
    assert [f.author for f in finds] == [friend.author, me.author]
    assert finds[0].detail == {"count": 2} and finds[0].mode == "auto" and finds[0].find_id is None


def test_note_revisions_highest_seq_wins_and_delete_hides(crew, ring):
    me = ring.for_commander("F1")
    note_id = ev.uuid7()
    crew.append_own([(me, note(note_id=note_id, text="first"))])
    crew.append_own([(me, note(note_id=note_id, text="second", at=ts(1)))])
    assert [n.text for n in crew.notes(ADDR)] == ["second"]
    crew.append_own([(me, note(note_id=note_id, text="second", deleted=True, at=ts(2)))])
    assert crew.notes() == []
    assert crew.note(me.author, note_id).deleted is True


def test_a_note_revision_by_another_author_is_ignored(crew, ring, friend):
    me = ring.for_commander("F1")
    note_id = ev.uuid7()
    crew.append_own([(me, note(note_id=note_id, text="mine"))])
    crew.insert_received(*sealed(friend, note("F9", note_id=note_id, text="overwritten", deleted=True), 99))
    assert crew.note(me.author, note_id).text == "mine"
    assert crew.note(me.author, note_id).deleted is False


def test_manual_find_revisions(crew, ring):
    me = ring.for_commander("F1")
    find_id = ev.uuid7()
    crew.append_own([(me, manual_find(find_id=find_id, note_text="wreck"))])
    crew.append_own([(me, manual_find(find_id=find_id, note_text="wreck, two pods", at=ts(1)))])
    [f] = crew.finds()
    assert (f.mode, f.find_id, f.note) == ("manual", find_id, "wreck, two pods")
    crew.append_own([(me, manual_find(find_id=find_id, deleted=True, at=ts(2)))])
    assert crew.finds() == []
    assert crew.manual_find(me.author, find_id).deleted is True


def _mixed_envelopes(tmp_path):
    a = KeyRing(tmp_path / "a").for_commander("FA")
    b = KeyRing(tmp_path / "b").for_commander("FB")
    note_id = ev.uuid7()
    drafts_a = [visit("FA", at=ts(1)), honk("FA", at=ts(2), bodies=2),
                body("FA", at=ts(3), body_id=0, star_type="G"), body("FA", at=ts(4), body_id=1, tier="basic"),
                signals("FA", at=ts(5), counts={"biological": 1}), bio_find("FA", at=ts(5)),
                note("FA", at=ts(6), note_id=note_id, text="one"),
                note("FA", at=ts(7), note_id=note_id, text="two")]
    drafts_b = [visit("FB", at=ts(2), system="SOL", coords=(5.0, 5.0, 5.0)), body("FB", at=ts(3), body_id=1),
                signals("FB", at=ts(4), source="saa", counts={"biological": 2}, genera=["$G;"]),
                all_bodies("FB", at=ts(8), count=2), bio_find("FB", at=ts(4))]
    envelopes = [sealed(a, d, i + 1) for i, d in enumerate(drafts_a)]
    envelopes += [sealed(b, d, i + 1) for i, d in enumerate(drafts_b)]
    envelopes.append(sealed(b, drafts_b[0], 99))
    return envelopes


def test_the_merge_is_independent_of_arrival_order(tmp_path):
    envelopes = _mixed_envelopes(tmp_path)
    dumps = []
    for attempt in range(5):
        shuffled = envelopes[:]
        random.Random(attempt).shuffle(shuffled)
        s = Store(tmp_path / f"order{attempt}.db")
        try:
            crew_store = CrewStore(s)
            for data, signature in shuffled:
                crew_store.insert_received(data, signature)
            dumps.append(dump(s))
        finally:
            s.close()
    assert all(d == dumps[0] for d in dumps)
    assert dumps[0]["crew_explored"]


def test_rebuild_equals_the_incremental_result(tmp_path, crew, store, ring):
    for data, signature in _mixed_envelopes(tmp_path):
        crew.insert_received(data, signature)
    crew.append_own([(ring.for_commander("F1"), visit(at=ts(30)))])
    before = dump(store)
    crew.rebuild_projections()
    assert dump(store) == before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_store.py -q`
Expected: the new tests fail with `AttributeError: 'CrewStore' object has no attribute 'system'` (and similar).

- [ ] **Step 3: Write the projectors**

In `edmrn/ext/crew_store.py`, add `import json` and `from dataclasses import dataclass` to the
imports and change `from typing import Callable` to `from typing import Callable, Optional`. Replace
the line `_PROJECTORS: dict[str, Projector] = {}` with the block below. In `_project`, replace the
last three lines (`projector = …` to `projector(conn, env)`) with `_PROJECTORS[kind](conn, env)`.

```python
def _order(env: dict) -> str:
    return f"{env['created_at']}|{env['author']}|{env['event_id']}"


def _name_system(conn, address: str, name: Optional[str], order: str) -> None:
    if name is None:
        return
    conn.execute(
        'INSERT INTO crew_systems (address, name, name_key) VALUES (?, ?, ?) '
        'ON CONFLICT (address) DO UPDATE SET name = excluded.name, name_key = excluded.name_key '
        'WHERE crew_systems.name_key IS NULL OR excluded.name_key < crew_systems.name_key',
        (address, name, order))


def _place_system(conn, address: str, coords: Optional[list], order: str) -> None:
    if coords is None:
        return
    conn.execute(
        'INSERT INTO crew_systems (address, x, y, z, coords_key) VALUES (?, ?, ?, ?, ?) '
        'ON CONFLICT (address) DO UPDATE SET x = excluded.x, y = excluded.y, z = excluded.z, '
        'coords_key = excluded.coords_key '
        'WHERE crew_systems.coords_key IS NULL OR excluded.coords_key < crew_systems.coords_key',
        (address, float(coords[0]), float(coords[1]), float(coords[2]), order))


def _count_system(conn, address: str, count: int, order: str) -> None:
    conn.execute(
        'INSERT INTO crew_systems (address, body_count, body_count_key) VALUES (?, ?, ?) '
        'ON CONFLICT (address) DO UPDATE SET body_count = excluded.body_count, '
        'body_count_key = excluded.body_count_key '
        'WHERE crew_systems.body_count_key IS NULL '
        'OR excluded.body_count_key > crew_systems.body_count_key',
        (address, count, order))


def _refresh_explored(conn, author: str, address: str) -> None:
    """C7: all_bodies, or distinct scanned bodies reaching the latest honk's body count."""
    explored = conn.execute('SELECT 1 FROM crew_all_bodies WHERE author = ? AND address = ?',
                            (author, address)).fetchone() is not None
    if not explored:
        honk = conn.execute('SELECT body_count FROM crew_honks WHERE author = ? AND address = ?',
                            (author, address)).fetchone()
        if honk is not None and honk[0] >= 1:
            scanned = conn.execute(
                'SELECT COUNT(DISTINCT body_id) FROM crew_body_reports WHERE author = ? AND address = ?',
                (author, address)).fetchone()[0]
            explored = scanned >= honk[0]
    if explored:
        conn.execute('INSERT OR IGNORE INTO crew_explored (author, address) VALUES (?, ?)',
                     (author, address))
    else:
        conn.execute('DELETE FROM crew_explored WHERE author = ? AND address = ?', (author, address))


def _project_visit(conn, env: dict) -> None:
    p, order = env['payload'], _order(env)
    _name_system(conn, p['address'], p['system'], order)
    _place_system(conn, p['address'], p['coords'], order)
    conn.execute(
        'INSERT INTO crew_visits (author, address, first_at, last_at, visit_count) '
        'VALUES (?, ?, ?, ?, 1) ON CONFLICT (author, address) DO UPDATE SET '
        'first_at = min(first_at, excluded.first_at), last_at = max(last_at, excluded.last_at), '
        'visit_count = visit_count + 1',
        (env['author'], p['address'], env['created_at'], env['created_at']))


def _project_honk(conn, env: dict) -> None:
    p, order = env['payload'], _order(env)
    _name_system(conn, p['address'], p['system'], order)
    _count_system(conn, p['address'], p['body_count'], order)
    conn.execute(
        'INSERT INTO crew_honks (author, address, body_count, honk_key) VALUES (?, ?, ?, ?) '
        'ON CONFLICT (author, address) DO UPDATE SET body_count = excluded.body_count, '
        'honk_key = excluded.honk_key WHERE excluded.honk_key > crew_honks.honk_key',
        (env['author'], p['address'], p['body_count'], order))
    _refresh_explored(conn, env['author'], p['address'])


def _project_all_bodies(conn, env: dict) -> None:
    p, order = env['payload'], _order(env)
    _name_system(conn, p['address'], p['system'], order)
    _count_system(conn, p['address'], p['count'], order)
    conn.execute('INSERT OR IGNORE INTO crew_all_bodies (author, address) VALUES (?, ?)',
                 (env['author'], p['address']))
    _refresh_explored(conn, env['author'], p['address'])


def _flag(value: Optional[bool]) -> Optional[int]:
    return None if value is None else int(value)


def _project_body(conn, env: dict) -> None:
    p = env['payload']
    _name_system(conn, p['address'], p['system'], _order(env))
    conn.execute(
        'INSERT INTO crew_body_reports (event_id, author, created_at, address, body_id, tier, name, '
        'star_type, planet_class, moon, landable, terraform, was_discovered, was_mapped, scan_type) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (env['event_id'], env['author'], env['created_at'], p['address'], p['body_id'], p['tier'],
         p['name'], p['star_type'], p['planet_class'], int(p['moon']), _flag(p['landable']),
         p['terraform'], int(p['was_discovered']), int(p['was_mapped']), p['scan_type']))
    _refresh_explored(conn, env['author'], p['address'])


def _project_signals(conn, env: dict) -> None:
    p = env['payload']
    _name_system(conn, p['address'], p['system'], _order(env))
    conn.executemany(
        'INSERT INTO crew_signals (address, body_id, type, signal_count) VALUES (?, ?, ?, ?) '
        'ON CONFLICT (address, body_id, type) DO UPDATE SET '
        'signal_count = max(signal_count, excluded.signal_count)',
        [(p['address'], p['body_id'], kind, count) for kind, count in sorted(p['counts'].items())])
    conn.executemany('INSERT OR IGNORE INTO crew_genera (address, body_id, genus) VALUES (?, ?, ?)',
                     [(p['address'], p['body_id'], genus) for genus in p['genera']])
    conn.execute(
        'INSERT INTO crew_signal_sources (address, body_id, source, body_name) VALUES (?, ?, ?, ?) '
        'ON CONFLICT (address, body_id, source) DO UPDATE SET '
        'body_name = min(body_name, excluded.body_name)',
        (p['address'], p['body_id'], p['source'], p['body_name']))


def _project_find(conn, env: dict) -> None:
    p = env['payload']
    if p['mode'] == 'auto':
        conn.execute(
            'INSERT INTO crew_finds (event_id, author, created_at, find_kind, system, address, '
            'body_name, body_id, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (env['event_id'], env['author'], env['created_at'], p['find_kind'], p['system'],
             p['address'], p['body_name'], p['body_id'],
             ev.canonical_bytes(p['detail']).decode('utf-8')))
        return
    conn.execute(
        'INSERT INTO crew_manual_finds (author, find_id, seq, event_id, created_at, find_kind, '
        'system, address, body_name, body_id, note, deleted) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
        'ON CONFLICT (author, find_id) DO UPDATE SET seq = excluded.seq, '
        'event_id = excluded.event_id, created_at = excluded.created_at, '
        'find_kind = excluded.find_kind, system = excluded.system, address = excluded.address, '
        'body_name = excluded.body_name, body_id = excluded.body_id, note = excluded.note, '
        'deleted = excluded.deleted WHERE excluded.seq > crew_manual_finds.seq',
        (env['author'], p['find_id'], env['seq'], env['event_id'], env['created_at'],
         p['find_kind'], p['system'], p['address'], p['body_name'], p['body_id'], p['note'],
         int(p['deleted'])))


def _project_note(conn, env: dict) -> None:
    p = env['payload']
    conn.execute(
        'INSERT INTO crew_notes (author, note_id, seq, event_id, created_at, system, address, text, '
        'deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) '
        'ON CONFLICT (author, note_id) DO UPDATE SET seq = excluded.seq, '
        'event_id = excluded.event_id, created_at = excluded.created_at, system = excluded.system, '
        'address = excluded.address, text = excluded.text, deleted = excluded.deleted '
        'WHERE excluded.seq > crew_notes.seq',
        (env['author'], p['note_id'], env['seq'], env['event_id'], env['created_at'], p['system'],
         p['address'], p['text'], int(p['deleted'])))


_PROJECTORS: dict[str, Projector] = {
    'visit': _project_visit, 'honk': _project_honk, 'all_bodies': _project_all_bodies,
    'body': _project_body, 'signals': _project_signals, 'find': _project_find,
    'note': _project_note,
}
```

- [ ] **Step 4: Write the row types, rebuild and queries**

Add the row dataclasses and helpers after `_PROJECTORS`:

```python
@dataclass(frozen=True)
class SystemRow:
    address: str
    name: Optional[str]
    coords: Optional[tuple[float, float, float]]
    body_count: Optional[int]


@dataclass(frozen=True)
class VisitRow:
    address: str
    first_at: str
    last_at: str
    count: int


@dataclass(frozen=True)
class BodyRow:
    body_id: int
    name: str
    tier: str
    star_type: Optional[str]
    planet_class: Optional[str]
    moon: bool
    landable: Optional[bool]
    terraform: Optional[str]
    scan_type: str
    author: str


@dataclass(frozen=True)
class BodyReport:
    author: str
    body_id: int
    tier: str
    was_discovered: bool
    was_mapped: bool
    created_at: str


@dataclass(frozen=True)
class BodySignals:
    body_id: int
    body_name: Optional[str]
    counts: dict
    genera: tuple
    sources: frozenset


@dataclass(frozen=True)
class FindRow:
    event_id: str
    author: str
    created_at: str
    mode: str
    find_id: Optional[str]
    find_kind: str
    system: Optional[str]
    address: str
    body_name: Optional[str]
    body_id: Optional[int]
    detail: dict
    note: str
    deleted: bool


@dataclass(frozen=True)
class NoteRow:
    note_id: str
    author: str
    seq: int
    created_at: str
    system: Optional[str]
    address: str
    text: str
    deleted: bool


def _optional_bool(value: Optional[int]) -> Optional[bool]:
    return None if value is None else bool(value)


_AUTO_FINDS_SELECT = (
    "SELECT event_id, author, created_at, 'auto', NULL, find_kind, system, address, body_name, "
    "body_id, detail, '', 0 FROM crew_finds")
_MANUAL_FINDS_SELECT = (
    "SELECT event_id, author, created_at, 'manual', find_id, find_kind, system, address, body_name, "
    "body_id, '{}', note, deleted FROM crew_manual_finds")
_NOTE_SELECT = ('SELECT note_id, author, seq, created_at, system, address, text, deleted '
                'FROM crew_notes')


def _find_row(row: tuple) -> FindRow:
    return FindRow(*row[:10], detail=json.loads(row[10]), note=row[11], deleted=bool(row[12]))


def _note_row(row: tuple) -> NoteRow:
    return NoteRow(*row[:7], deleted=bool(row[7]))
```

Then add these methods to `class CrewStore`, after `insert_received`:

```python
    def rebuild_projections(self) -> None:
        """Re-derive every projection from the log in (created_at, author, seq) order (C8)."""
        with self.store.transaction() as conn:
            for table in PROJECTION_TABLES:
                conn.execute(f'DELETE FROM {table}')
            rows = conn.execute(
                'SELECT envelope FROM crew_log ORDER BY created_at, author, seq').fetchall()
            for (data,) in rows:
                try:
                    env = ev.parse_envelope(data)
                except ev.EnvelopeError as e:
                    logger.warning(f'Crew log row skipped in rebuild: {e}')
                    continue
                _project(conn, env)

    def system(self, address: str) -> Optional[SystemRow]:
        with self.store.read() as conn:
            row = conn.execute('SELECT address, name, x, y, z, body_count FROM crew_systems '
                               'WHERE address = ?', (address,)).fetchone()
        if row is None:
            return None
        coords = None if row[2] is None else (row[2], row[3], row[4])
        return SystemRow(row[0], row[1], coords, row[5])

    def explored(self, author: Optional[str] = None) -> set[str]:
        with self.store.read() as conn:
            if author is None:
                rows = conn.execute('SELECT DISTINCT address FROM crew_explored').fetchall()
            else:
                rows = conn.execute('SELECT address FROM crew_explored WHERE author = ?',
                                    (author,)).fetchall()
        return {row[0] for row in rows}

    def visits(self, author: str) -> list[VisitRow]:
        with self.store.read() as conn:
            rows = conn.execute('SELECT address, first_at, last_at, visit_count FROM crew_visits '
                                'WHERE author = ? ORDER BY last_at DESC', (author,)).fetchall()
        return [VisitRow(*row) for row in rows]

    def bodies(self, address: str) -> list[BodyRow]:
        with self.store.read() as conn:
            rows = conn.execute(
                'SELECT body_id, name, tier, star_type, planet_class, moon, landable, terraform, '
                'scan_type, author FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY body_id '
                "ORDER BY tier = 'full' DESC, created_at, author, event_id) AS rank "
                'FROM crew_body_reports WHERE address = ?) WHERE rank = 1 ORDER BY body_id',
                (address,)).fetchall()
        return [BodyRow(r[0], r[1], r[2], r[3], r[4], bool(r[5]), _optional_bool(r[6]), r[7], r[8],
                        r[9]) for r in rows]

    def body_reports(self, address: str) -> list[BodyReport]:
        with self.store.read() as conn:
            rows = conn.execute(
                'SELECT author, body_id, tier, was_discovered, was_mapped, created_at '
                'FROM crew_body_reports WHERE address = ? ORDER BY body_id, created_at, author',
                (address,)).fetchall()
        return [BodyReport(r[0], r[1], r[2], bool(r[3]), bool(r[4]), r[5]) for r in rows]

    def signals(self, address: str) -> dict[int, BodySignals]:
        with self.store.read() as conn:
            counts = conn.execute('SELECT body_id, type, signal_count FROM crew_signals '
                                  'WHERE address = ?', (address,)).fetchall()
            genera = conn.execute('SELECT body_id, genus FROM crew_genera WHERE address = ? '
                                  'ORDER BY genus', (address,)).fetchall()
            sources = conn.execute('SELECT body_id, source, body_name FROM crew_signal_sources '
                                   'WHERE address = ?', (address,)).fetchall()
        bodies: dict[int, dict] = {}

        def entry(body_id: int) -> dict:
            return bodies.setdefault(body_id, {'name': None, 'counts': {}, 'genera': [],
                                               'sources': set()})

        for body_id, source, name in sources:
            e = entry(body_id)
            e['sources'].add(source)
            e['name'] = name if e['name'] is None else min(e['name'], name)
        for body_id, kind, count in counts:
            entry(body_id)['counts'][kind] = count
        for body_id, genus in genera:
            entry(body_id)['genera'].append(genus)
        return {body_id: BodySignals(body_id, e['name'], e['counts'], tuple(e['genera']),
                                     frozenset(e['sources']))
                for body_id, e in bodies.items()}

    def finds(self) -> list[FindRow]:
        with self.store.read() as conn:
            rows = conn.execute(f'{_AUTO_FINDS_SELECT} UNION ALL {_MANUAL_FINDS_SELECT} '
                                'WHERE deleted = 0 ORDER BY 3 DESC, 1 DESC').fetchall()
        return [_find_row(row) for row in rows]

    def manual_find(self, author: str, find_id: str) -> Optional[FindRow]:
        with self.store.read() as conn:
            row = conn.execute(f'{_MANUAL_FINDS_SELECT} WHERE author = ? AND find_id = ?',
                               (author, find_id)).fetchone()
        return None if row is None else _find_row(row)

    def notes(self, address: Optional[str] = None) -> list[NoteRow]:
        with self.store.read() as conn:
            if address is None:
                rows = conn.execute(f'{_NOTE_SELECT} WHERE deleted = 0 '
                                    'ORDER BY created_at DESC').fetchall()
            else:
                rows = conn.execute(f'{_NOTE_SELECT} WHERE deleted = 0 AND address = ? '
                                    'ORDER BY created_at DESC', (address,)).fetchall()
        return [_note_row(row) for row in rows]

    def note(self, author: str, note_id: str) -> Optional[NoteRow]:
        with self.store.read() as conn:
            row = conn.execute(f'{_NOTE_SELECT} WHERE author = ? AND note_id = ?',
                               (author, note_id)).fetchone()
        return None if row is None else _note_row(row)
```

The f-strings insert only module constants (`_AUTO_FINDS_SELECT`, `_MANUAL_FINDS_SELECT`,
`_NOTE_SELECT`, `PROJECTION_TABLES`). Every value goes in as a `?` parameter (Global
Constraints). In `finds()`, `ORDER BY 3 DESC, 1 DESC` orders the compound select by its third and
first columns (`created_at`, `event_id`), because a compound `ORDER BY` cannot name columns that
differ between its parts.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_store.py -q`
Expected: all pass.

- [ ] **Step 6: Mutation checks**

Make each change below on its own, run the tests, confirm the named test fails, and revert:
1. In `_name_system`, change `<` to `>`. Expected failure:
   `test_system_name_and_coords_are_the_earliest_and_body_count_the_latest`.
2. In `_project_signals`, change `max(` to `min(`. Expected failure:
   `test_signals_take_the_highest_count_and_the_union_of_genera`.
3. In `_project_note`, delete `WHERE excluded.seq > crew_notes.seq`. Expected failure:
   `test_the_merge_is_independent_of_arrival_order` (the notes' arrival order then decides).

- [ ] **Step 7: Run the whole suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`. Expected: all pass.

```bash
git add edmrn/ext/crew_store.py tests/test_crew_store.py
git commit -m "feat(crew): deterministic projections, rebuild and read queries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

# Phase 2 — capture

### Task 6: Journal mapper — commander, visits, honks, completion and bodies

**Files:**
- Create: `edmrn/ext/crew_capture.py`
- Test: `tests/test_crew_capture.py`

**Interfaces:**
- Consumes: Task 1's `stamp`; Task 2's `Draft`, `validate_payload`.
- Produces:
  - `@dataclass class MapperState(commander, commander_name, system, address, body_names)` with
    `copy()`; `body_names` maps `(address, body_id)` to a name for the current system only;
  - `commander_key(fid, name) -> str | None` (the FID, or `"name:" + 32 hex` of SHA-256 of the name);
  - `class JournalMapper(state: MapperState | None = None)` with `map(event) -> list[Draft]` (never
    raises), `snapshot() -> MapperState`, and attribute `state`.

Mapping rules (C5, C6 valuable bodies, C11). Every draft carries the active commander's key and
`created_at` = the event's `timestamp`:
- `LoadGame` (`Commander`, `FID`) and `Commander` (`Name`, `FID`) set the commander. A different key
  resets the whole state.
- Before the first commander is known, every event is dropped, as is an event without a valid
  timestamp.
- `Location` sets system and address. It is not a visit.
- `FSDJump` and `CarrierJump` set system and address and give a `visit`.
- Entering a different address forgets the body names.
- `FSSDiscoveryScan` gives a `honk`; `FSSAllBodiesFound` gives `all_bodies`.
- `Scan` with `StarType` or `PlanetClass` gives a `body`; `StarType` wins if both are present.
  - Belt clusters and rings (neither key), and `ScanType`s outside `SCAN_TYPES`, give nothing.
  - `moon` is true when the first `Parents` entry has the key `Planet`.
  - `landable` and `terraform` are null unless the event carries a bool or a string.
- A full-tier planet whose class is `Earthlike body`, `Water world` or `Ammonia world`, or whose
  terraform state is `Terraformable`, also gives an auto find `valuable_body`. Its detail is
  `{planet_class, terraform}`, with terraform `""` when absent.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_crew_capture.py`:

```python
import pytest

from edmrn.ext import crew_events as ev
from edmrn.ext.crew_capture import JournalMapper, MapperState, commander_key
from journal_helpers import event

SOL = 10477373803
AUTHOR = "A" * 43
LOAD = event("LoadGame", "2026-10-12T09:59:00Z", Commander="Jameson", FID="F100")


def jump(name="FSDJump", system="Sol", address=SOL, ts="2026-10-12T10:00:00Z", pos=(0, 0, 0)):
    return event(name, ts, StarSystem=system, SystemAddress=address, StarPos=list(pos))


def scan(body_id, body_name, scan_type="Detailed", ts="2026-10-12T10:01:00Z", address=SOL, **fields):
    return event("Scan", ts, ScanType=scan_type, BodyName=body_name, BodyID=body_id, StarSystem="Sol",
                 SystemAddress=address, DistanceFromArrivalLS=10.0, WasDiscovered=True, WasMapped=False,
                 **fields)


def planet(body_id=3, body_name="Sol 3", planet_class="Rocky body", scan_type="Detailed", landable=True,
           terraform="", parents=({"Star": 0},), **kw):
    fields = {"PlanetClass": planet_class, "Parents": [dict(p) for p in parents]}
    if scan_type != "Basic":
        fields.update(Landable=landable, TerraformState=terraform)
    return scan(body_id, body_name, scan_type, **fields, **kw)


def run(*events, mapper=None):
    """Map events through one mapper; every draft must pass its validator."""
    mapper = mapper or JournalMapper()
    drafts = []
    for e in events:
        drafts.extend(mapper.map(e))
    for d in drafts:
        ev.validate_payload(d.kind, 1, d.payload)
    return drafts


def test_events_before_the_first_commander_are_dropped():
    assert run(jump()) == []


def test_jumps_are_visits_and_location_is_not():
    drafts = run(LOAD, event("Location", StarSystem="Sol", SystemAddress=SOL), jump(),
                 jump("CarrierJump", "Alpha", 42, "2026-10-12T10:05:00Z", (1.5, 2, 3)))
    assert [d.kind for d in drafts] == ["visit", "visit"]
    assert drafts[0].payload == {"commander": "Jameson", "system": "Sol", "address": str(SOL),
                                 "coords": [0.0, 0.0, 0.0]}
    assert drafts[1].payload["address"] == "42" and drafts[1].payload["coords"] == [1.5, 2.0, 3.0]
    assert all(d.commander == "F100" and d.event_id is None for d in drafts)
    assert drafts[0].created_at == "2026-10-12T10:00:00Z"


def test_addresses_above_2_53_travel_as_strings():
    [d] = run(LOAD, jump(address=2**60))
    assert d.payload["address"] == str(2**60)


def test_honk_and_all_bodies():
    drafts = run(LOAD,
                 event("FSSDiscoveryScan", Progress=0.3, BodyCount=12, NonBodyCount=30, SystemName="Sol",
                       SystemAddress=SOL),
                 event("FSSAllBodiesFound", SystemName="Sol", SystemAddress=SOL, Count=12))
    assert [(d.kind, d.payload) for d in drafts] == [
        ("honk", {"system": "Sol", "address": str(SOL), "body_count": 12, "non_body_count": 30}),
        ("all_bodies", {"system": "Sol", "address": str(SOL), "count": 12})]


def test_star_and_planet_scans_are_bodies_with_tiers():
    drafts = run(LOAD, jump(), scan(0, "Sol", "AutoScan", StarType="G"), planet(),
                 planet(4, "Sol 3 a", parents=({"Planet": 3}, {"Star": 0})),
                 planet(5, "Sol 5", scan_type="Basic"))
    bodies = [d.payload for d in drafts if d.kind == "body"]
    assert [(b["body_id"], b["tier"]) for b in bodies] == [(0, "full"), (3, "full"), (4, "full"), (5, "basic")]
    star, rocky, moon, basic = bodies
    assert star["star_type"] == "G" and star["planet_class"] is None and star["landable"] is None
    assert rocky["planet_class"] == "Rocky body" and rocky["landable"] is True and rocky["moon"] is False
    assert moon["moon"] is True
    assert basic["landable"] is None and basic["terraform"] is None and basic["scan_type"] == "Basic"
    assert rocky["system"] == "Sol" and rocky["was_discovered"] is True and rocky["was_mapped"] is False


def test_belt_clusters_rings_and_unknown_scan_types_are_skipped():
    drafts = run(LOAD, jump(),
                 scan(6, "Sol A Belt Cluster 1", "AutoScan", Parents=[{"Ring": 5}]),
                 scan(7, "Sol 3 A Ring", "Detailed"),
                 planet(8, "Sol 8", scan_type="NavBeacon"))
    assert [d.kind for d in drafts] == ["visit"]


@pytest.mark.parametrize("planet_class,terraform,expected", [
    ("Earthlike body", "Terraformed", True),
    ("Water world", "", True),
    ("Ammonia world", "", True),
    ("High metal content body", "Terraformable", True),
    ("Rocky body", "", False),
    ("Icy body", "Terraformed", False),
])
def test_valuable_bodies(planet_class, terraform, expected):
    drafts = run(LOAD, jump(), planet(planet_class=planet_class, terraform=terraform))
    finds = [d for d in drafts if d.kind == "find"]
    assert bool(finds) is expected
    if expected:
        f = finds[0].payload
        assert (f["mode"], f["find_kind"], f["body_id"], f["body_name"], f["system"]) == (
            "auto", "valuable_body", 3, "Sol 3", "Sol")
        assert f["detail"] == {"planet_class": planet_class, "terraform": terraform}


def test_a_basic_scan_is_never_a_valuable_find():
    drafts = run(LOAD, jump(), planet(planet_class="Water world", scan_type="Basic"))
    assert [d.kind for d in drafts] == ["visit", "body"]


def test_two_commanders_in_one_sequence_are_attributed_to_whoever_was_active():
    drafts = run(LOAD, jump(), event("Commander", Name="Other", FID="F200"),
                 jump(ts="2026-10-12T11:00:00Z"),
                 event("LoadGame", Commander="Jameson", FID="F100"), jump(ts="2026-10-12T12:00:00Z"))
    assert [d.commander for d in drafts] == ["F100", "F200", "F100"]
    assert [d.payload["commander"] for d in drafts] == ["Jameson", "Other", "Jameson"]


def test_a_commander_switch_forgets_the_system_and_body_names():
    mapper = JournalMapper()
    run(LOAD, jump(), planet(), mapper=mapper)
    assert mapper.state.body_names
    run(event("LoadGame", Commander="Other", FID="F200"), mapper=mapper)
    assert mapper.state.system is None and mapper.state.commander == "F200"
    assert mapper.state.body_names == {}


def test_journals_without_fid_use_a_name_hash():
    [d] = run(event("LoadGame", Commander="Old Timer"), jump())
    assert d.commander == commander_key(None, "Old Timer") and d.commander.startswith("name:")
    assert "Old Timer" not in d.commander


def test_a_snapshot_continues_the_commander_and_system():
    mapper = JournalMapper()
    run(LOAD, jump(), mapper=mapper)
    copy = JournalMapper(mapper.snapshot())
    [d] = run(planet(), mapper=copy)
    assert d.commander == "F100" and d.payload["system"] == "Sol"
    copy.state.body_names[("x", 1)] = "y"
    assert ("x", 1) not in mapper.state.body_names


def test_replaying_the_same_journal_gives_the_same_ids():
    events = [LOAD, jump(), planet(planet_class="Water world")]
    first = [d.resolve_id(AUTHOR) for d in run(*events)]
    second = [d.resolve_id(AUTHOR) for d in run(*events)]
    assert first == second and len(set(first)) == len(first) == 3


@pytest.mark.parametrize("bad", [
    "not a dict",
    None,
    event("FSDJump", ts="yesterday", StarSystem="Sol", SystemAddress=SOL),
    {"event": "FSDJump", "StarSystem": "Sol", "SystemAddress": SOL},
    event("FSDJump", StarSystem="Sol", SystemAddress=True),
    event("FSDJump", StarSystem="Sol", SystemAddress=-5),
    event("FSDJump", StarSystem="", SystemAddress=SOL),
    event("FSSDiscoveryScan", SystemName="Sol", SystemAddress=SOL, BodyCount="12", NonBodyCount=0),
    event("Scan", ScanType="Detailed", BodyName="X", BodyID="3", SystemAddress=SOL, PlanetClass="Rocky body"),
])
def test_malformed_events_give_nothing_and_never_raise(bad):
    assert run(LOAD, bad) == []


def test_garbage_parents_mean_not_a_moon():
    [d] = run(LOAD, event("Scan", ScanType="Detailed", BodyName="X", BodyID=3, SystemAddress=SOL,
                          PlanetClass="Rocky body", Parents="garbage", WasDiscovered=True, WasMapped=False))
    assert d.payload["moon"] is False and d.payload["system"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_capture.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'edmrn.ext.crew_capture'`.

- [ ] **Step 3: Write the implementation**

Create `edmrn/ext/crew_capture.py`:

```python
"""Journal events to crew drafts (Spec A C5, C6, C11).

The mapper is a state machine over journal dicts: no I/O, no Tk, no threads. It never writes; its
drafts go to the writer in crew.py. Field shapes: knowledge/exploration-events.md in the hub.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from edmrn.ext import crew_events as ev
from edmrn.logger import get_logger

logger = get_logger('CrewCapture')

VALUABLE_CLASSES = ('Earthlike body', 'Water world', 'Ammonia world')


@dataclass
class MapperState:
    commander: Optional[str] = None
    commander_name: Optional[str] = None
    system: Optional[str] = None
    address: Optional[str] = None
    body_names: dict = field(default_factory=dict)

    def copy(self) -> 'MapperState':
        return MapperState(self.commander, self.commander_name, self.system, self.address,
                           dict(self.body_names))


def _str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _address(value: Any) -> Optional[str]:
    number = _int(value)
    return str(number) if number is not None and 0 <= number < 2**64 else None


def _coords(value: Any) -> Optional[list]:
    if (isinstance(value, list) and len(value) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        try:
            coords = [float(v) for v in value]
        except OverflowError:
            return None
        if all(math.isfinite(v) for v in coords):
            return coords
    return None


def commander_key(fid: Any, name: Any) -> Optional[str]:
    """Local key of a commander: the FID, or a hash of the name for journals without one (C11)."""
    fid = _str(fid)
    if fid is not None:
        return fid
    name = _str(name)
    if name is None:
        return None
    return 'name:' + hashlib.sha256(name.encode('utf-8', 'surrogatepass')).hexdigest()[:32]


class JournalMapper:
    def __init__(self, state: Optional[MapperState] = None) -> None:
        self.state = state.copy() if state is not None else MapperState()

    def snapshot(self) -> MapperState:
        return self.state.copy()

    def map(self, event: Any) -> list[ev.Draft]:
        if not isinstance(event, dict):
            return []
        try:
            return self._map(event)
        except Exception as e:  # a malformed event must never stop capture
            logger.error(f"Crew mapping skipped {event.get('event')!r}: {e}")
            return []

    def _map(self, event: dict) -> list[ev.Draft]:
        name = event.get('event')
        if name in ('LoadGame', 'Commander'):
            self._set_commander(event)
            return []
        if self.state.commander is None:
            return []
        created_at = ev.stamp(event.get('timestamp'))
        if created_at is None:
            return []
        if name == 'Location':
            self._enter(event.get('StarSystem'), event.get('SystemAddress'))
            return []
        if name in ('FSDJump', 'CarrierJump'):
            return self._visit(event, created_at)
        if name == 'FSSDiscoveryScan':
            return self._honk(event, created_at)
        if name == 'FSSAllBodiesFound':
            return self._all_bodies(event, created_at)
        if name == 'Scan':
            return self._scan(event, created_at)
        return []

    # --- state ---------------------------------------------------------------------------

    def _set_commander(self, event: dict) -> None:
        display = _str(event.get('Commander' if event.get('event') == 'LoadGame' else 'Name'))
        key = commander_key(event.get('FID'), display)
        if key is None:
            return
        if key != self.state.commander:
            self.state = MapperState(commander=key, commander_name=display)
        elif display is not None:
            self.state.commander_name = display

    def _enter(self, system: Any, address: Any) -> bool:
        system, address = _str(system), _address(address)
        if system is None or address is None:
            return False
        if address != self.state.address:
            self.state.body_names = {}
        self.state.system, self.state.address = system, address
        return True

    def _system_for(self, address: str) -> Optional[str]:
        return self.state.system if address == self.state.address else None

    def _draft(self, kind: str, created_at: str, payload: dict) -> ev.Draft:
        return ev.Draft(kind=kind, commander=self.state.commander, created_at=created_at,
                        payload=payload)

    def _auto_find(self, created_at: str, find_kind: str, system: Optional[str], address: str,
                   body_name: Optional[str], body_id: Optional[int], detail: dict) -> ev.Draft:
        return self._draft('find', created_at, {
            'mode': 'auto', 'find_kind': find_kind, 'system': system, 'address': address,
            'body_name': body_name, 'body_id': body_id, 'detail': detail})

    # --- events --------------------------------------------------------------------------

    def _visit(self, event: dict, created_at: str) -> list[ev.Draft]:
        if not self._enter(event.get('StarSystem'), event.get('SystemAddress')):
            return []
        s = self.state
        return [self._draft('visit', created_at, {
            'commander': s.commander_name, 'system': s.system, 'address': s.address,
            'coords': _coords(event.get('StarPos'))})]

    def _honk(self, event: dict, created_at: str) -> list[ev.Draft]:
        system, address = _str(event.get('SystemName')), _address(event.get('SystemAddress'))
        bodies, non_bodies = _int(event.get('BodyCount')), _int(event.get('NonBodyCount'))
        if system is None or address is None or bodies is None or non_bodies is None:
            return []
        return [self._draft('honk', created_at, {
            'system': system, 'address': address, 'body_count': bodies,
            'non_body_count': non_bodies})]

    def _all_bodies(self, event: dict, created_at: str) -> list[ev.Draft]:
        system, address = _str(event.get('SystemName')), _address(event.get('SystemAddress'))
        count = _int(event.get('Count'))
        if system is None or address is None or count is None:
            return []
        return [self._draft('all_bodies', created_at,
                            {'system': system, 'address': address, 'count': count})]

    def _scan(self, event: dict, created_at: str) -> list[ev.Draft]:
        star_type, planet_class = _str(event.get('StarType')), _str(event.get('PlanetClass'))
        scan_type = event.get('ScanType')
        if (star_type is None and planet_class is None) or scan_type not in ev.SCAN_TYPES:
            return []
        address, body_id = _address(event.get('SystemAddress')), _int(event.get('BodyID'))
        name = _str(event.get('BodyName'))
        if address is None or body_id is None or name is None:
            return []
        if star_type is not None:
            planet_class = None
        tier = 'basic' if scan_type == 'Basic' else 'full'
        parents = event.get('Parents')
        moon = (isinstance(parents, list) and bool(parents) and isinstance(parents[0], dict)
                and 'Planet' in parents[0])
        landable = event.get('Landable') if isinstance(event.get('Landable'), bool) else None
        terraform = event.get('TerraformState') if isinstance(event.get('TerraformState'), str) else None
        if star_type is not None:
            landable, terraform = None, None
        system = _str(event.get('StarSystem')) or self._system_for(address)
        if address == self.state.address:
            self.state.body_names[(address, body_id)] = name
        drafts = [self._draft('body', created_at, {
            'system': system, 'address': address, 'body_id': body_id, 'name': name, 'tier': tier,
            'star_type': star_type, 'planet_class': planet_class, 'moon': moon,
            'landable': landable, 'terraform': terraform,
            'was_discovered': event.get('WasDiscovered') is True,
            'was_mapped': event.get('WasMapped') is True, 'scan_type': scan_type})]
        if tier == 'full' and planet_class is not None and (
                planet_class in VALUABLE_CLASSES or terraform == 'Terraformable'):
            drafts.append(self._auto_find(created_at, 'valuable_body', system, address, name, body_id,
                                          {'planet_class': planet_class, 'terraform': terraform or ''}))
        return drafts
```

Note: `test_garbage_parents_mean_not_a_moon` sends a `Scan` without `StarSystem` while the mapper
knows no system, so `system` is `None`. That is the intended null case.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_capture.py -q`
Expected: all pass.

- [ ] **Step 5: Mutation check**

In `_set_commander`, temporarily replace the reset `self.state = MapperState(...)` with
`self.state.commander = key`. Run the tests. Expected:
`test_a_commander_switch_forgets_the_system_and_body_names` fails. Revert.

- [ ] **Step 6: Commit**

```bash
git add edmrn/ext/crew_capture.py tests/test_crew_capture.py
git commit -m "feat(crew): journal mapper for commanders, visits, honks, completion and bodies

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 7: Journal mapper — signals, phenomena, codex finds and the manual draft builders

**Files:**
- Modify: `edmrn/ext/crew_capture.py`
- Test: `tests/test_crew_capture.py` (append)

**Interfaces:**
- Consumes: Task 6's `JournalMapper` internals (`_map`, `_auto_find`, `_system_for`, `_draft`,
  `state.body_names`); Task 1's `uuid7`, `utc_now_stamp`; Task 2's `validate_payload`.
- Produces:
  - `SIGNAL_TYPE_MAP: dict[str, str]`;
  - `find_revision(commander, find_id, find_kind, system, address, body_name, body_id, note, deleted=False, *, created_at=None) -> Draft`;
  - `note_revision(commander, note_id, system, address, text, deleted=False, *, created_at=None) -> Draft`.

  Both builders validate (raising `PayloadError`) and give each revision a fresh UUIDv7
  `event_id`.

Rules (C5, C6):
- **Signal events.** `FSSBodySignals` gives `signals` with source `fss`; `SAASignalsFound` gives
  source `saa`.
  - A `Type` that does not start with `$` is a ring material, so the whole event is a ring event and
    gives nothing. So does a `BodyName` ending in ` Ring`.
  - Known types map through `SIGNAL_TYPE_MAP`. Any other `$…;` type counts as `other`. Counts of the
    same class add up.
  - `genera` (SAA only) are the distinct `Genuses[].Genus`, sorted.
  - A biological count ≥ 1 also gives an auto find `biological`. Its key is the body, so an FSS find
    and an SAA find for the same body share one id.
- **Phenomena.** `FSSSignalDiscovered` with `SignalType` `Codex` and a `SignalName` starting
  `$Fixed_Event_Life` gives an auto find `stellar_phenomenon`. Its key is the system, its `body_id`
  is null, and its detail is `{signal}`.
- **Codex.** `CodexEntry` whose `NearestDestination` starts with `$Fixed_Event_Life_`, or whose
  `IsNewEntry` is true, gives an auto find `codex`.
  - The body name comes from the earlier `Scan` of (`SystemAddress`, `BodyID`) in this system, and
    is null without one.
  - The detail is `{entry_id, name, category, subcategory, new_entry}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crew_capture.py`:

```python
from edmrn.ext.crew_capture import find_revision, note_revision

BIO = {"Type": "$SAA_SignalType_Biological;", "Type_Localised": "Biologisch", "Count": 2}


def body_signals(name="FSSBodySignals", body_id=3, body_name="Sol 3", ts="2026-10-12T10:02:00Z",
                 signals=(BIO,), genuses=None):
    fields = {"BodyName": body_name, "BodyID": body_id, "SystemAddress": SOL,
              "Signals": [dict(s) for s in signals]}
    if genuses is not None:
        fields["Genuses"] = [{"Genus": g, "Genus_Localised": g.strip("$;")} for g in genuses]
    return event(name, ts, **fields)


def test_fss_body_signals_give_counts_and_a_biological_find():
    drafts = run(LOAD, jump(), body_signals(signals=(
        BIO, {"Type": "$SAA_SignalType_Geological;", "Count": 1},
        {"Type": "$PlanetaryMiningLocation_Name;", "Type_Localised": "Planetary mining location", "Count": 3})))
    assert drafts[1].payload == {"system": "Sol", "address": str(SOL), "body_id": 3, "body_name": "Sol 3",
                                 "source": "fss", "counts": {"biological": 2, "geological": 1, "mining": 3},
                                 "genera": []}
    [find] = [d for d in drafts if d.kind == "find"]
    assert (find.payload["find_kind"], find.payload["body_name"], find.payload["detail"]) == (
        "biological", "Sol 3", {"count": 2})


def test_saa_signals_classify_types_and_carry_sorted_unique_genera():
    signals = [BIO] + [{"Type": f"$SAA_SignalType_{t};", "Count": 1}
                       for t in ("Human", "Guardian", "Thargoid", "PlanetAnomaly", "Other")]
    signals.append({"Type": "$SAA_SignalType_FutureThing;", "Count": 2})
    drafts = run(LOAD, jump(), body_signals("SAASignalsFound", signals=signals, genuses=[
        "$Codex_Ent_Stratum_Genus_Name;", "$Codex_Ent_Bacterial_Genus_Name;", "$Codex_Ent_Bacterial_Genus_Name;"]))
    payload = drafts[1].payload
    assert payload["source"] == "saa"
    assert payload["counts"] == {"biological": 2, "human": 1, "guardian": 1, "thargoid": 2, "other": 3}
    assert payload["genera"] == ["$Codex_Ent_Bacterial_Genus_Name;", "$Codex_Ent_Stratum_Genus_Name;"]


def test_biological_from_fss_then_saa_is_one_find():
    drafts = run(LOAD, jump(), body_signals(),
                 body_signals("SAASignalsFound", ts="2026-10-12T10:09:00Z",
                              genuses=["$Codex_Ent_Bacterial_Genus_Name;", "$Codex_Ent_Stratum_Genus_Name;"]))
    finds = [d for d in drafts if d.kind == "find"]
    assert len(finds) == 2
    assert finds[0].resolve_id(AUTHOR) == finds[1].resolve_id(AUTHOR)
    signal_ids = {d.resolve_id(AUTHOR) for d in drafts if d.kind == "signals"}
    assert len(signal_ids) == 2


def test_ring_signal_events_are_skipped():
    drafts = run(LOAD, jump(),
                 body_signals("SAASignalsFound", body_id=7, body_name="Sol 3 A Ring", genuses=[]),
                 body_signals("SAASignalsFound", body_id=9, body_name="Sol 9 B",
                              signals=({"Type": "Painite", "Count": 4},), genuses=[]))
    assert [d.kind for d in drafts] == ["visit"]


def test_signals_without_biology_give_no_find():
    drafts = run(LOAD, jump(), body_signals(signals=({"Type": "$SAA_SignalType_Geological;", "Count": 1},)))
    assert [d.kind for d in drafts] == ["visit", "signals"]


@pytest.mark.parametrize("signals", [
    "not a list",
    [{"Type": "$SAA_SignalType_Biological;", "Count": "2"}],
    [{"Type": 5, "Count": 2}],
    ["not a dict"],
])
def test_malformed_signal_lists_give_nothing(signals):
    bad = event("FSSBodySignals", BodyName="Sol 3", BodyID=3, SystemAddress=SOL, Signals=signals)
    assert run(LOAD, bad) == []


def nsp(ts, name="$Fixed_Event_Life_Cloud;", signal_type="Codex"):
    return event("FSSSignalDiscovered", ts, SystemAddress=SOL, SignalName=name,
                 SignalName_Localised="Bemerkenswerte Sternenphänomene", SignalType=signal_type)


def test_several_nsp_markers_in_one_system_give_one_find():
    drafts = run(LOAD, jump(), nsp("2026-10-12T10:00:10Z"), nsp("2026-10-12T10:00:11Z"),
                 nsp("2026-10-12T10:00:12Z", "$MULTIPLAYER_SCENARION_TITLE;", "ResourceExtraction"),
                 nsp("2026-10-12T10:00:13Z", "$Codex_Something_Else;"))
    finds = [d for d in drafts if d.kind == "find"]
    assert len(finds) == 2 and len({f.resolve_id(AUTHOR) for f in finds}) == 1
    p = finds[0].payload
    assert (p["find_kind"], p["system"], p["body_id"], p["body_name"], p["detail"]) == (
        "stellar_phenomenon", "Sol", None, None, {"signal": "$Fixed_Event_Life_Cloud;"})


def codex(**fields):
    base = dict(EntryID=2420101, Name="$Codex_Ent_Gas_Clds_Yellow_Name;", Name_Localised="Croceum Lagrange Cloud",
                SubCategory="$Codex_SubCategory_Organic_Structures;", Category="$Codex_Category_Biology;",
                Region="$Codex_RegionName_18;", System="Sol", SystemAddress=SOL, BodyID=3)
    base.update(fields)
    return event("CodexEntry", "2026-10-12T10:04:00Z", **base)


def test_a_codex_find_at_a_phenomenon_carries_the_body_name_from_the_earlier_scan():
    drafts = run(LOAD, jump(), planet(), codex(NearestDestination="$Fixed_Event_Life_Cloud;"))
    [find] = [d for d in drafts if d.kind == "find"]
    p = find.payload
    assert (p["find_kind"], p["body_name"], p["body_id"], p["system"]) == ("codex", "Sol 3", 3, "Sol")
    assert p["detail"] == {"entry_id": 2420101, "name": "$Codex_Ent_Gas_Clds_Yellow_Name;",
                           "category": "$Codex_Category_Biology;",
                           "subcategory": "$Codex_SubCategory_Organic_Structures;", "new_entry": False}


def test_a_new_codex_entry_is_a_find_and_other_entries_are_not():
    drafts = run(LOAD, jump(), codex(IsNewEntry=True),
                 codex(EntryID=1, NearestDestination="$Ancient:#index=1;"),
                 codex(EntryID=2, IsNewEntry=False), codex(EntryID=3))
    finds = [d for d in drafts if d.kind == "find"]
    assert len(finds) == 1
    assert finds[0].payload["detail"]["new_entry"] is True and finds[0].payload["body_name"] is None


def test_manual_find_and_note_revisions():
    first = find_revision("F100", ev.uuid7(), "other", "Sol", str(SOL), None, None, "Wreck")
    second = find_revision("F100", first.payload["find_id"], "other", "Sol", str(SOL), None, None,
                           "Wreck, 2 pods")
    assert first.event_id != second.event_id and ev.is_uuid7(first.event_id)
    assert first.payload["mode"] == "manual" and ev.stamp(first.created_at)
    assert first.resolve_id(AUTHOR) == first.event_id
    note = note_revision("F100", ev.uuid7(), "Sol", str(SOL), "Nice rings")
    assert note.kind == "note" and note.payload["deleted"] is False
    with pytest.raises(ev.PayloadError):
        note_revision("F100", ev.uuid7(), "Sol", str(SOL), "x‮")
    with pytest.raises(ev.PayloadError):
        find_revision("F100", ev.uuid7(), "treasure", "Sol", str(SOL), None, None, "")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_capture.py -q`
Expected: collection error, `ImportError: cannot import name 'find_revision'`.

- [ ] **Step 3: Write the implementation**

In `edmrn/ext/crew_capture.py`, add after `VALUABLE_CLASSES`:

```python
SIGNAL_TYPE_MAP = {
    '$SAA_SignalType_Biological;': 'biological',
    '$SAA_SignalType_Geological;': 'geological',
    '$SAA_SignalType_Human;': 'human',
    '$SAA_SignalType_Guardian;': 'guardian',
    '$SAA_SignalType_Thargoid;': 'thargoid',
    '$SAA_SignalType_PlanetAnomaly;': 'thargoid',
    '$SAA_SignalType_Other;': 'other',
    '$PlanetaryMiningLocation_Name;': 'mining',
}
NSP_PREFIX = '$Fixed_Event_Life'
CODEX_PHENOMENON_PREFIX = '$Fixed_Event_Life_'
```

In `_map`, insert before the final `return []`:

```python
        if name in ('FSSBodySignals', 'SAASignalsFound'):
            return self._signals(event, created_at, 'fss' if name == 'FSSBodySignals' else 'saa')
        if name == 'FSSSignalDiscovered':
            return self._phenomenon(event, created_at)
        if name == 'CodexEntry':
            return self._codex(event, created_at)
```

Append these methods to `JournalMapper`:

```python
    def _signals(self, event: dict, created_at: str, source: str) -> list[ev.Draft]:
        address, body_id = _address(event.get('SystemAddress')), _int(event.get('BodyID'))
        body_name = _str(event.get('BodyName'))
        signals = event.get('Signals')
        if address is None or body_id is None or body_name is None or not isinstance(signals, list):
            return []
        if body_name.endswith(' Ring'):
            return []
        counts: dict[str, int] = {}
        for signal in signals:
            if not isinstance(signal, dict):
                return []
            kind, count = signal.get('Type'), _int(signal.get('Count'))
            if not isinstance(kind, str) or count is None:
                return []
            if not kind.startswith('$'):
                return []  # a ring material: this is a ring's event
            kind = SIGNAL_TYPE_MAP.get(kind, 'other')
            counts[kind] = counts.get(kind, 0) + count
        genera: list[str] = []
        genuses = event.get('Genuses')
        if source == 'saa' and isinstance(genuses, list):
            genera = sorted({g['Genus'] for g in genuses
                             if isinstance(g, dict) and _str(g.get('Genus')) is not None})
        system = self._system_for(address)
        drafts = [self._draft('signals', created_at, {
            'system': system, 'address': address, 'body_id': body_id, 'body_name': body_name,
            'source': source, 'counts': counts, 'genera': genera})]
        if counts.get('biological', 0) >= 1:
            drafts.append(self._auto_find(created_at, 'biological', system, address, body_name, body_id,
                                          {'count': counts['biological']}))
        return drafts

    def _phenomenon(self, event: dict, created_at: str) -> list[ev.Draft]:
        signal, address = _str(event.get('SignalName')), _address(event.get('SystemAddress'))
        if (event.get('SignalType') != 'Codex' or signal is None or not signal.startswith(NSP_PREFIX)
                or address is None):
            return []
        return [self._auto_find(created_at, 'stellar_phenomenon', self._system_for(address), address,
                                None, None, {'signal': signal})]

    def _codex(self, event: dict, created_at: str) -> list[ev.Draft]:
        nearest = event.get('NearestDestination')
        new_entry = event.get('IsNewEntry') is True
        at_phenomenon = isinstance(nearest, str) and nearest.startswith(CODEX_PHENOMENON_PREFIX)
        if not (at_phenomenon or new_entry):
            return []
        address, body_id, entry_id = (_address(event.get('SystemAddress')), _int(event.get('BodyID')),
                                      _int(event.get('EntryID')))
        entry_name, category = _str(event.get('Name')), _str(event.get('Category'))
        subcategory = _str(event.get('SubCategory'))
        if None in (address, body_id, entry_id, entry_name, category, subcategory):
            return []
        system = _str(event.get('System')) or self._system_for(address)
        return [self._auto_find(created_at, 'codex', system, address,
                                self.state.body_names.get((address, body_id)), body_id,
                                {'entry_id': entry_id, 'name': entry_name, 'category': category,
                                 'subcategory': subcategory, 'new_entry': new_entry})]
```

Append the manual builders at module level, after the class:

```python
# --- manual entries (Spec C's UI and the later local API build on these) ----------------

def find_revision(commander: str, find_id: str, find_kind: str, system: Optional[str], address: str,
                  body_name: Optional[str], body_id: Optional[int], note: str, deleted: bool = False,
                  *, created_at: Optional[str] = None) -> ev.Draft:
    """One revision of a manual find (C5); raises PayloadError for invalid input (C12)."""
    payload = {'mode': 'manual', 'find_id': find_id, 'find_kind': find_kind, 'system': system,
               'address': address, 'body_name': body_name, 'body_id': body_id, 'note': note,
               'deleted': deleted}
    ev.validate_payload('find', ev.ENVELOPE_VERSION, payload)
    return ev.Draft('find', commander, created_at or ev.utc_now_stamp(), payload, event_id=ev.uuid7())


def note_revision(commander: str, note_id: str, system: Optional[str], address: str, text: str,
                  deleted: bool = False, *, created_at: Optional[str] = None) -> ev.Draft:
    """One revision of a note (C5); raises PayloadError for invalid input (C12)."""
    payload = {'note_id': note_id, 'system': system, 'address': address, 'text': text,
               'deleted': deleted}
    ev.validate_payload('note', ev.ENVELOPE_VERSION, payload)
    return ev.Draft('note', commander, created_at or ev.utc_now_stamp(), payload, event_id=ev.uuid7())
```

Update the module docstring's spec line to `(Spec A C5, C6, C11) and the manual entry builders.`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_capture.py -q`
Expected: all pass.

- [ ] **Step 5: Mutation check**

In `_signals`, temporarily delete the line `return []  # a ring material: this is a ring's event`
together with its `if`. Run the tests. Expected: `test_ring_signal_events_are_skipped` fails (the
`Painite` event becomes a signals draft counting `other`). Revert.

- [ ] **Step 6: Commit**

```bash
git add edmrn/ext/crew_capture.py tests/test_crew_capture.py
git commit -m "feat(crew): signals, phenomena and codex finds; manual find and note builders

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 8: Ship status

**Files:**
- Create: `edmrn/ext/crew_status.py`
- Test: `tests/test_crew_status.py`

**Interfaces:**
- Consumes: Task 1's `stamp`.
- Produces:
  - `IN_MAIN_SHIP = 1 << 24`, `EDSY_IMPORT_URL`;
  - `@dataclass(frozen=True) class ShipStatus` with fields `commander`, `ship`, `ship_name`,
    `ship_ident`, `edsy_url`, `hull`, `fuel_main`, `fuel_reserve`, `fuel_capacity_main`,
    `fuel_capacity_reserve`, `journal_at` and `timestamp`, all defaulting to `None`;
  - `edsy_url(loadout: dict) -> str`;
  - `class CrewStatus` with `state`, `subscribe(fn)`, `feed(event)` (journal),
    `feed_status(status)` (`Status.json`) and `adopt(replayed: ShipStatus) -> bool`.

Rules (C9, ruling 5 and ruling 6):
- **Commander.** `LoadGame`/`Commander` set the commander by name. Switching to a different known
  commander resets everything.
- **`Loadout`.**
  - It sets ship, name and ident, and the EDSY link built from the whole event.
  - Hull comes from `HullHealth` (0–1, kept if invalid).
  - Capacities come from `FuelCapacity.Main`/`.Reserve`.
- **Hull.**
  - `HullDamage` sets hull to `Health` (0–1) unless `Fighter` is true. That covers both the pilot
    and NPC crew flying the player's ship.
  - `RepairAll`, or a `Repair` whose `Item` or any `Items` entry is `hull` or `all`
    (case-insensitive), sets hull to 1.0.
- **Fuel.** A `Status.json` dict with bit 24 set in `Flags` updates `fuel_main`/`fuel_reserve`
  from `Fuel.FuelMain`/`FuelReservoir`, each only if it is a finite number between 0 and 100000.
  Otherwise the fuel values are kept.
- **Timestamps.** A change sets `timestamp` to the source's timestamp. A journal change also sets
  `journal_at`.
- **Observers** hear only real changes. A raising observer is logged and nothing else stops.
- **`adopt(replayed)`.**
  - It takes the replayed state only if `replayed.journal_at` is newer than the current
    `journal_at`, or the current one is `None`.
  - It keeps the current live fuel readings when the current state has them.
  - `timestamp` becomes the later of the two.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_crew_status.py`:

```python
import base64
import gzip
import json

import pytest

from edmrn.ext.crew_status import IN_MAIN_SHIP, CrewStatus, ShipStatus, edsy_url
from journal_helpers import event

LOADOUT = event("Loadout", "2026-10-12T10:00:00Z", Ship="krait_light", ShipID=7, ShipName="Wanderer",
                ShipIdent="DR-01", HullValue=1, ModulesValue=2, HullHealth=0.87, UnladenMass=400.0,
                CargoCapacity=0, MaxJumpRange=62.3, FuelCapacity={"Main": 32.0, "Reserve": 0.63},
                Rebuy=1000, Modules=[{"Slot": "Armour", "Item": "krait_light_armour_grade1", "On": True,
                                      "Priority": 1, "Health": 1.0}])


def fed(*events):
    status = CrewStatus()
    for e in events:
        status.feed(e)
    return status


def in_ship(ts, main, reserve):
    return {"timestamp": ts, "event": "Status", "Flags": IN_MAIN_SHIP | 8,
            "Fuel": {"FuelMain": main, "FuelReservoir": reserve}}


def test_loadout_sets_ship_hull_capacity_and_edsy_link():
    s = fed(event("LoadGame", Commander="Jameson", FID="F1"), LOADOUT).state
    assert (s.commander, s.ship, s.ship_name, s.ship_ident) == ("Jameson", "krait_light", "Wanderer", "DR-01")
    assert s.hull == 0.87 and (s.fuel_capacity_main, s.fuel_capacity_reserve) == (32.0, 0.63)
    assert s.journal_at == s.timestamp == "2026-10-12T10:00:00Z"
    assert s.edsy_url.startswith("https://edsy.org/#/I=")


def test_edsy_link_round_trips_to_the_loadout():
    url = edsy_url(LOADOUT)
    payload = url[len("https://edsy.org/#/I="):]
    assert "=" not in payload
    assert json.loads(gzip.decompress(base64.urlsafe_b64decode(payload.replace("%3D", "=")))) == LOADOUT


@pytest.mark.parametrize("fields,expected", [
    ({"Health": 0.55, "PlayerPilot": True}, 0.55),
    ({"Health": 0.55, "PlayerPilot": True, "Fighter": False}, 0.55),
    ({"Health": 0.55, "PlayerPilot": False, "Fighter": False}, 0.55),
    ({"Health": 0.55, "PlayerPilot": True, "Fighter": True}, 0.87),
    ({"Health": 0.55, "PlayerPilot": False, "Fighter": True}, 0.87),
    ({"Health": float("nan"), "PlayerPilot": True}, 0.87),
    ({"Health": 1.5, "PlayerPilot": True}, 0.87),
    ({"Health": True, "PlayerPilot": True}, 0.87),
])
def test_hull_damage(fields, expected):
    assert fed(LOADOUT, event("HullDamage", "2026-10-12T10:05:00Z", **fields)).state.hull == expected


@pytest.mark.parametrize("repair,expected", [
    (event("RepairAll", Cost=100), 1.0),
    (event("Repair", Cost=10, Items=["Wear", "Hull"]), 1.0),
    (event("Repair", Cost=10, Item="all"), 1.0),
    (event("Repair", Cost=10, Item="HULL"), 1.0),
    (event("Repair", Cost=10, Items=["Wear", "$int_cockpit_name;"]), 0.4),
    (event("Repair", Cost=10, Items="Hull"), 0.4),
])
def test_repairs(repair, expected):
    damaged = event("HullDamage", "2026-10-12T10:01:00Z", Health=0.4, PlayerPilot=True)
    assert fed(LOADOUT, damaged, repair).state.hull == expected


def test_status_json_fuel_only_while_in_the_ship():
    status = fed(LOADOUT)
    status.feed_status(in_ship("2026-10-12T10:01:00Z", 20.5, 0.4))
    s = status.state
    assert (s.fuel_main, s.fuel_reserve) == (20.5, 0.4)
    assert s.timestamp == "2026-10-12T10:01:00Z" and s.journal_at == "2026-10-12T10:00:00Z"
    status.feed_status({"timestamp": "2026-10-12T10:02:00Z", "event": "Status", "Flags": 0})
    status.feed_status({"timestamp": "2026-10-12T10:03:00Z", "event": "Status", "Flags": IN_MAIN_SHIP,
                        "Fuel": {"FuelMain": True, "FuelReservoir": float("nan")}})
    status.feed_status({"timestamp": "2026-10-12T10:04:00Z", "Flags": True, "Fuel": {"FuelMain": 1.0}})
    status.feed_status("garbage")
    status.feed_status(None)
    assert (status.state.fuel_main, status.state.fuel_reserve) == (20.5, 0.4)


def test_observers_hear_only_changes_and_a_raising_observer_stops_nothing():
    status = CrewStatus()
    seen = []
    status.subscribe(lambda s: (_ for _ in ()).throw(RuntimeError("observer failed")))
    status.subscribe(seen.append)
    status.feed(LOADOUT)
    status.feed(LOADOUT)
    status.feed(event("Music", MusicTrack="Exploration"))
    status.feed("not a dict")
    assert len(seen) == 1 and seen[0].ship == "krait_light"


def test_a_commander_switch_resets_the_ship():
    s = fed(event("LoadGame", Commander="Jameson"), LOADOUT, event("LoadGame", Commander="Other")).state
    assert s.commander == "Other" and s.ship is None and s.hull is None


def test_adopt_keeps_live_fuel_and_takes_newer_journal_fields():
    live = CrewStatus()
    live.feed_status(in_ship("2026-10-12T10:00:05Z", 12.0, 0.5))
    assert live.state.journal_at is None
    replayed = fed(event("LoadGame", "2026-10-12T09:59:00Z", Commander="Jameson"), LOADOUT).state
    assert live.adopt(replayed) is True
    s = live.state
    assert s.ship == "krait_light" and s.commander == "Jameson" and s.fuel_capacity_main == 32.0
    assert (s.fuel_main, s.fuel_reserve) == (12.0, 0.5)
    assert s.timestamp == "2026-10-12T10:00:05Z" and s.journal_at == "2026-10-12T10:00:00Z"


def test_adopt_rejects_an_older_or_empty_replay():
    live = fed(event("Loadout", "2026-10-12T11:00:00Z", Ship="anaconda", HullHealth=1.0,
                     FuelCapacity={"Main": 64.0, "Reserve": 1.0}))
    assert live.adopt(fed(LOADOUT).state) is False
    assert live.state.ship == "anaconda"
    assert live.adopt(ShipStatus()) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_status.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'edmrn.ext.crew_status'`.

- [ ] **Step 3: Write the implementation**

Create `edmrn/ext/crew_status.py`:

```python
"""Ship, hull and fuel of the active commander (Spec A C9). Ephemeral: never logged.

Commander and location live in app.location_tracker; this holds the rest. Observers run on the
thread that caused the change (journal monitor, fuel tracker or replay), so UI observers go
through root.after.
"""
from __future__ import annotations

import base64
import gzip
import json
import math
import threading
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from edmrn.ext.crew_events import stamp
from edmrn.logger import get_logger

logger = get_logger('CrewStatus')

EDSY_IMPORT_URL = 'https://edsy.org/#/I='
IN_MAIN_SHIP = 1 << 24
MAX_TONS = 1e5


@dataclass(frozen=True)
class ShipStatus:
    commander: Optional[str] = None
    ship: Optional[str] = None
    ship_name: Optional[str] = None
    ship_ident: Optional[str] = None
    edsy_url: Optional[str] = None
    hull: Optional[float] = None
    fuel_main: Optional[float] = None
    fuel_reserve: Optional[float] = None
    fuel_capacity_main: Optional[float] = None
    fuel_capacity_reserve: Optional[float] = None
    journal_at: Optional[str] = None
    timestamp: Optional[str] = None


Observer = Callable[[ShipStatus], None]


def edsy_url(loadout: dict) -> str:
    """EDSY import link for a journal Loadout, built as EDMC builds it (knowledge/edsy.md)."""
    data = json.dumps(loadout, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    encoded = base64.urlsafe_b64encode(gzip.compress(data, mtime=0)).decode('ascii')
    return EDSY_IMPORT_URL + encoded.replace('=', '%3D')


def _str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _number(value: Any, high: float) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and 0.0 <= number <= high else None


def _repairs_hull(event: dict) -> bool:
    items = []
    if isinstance(event.get('Item'), str):
        items.append(event['Item'])
    if isinstance(event.get('Items'), list):
        items.extend(item for item in event['Items'] if isinstance(item, str))
    return any(item.lower() in ('hull', 'all') for item in items)


def apply_journal(state: ShipStatus, event: dict) -> ShipStatus:
    name = event.get('event')
    if name in ('LoadGame', 'Commander'):
        commander = _str(event.get('Commander' if name == 'LoadGame' else 'Name'))
        if commander is None:
            return state
        if state.commander is not None and state.commander != commander:
            return ShipStatus(commander=commander)
        return replace(state, commander=commander)
    if name == 'Loadout':
        capacity = event.get('FuelCapacity') if isinstance(event.get('FuelCapacity'), dict) else {}
        hull = _number(event.get('HullHealth'), 1.0)
        return replace(state, ship=_str(event.get('Ship')), ship_name=_str(event.get('ShipName')),
                       ship_ident=_str(event.get('ShipIdent')), edsy_url=edsy_url(event),
                       hull=state.hull if hull is None else hull,
                       fuel_capacity_main=_number(capacity.get('Main'), MAX_TONS),
                       fuel_capacity_reserve=_number(capacity.get('Reserve'), MAX_TONS))
    if name == 'HullDamage':
        health = _number(event.get('Health'), 1.0)
        if event.get('Fighter') is True or health is None:
            return state
        return replace(state, hull=health)
    if name == 'RepairAll' or (name == 'Repair' and _repairs_hull(event)):
        return replace(state, hull=1.0)
    return state


def apply_status(state: ShipStatus, status: dict) -> ShipStatus:
    flags = status.get('Flags')
    if not isinstance(flags, int) or isinstance(flags, bool) or not flags & IN_MAIN_SHIP:
        return state
    fuel = status.get('Fuel')
    if not isinstance(fuel, dict):
        return state
    main = _number(fuel.get('FuelMain'), MAX_TONS)
    reserve = _number(fuel.get('FuelReservoir'), MAX_TONS)
    return replace(state, fuel_main=state.fuel_main if main is None else main,
                   fuel_reserve=state.fuel_reserve if reserve is None else reserve)


class CrewStatus:
    """Thread-safe holder of the active commander's ShipStatus."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = ShipStatus()
        self._observers: list[Observer] = []

    @property
    def state(self) -> ShipStatus:
        return self._state

    def subscribe(self, observer: Observer) -> None:
        self._observers.append(observer)

    def feed(self, event: Any) -> None:
        """A journal event (journal listener or replay)."""
        if isinstance(event, dict):
            self._apply(lambda s: apply_journal(s, event), event.get('timestamp'), journal=True)

    def feed_status(self, status: Any) -> None:
        """A Status.json dict (FuelTracker.status_listeners)."""
        if isinstance(status, dict):
            self._apply(lambda s: apply_status(s, status), status.get('timestamp'), journal=False)

    def _apply(self, step: Callable[[ShipStatus], ShipStatus], timestamp: Any, journal: bool) -> None:
        try:
            with self._lock:
                old = self._state
                new = step(old)
                if new == old:
                    return
                at = stamp(timestamp)
                self._state = replace(new, timestamp=at or old.timestamp,
                                      journal_at=(at or old.journal_at) if journal else new.journal_at)
        except Exception as e:
            logger.error(f"Crew status update ignored: {e}")
            return
        self._notify()

    def adopt(self, replayed: ShipStatus) -> bool:
        """Take a replayed state's journal fields if newer; keep live Status.json fuel (plan ruling 5)."""
        with self._lock:
            current = self._state
            if replayed.journal_at is None or (
                    current.journal_at is not None and replayed.journal_at <= current.journal_at):
                return False
            stamps = [t for t in (current.timestamp, replayed.timestamp) if t is not None]
            new = replace(replayed,
                          fuel_main=replayed.fuel_main if current.fuel_main is None else current.fuel_main,
                          fuel_reserve=(replayed.fuel_reserve if current.fuel_reserve is None
                                        else current.fuel_reserve),
                          timestamp=max(stamps) if stamps else None)
            if new == current:
                return False
            self._state = new
        self._notify()
        return True

    def _notify(self) -> None:
        state = self._state  # the newest, as in LocationTracker._notify
        for observer in list(self._observers):
            try:
                observer(state)
            except Exception as e:
                logger.error(f"Crew status observer failed: {e}")
```

Note on `journal_at` in `_apply`: a commander switch returns a fresh `ShipStatus` with
`journal_at=None`. The journal branch then sets it to the switch's timestamp. A `Status.json`
change keeps the `journal_at` the step produced, which is always the old one.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_status.py -q`
Expected: all pass.

- [ ] **Step 5: Mutation check**

In `adopt`, temporarily replace the `fuel_main=…` argument with `fuel_main=replayed.fuel_main`. Run
the tests. Expected: `test_adopt_keeps_live_fuel_and_takes_newer_journal_fields` fails. Revert.

- [ ] **Step 6: Commit**

```bash
git add edmrn/ext/crew_status.py tests/test_crew_status.py
git commit -m "feat(crew): ship, hull and fuel status with EDSY link and replay adoption

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 9: `Status.json` listener hook in upstream's fuel tracker

**Files:**
- Modify: `edmrn/fuel_tracker.py` (upstream; CRLF; three small insertions)
- Modify: `docs/architecture/overview.md` (hook-point row)
- Modify: `docs/knowledge/upstream-baseline.md` (one fact)
- Test: `tests/test_fuel_tracker_hook.py`

**Interfaces:**
- Produces: the class attribute `FuelTracker.status_listeners: list`. `_tracking_loop` passes every
  `Status.json` dict it read to each listener, before upstream's `_update_fuel`. Each call has its
  own try/except and log line. Task 11 appends `CrewStatus.feed_status` to it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_fuel_tracker_hook.py`:

```python
import json
import threading
from types import SimpleNamespace

import pytest

from edmrn.fuel_tracker import FuelTracker
from journal_helpers import wait_for


@pytest.fixture(autouse=True)
def listeners(monkeypatch):
    fresh = []
    monkeypatch.setattr(FuelTracker, "status_listeners", fresh)
    return fresh


def test_status_dicts_reach_listeners_and_a_raising_listener_stops_nothing(tmp_path, listeners):
    status_file = tmp_path / "Status.json"
    status_file.write_text(json.dumps({"timestamp": "2026-10-12T10:00:00Z", "event": "Status",
                                       "Flags": 1 << 24, "Fuel": {"FuelMain": 16.0, "FuelReservoir": 0.5}}),
                           encoding="utf-8")
    tracker = FuelTracker(SimpleNamespace())
    tracker.status_file = str(status_file)
    seen = []

    def boom(status):
        raise RuntimeError("listener failed")

    listeners.extend([boom, seen.append])
    tracker.is_tracking = True
    thread = threading.Thread(target=tracker._tracking_loop, daemon=True)
    thread.start()
    try:
        assert wait_for(lambda: seen and tracker.fuel_capacity == 16.0)
    finally:
        tracker.is_tracking = False
        thread.join(3)
    assert seen[0]["Fuel"]["FuelMain"] == 16.0


def test_the_hook_is_a_class_attribute_shared_by_every_tracker():
    assert FuelTracker.status_listeners is FuelTracker(SimpleNamespace()).status_listeners
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fuel_tracker_hook.py -q`
Expected: FAIL. `monkeypatch.setattr` raises `AttributeError: <class 'edmrn.fuel_tracker.FuelTracker'> has no attribute 'status_listeners'`.

- [ ] **Step 3: Add the hook (CRLF, no other changes)**

In `edmrn/fuel_tracker.py`:
1. Directly below `class FuelTracker:`, insert the line `    status_listeners = []`.
2. In `_tracking_loop`, change
   ```python
                   if status:
                       self._update_fuel(status)
   ```
   to
   ```python
                   if status:
                       self._dispatch_status(status)
                       self._update_fuel(status)
   ```
3. Directly after the `_read_status` method, insert:
   ```python
       def _dispatch_status(self, status):
           for listener in list(FuelTracker.status_listeners):
               try:
                   listener(status)
               except Exception as e:
                   logger.error(f"Status listener error: {e}")
       
   ```

Use an editor that keeps CRLF. Then check:
- `file edmrn/fuel_tracker.py` reports "with CRLF line terminators";
- `git diff --stat edmrn/fuel_tracker.py` shows only about 9 insertions and no deletions apart from
  the unchanged context.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fuel_tracker_hook.py -q`
Expected: both pass (the first takes about 1 s).

- [ ] **Step 5: Verify the upstream capacity defect before recording it**

Write a probe script outside the repository (scratch directory) that redirects
`HOME`/`USERPROFILE`/`APPDATA`/`LOCALAPPDATA` to an empty temp directory before importing `edmrn`,
per `docs/how-to/run.md`. It evaluates the expression upstream uses in
`EDMRN_App._journal_callback` for a `Loadout`:

```python
fuel_capacity = {"Main": 32.0, "Reserve": 0.63}
try:
    print(bool(fuel_capacity and fuel_capacity > 0))
except TypeError as e:
    print("TypeError:", e)
```

Then read `edmrn/app.py` `_journal_callback` (around line 80–115) and confirm that the `if event ==
'Loadout'` block sits inside the method's outer `try`, so the `TypeError` is caught there.

- [ ] **Step 6: Update the docs**

In `docs/architecture/overview.md`, add a row to the "Upstream hook points" table, after the
`edmrn/journal.py` listener row:

```
| `edmrn/fuel_tracker.py` | Class attribute `status_listeners` and `_dispatch_status`: every `Status.json` dict the 1 s loop reads goes to each listener before upstream's fuel handling | Extension point for the crew status (crew capture spec C9) without a second `Status.json` reader (ADR-0001); class-level for the same reason as `JournalMonitor.event_listeners` | yes |
```

In `docs/knowledge/upstream-baseline.md`, below the bullet
"`Status.json` is read only by `fuel_tracker` …", add (adjusting the wording to what Step 5
showed):

```
- `_journal_callback` hands `Loadout.FuelCapacity` to the fuel tracker only if `fuel_capacity > 0`,
  but the field is an object (`{Main, Reserve}`). The comparison raises `TypeError` inside the
  callback's `try`, so the tracker never gets the journal capacity and treats the first `FuelMain`
  it reads as the capacity **[probe, 2026-10-12]**.
```

- [ ] **Step 7: Run the whole suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`. Expected: all pass.

```bash
git add edmrn/fuel_tracker.py tests/test_fuel_tracker_hook.py docs/architecture/overview.md docs/knowledge/upstream-baseline.md
git commit -m "feat(crew): Status.json listener hook in upstream's fuel tracker

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 10: Live capture and the startup replay

**Files:**
- Modify: `edmrn/ext/crew_capture.py` (append)
- Test: `tests/test_crew_capture.py` (append)

**Interfaces:**
- Consumes: Task 6's `JournalMapper`, `MapperState`; Task 8's `CrewStatus` (`feed`, `adopt`).
- Produces:
  - `class LiveCapture(enqueue: Callable[[Draft], None], status: CrewStatus | None = None)` with
    `BUFFER_LIMIT = 5000`, `feed(event)` (journal listener), `seed(state: MapperState | None)`,
    property `seeded`, and attribute `mapper`;
  - `replay(journal_dir: str | None, enqueue, status: CrewStatus | None = None, max_files: int = 5) -> MapperState | None`.

Rules (C10, ruling 7):
- `LiveCapture.feed` passes every dict to `status.feed` at once.
- Until `seed` runs, `feed` buffers events (up to `BUFFER_LIMIT`; the overflow is logged once). After
  that it maps them and enqueues the drafts.
- `seed(state)` runs once:
  1. a non-`None` state becomes the live mapper's state;
  2. the buffer is mapped in order and enqueued;
  3. `seeded` becomes true.

  A `LoadGame` inside the buffer therefore overrides the seed.
- `replay` picks the newest `max_files` `Journal.*.log` by modification time and reads them oldest
  first, line by line.
  - Lines without `"event"`, broken JSON and non-dicts are skipped. An unreadable file is logged and
    skipped.
  - Drafts are enqueued. Events are also fed to a private `CrewStatus`, which is offered to
    `status.adopt` at the end.
  - It returns the final mapper state, or `None` when no file was found.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crew_capture.py`:

```python
import os

from edmrn.ext.crew_capture import LiveCapture, replay
from edmrn.ext.crew_identity import KeyRing
from edmrn.ext.crew_status import CrewStatus
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.store import Store
from journal_helpers import append_events, journal_file


def test_live_events_before_seed_are_buffered_and_attributed():
    queued = []
    live = LiveCapture(queued.append)
    live.feed(jump())
    assert queued == [] and not live.seeded
    live.seed(MapperState(commander="F100", commander_name="Jameson"))
    assert live.seeded
    assert [(d.kind, d.commander) for d in queued] == [("visit", "F100")]
    live.feed(jump(system="Alpha", address=42, ts="2026-10-12T10:10:00Z"))
    assert len(queued) == 2 and queued[1].payload["system"] == "Alpha"


def test_a_loadgame_in_the_buffer_overrides_the_seed():
    queued = []
    live = LiveCapture(queued.append)
    live.feed(event("LoadGame", Commander="Other", FID="F200"))
    live.feed(jump())
    live.seed(MapperState(commander="F100", commander_name="Jameson"))
    assert [d.commander for d in queued] == ["F200"]


def test_seed_without_a_state_maps_the_buffer_from_scratch():
    queued = []
    live = LiveCapture(queued.append)
    live.feed(jump())
    live.feed(LOAD)
    live.feed(jump(ts="2026-10-12T10:01:00Z"))
    live.seed(None)
    assert [d.commander for d in queued] == ["F100"]


def test_a_second_seed_changes_nothing():
    queued = []
    live = LiveCapture(queued.append)
    live.seed(MapperState(commander="F100", commander_name="Jameson"))
    live.seed(MapperState(commander="F200", commander_name="Other"))
    live.feed(jump())
    assert [d.commander for d in queued] == ["F100"]


def test_the_buffer_is_bounded():
    live = LiveCapture(lambda d: None)
    for _ in range(LiveCapture.BUFFER_LIMIT + 10):
        live.feed(jump())
    assert len(live._buffer) == LiveCapture.BUFFER_LIMIT


def test_status_is_fed_live_even_before_the_seed():
    status = CrewStatus()
    live = LiveCapture(lambda d: None, status)
    live.feed(event("Loadout", "2026-10-12T10:00:00Z", Ship="sidewinder", HullHealth=1.0,
                    FuelCapacity={"Main": 2.0, "Reserve": 0.3}))
    assert status.state.ship == "sidewinder"


def test_a_raising_enqueue_does_not_reach_the_monitor():
    def refuse(draft):
        raise RuntimeError("queue gone")

    live = LiveCapture(refuse)
    live.seed(MapperState(commander="F100", commander_name="Jameson"))
    live.feed(jump())


def write_journal(directory, stamp, mtime, *events):
    directory.mkdir(exist_ok=True)
    path = journal_file(directory, stamp)
    append_events(path, *events)
    os.utime(path, (mtime, mtime))
    return path


def test_replay_reads_the_newest_five_files_oldest_first(tmp_path):
    journals = tmp_path / "journals"
    for i in range(6):
        write_journal(journals, f"2026-10-0{i + 1}T100000", 1_000_000 + i * 100, LOAD,
                      jump(system=f"S{i}", address=100 + i, ts=f"2026-10-0{i + 1}T10:00:00Z"))
    queued = []
    state = replay(str(journals), queued.append)
    assert [d.payload["system"] for d in queued] == ["S1", "S2", "S3", "S4", "S5"]
    assert state.commander == "F100" and state.system == "S5"


def test_replay_chooses_by_modification_time_not_by_name(tmp_path):
    journals = tmp_path / "journals"
    write_journal(journals, "220315152335", 2_000_000, LOAD, jump(system="Horizons"))
    write_journal(journals, "2026-10-01T100000", 1_000_000, LOAD, jump(system="Odyssey"))
    queued = []
    replay(str(journals), queued.append, max_files=1)
    assert [d.payload["system"] for d in queued] == ["Horizons"]


def test_replay_seeds_the_status_from_the_startup_loadout(tmp_path):
    journals = tmp_path / "journals"
    write_journal(journals, "2026-10-12T100000", 1_000_000, LOAD,
                  event("Loadout", "2026-10-12T10:00:01Z", Ship="krait_light", HullHealth=0.9,
                        FuelCapacity={"Main": 32.0, "Reserve": 0.63}))
    status = CrewStatus()
    replay(str(journals), lambda d: None, status)
    assert status.state.ship == "krait_light" and status.state.hull == 0.9


def test_replay_of_a_missing_folder_or_no_files_returns_none(tmp_path):
    assert replay(str(tmp_path / "missing"), lambda d: None) is None
    assert replay(str(tmp_path), lambda d: None) is None
    assert replay(None, lambda d: None) is None


def test_replay_skips_unreadable_files_and_bad_lines(tmp_path):
    journals = tmp_path / "journals"
    path = write_journal(journals, "2026-10-12T100000", 1_000_000, LOAD)
    (journals / "Journal.2026-10-12T090000.01.log").mkdir()
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"event": broken\n["event"]\n')
    append_events(path, jump())
    queued = []
    replay(str(journals), queued.append)
    assert [d.kind for d in queued] == ["visit"]


def _session(journals):
    write_journal(journals, "2026-10-12T100000", 1_000_000, LOAD, jump(),
                  event("FSSDiscoveryScan", "2026-10-12T10:00:30Z", Progress=0.5, BodyCount=1,
                        NonBodyCount=0, SystemName="Sol", SystemAddress=SOL),
                  planet(), event("FSSAllBodiesFound", "2026-10-12T10:02:00Z", SystemName="Sol",
                                  SystemAddress=SOL, Count=1))


def test_replaying_a_journal_twice_adds_no_envelope(tmp_path):
    journals = tmp_path / "journals"
    _session(journals)
    store = Store(tmp_path / "edmrn.db")
    ring = KeyRing(tmp_path / "crew_keys")
    try:
        crew = CrewStore(store)
        for _ in range(2):
            queued = []
            replay(str(journals), queued.append)
            crew.append_own([(ring.for_commander(d.commander), d) for d in queued])
        with store.read() as conn:
            assert conn.execute("SELECT kind FROM crew_log ORDER BY seq").fetchall() == [
                ("visit",), ("honk",), ("body",), ("all_bodies",)]
    finally:
        store.close()


def test_two_commanders_in_one_journal_sequence_give_two_authors(tmp_path):
    journals = tmp_path / "journals"
    write_journal(journals, "2026-10-12T100000", 1_000_000, LOAD, jump(),
                  event("LoadGame", "2026-10-12T11:00:00Z", Commander="Other", FID="F200"),
                  jump(ts="2026-10-12T11:01:00Z"))
    store = Store(tmp_path / "edmrn.db")
    ring = KeyRing(tmp_path / "crew_keys")
    try:
        queued = []
        replay(str(journals), queued.append)
        CrewStore(store).append_own([(ring.for_commander(d.commander), d) for d in queued])
        with store.read() as conn:
            authors = conn.execute("SELECT DISTINCT author FROM crew_log").fetchall()
        assert len(authors) == 2
        assert len(list((tmp_path / "crew_keys").glob("cmdr-*.json"))) == 2
    finally:
        store.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_capture.py -q`
Expected: collection error, `ImportError: cannot import name 'LiveCapture'`.

- [ ] **Step 3: Write the implementation**

In `edmrn/ext/crew_capture.py`, add `import glob`, `import json`, `import os` and
`import threading` to the imports, and change `from typing import Any, Optional` to
`from typing import Any, Callable, Optional`. Add `from edmrn.ext.crew_status import CrewStatus`
after the `crew_events` import. Update the module docstring's spec line to
`(Spec A C5, C6, C10, C11), the live listener, the startup replay and the manual entry builders.`
Then append:

```python
# --- live capture and the startup replay (C10) -------------------------------------------

Enqueue = Callable[[ev.Draft], None]


class LiveCapture:
    """The journal listener (monitor thread): maps live events and hands drafts on. Never writes.

    The monitor reads the newest journal from its end, so the live mapper would not know the
    commander. Events are buffered until the startup replay seeds the mapper (plan ruling 7);
    deterministic ids remove what both saw.
    """
    BUFFER_LIMIT = 5000

    def __init__(self, enqueue: Enqueue, status: Optional[CrewStatus] = None) -> None:
        self.mapper = JournalMapper()
        self._enqueue = enqueue
        self._status = status
        self._lock = threading.Lock()
        self._seeded = False
        self._buffer: list[dict] = []
        self._overflow_logged = False

    @property
    def seeded(self) -> bool:
        return self._seeded

    def feed(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        if self._status is not None:
            self._status.feed(event)
        with self._lock:
            if not self._seeded:
                if len(self._buffer) < self.BUFFER_LIMIT:
                    self._buffer.append(event)
                elif not self._overflow_logged:
                    self._overflow_logged = True
                    logger.warning('Crew capture buffer full before the startup replay finished')
                return
            drafts = self.mapper.map(event)
        self._hand_on(drafts)

    def seed(self, state: Optional[MapperState]) -> None:
        with self._lock:
            if self._seeded:
                return
            if state is not None:
                self.mapper = JournalMapper(state)
            drafts = []
            for event in self._buffer:
                drafts.extend(self.mapper.map(event))
            self._buffer = []
            self._seeded = True
        self._hand_on(drafts)

    def _hand_on(self, drafts: list[ev.Draft]) -> None:
        for draft in drafts:
            try:
                self._enqueue(draft)
            except Exception as e:
                logger.error(f"Crew draft not queued: {e}")


def _newest_journals(journal_dir: Optional[str], max_files: int) -> list[str]:
    if not journal_dir or not os.path.isdir(journal_dir):
        return []

    def mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    try:
        files = glob.glob(os.path.join(journal_dir, 'Journal.*.log'))
    except OSError as e:
        logger.error(f"Crew replay could not list journals: {e}")
        return []
    return sorted(files, key=mtime, reverse=True)[:max_files]


def replay(journal_dir: Optional[str], enqueue: Enqueue, status: Optional[CrewStatus] = None,
           max_files: int = 5) -> Optional[MapperState]:
    """Map the newest journal files, oldest first (C10); seed `status` only if newer."""
    files = _newest_journals(journal_dir, max_files)
    if not files:
        return None
    mapper = JournalMapper()
    replayed = CrewStatus()
    for path in reversed(files):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if '"event"' not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    replayed.feed(event)
                    for draft in mapper.map(event):
                        enqueue(draft)
        except OSError as e:
            logger.warning(f"Crew replay skipped {os.path.basename(path)}: {e}")
    if status is not None:
        status.adopt(replayed.state)
    return mapper.snapshot()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_capture.py -q`
Expected: all pass.

- [ ] **Step 5: Mutation check**

In `LiveCapture.feed`, temporarily replace the whole `if not self._seeded:` block with nothing (map
immediately). Run the tests. Expected:
`test_live_events_before_seed_are_buffered_and_attributed` fails. Revert.

- [ ] **Step 6: Commit**

```bash
git add edmrn/ext/crew_capture.py tests/test_crew_capture.py
git commit -m "feat(crew): live capture with seed buffer and the startup replay

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 11: Writer, `app.crew`, install and the `app.py` hook

**Files:**
- Create: `edmrn/ext/crew.py`
- Modify: `edmrn/ext/location.py` (extract `journal_dir_for`)
- Modify: `edmrn/app.py` (upstream; CRLF; two lines)
- Modify: `docs/architecture/overview.md` (hook-point row, crew data-flow section)
- Test: `tests/test_crew_install.py`

**Interfaces:**
- Consumes:
  - Task 4/5's `CrewStore` (`append_own`, `manual_find`, `note`, the queries);
  - Task 3's `KeyRing`, `KeyFileError`, `Identity`;
  - Task 7's `find_revision`, `note_revision`;
  - Task 8's `CrewStatus`;
  - Task 9's `FuelTracker.status_listeners`;
  - Task 10's `LiveCapture`, `replay`;
  - `JournalMonitor.event_listeners`.
- Produces:
  - `location.journal_dir_for(app) -> str | None`;
  - `class Writer(crew_store, keyring, root)`:
    - constants `INTERVAL_MS = 1000`, `BATCH = 500`, `QUEUE_CAP = 10_000`;
    - `enqueue(draft)` (any thread), `start()`, `stop()`, `drain(limit=None) -> int` (Tk thread);
    - properties `pending` and `running`;
  - `class CrewService` (`app.crew`):
    - attributes `store` (the `CrewStore`), `keyring`, `writer`, `live`, `ship` (the `CrewStatus`);
    - `commander`, `identity()`, and `status() -> (LocationState | None, ShipStatus)`;
    - `report_find(find_kind, system, address, body_name=None, body_id=None, note='') -> str`;
    - `edit_find(find_id, **changes)` and `delete_find(find_id)`;
    - `set_note(system, address, text, note_id=None) -> str` and `delete_note(note_id)`;
  - `install(app) -> CrewService | None`.

Rules (spec Design, Error handling):
- **Install.**
  - `install` reuses `app.edmrn_store`, which the neutron install opened. If the store is `None`,
    it logs `app.edmrn_store_error`, sets `app.crew = None` and registers nothing.
  - Key files go to `<folder of edmrn.db>/crew_keys/`.
  - It is idempotent: an existing `CrewService` is returned as is.
- **Writer.**
  - `enqueue` validates first. An invalid draft is dropped and logged once per kind.
  - Past `QUEUE_CAP`, the oldest draft is dropped, with a log line every 1,000 drops.
  - `_tick` drains at most `BATCH` drafts, then reschedules through `root.after`. If `root.after`
    raises, the writer stops quietly.
  - `drain` resolves each draft's identity. A broken key file disables that commander for the rest
    of the run (logged once) and is never replaced.
  - A failing batch is retried one draft at a time, and a failing draft is dropped and logged.
- **Manual API.**
  - It needs a known commander (`RuntimeError` otherwise).
  - `edit_*`/`delete_*` flush the writer, then read the own author's current revision. An unknown or
    deleted id raises `KeyError`, and unknown fields raise `TypeError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_crew_install.py`:

```python
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from crew_helpers import ADDR, ts, visit
from edmrn.ext import crew as crew_module
from edmrn.ext import crew_events as ev
from edmrn.ext.crew import Writer, install
from edmrn.ext.crew_identity import KeyRing
from edmrn.ext.crew_status import IN_MAIN_SHIP
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.location import journal_dir_for
from edmrn.ext.store import Store
from edmrn.fuel_tracker import FuelTracker
from edmrn.journal import JournalMonitor
from journal_helpers import append_events, event, journal_file, wait_for

SOL = 10477373803
LOAD = event("LoadGame", "2026-10-12T09:59:00Z", Commander="Jameson", FID="F100")
JUMP = event("FSDJump", "2026-10-12T10:00:00Z", StarSystem="Sol", SystemAddress=SOL, StarPos=[0, 0, 0])


class ManualRoot:
    """Records after() calls; fire() runs the oldest one, like one turn of the Tk loop."""

    def __init__(self):
        self.calls = []

    def after(self, ms, fn, *args):
        self.calls.append((ms, fn, args))

    def fire(self):
        ms, fn, args = self.calls.pop(0)
        fn(*args)


class DeadRoot:
    def after(self, ms, fn, *args):
        raise RuntimeError("main thread is not in main loop")


@pytest.fixture(autouse=True)
def listeners(monkeypatch):
    lists = SimpleNamespace(journal=[], status=[])
    monkeypatch.setattr(JournalMonitor, "event_listeners", lists.journal)
    monkeypatch.setattr(FuelTracker, "status_listeners", lists.status)
    return lists


@pytest.fixture
def store(tmp_path):
    (tmp_path / "data").mkdir()
    s = Store(tmp_path / "data" / "edmrn.db")
    yield s
    s.close()


def make_app(store, journal_dir, root=None):
    journal_dir.mkdir(exist_ok=True)
    return SimpleNamespace(root=root or ManualRoot(), edmrn_store=store, edmrn_store_error=None,
                           config=SimpleNamespace(journal_path=str(journal_dir)), location_tracker=None)


def start(store, tmp_path, root=None):
    app = make_app(store, tmp_path / "journals", root)
    service = install(app)
    assert service is not None and wait_for(lambda: service.live.seeded)
    return app, service


def feed(listeners, *events):
    for e in events:
        for listener in list(listeners.journal):
            listener(e)


def log_kinds(store):
    with store.read() as conn:
        return [row[0] for row in conn.execute("SELECT kind FROM crew_log ORDER BY created_at, seq")]


def writer_for(store, tmp_path, crew_store=None):
    root = ManualRoot()
    return Writer(crew_store or CrewStore(store), KeyRing(tmp_path / "keys"), root), root


# --- install ----------------------------------------------------------------------------

def test_install_registers_listeners_and_schedules_the_writer(store, tmp_path, listeners):
    app, service = start(store, tmp_path)
    assert app.crew is service
    assert listeners.journal == [service.live.feed]
    assert listeners.status == [service.ship.feed_status]
    assert app.root.calls[0][0] == Writer.INTERVAL_MS and service.writer.running


def test_listeners_only_enqueue_and_the_writer_drains_on_the_tk_loop(store, tmp_path, listeners):
    app, service = start(store, tmp_path)
    feed(listeners, LOAD, JUMP)
    assert log_kinds(store) == [] and service.writer.pending == 1
    app.root.fire()
    assert log_kinds(store) == ["visit"] and service.writer.pending == 0
    assert len(app.root.calls) == 1
    assert len(list((tmp_path / "data" / "crew_keys").glob("cmdr-*.json"))) == 1


def test_install_is_idempotent(store, tmp_path, listeners):
    app, service = start(store, tmp_path)
    assert install(app) is service
    assert len(listeners.journal) == 1 and len(listeners.status) == 1


def test_an_unusable_store_disables_crew_without_raising(tmp_path, listeners):
    app = make_app(None, tmp_path / "journals")
    app.edmrn_store_error = "database disk image is malformed"
    assert install(app) is None
    assert app.crew is None and listeners.journal == [] and listeners.status == []


def test_writer_stops_when_root_is_gone(store, tmp_path, listeners):
    app, service = start(store, tmp_path, root=DeadRoot())
    assert service.writer.running is False
    feed(listeners, LOAD, JUMP)
    assert service.writer.pending == 1


def test_status_json_reaches_the_ship_status(store, tmp_path, listeners):
    app, service = start(store, tmp_path)
    for listener in listeners.status:
        listener({"timestamp": "2026-10-12T10:00:00Z", "Flags": IN_MAIN_SHIP,
                  "Fuel": {"FuelMain": 8.0, "FuelReservoir": 0.4}})
    assert service.ship.state.fuel_main == 8.0


def test_status_combines_location_and_ship(store, tmp_path, listeners):
    app, service = start(store, tmp_path)
    app.location_tracker = SimpleNamespace(state="LOCATION")
    feed(listeners, event("Loadout", "2026-10-12T10:00:00Z", Ship="sidewinder", HullHealth=1.0,
                          FuelCapacity={"Main": 2.0, "Reserve": 0.3}))
    location, ship = service.status()
    assert location == "LOCATION" and ship.ship == "sidewinder"


def test_journal_dir_for_prefers_the_live_monitor_then_the_setting(tmp_path):
    with_monitor = SimpleNamespace(journal_monitor=SimpleNamespace(journal_path="X:/live"),
                                   config=SimpleNamespace(journal_path=str(tmp_path)))
    assert journal_dir_for(with_monitor) == "X:/live"
    assert journal_dir_for(SimpleNamespace(config=SimpleNamespace(journal_path=str(tmp_path)))) == str(tmp_path)


def test_app_installs_crew_right_after_location():
    """Source-level check: importing edmrn.app needs Tk and replaces sys.excepthook."""
    tree = ast.parse((Path(__file__).parent.parent / "edmrn" / "app.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "EDMRN_App")
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    calls = {getattr(n.func, "id", None): n.lineno for n in ast.walk(init) if isinstance(n, ast.Call)}
    assert calls["install_edmrn_crew"] > calls["install_edmrn_location"]
    imports = [n for n in ast.walk(init) if isinstance(n, ast.ImportFrom) and n.module == "edmrn.ext.crew"]
    assert imports and imports[0].names[0].name == "install"


# --- writer -----------------------------------------------------------------------------

def test_the_writer_takes_at_most_500_drafts_per_tick(store, tmp_path):
    writer, root = writer_for(store, tmp_path)
    for i in range(600):
        writer.enqueue(visit(at=ts(i // 60, i % 60)))
    writer.start()
    root.fire()
    assert len(log_kinds(store)) == 500 and writer.pending == 100
    root.fire()
    assert len(log_kinds(store)) == 600 and writer.pending == 0


class PoisonStore(CrewStore):
    def append_own(self, items):
        if any(draft.payload["system"] == "Poison" for _, draft in items):
            raise RuntimeError("poison")
        return super().append_own(items)


def test_a_poison_draft_is_dropped_without_losing_the_rest_of_its_batch(store, tmp_path):
    writer, root = writer_for(store, tmp_path, PoisonStore(store))
    for i, system in enumerate(["A", "Poison", "B", "C"]):
        writer.enqueue(visit(at=ts(0, i), system=system, address=str(100 + i)))
    assert writer.drain() == 3 and writer.pending == 0
    with store.read() as conn:
        assert sorted(r[0] for r in conn.execute("SELECT name FROM crew_systems")) == ["A", "B", "C"]


def test_queue_overflow_drops_the_oldest(store, tmp_path):
    writer, root = writer_for(store, tmp_path)
    writer.QUEUE_CAP = 3
    for i in range(5):
        writer.enqueue(visit(at=ts(0, i)))
    writer.drain()
    with store.read() as conn:
        assert [r[0] for r in conn.execute("SELECT created_at FROM crew_log ORDER BY seq")] == [
            ts(0, 2), ts(0, 3), ts(0, 4)]


def test_invalid_drafts_are_dropped_and_logged_once_per_kind(store, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(crew_module.logger, "warning", warnings.append)
    writer, root = writer_for(store, tmp_path)
    for _ in range(3):
        writer.enqueue(ev.Draft("visit", "F1", ts(0), {"system": "Sol"}))
    assert writer.pending == 0 and len(warnings) == 1


def test_a_broken_key_file_disables_only_that_commander(store, tmp_path):
    writer, root = writer_for(store, tmp_path)
    broken = KeyRing(tmp_path / "keys").path_for("F1")
    broken.parent.mkdir(parents=True)
    broken.write_text("garbage", encoding="utf-8")
    writer.enqueue(visit("F1"))
    writer.enqueue(visit("F2"))
    writer.enqueue(visit("F1", at=ts(1)))
    assert writer.drain() == 1
    assert broken.read_text(encoding="utf-8") == "garbage"


# --- manual API -------------------------------------------------------------------------

def test_manual_api_round_trip(store, tmp_path, listeners):
    app, service = start(store, tmp_path)
    feed(listeners, LOAD)
    find_id = service.report_find("other", "Sol", ADDR, note="Wreck")
    service.edit_find(find_id, note="Wreck, two pods")
    service.writer.drain()
    [find] = service.store.finds()
    assert (find.find_id, find.note, find.author) == (find_id, "Wreck, two pods", service.identity().author)
    service.delete_find(find_id)
    service.writer.drain()
    assert service.store.finds() == []
    note_id = service.set_note("Sol", ADDR, "Nice rings")
    assert service.set_note("Sol", ADDR, "Nicer rings", note_id=note_id) == note_id
    service.writer.drain()
    assert [n.text for n in service.store.notes(ADDR)] == ["Nicer rings"]
    service.delete_note(note_id)
    service.writer.drain()
    assert service.store.notes() == []


def test_manual_api_rejects_unknown_ids_fields_and_bad_text(store, tmp_path, listeners):
    app, service = start(store, tmp_path)
    feed(listeners, LOAD)
    with pytest.raises(KeyError):
        service.edit_find(ev.uuid7(), note="x")
    with pytest.raises(KeyError):
        service.delete_note(ev.uuid7())
    find_id = service.report_find("other", "Sol", ADDR)
    with pytest.raises(TypeError):
        service.edit_find(find_id, colour="red")
    with pytest.raises(ev.PayloadError):
        service.set_note("Sol", ADDR, "bad\u202e")


def test_manual_api_without_a_commander_raises(store, tmp_path):
    app, service = start(store, tmp_path)
    with pytest.raises(RuntimeError):
        service.report_find("other", "Sol", ADDR)
```

(`append_events` and `journal_file` are used by Task 12's tests in this file.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_install.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'edmrn.ext.crew'`.

- [ ] **Step 3: Extract `journal_dir_for` in `location.py`**

In `edmrn/ext/location.py`, add after `tracked_system`:

```python
def journal_dir_for(app) -> Optional[str]:
    """The journal folder the live monitor uses, including the manual journal_path setting."""
    manual_path = getattr(getattr(app, 'config', None), 'journal_path', '') or None
    live_dir = getattr(getattr(app, 'journal_monitor', None), 'journal_path', None)
    return live_dir or JournalMonitor(None, manual_journal_path=manual_path).journal_path
```

In `install`, replace the three lines

```python
    manual_path = getattr(getattr(app, 'config', None), 'journal_path', '') or None
    live_dir = getattr(getattr(app, 'journal_monitor', None), 'journal_path', None)
    journal_dir = live_dir or JournalMonitor(None, manual_journal_path=manual_path).journal_path
```

with `journal_dir = journal_dir_for(app)`.

- [ ] **Step 4: Write `crew.py`**

Create `edmrn/ext/crew.py`:

```python
"""Crew capture wiring: the Tk-loop writer, `app.crew` and install (Spec A, Design).

Listeners and the replay only queue drafts; the writer alone signs and inserts, on the Tk thread.
"""
from __future__ import annotations

import threading
from collections import deque
from pathlib import Path
from typing import Any, Optional

from edmrn.ext import crew_events as ev
from edmrn.ext.crew_capture import LiveCapture, MapperState, find_revision, note_revision, replay
from edmrn.ext.crew_identity import Identity, KeyFileError, KeyRing
from edmrn.ext.crew_status import CrewStatus, ShipStatus
from edmrn.ext.crew_store import CrewStore, FindRow, NoteRow
from edmrn.ext.location import journal_dir_for
from edmrn.fuel_tracker import FuelTracker
from edmrn.journal import JournalMonitor
from edmrn.logger import get_logger

logger = get_logger('Crew')


class Writer:
    INTERVAL_MS = 1000
    BATCH = 500
    QUEUE_CAP = 10_000

    def __init__(self, crew_store: CrewStore, keyring: KeyRing, root: Any) -> None:
        self._crew_store = crew_store
        self._keyring = keyring
        self._root = root
        self._queue: deque = deque()
        self._lock = threading.Lock()
        self._dropped = 0
        self._bad_kinds: set[str] = set()
        self._broken: set[str] = set()
        self._running = False

    @property
    def pending(self) -> int:
        return len(self._queue)

    @property
    def running(self) -> bool:
        return self._running

    def enqueue(self, draft: ev.Draft) -> None:
        """Any thread. An invalid draft is dropped here, logged once per kind."""
        try:
            ev.validate_payload(draft.kind, ev.ENVELOPE_VERSION, draft.payload)
        except ev.PayloadError as e:
            if draft.kind not in self._bad_kinds:
                self._bad_kinds.add(draft.kind)
                logger.warning(f"Crew {draft.kind} draft dropped: {e}")
            return
        with self._lock:
            if len(self._queue) >= self.QUEUE_CAP:
                self._queue.popleft()
                self._dropped += 1
                if self._dropped % 1000 == 1:
                    logger.warning(f"Crew queue full; {self._dropped} oldest drafts dropped so far")
            self._queue.append(draft)

    def start(self) -> None:
        self._running = True
        self._schedule()

    def stop(self) -> None:
        self._running = False

    def _schedule(self) -> None:
        if not self._running:
            return
        try:
            self._root.after(self.INTERVAL_MS, self._tick)
        except Exception as e:  # the Tk loop is gone: the app is closing
            self._running = False
            logger.debug(f"Crew writer stopped: {e}")

    def _tick(self) -> None:
        try:
            self.drain(self.BATCH)
        except Exception as e:
            logger.error(f"Crew writer tick failed: {e}")
        self._schedule()

    def drain(self, limit: Optional[int] = None) -> int:
        """Sign and log queued drafts now (Tk thread). Returns how many were written."""
        with self._lock:
            count = len(self._queue) if limit is None else min(limit, len(self._queue))
            drafts = [self._queue.popleft() for _ in range(count)]
        items = []
        for draft in drafts:
            identity = self._identity(draft.commander)
            if identity is not None:
                items.append((identity, draft))
        if not items:
            return 0
        try:
            return self._crew_store.append_own(items)
        except Exception as e:
            logger.warning(f"Crew batch of {len(items)} failed ({e}); retrying one by one")
        written = 0
        for item in items:
            try:
                written += self._crew_store.append_own([item])
            except Exception as e:
                logger.error(f"Crew {item[1].kind} draft dropped: {e}")
        return written

    def _identity(self, commander: Any) -> Optional[Identity]:
        if not isinstance(commander, str) or commander in self._broken:
            return None
        try:
            return self._keyring.for_commander(commander)
        except (KeyFileError, OSError) as e:
            self._broken.add(commander)
            logger.error(f"Crew capture disabled for one commander: {e}")
            return None


class CrewService:
    """`app.crew`: the queries (`store`), the manual API, the status and identities."""

    def __init__(self, crew_store: CrewStore, keyring: KeyRing, writer: Writer, live: LiveCapture,
                 ship: CrewStatus, app: Any = None) -> None:
        self.store = crew_store
        self.keyring = keyring
        self.writer = writer
        self.live = live
        self.ship = ship
        self._app = app

    @property
    def commander(self) -> Optional[str]:
        return self.live.mapper.state.commander

    def identity(self) -> Optional[Identity]:
        commander = self.commander
        if commander is None:
            return None
        try:
            return self.keyring.for_commander(commander)
        except (KeyFileError, OSError):
            return None

    def status(self) -> tuple[Any, ShipStatus]:
        """(location state or None, ship status) of the active commander (C9)."""
        tracker = getattr(self._app, 'location_tracker', None)
        return (tracker.state if tracker is not None else None), self.ship.state

    def report_find(self, find_kind: str, system: Optional[str], address: str,
                    body_name: Optional[str] = None, body_id: Optional[int] = None,
                    note: str = '') -> str:
        commander, _ = self._me()
        draft = find_revision(commander, ev.uuid7(), find_kind, system, address, body_name, body_id, note)
        self.writer.enqueue(draft)
        return draft.payload['find_id']

    def edit_find(self, find_id: str, **changes: Any) -> None:
        commander, identity = self._me()
        current = self._current_find(identity, find_id)
        fields = {'find_kind': current.find_kind, 'system': current.system,
                  'address': current.address, 'body_name': current.body_name,
                  'body_id': current.body_id, 'note': current.note}
        unknown = set(changes) - set(fields)
        if unknown:
            raise TypeError(f'unknown find fields: {sorted(unknown)}')
        fields.update(changes)
        self.writer.enqueue(find_revision(commander, find_id, **fields))

    def delete_find(self, find_id: str) -> None:
        commander, identity = self._me()
        current = self._current_find(identity, find_id)
        self.writer.enqueue(find_revision(commander, find_id, current.find_kind, current.system,
                                          current.address, current.body_name, current.body_id,
                                          current.note, deleted=True))

    def set_note(self, system: Optional[str], address: str, text: str,
                 note_id: Optional[str] = None) -> str:
        commander, identity = self._me()
        if note_id is not None:
            self._current_note(identity, note_id)
        note_id = note_id or ev.uuid7()
        self.writer.enqueue(note_revision(commander, note_id, system, address, text))
        return note_id

    def delete_note(self, note_id: str) -> None:
        commander, identity = self._me()
        current = self._current_note(identity, note_id)
        self.writer.enqueue(note_revision(commander, note_id, current.system, current.address,
                                          current.text, deleted=True))

    def _me(self) -> tuple[str, Identity]:
        commander, identity = self.commander, self.identity()
        if commander is None or identity is None:
            raise RuntimeError('no commander is known yet')
        return commander, identity

    def _current_find(self, identity: Identity, find_id: str) -> FindRow:
        self.writer.drain()
        row = self.store.manual_find(identity.author, find_id)
        if row is None or row.deleted:
            raise KeyError(find_id)
        return row

    def _current_note(self, identity: Identity, note_id: str) -> NoteRow:
        self.writer.drain()
        row = self.store.note(identity.author, note_id)
        if row is None or row.deleted:
            raise KeyError(note_id)
        return row


def _startup_replay(journal_dir: Optional[str], live: LiveCapture, writer: Writer,
                    status: CrewStatus) -> None:
    state: Optional[MapperState] = None
    try:
        state = replay(journal_dir, writer.enqueue, status)
    except Exception as e:
        logger.error(f"Crew startup replay failed: {e}")
    finally:
        live.seed(state)


def install(app: Any) -> Optional[CrewService]:
    """Wire crew capture into a running EDMRN_App. Called once from EDMRN_App.__init__."""
    existing = getattr(app, 'crew', None)
    if isinstance(existing, CrewService):
        return existing
    store = getattr(app, 'edmrn_store', None)
    if store is None:
        reason = getattr(app, 'edmrn_store_error', None) or 'no store'
        logger.error(f"Crew capture disabled: {reason}")
        app.crew = None
        return None
    try:
        keyring = KeyRing(Path(store.path).parent / 'crew_keys')
        crew_store = CrewStore(store)
        status = CrewStatus()
        writer = Writer(crew_store, keyring, app.root)
        live = LiveCapture(writer.enqueue, status)
        service = CrewService(crew_store, keyring, writer, live, status, app)
        journal_dir = journal_dir_for(app)
    except Exception as e:  # the hook must never abort EDMRN_App.__init__
        logger.error(f"Crew capture disabled: {e}")
        app.crew = None
        return None
    JournalMonitor.event_listeners.append(live.feed)
    FuelTracker.status_listeners.append(status.feed_status)
    app.crew = service
    writer.start()
    threading.Thread(target=_startup_replay, args=(journal_dir, live, writer, status),
                     name='edmrn-crew-replay', daemon=True).start()
    return service
```

- [ ] **Step 5: Add the two-line hook to `app.py` (CRLF)**

In `edmrn/app.py` `EDMRN_App.__init__`, directly after `install_edmrn_location(self)`, insert:

```python
        from edmrn.ext.crew import install as install_edmrn_crew
        install_edmrn_crew(self)
```

Check: `file edmrn/app.py` reports "with CRLF line terminators"; `git diff --stat edmrn/app.py`
shows 2 insertions.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_install.py tests/test_location_install.py tests/test_location_tracker.py -q`
Expected: all pass.

- [ ] **Step 7: Mutation check**

In `Writer._schedule`, temporarily remove the `try`/`except` so that `self._root.after(...)` is
called bare. Run the tests. Expected: `test_writer_stops_when_root_is_gone` fails with
`RuntimeError: main thread is not in main loop`. Revert.

- [ ] **Step 8: Update the docs**

In `docs/architecture/overview.md`:

1. In the "Upstream hook points" table, after the `edmrn/app.py` (location) row, add:

```
| `edmrn/app.py` (crew) | Two lines directly after `install_edmrn_location(self)` in `EDMRN_App.__init__`: `edmrn.ext.crew.install(self)` | Wires crew capture: journal and `Status.json` listeners, the Tk-loop writer, the startup replay (crew capture spec) | no |
```

2. Add this section after "Location data flow":

```
## Crew capture data flow (crew capture spec, ADR-0005, ADR-0006)

1. Upstream's `JournalMonitor` hands each event to `LiveCapture.feed` (`event_listeners`).
   Upstream's `FuelTracker` hands each `Status.json` dict to `CrewStatus.feed_status`
   (`status_listeners`).
2. `JournalMapper` turns events into unsigned drafts, each attributed to the commander who was
   active when the line was written. `CrewStatus` keeps ship, hull and fuel; it is never logged.
3. The `Writer` takes drafts from any thread. Once a second, on the Tk loop, it signs at most 500
   with the commander's key from `crew_keys/` and inserts them into `crew_log` in one transaction.
   Each new event is projected into the `crew_*` tables.
4. At startup, `replay` maps the newest 5 journals in the `edmrn-crew-replay` thread. Deterministic
   ids mean it adds only what EDMRN missed.
   - The monitor reads the newest journal from its end and never sees that session's `LoadGame`.
     The live mapper therefore buffers events until the replay hands it its final state.

Invariants:
- Only the writer (own events) and `CrewStore.insert_received` (received events) write
  `crew_log`. Projections change only through `crew_store._project`, once per `event_id`.
- Every projection merge is independent of arrival order. `rebuild_projections()` re-derives the
  same tables from the log.
- Key files live in `crew_keys/` next to `edmrn.db`. An unusable key file disables capture for
  that commander and is never replaced.
- `app.crew` is `None` when the store is unusable (ADR-0004).
```

- [ ] **Step 9: Run the whole suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`. Expected: all pass.

```bash
git add edmrn/ext/crew.py edmrn/ext/location.py edmrn/app.py tests/test_crew_install.py docs/architecture/overview.md
git commit -m "feat(crew): Tk-loop writer, app.crew service and install hook

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 12: End to end and the isolated smoke run

**Files:**
- Test: `tests/test_crew_install.py` (append)
- Modify: `docs/how-to/run.md` (crew smoke check)

**Interfaces:**
- Consumes: everything above, through `install(app)` and the listener lists only.

- [ ] **Step 1: Write the end-to-end tests**

Append to `tests/test_crew_install.py`:

```python
# --- end to end -------------------------------------------------------------------------

def at(minute, second=0):
    return f"2026-10-12T10:{minute:02d}:{second:02d}Z"


SESSION = [
    event("Fileheader", "2026-10-12T09:58:00Z", part=1, gameversion="4.4.0.0"),
    event("Commander", "2026-10-12T09:58:30Z", FID="F100", Name="Jameson"),
    LOAD,
    event("Loadout", "2026-10-12T09:59:10Z", Ship="krait_light", ShipName="Wanderer", ShipIdent="DR-01",
          HullHealth=0.92, FuelCapacity={"Main": 32.0, "Reserve": 0.63}, Modules=[]),
    JUMP,
    event("FSSDiscoveryScan", at(0, 30), Progress=0.5, BodyCount=3, NonBodyCount=1, SystemName="Sol",
          SystemAddress=SOL),
    event("FSSSignalDiscovered", at(0, 31), SystemAddress=SOL, SignalName="$Fixed_Event_Life_Cloud;",
          SignalType="Codex"),
    event("FSSSignalDiscovered", at(0, 32), SystemAddress=SOL, SignalName="$Fixed_Event_Life_Cloud;",
          SignalType="Codex"),
    event("Scan", at(1), ScanType="AutoScan", BodyName="Sol", BodyID=0, StarSystem="Sol", SystemAddress=SOL,
          StarType="G", WasDiscovered=True, WasMapped=False),
    event("FSSBodySignals", at(2), BodyName="Sol 3", BodyID=3, SystemAddress=SOL,
          Signals=[{"Type": "$SAA_SignalType_Biological;", "Count": 2}]),
    event("Scan", at(2, 1), ScanType="Detailed", BodyName="Sol 3", BodyID=3, Parents=[{"Star": 0}],
          StarSystem="Sol", SystemAddress=SOL, PlanetClass="Earthlike body", Landable=False,
          TerraformState="Terraformed", WasDiscovered=True, WasMapped=True),
    event("Scan", at(3), ScanType="Detailed", BodyName="Sol 4", BodyID=4, Parents=[{"Star": 0}],
          StarSystem="Sol", SystemAddress=SOL, PlanetClass="Rocky body", Landable=True, TerraformState="",
          WasDiscovered=False, WasMapped=False),
    event("FSSAllBodiesFound", at(3, 1), SystemName="Sol", SystemAddress=SOL, Count=3),
    event("SAASignalsFound", at(10), BodyName="Sol 3", BodyID=3, SystemAddress=SOL,
          Signals=[{"Type": "$SAA_SignalType_Biological;", "Count": 2}, {"Type": "$SAA_SignalType_Human;", "Count": 1}],
          Genuses=[{"Genus": "$Codex_Ent_Bacterial_Genus_Name;"}, {"Genus": "$Codex_Ent_Stratum_Genus_Name;"}]),
    event("CodexEntry", at(20), EntryID=2420101, Name="$Codex_Ent_Gas_Clds_Yellow_Name;",
          SubCategory="$Codex_SubCategory_Organic_Structures;", Category="$Codex_Category_Biology;",
          Region="$Codex_RegionName_18;", System="Sol", SystemAddress=SOL, BodyID=3,
          NearestDestination="$Fixed_Event_Life_Cloud;"),
    event("HullDamage", at(30), Health=0.61, PlayerPilot=True, Fighter=False),
]


def write_session(tmp_path):
    journals = tmp_path / "journals"
    journals.mkdir(exist_ok=True)
    append_events(journal_file(journals, "2026-10-12T095800"), *SESSION)


def test_a_session_journal_becomes_explored_systems_bodies_signals_finds_and_status(store, tmp_path, listeners):
    write_session(tmp_path)
    app, service = start(store, tmp_path)
    app.root.fire()
    me = service.identity().author
    address = str(SOL)
    assert service.commander == "F100"
    assert service.store.explored(me) == {address}
    assert service.store.system(address).body_count == 3
    assert [b.name for b in service.store.bodies(address)] == ["Sol", "Sol 3", "Sol 4"]
    s3 = service.store.signals(address)[3]
    assert s3.counts == {"biological": 2, "human": 1} and s3.sources == frozenset({"fss", "saa"})
    assert s3.genera == ("$Codex_Ent_Bacterial_Genus_Name;", "$Codex_Ent_Stratum_Genus_Name;")
    finds = service.store.finds()
    assert sorted(f.find_kind for f in finds) == ["biological", "codex", "stellar_phenomenon", "valuable_body"]
    by_kind = {f.find_kind: f for f in finds}
    assert by_kind["codex"].body_name == "Sol 3" and by_kind["valuable_body"].body_name == "Sol 3"
    ship = service.ship.state
    assert (ship.ship, ship.hull, ship.fuel_capacity_main) == ("krait_light", 0.61, 32.0)


def test_a_restart_adds_nothing_and_live_events_continue(store, tmp_path, listeners):
    write_session(tmp_path)
    app, service = start(store, tmp_path)
    app.root.fire()
    before = log_kinds(store)
    listeners.journal.clear()
    listeners.status.clear()
    app2, service2 = start(store, tmp_path)
    app2.root.fire()
    assert log_kinds(store) == before
    feed(listeners, event("FSDJump", at(40), StarSystem="Alpha", SystemAddress=42, StarPos=[1, 2, 3]))
    app2.root.fire()
    assert log_kinds(store) == before + ["visit"]
    assert service2.store.visits(service2.identity().author)[0].address == "42"
```

- [ ] **Step 2: Run them**

Run: `.venv/Scripts/python.exe -m pytest tests/test_crew_install.py -q`
Expected: all pass. If one fails, the defect is in an earlier task's code. Fix it there with a test
that pins it in that task's test file, and say so in the report.

- [ ] **Step 3: Isolated smoke run of the real app**

From Git Bash, with `H` an empty temp directory outside the repository:

```bash
H=$(mktemp -d)
J="$H/Saved Games/Frontier Developments/Elite Dangerous"
mkdir -p "$J"
printf '%s\n' \
  '{"timestamp":"2026-10-12T10:00:00Z","event":"LoadGame","Commander":"Smoke","FID":"F1"}' \
  '{"timestamp":"2026-10-12T10:01:00Z","event":"FSDJump","StarSystem":"Sol","SystemAddress":10477373803,"StarPos":[0,0,0]}' \
  > "$J/Journal.2026-10-12T100000.01.log"
HOME="$H" USERPROFILE="$H" APPDATA="$H" LOCALAPPDATA="$H" timeout 25 .venv/Scripts/python.exe -u main.py
ls "$H/EDMRN_Route_Data/crew_keys"
.venv/Scripts/python.exe -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('select kind from crew_log').fetchall())" "$H/EDMRN_Route_Data/edmrn.db"
```

Expected:
- exit code 124 (the timeout ending a healthy run);
- one `cmdr-*.json` in `crew_keys`;
- `[('visit',)]`.

If upstream finds no journal folder under `$H`, check `edmrn/utils.py` `get_ed_journal_dir` for
the path it expects. Use that path, and write the correct one into `run.md` in Step 4.

- [ ] **Step 4: Document the smoke check**

In `docs/how-to/run.md`, at the end of "Start it isolated (smoke tests, agents)", add:

````
To check crew capture in such a run, write a journal into the default journal folder under `$H`
before starting:

```
J="$H/Saved Games/Frontier Developments/Elite Dangerous"; mkdir -p "$J"
printf '%s\n' '{"timestamp":"2026-10-12T10:00:00Z","event":"LoadGame","Commander":"Smoke","FID":"F1"}' \
  '{"timestamp":"2026-10-12T10:01:00Z","event":"FSDJump","StarSystem":"Sol","SystemAddress":10477373803,"StarPos":[0,0,0]}' \
  > "$J/Journal.2026-10-12T100000.01.log"
```

After the run, `$H/EDMRN_Route_Data/crew_keys/` holds one key file and the `crew_log` table of
`$H/EDMRN_Route_Data/edmrn.db` holds one `visit`.
````

(Use the path Step 3 confirmed.)

- [ ] **Step 5: Run the whole suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`. Expected: all pass.

```bash
git add tests/test_crew_install.py docs/how-to/run.md
git commit -m "test(crew): end-to-end capture from a session journal; smoke check in run.md

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

## After the last task (controller)

- Whole-branch review on the most capable model. It should execute, not only read:
  - feed hostile envelopes to `insert_received`;
  - kill a process mid-batch and reopen the store;
  - shuffle arrival orders;
  - mutate the merge rules.
- Milestone sweep (`docs/README.md` rule 7): re-read `CLAUDE.md`, `docs/architecture/`,
  `docs/how-to/`, `docs/knowledge/`, and the ADR index, and correct what this branch falsified.
- Rewrite `docs/handover/HANDOVER.md`:
  - state, test count, and the deferred findings;
  - next: Spec B (sync). The crew requirements block there still feeds Specs B and C.
- Stop before merging into `main` and ask the commander.
