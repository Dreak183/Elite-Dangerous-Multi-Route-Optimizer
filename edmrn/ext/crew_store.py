"""Crew event log and its projections (Spec A C4, C7, C8; store layer per ADR-0006).

Writes run inside Store.transaction(); the writer in crew.py calls them on the Tk thread. Reads take
Store.read() and may come from any thread. Values reach SQL only as parameters.
"""
from __future__ import annotations

import sqlite3
from typing import Callable

from edmrn.ext import crew_events as ev
from edmrn.ext.crew_identity import Identity, KeyFileError, verify
from edmrn.ext.store import Store
from edmrn.logger import get_logger

logger = get_logger('CrewStore')

PROJECTION_TABLES = (
    'crew_projected', 'crew_systems', 'crew_visits', 'crew_honks', 'crew_all_bodies',
    'crew_explored', 'crew_body_reports', 'crew_signals', 'crew_genera', 'crew_signal_sources',
    'crew_finds', 'crew_manual_finds', 'crew_notes',
)

Projector = Callable[[sqlite3.Connection, dict], None]
_PROJECTORS: dict[str, Projector] = {}


def _insert(conn: sqlite3.Connection, env: dict, data: bytes, signature: bytes) -> bool:
    cursor = conn.execute(
        'INSERT OR IGNORE INTO crew_log (author, seq, event_id, kind, v, created_at, envelope, '
        'signature) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (env['author'], env['seq'], env['event_id'], env['kind'], env['v'], env['created_at'],
         data, signature))
    return cursor.rowcount == 1


def _project(conn: sqlite3.Connection, env: dict) -> None:
    """Apply an envelope once per event_id, if its kind, v and payload are known-good (C4)."""
    kind, v = env['kind'], env['v']
    if not ev.is_known(kind, v):
        return
    try:
        ev.validate_payload(kind, v, env['payload'])
    except ev.PayloadError as e:
        logger.warning(f"Crew event {env['event_id']} kept unprojected: {e}")
        return
    key = ev.natural_key(kind, env['created_at'], env['payload'])
    if key is not None:
        bound = env['event_id'] == ev.deterministic_id(env['author'], kind, *key)
    else:
        bound = ev.is_uuid7(env['event_id'])
    if not bound:
        logger.warning(f"Crew event {env['event_id']} kept unprojected: "
                       'event_id does not match its author and key')
        return
    if conn.execute('INSERT OR IGNORE INTO crew_projected (author, event_id) VALUES (?, ?)',
                    (env['author'], env['event_id'])).rowcount == 0:
        return
    projector = _PROJECTORS.get(kind)
    if projector is not None:
        projector(conn, env)


class CrewStore:
    def __init__(self, store: Store) -> None:
        self.store = store

    def append_own(self, items: list[tuple[Identity, ev.Draft]]) -> int:
        """Sign and log own drafts in one transaction (C4); the key files follow after the commit."""
        written = 0
        allocated: dict[str, tuple[Identity, int]] = {}
        with self.store.transaction() as conn:
            for identity, draft in items:
                ev.validate_payload(draft.kind, ev.ENVELOPE_VERSION, draft.payload)
                event_id = draft.resolve_id(identity.author)
                if conn.execute('SELECT 1 FROM crew_log WHERE author = ? AND event_id = ? LIMIT 1',
                                (identity.author, event_id)).fetchone():
                    continue
                if identity.author in allocated:
                    last = allocated[identity.author][1]
                else:
                    row = conn.execute('SELECT MAX(seq) FROM crew_log WHERE author = ?',
                                       (identity.author,)).fetchone()
                    last = max(row[0] or 0, identity.high_water)
                env = ev.build_envelope(event_id, identity.author, last + 1, draft.kind,
                                        draft.created_at, draft.payload)
                data, signature = ev.seal(env, identity.sign)
                _insert(conn, env, data, signature)
                _project(conn, env)
                allocated[identity.author] = (identity, last + 1)
                written += 1
        for identity, seq in allocated.values():
            try:
                identity.raise_high_water(seq)
            except (OSError, KeyFileError) as e:
                logger.error(f"Crew key file not updated ({identity.path.name}): {e}")
        return written

    def insert_received(self, envelope: bytes, signature: bytes) -> bool:
        """A received envelope: structure, signature, then INSERT OR IGNORE on (author, seq)."""
        env = ev.parse_envelope(envelope)
        if not verify(env['author'], signature, envelope):
            raise ev.EnvelopeError('signature does not verify')
        with self.store.transaction() as conn:
            inserted = _insert(conn, env, bytes(envelope), bytes(signature))
            if inserted:
                _project(conn, env)
        return inserted
