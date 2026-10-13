"""Ship, hull and fuel of the active commander (Spec A C9). Ephemeral: never logged.

Commander and location live in app.location_tracker; this holds the rest. Observers run on the
thread that caused the change (journal monitor, fuel tracker or replay), so UI observers go
through root.after.
"""
from __future__ import annotations

import base64
import gzip
import json
import math
import threading
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from edmrn.ext.crew_events import stamp
from edmrn.logger import get_logger

logger = get_logger('CrewStatus')

EDSY_IMPORT_URL = 'https://edsy.org/#/I='
IN_MAIN_SHIP = 1 << 24
MAX_TONS = 1e5


@dataclass(frozen=True)
class ShipStatus:
    commander: Optional[str] = None
    ship: Optional[str] = None
    ship_name: Optional[str] = None
    ship_ident: Optional[str] = None
    edsy_url: Optional[str] = None
    hull: Optional[float] = None
    fuel_main: Optional[float] = None
    fuel_reserve: Optional[float] = None
    fuel_capacity_main: Optional[float] = None
    fuel_capacity_reserve: Optional[float] = None
    journal_at: Optional[str] = None
    timestamp: Optional[str] = None


Observer = Callable[[ShipStatus], None]


def edsy_url(loadout: dict) -> str:
    """EDSY import link for a journal Loadout, built as EDMC builds it (knowledge/edsy.md)."""
    data = json.dumps(loadout, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    encoded = base64.urlsafe_b64encode(gzip.compress(data, mtime=0)).decode('ascii')
    return EDSY_IMPORT_URL + encoded.replace('=', '%3D')


def _str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _number(value: Any, high: float) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and 0.0 <= number <= high else None


def _repairs_hull(event: dict) -> bool:
    items = []
    if isinstance(event.get('Item'), str):
        items.append(event['Item'])
    if isinstance(event.get('Items'), list):
        items.extend(item for item in event['Items'] if isinstance(item, str))
    return any(item.lower() in ('hull', 'all') for item in items)


def apply_journal(state: ShipStatus, event: dict) -> ShipStatus:
    name = event.get('event')
    if name in ('LoadGame', 'Commander'):
        commander = _str(event.get('Commander' if name == 'LoadGame' else 'Name'))
        if commander is None:
            return state
        if state.commander is not None and state.commander != commander:
            return ShipStatus(commander=commander)
        return replace(state, commander=commander)
    if name == 'Loadout':
        capacity = event.get('FuelCapacity') if isinstance(event.get('FuelCapacity'), dict) else {}
        hull = _number(event.get('HullHealth'), 1.0)
        return replace(state, ship=_str(event.get('Ship')), ship_name=_str(event.get('ShipName')),
                       ship_ident=_str(event.get('ShipIdent')), edsy_url=edsy_url(event),
                       hull=state.hull if hull is None else hull,
                       fuel_capacity_main=_number(capacity.get('Main'), MAX_TONS),
                       fuel_capacity_reserve=_number(capacity.get('Reserve'), MAX_TONS))
    if name == 'HullDamage':
        health = _number(event.get('Health'), 1.0)
        if event.get('Fighter') is True or health is None:
            return state
        return replace(state, hull=health)
    if name == 'RepairAll' or (name == 'Repair' and _repairs_hull(event)):
        return replace(state, hull=1.0)
    return state


def apply_status(state: ShipStatus, status: dict) -> ShipStatus:
    flags = status.get('Flags')
    if not isinstance(flags, int) or isinstance(flags, bool) or not flags & IN_MAIN_SHIP:
        return state
    fuel = status.get('Fuel')
    if not isinstance(fuel, dict):
        return state
    main = _number(fuel.get('FuelMain'), MAX_TONS)
    reserve = _number(fuel.get('FuelReservoir'), MAX_TONS)
    return replace(state, fuel_main=state.fuel_main if main is None else main,
                   fuel_reserve=state.fuel_reserve if reserve is None else reserve)


class CrewStatus:
    """Thread-safe holder of the active commander's ShipStatus."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = ShipStatus()
        self._observers: list[Observer] = []

    @property
    def state(self) -> ShipStatus:
        return self._state

    def subscribe(self, observer: Observer) -> None:
        self._observers.append(observer)

    def feed(self, event: Any) -> None:
        """A journal event (journal listener or replay)."""
        if isinstance(event, dict):
            self._apply(lambda s: apply_journal(s, event), event.get('timestamp'), journal=True)

    def feed_status(self, status: Any) -> None:
        """A Status.json dict (FuelTracker.status_listeners)."""
        if isinstance(status, dict):
            self._apply(lambda s: apply_status(s, status), status.get('timestamp'), journal=False)

    def _apply(self, step: Callable[[ShipStatus], ShipStatus], timestamp: Any, journal: bool) -> None:
        try:
            with self._lock:
                old = self._state
                new = step(old)
                if new == old:
                    return
                at = stamp(timestamp)
                self._state = replace(new, timestamp=at or old.timestamp,
                                      journal_at=(at or old.journal_at) if journal else new.journal_at)
        except Exception as e:
            logger.error(f"Crew status update ignored: {e}")
            return
        self._notify()

    def adopt(self, replayed: ShipStatus) -> bool:
        """Take a replayed state's journal fields if newer; keep live Status.json fuel (plan ruling 5)."""
        with self._lock:
            current = self._state
            if replayed.journal_at is None or (
                    current.journal_at is not None and replayed.journal_at <= current.journal_at):
                return False
            stamps = [t for t in (current.timestamp, replayed.timestamp) if t is not None]
            new = replace(replayed,
                          fuel_main=replayed.fuel_main if current.fuel_main is None else current.fuel_main,
                          fuel_reserve=(replayed.fuel_reserve if current.fuel_reserve is None
                                        else current.fuel_reserve),
                          timestamp=max(stamps) if stamps else None)
            if new == current:
                return False
            self._state = new
        self._notify()
        return True

    def _notify(self) -> None:
        state = self._state  # the newest, as in LocationTracker._notify
        for observer in list(self._observers):
            try:
                observer(state)
            except Exception as e:
                logger.error(f"Crew status observer failed: {e}")
