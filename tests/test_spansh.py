"""Spansh error reasons, transient retries and nearest-start fallback. No network."""
import pytest
import requests

from edmrn.ext.spansh import (
    is_unknown_start, nearest_known_system, post_with_retry, spansh_error, submit_route_job,
)

UNKNOWN = {"error": "Could not find starting system"}


class FakeResponse:
    def __init__(self, status_code, body=None, text=None, reason=""):
        self.status_code = status_code
        self._body = body
        self.reason = reason
        self.text = text if text is not None else ("" if body is None else str(body))

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class Script:
    """Callable that records calls and replays queued responses or exceptions."""

    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Sleeps(list):
    def __call__(self, seconds):
        self.append(seconds)


PARAMS = {"efficiency": 60, "range": 40.0, "from": "Unknown Sys", "to": "Sol",
          "supercharge_multiplier": 4}
HEADERS = {"User-Agent": "EDMRN 3.0"}
NEAREST_OK = FakeResponse(200, {"system": {"name": "Blae Phoe SS-B d751", "distance": 10.6}})


# --- spansh_error -----------------------------------------------------------

def test_spansh_error_reads_json_error():
    assert spansh_error(FakeResponse(400, UNKNOWN)) == "Could not find starting system"


def test_spansh_error_html_body_falls_back_to_status():
    r = FakeResponse(502, None, text="<html>Bad Gateway</html>", reason="Bad Gateway")
    assert spansh_error(r) == "HTTP 502 Bad Gateway"


def test_spansh_error_empty_body_without_reason():
    assert spansh_error(FakeResponse(500)) == "HTTP 500"


# --- post_with_retry --------------------------------------------------------

def test_client_error_is_returned_without_retry():
    post, sleep = Script(FakeResponse(400, UNKNOWN)), Sleeps()
    r = post_with_retry("u", PARAMS, HEADERS, 30, post=post, sleep=sleep)
    assert r.status_code == 400 and len(post.calls) == 1 and sleep == []


def test_transient_then_success():
    post, sleep, seen = Script(FakeResponse(502), FakeResponse(502), FakeResponse(202)), Sleeps(), []
    r = post_with_retry("u", PARAMS, HEADERS, 30, on_retry=lambda n, why: seen.append((n, why)),
                        post=post, sleep=sleep)
    assert r.status_code == 202
    assert sleep == [2.0, 5.0]
    assert [n for n, _ in seen] == [1, 2]
    assert post.calls[0][1]["params"] == PARAMS and post.calls[0][1]["timeout"] == 30


def test_all_attempts_transient_returns_last_response():
    last = FakeResponse(502, reason="last")
    post = Script(FakeResponse(502), FakeResponse(503), last)
    assert post_with_retry("u", PARAMS, HEADERS, 30, post=post, sleep=Sleeps()) is last


def test_all_attempts_timeout_raises():
    post = Script(*[requests.exceptions.Timeout() for _ in range(3)])
    with pytest.raises(requests.exceptions.Timeout):
        post_with_retry("u", PARAMS, HEADERS, 30, post=post, sleep=Sleeps())
    assert len(post.calls) == 3


def test_connection_error_then_success():
    post = Script(requests.exceptions.ConnectionError(), FakeResponse(202))
    assert post_with_retry("u", PARAMS, HEADERS, 30, post=post, sleep=Sleeps()).status_code == 202


# --- is_unknown_start / nearest_known_system --------------------------------

def test_is_unknown_start_only_for_that_400():
    assert is_unknown_start(FakeResponse(400, {"error": "could not find starting SYSTEM"}))
    assert not is_unknown_start(FakeResponse(400, {"error": "range must be greater than 10 LY"}))
    assert not is_unknown_start(FakeResponse(502, UNKNOWN))


def test_nearest_known_system_parses_body():
    get = Script(NEAREST_OK)
    assert nearest_known_system(1.0, 2.0, 3.0, get=get) == ("Blae Phoe SS-B d751", 10.6)
    url, kw = get.calls[0]
    assert url == "https://spansh.co.uk/api/nearest"
    assert kw["params"] == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert "User-Agent" in kw["headers"]


@pytest.mark.parametrize("item", [
    FakeResponse(200, {"nope": 1}), FakeResponse(200, None, text="<html>"),
    FakeResponse(500, {"system": {"name": "X", "distance": 1}}),
    requests.exceptions.ConnectionError(),
])
def test_nearest_known_system_bad_input_is_none(item):
    assert nearest_known_system(1, 2, 3, get=Script(item)) is None


# --- submit_route_job -------------------------------------------------------

def submit(post, get=None, coords=(1.0, 2.0, 3.0), params=None):
    return submit_route_job("u", params if params is not None else PARAMS, HEADERS, 30,
                            start_coords=coords, post=post, get=get or Script(),
                            sleep=Sleeps())


def test_unknown_start_falls_back_to_nearest():
    params = dict(PARAMS)
    post = Script(FakeResponse(400, UNKNOWN), FakeResponse(202, {"job": "j"}))
    response, note, used = submit(post, Script(NEAREST_OK), params=params)
    assert response.status_code == 202
    assert post.calls[1][1]["params"]["from"] == "Blae Phoe SS-B d751"
    assert used == "Blae Phoe SS-B d751"
    assert note == ("Start system 'Unknown Sys' is not known to Spansh yet - plotted from "
                    "nearest known system 'Blae Phoe SS-B d751' (10.6 ly away).")
    assert params == PARAMS and params["from"] == "Unknown Sys"


