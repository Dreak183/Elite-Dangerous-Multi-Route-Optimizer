"""Crew capture wiring: the Tk-loop writer, `app.crew` and install (Spec A, Design).

Listeners and the replay only queue drafts; the writer alone signs and inserts, on the Tk thread.
"""
from __future__ import annotations

import sqlite3
import threading
from collections import deque
from pathlib import Path
from typing import Any, Optional

from edmrn.ext import crew_events as ev
from edmrn.ext.crew_capture import LiveCapture, MapperState, find_revision, note_revision, replay
from edmrn.ext.crew_status import CrewStatus, ShipStatus
from edmrn.ext.location import journal_dir_for
from edmrn.fuel_tracker import FuelTracker
from edmrn.journal import JournalMonitor
from edmrn.logger import get_logger

logger = get_logger('Crew')

# `cryptography` is an optional install: without it crew capture is off and EDMRN still starts.
try:
    from edmrn.ext.crew_identity import Identity, KeyFileError, KeyRing
    from edmrn.ext.crew_store import CrewStore, FindRow, NoteRow
    _UNAVAILABLE: Optional[Exception] = None
except ImportError as _e:
    _UNAVAILABLE = _e

    class KeyFileError(Exception):  # placeholder so the except clauses below stay valid
        pass


class Writer:
    INTERVAL_MS = 1000
    BATCH = 500
    QUEUE_CAP = 10_000

    def __init__(self, crew_store: CrewStore, keyring: KeyRing, root: Any) -> None:
        self._crew_store = crew_store
        self._keyring = keyring
        self._root = root
        self._queue: deque = deque()
        self._lock = threading.Lock()
        self._space = threading.Condition(self._lock)
        self._caught_up: set[str] = set()
        self._dropped = 0
        self._bad_kinds: set[str] = set()
        self._broken: set[str] = set()
        self._locked = False
        self._running = False

    @property
    def pending(self) -> int:
        return len(self._queue)

    @property
    def running(self) -> bool:
        return self._running

    def enqueue(self, draft: ev.Draft, block: bool = False) -> None:
        """Any thread. An invalid draft is dropped here, logged once per kind.

        `block` (the startup replay only) waits for the writer to free space while it runs, so a
        long replay is not cut by the queue cap. A stopped writer never blocks."""
        try:
            ev.validate_payload(draft.kind, ev.ENVELOPE_VERSION, draft.payload)
        except ev.PayloadError as e:
            with self._lock:
                first = draft.kind not in self._bad_kinds
                self._bad_kinds.add(draft.kind)
            if first:
                logger.warning(f"Crew {draft.kind} draft dropped: {e}")
            return
        with self._space:
            if block:
                while self._running and len(self._queue) >= self.QUEUE_CAP - self.BATCH:
                    self._space.wait(0.5)
            self._queue.append(draft)
            self._trim()

    def _trim(self) -> None:
        """Hold `_lock`. Past the cap the oldest drafts go; the newest may be manual entries."""
        while len(self._queue) > self.QUEUE_CAP:
            self._queue.popleft()
            self._dropped += 1
            if self._dropped % 1000 == 1:
                logger.warning(f"Crew queue full; {self._dropped} oldest drafts dropped so far")

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
            logger.debug(f"Crew writer stopped: {e}")

    def _tick(self) -> None:
        try:
            self.drain(self.BATCH)
        except Exception as e:
            logger.error(f"Crew writer tick failed: {e}")
        self._schedule()

    def drain(self, limit: Optional[int] = None) -> int:
        """Sign and log queued drafts now (Tk thread). Returns how many were written."""
        with self._space:
            count = len(self._queue) if limit is None else min(limit, len(self._queue))
            drafts = [self._queue.popleft() for _ in range(count)]
            self._space.notify_all()
        items = []
        for draft in drafts:
            identity = self._identity(draft.commander)
            if identity is not None:
                items.append((identity, draft))
        if not items:
            return 0
        try:
            written = self._crew_store.append_own(items)
            self._locked = False
            return written
        except sqlite3.OperationalError as e:
            if any(word in str(e).lower() for word in ('locked', 'busy')):
                self._requeue(drafts)
                if not self._locked:
                    self._locked = True
                    logger.warning(f"Crew store is locked ({e}); {len(drafts)} drafts stay queued")
                return 0
            logger.warning(f"Crew batch of {len(items)} failed ({e}); retrying one by one")
        except Exception as e:
            logger.warning(f"Crew batch of {len(items)} failed ({e}); retrying one by one")
        written = 0
        for item in items:
            try:
                written += self._crew_store.append_own([item])
            except Exception as e:
                logger.error(f"Crew {item[1].kind} draft dropped: {e}")
        return written

    def _requeue(self, drafts: list) -> None:
        """Put a failed batch back ahead of newer drafts; past the cap the oldest are dropped, as in
        `enqueue`: the newest may be manual notes and finds that no replay restores."""
        with self._lock:
            self._queue.extendleft(reversed(drafts))
            self._trim()

    def _identity(self, commander: Any) -> Optional[Identity]:
        if not isinstance(commander, str) or commander in self._broken:
            return None
        try:
            identity = self._keyring.for_commander(commander)
        except (KeyFileError, OSError) as e:
            self._broken.add(commander)
            logger.error(f"Crew capture disabled for one commander: {e}")
            return None
        if commander not in self._caught_up:
            self._catch_up(commander, identity)
        return identity

    def _catch_up(self, commander: str, identity: Identity) -> None:
        """A crash between the log commit and the key-file write leaves the file behind the log;
        raise it to the log's maximum before any seq is allocated from it."""
        try:
            logged = self._crew_store.max_seq(identity.author)
            if logged > identity.high_water:
                identity.raise_high_water(logged)
        except sqlite3.Error as e:  # locked or busy: try again on the next drain
            logger.warning(f"Crew high-water catch-up postponed: {e}")
            return
        except (OSError, KeyFileError, ValueError) as e:
            logger.error(f"Crew high-water catch-up failed: {e}")
        self._caught_up.add(commander)


