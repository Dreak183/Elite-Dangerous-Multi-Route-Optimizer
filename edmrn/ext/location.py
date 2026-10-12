"""Current location of the commander, derived from journal events.

Spec: docs/superpowers/specs/2026-10-11-journal-location-design.md.
Event field shapes: knowledge/reference-data.md section 6 in the EDProjects hub.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from edmrn.logger import get_logger

logger = get_logger('Location')

Coords = tuple[float, float, float]


@dataclass(frozen=True)
class LocationState:
    commander: Optional[str] = None
    system: Optional[str] = None
    system_address: Optional[int] = None
    coords: Optional[Coords] = None
    station: Optional[str] = None
    market_id: Optional[int] = None
    station_type: Optional[str] = None
    body: Optional[str] = None
    body_id: Optional[int] = None
    body_type: Optional[str] = None
    timestamp: Optional[str] = None


Observer = Callable[[LocationState], None]

_SYSTEM_EVENTS = ('FSDJump', 'CarrierJump', 'Location')
_BODY_EVENTS = ('ApproachBody', 'SupercruiseExit', 'Touchdown')
_NO_STATION = {'station': None, 'market_id': None, 'station_type': None}
_NO_BODY = {'body': None, 'body_id': None, 'body_type': None}


def _str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _coords(value: Any) -> Optional[Coords]:
    if (isinstance(value, (list, tuple)) and len(value) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        return (float(value[0]), float(value[1]), float(value[2]))
    return None


def _station_fields(event: dict) -> dict:
    return {'station': _str(event.get('StationName')),
            'market_id': _int(event.get('MarketID')),
            'station_type': _str(event.get('StationType'))}


def _body_fields(event: dict, current: LocationState) -> Optional[dict]:
    """Body fields of an event, or None when it names no body or names a station."""
    body = _str(event.get('Body'))
    if body is None or event.get('BodyType') == 'Station':
        return None
    body_type = _str(event.get('BodyType'))
    if body_type is None and body == current.body:
        body_type = current.body_type
    return {'body': body, 'body_id': _int(event.get('BodyID')), 'body_type': body_type}


def _apply_rules(state: LocationState, event: dict) -> LocationState:
    name = event.get('event')
    if name in ('LoadGame', 'Commander'):
        commander = _str(event.get('Commander' if name == 'LoadGame' else 'Name'))
        if commander is None:
            return state
        if state.commander is not None and state.commander != commander:
            return LocationState(commander=commander)
        return replace(state, commander=commander)
    if name in _SYSTEM_EVENTS:
        system = _str(event.get('StarSystem'))
        if system is None:
            return state
        new = LocationState(commander=state.commander, system=system,
                            system_address=_int(event.get('SystemAddress')),
                            coords=_coords(event.get('StarPos')))
        body = _body_fields(event, new)
        if body:
            new = replace(new, **body)
        if event.get('Docked') is True:
            new = replace(new, **_station_fields(event))
        return new
    if name == 'Docked':
        address = _int(event.get('SystemAddress'))
        if state.system is None or (address is not None and address != state.system_address):
            base = LocationState(commander=state.commander, system=_str(event.get('StarSystem')),
                                 system_address=address)
        else:
            base = state
        return replace(base, **_station_fields(event))
    if name == 'Undocked':
        return replace(state, **_NO_STATION)
    if name in _BODY_EVENTS:
        body = _body_fields(event, state)
        return replace(state, **body) if body else state
    if name == 'LeaveBody':
        return replace(state, **_NO_BODY)
    return state


def apply_event(state: LocationState, event: Any) -> LocationState:
    """Next state after one journal event; the same object when nothing changed."""
    if not isinstance(event, dict):
        return state
    new = _apply_rules(state, event)
    if replace(new, timestamp=state.timestamp) == state:
        return state
    return replace(new, timestamp=_str(event.get('timestamp')))


class LocationTracker:
    """Thread-safe holder of the current LocationState; notifies observers on change."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = LocationState()
        self._observers: list[Observer] = []

    @property
    def state(self) -> LocationState:
        return self._state

    def subscribe(self, observer: Observer) -> None:
        self._observers.append(observer)

    def feed(self, event: Any) -> None:
        try:
            with self._lock:
                old = self._state
                new = apply_event(old, event)
                self._state = new
        except Exception as e:
            logger.error(f"Location event ignored: {e}")
            return
        if new is not old:
            self._notify(new)

    def _notify(self, state: LocationState) -> None:
        for observer in list(self._observers):
            try:
                observer(state)
            except Exception as e:
                logger.error(f"Location observer failed: {e}")
