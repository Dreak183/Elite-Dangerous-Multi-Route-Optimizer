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


@dataclass(eq=False)
class _Batch:
    """The state of one `evs` frame between its checks and its single commit."""
    session: Session
    group: Group
    now: float
    day: int
    roster: Roster  # working roster: grows with the joins this frame admits
    group_left: int
    refused: dict[str, tuple[int, int]]
    pending: list[RosterEnvelope] = field(default_factory=list)
    errs: list[Action] = field(default_factory=list)
    advanced: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    used: dict[str, int] = field(default_factory=dict)
    log_rows: list[LogRow] = field(default_factory=list)
    roster_rows: list[LogRow] = field(default_factory=list)
    forwarded: list[tuple[bytes, bytes]] = field(default_factory=list)

    def refuse(self, author: str, seq: int, code: str, daily: bool = False) -> None:
        self.errs.append(Send(self.session, _err(code, author, seq)))
        existing = self.refused.get(author)
        if existing is None or existing[0] > seq:
            self.refused[author] = (seq, self.day if daily else 0)


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

    def reload_roster(self, group: Group, leader: str | None = None) -> None:
        self._set_roster(group, self.store.roster_bytes(group.group_id), leader)

    def _set_roster(self, group: Group, raws: list[bytes], leader: str | None = None) -> None:
        """Memory changes only once the whole roster is computed; `leader` is for a group whose
        leader is not in memory yet."""
        leader = leader or group.leader
        if leader is None:
            group.roster_entries, group.roster = [], None
            return
        when = datetime.fromtimestamp(self._now(), timezone.utc)
        entries = [parse_roster_envelope(raw, frozenset(), when) for raw in raws]
        kept = [e for e in entries if e is not None]
        group.roster = compute_roster(kept, group.group_id, leader)
        group.roster_entries = kept
        group.leader = leader

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
        self._persist_status(session)
        session.state = "closed"
        group = self.groups.get(session.group_id)
        if group is None:
            return
        if session in group.sessions:
            group.sessions.remove(session)
        if not group.sessions and group.leader is None:
            del self.groups[session.group_id]

    def _persist_status(self, session: Session) -> None:
        """Write a member's latest status to the store and forget it in memory (B1 S8). A failing
        write is logged and keeps the status in memory for the next attempt."""
        if session.status is None or session.author is None:
            return
        try:
            self.store.save_status(session.group_id, session.author, *session.status)
        except Exception:
            log.exception("status save failed group=%s author=%s", session.group_id,
                          _short(session.author))
            return
        session.status = None

    def shutdown(self) -> list[Action]:
        """Persist every status, then close every session with 1001."""
        actions: list[Action] = []
        for group in list(self.groups.values()):
            for session in list(group.sessions):
                if session.state == "closed":
                    continue
                self._persist_status(session)
                session.closing = True
                actions.append(Close(session, CLOSE_GOING_AWAY, "relay shutting down"))
        log.info("shutdown sessions=%d", len(actions))
        return actions

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
        if frame["t"] == "st":
            return self._status(session, frame)
        if frame["t"] == "have":
            return self._have(session, frame)
        if frame["t"] == "evs":
            return self._evs(session, frame)
        return []

    # -- statuses -------------------------------------------------------------------------

    def _status(self, session: Session, frame: dict) -> list[Action]:
        """B1 S8. The content is the clients' business: the relay checks author, signature and
        size only (ruling 3)."""
        group = self.group(session.group_id)
        author = session.author
        now = self._now()
        if now - group.status_at.get(author, float("-inf")) < STATUS_INTERVAL:  # T1
            return [Send(session, _err("rate"))]
        e, s = frame.get("e"), frame.get("s")
        if not isinstance(e, str) or not isinstance(s, str):  # T2
            return [Send(session, _err("bad_frame"))]
        if len(e) > MAX_STATUS_CHARS:  # T3
            return [Send(session, _err("too_big"))]
        try:  # T4
            data = proto.unb64(e, proto.MAX_STATUS_BYTES)
            sig = proto.unb64(s, proto.SIGNATURE_BYTES)
        except proto.FrameError:
            return [Send(session, _err("bad_frame"))]
        if len(sig) != proto.SIGNATURE_BYTES:
            return [Send(session, _err("bad_frame"))]
        try:  # T5
            doc = ev.load_json(data, proto.MAX_STATUS_BYTES, "status")
        except ev.EnvelopeError:
            return [Send(session, _err("bad_frame"))]
        if (not isinstance(doc, dict) or doc.get("author") != author
                or not isinstance(doc.get("at"), str) or not isinstance(doc.get("status"), dict)):
            return [Send(session, _err("bad_frame"))]
        if not verify(author, sig, data):  # T6
            return [Send(session, _err("bad_sig"))]
        session.status = (data, sig)  # T7
        group.status_at[author] = now
        text = proto.encode(proto.status_frame(data, sig))
        return [Send(o, text) for o in group.sessions
                if o is not session and o.state == "member" and not o.closing]

    def _statuses_at_auth(self, session: Session, group: Group) -> list[Action]:
        """B1 S7.5: the statuses of the members present, then the stored ones of those absent."""
        members = group.roster.members

        def active(author: str | None) -> bool:
            m = members.get(author)
            return m is not None and m.last_seq is None

        actions: list[Action] = []
        live: set[str] = set()
        for other in group.sessions:
            if (other is session or other.state != "member" or other.closing
                    or other.author == session.author or not active(other.author)):
                continue
            live.add(other.author)
            if other.status is not None:
                actions.append(Send(session, proto.encode(proto.status_frame(*other.status))))
        for author, e, s in self.store.statuses(group.group_id):
            if author != session.author and author not in live and active(author):
                actions.append(Send(session, proto.encode(proto.status_frame(e, s))))
        return actions

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
        # E3
        members_before = set(group.roster.members)
        now = self._now()
        day = utc_day(now)
        if group.cleaned_day != day:
            group.cleaned_day = day
            for past, n in self.store.take_past_quota(g, day):
                log.info(json.dumps({
                    "event": "crew-daily-total", "group": g,
                    "day": datetime.fromtimestamp(past * 86400, timezone.utc).strftime("%Y-%m-%d"),
                    "envelopes": n}, separators=(",", ":")))
        b = _Batch(session, group, now, day, group.roster,
                   self.config.group_quota - self.store.group_quota_used(g, day),
                   {a: r for a, r in session.refused.items() if r[1] in (0, day)})
        close_after: str | None = None
        # E4
        for item in items:
            c = decode_item(item)
            if c == "oversize":
                b.errs.append(Send(session, _err("too_big")))
                close_after = "too_big"
                break
            if c == "undecodable":
                b.errs.append(Send(session, _err("bad_frame")))
                close_after = "bad item"
                break
            if isinstance(c, BadSig):
                b.refuse(c.author, c.seq, "bad_sig")
                continue
            self._judge(b, c)
        # E5, E6: the commit comes first, memory follows.
        roster_changed = self.store.apply_frame(g, day, b.log_rows, b.roster_rows, b.advanced,
                                                b.counts)
        group.heads.update(b.advanced)
        session.refused = b.refused
        # E8, built before the reload (the Worker forwards first): a failing reload cannot lose them.
        forwards = [proto.encode(out) for out in proto.evs_frames(b.forwarded)]
        actions = b.errs
        if forwards:
            for other in group.sessions:
                if other is not session and other.state == "member" and not other.closing:
                    actions += [Send(other, text) for text in forwards]
        if roster_changed:
            failed = self._reload_or_drop(group)
            if failed is not None:
                return actions + failed
            actions += self._roster_consequences(group, members_before)  # E9
        if len(b.refused) > MAX_REFUSALS:  # E7
            close_after = close_after or "too many refusals"
        if close_after and not session.closing:  # E10
            actions += self._close(session, proto.CLOSE_POLICY, close_after)
        return actions

    def _judge(self, b: _Batch, c: Checked) -> None:
        """E4 for one decoded item: refuse it, skip it, admit a join, or accept it."""
        group = b.group
        g, roster = group.group_id, b.roster
        author, seq = c.env["author"], c.env["seq"]
        block = b.refused.get(author)
        if block is not None and seq >= block[0]:
            return
        if author not in roster.members and c.env["kind"] == "join":  # E4d, B1 S7
            # A re-seed: the join is stored and changes the roster, but moves no head and is not
            # forwarded: the member's earlier envelopes, with lower seq, still have to arrive first.
            # The invite's expiry on the relay clock is an auth-time rule only.
            if not verify(author, c.s, c.e):
                b.refuse(author, seq, "bad_sig")
                return
            entry = roster_entry(c, b.now)
            if entry is not None:
                candidate = compute_roster(group.roster_entries + b.pending + [entry], g,
                                           roster.leader)
                if author in candidate.members:
                    b.roster = candidate
                    b.pending.append(entry)
                    b.log_rows.append(LogRow(author, seq, c.e, c.s))
                    b.roster_rows.append(LogRow(author, seq, c.e, c.s))
                    return
        if author not in roster.members:
            b.refuse(author, seq, "not_member")
            return
        if not roster.admits(author, seq):
            b.refuse(author, seq, "removed")
            return
        if seq <= b.advanced.get(author, group.heads.get(author, 0)):
            return
        if not verify(author, c.s, c.e):
            b.refuse(author, seq, "bad_sig")
            return
        entry = roster_entry(c, b.now) if c.env["kind"] in ev.ROSTER_KINDS else None
        exempt = entry is not None and author == group.leader
        if not exempt:
            if author not in b.used:
                b.used[author] = self.store.quota_used(g, b.day, author)
            if b.used[author] + b.counts.get(author, 0) >= self.config.member_quota:
                b.refuse(author, seq, "quota", daily=True)
                return
            if b.group_left <= 0:
                b.refuse(author, seq, "group_quota", daily=True)
                return
            b.counts[author] = b.counts.get(author, 0) + 1
            b.group_left -= 1
        b.advanced[author] = seq
        b.log_rows.append(LogRow(author, seq, c.e, c.s))
        b.forwarded.append((c.e, c.s))
        if exempt and entry.kind != "join":
            b.roster_rows.append(LogRow(author, seq, c.e, c.s))

    def _reload_or_drop(self, group: Group, leader: str | None = None) -> list[Action] | None:
        """Reload the roster after a commit. When that fails memory no longer matches the store,
        so every session of the group is closed and the group forgotten: the next connection
        loads everything from the store."""
        try:
            self.reload_roster(group, leader)
            return None
        except Exception:
            log.exception("roster reload failed group=%s", group.group_id)
        if self.groups.get(group.group_id) is group:
            del self.groups[group.group_id]
        actions: list[Action] = []
        for other in list(group.sessions):
            self._persist_status(other)  # the dropped group's memory is the only copy
            if other.state != "closed" and not other.closing:
                actions += self._close(other, CLOSE_INTERNAL, "internal error")
        return actions

    def _roster_consequences(self, group: Group, members_before: set[str]) -> list[Action]:
        """E9: close the sessions of removed members; tell the rest about new members."""
        roster = group.roster
        actions: list[Action] = []
        for other in list(group.sessions):
            if other.state != "member" or other.closing:
                continue
            m = roster.members.get(other.author)
            if m is None or m.last_seq is not None:
                actions += self._close(other, proto.CLOSE_FORBIDDEN, "removed")
        if any(a not in members_before for a in roster.members):
            text = proto.encode(proto.have_frame(self._listed_heads(group)))
            actions += [Send(o, text) for o in group.sessions
                        if o.state == "member" and not o.closing]
        return actions

    def _listed_heads(self, group: Group) -> dict[str, int]:
        """The stored head of every member, 0 for one the relay holds nothing of (B1 S7). Uncapped:
        a client holding a removed member's envelopes above last_seq would otherwise upload them on
        every connection; catch-up still serves only up to the cap."""
        return {a: group.heads.get(a, 0) for a in group.roster.members}

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
            failed = self._reload_or_drop(group, author)
            if failed is not None:
                return failed
            log.info("group created %s leader=%s", session.group_id, _short(author))
        actions: list[Action] = []
        if (frame.get("join") is not None and author != group.leader
                and author not in group.roster.members):  # U4
            members_before = set(group.roster.members)
            if self._admit_join(session, group, frame["join"], author):
                failed = self._reload_or_drop(group)
                if failed is not None:
                    return failed
                log.info("member admitted %s author=%s", group.group_id, _short(author))
                actions += self._roster_consequences(group, members_before)  # E9, as in the evs path
        member = group.roster.members.get(author)
        if member is None:
            return self._close(session, proto.CLOSE_FORBIDDEN, "not_member")
        if member.last_seq is not None:
            return self._close(session, proto.CLOSE_FORBIDDEN, "removed")
        for other in list(group.sessions):
            if (other is not session and other.state == "member" and other.author == author
                    and not other.closing):
                actions += self._close(other, proto.CLOSE_REPLACED, "replaced")
        session.state = "member"
        session.author = author
        text = proto.encode(proto.have_frame(self._listed_heads(group)))
        actions.append(Send(session, text))
        return actions + self._statuses_at_auth(session, group)

    def _admit_join(self, session: Session, group: Group, raw: Any, author: str) -> bool:
        """U4: a join at auth must pass the envelope checks, name a live invite on the relay clock
        and admit its author under the roster rules (B1 S7). True when the join is committed (the
        caller reloads). A refusal is quiet: U5 closes."""
        checked = self._check_auth_envelope(session, raw, author, "join")
        if checked is None:
            return False
        c, entry = checked
        invite = group.roster.invites.get(entry.payload["invite_id"])
        expires = ev.parse_stamp(invite.expires_at) if invite else None
        if expires is None or self._now() > expires.timestamp():
            return False
        candidate = compute_roster(group.roster_entries + [entry], group.group_id, group.leader)
        if author not in candidate.members:
            return False
        row = LogRow(author, c.env["seq"], c.e, c.s)  # log and roster in one commit, no head
        self.store.apply_frame(group.group_id, utc_day(self._now()), [row], [row], {}, {})
        return True

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