class CrewService:
    """`app.crew`: the queries (`store`), the manual API, the status and identities.

    The manual API and `Writer.drain` run on the Tk thread only: the `KeyRing` is not locked."""

    def __init__(self, crew_store: CrewStore, keyring: KeyRing, writer: Writer, live: LiveCapture,
                 ship: CrewStatus, app: Any = None) -> None:
        self.store = crew_store
        self.keyring = keyring
        self.writer = writer
        self.live = live
        self.ship = ship
        self._app = app

    @property
    def commander(self) -> Optional[str]:
        return self.live.mapper.state.commander

    @property
    def commander_name(self) -> Optional[str]:
        return self.live.mapper.state.commander_name

    def identity(self) -> Optional[Identity]:
        commander = self.commander
        if commander is None:
            return None
        try:
            return self.keyring.for_commander(commander)
        except (KeyFileError, OSError):
            return None

    def status(self) -> tuple[Any, ShipStatus]:
        """(location state or None, ship status) of the active commander (C9)."""
        tracker = getattr(self._app, 'location_tracker', None)
        return (tracker.state if tracker is not None else None), self.ship.state

    def report_find(self, find_kind: str, system: Optional[str], address: str,
                    body_name: Optional[str] = None, body_id: Optional[int] = None,
                    note: str = '') -> str:
        commander, _ = self._me()
        draft = find_revision(commander, ev.uuid7(), find_kind, system, address, body_name, body_id, note)
        self.writer.enqueue(draft)
        return draft.payload['find_id']

    def edit_find(self, find_id: str, **changes: Any) -> None:
        commander, identity = self._me()
        current = self._current_find(identity, find_id)
        fields = {'find_kind': current.find_kind, 'system': current.system,
                  'address': current.address, 'body_name': current.body_name,
                  'body_id': current.body_id, 'note': current.note}
        unknown = set(changes) - set(fields)
        if unknown:
            raise TypeError(f'unknown find fields: {sorted(unknown)}')
        fields.update(changes)
        self.writer.enqueue(find_revision(commander, find_id, **fields))

    def delete_find(self, find_id: str) -> None:
        commander, identity = self._me()
        current = self._current_find(identity, find_id)
        self.writer.enqueue(find_revision(commander, find_id, current.find_kind, current.system,
                                          current.address, current.body_name, current.body_id,
                                          current.note, deleted=True))

    def set_note(self, system: Optional[str], address: str, text: str,
                 note_id: Optional[str] = None) -> str:
        commander, identity = self._me()
        if note_id is not None:
            self._current_note(identity, note_id)
        note_id = note_id or ev.uuid7()
        self.writer.enqueue(note_revision(commander, note_id, system, address, text))
        return note_id

    def delete_note(self, note_id: str) -> None:
        commander, identity = self._me()
        current = self._current_note(identity, note_id)
        self.writer.enqueue(note_revision(commander, note_id, current.system, current.address,
                                          current.text, deleted=True))

    def _me(self) -> tuple[str, Identity]:
        commander, identity = self.commander, self.identity()
        if commander is None or identity is None:
            raise RuntimeError('no commander is known yet')
        return commander, identity

    def _current_find(self, identity: Identity, find_id: str) -> FindRow:
        self.writer.drain()
        row = self.store.manual_find(identity.author, find_id)
        if row is None or row.deleted:
            raise KeyError(find_id)
        return row

    def _current_note(self, identity: Identity, note_id: str) -> NoteRow:
        self.writer.drain()
        row = self.store.note(identity.author, note_id)
        if row is None or row.deleted:
            raise KeyError(note_id)
        return row


def _startup_replay(journal_dir: Optional[str], live: LiveCapture, writer: Writer,
                    status: CrewStatus) -> None:
    state: Optional[MapperState] = None
    try:
        state = replay(journal_dir, lambda d: writer.enqueue(d, block=True), status)
    except Exception as e:
        logger.error(f"Crew startup replay failed: {e}")
    finally:
        live.seed(state)


def install(app: Any) -> Optional[CrewService]:
    """Wire crew capture into a running EDMRN_App. Called once from EDMRN_App.__init__."""
    if _UNAVAILABLE is not None:
        logger.error(f"Crew capture disabled: {_UNAVAILABLE}")
        app.crew = None
        return None
    existing = getattr(app, 'crew', None)
    if isinstance(existing, CrewService):
        return existing
    store = getattr(app, 'edmrn_store', None)
    if store is None:
        reason = getattr(app, 'edmrn_store_error', None) or 'no store'
        logger.error(f"Crew capture disabled: {reason}")
        app.crew = None
        return None
    try:
        keyring = KeyRing(Path(store.path).parent / 'crew_keys')
        crew_store = CrewStore(store)
        status = CrewStatus()
        writer = Writer(crew_store, keyring, app.root)
        live = LiveCapture(writer.enqueue, status)
        service = CrewService(crew_store, keyring, writer, live, status, app)
        journal_dir = journal_dir_for(app)
    except Exception as e:  # the hook must never abort EDMRN_App.__init__
        logger.error(f"Crew capture disabled: {e}")
        app.crew = None
        return None
    JournalMonitor.event_listeners.append(live.feed)
    FuelTracker.status_listeners.append(status.feed_status)
    app.crew = service
    writer.start()
    threading.Thread(target=_startup_replay, args=(journal_dir, live, writer, status),
                     name='edmrn-crew-replay', daemon=True).start()
    return service
