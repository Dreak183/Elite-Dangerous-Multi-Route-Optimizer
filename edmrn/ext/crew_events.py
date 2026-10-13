"""Crew event envelopes: canonical bytes, ids, parsing and payload validation.

Spec: docs/superpowers/specs/2026-10-12-crew-capture-design.md (C2, C3, C12). Pure: no I/O, no Tk.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

ENVELOPE_VERSION = 1
MAX_ENVELOPE_BYTES = 16 * 1024
MAX_SAFE_INT = 2**53 - 1
ENVELOPE_KEYS = frozenset({'v', 'event_id', 'author', 'seq', 'kind', 'created_at', 'payload'})

_STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z')
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
