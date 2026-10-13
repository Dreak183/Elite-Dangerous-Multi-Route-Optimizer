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
