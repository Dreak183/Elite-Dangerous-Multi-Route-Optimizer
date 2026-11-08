"""Ship state of the tracked commander: drive, cargo, fuel and the supercharge, from the journal.

Spec: docs/superpowers/specs/2026-11-08-ship-range-and-neutron-start-design.md (Ship state).
Fuel and cargo come from ``Status.json`` while the commander sits in the main ship (flag bit 24);
the journal's ``Cargo`` event is only a fallback. Values are validated here because the range
model does not validate its inputs and ``json.loads`` accepts ``NaN`` and ``Infinity``.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from edmrn.ext.crew_events import bounded_number
from edmrn.ext.fsd import Drive, drive_from_loadout, jump_range, supercharge_multiplier
from edmrn.ext.crew_status import IN_MAIN_SHIP
from edmrn.ext.journal_replay import replay_newest
from edmrn.ext.location import journal_dir_for
from edmrn.fuel_tracker import FuelTracker
from edmrn.journal import JournalMonitor
from edmrn.logger import get_logger

logger = get_logger('Ship')

MAX_TONS = 10_000.0


@dataclass(frozen=True)
class ShipState:
    commander: Optional[str] = None
    system: Optional[str] = None
    drive: Optional[Drive] = None
    max_jump_range: Optional[float] = None
    cargo: Optional[float] = None
    fuel_main: Optional[float] = None
    star_class: Optional[str] = None
    supercharge: Optional[float] = None
    pending_star: Optional[tuple[str, str]] = None
    journal_at: Optional[str] = None

    @property
    def laden(self) -> Optional[float]:
        """Range with the current cargo and a full main tank."""
        if self.drive is None:
            return None
        return jump_range(self.drive, _finite(self.cargo, 0.0), self.drive.fuel_capacity)

    @property
    def current(self) -> Optional[float]:
        """Range with the current cargo and the current main-tank fuel."""
        if self.drive is None or not _is_finite(self.fuel_main):
            return None
        return jump_range(self.drive, _finite(self.cargo, 0.0), self.fuel_main)


Observer = Callable[[ShipState], None]


def _is_finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:  # an int beyond the float range
        return False


def _finite(value: Any, default: float) -> float:
    return float(value) if _is_finite(value) else default


def _str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _tons(value: Any) -> Optional[float]:
    """A real finite number of tons in 0 ... MAX_TONS (bool rejected), else None."""
    return bounded_number(value, MAX_TONS)


def _positive(value: Any) -> Optional[float]:
    return float(value) if _is_finite(value) and value > 0 else None


def _apply_rules(state: ShipState, event: dict) -> ShipState:
    name = event.get('event')
    if name in ('LoadGame', 'Commander'):
        commander = _str(event.get('Commander' if name == 'LoadGame' else 'Name'))
        if commander is None:
            return state
        if state.commander is not None and state.commander != commander:
            return ShipState(commander=commander)
        return replace(state, commander=commander)
    if name == 'Loadout':
        return replace(state, drive=drive_from_loadout(event),
                       max_jump_range=_positive(event.get('MaxJumpRange')))
    if name == 'StartJump':
        system, star = _str(event.get('StarSystem')), _str(event.get('StarClass'))
        if event.get('JumpType') != 'Hyperspace' or system is None or star is None:
            return state
        return replace(state, pending_star=(system, star))
    if name == 'FSDJump':
        system = _str(event.get('StarSystem'))
        if system is None:
            return state
        pending = state.pending_star
        star = pending[1] if pending and pending[0] == system else None
        return replace(state, system=system, star_class=star, supercharge=None, pending_star=None)
    if name == 'CarrierJump':
        system = _str(event.get('StarSystem'))
        if system is None:
            return state
        return replace(state, system=system, star_class=None, supercharge=None, pending_star=None)
    if name == 'Location':
        system = _str(event.get('StarSystem'))
        if system is None:
            return state
        if system == state.system:
            return state
        return replace(state, system=system, star_class=None, supercharge=None, pending_star=None)
    if name == 'Scan':
        star = _str(event.get('StarType'))
        distance = event.get('DistanceFromArrivalLS')
        if star is None or not _is_finite(distance) or distance != 0:
            return state
        scanned = event.get('StarSystem')
        if scanned is not None and scanned != state.system:
            return state
        return replace(state, star_class=star)
    if name == 'JetConeBoost':
        boost = _positive(event.get('BoostValue'))
        return state if boost is None else replace(state, supercharge=boost)
    if name == 'Cargo':
        count = _tons(event.get('Count'))
        if event.get('Vessel') != 'Ship' or count is None:
            return state
        return replace(state, cargo=count)
    return state


def apply_event(state: ShipState, event: Any) -> ShipState:
    """Next state after one journal event; the same object when nothing changed."""
    if not isinstance(event, dict):
        return state
    new = _apply_rules(state, event)
    if replace(new, journal_at=state.journal_at) == state:
        return state
    return replace(new, journal_at=_str(event.get('timestamp')) or state.journal_at)


def apply_status(state: ShipState, status: Any) -> ShipState:
    """Next state after one ``Status.json`` read; the same object when nothing changed."""
    if not isinstance(status, dict):
        return state
    flags = status.get('Flags')
    if not isinstance(flags, int) or isinstance(flags, bool) or not flags & IN_MAIN_SHIP:
        return state
    fuel = status.get('Fuel')
    main = _tons(fuel.get('FuelMain')) if isinstance(fuel, dict) else None
    cargo = _tons(status.get('Cargo'))
    new = state
    if main is not None:
        new = replace(new, fuel_main=main)
    if cargo is not None:
        new = replace(new, cargo=cargo)
    return state if new == state else new


class ShipTracker:
    """Thread-safe holder of the current ShipState; notifies observers on change."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = ShipState()
        self._observers: list[Observer] = []

    @property
    def state(self) -> ShipState:
        return self._state

    def subscribe(self, observer: Observer) -> None:
        self._observers.append(observer)

    def feed(self, event: Any) -> None:
        self._update(lambda state: apply_event(state, event), "event")

    def feed_status(self, status: Any) -> None:
        self._update(lambda state: apply_status(state, status), "status")

    def _update(self, step: Callable[[ShipState], ShipState], what: str) -> None:
        try:
            with self._lock:
                old = self._state
                new = step(old)
                self._state = new
        except Exception as e:
            logger.error(f"Ship {what} ignored: {e}")
            return
        if new is not old:
            self._notify()

    def adopt(self, state: ShipState) -> bool:
        """Take a replayed state unless the tracker already holds newer journal data.

        The live fuel and cargo (from Status.json) win over the replay's. When live journal data
        is newer but no live Loadout was seen, the replay's drive and maximum range are still taken.
        """
        with self._lock:
            current = self._state
            if current.journal_at is not None and (
                    state.journal_at is None or state.journal_at <= current.journal_at):
                if current.drive is not None or current.max_jump_range is not None:
                    return False
                merged = replace(current, drive=state.drive,
                                 max_jump_range=state.max_jump_range)
            else:
                merged = replace(
                    state,
                    fuel_main=(current.fuel_main if current.fuel_main is not None
                               else state.fuel_main),
                    cargo=current.cargo if current.cargo is not None else state.cargo)
            if merged == current:
                return False
            self._state = merged
        self._notify()
        return True

    def _notify(self) -> None:
        # Deliver the current state: another thread may have moved on since the lock was released.
        state = self._state
        for observer in list(self._observers):
            try:
                observer(state)
            except Exception as e:
                logger.error(f"Ship observer failed: {e}")


