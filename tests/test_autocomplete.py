"""Autocomplete (A12): no retries, 60 s cooldown per service, one shared cache, Tk-thread delivery."""
import sys
from types import SimpleNamespace

import edmrn.custom_route as cr
import edmrn.system_autocomplete as sa
from edmrn.ext.api import ApiError, ApiFailure, ServiceCooldown
from fakes import Clock, FakeResponse, Root

_hook = sys.excepthook
import edmrn.app as app_module  # noqa: E402  (sets sys.excepthook at import)
sys.excepthook = _hook


class Calls:
    """Fake edmrn.ext.api.call keyed by service; each service replays its own queue."""

    def __init__(self, **queues):
        self.queues = {k: list(v) for k, v in queues.items()}
        self.calls = []

    def __call__(self, method, url, *, service, **kwargs):
        self.calls.append((service, kwargs))
        item = self.queues[service].pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def spansh(*names):
    return FakeResponse(200, {"results": [{"name": n} for n in names]})


def edsm(*names):
    return FakeResponse(200, [{"name": n} for n in names])


def completer(monkeypatch, fake, clock=None):
    monkeypatch.setattr(sa, "call", fake)
    c = sa.SystemAutocompleter()
    c.cooldown = ServiceCooldown(60.0, clock=clock or Clock())
    return c


def test_requests_have_no_retries_and_short_timeout(monkeypatch):
    fake = Calls(Spansh=[spansh("Sol", "Solati")])
    assert completer(monkeypatch, fake).get_suggestions("Sol") == ["Sol", "Solati"]
    service, kwargs = fake.calls[0]
    assert service == "Spansh" and kwargs["retries"] == 0 and kwargs["timeout"] == 5
    assert kwargs["json"]["filters"] == {"name": {"value": "Sol"}}


def test_spansh_timeout_falls_back_to_edsm_and_cools_down(monkeypatch):
    clock = Clock()
    timeout = ApiError(ApiFailure("Spansh", "timeout", None, "5 s"))
    fake = Calls(Spansh=[timeout, spansh("Colonia")], EDSM=[edsm("Sol"), edsm("Achenar")])
    c = completer(monkeypatch, fake, clock)
    assert c.get_suggestions("Sol") == ["Sol"]
    clock.now += 30
    assert c.get_suggestions("Ach") == ["Achenar"]
    assert [s for s, _ in fake.calls] == ["Spansh", "EDSM", "EDSM"]
    clock.now += 31
    assert c.get_suggestions("Col") == ["Colonia"]
    assert [s for s, _ in fake.calls][-1] == "Spansh"


def test_rejection_does_not_cool_down(monkeypatch):
    fake = Calls(Spansh=[FakeResponse(400, {"error": "bad"}), spansh("Sol")], EDSM=[edsm()])
    c = completer(monkeypatch, fake)
    assert c.get_suggestions("Sol") == []
    assert c.get_suggestions("Sol") == ["Sol"]


def test_edsm_failure_cools_down_edsm_only(monkeypatch):
    clock = Clock()
    fake = Calls(Spansh=[spansh(), spansh()], EDSM=[ApiError(ApiFailure("EDSM", "offline"))])
    c = completer(monkeypatch, fake, clock)
    assert c.get_suggestions("Xyz") == [] and c.get_suggestions("Xyw") == []
    assert [s for s, _ in fake.calls] == ["Spansh", "EDSM", "Spansh"]


def test_app_delivers_suggestions_on_the_tk_loop():
    delivered, root = [], Root()

    def worker_style(query, callback, max_results=10):
        callback(["Sol"])  # what the worker thread does

    app = SimpleNamespace(root=root, system_autocompleter=SimpleNamespace(get_suggestions_async=worker_style))
    app_module.EDMRN_App._get_system_suggestions(app, "Sol", delivered.append)
    assert delivered == []
    root.run()
    assert delivered == [["Sol"]]


def test_custom_route_uses_the_apps_autocompleter():
    seen = []
    tab = cr.CustomRouteTab.__new__(cr.CustomRouteTab)
    tab.app = SimpleNamespace(_get_system_suggestions=lambda query, callback: seen.append((query, callback)))
    tab._get_suggestions("Sol", print)
    assert seen == [("Sol", print)]