def test_unknown_start_without_coords_does_not_look_up():
    get = Script()
    response, note, used = submit(Script(FakeResponse(400, UNKNOWN)), get, coords=None)
    assert response.status_code == 400 and note is None and used == "Unknown Sys"
    assert get.calls == []


def test_unknown_start_nearest_fails_returns_original_400():
    post = Script(FakeResponse(400, UNKNOWN))
    response, note, used = submit(post, Script(requests.exceptions.Timeout()))
    assert response.status_code == 400 and note is None and used == "Unknown Sys"
    assert len(post.calls) == 1


def test_nearest_same_name_is_not_resubmitted():
    same = FakeResponse(200, {"system": {"name": "unknown sys", "distance": 0.5}})
    post = Script(FakeResponse(400, UNKNOWN))
    response, note, _ = submit(post, Script(same))
    assert response.status_code == 400 and note is None and len(post.calls) == 1


def test_other_400_has_no_fallback():
    get = Script()
    post = Script(FakeResponse(400, {"error": "range must be greater than 10 LY"}))
    response, note, _ = submit(post, get)
    assert response.status_code == 400 and note is None and get.calls == []


# --- NeutronRouter ----------------------------------------------------------

@pytest.fixture
def router(monkeypatch):
    import edmrn.neutron as neutron
    monkeypatch.setattr(neutron.time, "sleep", lambda s: None)
    return neutron.NeutronRouter()


def test_router_shows_spansh_error_reason(router, monkeypatch):
    import edmrn.neutron as neutron
    monkeypatch.setattr(neutron, "submit_route_job",
                        lambda *a, **k: (FakeResponse(400, UNKNOWN), None, "A"))
    result = router.calculate_route("A", "B", 40.0)
    assert result == {"success": False,
                      "error": "API Error 400: Could not find starting system"}


def test_router_success_carries_note_and_start_system(router, monkeypatch):
    import edmrn.neutron as neutron
    seen = {}

    def fake_submit(url, params, headers, timeout, start_coords=None, on_retry=None):
        seen.update(params=params, coords=start_coords)
        return FakeResponse(202, {"job": "j1"}), "NOTE", "Near Sys"

    done = FakeResponse(200, {"result": {"system_jumps": [
        {"system": "Near Sys", "distance_jumped": 0.0, "jumps": 1, "neutron_star": False},
        {"system": "Sol", "distance_jumped": 100.0, "jumps": 3, "neutron_star": True}]}})
    monkeypatch.setattr(neutron, "submit_route_job", fake_submit)
    monkeypatch.setattr(neutron.requests, "get", lambda *a, **k: done)
    result = router.calculate_route("Far Sys", "Sol", 40.0, start_coords=(1.0, 2.0, 3.0))
    assert result["success"] and result["note"] == "NOTE" and result["start_system"] == "Near Sys"
    assert seen["params"]["from"] == "Far Sys" and seen["coords"] == (1.0, 2.0, 3.0)


def test_router_unsubstituted_start_has_no_note(router, monkeypatch):
    import edmrn.neutron as neutron
    monkeypatch.setattr(neutron, "submit_route_job",
                        lambda *a, **k: (FakeResponse(202, {"job": "j"}), None, "A"))
    monkeypatch.setattr(neutron.requests, "get",
                        lambda *a, **k: FakeResponse(200, {"result": {"system_jumps": []}}))
    result = router.calculate_route("A", "B", 40.0)
    assert result["success"] and result["note"] is None and result["start_system"] == "A"


def test_wait_tolerates_transient_failures(router, monkeypatch):
    import edmrn.neutron as neutron
    get = Script(FakeResponse(502), requests.exceptions.Timeout(), FakeResponse(202),
                 FakeResponse(200, {"result": {}}))
    monkeypatch.setattr(neutron.requests, "get", get)
    result = router._wait_for_result("j")
    assert result["success"] is True and len(get.calls) == 4


def test_wait_gives_up_after_three_consecutive_transient(router, monkeypatch):
    import edmrn.neutron as neutron
    get = Script(FakeResponse(502), FakeResponse(503), FakeResponse(504))
    monkeypatch.setattr(neutron.requests, "get", get)
    result = router._wait_for_result("j")
    assert result == {"success": False, "error": "Result API Error: 504"}


def test_wait_counter_resets_on_202(router, monkeypatch):
    import edmrn.neutron as neutron
    get = Script(FakeResponse(502), FakeResponse(502), FakeResponse(202), FakeResponse(502),
                 FakeResponse(502), FakeResponse(200, {"result": {}}))
    monkeypatch.setattr(neutron.requests, "get", get)
    assert router._wait_for_result("j")["success"] is True


def test_wait_other_status_is_fatal_with_reason(router, monkeypatch):
    import edmrn.neutron as neutron
    monkeypatch.setattr(neutron.requests, "get",
                        Script(FakeResponse(404, {"error": "Job not found"})))
    assert router._wait_for_result("j") == {
        "success": False, "error": "Result API Error 404: Job not found"}