def cold_start(journal_dir: Optional[str], tracker: ShipTracker,
               max_files: int = 5) -> Optional[ShipState]:
    """Replay the newest journal files until one yields a drive; hand it to the tracker."""
    replay = replay_newest(
        journal_dir, ShipTracker,
        lambda state: state.drive is not None or state.max_jump_range is not None, max_files)
    if replay is None:
        return None
    tracker.adopt(replay.state)
    return replay.state


def start_boost(app, from_system: str) -> Optional[float]:
    """Range multiplier of the first jump from ``from_system`` known from the journal, or None.

    Order: the pending jet cone boost, then the arrival star's neutron or white dwarf multiplier.
    Only valid when ``from_system`` is the tracked current system. Never raises.
    """
    try:
        state = app.ship_tracker.state
        if not isinstance(from_system, str) or not state.system:
            return None
        if from_system.strip().casefold() != state.system.strip().casefold():
            return None
        boost = state.supercharge
        if boost is None:
            boost = supercharge_multiplier(state.drive, state.star_class)
        return float(boost) if boost > 1 else None
    except Exception:
        return None


def install(app) -> ShipTracker:
    """Wire the tracker into a running EDMRN_App. Called once from EDMRN_App.__init__."""
    existing = getattr(app, 'ship_tracker', None)
    if isinstance(existing, ShipTracker):
        return existing
    tracker = ShipTracker()
    app.ship_tracker = tracker
    JournalMonitor.event_listeners.append(tracker.feed)
    FuelTracker.status_listeners.append(tracker.feed_status)

    try:
        from edmrn.ext.ship_panel import binding_for  # Task 3 removes this guard
    except ImportError:
        binding_for = None
    if binding_for is not None:
        def apply_on_tk() -> None:
            # Read the tracker here, not at notify time: a stale snapshot must never win.
            try:
                binding_for(app).on_state(tracker.state)
            except Exception as e:
                logger.error(f"Range update failed: {e}")

        def on_change(_state: ShipState) -> None:
            try:
                app.root.after(0, apply_on_tk)
            except Exception as e:
                logger.debug(f"Range update skipped: {e}")

        tracker.subscribe(on_change)

    threading.Thread(target=cold_start, args=(journal_dir_for(app), tracker),
                     name='edmrn-ship-cold-start', daemon=True).start()
    return tracker
