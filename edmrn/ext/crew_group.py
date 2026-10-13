"""The crew group controller behind the dialog (crew sync core spec S4, S5, S5a, S20).

Every action runs on the Tk thread, like Spec A's manual API: the KeyRing and the writer's drain are
not locked. Two things come from other threads and reach Tk only through root.after: the Discord post
and the observers of the sync (state) and the board (statuses).
"""
from __future__ import annotations

import dataclasses
import contextlib
import importlib.util
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from edmrn.ext import crew_discord
from edmrn.ext import crew_events as ev
from edmrn.ext.api import ApiError
from edmrn.ext.crew_roster import (DEFAULT_RELAY, INVITE_HOURS, CreatedInvite, GroupConfig,
                                   GroupConfigError, GroupError, InviteCode, InviteError, check_relay_url, check_webhook,
                                   decode_invite, delete_group, encode_invite, invite_link,
                                   load_group, new_token, save_group, token_hash)
from edmrn.ext.crew_sync import CrewBoard, CrewSync, Inbox, make_status_source, websockets_connect
from edmrn.logger import get_logger

logger = get_logger('CrewGroup')


class CrewGroup:
    def __init__(self, service: Any, app: Any, *, sync_factory: Callable = CrewSync,
                 connect: Optional[Callable] = None, post: Callable = crew_discord.post_invite,
                 clock: Optional[Callable[[], datetime]] = None) -> None:
        self.service = service
        self.app = app
        self.root = getattr(app, 'root', None)
        self.directory = service.keyring.directory
        self.board = CrewBoard()
        self.inbox = Inbox(service.store, self.root)
        self.sync: Any = None
        self.config: Optional[GroupConfig] = None
        self.problem: Optional[str] = None
        self._sync_factory = sync_factory
        self._connect = connect  # None: `websockets_connect`, looked up when a sync starts
        self._post = post
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._subscribed = False
        self._inbox_started = False
        self._websockets_logged = False
        self._key_logged = False
        self._state_observers: list = []
        self._board_observers: list = []
        self.board.subscribe(self._board_changed)

    # --- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        """Load group.json, set the roster gate, start the inbox and, with a group, the sync.

        Safe to call again: while a sync runs it does nothing, and the inbox keeps one tick chain."""
        if self.sync is not None and self._inbox_started:
            return
        self.problem = None
        self.config = None
        try:
            self.config = load_group(self.directory)
        except GroupConfigError as e:
            self.problem = f'The group file cannot be used ({e}); leave the group to start over.'
            logger.error(self.problem)
        self._apply_gate()
        if not self._inbox_started:
            self._inbox_started = True
            self.inbox.start()  # before the sync: nothing may be put into an inbox nobody drains
        if self.config is not None:
            self._start_sync()

    def stop(self) -> None:
        """Sync first, then the inbox."""
        sync, self.sync = self.sync, None
        if sync is not None:
            sync.stop()
        self.inbox.stop()
        self._inbox_started = False

    @property
    def roster(self):
        return self.service.store.roster

    @property
    def state(self) -> str:
        if self.config is None:
            return 'no group'
        return self.sync.state if self.sync is not None else 'stopped'

    def subscribe_state(self, observer: Callable[[], None]) -> Callable[[], None]:
        """`observer()` runs on the Tk loop when the sync state changes; re-read `state` there,
        notifications can arrive out of order. Returns the function that unsubscribes."""
        self._state_observers.append(observer)
        return lambda: self._unsubscribe(self._state_observers, observer)

    def subscribe_board(self, observer: Callable[[Any], None]) -> Callable[[], None]:
        """`observer(entry)` runs on the Tk loop for each friend status that is accepted.
        Returns the function that unsubscribes."""
        self._board_observers.append(observer)
        return lambda: self._unsubscribe(self._board_observers, observer)

    @staticmethod
    def _unsubscribe(observers: list, observer: Callable) -> None:
        with contextlib.suppress(ValueError):  # already removed
            observers.remove(observer)

    def _to_tk(self, fn: Callable[[], None]) -> None:
        if self.root is None:
            return
        try:
            self.root.after(0, fn)
        except Exception as e:  # the Tk loop is gone: the app is closing
            logger.debug(f'Crew group notification dropped: {e}')

    def _state_changed(self, *_: Any) -> None:  # sync thread
        for observer in list(self._state_observers):
            self._to_tk(self._guarded(observer))

    def _board_changed(self, entry: Any) -> None:  # sync thread
        for observer in list(self._board_observers):
            self._to_tk(self._guarded(observer, entry))

    @staticmethod
    def _guarded(observer: Callable, *args: Any) -> Callable[[], None]:
        def run() -> None:
            try:
                observer(*args)
            except Exception as e:
                logger.error(f'Crew group observer failed: {e}')
        return run

    def _apply_gate(self) -> None:
        cfg = self.config
        self.service.store.set_gate(self.service.keyring.own_authors(),
                                    cfg.group_id if cfg else None, cfg.leader if cfg else None)

    def _start_sync(self) -> None:
        if self.sync is not None:
            return
        if importlib.util.find_spec('websockets') is None:
            self.problem = 'Crew sync needs the websockets package (pip install websockets).'
            if not self._websockets_logged:
                self._websockets_logged = True
                logger.error(self.problem)
            return
        identity = self.service.keyring.identity_for_author(self.config.member)
        if identity is None:
            self.problem = 'The key of this group membership is missing from crew_keys.'
            if not self._key_logged:
                self._key_logged = True
                logger.error(self.problem)
            return
        self.problem = None
        connect = self._connect or websockets_connect
        try:
            sync = self._sync_factory(self.service.store, self.config, identity, self.inbox,
                                      self.board, make_status_source(self.service, identity.path),
                                      connect=connect)
            sync.subscribe(self._state_changed)
        except Exception:
            logger.exception('Crew sync could not be created')
            self.problem = 'Crew sync could not start; the log file says why.'
            return
        self.sync = sync
        if not self._subscribed:
            self._subscribed = True
            self.service.ship.subscribe(self._status_changed)
            tracker = getattr(self.app, 'location_tracker', None)
            if tracker is not None:
                tracker.subscribe(self._status_changed)
        sync.start()

    def _status_changed(self, *_: Any) -> None:
        sync = self.sync
        if sync is not None:
            sync.status_changed()

    # --- helpers -----------------------------------------------------------------------

    @contextlib.contextmanager
    def _storage(self, what: str):
        """Disk and database errors become one logged sentence for the dialog."""
        try:
            yield
        except (OSError, sqlite3.Error) as e:
            logger.error(f'Crew {what} failed: {e}')
            raise GroupError(f'EDMRN could not {what}; the log file says why.') from None

    def _me(self) -> tuple:
        """(commander key, identity). The key finds the key file; it is never published."""
        commander, identity = self.service.commander, self.service.identity()
        if commander is None or identity is None:
            raise GroupError('No commander is known yet; start Elite Dangerous or wait until '
                             'EDMRN has read the journal.')
        return commander, identity

    def _roster_name(self) -> str:
        """The in-game name for roster payloads; the key is the Frontier ID and stays private."""
        name = getattr(self.service, 'commander_name', None)
        if not isinstance(name, str) or not name.strip():
            raise GroupError('No commander is known yet; start Elite Dangerous or wait until '
                             'EDMRN has read the journal.')
        return ev.ascii_name(name)

    def _leader(self) -> tuple:
        if self.config is None:
            raise GroupError('This EDMRN is not in a group.')
        commander, identity = self._me()
        if not self.config.is_leader or identity.author != self.config.leader:
            raise GroupError('Only the group leader can do this; switch to the commander who '
                             'created the group.')
        return commander, identity

    def _record(self, kind: str, commander: str, author: str, payload: dict) -> None:
        draft = ev.Draft(kind, commander, ev.utc_now_stamp(), payload, event_id=ev.uuid7())
        with self._storage('record this'):
            self.service.writer.enqueue(draft)
            self.service.writer.drain()
            recorded = self.service.store.has_event(author, draft.event_id)
        if not recorded:
            raise GroupError('EDMRN could not record this; the log file says why.')
        if kind in ('invite', 'remove') and self.sync is not None:
            self.sync.flush_soon()  # the relay must know before the friend connects (B4)

    def _enter(self, config: GroupConfig) -> None:
        with self._storage('save the group file'):
            save_group(self.directory, config)
        self.config = config
        try:
            with self._storage('open the group'):
                self._apply_gate()
        except GroupError:  # undo: no group on disk, none in memory
            self.config = None
            with contextlib.suppress(OSError):
                delete_group(self.directory)
            with contextlib.suppress(Exception):
                self._apply_gate()
            raise
        self._start_sync()

    @staticmethod
    def _relay(relay: str) -> str:
        try:
            return check_relay_url(relay)
        except InviteError as e:
            raise GroupError(str(e)) from None

    # --- actions -----------------------------------------------------------------------

    def create_group(self, name: str, relay: str = DEFAULT_RELAY) -> None:
        if self.config is not None:
            raise GroupError('This EDMRN is already in a group; leave it first.')
        name = name.strip() if isinstance(name, str) else ''
        if not ev.is_roster_name(name):
            raise GroupError('A group name is 1 to 64 plain ASCII characters.')
        url = self._relay(relay)
        commander, identity = self._me()
        group_id = ev.uuid7()
        self._record('group', commander, identity.author,
                     {'group_id': group_id, 'name': name, 'commander': self._roster_name()})
        self._enter(GroupConfig(url, group_id, identity.author, identity.author))

    def join(self, text: str) -> None:
        if self.config is not None:
            raise GroupError('This EDMRN is already in a group; leave it first.')
        try:
            code = decode_invite(text)
        except InviteError as e:
            raise GroupError(str(e)) from None
        commander, identity = self._me()
        if identity.author == code.leader:
            raise GroupError('This is your own invite; send it to a friend.')
        self._record('join', commander, identity.author,
                     {'group_id': code.group_id, 'invite_id': code.invite_id, 'token': code.token,
                      'commander': self._roster_name()})
        self._enter(GroupConfig(code.url, code.group_id, code.leader, identity.author))

    def create_invite(self, label: Optional[str] = None, hours: int = 24) -> CreatedInvite:
        commander, identity = self._leader()
        if type(hours) is not int or hours not in INVITE_HOURS:
            raise GroupError('An invite lasts 1 hour, 24 hours or 7 days.')
        label = label.strip() if isinstance(label, str) and label.strip() else None
        if label is not None and not ev.is_roster_name(label):
            raise GroupError('A label is 1 to 64 plain ASCII characters.')
        cfg = self.config
        token, invite_id = new_token(), ev.uuid7()
        expires = (self._clock() + timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
        self._record('invite', commander, identity.author,
                     {'group_id': cfg.group_id, 'invite_id': invite_id,
                      'token_hash': token_hash(token), 'expires_at': expires, 'label': label})
        code = encode_invite(InviteCode(cfg.url, cfg.group_id, invite_id, token, cfg.leader))
        return CreatedInvite(code, invite_link(code, cfg.url), expires, label)

    def remove(self, member: str) -> None:
        commander, identity = self._leader()
        roster = self.roster
        if (member == self.config.leader or roster is None or member not in roster.members
                or roster.members[member].last_seq is not None):
            raise GroupError('Only a current member other than the leader can be removed.')
        with self._storage('read the log'):
            last_seq = self.service.store.max_seq(member)
        self._record('remove', commander, identity.author,
                     {'group_id': self.config.group_id, 'member': member, 'last_seq': last_seq})

    def leave(self) -> None:
        with self._storage('delete the group file'):  # first: a failure changes nothing
            delete_group(self.directory)
        sync, self.sync = self.sync, None
        if sync is not None:
            sync.stop()
        self.config = None
        self.problem = None
        with self._storage('close the group'):
            self._apply_gate()

    def reconnect(self) -> None:
        """A new connection attempt, for the state 'refused' (the relay turned this member away)."""
        if self.config is None:
            raise GroupError('This EDMRN is not in a group.')
        sync, self.sync = self.sync, None
        if sync is not None:
            sync.stop()
        self._start_sync()

    def set_paused(self, paused: bool) -> None:
        if self.config is None:
            raise GroupError('This EDMRN is not in a group.')
        updated = dataclasses.replace(self.config, paused=bool(paused))
        with self._storage('save the group file'):
            save_group(self.directory, updated)
        self.config = updated
        if self.sync is not None:
            self.sync.set_paused(bool(paused))

    def set_webhook(self, url: Optional[str]) -> None:
        self._leader()
        webhook = None
        if isinstance(url, str) and url.strip():
            try:
                webhook = check_webhook(url)
            except InviteError as e:
                raise GroupError(str(e)) from None
        updated = dataclasses.replace(self.config, webhook=webhook)
        with self._storage('save the group file'):
            save_group(self.directory, updated)
        self.config = updated

    def post_invite(self, invite: CreatedInvite, done: Callable[[Optional[str]], None]) -> None:
        """Post through the webhook in a worker thread; `done(None or sentence)` on the Tk loop."""
        self._leader()
        if self.config.webhook is None:
            raise GroupError('Set a Discord webhook first.')
        webhook = self.config.webhook
        roster = self.roster
        name = roster.name if roster is not None and roster.name else 'crew'
        message = crew_discord.invite_message(name, invite.label, invite.expires_at, invite.link)

        def work() -> None:
            try:
                self._post(webhook, message)
                result = None
            except (ApiError, InviteError) as e:
                result = str(e)
            except Exception:
                logger.exception('Crew invite post failed')
                result = 'The post to Discord failed; the log file says why.'
            self._to_tk(lambda: done(result))

        threading.Thread(target=work, name='edmrn-crew-discord', daemon=True).start()
