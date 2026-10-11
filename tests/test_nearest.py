"""Nearest system by coordinates (A11): distance from `system`, readable failures. No network."""
import sys
from types import SimpleNamespace

import pytest

from edmrn.ext.api import ApiError, ApiFailure
from fakes import Root, Var, sync_threading

_hook = sys.excepthook
import edmrn.app as app_module  # noqa: E402  (sets sys.excepthook at import)
sys.excepthook = _hook

SOL = {"name": "Sol", "x": 0.0, "y": 0.0, "z": 0.0, "distance": 4.38}


@pytest.fixture
def dialogs(monkeypatch):
    seen = []

    class Confirm:
        def __init__(self, app, title, message):
            seen.append(("confirm", title, message))

        def get_result(self):
            return False

    monkeypatch.setattr(app_module, "ConfirmDialog", Confirm)
    monkeypatch.setattr(app_module, "ErrorDialog", lambda app, title, text: seen.append(("error", title, text)))
    monkeypatch.setattr(app_module, "InfoDialog", lambda app, title, text: seen.append(("info", title, text)))
    monkeypatch.setattr(app_module, "WarningDialog", lambda app, title, text: seen.append(("warning", title, text)))
    monkeypatch.setattr(app_module, "threading", sync_threading())
    return seen


def search(monkeypatch, outcome, coords=("1", "2", "3")):
    calls = []

    def fake_nearest(x, y, z):
        calls.append((x, y, z))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(app_module, "nearest_system", fake_nearest)
    app = SimpleNamespace(nearest_x_entry=Var(coords[0]), nearest_y_entry=Var(coords[1]),
                          nearest_z_entry=Var(coords[2]), root=Root(), _log=lambda m: None)
    app._show_nearest_results = lambda data, x, y, z: app_module.EDMRN_App._show_nearest_results(app, data, x, y, z)
    app_module.EDMRN_App._find_nearest_system_by_coordinates(app)
    app.root.run()
    return calls


def test_distance_comes_from_system(monkeypatch, dialogs):
    assert search(monkeypatch, SOL) == [(1.0, 2.0, 3.0)]
    [(kind, title, message)] = dialogs
    assert kind == "confirm" and "🌟 Sol" in message and "📏 Distance: 4.38 LY" in message


def test_failure_is_one_readable_sentence(monkeypatch, dialogs):
    search(monkeypatch, ApiError(ApiFailure("Spansh", "overloaded", 502)))
    assert dialogs == [("error", "API Error", "Spansh is overloaded (HTTP 502). Try again in a minute.")]


def test_no_system_found(monkeypatch, dialogs):
    search(monkeypatch, None)
    assert dialogs == [("info", "No Results", "No systems found near those coordinates")]


def test_bug_is_not_shown_raw(monkeypatch, dialogs):
    search(monkeypatch, KeyError("internal detail"))
    assert dialogs == [("error", "Error", "Unexpected error talking to Spansh.")]


def test_non_numeric_input_is_rejected_before_any_request(monkeypatch, dialogs):
    assert search(monkeypatch, SOL, coords=("1", "abc", "3")) == []
    assert dialogs == [("warning", "Input Error", "Coordinates must be numbers")]
