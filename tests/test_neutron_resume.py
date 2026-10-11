from types import SimpleNamespace

import pytest

from edmrn.ext.neutron_resume import Decision, NeutronAutosave, decide_resume, resume_route
from edmrn.ext.store import RouteMeta, Store

SYSTEMS = ["Sol", "A", "B", "C", "Colonia"]


@pytest.mark.parametrize("current,expected", [
    (None, Decision("continue", 2)),
    ("  ", Decision("continue", 2)),
    ("A", Decision("continue", 2)),
    ("b", Decision("advance", 2)),
    (" C ", Decision("advance", 3)),
    ("Colonia", Decision("advance", 4)),
    ("Sol", Decision("ask", 2)),
    ("Elsewhere", Decision("ask", 2)),
])
def test_decide_resume(current, expected):
    assert decide_resume(SYSTEMS, 2, current) == expected


def test_decide_resume_at_start_index_zero():
    assert decide_resume(SYSTEMS, 0, "Sol") == Decision("advance", 0)


class Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def waypoint(system, status="unvisited"):
    return {"system": system, "type": "Normal", "distance": 10.0, "jumps": 1, "status": status}


def make_app(route=None, index=0, last_request=None):
    router = SimpleNamespace(last_route=route or [], current_waypoint_index=index)
    if last_request is not None:
        router.last_request = last_request
    calls = []
    manager = SimpleNamespace(
        update_neutron_navigation=lambda: calls.append("nav"),
        update_neutron_statistics_from_loaded_route=lambda: calls.append("stats"),
        handle_neutron_system_jump=lambda name: calls.append(("jump", name)),
        copy_current_neutron_system=lambda auto=False: calls.append("copy"),
        get_current_system_from_journal=lambda: None)
    app = SimpleNamespace(
        root=SimpleNamespace(after=lambda ms, fn: calls.append(("after", ms))),
        neutron_router=router, neutron_manager=manager,
        neutron_range_var=Var("70"), neutron_boost_var=Var("x4 (Normal)"),
        neutron_from_autocomplete=Var(), neutron_to_autocomplete=Var(),
        _calculate_neutron_route=lambda: calls.append("calculate"))
    return app, calls


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "edmrn.db")
    yield s
    s.close()


def test_new_route_inserted_once_with_last_request(store):
    route = [waypoint("Nearest"), waypoint("Colonia")]
    app, _ = make_app(route, last_request={"from": "Nearest", "to": "Colonia", "range": 65.5, "boost": "x6"})
    autosave = NeutronAutosave(app, store)
    autosave.check()
    autosave.check()
    [summary] = store.list_neutron_routes()
    assert (summary.from_system, summary.to_system, summary.range_ly, summary.boost) == ("Nearest", "Colonia", 65.5, "x6")


def test_meta_falls_back_to_waypoints_and_tab_fields(store):
    app, _ = make_app([waypoint("Sol"), waypoint("Colonia")])
    app.neutron_range_var.set("48.2")
    app.neutron_boost_var.set("x6 (Caspian)")
    NeutronAutosave(app, store).check()
    [summary] = store.list_neutron_routes()
    assert (summary.from_system, summary.to_system, summary.range_ly, summary.boost) == ("Sol", "Colonia", 48.2, "x6")


def test_stale_last_request_for_other_destination_is_ignored(store):
    app, _ = make_app([waypoint("Sol"), waypoint("Colonia")],
                      last_request={"from": "X", "to": "Beagle Point", "range": 30.0, "boost": "x4"})
    NeutronAutosave(app, store).check()
    [summary] = store.list_neutron_routes()
    assert summary.to_system == "Colonia"
    assert summary.from_system == "Sol" and summary.range_ly == 70.0


def test_progress_change_updates_row_and_completes(store):
    route = [waypoint("Sol"), waypoint("Colonia")]
    app, _ = make_app(route)
    autosave = NeutronAutosave(app, store)
    autosave.check()
    route[0]["status"] = "visited"
    app.neutron_router.current_waypoint_index = 1
    autosave.check()
    rid = store.list_neutron_routes()[0].id
    assert store.load_neutron_route(rid).current_index == 1
    route[1]["status"] = "visited"
    autosave.check()
    assert store.load_neutron_route(rid).state == "completed"
    assert len(store.list_neutron_routes()) == 1


def test_upstream_backup_with_odd_values_is_normalised(store):
    route = [{"system": "Sol", "type": "Neutron", "distance": "n/a", "jumps": None, "status": "skipped"},
             {"system": "Colonia", "status": "visited"}]
    app, _ = make_app(route)
    NeutronAutosave(app, store).check()
    saved = store.load_neutron_route(store.list_neutron_routes()[0].id)
    assert [w["status"] for w in saved.waypoints] == ["unvisited", "visited"]
    assert saved.waypoints[0]["distance"] == 0.0 and saved.waypoints[0]["jumps"] == 0


