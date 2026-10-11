"""Galaxy Plotter on edmrn.ext.spansh (A5-A7): reasons stay visible, button state, Tk reads. No network."""
import sys
from types import SimpleNamespace

import pytest

import edmrn.galaxy_plotter as gp
from edmrn.ext.api import ApiError, ApiFailure
from edmrn.ext.spansh import RouteSubmission
from fakes import FakeResponse, Root, SyncThread, Text, Var, Widget

_hook = sys.excepthook
import edmrn.app as app_module  # noqa: E402  (sets sys.excepthook at import)
sys.excepthook = _hook

DONE = {"job": "j1", "result": {"distance": 100.0, "system_jumps": [
    {"system": "Near Sys", "distance_jumped": 0.0, "jumps": 0, "neutron_star": False, "refuel": False},
    {"system": "Sol", "distance_jumped": 100.0, "jumps": 2, "neutron_star": True, "refuel": True}]}}


def plot(plotter=None, **kwargs):
    plotter = plotter or gp.GalaxyPlotter()
    return plotter.plot_route("Far Sys", "Sol", ship_build="", range_ly=50.0, **kwargs)


# --- GalaxyPlotter -----------------------------------------------------------

def test_rejection_is_returned_as_reason(monkeypatch):
    monkeypatch.setattr(gp, "submit_spansh_job", lambda url, params, **k: RouteSubmission(
        FakeResponse(400, {"error": "range must be greater than 10 LY"}), None, "Far Sys"))
    assert plot() == {"error": "Spansh rejected the request: range must be greater than 10 LY"}


def test_success_passes_start_coords_and_carries_note(monkeypatch):
    seen, polled = {}, {}

    def fake_submit(url, params, **kwargs):
        seen.update(url=url, params=params, **kwargs)
        return RouteSubmission(FakeResponse(202, {"job": "j1"}), "NOTE", "Near Sys")

    def fake_poll(job_id, *, max_wait, on_progress=None):
        polled.update(job_id=job_id, max_wait=max_wait)
        return dict(DONE)

    monkeypatch.setattr(gp, "submit_spansh_job", fake_submit)
    monkeypatch.setattr(gp, "poll_route_result", fake_poll)
    result = plot(start_coords=(1.0, 2.0, 3.0))
    assert result["note"] == "NOTE" and result["result"] == DONE["result"]
    assert seen["start_coords"] == (1.0, 2.0, 3.0) and seen["timeout"] == 60
    assert seen["url"] == "https://spansh.co.uk/api/route"
    assert seen["params"]["from"] == "Far Sys" and seen["params"]["range"] == 50.0
    assert polled == {"job_id": "j1", "max_wait": 300}


def test_retry_progress_text_is_the_neutron_wording(monkeypatch):
    def fake_submit(url, params, **kwargs):
        kwargs["on_retry"](1, ApiFailure("Spansh", "overloaded", 502))
        return RouteSubmission(FakeResponse(202, {"job": "j1"}), None, "Far Sys")

    monkeypatch.setattr(gp, "submit_spansh_job", fake_submit)
    shown = []
    gp.GalaxyPlotter().submit_route_job("Far Sys", "Sol", ship_build="", range_ly=50.0,
                                        progress_callback=shown.append)
    assert "Spansh busy, retrying (1/2)..." in shown


def test_poll_timeout_reason(monkeypatch):
    monkeypatch.setattr(gp, "submit_spansh_job",
                        lambda url, params, **k: RouteSubmission(FakeResponse(202, {"job": "j1"}), None, "A"))

    def timed_out(job_id, *, max_wait, on_progress=None):
        raise ApiError(ApiFailure("Spansh", "timeout", None, "300 s; the job may still finish on Spansh's website"))
    monkeypatch.setattr(gp, "poll_route_result", timed_out)
    assert plot() == {"error": "Spansh did not answer within 300 s; the job may still finish on Spansh's website."}


def test_overloaded_submit_reason(monkeypatch):
    def overloaded(url, params, **k):
        raise ApiError(ApiFailure("Spansh", "overloaded", 503))
    monkeypatch.setattr(gp, "submit_spansh_job", overloaded)
    assert plot() == {"error": "Spansh is overloaded (HTTP 503). Try again in a minute."}


def test_bug_is_unexpected_not_raw(monkeypatch):
    def broken(url, params, **k):
        raise KeyError("internal")
    monkeypatch.setattr(gp, "submit_spansh_job", broken)
    assert plot() == {"error": "Unexpected error talking to Spansh."}


# --- EDMRN_App galaxy flow (fake app, no Tk) ---------------------------------

class Journal:
    def __init__(self, system, coords):
        self.system, self.coords = system, coords

    def get_current_system(self):
        return self.system

    def get_current_coordinates(self):
        return self.coords


class StartRecordingThread(SyncThread):
    log = None

    def start(self):
        StartRecordingThread.log.append(("thread-start",))
        super().start()


