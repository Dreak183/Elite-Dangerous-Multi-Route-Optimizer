"""Save neutron routes automatically and resume them (spec 2026-10-11-neutron-route-resume-design)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from edmrn.ext.store import RouteMeta, SavedRoute, Store, StoreError
from edmrn.logger import get_logger

logger = get_logger('NeutronResume')

BOOST_LABELS = {'x4': 'x4 (Normal)', 'x6': 'x6 (Caspian)'}


@dataclass(frozen=True)
class Decision:
    kind: str
    index: int


def _key(name: Any) -> str:
    return name.strip().casefold() if isinstance(name, str) else ''


def decide_resume(systems: list, saved_index: int, current: Optional[str]) -> Decision:
    """Where to continue a saved route, given the commander's current system."""
    key = _key(current)
    if not key:
        return Decision('continue', saved_index)
    for i, system in enumerate(systems):
        if i >= saved_index - 1 and _key(system) == key:
            if i <= saved_index:
                return Decision('continue', saved_index)
            return Decision('advance', i)
    return Decision('ask', saved_index)


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _normalise(route: list) -> list:
    """Copy of upstream waypoint dicts in the store's shape; odd upstream values are coerced."""
    result = []
    for wp in route:
        wp = wp if isinstance(wp, dict) else {}
        jumps = wp.get('jumps')
        result.append({
            'system': wp.get('system') if isinstance(wp.get('system'), str) else '',
            'type': wp.get('type') if isinstance(wp.get('type'), str) else 'Normal',
            'distance': _number(wp.get('distance'), 0.0),
            'jumps': jumps if isinstance(jumps, int) and not isinstance(jumps, bool) else 0,
            'status': 'visited' if wp.get('status') == 'visited' else 'unvisited',
        })
    return result


class NeutronAutosave:
    INTERVAL_MS = 2000

    def __init__(self, app, store: Store) -> None:
        self.app = app
        self.store = store
        self._route_obj = None
        self._route_id: Optional[int] = None
        self._snapshot = None
        self._pending: Optional[tuple[int, str]] = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._schedule()

    def stop(self) -> None:
        self._running = False

    def _schedule(self) -> None:
        try:
            self.app.root.after(self.INTERVAL_MS, self._tick)
        except Exception as e:
            logger.debug(f"Neutron autosave not rescheduled: {e}")

    def _tick(self) -> None:
        try:
            self.check()
        except Exception as e:
            logger.warning(f"Neutron autosave will retry: {e}")
        finally:
            if self._running:
                self._schedule()

    def adopt(self, route_obj: list, route_id: int) -> None:
        """Map a route the user resumed to its row, so it is not inserted again."""
        self._route_obj, self._route_id = route_obj, route_id
        self._snapshot = self._snap(route_obj)
        self._pending = None

    def request_replace(self, route_id: int, to_system: str) -> None:
        """The next new route to `to_system` replaces the waypoints of `route_id`."""
        self._pending = (route_id, _key(to_system))

    def _snap(self, route: list) -> tuple:
        router = self.app.neutron_router
        return (router.current_waypoint_index,
                tuple(wp.get('status') if isinstance(wp, dict) else None for wp in route))

    def _index(self, count: int) -> int:
        index = self.app.neutron_router.current_waypoint_index
        if not isinstance(index, int) or isinstance(index, bool):
            return 0
        return min(max(index, 0), count - 1)

    def _meta(self, waypoints: list) -> RouteMeta:
        request = getattr(self.app.neutron_router, 'last_request', None)
        to_system = waypoints[-1]['system']
        if isinstance(request, dict) and _key(request.get('to')) == _key(to_system):
            return RouteMeta(request.get('from') or waypoints[0]['system'], to_system,
                             request.get('range'), request.get('boost'))
        try:
            range_ly = float(self.app.neutron_range_var.get())
        except (TypeError, ValueError):
            range_ly = 0.0
        boost = 'x6' if 'x6' in str(self.app.neutron_boost_var.get()) else 'x4'
        return RouteMeta(waypoints[0]['system'], to_system, range_ly, boost)

    def check(self) -> None:
        route = self.app.neutron_router.last_route
        if not route:
            return
        snapshot = self._snap(route)
        if route is not self._route_obj:
            self._save_new(route, snapshot)
        elif self._route_id is not None and snapshot != self._snapshot:
            waypoints = _normalise(route)
            self.store.update_neutron_progress(self._route_id, self._index(len(waypoints)),
                                               [wp['status'] for wp in waypoints])
            self._snapshot = snapshot

    def _save_new(self, route: list, snapshot: tuple) -> None:
        waypoints = _normalise(route)
        index = self._index(len(waypoints))
        try:
            meta = self._meta(waypoints)
            if self._pending and self._pending[1] == _key(meta.to_system):
                route_id = self._pending[0]
                self.store.replace_neutron_waypoints(route_id, meta, waypoints, index)
            else:
                route_id = self.store.add_neutron_route(meta, waypoints, index)
        except (ValueError, KeyError) as e:
            logger.error(f"Neutron route not saved: {e}")
            self._route_obj, self._route_id, self._snapshot = route, None, snapshot
            return
        self._pending = None
        self._route_obj, self._route_id, self._snapshot = route, route_id, snapshot


Ask = Callable[[Any, SavedRoute, Optional[str]], Optional[str]]


def resume_route(app, store: Store, autosave: NeutronAutosave, route_id: int, ask: Ask) -> Decision:
    """Load a saved route into the router and continue according to decide_resume."""
    saved = store.load_neutron_route(route_id)
    router = app.neutron_router
    route = [dict(wp) for wp in saved.waypoints]
    router.last_route = route
    router.current_waypoint_index = saved.current_index
    autosave.adopt(route, saved.id)
    app.neutron_manager.update_neutron_navigation()
    current = app.neutron_manager.get_current_system_from_journal()
    decision = decide_resume([wp['system'] for wp in route], saved.current_index, current)
    if decision.kind == 'advance':
        app.neutron_manager.handle_neutron_system_jump(current)
    elif decision.kind == 'ask':
        choice = ask(app, saved, current)
        if choice == 'back':
            app.neutron_manager.copy_current_neutron_system(auto=True)
        elif choice == 'replot':
            autosave.request_replace(saved.id, saved.meta.to_system)
            app.neutron_from_autocomplete.set(current)
            app.neutron_to_autocomplete.set(saved.meta.to_system)
            app.neutron_range_var.set(f"{saved.meta.range_ly:g}")
            app.neutron_boost_var.set(BOOST_LABELS.get(saved.meta.boost, BOOST_LABELS['x4']))
            app._calculate_neutron_route()
    return decision
