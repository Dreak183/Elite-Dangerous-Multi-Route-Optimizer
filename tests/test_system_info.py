"""System Info on edmrn.ext.api (A10): encoded names, unknown systems, partial failures. No network."""
import sys
import threading
from types import SimpleNamespace

import pytest

from edmrn.ext.api import ApiError, ApiFailure
from edmrn.ext.system_info import fetch_system_data
from fakes import FakeResponse, Root, Script, SyncThread, Var, Widget

_hook = sys.excepthook
import edmrn.app as app_module  # noqa: E402  (sets sys.excepthook at import)
sys.excepthook = _hook

SOL = {"name": "Sol", "id": 27, "id64": 10477373803, "coords": {"x": 0, "y": 0, "z": 0},
       "information": {"allegiance": "Federation"}}
BODIES = FakeResponse(200, {"id": 27, "name": "Sol", "bodyCount": 1, "bodies": [{"name": "Earth"}]})
STATIONS = FakeResponse(200, {"id": 27, "name": "Sol", "stations": [{"name": "Abraham Lincoln"}]})
FACTIONS = FakeResponse(200, {"id": 27, "name": "Sol", "factions": [{"name": "Mother Gaia"}]})
GEC = FakeResponse(200, {"name": "Sol", "type": "Star", "region": "Inner Orion Spur", "summary": "Home"})


def test_full_fetch_sends_encoded_params_and_builds_data():
    name = "Jackson's & #Lighthouse+"
    fake = Script(FakeResponse(200, {**SOL, "name": name}), BODIES, STATIONS, FACTIONS, GEC)
    data = fetch_system_data(name, call=fake)
    urls = [args[1] for args, _ in fake.calls]
    assert urls == ["https://www.edsm.net/api-v1/system", "https://www.edsm.net/api-system-v1/bodies",
                    "https://www.edsm.net/api-system-v1/stations", "https://www.edsm.net/api-system-v1/factions",
                    "https://edastro.com/gec/json/id64/10477373803"]
    assert all(name not in url for url in urls)
    assert fake.calls[0][1]["params"]["systemName"] == name and fake.calls[0][1]["params"]["showId"] == 1
    assert [kwargs["params"] for _, kwargs in fake.calls[1:4]] == [{"systemName": name}] * 3
    assert data["bodies"] == [{"name": "Earth"}] and data["stations"] == [{"name": "Abraham Lincoln"}]
    assert data["factions"] == [{"name": "Mother Gaia"}] and data["notes"] == []
    assert data["gmp"].startswith("Sol\nType: Star")
    assert data["url"] == "https://www.edsm.net/en/system/id/27/name/Jackson%27s+%26+%23Lighthouse%2B"


@pytest.mark.parametrize("body", [[], {}])
def test_unknown_system_is_an_error_not_fetched(body):
    fake = Script(FakeResponse(200, body))
    assert fetch_system_data("Sool", call=fake) == {"error": "System 'Sool' is not known to EDSM."}
    assert len(fake.calls) == 1


def test_basic_failure_is_readable():
    fake = Script(ApiError(ApiFailure("EDSM", "offline")))
    assert fetch_system_data("Sol", call=fake) == {"error": "No connection to EDSM."}


def test_partial_failure_keeps_the_rest_and_adds_notes():
    fake = Script(FakeResponse(200, dict(SOL)), ApiError(ApiFailure("EDSM", "timeout", None, "10 s")),
                  STATIONS, FakeResponse(503, text="<html>"), ApiError(ApiFailure("EDAstro", "offline")))
    data = fetch_system_data("Sol", call=fake)
    assert data["name"] == "Sol" and data["stations"] == [{"name": "Abraham Lincoln"}]
    assert data["bodies"] == [] and data["factions"] == [] and data["gmp"] == ""
    assert data["notes"] == [
        "Bodies could not be loaded: EDSM did not answer within 10 s.",
        "Factions could not be loaded: EDSM is overloaded (HTTP 503). Try again in a minute.",
        "Galactic POI data could not be loaded: No connection to EDAstro.",
    ]


def test_list_shaped_factions_body():
    fake = Script(FakeResponse(200, dict(SOL)), BODIES, STATIONS, FakeResponse(200, [{"name": "F"}]), GEC)
    assert fetch_system_data("Sol", call=fake)["factions"] == [{"name": "F"}]


def test_edastro_without_poi_is_silent():
    fake = Script(FakeResponse(200, dict(SOL)), BODIES, STATIONS, FACTIONS, FakeResponse(404, text="not found"))
    data = fetch_system_data("Sol", call=fake)
    assert data["gmp"] == "" and data["notes"] == []


@pytest.mark.parametrize("id64", ["../../admin", True, None, 1.5])
def test_non_integer_id64_never_reaches_a_url(id64):
    fake = Script(FakeResponse(200, {**SOL, "id64": id64, "id": None}), BODIES, STATIONS, FACTIONS)
    data = fetch_system_data("Sol", call=fake)
    assert len(fake.calls) == 4 and data["gmp"] == ""
    assert data["url"] == "https://www.edsm.net/en/system?systemName=Sol"


def test_bug_is_unexpected_and_writes_no_crash_log(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = Script(TypeError("internal"))
    assert fetch_system_data("Sol", call=fake) == {"error": "Unexpected error talking to EDSM."}
    assert not (tmp_path / "edmrn_crash.log").exists()


# --- status line in the app (fake app, no Tk) --------------------------------

def run_fetch(monkeypatch, data):
    monkeypatch.setattr(threading, "Thread", SyncThread)
    monkeypatch.setattr(app_module, "fetch_system_data", lambda name: data)
    shown = []
    section = SimpleNamespace(
        system_info_entry=Var("Sol"), system_info_status=Widget(),
        update_system_info=shown.append, update_planetary_access=lambda b: None,
        update_stations=lambda s: None, update_gmp=lambda g: None, update_trivia=lambda t: None)
    app = SimpleNamespace(system_info_section=section, root=Root())
    app.fetch_edsm_system_data = lambda name: app_module.EDMRN_App.fetch_edsm_system_data(app, name)
    app_module.EDMRN_App._system_info_fetch_callback(app, "Sol")
    app.root.run()
    return section.system_info_status.state["text"], shown


def test_status_shows_unknown_system_sentence(monkeypatch):
    text, shown = run_fetch(monkeypatch, {"error": "System 'Sol' is not known to EDSM."})
    assert text == "System 'Sol' is not known to EDSM." and shown == []


def test_status_lists_notes_after_fetch(monkeypatch):
    data = {**SOL, "bodies": [], "stations": [], "gmp": "", "notes": ["Bodies could not be loaded: No connection to EDSM."]}
    text, shown = run_fetch(monkeypatch, data)
    assert text == "Data fetched for Sol\nBodies could not be loaded: No connection to EDSM."
    assert shown and shown[0]["name"] == "Sol"