def make_app(monkeypatch, result, source="Far Sys", journal=None):
    log = []
    StartRecordingThread.log = log
    monkeypatch.setattr(app_module, "threading", SimpleNamespace(Thread=StartRecordingThread))
    plotter = gp.GalaxyPlotter()
    calls = []

    def fake_plot_route(**kwargs):
        log.append(("plot",))
        calls.append(kwargs)
        return result

    plotter.plot_route = fake_plot_route
    app = SimpleNamespace(
        galaxy_source_autocomplete=Var(source), galaxy_dest_autocomplete=Var("Sol"),
        galaxy_jump_range_var=Var("50"), galaxy_ship_build=Var(""), galaxy_boost_var=Var("x6 (Caspian)"),
        galaxy_cargo_var=Var("0"), galaxy_reserve_fuel_var=Var("0"),
        galaxy_already_supercharged=Var(False, log, "already_supercharged"),
        galaxy_use_injections=Var(False, log, "use_injections"),
        galaxy_exclude_secondary=Var(True, log, "exclude_secondary"),
        galaxy_refuel_every=Var(True, log, "refuel_every"),
        galaxy_algorithm_var=Var("optimistic", log, "algorithm"),
        galaxy_output=Text(), galaxy_calculate_btn=Widget(state="normal", text="Optimize & Track"),
        galaxy_current_btn=Widget(), galaxy_prev_btn=Widget(), galaxy_next_btn=Widget(),
        galaxy_export_btn=Widget(), galaxy_plotter=plotter, journal_monitor=journal,
        current_backup_folder=None, root=Root(), logged=[],
        galaxy_route_waypoints=[], galaxy_current_waypoint_index=0,
    )
    app._log = app.logged.append
    for name in ("_update_galaxy_systems_list", "_update_galaxy_statistics", "_update_galaxy_navigation",
                 "_copy_current_galaxy_system", "_start_journal_monitor", "_ensure_overlay_started"):
        setattr(app, name, lambda *a, **k: None)
    app._display_galaxy_route_result = lambda r: app_module.EDMRN_App._display_galaxy_route_result(app, r)
    app._display_galaxy_route_error = lambda m: app_module.EDMRN_App._display_galaxy_route_error(app, m)
    return app, calls, log


def calculate(app):
    app_module.EDMRN_App._calculate_galaxy_route(app)


def test_failure_reason_stays_visible_and_button_comes_back(monkeypatch):
    app, _, _ = make_app(monkeypatch, {"error": "Spansh rejected the request: Could not find starting system"})
    calculate(app)
    assert app.galaxy_calculate_btn.state["state"] == "disabled"
    app.root.run()
    assert app.galaxy_calculate_btn.state["state"] == "normal"
    assert app.galaxy_calculate_btn.state["text"] == "Optimize & Track"
    assert "Spansh rejected the request: Could not find starting system" in app.galaxy_output.content
    assert "No route data returned" not in app.galaxy_output.content


def test_tk_variables_are_read_before_the_worker_starts(monkeypatch):
    app, calls, log = make_app(monkeypatch, {"error": "x"})
    calculate(app)
    start = log.index(("thread-start",))
    reads = [i for i, entry in enumerate(log) if entry[0] == "get"]
    assert len(reads) == 5 and max(reads) < start
    assert calls[0]["routing_algorithm"] == "optimistic" and calls[0]["exclude_secondary"] is True


def test_journal_coords_passed_when_source_is_current_system(monkeypatch):
    app, calls, _ = make_app(monkeypatch, {"error": "x"}, source="far sys",
                             journal=Journal("Far Sys", (1.0, 2.0, 3.0)))
    calculate(app)
    assert calls[0]["start_coords"] == (1.0, 2.0, 3.0)


def test_no_journal_coords_for_another_source(monkeypatch):
    app, calls, _ = make_app(monkeypatch, {"error": "x"}, source="Other",
                             journal=Journal("Far Sys", (1.0, 2.0, 3.0)))
    calculate(app)
    assert calls[0]["start_coords"] is None


def test_success_shows_substitution_note(monkeypatch):
    app, _, _ = make_app(monkeypatch, {**DONE, "note": "NOTE TEXT"})
    calculate(app)
    app.root.run()
    assert "=== ROUTE SUMMARY ===" in app.galaxy_output.content and "NOTE TEXT" in app.galaxy_output.content
    assert app.galaxy_calculate_btn.state["state"] == "normal"
    assert "Galaxy Plotter: NOTE TEXT" in app.logged


def test_button_comes_back_even_if_display_fails(monkeypatch):
    app, _, _ = make_app(monkeypatch, dict(DONE))

    def broken():
        raise RuntimeError("malformed jump")
    app._update_galaxy_systems_list = broken
    calculate(app)
    with pytest.raises(RuntimeError):
        app.root.run()
    assert app.galaxy_calculate_btn.state["state"] == "normal"


# --- journal_start_coords ------------------------------------------------------

from edmrn.ext.spansh import journal_start_coords  # noqa: E402


class BrokenJournal:
    def get_current_system(self):
        raise OSError("journal locked")


@pytest.mark.parametrize("journal,name,expected", [
    (Journal("Far Sys", (1, 2, 3)), " FAR SYS ", (1.0, 2.0, 3.0)),
    (Journal("Far Sys", (1, 2, 3)), "Other", None),
    (Journal(None, (1, 2, 3)), "Far Sys", None),
    (Journal("Far Sys", None), "Far Sys", None),
    (Journal("Far Sys", ("1", 2, 3)), "Far Sys", None),
    (BrokenJournal(), "Far Sys", None),
    (None, "Far Sys", None),
    (Journal("Far Sys", (1, 2, 3)), "", None),
])
def test_journal_start_coords(journal, name, expected):
    assert journal_start_coords(journal, name) == expected
