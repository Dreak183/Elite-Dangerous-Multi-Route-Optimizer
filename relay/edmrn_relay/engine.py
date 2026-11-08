"""Protocol engine of the crew relay (ADR-0009).

Synchronous and free of I/O: every method returns the actions (frames to send, sockets to close) in
the order they must reach the peers. Every rule shared with the client comes from the shared
modules; this file holds the relay's decisions only.
"""
from __future__ import annotations

import json
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
        try:
            return self._receive(session, message)
        except Exception:
            log.exception("internal error author=%s", _short(session.author))
            return self._close(session, CLOSE_INTERNAL, "internal error")

    def _receive(self, session: Session, message: str | bytes) -> list[Action]:
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
        # Task 5 adds `st`; any other type is ignored.
        if frame["t"] == "have":
            return self._have(session, frame)
        if frame["t"] == "evs":
            return self._evs(session, frame)
        return []

    # -- catch-up -------------------------------------------------------------------------

    def _have(self, session: Session, frame: dict) -> list[Action]:
        try:
            heads = proto.parse_have(frame)
        except proto.FrameError:
            return [Send(session, _err("bad_frame"))]
        group = self.group(session.group_id)
        members = group.roster.members if group.roster else {}
        actions: list[Action] = []
        for author in sorted(group.heads):
            until = group.heads[author]
            member = members.get(author)
            if member is not None and member.last_seq is not None:
                until = min(until, member.last_seq)
            after = heads.get(author, 0)
            while after < until:
                rows = self.store.log_after(group.group_id, author, after, until,
                                            proto.MAX_EVS_ITEMS)
                if not rows:
                    break
                for out in proto.evs_frames((r.e, r.s) for r in rows):
                    actions.append(Send(session, proto.encode(out)))
                after = rows[-1].seq
        actions.append(Send(session, proto.encode({"t": "synced"})))
        return actions

    # -- uploads --------------------------------------------------------------------------

    def _evs(self, session: Session, frame: dict) -> list[Action]:
        items = frame.get("items")
        if not isinstance(items, list) or len(items) > proto.MAX_EVS_ITEMS:  # E1
            return [Send(session, _err("bad_frame"))]
        total = sum(len(i["e"]) + len(i["s"]) + proto._ITEM_OVERHEAD for i in items
                    if isinstance(i, dict) and isinstance(i.get("e"), str)
                    and isinstance(i.get("s"), str))
        if total > proto.MAX_EVS_BYTES:  # E2
            return [Send(session, _err("too_big"))] + self._close(
                session, proto.CLOSE_POLICY, "too_big")
        group = self.group(session.group_id)
        g = group.group_id
        roster = group.roster
        # E3
        now = self._now()
        day = utc_day(now)
        if group.cleaned_day != day:
            group.cleaned_day = day
            for past, n in self.store.take_past_quota(g, day):
                log.info(json.dumps({
                    "event": "crew-daily-total", "group": g,
                    "day": datetime.fromtimestamp(past * 86400, timezone.utc).strftime("%Y-%m-%d"),
                    "envelopes": n}, separators=(",", ":")))
        group_left = self.config.group_quota - self.store.group_quota_used(g, day)
        refused = {a: r for a, r in session.refused.items() if r[1] in (0, day)}
        errs: list[Action] = []
        close_after: str | None = None
        advanced: dict[str, int] = {}
        counts: dict[str, int] = {}
        used: dict[str, int] = {}
        log_rows: list[LogRow] = []
        roster_rows: list[LogRow] = []
        forwarded: list[tuple[bytes, bytes]] = []

        def refuse(author: str, seq: int, code: str, daily: bool = False) -> None:
            errs.append(Send(session, _err(code, author, seq)))
            existing = refused.get(author)
            if existing is None or existing[0] > seq:
                refused[author] = (seq, day if daily else 0)

        # E4
        for item in items:
            c = decode_item(item)
            if c == "oversize":
                errs.append(Send(session, _err("too_big")))
                close_after = "too_big"
                break
            if c == "undecodable":
                errs.append(Send(session, _err("bad_frame")))
                close_after = "bad item"
                break
            if isinstance(c, BadSig):
                refuse(c.author, c.seq, "bad_sig")
                continue
            author, seq = c.env["author"], c.env["seq"]
            block = refused.get(author)
            if block is not None and seq >= block[0]:
                continue
            # Task 4: a join by a non-member is judged here.
            if author not in roster.members:
                refuse(author, seq, "not_member")
                continue
            if not roster.admits(author, seq):
                refuse(author, seq, "removed")
                continue
            if seq <= advanced.get(author, group.heads.get(author, 0)):
                continue
            if not verify(author, c.s, c.e):
                refuse(author, seq, "bad_sig")
                continue
            entry = roster_entry(c, now) if c.env["kind"] in ev.ROSTER_KINDS else None
            exempt = entry is not None and author == group.leader
            if not exempt:
                if author not in used:
                    used[author] = self.store.quota_used(g, day, author)
                if used[author] + counts.get(author, 0) >= self.config.member_quota:
                    refuse(author, seq, "quota", daily=True)
                    continue
                if group_left <= 0:
                    refuse(author, seq, "group_quota", daily=True)
                    continue
                counts[author] = counts.get(author, 0) + 1
                group_left -= 1
            advanced[author] = seq
            log_rows.append(LogRow(author, seq, c.e, c.s))
            forwarded.append((c.e, c.s))
            if exempt and entry.kind != "join":
                roster_rows.append(LogRow(author, seq, c.e, c.s))
        # E5, E6: the commit comes first, memory follows.
        roster_changed = self.store.apply_frame(g, day, log_rows, roster_rows, advanced, counts)
        group.heads.update(advanced)
        session.refused = refused
        if roster_changed:
            self.reload_roster(group)
        if len(refused) > MAX_REFUSALS:  # E7
            close_after = close_after or "too many refusals"
        actions = errs
        if forwarded:  # E8
            for other in group.sessions:
                if other is session or other.state != "member" or other.closing:
                    continue
                for out in proto.evs_frames(forwarded):
                    actions.append(Send(other, proto.encode(out)))
        # Task 4: E9, the roster consequences.
        if close_after:  # E10
            actions += self._close(session, proto.CLOSE_POLICY, close_after)
        return actions

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
