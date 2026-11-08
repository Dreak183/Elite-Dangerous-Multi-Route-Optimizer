"""Current location of the commander, derived from journal events.

Spec: docs/superpowers/specs/2026-10-11-journal-location-design.md.
Event field shapes: knowledge/reference-data.md section 6 in the EDProjects hub.
"""
from __future__ import annotations

import math
import os  # noqa: F401  (tests patch os.path through this module)
import threading
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from edmrn.ext.journal_replay import replay_newest
from edmrn.journal import JournalMonitor
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
        try:
            result = (float(value[0]), float(value[1]), float(value[2]))
        except (OverflowError, ValueError):
            return None
        return result if all(math.isfinite(v) for v in result) else None
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
    if name in ('LeaveBody', 'SupercruiseEntry'):
        return replace(state, **_NO_BODY)
    return state


def apply_event(state: LocationState, event: Any) -> LocationState:
    """Next state after one journal event; the same object when nothing changed."""
    if not isinstance(event, dict):
        return state
    new = _apply_rules(state, event)
    if replace(new, timestamp=state.timestamp) == state:
        return state
    return replace(new, timestamp=_str(event.get('timestamp')) or state.timestamp)


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

    def adopt(self, state: LocationState) -> bool:
        """Take a replayed state unless the tracker already holds a newer one."""
        with self._lock:
            current = self._state
            if current.timestamp is not None and (
                    state.timestamp is None or state.timestamp <= current.timestamp):
                return False
            if state == current:
                return False
            self._state = state
        self._notify(state)
        return True

    def _notify(self, _applied: LocationState) -> None:
        # Deliver the current state, not the argument: another thread may have moved on
        # between releasing the lock and getting here, and the last notice must be the newest.
        state = self._state
        for observer in list(self._observers):
            try:
                observer(state)
            except Exception as e:
                logger.error(f"Location observer failed: {e}")


def cold_start(journal_dir: Optional[str], tracker: LocationTracker,
               max_files: int = 5) -> Optional[LocationState]:
    """Replay the newest journal files until one yields a system; hand it to the tracker."""
    replay = replay_newest(journal_dir, LocationTracker,
                           lambda state: state.system is not None, max_files)
    if replay is None:
        return None
    tracker.adopt(replay.state)
    return replay.state


def format_location(state: LocationState) -> Optional[str]:
    """Header text, or None when the system is unknown. The arrival star is not shown."""
    if state.system is None:
        return None
    if state.station:
        return f"{state.system} · {state.station}"
    if state.body and state.body_type != 'Star' and state.body != state.system:
        return f"{state.system} · {state.body}"
    return state.system


def tracked_system(app) -> Optional[str]:
    """Bare system name the tracker knows, or None. Never raises."""
    try:
        return app.location_tracker.state.system
    except Exception:
        return None


def journal_dir_for(app) -> Optional[str]:
    """The journal folder the live monitor uses, including the manual journal_path setting."""
    manual_path = getattr(getattr(app, 'config', None), 'journal_path', '') or None
    live_dir = getattr(getattr(app, 'journal_monitor', None), 'journal_path', None)
    return live_dir or JournalMonitor(None, manual_journal_path=manual_path).journal_path


def install(app) -> LocationTracker:
    """Wire the tracker into a running EDMRN_App. Called once from EDMRN_App.__init__."""
    existing = getattr(app, 'location_tracker', None)
    if isinstance(existing, LocationTracker):
        return existing
    tracker = LocationTracker()
    app.location_tracker = tracker
    JournalMonitor.event_listeners.append(tracker.feed)

    def reassert_header(*_):
        text = format_location(tracker.state)
        if text is not None and app.cmdr_location.get() != text:
            app.cmdr_location.set(text)

    def on_change(_state: LocationState) -> None:
        try:
            app.root.after(0, reassert_header)
        except Exception as e:
            logger.debug(f"Header update skipped: {e}")

    tracker.subscribe(on_change)
    app.cmdr_location.trace_add('write', reassert_header)

    journal_dir = journal_dir_for(app)
    threading.Thread(target=cold_start, args=(journal_dir, tracker),
                     name='edmrn-location-cold-start', daemon=True).start()
    return tracker
