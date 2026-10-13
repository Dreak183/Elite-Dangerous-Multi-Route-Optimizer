"""Crew event log and its projections (Spec A C4, C7, C8; store layer per ADR-0006).

Writes run inside Store.transaction(); the writer in crew.py calls them on the Tk thread. Reads take
Store.read() and may come from any thread. Values reach SQL only as parameters.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Callable, Optional

from edmrn.ext import crew_events as ev
from edmrn.ext.crew_identity import Identity, KeyFileError, verify
from edmrn.ext.store import Store
from edmrn.logger import get_logger

logger = get_logger('CrewStore')

# The Tk thread writes; a locked database must cost it only this long per attempt.
BUSY_TIMEOUT_MS = 200

PROJECTION_TABLES = (
    'crew_projected', 'crew_systems', 'crew_visits', 'crew_honks', 'crew_all_bodies',
    'crew_explored', 'crew_body_reports', 'crew_signals', 'crew_genera', 'crew_signal_sources',
    'crew_finds', 'crew_manual_finds', 'crew_notes',
)

Projector = Callable[[sqlite3.Connection, dict, bool], None]
def _order(env: dict) -> str:
    return f"{env['created_at']}|{env['author']}|{env['seq']:016d}"


def _name_system(conn, address: str, name: Optional[str], order: str) -> None:
    if name is None:
        return
    conn.execute(
        'INSERT INTO crew_systems (address, name, name_key) VALUES (?, ?, ?) '
        'ON CONFLICT (address) DO UPDATE SET name = excluded.name, name_key = excluded.name_key '
        'WHERE crew_systems.name_key IS NULL OR excluded.name_key < crew_systems.name_key',
        (address, name, order))


def _place_system(conn, address: str, coords: Optional[list], order: str) -> None:
    if coords is None:
        return
    conn.execute(
        'INSERT INTO crew_systems (address, x, y, z, coords_key) VALUES (?, ?, ?, ?, ?) '
        'ON CONFLICT (address) DO UPDATE SET x = excluded.x, y = excluded.y, z = excluded.z, '
        'coords_key = excluded.coords_key '
        'WHERE crew_systems.coords_key IS NULL OR excluded.coords_key < crew_systems.coords_key',
        (address, float(coords[0]), float(coords[1]), float(coords[2]), order))


def _count_system(conn, address: str, count: int, order: str) -> None:
    conn.execute(
        'INSERT INTO crew_systems (address, body_count, body_count_key) VALUES (?, ?, ?) '
        'ON CONFLICT (address) DO UPDATE SET body_count = excluded.body_count, '
        'body_count_key = excluded.body_count_key '
        'WHERE crew_systems.body_count_key IS NULL '
        'OR excluded.body_count_key > crew_systems.body_count_key',
        (address, count, order))


def _refresh_explored(conn, author: str, address: str) -> None:
    """C7: all_bodies, or distinct scanned bodies reaching the latest honk's body count."""
    explored = conn.execute('SELECT 1 FROM crew_all_bodies WHERE author = ? AND address = ?',
                            (author, address)).fetchone() is not None
    if not explored:
        honk = conn.execute('SELECT body_count FROM crew_honks WHERE author = ? AND address = ?',
                            (author, address)).fetchone()
        if honk is not None and honk[0] >= 1:
            scanned = conn.execute(
                'SELECT COUNT(DISTINCT body_id) FROM crew_body_reports WHERE author = ? AND address = ?',
                (author, address)).fetchone()[0]
            explored = scanned >= honk[0]
    if explored:
        conn.execute('INSERT OR IGNORE INTO crew_explored (author, address) VALUES (?, ?)',
                     (author, address))
    else:
        conn.execute('DELETE FROM crew_explored WHERE author = ? AND address = ?', (author, address))


def _project_visit(conn, env: dict, first: bool) -> None:
    p, order = env['payload'], _order(env)
    _name_system(conn, p['address'], p['system'], order)
    _place_system(conn, p['address'], p['coords'], order)
    conn.execute(
        'INSERT INTO crew_visits (author, address, first_at, last_at, visit_count) '
        'VALUES (?, ?, ?, ?, ?) ON CONFLICT (author, address) DO UPDATE SET '
        'first_at = min(first_at, excluded.first_at), last_at = max(last_at, excluded.last_at), '
        'visit_count = visit_count + ?',
        (env['author'], p['address'], env['created_at'], env['created_at'], int(first), int(first)))


