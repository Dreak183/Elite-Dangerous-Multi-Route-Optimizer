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
# libsodium's small-order blocklist, compared with the top bit of the last byte cleared.
_SMALL_ORDER = frozenset(bytes.fromhex(h) for h in (
    '00' * 32,
    '01' + '00' * 31,
    '26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05',
    'c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a',
    'ec' + 'ff' * 30 + '7f',
    'ed' + 'ff' * 30 + '7f',
    'ee' + 'ff' * 30 + '7f',
))


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
        if not isinstance(data, (bytes, bytearray)):
            return False
        raw = _raw32(author)
        if raw[:31] + bytes([raw[31] & 0x7f]) in _SMALL_ORDER:
            return False
        Ed25519PublicKey.from_public_bytes(raw).verify(bytes(signature), bytes(data))
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
        if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq <= MAX_SAFE_INT:
            raise ValueError('seq must be an integer from 0 to 2^53-1')
        if seq <= self.high_water:
            return
        if self.path.exists():
            try:
                disk_key, disk_hw = _parse_record(self.path.read_text(encoding='utf-8'))
                on_disk_author = _author_of(disk_key)
            except (OSError, UnicodeDecodeError, KeyFileError) as e:
                raise KeyFileError(f'{self.path.name}: {e}') from e
            if on_disk_author != self.author:
                raise KeyFileError(f'{self.path.name}: author changed on disk; key file not overwritten')
            seq = max(seq, disk_hw)
        seq = max(seq, self.high_water)
        _write(self.path, self._key, seq)
        self.high_water = seq

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
