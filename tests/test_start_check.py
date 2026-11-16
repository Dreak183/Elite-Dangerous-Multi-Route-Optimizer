"""edmrn.ext.start_check and api.spansh_coords: the Spansh check for plotter fields. No network."""
import pytest

from edmrn.ext.api import (
    SPANSH_NEAREST_URL, SPANSH_SEARCH_URL, ApiError, ApiFailure, call, spansh_coords,
)
from edmrn.ext.start_check import StartCheck, check_plot_system
from fakes import FakeResponse, Sleeps

XT = "Blae Phoe XT-A e1459"
SS = "Blae Phoe SS-B d751"
XT_COORDS = (768.375, -565.90625, 14325.34375)


class Fake:
    """Routes by URL; a dict value is the JSON body of a 200, an exception is raised."""

    def __init__(self, search=None, nearest=None):
        self.routes = {SPANSH_SEARCH_URL: search, SPANSH_NEAREST_URL: nearest}
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.routes[url]
        if isinstance(item, BaseException):
            raise item
        return FakeResponse(200, item)

    def urls(self):
        return [u for u, _ in self.calls]


def nearest_body(name, distance):
    return {"system": {"name": name, "x": 1, "y": 2, "z": 3, "distance": distance}}


def test_spansh_coords_exact_match():
    fake = Fake(search={"results": [{"name": "Sol Sector", "x": 9, "y": 9, "z": 9},
                                    {"name": "SOL", "x": 0, "y": 0, "z": 0}]})
    assert spansh_coords("sol", call=fake) == (0.0, 0.0, 0.0)
    assert spansh_coords("Nowhere", call=fake) is None
    n = len(fake.calls)
    assert spansh_coords("  ", call=fake) is None
    assert len(fake.calls) == n


def test_spansh_coords_failure_raises():
    def wrapped(method, url, **kwargs):
        return call(method, url, transport=lambda *a, **k: FakeResponse(503),
                    sleep=Sleeps(), **kwargs)

    with pytest.raises(ApiError) as info:
        spansh_coords("Sol", call=wrapped)
    assert info.value.failure.kind == "overloaded"


def test_known_system():
    fake = Fake(search={"results": [{"name": "Sol", "x": 0, "y": 0, "z": 0}]})
    assert check_plot_system("Sol", (0, 0, 0), call=fake) == \
        StartCheck("Sol", "✓ 'Sol' is known to Spansh.", True)
    assert SPANSH_NEAREST_URL not in fake.urls()


def test_unknown_uses_nearest():
    fake = Fake(search={"results": []}, nearest=nearest_body(SS, 10.6249))
    result = check_plot_system(XT, XT_COORDS, call=fake)
    assert result == StartCheck(
        SS, f"⚠ '{XT}' is not known to Spansh - using the nearest known system '{SS}' "
            "(10.6 ly away).", False)
    params = dict(fake.calls)[SPANSH_NEAREST_URL]["params"]
    assert params == {"x": 768.375, "y": -565.90625, "z": 14325.34375}


def test_nearest_is_same_system_counts_as_known():
    fake = Fake(search={"results": []}, nearest=nearest_body("blae phoe xt-a E1459", 0.0))
    assert check_plot_system(XT, XT_COORDS, call=fake) == \
        StartCheck(XT, f"✓ '{XT}' is known to Spansh.", True)


def test_unknown_without_coords():
    fake = Fake(search={"results": []})
    result = check_plot_system(XT, None, call=fake)
    assert result == StartCheck(
        XT, f"⚠ '{XT}' is not known to Spansh, and the journal has no coordinates for it.", False)
    assert SPANSH_NEAREST_URL not in fake.urls()


def test_nearest_answers_nothing():
    fake = Fake(search={"results": []}, nearest={})
    assert check_plot_system(XT, XT_COORDS, call=fake) == StartCheck(
        XT, f"⚠ '{XT}' is not known to Spansh, and Spansh named no known system near it.", False)


FAILURE = "⚠ Could not check 'X' at Spansh: Spansh is overloaded (HTTP 502). Try again in a minute."


def test_search_failure():
    err = ApiError(ApiFailure("Spansh", "overloaded", 502))
    assert check_plot_system("X", (1, 2, 3), call=Fake(search=err)) == StartCheck("X", FAILURE, False)


def test_nearest_failure():
    err = ApiError(ApiFailure("Spansh", "overloaded", 502))
    fake = Fake(search={"results": []}, nearest=err)
    assert check_plot_system("X", (1, 2, 3), call=fake) == StartCheck("X", FAILURE, False)


def test_unexpected_exception():
    result = check_plot_system("X", (1, 2, 3), call=Fake(search=ValueError("boom")))
    assert result.name == "X" and result.known is False
    assert result.hint.endswith("Unexpected error talking to Spansh.")