def _project_honk(conn, env: dict, first: bool) -> None:
    p, order = env['payload'], _order(env)
    _name_system(conn, p['address'], p['system'], order)
    _count_system(conn, p['address'], p['body_count'], order)
    conn.execute(
        'INSERT INTO crew_honks (author, address, body_count, honk_key) VALUES (?, ?, ?, ?) '
        'ON CONFLICT (author, address) DO UPDATE SET body_count = excluded.body_count, '
        'honk_key = excluded.honk_key WHERE excluded.honk_key > crew_honks.honk_key',
        (env['author'], p['address'], p['body_count'], order))
    _refresh_explored(conn, env['author'], p['address'])


def _project_all_bodies(conn, env: dict, first: bool) -> None:
    p, order = env['payload'], _order(env)
    _name_system(conn, p['address'], p['system'], order)
    _count_system(conn, p['address'], p['count'], order)
    conn.execute('INSERT OR IGNORE INTO crew_all_bodies (author, address) VALUES (?, ?)',
                 (env['author'], p['address']))
    _refresh_explored(conn, env['author'], p['address'])


def _flag(value: Optional[bool]) -> Optional[int]:
    return None if value is None else int(value)


def _project_body(conn, env: dict, first: bool) -> None:
    p = env['payload']
    _name_system(conn, p['address'], p['system'], _order(env))
    conn.execute(
        'INSERT INTO crew_body_reports (event_id, author, created_at, address, body_id, tier, name, '
        'star_type, planet_class, moon, landable, terraform, was_discovered, was_mapped, scan_type, '
        'report_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
        'ON CONFLICT (event_id) DO UPDATE SET created_at = excluded.created_at, '
        'address = excluded.address, body_id = excluded.body_id, tier = excluded.tier, '
        'name = excluded.name, star_type = excluded.star_type, planet_class = excluded.planet_class, '
        'moon = excluded.moon, landable = excluded.landable, terraform = excluded.terraform, '
        'was_discovered = excluded.was_discovered, was_mapped = excluded.was_mapped, '
        'scan_type = excluded.scan_type, report_key = excluded.report_key '
        'WHERE excluded.report_key < crew_body_reports.report_key',
        (env['event_id'], env['author'], env['created_at'], p['address'], p['body_id'], p['tier'],
         p['name'], p['star_type'], p['planet_class'], int(p['moon']), _flag(p['landable']),
         p['terraform'], int(p['was_discovered']), int(p['was_mapped']), p['scan_type'], _order(env)))
    _refresh_explored(conn, env['author'], p['address'])


def _project_signals(conn, env: dict, first: bool) -> None:
    p = env['payload']
    _name_system(conn, p['address'], p['system'], _order(env))
    conn.executemany(
        'INSERT INTO crew_signals (address, body_id, type, signal_count) VALUES (?, ?, ?, ?) '
        'ON CONFLICT (address, body_id, type) DO UPDATE SET '
        'signal_count = max(signal_count, excluded.signal_count)',
        [(p['address'], p['body_id'], kind, count) for kind, count in sorted(p['counts'].items())])
    conn.executemany('INSERT OR IGNORE INTO crew_genera (address, body_id, genus) VALUES (?, ?, ?)',
                     [(p['address'], p['body_id'], genus) for genus in p['genera']])
    conn.execute(
        'INSERT INTO crew_signal_sources (address, body_id, source, body_name) VALUES (?, ?, ?, ?) '
        'ON CONFLICT (address, body_id, source) DO UPDATE SET '
        'body_name = min(body_name, excluded.body_name)',
        (p['address'], p['body_id'], p['source'], p['body_name']))


