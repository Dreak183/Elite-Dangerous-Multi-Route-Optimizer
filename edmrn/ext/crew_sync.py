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
from datetime import datetime, timezone
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
                 ping_interval=30, ping_timeout=30, user_agent_header=USER_AGENT, legacy=True)
    return _WebSocketsAdapter(ws, ConnectionClosed)


@dataclass(frozen=True)
class BoardEntry:
    author: str
    at: str
    status: dict


class CrewBoard:
    """Friends' latest statuses, in memory only (S8). Observers run on the sync thread."""

    def __init__(self, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._now = now
        self._lock = threading.Lock()
        self._entries: dict[str, BoardEntry] = {}
        self._observers: list = []

    def update(self, author: str, at: str, status: dict) -> bool:
        moment = ev.parse_stamp(at)
        if moment is not None and moment > self._now() + ev.FUTURE_SLACK:
            return False  # S11: a far-future `at` would freeze the entry against every later status
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
    """Received envelopes waiting for the Tk loop (S19); the writer's pattern from Spec A.

    Start order: inbox before sync. Stop order: sync before inbox, so nothing is put into an inbox
    nobody drains. A put into a stopped inbox (or by a stopping sync) keeps at most CAP items and
    drops the rest with a warning; the next session's `have` fetches them again.
    """

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
        self._generation = 0
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
            if not self._running or stop.is_set():  # nobody may drain: stay bounded
                room = max(0, self.CAP - len(self._queue))
                if len(items) > room:
                    logger.warning(f'Crew inbox is full and not draining; dropped {len(items) - room} '
                                   f'received envelopes')
                    items = items[:room]
            self._queue.extend(items)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._generation += 1  # a tick still pending from before a stop() belongs to a dead chain
        self._schedule()

    def stop(self) -> None:
        self._running = False
        with self._space:
            self._space.notify_all()

    def _schedule(self) -> None:
        if not self._running:
            return
        generation = self._generation
        try:
            self._root.after(self.INTERVAL_MS, lambda: self._chain_tick(generation))
        except Exception as e:  # the Tk loop is gone: the app is closing
            self._running = False
            logger.debug(f'Crew inbox stopped: {e}')

    def _chain_tick(self, generation: int) -> None:
        if generation == self._generation and self._running:
            self._tick()

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
            if self._is_locked(e):
                self._requeue(batch, e)
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
        for index, item in enumerate(batch):
            try:
                inserted += self._crew_store.insert_received_batch([item]).inserted
            except sqlite3.OperationalError as e:
                if self._is_locked(e):  # transient: keep this item and the rest, in order
                    self._requeue(batch[index:], e)
                    return inserted
                logger.error(f'Crew received envelope dropped: {e}')
            except Exception as e:
                logger.error(f'Crew received envelope dropped: {e}')
        return inserted

    @staticmethod
    def _is_locked(error: Exception) -> bool:
        return any(word in str(error).lower() for word in ('locked', 'busy'))

    def _requeue(self, items: list, error: Exception) -> None:
        with self._lock:
            self._queue.extendleft(reversed(items))
        if not self._locked:
            self._locked = True
            logger.warning(f'Crew store is locked ({error}); {len(items)} received envelopes wait')


class _Session:
    """One connection: hello, auth, have, then frames until the socket closes or sync stops."""

    def __init__(self, sync: 'CrewSync', ws: Any, stop: Any) -> None:
        self.sync = sync
        self.ws = ws
        self.stop = stop
        self.live_at: Optional[float] = None
        self.relay_heads: Optional[dict] = None
        self.received: dict[str, int] = {}
        self.blocked: set[str] = set()
        self.daily_blocked: set[str] = set()
        self.unknown_seq: set[str] = set()
        self.logged: set[str] = set()
        self.reached_live = False
        self.last_flush = float('-inf')
        self.last_status_at = float('-inf')
        self.last_status: Optional[dict] = None
        self.offered: set[str] = set()
        self.day = sync._wall()[:10]

    def send(self, frame: dict) -> None:
        self.ws.send(proto.encode(frame))

    def log_once(self, key: str, message: str, exc_info: bool = False) -> None:
        if key not in self.logged:
            self.logged.add(key)
            logger.warning(f'Crew sync: {message}', exc_info=exc_info)

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
        sync._set_state('syncing', stop=self.stop)
        while not self.stop.is_set():
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
            for author, seq in self.received.items():  # S7: only listed authors are uploaded
                if author in self.relay_heads:
                    self.relay_heads[author] = max(seq, self.relay_heads[author])
            self.upload()
        elif kind == 'evs':
            items = proto.parse_evs(frame)
            self.note_received(items)
            self.sync._inbox.put(items, self.stop)
        elif kind == 'synced':
            if not self.reached_live:
                self.live_at = self.sync._clock()
            self.reached_live = True
            self.sync._set_state('live', stop=self.stop)
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
                if seq is not None and self.relay_heads is not None:
                    # The relay refused seq: its head stays before the hole (S7), so the envelopes
                    # sent optimistically are offered again once the block lifts.
                    held = self.relay_heads.get(author, 0)
                    self.relay_heads[author] = min(held, max(seq - 1, 0))
                elif seq is None:
                    self.unknown_seq.add(author)

    def note_received(self, items: list) -> None:
        """The relay holds what it forwards (S1): advance its head so uploads do not echo it back."""
        for data, _ in items:
            try:
                env = ev.parse_envelope(data)
            except ev.EnvelopeError:
                continue  # the inbox's store judges it; here it only matters for bookkeeping
            author, seq = env['author'], env['seq']
            self.received[author] = max(seq, self.received.get(author, 0))
            if self.relay_heads is not None and author in self.relay_heads:
                self.relay_heads[author] = max(seq, self.relay_heads[author])

    def tick(self) -> None:
        if self.relay_heads is None:
            return
        sync = self.sync
        due = sync._flush_requested and self.stop is sync._stop
        day = sync._wall()[:10]
        if day != self.day:  # S11: quota refusals are per UTC day
            self.day = day
            if self.daily_blocked & self.unknown_seq:
                # The refused seq is unknown, so our idea of the relay head may be too high: end
                # the session; the next have is authoritative.
                raise SocketClosed(None, 'resync after a quota refusal without a seq')
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
        if self.stop is sync._stop:  # a superseded session must not consume the new one's request
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
            while local.get(author, 0) > head and not self.stop.is_set():
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
            if sync._paused and author == sync._identity.author:
                continue  # S8: a paused client sends nothing of its own
            self.offered.add(author)
            pair = sync._store.roster_envelope(author, 'join', sync._config.group_id)
            if pair is not None:
                self.send({'t': 'evs', 'items': [{'e': proto.b64(pair[0]), 's': proto.b64(pair[1])}]})

    def receive_status(self, frame: dict) -> None:
        try:
            author, at, status = proto.parse_status(frame)
        except proto.FrameError as e:
            self.log_once('st', f'a status was dropped: {e}')
            return
        if author != self.sync._identity.author:
            self.sync._board.update(author, at, status)

    def maybe_status(self, now: float) -> None:
        """S8: on change, at most every 30 s; paused sends PAUSED_STATUS once.

        Called on every tick (about 4 per second): the interval gate and the dirty flag come
        first, so the status source is consulted at most once per interval.
        """
        sync = self.sync
        if now - self.last_status_at < sync.STATUS_INTERVAL:
            return
        if not sync._status_dirty and self.last_status is not None:
            return
        sync._status_dirty = False
        if sync._paused:
            status = PAUSED_STATUS
        else:
            try:
                built = sync._status_source()
            except Exception:  # a broken source must not end the session before catch-up
                self.log_once('status-source', 'the status source failed; no status is sent',
                              exc_info=True)
                built = None
            if built is None:  # no commander, or an alt that is not the member (Review Focus 2)
                self.last_status_at = now
                return
            status = {**built, 'paused': False}
        if status == self.last_status:
            return  # nothing to send: the interval is not spent
        doc = {'author': sync._identity.author, 'at': sync._wall(), 'status': status}
        try:
            data = self.signable(doc)
        except (ev.PayloadError, proto.FrameError) as e:
            self.log_once('own-status', f'own status not sent: {e}')
            self.last_status_at = now
            return
        self.send(proto.status_frame(data, sync._identity.sign(data)))
        self.last_status = status
        self.last_status_at = now

    @staticmethod
    def signable(doc: dict) -> bytes:
        """Canonical bytes of the status document; a field that fails validation is nulled alone."""
        try:
            ev.validate_status(doc)
        except ev.PayloadError:
            status = dict(doc['status'])
            for name, value in doc['status'].items():
                if name == 'paused' or value is None:
                    continue
                try:
                    ev.validate_status({**doc, 'status': {**PAUSED_STATUS, name: value, 'paused': False}})
                except ev.PayloadError:
                    status[name] = None
            doc['status'] = status
            ev.validate_status(doc)
        return proto.status_bytes(doc)


_SHIP_FIELDS = ('ship', 'ship_name', 'ship_ident', 'hull', 'fuel_main', 'fuel_reserve',
                'fuel_capacity_main', 'fuel_capacity_reserve')


def status_from(location: Any, ship: Any) -> dict:
    """The S8 status fields, without `paused`, from a LocationState and a ShipStatus (either None)."""
    address = getattr(location, 'system_address', None)
    coords = getattr(location, 'coords', None)
    edsy = getattr(ship, 'edsy_url', None)
    status = {
        'commander': getattr(ship, 'commander', None) or getattr(location, 'commander', None),
        'system': getattr(location, 'system', None),
        'address': None if address is None else str(address),
        'coords': None if coords is None else [float(c) for c in coords],
        'edsy_url': edsy if isinstance(edsy, str) and len(edsy) <= ev.EDSY_MAX else None,
    }
    for name in _SHIP_FIELDS:
        status[name] = getattr(ship, name, None)
    return status


def make_status_source(service: Any, member_path: Any) -> Callable[[], Optional[dict]]:
    """Status of the active commander, but only if it is the group member (Review Focus 2)."""
    def source() -> Optional[dict]:
        # The mapper state, the location tracker and the ship status update independently: take
        # the commander once, then accept the others only if they name the same commander.
        commander, name = service.commander, getattr(service, 'commander_name', None)
        if commander is None or name is None or service.keyring.path_for(commander) != member_path:
            return None
        location, ship = service.status()
        for holder in (location, ship):
            held = getattr(holder, 'commander', None)
            if held is not None and held != name:
                return None
        built = status_from(location, ship)
        if service.commander != commander:  # switched while the status was built
            return None
        return built
    return source


class CrewSync:
    RECV_TIMEOUT = 0.25  # also how soon a run notices stop()
    HELLO_TIMEOUT = 10.0
    FLUSH_INTERVAL = 5.0
    STATUS_INTERVAL = 30.0
    BACKOFF_FIRST = 1.0
    BACKOFF_MAX = 300.0
    REPLACED_WAIT = 60.0
    JITTER = 0.2
    MIN_LIVE = 30.0  # a session shorter than this does not reset the backoff

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
        self._state_lock = threading.Lock()
        self._observers: list = []
        self.state = 'stopped'
        self.last_error: Optional[str] = None

    @property
    def paused(self) -> bool:
        return self._paused

    def subscribe(self, observer: Callable[[str, Optional[str]], None]) -> None:
        """Observers run on the sync thread, not the Tk thread: marshal before touching widgets."""
        self._observers.append(observer)

    def start(self) -> None:
        """Start a run. Safe after a `stop()` whose thread is still winding down: every run has its
        own stop event, and a superseded run no longer touches the shared state."""
        if self._thread is not None and self._thread.is_alive() and not self._stop.is_set():
            return
        stop = threading.Event()
        self._stop = stop
        # The run gets its own event as an argument: reading self._stop when the thread first runs
        # would let a delayed old run adopt the next run's event.
        self._thread = threading.Thread(target=self._run, args=(stop,), name='edmrn-crew-sync',
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 0.5) -> None:
        """Ask the run to end. No closing handshake happens on the caller's thread: the session
        closes its own socket when its loop sees the event (within RECV_TIMEOUT)."""
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)
        if self.state != 'refused':
            self._set_state('stopped')

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        self._status_dirty = True

    def status_changed(self, *_: Any) -> None:
        self._status_dirty = True

    def flush_soon(self) -> None:
        """Upload at the live session's next tick instead of waiting out the flush interval (B4)."""
        self._flush_requested = True

    def _set_state(self, state: str, error: Optional[str] = None, stop: Any = None) -> None:
        with self._state_lock:  # check and write are one step against a concurrent stop()
            if stop is not None and (stop is not self._stop
                                     or (stop.is_set() and state != 'stopped')):
                return  # a superseded or stopped run publishes nothing but its final 'stopped'
            self.state = state
            if error is not None:
                self.last_error = error
        for observer in list(self._observers):
            try:
                observer(state, self.last_error)
            except Exception as e:
                logger.error(f'Crew sync observer failed: {e}')

    def _note(self, message: str, stop: Any = None) -> None:
        with self._state_lock:
            if stop is not None and (stop is not self._stop or stop.is_set()):
                return
            self.last_error = message
        logger.info(f'Crew sync: {message}')

    def _run(self, stop: Any = None) -> None:
        stop = stop if stop is not None else self._stop
        delay = self.BACKOFF_FIRST
        while not stop.is_set():
            self._set_state('connecting', stop=stop)
            outcome, live_seconds = self._attempt(stop)
            if outcome == 'refused' or stop.is_set():
                break
            stable = outcome == 'live' and live_seconds >= self.MIN_LIVE
            if stable:
                delay = self.BACKOFF_FIRST
            if outcome == 'replaced':
                wait = self.REPLACED_WAIT
            else:
                wait = delay * (1 + self.JITTER * (2 * self._rand() - 1))
            self._set_state('waiting', stop=stop)
            if stop.wait(wait):
                break
            if outcome != 'replaced' and not stable:  # a session that dies young is a failure
                delay = min(delay * 2, self.BACKOFF_MAX)
        if self.state != 'refused':
            self._set_state('stopped', stop=stop)

    def connect_once(self, stop: Any = None) -> str:
        """One connection attempt: 'live', 'failed', 'replaced' or 'refused' (S9)."""
        return self._attempt(stop)[0]

    def _attempt(self, stop: Any = None) -> tuple:
        """(outcome, seconds the session was live); nothing here is shared with other runs."""
        stop = stop if stop is not None else self._stop
        live_seconds = 0.0
        try:
            ws = self._connect(connection_url(self._config.url, self._config.group_id))
        except Exception as e:
            self._note(f'cannot reach the relay: {e}', stop)
            return 'failed', 0.0
        if stop.is_set():  # stopped or superseded while connecting
            ws.close()
            return 'failed', 0.0
        session = _Session(self, ws, stop)
        try:
            session.run()
        except SocketClosed as e:
            if e.code in (proto.CLOSE_BAD_AUTH, proto.CLOSE_FORBIDDEN):
                self._set_state('refused', f'The relay refused this member ({e.reason or e.code}).', stop)
                return 'refused', 0.0
            if e.code == proto.CLOSE_REPLACED:
                self._note('another EDMRN with this commander took over the connection', stop)
                return 'replaced', 0.0
            self._note(f'the connection closed ({e.code})', stop)
        except (proto.FrameError, TimeoutError, OSError) as e:
            self._note(f'the connection failed: {e}', stop)
        except Exception as e:  # S13: nothing escapes the thread; backoff continues
            logger.exception('Crew sync session failed unexpectedly')
            self._note(f'unexpected {type(e).__name__} in the connection', stop)
        finally:
            if session.live_at is not None:
                live_seconds = self._clock() - session.live_at
            ws.close()
        return ('live' if session.reached_live else 'failed'), live_seconds
