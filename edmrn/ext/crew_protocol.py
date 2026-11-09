"""Frames of the crew relay protocol (crew sync core spec S6-S9). Pure: no I/O.

Envelopes and signatures travel as base64url of the exact signed bytes and are never re-encoded.
The relay's side of the same protocol is `relay/edmrn_relay/engine.py`, which imports this module.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any, Iterable, Iterator, Optional

from edmrn.ext import crew_events as ev
from edmrn.ext.crew_identity import _raw32, b64url, verify

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
_T = re.compile(r'[ -~]{1,32}')
_ITEM_OVERHEAD = 16  # {"e":"","s":""}, per item


class FrameError(ValueError):
    """A frame that breaks the protocol."""


def _author(value: Any) -> bool:
    """True for the canonical spelling of an author key."""
    try:
        _raw32(value)
    except ValueError:
        return False
    return True


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
    if not _T.fullmatch(frame['t']):
        raise FrameError('a frame type is short printable ASCII')
    return frame


def auth_message(group_id: str, nonce: str) -> bytes:
    return f'edmrn-crew-auth\n{group_id}\n{nonce}'.encode('ascii')


def parse_hello(frame: dict) -> str:
    if type(frame.get('v')) is not int or frame['v'] != PROTOCOL_VERSION:
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
        if (not _author(author) or isinstance(seq, bool) or not isinstance(seq, int)
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
    total = 0
    for item in items:
        if not isinstance(item, dict) or set(item) != {'e', 's'}:
            raise FrameError('an item is {e, s}')
        data = unb64(item['e'], ev.MAX_ENVELOPE_BYTES)
        signature = unb64(item['s'], SIGNATURE_BYTES)
        if len(signature) != SIGNATURE_BYTES:
            raise FrameError('a signature is 64 bytes')
        total += len(item['e']) + len(item['s']) + _ITEM_OVERHEAD
        if total > MAX_EVS_BYTES:
            raise FrameError('items larger than 512 KiB')
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
    if ev.canonical_bytes(doc) != data:
        raise FrameError('status is not in canonical form')
    if not verify(doc['author'], signature, data):
        raise FrameError('status signature does not verify')
    return doc['author'], doc['at'], doc['status']


def parse_err(frame: dict) -> tuple:
    code = frame.get('code')
    if not isinstance(code, str) or code not in ERROR_CODES:
        return 'unknown', None, None
    author, seq = frame.get('author'), frame.get('seq')
    if not _author(author):
        return code, None, None
    if isinstance(seq, bool) or not isinstance(seq, int) or not 0 <= seq <= ev.MAX_SAFE_INT:
        return code, author, None  # a valid author still blocks; the caller resyncs without a seq
    return code, author, seq