def _project_find(conn, env: dict, first: bool) -> None:
    p = env['payload']
    if p['mode'] == 'auto':
        conn.execute(
            'INSERT INTO crew_finds (event_id, author, created_at, find_kind, system, address, '
            'body_name, body_id, detail, report_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
            'ON CONFLICT (event_id) DO UPDATE SET created_at = excluded.created_at, '
            'find_kind = excluded.find_kind, system = excluded.system, address = excluded.address, '
            'body_name = excluded.body_name, body_id = excluded.body_id, detail = excluded.detail, '
            'report_key = excluded.report_key WHERE excluded.report_key < crew_finds.report_key',
            (env['event_id'], env['author'], env['created_at'], p['find_kind'], p['system'],
             p['address'], p['body_name'], p['body_id'],
             ev.canonical_bytes(p['detail']).decode('utf-8'), _order(env)))
        return
    conn.execute(
        'INSERT INTO crew_manual_finds (author, find_id, seq, event_id, created_at, find_kind, '
        'system, address, body_name, body_id, note, deleted) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
        'ON CONFLICT (author, find_id) DO UPDATE SET seq = excluded.seq, '
        'event_id = excluded.event_id, created_at = excluded.created_at, '
        'find_kind = excluded.find_kind, system = excluded.system, address = excluded.address, '
        'body_name = excluded.body_name, body_id = excluded.body_id, note = excluded.note, '
        'deleted = excluded.deleted WHERE excluded.seq > crew_manual_finds.seq',
        (env['author'], p['find_id'], env['seq'], env['event_id'], env['created_at'],
         p['find_kind'], p['system'], p['address'], p['body_name'], p['body_id'], p['note'],
         int(p['deleted'])))


def _project_note(conn, env: dict, first: bool) -> None:
    p = env['payload']
    conn.execute(
        'INSERT INTO crew_notes (author, note_id, seq, event_id, created_at, system, address, text, '
        'deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) '
        'ON CONFLICT (author, note_id) DO UPDATE SET seq = excluded.seq, '
        'event_id = excluded.event_id, created_at = excluded.created_at, system = excluded.system, '
        'address = excluded.address, text = excluded.text, deleted = excluded.deleted '
        'WHERE excluded.seq > crew_notes.seq',
        (env['author'], p['note_id'], env['seq'], env['event_id'], env['created_at'], p['system'],
         p['address'], p['text'], int(p['deleted'])))


_PROJECTORS: dict[str, Projector] = {
    'visit': _project_visit, 'honk': _project_honk, 'all_bodies': _project_all_bodies,
    'body': _project_body, 'signals': _project_signals, 'find': _project_find,
    'note': _project_note,
}


def _insert(conn: sqlite3.Connection, env: dict, data: bytes, signature: bytes) -> bool:
    cursor = conn.execute(
        'INSERT OR IGNORE INTO crew_log (author, seq, event_id, kind, v, created_at, envelope, '
        'signature) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (env['author'], env['seq'], env['event_id'], env['kind'], env['v'], env['created_at'],
         data, signature))
    return cursor.rowcount == 1


def _project(conn: sqlite3.Connection, env: dict) -> None:
    """Project every valid, author-bound envelope; first sight per (author, event_id) gates counting."""
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
    first = conn.execute('INSERT OR IGNORE INTO crew_projected (author, event_id) VALUES (?, ?)',
                         (env['author'], env['event_id'])).rowcount == 1
    _PROJECTORS[kind](conn, env, first)


@dataclass(frozen=True)
class SystemRow:
    address: str
    name: Optional[str]
    coords: Optional[tuple[float, float, float]]
    body_count: Optional[int]


@dataclass(frozen=True)
class VisitRow:
    address: str
    first_at: str
    last_at: str
    count: int


@dataclass(frozen=True)
class BodyRow:
    body_id: int
    name: str
    tier: str
    star_type: Optional[str]
    planet_class: Optional[str]
    moon: bool
    landable: Optional[bool]
    terraform: Optional[str]
    scan_type: str
    author: str


@dataclass(frozen=True)
class BodyReport:
    author: str
    body_id: int
    tier: str
    was_discovered: bool
    was_mapped: bool
    created_at: str


@dataclass(frozen=True)
class BodySignals:
    body_id: int
    body_name: Optional[str]
    counts: dict
    genera: tuple
    sources: frozenset


