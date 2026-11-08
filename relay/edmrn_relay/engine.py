"""Protocol engine of the crew relay (ADR-0009).

Synchronous and free of I/O: every method returns the actions (frames to send, sockets to close) in
the order they must reach the peers. Every rule shared with the client comes from the shared
modules; this file holds the relay's decisions only.
"""
from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from edmrn.ext import crew_events as ev
from edmrn.ext import crew_protocol as proto
from edmrn.ext.crew_identity import verify
from edmrn.ext.crew_roster import Roster, RosterEnvelope, compute_roster, parse_roster_envelope

from .config import RelayConfig
from .store import LogRow, RelayStore

log = logging.getLogger("edmrn_relay")

AUTH_TIMEOUT = 10.0  # read at call time, so tests may monkeypatch it
STATUS_INTERVAL = 30.0
MAX_REFUSALS = 64
MAX_E_CHARS = -(-ev.MAX_ENVELOPE_BYTES * 4 // 3)
MAX_S_CHARS = -(-proto.SIGNATURE_BYTES * 4 // 3)
MAX_STATUS_CHARS = -(-proto.MAX_STATUS_BYTES * 4 // 3)
CLOSE_GOING_AWAY, CLOSE_INTERNAL = 1001, 1011


def utc_day(now: float) -> int:
    return int(now // 86400)


def _short(author: str | None) -> str:
    return (author or "")[:12]


@dataclass(eq=False)
class Session:
    group_id: str
    conn: Any
    nonce: str
    opened_at: float
    state: str = "hello"  # "hello" | "member" | "closed"
    author: str | None = None
    status: tuple[bytes, bytes] | None = None
    refused: dict[str, tuple[int, int]] = field(default_factory=dict)
    closing: bool = False


@dataclass(eq=False)
class Group:
    group_id: str
    leader: str | None
    heads: dict[str, int]
    roster_entries: list[RosterEnvelope]
    roster: Roster | None
    sessions: list[Session]
    status_at: dict[str, float]
    cleaned_day: int = -1


@dataclass(frozen=True)
class Send:
    session: Session
    text: str


@dataclass(frozen=True)
class Close:
    session: Session
    code: int
    reason: str


Action = Send | Close


@dataclass(frozen=True)
class Checked:
    env: dict
    e: bytes
    s: bytes


@dataclass(frozen=True)
class BadSig:
    author: str
    seq: int


def decode_item(item: Any) -> Checked | BadSig | Literal["oversize", "undecodable"]:
    if not isinstance(item, dict):
        return "undecodable"
    e, s = item.get("e"), item.get("s")
    if not isinstance(e, str) or not isinstance(s, str):
        return "undecodable"
    if len(e) > MAX_E_CHARS or len(s) > MAX_S_CHARS:
        return "oversize"
    try:
        data = proto.unb64(e, ev.MAX_ENVELOPE_BYTES)
        env = ev.parse_envelope(data)
    except (proto.FrameError, ev.EnvelopeError):
        return "undecodable"
    try:
        sig = proto.unb64(s, proto.SIGNATURE_BYTES)
    except proto.FrameError:
        return BadSig(env["author"], env["seq"])
    if len(sig) != proto.SIGNATURE_BYTES:
        return BadSig(env["author"], env["seq"])
    return Checked(env, data, sig)


def roster_entry(checked: Checked, now: float) -> RosterEnvelope | None:
    return parse_roster_envelope(checked.e, frozenset(), datetime.fromtimestamp(now, timezone.utc))


def _err(code: str, author: str | None = None, seq: int | None = None) -> str:
    frame: dict = {"t": "err", "code": code}
    if author is not None:
        frame["author"] = author
    if seq is not None:
        frame["seq"] = seq
    return proto.encode(frame)


class Relay:
    def __init__(self, store: RelayStore, config: RelayConfig, *,
                 now: Callable[[], float] = time.time,
                 token: Callable[[], bytes] = lambda: secrets.token_bytes(proto.NONCE_BYTES)
                 ) -> None:
        self.store = store
        self.config = config
        self.groups: dict[str, Group] = {}
        self._now = now
        self._token = token

    # -- groups ---------------------------------------------------------------------------

    def group(self, group_id: str) -> Group:
        cached = self.groups.get(group_id)
        if cached is not None:
            return cached
        loaded = self.store.load_group(group_id)
        if loaded is None:
            group = Group(group_id, None, {}, [], None, [], {})
        else:
            group = Group(group_id, loaded.leader, dict(loaded.heads), [], None, [], {})
            self._set_roster(group, loaded.roster)
        self.groups[group_id] = group
        return group

    def reload_roster(self, group: Group) -> None:
        self._set_roster(group, self.store.roster_bytes(group.group_id))

    def _set_roster(self, group: Group, raws: list[bytes]) -> None:
        if group.leader is None:
            group.roster_entries, group.roster = [], None
            return
        when = datetime.fromtimestamp(self._now(), timezone.utc)
        entries = [parse_roster_envelope(raw, frozenset(), when) for raw in raws]
        group.roster_entries = [e for e in entries if e is not None]
        group.roster = compute_roster(group.roster_entries, group.group_id, group.leader)

    # -- sessions -------------------------------------------------------------------------

    def connect(self, group_id: str, conn: Any) -> tuple[Session, list[Action]]:
        if not ev.is_uuid7(group_id):
            raise ValueError("group id is not a UUIDv7")
        group = self.group(group_id)
        session = Session(group_id, conn, proto.b64(self._token()), self._now())
        group.sessions.append(session)
        hello = proto.encode({"t": "hello", "v": proto.PROTOCOL_VERSION,
                              "source": self.config.source_url, "nonce": session.nonce})
        return session, [Send(session, hello)]

    def expire_auth(self, session: Session) -> list[Action]:
        if session.state == "hello" and not session.closing:
            return self._close(session, proto.CLOSE_BAD_AUTH, "auth timeout")
        return []

    def disconnect(self, session: Session) -> None:
        if session.state == "closed":
            return
        session.state = "closed"
        group = self.groups.get(session.group_id)
        if group is None:
            return
        if session in group.sessions:
            group.sessions.remove(session)
        if not group.sessions and group.leader is None:
            del self.groups[session.group_id]

    def _close(self, session: Session, code: int, reason: str) -> list[Action]:
        session.closing = True
        log.info("close %d %s author=%s", code, reason, _short(session.author))
        return [Close(session, code, reason)]

    # -- dispatch -------------------------------------------------------------------------

    def receive(self, session: Session, message: str | bytes) -> list[Action]:
        if session.state == "closed" or session.closing:
            return []
        if session.state == "hello" and self._now() - session.opened_at > AUTH_TIMEOUT:
            return self._close(session, proto.CLOSE_BAD_AUTH, "auth timeout")
        if isinstance(message, (bytes, bytearray)):
            return self._close(session, proto.CLOSE_POLICY, "binary")
        if len(message.encode("utf-8", "surrogatepass")) > proto.MAX_FRAME_BYTES:
            return self._close(session, proto.CLOSE_POLICY, "too_big")
        try:
            frame = proto.decode(message)
        except proto.FrameError:
            return [Send(session, _err("bad_frame"))]
        if session.state == "hello":
            if frame["t"] == "auth":
                return self._auth(session, frame)
            return self._close(session, proto.CLOSE_BAD_AUTH, "auth first")
        return self._member_frame(session, frame)

    def _member_frame(self, session: Session, frame: dict) -> list[Action]:
        # Task 3 (have, evs) and Task 5 (st) are handled here; any other type is ignored.
        return []

    # -- auth -----------------------------------------------------------------------------

    def _auth(self, session: Session, frame: dict) -> list[Action]:
        group = self.group(session.group_id)
        author = frame.get("author")
        if frame.get("group") != session.group_id or not ev.is_author(author):
            return self._close(session, proto.CLOSE_BAD_AUTH, "bad auth")
        try:
            sig = proto.unb64(frame.get("sig"), proto.SIGNATURE_BYTES)
        except proto.FrameError:
            return self._close(session, proto.CLOSE_BAD_AUTH, "bad signature")
        if (len(sig) != proto.SIGNATURE_BYTES
                or not verify(author, sig, proto.auth_message(session.group_id, session.nonce))):
            return self._close(session, proto.CLOSE_BAD_AUTH, "bad signature")
        if group.leader is None:
            genesis = None
            if author in self.config.leaders:
                genesis = self._check_auth_envelope(session, frame.get("genesis"), author, "group")
            if genesis is None:
                return self._close(session, proto.CLOSE_FORBIDDEN, "no group")
            checked, _ = genesis
            self.store.create_group(session.group_id, author, int(self._now()),
                                    LogRow(author, checked.env["seq"], checked.e, checked.s))
            group.leader = author
            self.reload_roster(group)
            log.info("group created %s leader=%s", session.group_id, _short(author))
        # Task 4: join at auth.
        member = group.roster.members.get(author) if group.roster else None
        if member is None:
            return self._close(session, proto.CLOSE_FORBIDDEN, "not_member")
        if member.last_seq is not None:
            return self._close(session, proto.CLOSE_FORBIDDEN, "removed")
        actions: list[Action] = []
        for other in list(group.sessions):
            if (other is not session and other.state == "member" and other.author == author
                    and not other.closing):
                actions += self._close(other, proto.CLOSE_REPLACED, "replaced")
        session.state = "member"
        session.author = author
        # Task 4: a `have` to every other member session on admission.
        heads = {a: group.heads.get(a, 0) for a in group.roster.members}
        actions.append(Send(session, proto.encode(proto.have_frame(heads))))
        # Task 5: statuses.
        return actions

    def _check_auth_envelope(self, session: Session, raw: Any, author: str,
                             kind: str) -> tuple[Checked, RosterEnvelope] | None:
        checked = decode_item(raw)
        if not isinstance(checked, Checked):
            return None
        if checked.env["author"] != author or checked.env["kind"] != kind:
            return None
        entry = roster_entry(checked, self._now())
        if entry is None or entry.payload.get("group_id") != session.group_id:
            return None
        if not verify(author, checked.s, checked.e):
            return None
        return checked, entry