def test_unsaveable_route_is_logged_once_not_retried(store, monkeypatch):
    app, _ = make_app([waypoint("Sol")])
    app.neutron_range_var.set("not a number")
    autosave = NeutronAutosave(app, store)
    errors = []
    monkeypatch.setattr("edmrn.ext.neutron_resume.logger.error", lambda msg: errors.append(msg))
    autosave.check()
    autosave.check()
    assert store.list_neutron_routes() == [] and len(errors) == 1


def test_store_failure_is_retried_next_tick(store, monkeypatch):
    app, _ = make_app([waypoint("Sol"), waypoint("Colonia")])
    autosave = NeutronAutosave(app, store)
    original = store.add_neutron_route
    attempts = []

    def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            import sqlite3
            raise sqlite3.OperationalError("database is locked")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "add_neutron_route", flaky)
    autosave._tick()
    autosave._tick()
    assert len(store.list_neutron_routes()) == 1


def test_tick_reschedules_itself(store):
    app, calls = make_app()
    autosave = NeutronAutosave(app, store)
    autosave.start()
    autosave._tick()
    assert calls.count(("after", 2000)) == 2


def _saved(store, index=1):
    return store.add_neutron_route(RouteMeta("Sol", "Colonia", 70.0, "x4"),
                                   [waypoint("Sol", "visited"), waypoint("A"), waypoint("Colonia")], index)


def test_resume_continue_does_not_duplicate(store):
    rid = _saved(store)
    app, calls = make_app()
    autosave = NeutronAutosave(app, store)
    decision = resume_route(app, store, autosave, rid, ask=lambda *a: pytest.fail("no ask expected"))
    autosave.check()
    resume_route(app, store, autosave, rid, ask=lambda *a: None)
    autosave.check()
    assert decision.kind == "continue" and "nav" in calls and "stats" in calls
    assert app.neutron_router.current_waypoint_index == 1
    assert len(store.list_neutron_routes()) == 1


def test_resume_advance_uses_upstream_jump_handler(store):
    rid = _saved(store)
    app, calls = make_app()
    app.neutron_manager.get_current_system_from_journal = lambda: "Colonia"
    resume_route(app, store, NeutronAutosave(app, store), rid, ask=lambda *a: pytest.fail("no ask"))
    assert ("jump", "Colonia") in calls


def test_resume_ask_back_copies_waypoint(store):
    rid = _saved(store)
    app, calls = make_app()
    app.neutron_manager.get_current_system_from_journal = lambda: "Elsewhere"
    resume_route(app, store, NeutronAutosave(app, store), rid, ask=lambda *a: "back")
    assert "copy" in calls and "calculate" not in calls


def test_resume_ask_replot_replaces_saved_route(store):
    rid = _saved(store)
    app, calls = make_app()
    app.neutron_manager.get_current_system_from_journal = lambda: "Elsewhere"
    autosave = NeutronAutosave(app, store)
    resume_route(app, store, autosave, rid, ask=lambda *a: "replot")
    assert "calculate" in calls
    assert (app.neutron_from_autocomplete.get(), app.neutron_to_autocomplete.get()) == ("Elsewhere", "Colonia")
    assert app.neutron_range_var.get() == "70" and app.neutron_boost_var.get() == "x4 (Normal)"

    app.neutron_router.last_route = [waypoint("Elsewhere"), waypoint("B"), waypoint("Colonia")]
    app.neutron_router.current_waypoint_index = 0
    app.neutron_router.last_request = {"from": "Elsewhere", "to": "Colonia", "range": 70.0, "boost": "x4"}
    autosave.check()

    [summary] = store.list_neutron_routes()
    assert summary.id == rid and summary.from_system == "Elsewhere" and summary.total == 3


def test_replace_request_is_cleared_once_answered(store):
    rid = _saved(store)
    app, _ = make_app()
    app.neutron_manager.get_current_system_from_journal = lambda: "Elsewhere"
    autosave = NeutronAutosave(app, store)
    resume_route(app, store, autosave, rid, ask=lambda *a: "replot")
    app.neutron_router.last_route = [waypoint("Elsewhere"), waypoint("B"), waypoint("Colonia")]
    app.neutron_router.last_request = {"from": "Elsewhere", "to": "Colonia", "range": 70.0, "boost": "x4"}
    autosave.check()
    app.neutron_router.last_route = [waypoint("Other"), waypoint("Colonia")]
    app.neutron_router.last_request = {"from": "Other", "to": "Colonia", "range": 70.0, "boost": "x4"}
    autosave.check()
    rows = {s.id: s for s in store.list_neutron_routes()}
    assert len(rows) == 2
    assert rows[rid].from_system == "Elsewhere" and rows[rid].total == 3


def test_failed_replot_does_not_hijack_unrelated_route(store):
    rid = _saved(store)
    app, _ = make_app()
    app.neutron_manager.get_current_system_from_journal = lambda: "Elsewhere"
    autosave = NeutronAutosave(app, store)
    resume_route(app, store, autosave, rid, ask=lambda *a: "replot")
    app.neutron_router.last_route = [waypoint("Sol"), waypoint("Beagle Point")]
    autosave.check()
    assert len(store.list_neutron_routes()) == 2
    assert store.load_neutron_route(rid).meta.to_system == "Colonia"