@dataclass(frozen=True)
class FindRow:
    event_id: str
    author: str
    created_at: str
    mode: str
    find_id: Optional[str]
    find_kind: str
    system: Optional[str]
    address: str
    body_name: Optional[str]
    body_id: Optional[int]
    detail: dict
    note: str
    deleted: bool


@dataclass(frozen=True)
class NoteRow:
    note_id: str
    author: str
    seq: int
    created_at: str
    system: Optional[str]
    address: str
    text: str
    deleted: bool


def _optional_bool(value: Optional[int]) -> Optional[bool]:
    return None if value is None else bool(value)


_AUTO_FINDS_SELECT = (
    "SELECT event_id, author, created_at, 'auto', NULL, find_kind, system, address, body_name, "
    "body_id, detail, '', 0 FROM crew_finds")
_MANUAL_FINDS_SELECT = (
    "SELECT event_id, author, created_at, 'manual', find_id, find_kind, system, address, body_name, "
    "body_id, '{}', note, deleted FROM crew_manual_finds")
_NOTE_SELECT = ('SELECT note_id, author, seq, created_at, system, address, text, deleted '
                'FROM crew_notes')


def _find_row(row: tuple) -> FindRow:
    return FindRow(*row[:10], detail=json.loads(row[10]), note=row[11], deleted=bool(row[12]))


def _note_row(row: tuple) -> NoteRow:
    return NoteRow(*row[:7], deleted=bool(row[7]))


