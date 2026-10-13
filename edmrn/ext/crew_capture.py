"""Journal events to crew drafts (Spec A C5, C6, C11) and the manual entry builders.

The mapper is a state machine over journal dicts: no I/O, no Tk, no threads. It never writes; its
drafts go to the writer in crew.py. Field shapes: knowledge/exploration-events.md in the hub.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from edmrn.ext import crew_events as ev
from edmrn.logger import get_logger

logger = get_logger('CrewCapture')

VALUABLE_CLASSES = ('Earthlike body', 'Water world', 'Ammonia world')
SIGNAL_TYPE_MAP = {
    '$SAA_SignalType_Biological;': 'biological',
    '$SAA_SignalType_Geological;': 'geological',
    '$SAA_SignalType_Human;': 'human',
    '$SAA_SignalType_Guardian;': 'guardian',
    '$SAA_SignalType_Thargoid;': 'thargoid',
    '$SAA_SignalType_PlanetAnomaly;': 'thargoid',
    '$SAA_SignalType_Other;': 'other',
    '$PlanetaryMiningLocation_Name;': 'mining',
}
NSP_PREFIX = '$Fixed_Event_Life'
CODEX_PHENOMENON_PREFIX = '$Fixed_Event_Life_'


@dataclass
class MapperState:
    commander: Optional[str] = None
    commander_name: Optional[str] = None
    system: Optional[str] = None
    address: Optional[str] = None
    body_names: dict = field(default_factory=dict)

    def copy(self) -> 'MapperState':
        return MapperState(self.commander, self.commander_name, self.system, self.address,
                           dict(self.body_names))


def _str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _address(value: Any) -> Optional[str]:
    number = _int(value)
    return str(number) if number is not None and 0 <= number < 2**64 else None


def _coords(value: Any) -> Optional[list]:
    if (isinstance(value, list) and len(value) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        try:
            coords = [float(v) for v in value]
        except OverflowError:
            return None
        if all(math.isfinite(v) for v in coords):
            return coords
    return None


def commander_key(fid: Any, name: Any) -> Optional[str]:
    """Local key of a commander: the FID, or a hash of the name for journals without one (C11)."""
    fid = _str(fid)
    if fid is not None:
        return fid
    name = _str(name)
    if name is None:
        return None
    return 'name:' + hashlib.sha256(name.encode('utf-8', 'surrogatepass')).hexdigest()[:32]


class JournalMapper:
    def __init__(self, state: Optional[MapperState] = None) -> None:
        self.state = state.copy() if state is not None else MapperState()

    def snapshot(self) -> MapperState:
        return self.state.copy()

    def map(self, event: Any) -> list[ev.Draft]:
        if not isinstance(event, dict):
            return []
        try:
            return self._map(event)
        except Exception as e:  # a malformed event must never stop capture
            logger.error(f"Crew mapping skipped {event.get('event')!r}: {e}")
            return []

    def _map(self, event: dict) -> list[ev.Draft]:
        name = event.get('event')
        if name in ('LoadGame', 'Commander'):
            self._set_commander(event)
            return []
        if self.state.commander is None:
            return []
        created_at = ev.stamp(event.get('timestamp'))
        if created_at is None:
            return []
        if name == 'Location':
            self._enter(event.get('StarSystem'), event.get('SystemAddress'))
            return []
        if name in ('FSDJump', 'CarrierJump'):
            return self._visit(event, created_at)
        if name == 'FSSDiscoveryScan':
            return self._honk(event, created_at)
        if name == 'FSSAllBodiesFound':
            return self._all_bodies(event, created_at)
        if name == 'Scan':
            return self._scan(event, created_at)
        if name in ('FSSBodySignals', 'SAASignalsFound'):
            return self._signals(event, created_at, 'fss' if name == 'FSSBodySignals' else 'saa')
        if name == 'FSSSignalDiscovered':
            return self._phenomenon(event, created_at)
        if name == 'CodexEntry':
            return self._codex(event, created_at)
        return []

    # --- state ---------------------------------------------------------------------------

    def _set_commander(self, event: dict) -> None:
        display = _str(event.get('Commander' if event.get('event') == 'LoadGame' else 'Name'))
        key = commander_key(event.get('FID'), display)
        if key is None:
            return
        if key != self.state.commander:
            self.state = MapperState(commander=key, commander_name=display)
        elif display is not None:
            self.state.commander_name = display

    def _enter(self, system: Any, address: Any) -> bool:
        system, address = _str(system), _address(address)
        if system is None or address is None:
            return False
        if address != self.state.address:
            self.state.body_names = {}
        self.state.system, self.state.address = system, address
        return True

    def _system_for(self, address: str) -> Optional[str]:
        return self.state.system if address == self.state.address else None

    def _draft(self, kind: str, created_at: str, payload: dict) -> ev.Draft:
        return ev.Draft(kind=kind, commander=self.state.commander, created_at=created_at,
                        payload=payload)

    def _auto_find(self, created_at: str, find_kind: str, system: Optional[str], address: str,
                   body_name: Optional[str], body_id: Optional[int], detail: dict) -> ev.Draft:
        return self._draft('find', created_at, {
            'mode': 'auto', 'find_kind': find_kind, 'system': system, 'address': address,
            'body_name': body_name, 'body_id': body_id, 'detail': detail})

    # --- events --------------------------------------------------------------------------

    def _visit(self, event: dict, created_at: str) -> list[ev.Draft]:
        if not self._enter(event.get('StarSystem'), event.get('SystemAddress')):
            return []
        s = self.state
        return [self._draft('visit', created_at, {
            'commander': s.commander_name, 'system': s.system, 'address': s.address,
            'coords': _coords(event.get('StarPos'))})]

    def _honk(self, event: dict, created_at: str) -> list[ev.Draft]:
        system, address = _str(event.get('SystemName')), _address(event.get('SystemAddress'))
        bodies, non_bodies = _int(event.get('BodyCount')), _int(event.get('NonBodyCount'))
        if system is None or address is None or bodies is None or non_bodies is None:
            return []
        return [self._draft('honk', created_at, {
            'system': system, 'address': address, 'body_count': bodies,
            'non_body_count': non_bodies})]

    def _all_bodies(self, event: dict, created_at: str) -> list[ev.Draft]:
        system, address = _str(event.get('SystemName')), _address(event.get('SystemAddress'))
        count = _int(event.get('Count'))
        if system is None or address is None or count is None:
            return []
        return [self._draft('all_bodies', created_at,
                            {'system': system, 'address': address, 'count': count})]

    def _scan(self, event: dict, created_at: str) -> list[ev.Draft]:
        star_type, planet_class = _str(event.get('StarType')), _str(event.get('PlanetClass'))
        scan_type = event.get('ScanType')
        if (star_type is None and planet_class is None) or scan_type not in ev.SCAN_TYPES:
            return []
        address, body_id = _address(event.get('SystemAddress')), _int(event.get('BodyID'))
        name = _str(event.get('BodyName'))
        if address is None or body_id is None or name is None:
            return []
        if star_type is not None:
            planet_class = None
        tier = 'basic' if scan_type == 'Basic' else 'full'
        parents = event.get('Parents')
        moon = (isinstance(parents, list) and bool(parents) and isinstance(parents[0], dict)
                and 'Planet' in parents[0])
        landable = event.get('Landable') if isinstance(event.get('Landable'), bool) else None
        terraform = event.get('TerraformState') if isinstance(event.get('TerraformState'), str) else None
        if star_type is not None:
            landable, terraform = None, None
        system = _str(event.get('StarSystem')) or self._system_for(address)
        if address == self.state.address:
            self.state.body_names[(address, body_id)] = name
        drafts = [self._draft('body', created_at, {
            'system': system, 'address': address, 'body_id': body_id, 'name': name, 'tier': tier,
            'star_type': star_type, 'planet_class': planet_class, 'moon': moon,
            'landable': landable, 'terraform': terraform,
            'was_discovered': event.get('WasDiscovered') is True,
            'was_mapped': event.get('WasMapped') is True, 'scan_type': scan_type})]
        if tier == 'full' and planet_class is not None and (
                planet_class in VALUABLE_CLASSES or terraform == 'Terraformable'):
            drafts.append(self._auto_find(created_at, 'valuable_body', system, address, name, body_id,
                                          {'planet_class': planet_class, 'terraform': terraform or ''}))
        return drafts

    def _signals(self, event: dict, created_at: str, source: str) -> list[ev.Draft]:
        address, body_id = _address(event.get('SystemAddress')), _int(event.get('BodyID'))
        body_name = _str(event.get('BodyName'))
        signals = event.get('Signals')
        if address is None or body_id is None or body_name is None or not isinstance(signals, list):
            return []
        if body_name.endswith(' Ring'):
            return []
        counts: dict[str, int] = {}
        for signal in signals:
            if not isinstance(signal, dict):
                return []
            kind, count = signal.get('Type'), _int(signal.get('Count'))
            if not isinstance(kind, str) or count is None:
                return []
            if not kind.startswith('$'):
                return []  # a ring material: this is a ring's event
            kind = SIGNAL_TYPE_MAP.get(kind, 'other')
            counts[kind] = counts.get(kind, 0) + count
        genera: list[str] = []
        genuses = event.get('Genuses')
        if source == 'saa' and isinstance(genuses, list):
            genera = sorted({g['Genus'] for g in genuses
                             if isinstance(g, dict) and _str(g.get('Genus')) is not None})
        system = self._system_for(address)
        drafts = [self._draft('signals', created_at, {
            'system': system, 'address': address, 'body_id': body_id, 'body_name': body_name,
            'source': source, 'counts': counts, 'genera': genera})]
        if counts.get('biological', 0) >= 1:
            drafts.append(self._auto_find(created_at, 'biological', system, address, body_name, body_id,
                                          {'count': counts['biological']}))
        return drafts

    def _phenomenon(self, event: dict, created_at: str) -> list[ev.Draft]:
        signal, address = _str(event.get('SignalName')), _address(event.get('SystemAddress'))
        if (event.get('SignalType') != 'Codex' or signal is None or not signal.startswith(NSP_PREFIX)
                or address is None):
            return []
        return [self._auto_find(created_at, 'stellar_phenomenon', self._system_for(address), address,
                                None, None, {'signal': signal})]

    def _codex(self, event: dict, created_at: str) -> list[ev.Draft]:
        nearest = event.get('NearestDestination')
        new_entry = event.get('IsNewEntry') is True
        at_phenomenon = isinstance(nearest, str) and nearest.startswith(CODEX_PHENOMENON_PREFIX)
        if not (at_phenomenon or new_entry):
            return []
        address, body_id, entry_id = (_address(event.get('SystemAddress')), _int(event.get('BodyID')),
                                      _int(event.get('EntryID')))
        entry_name, category = _str(event.get('Name')), _str(event.get('Category'))
        subcategory = _str(event.get('SubCategory'))
        if None in (address, body_id, entry_id, entry_name, category, subcategory):
            return []
        system = _str(event.get('System')) or self._system_for(address)
        return [self._auto_find(created_at, 'codex', system, address,
                                self.state.body_names.get((address, body_id)), body_id,
                                {'entry_id': entry_id, 'name': entry_name, 'category': category,
                                 'subcategory': subcategory, 'new_entry': new_entry})]


# --- manual entries (Spec C's UI and the later local API build on these) ----------------

def find_revision(commander: str, find_id: str, find_kind: str, system: Optional[str], address: str,
                  body_name: Optional[str], body_id: Optional[int], note: str, deleted: bool = False,
                  *, created_at: Optional[str] = None) -> ev.Draft:
    """One revision of a manual find (C5); raises PayloadError for invalid input (C12)."""
    payload = {'mode': 'manual', 'find_id': find_id, 'find_kind': find_kind, 'system': system,
               'address': address, 'body_name': body_name, 'body_id': body_id, 'note': note,
               'deleted': deleted}
    ev.validate_payload('find', ev.ENVELOPE_VERSION, payload)
    if created_at is None:
        created_at = ev.utc_now_stamp()
    elif ev.stamp(created_at) is None:
        raise ev.PayloadError('created_at is not a UTC timestamp')
    return ev.Draft('find', commander, created_at, payload, event_id=ev.uuid7())


def note_revision(commander: str, note_id: str, system: Optional[str], address: str, text: str,
                  deleted: bool = False, *, created_at: Optional[str] = None) -> ev.Draft:
    """One revision of a note (C5); raises PayloadError for invalid input (C12)."""
    payload = {'note_id': note_id, 'system': system, 'address': address, 'text': text,
               'deleted': deleted}
    ev.validate_payload('note', ev.ENVELOPE_VERSION, payload)
    if created_at is None:
        created_at = ev.utc_now_stamp()
    elif ev.stamp(created_at) is None:
        raise ev.PayloadError('created_at is not a UTC timestamp')
    return ev.Draft('note', commander, created_at, payload, event_id=ev.uuid7())
