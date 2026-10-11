"""Custom Route on lookup_system (A9): readable messages, stop on a missing start or end. No network."""
from types import SimpleNamespace

import pytest

import edmrn.custom_route as cr
from edmrn.ext.api import ApiFailure, Lookup
from fakes import Root, sync_threading

NOT_FOUND = "System 'Sool' was not found at Spansh or EDSM. Check the spelling."
FAILED = ("Could not look up 'Busy': Spansh is overloaded (HTTP 502). Try again in a minute; "
          "System 'Busy' is not known to EDSM.")


class Journal:
    def __init__(self, system, coords):
        self.system, self.coords = system, coords

    def get_current_system(self):
        return self.system

    def get_current_coordinates(self):
        return self.coords


class Lookups:
    def __init__(self, results):
        self.results, self.names = dict(results), []

    def __call__(self, name):
        self.names.append(name)
        return self.results.get(name, Lookup.not_found(name))


@pytest.fixture
def lookups(monkeypatch):
    fake = Lookups({
        "Sol": Lookup("Sol", (0.0, 0.0, 0.0), "Spansh"),
        "Blae Phoe XT-A e1459": Lookup("Blae Phoe XT-A e1459", (768.375, -565.90625, 14325.34375), "EDSM"),
        "Sool": Lookup.not_found("Sool"),
        "Busy": Lookup.failed("Busy", [ApiFailure("Spansh", "overloaded", 502)]),
    })
    monkeypatch.setattr(cr, "lookup_system", fake)
    return fake


def manager(journal=None):
    app = SimpleNamespace(journal_monitor=journal, jump_range=SimpleNamespace(get=lambda: "50"))
    return cr.CustomRouteManager(app)


def test_add_system_uses_lookup_coordinates(lookups):
    mgr = manager()
    assert mgr.add_system("Blae Phoe XT-A e1459") is True
    assert (mgr.systems[0]["x"], mgr.systems[0]["y"], mgr.systems[0]["z"]) == (768.375, -565.90625, 14325.34375)


def test_not_found_records_spelling_message(lookups):
    mgr = manager()
    assert mgr.add_system("Sool") is False and mgr.systems == []
    assert mgr.lookup_errors == {"sool": NOT_FOUND}


def test_service_failure_records_both_reasons(lookups):
    mgr = manager()
    assert mgr.add_system("Busy") is False
    assert mgr.lookup_errors == {"busy": FAILED}


def test_add_system_async_shows_the_lookup_message(lookups, monkeypatch):
    shown = []
    monkeypatch.setattr(cr, "threading", sync_threading())
    monkeypatch.setattr(cr, "WarningDialog", lambda app, title, text: shown.append((title, text)))
    tab = cr.CustomRouteTab.__new__(cr.CustomRouteTab)
    tab.app = SimpleNamespace(root=Root(), journal_monitor=None)
    tab.manager = cr.CustomRouteManager(tab.app)
    tab._add_system_async("Busy")
    tab.app.root.run()
    assert shown == [("System Lookup", FAILED)] and tab.manager.lookup_errors == {}


def two_systems(mgr):
    mgr.add_system_direct("A", 10.0, 0.0, 0.0)
    mgr.add_system_direct("B", 20.0, 0.0, 0.0)


def test_unknown_start_stops_optimisation(lookups):
    mgr = manager()
    two_systems(mgr)
    mgr.set_starting_system("Sool")
    assert mgr.optimize_route() is False
    assert mgr.optimize_error == NOT_FOUND and mgr.optimized_route == [] and not mgr.is_optimized


def test_unreachable_end_stops_optimisation(lookups):
    mgr = manager()
    two_systems(mgr)
    mgr.set_ending_system("Busy")
    assert mgr.optimize_route() is False and mgr.optimize_error == FAILED


def test_found_start_and_end_are_kept(lookups):
    mgr = manager()
    two_systems(mgr)
    mgr.set_starting_system("Sol")
    mgr.set_ending_system("Blae Phoe XT-A e1459")
    assert mgr.optimize_route() is True and mgr.optimize_error is None
    names = [s["name"] for s in mgr.optimized_route]
    assert names[0] == "Sol" and names[-1] == "Blae Phoe XT-A e1459"


def test_journal_start_unknown_to_both_services_uses_journal_coords(lookups):
    mgr = manager(Journal("Pyra Dryoae AB-C d1", (5.0, 5.0, 5.0)))
    two_systems(mgr)
    mgr.set_starting_system("Pyra Dryoae AB-C d1")
    assert mgr.optimize_route() is True
    assert mgr.optimized_route[0]["name"] == "Pyra Dryoae AB-C d1"
    assert (mgr.optimized_route[0]["x"], mgr.optimized_route[0]["y"]) == (5.0, 5.0)
    assert lookups.names == []


def test_optimize_failure_dialog_shows_the_reason(monkeypatch):
    shown = []
    monkeypatch.setattr(cr, "WarningDialog", lambda app, title, text: shown.append((title, text)))
    tab = cr.CustomRouteTab.__new__(cr.CustomRouteTab)
    tab.app = SimpleNamespace(_log=lambda m: None)
    tab.optimize_btn = SimpleNamespace(configure=lambda **k: None)
    tab.manager = SimpleNamespace(optimize_error=NOT_FOUND, systems=[], optimized_route=[])
    tab._on_optimize_complete(False)
    assert shown == [("Route Optimization", NOT_FOUND)]