class CrewStore:
    def __init__(self, store: Store) -> None:
        self.store = store

    def append_own(self, items: list[tuple[Identity, ev.Draft]]) -> int:
        """Sign and log own drafts in one transaction (C4); the key files follow after the commit."""
        written = 0
        allocated: dict[str, tuple[Identity, int]] = {}
        with self.store.transaction(busy_timeout_ms=BUSY_TIMEOUT_MS) as conn:
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

    def max_seq(self, author: str) -> int:
        """Highest seq of `author` in the log, 0 when there is none."""
        with self.store.read() as conn:
            row = conn.execute('SELECT MAX(seq) FROM crew_log WHERE author = ?', (author,)).fetchone()
        return row[0] or 0

    def insert_received(self, envelope: bytes, signature: bytes) -> bool:
        """A received envelope: structure, signature, then INSERT OR IGNORE on (author, seq)."""
        env = ev.parse_envelope(envelope)
        if not verify(env['author'], signature, envelope):
            raise ev.EnvelopeError('signature does not verify')
        with self.store.transaction(busy_timeout_ms=BUSY_TIMEOUT_MS) as conn:
            inserted = _insert(conn, env, bytes(envelope), bytes(signature))
            if inserted:
                _project(conn, env)
        return inserted

    def rebuild_projections(self) -> None:
        """Re-derive every projection from the log in (created_at, author, seq) order (C8)."""
        with self.store.transaction() as conn:
            for table in PROJECTION_TABLES:
                conn.execute(f'DELETE FROM {table}')
            rows = conn.execute(
                'SELECT envelope FROM crew_log ORDER BY created_at, author, seq').fetchall()
            for (data,) in rows:
                try:
                    env = ev.parse_envelope(data)
                except ev.EnvelopeError as e:
                    logger.warning(f'Crew log row skipped in rebuild: {e}')
                    continue
                _project(conn, env)

    def system(self, address: str) -> Optional[SystemRow]:
        with self.store.read() as conn:
            row = conn.execute('SELECT address, name, x, y, z, body_count FROM crew_systems '
                               'WHERE address = ?', (address,)).fetchone()
        if row is None:
            return None
        coords = None if row[2] is None else (row[2], row[3], row[4])
        return SystemRow(row[0], row[1], coords, row[5])

    def explored(self, author: Optional[str] = None) -> set[str]:
        with self.store.read() as conn:
            if author is None:
                rows = conn.execute('SELECT DISTINCT address FROM crew_explored').fetchall()
            else:
                rows = conn.execute('SELECT address FROM crew_explored WHERE author = ?',
                                    (author,)).fetchall()
        return {row[0] for row in rows}

    def visits(self, author: str) -> list[VisitRow]:
        with self.store.read() as conn:
            rows = conn.execute('SELECT address, first_at, last_at, visit_count FROM crew_visits '
                                'WHERE author = ? ORDER BY last_at DESC, address', (author,)).fetchall()
        return [VisitRow(*row) for row in rows]

    def bodies(self, address: str) -> list[BodyRow]:
        with self.store.read() as conn:
            rows = conn.execute(
                'SELECT body_id, name, tier, star_type, planet_class, moon, landable, terraform, '
                'scan_type, author FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY body_id '
                "ORDER BY tier = 'full' DESC, report_key) AS rank "
                'FROM crew_body_reports WHERE address = ?) WHERE rank = 1 ORDER BY body_id',
                (address,)).fetchall()
        return [BodyRow(r[0], r[1], r[2], r[3], r[4], bool(r[5]), _optional_bool(r[6]), r[7], r[8],
                        r[9]) for r in rows]

    def body_reports(self, address: str) -> list[BodyReport]:
        with self.store.read() as conn:
            rows = conn.execute(
                'SELECT author, body_id, tier, was_discovered, was_mapped, created_at '
                'FROM crew_body_reports WHERE address = ? ORDER BY body_id, created_at, author, tier',
                (address,)).fetchall()
        return [BodyReport(r[0], r[1], r[2], bool(r[3]), bool(r[4]), r[5]) for r in rows]

    def signals(self, address: str) -> dict[int, BodySignals]:
        with self.store.read() as conn:
            counts = conn.execute('SELECT body_id, type, signal_count FROM crew_signals '
                                  'WHERE address = ?', (address,)).fetchall()
            genera = conn.execute('SELECT body_id, genus FROM crew_genera WHERE address = ? '
                                  'ORDER BY genus', (address,)).fetchall()
            sources = conn.execute('SELECT body_id, source, body_name FROM crew_signal_sources '
                                   'WHERE address = ?', (address,)).fetchall()
        bodies: dict[int, dict] = {}

        def entry(body_id: int) -> dict:
            return bodies.setdefault(body_id, {'name': None, 'counts': {}, 'genera': [],
                                               'sources': set()})

        for body_id, source, name in sources:
            e = entry(body_id)
            e['sources'].add(source)
            e['name'] = name if e['name'] is None else min(e['name'], name)
        for body_id, kind, count in counts:
            entry(body_id)['counts'][kind] = count
        for body_id, genus in genera:
            entry(body_id)['genera'].append(genus)
        return {body_id: BodySignals(body_id, e['name'], e['counts'], tuple(e['genera']),
                                     frozenset(e['sources']))
                for body_id, e in bodies.items()}

    def finds(self) -> list[FindRow]:
        with self.store.read() as conn:
            rows = conn.execute(f'{_AUTO_FINDS_SELECT} UNION ALL {_MANUAL_FINDS_SELECT} '
                                'WHERE deleted = 0 ORDER BY 3 DESC, 1 DESC, 2').fetchall()
        return [_find_row(row) for row in rows]

    def manual_find(self, author: str, find_id: str) -> Optional[FindRow]:
        with self.store.read() as conn:
            row = conn.execute(f'{_MANUAL_FINDS_SELECT} WHERE author = ? AND find_id = ?',
                               (author, find_id)).fetchone()
        return None if row is None else _find_row(row)

    def notes(self, address: Optional[str] = None) -> list[NoteRow]:
        with self.store.read() as conn:
            if address is None:
                rows = conn.execute(f'{_NOTE_SELECT} WHERE deleted = 0 '
                                    'ORDER BY created_at DESC, author, note_id').fetchall()
            else:
                rows = conn.execute(f'{_NOTE_SELECT} WHERE deleted = 0 AND address = ? '
                                    'ORDER BY created_at DESC, author, note_id', (address,)).fetchall()
        return [_note_row(row) for row in rows]

    def note(self, author: str, note_id: str) -> Optional[NoteRow]:
        with self.store.read() as conn:
            row = conn.execute(f'{_NOTE_SELECT} WHERE author = ? AND note_id = ?',
                               (author, note_id)).fetchone()
        return None if row is None else _note_row(row)
