"""NeutronManager.calculate_neutron_route: range check, start coordinates, substitution note."""
from types import SimpleNamespace

import pytest

import edmrn.neutron_manager as nm


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Label:
    def __init__(self):
        self.text = ""

    def configure(self, **kw):
        self.text = kw.get("text", self.text)


class Router:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def calculate_route_async(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.result is not None:
            args[4](self.result)

    def save_neutron_route(self, folder):
        pass


class Journal:
    def __init__(self, system, coords):
        self.system, self.coords = system, coords

    def get_current_system(self):
        return self.system

    def get_current_coordinates(self):
        return self.coords


def make_app(jump_range="40", from_system="Here", journal=None, result=None):
    log = []
    app = SimpleNamespace(
        neutron_from_autocomplete=Var(from_system), neutron_to_autocomplete=Var("Sol"),
        neutron_range_var=Var(jump_range), neutron_boost_var=Var("x4"),
        neutron_calculate_btn=Label(), neutron_info_label=Label(),
        neutron_router=Router(result), journal_monitor=journal,
        root=SimpleNamespace(after=lambda ms, fn: fn()), _log=log.append,
        _start_journal_monitor=lambda: None, _ensure_overlay_started=lambda name: None,
    )
    return app, log


@pytest.fixture
def errors(monkeypatch):
    seen = []
    monkeypatch.setattr(nm, "ErrorDialog", lambda app, title, text: seen.append(text))
    return seen


@pytest.mark.parametrize("value", ["0", "10", "-5", "abc"])
def test_range_not_above_10_is_rejected(value, errors):
    app, _ = make_app(jump_range=value)
    nm.NeutronManager(app).calculate_neutron_route()
    assert app.neutron_router.calls == []
    assert errors and ("greater than 10 LY" in errors[0] or "valid jump range" in errors[0])


def test_range_above_10_is_accepted(errors):
    app, _ = make_app(jump_range="10.5")
    nm.NeutronManager(app).calculate_neutron_route()
    assert errors == [] and len(app.neutron_router.calls) == 1


def test_start_coords_passed_when_from_is_current_system(errors):
    app, _ = make_app(from_system="HERE", journal=Journal("Here", (1.0, 2.0, 3.0)))
    nm.NeutronManager(app).calculate_neutron_route()
    assert app.neutron_router.calls[0][1]["start_coords"] == (1.0, 2.0, 3.0)


def test_no_start_coords_when_from_is_another_system(errors):
    app, _ = make_app(from_system="Other", journal=Journal("Here", (1.0, 2.0, 3.0)))
    nm.NeutronManager(app).calculate_neutron_route()
    assert app.neutron_router.calls[0][1]["start_coords"] is None


def test_no_start_coords_without_journal_monitor(errors):
    app, _ = make_app(journal=None)
    nm.NeutronManager(app).calculate_neutron_route()
    assert app.neutron_router.calls[0][1]["start_coords"] is None


def test_substitution_note_is_shown_logged_and_from_field_updated(errors, monkeypatch):
    result = {"success": True, "waypoints": [], "total_distance": 1.0, "total_jumps": 1,
              "neutron_jumps": 0, "note": "NOTE TEXT", "start_system": "Near Sys"}
    app, log = make_app(result=result)
    mgr = nm.NeutronManager(app)
    for name in ("update_neutron_statistics", "update_neutron_navigation",
                 "copy_current_neutron_system"):
        monkeypatch.setattr(mgr, name, lambda *a, **k: None)
    mgr.calculate_neutron_route()
    assert app.neutron_info_label.text.endswith("\nNOTE TEXT")
    assert "NOTE TEXT" in log
    assert app.neutron_from_autocomplete.get() == "Near Sys"