def test_deleted_row_of_active_route_is_logged_once_not_recreated(store, monkeypatch):
    route = [waypoint("Sol"), waypoint("Colonia")]
    app, _ = make_app(route)
    autosave = NeutronAutosave(app, store)
    autosave._tick()
    store.delete_neutron_route(store.list_neutron_routes()[0].id)
    errors = []
    monkeypatch.setattr("edmrn.ext.neutron_resume.logger.error", lambda msg: errors.append(msg))
    route[0]["status"] = "visited"
    app.neutron_router.current_waypoint_index = 1
    for _ in range(3):
        autosave._tick()
    assert len(errors) == 1 and store.list_neutron_routes() == []
    route[1]["status"] = "visited"
    app.neutron_router.current_waypoint_index = 0
    autosave._tick()
    autosave._tick()
    assert len(errors) == 1 and store.list_neutron_routes() == []


def test_unchanged_route_does_not_write_progress(store, monkeypatch):
    app, _ = make_app([waypoint("Sol"), waypoint("Colonia")])
    autosave = NeutronAutosave(app, store)
    autosave.check()
    count = []
    monkeypatch.setattr(store, "update_neutron_progress", lambda *a, **k: count.append(a))
    for _ in range(3):
        autosave.check()
    assert count == []


def test_waypoint_count_mismatch_is_logged_once(store, monkeypatch):
    route = [waypoint("Sol"), waypoint("A"), waypoint("Colonia")]
    app, _ = make_app(route)
    autosave = NeutronAutosave(app, store)
    autosave.check()
    errors = []
    monkeypatch.setattr("edmrn.ext.neutron_resume.logger.error", lambda msg: errors.append(msg))
    route[:] = [waypoint("Sol", "visited"), waypoint("Colonia")]
    for _ in range(3):
        autosave.check()
    assert len(errors) == 1


def test_resume_at_destination_completes_route(store):
    rid = store.add_neutron_route(RouteMeta("Sol", "Colonia", 70.0, "x4"),
                                  [waypoint("Sol", "visited"), waypoint("A", "visited"), waypoint("Colonia")], 2)
    app, calls = make_app()
    app.neutron_manager.get_current_system_from_journal = lambda: "Colonia"

    def jump(name):
        router = app.neutron_router
        for i, wp in enumerate(router.last_route):
            if wp["system"] == name:
                for earlier in router.last_route[:i + 1]:
                    earlier["status"] = "visited"
                router.current_waypoint_index = i
    app.neutron_manager.handle_neutron_system_jump = jump
    autosave = NeutronAutosave(app, store)
    resume_route(app, store, autosave, rid, ask=lambda *a: pytest.fail("no ask"))
    autosave.check()
    assert store.load_neutron_route(rid).state == "completed"


def test_replace_target_deleted_falls_back_to_new_row(store):
    rid = _saved(store)
    app, _ = make_app()
    app.neutron_manager.get_current_system_from_journal = lambda: "Elsewhere"
    autosave = NeutronAutosave(app, store)
    resume_route(app, store, autosave, rid, ask=lambda *a: "replot")
    store.delete_neutron_route(rid)
    app.neutron_router.last_route = [waypoint("Elsewhere"), waypoint("Colonia")]
    app.neutron_router.last_request = {"from": "Elsewhere", "to": "Colonia", "range": 70.0, "boost": "x4"}
    autosave.check()
    [summary] = store.list_neutron_routes()
    assert summary.from_system == "Elsewhere"


from edmrn.ext.neutron_resume import install


def test_install_opens_default_store_and_starts(isolated_home):
    app, calls = make_app()
    autosave = install(app)
    try:
        assert autosave is app.neutron_autosave and install(app) is autosave
        assert str(isolated_home) in app.edmrn_store.path
        assert ("after", 2000) in calls
    finally:
        app.edmrn_store.close()


def test_install_survives_unexpected_open_error(isolated_home, monkeypatch):
    def boom():
        raise PermissionError("denied")
    monkeypatch.setattr(Store, "open_default", staticmethod(boom))
    app, _ = make_app()
    assert install(app) is None
    assert "denied" in app.edmrn_store_error


def test_install_with_corrupt_database_disables_feature(isolated_home):
    from edmrn.config import AppConfig
    from pathlib import Path
    data_dir = Path(AppConfig.get_app_data_path())
    (data_dir / "edmrn.db").write_bytes(b"garbage" * 200)
    app, _ = make_app()
    assert install(app) is None
    assert app.neutron_autosave is None and "edmrn.db" in app.edmrn_store_error


def test_format_updated_local_and_fallback():
    from datetime import datetime, timezone
    from edmrn.ext.neutron_resume import format_updated
    expected = datetime(2026, 10, 11, 22, 49, 38, tzinfo=timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')
    assert format_updated('2026-10-11T22:49:38Z') == expected
    assert format_updated('not a date') == 'not a date'
    assert format_updated(None) == 'None'
