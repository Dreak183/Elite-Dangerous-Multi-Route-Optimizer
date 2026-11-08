"""Harness of the relay tests: keys, envelopes, fake connections and a driver over the engine."""
from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_crew_vectors import _generator  # same module object as the collected test module

from edmrn.ext import crew_events as ev
from edmrn.ext import crew_protocol as proto
from edmrn_relay.config import RelayConfig
from edmrn_relay.engine import Action, Close, Relay, Send, Session
from edmrn_relay.store import RelayStore

TOKEN = "11" * 32
_KEYS: dict = {}
_counter = 0x1000


def key_for(name: str):
    """The vector key of that name: `.author` and `.sign(data) -> bytes`."""
    if name not in _KEYS:
        _KEYS[name] = _generator().Key(name)
    return _KEYS[name]


class _Stranger:
    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.author = proto.b64(self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw))

    def sign(self, data: bytes) -> bytes:
        return self.key.sign(data)


def strangers(n: int) -> list:
    return [_Stranger() for _ in range(n)]


def uuid7() -> str:
    global _counter
    _counter += 1
    return f"01920000-0000-7000-8000-{_counter:012x}"


def new_group() -> str:
    return uuid7()


def stamp_at(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope_item(key, seq: int, kind: str, payload: dict, created_at: str,
                  event_id: str | None = None) -> dict:
    env = {"v": 1, "event_id": event_id or uuid7(), "author": key.author, "seq": seq,
           "kind": kind, "created_at": created_at, "payload": payload}
    data = ev.canonical_bytes(env)
    return {"e": proto.b64(data), "s": proto.b64(key.sign(data))}


def auth_frame(key, group_id: str, nonce: str, **extra) -> dict:
    frame = {"t": "auth", "group": group_id, "author": key.author,
             "sig": proto.b64(key.sign(proto.auth_message(group_id, nonce)))}
    frame.update(extra)
    return frame


def status_frame(key, hull: float, at: str, author: str | None = None, **fields) -> dict:
    status = {name: None for name in ev.STATUS_FIELDS}
    status.update(commander="X", system="Sol", address="1", paused=False, hull=hull)
    status.update(fields)
    data = ev.canonical_bytes({"author": author or key.author, "at": at, "status": status})
    return proto.status_frame(data, key.sign(data))


def decode(item: dict) -> dict:
    return ev.parse_envelope(proto.unb64(item["e"], ev.MAX_ENVELOPE_BYTES))


def items_of(frames: list[dict]) -> list[dict]:
    return [item for f in frames if f.get("t") == "evs" for item in f["items"]]


class Clock:
    def __init__(self, start: float | None = None) -> None:
        self.now = time.time() if start is None else start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def next_utc_day(self) -> None:
        self.now = (self.now // 86400 + 1) * 86400 + 1


class FakeConn:
    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.closed: tuple[int, str] | None = None
        self.session: Session | None = None

    def next_of(self, t: str) -> dict:
        for i, frame in enumerate(self.frames):
            if frame.get("t") == t:
                del self.frames[:i + 1]
                return frame
        raise AssertionError(f"no {t!r} frame; have {[f.get('t') for f in self.frames]}")

    def take(self, t: str | None = None) -> list[dict]:
        frames, self.frames = self.frames, []
        return frames if t is None else [f for f in frames if f.get("t") == t]


class Harness:
    def __init__(self, db_path: Path, *, leaders: Iterable[str] | None = None,
                 member_quota: int = 5000, group_quota: int = 80000, clock: Clock | None = None,
                 token: Callable[[], bytes] | None = None,
                 source_url: str = "https://example.invalid/edmrn") -> None:
        self.db_path = Path(db_path)
        self.clock = clock or Clock()
        self.leaders = frozenset([key_for("leader").author] if leaders is None else leaders)
        self.member_quota, self.group_quota, self.source_url = member_quota, group_quota, source_url
        self._token = token
        self.store = RelayStore(self.db_path)
        self.relay = self._make_relay()

    def _make_relay(self) -> Relay:
        config = RelayConfig(db=self.db_path, leaders=self.leaders, source_url=self.source_url,
                             member_quota=self.member_quota, group_quota=self.group_quota)
        kwargs = {} if self._token is None else {"token": self._token}
        return Relay(self.store, config, now=self.clock, **kwargs)

    # -- driving the engine ---------------------------------------------------------------

    def _apply(self, actions: list[Action]) -> None:
        for action in actions:
            target = action.session.conn
            if isinstance(action, Send):
                target.frames.append(json.loads(action.text))
            else:
                target.closed = (action.code, action.reason)
                self.relay.disconnect(action.session)

    def open(self, group_id: str) -> FakeConn:
        conn = FakeConn()
        conn.session, actions = self.relay.connect(group_id, conn)
        self._apply(actions)
        return conn

    def send(self, conn: FakeConn, frame: dict | str | bytes) -> None:
        message = proto.encode(frame) if isinstance(frame, dict) else frame
        self._apply(self.relay.receive(self.session(conn), message))

    def close(self, conn: FakeConn) -> None:
        self.relay.disconnect(self.session(conn))

    def expire(self, conn: FakeConn) -> None:
        self._apply(self.relay.expire_auth(self.session(conn)))

    def session(self, conn: FakeConn) -> Session:
        assert conn.session is not None
        return conn.session

    # -- building frames ------------------------------------------------------------------

    def stamp(self, offset: float = 0.0) -> str:
        return stamp_at(self.clock.now + offset)

    def envelope(self, key, seq, kind, payload, created_at: str | None = None,
                 event_id=None) -> dict:
        return envelope_item(key, seq, kind, payload, created_at or self.stamp(-60), event_id)

    def visit(self, key, seq: int, created_at: str | None = None) -> dict:
        return self.envelope(key, seq, "visit", {"commander": "A", "system": "Sol",
                                                 "address": str(seq), "coords": None}, created_at)

    def genesis(self, key, group_id: str) -> dict:
        return self.envelope(key, 1, "group",
                             {"group_id": group_id, "name": "Crew", "commander": "Leader"})

    def authed(self, group_id: str, key, **extra) -> FakeConn:
        conn = self.open(group_id)
        hello = conn.next_of("hello")
        self.send(conn, auth_frame(key, group_id, hello["nonce"], **extra))
        return conn

    def create_group(self, group_id: str, key) -> FakeConn:
        return self.authed(group_id, key, genesis=self.genesis(key, group_id))

    # -- reading the database -------------------------------------------------------------

    def head(self, group_id: str, author: str) -> int | None:
        row = self.store.db.execute("SELECT seq FROM heads WHERE group_id = ? AND author = ?",
                                    (group_id, author)).fetchone()
        return None if row is None else row[0]

    def log_seqs(self, group_id: str, author: str) -> list[int]:
        return [r[0] for r in self.store.db.execute(
            "SELECT seq FROM log WHERE group_id = ? AND author = ? ORDER BY seq",
            (group_id, author))]

    def roster_seqs(self, group_id: str, author: str) -> list[int]:
        return [r[0] for r in self.store.db.execute(
            "SELECT seq FROM roster WHERE group_id = ? AND author = ? ORDER BY seq",
            (group_id, author))]

    def table_counts(self, group_id: str) -> dict[str, int]:
        return {t: self.store.db.execute(
            f"SELECT COUNT(*) FROM {t} WHERE group_id = ?", (group_id,)).fetchone()[0]
            for t in ("groups", "log", "heads", "roster", "quota", "status")}
