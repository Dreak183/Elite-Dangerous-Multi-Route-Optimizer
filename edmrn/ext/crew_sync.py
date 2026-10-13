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
_DAILY_ERRORS = ('quota', 'group_quota')  # lifted when the UTC day changes (S11)


class SocketClosed(Exception):
    def __init__(self, code: Optional[int] = None, reason: str = '') -> None:
        super().__init__(f'closed ({code}) {reason}'.strip())
        self.code = code
        self.reason = reason


def _close_info(exc: Exception) -> tuple:
    received = getattr(exc, 'rcvd', None)
    return getattr(received, 'code', None), getattr(received, 'reason', '') or ''


class _WebSocketsAdapter:
    """The three calls CrewSync needs, over a `websockets.sync` connection."""

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
        self.received: dict[str, int] = {}
        self.blocked: set[str] = set()
        self.daily_blocked: set[str] = set()
        self.logged: set[str] = set()
        self.reached_live = False
        self.last_flush = float('-inf')
        self.last_status_at = float('-inf')
        self.last_status: Optional[dict] = None
        self.offered: set[str] = set()
        self.day = sync._wall()[:10]

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
            genesis = store.roster_envelope(identity.author, 'group', config.group_id)
        else:
            join = store.roster_envelope(identity.author, 'join', config.group_id)
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
            for author, seq in self.received.items():
                self.relay_heads[author] = max(seq, self.relay_heads.get(author, 0))
            self.upload()
        elif kind == 'evs':
            items = proto.parse_evs(frame)
            self.note_received(items)
            self.sync._inbox.put(items, self.sync._stop)
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
                if code in _DAILY_ERRORS:
                    self.daily_blocked.add(author)

    def note_received(self, items: list) -> None:
        """The relay holds what it forwards (S1): advance its head so uploads do not echo it back."""
        for data, _ in items:
            try:
                env = ev.parse_envelope(data)
            except ev.EnvelopeError:
                continue  # the inbox's store judges it; here it only matters for bookkeeping
            author, seq = env['author'], env['seq']
            self.received[author] = max(seq, self.received.get(author, 0))
            if self.relay_heads is not None:
                self.relay_heads[author] = max(seq, self.relay_heads.get(author, 0))

    def tick(self) -> None:
        if self.relay_heads is None:
            return
        sync = self.sync
        due = sync._flush_requested
        day = sync._wall()[:10]
        if day != self.day:  # S11: quota refusals are per UTC day
            self.day = day
            if self.daily_blocked:
                self.blocked -= self.daily_blocked
                self.daily_blocked.clear()
                self.logged -= set(_DAILY_ERRORS)
                due = True
        now = sync._clock()
        if due or now - self.last_flush >= sync.FLUSH_INTERVAL:
            self.upload()
        self.maybe_status(now)

    def upload(self) -> None:
        """Send, per author in ascending seq, what the relay lacks (S7). Paused: nothing own."""
        sync = self.sync
        sync._flush_requested = False
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
            pair = sync._store.roster_envelope(author, 'join', sync._config.group_id)
            if pair is not None:
                self.send({'t': 'evs', 'items': [{'e': proto.b64(pair[0]), 's': proto.b64(pair[1])}]})

    def receive_status(self, frame: dict) -> None:
        """Statuses are handled by the status layer."""

    def maybe_status(self, now: float) -> None:
        """Statuses are handled by the status layer."""


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
        self._flush_requested = False
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

    def flush_soon(self) -> None:
        """Upload at the live session's next tick instead of waiting out the flush interval (B4)."""
        self._flush_requested = True

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
            if outcome not in ('live', 'replaced'):
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
        except Exception as e:  # S13: nothing escapes the thread; backoff continues
            logger.exception('Crew sync session failed unexpectedly')
            self._note(f'unexpected {type(e).__name__} in the connection')
        finally:
            self._ws = None
            ws.close()
        return 'live' if session.reached_live else 'failed'
