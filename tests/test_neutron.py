"""NeutronRouter on edmrn.ext.spansh: readable errors, substituted start, polling limits. No network."""
import pytest

import edmrn.neutron as neutron
from edmrn.ext.api import ApiError, ApiFailure
from edmrn.ext.spansh import RouteSubmission
from fakes import FakeResponse

DONE = {"result": {"system_jumps": [
    {"system": "Near Sys", "distance_jumped": 0.0, "jumps": 1, "neutron_star": False},
    {"system": "Sol", "distance_jumped": 100.0, "jumps": 3, "neutron_star": True}]}}


@pytest.fixture
def router():
    return neutron.NeutronRouter()


def fake_submit(response, note=None, used_from="A", seen=None):
    def submit(url, params, **kwargs):
        if seen is not None:
            seen.update(url=url, params=params, **kwargs)
        return RouteSubmission(response, note, used_from)
    return submit


def test_rejected_start_shows_spansh_reason(router, monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job", fake_submit(FakeResponse(400, {"error": "Could not find starting system"})))
    assert router.calculate_route("A", "B", 40.0) == {
        "success": False, "error": "Spansh rejected the request: Could not find starting system"}
    assert router.is_calculating is False


def test_overloaded_submit_is_readable(router, monkeypatch):
    def overloaded(*a, **k):
        raise ApiError(ApiFailure("Spansh", "overloaded", 502))
    monkeypatch.setattr(neutron, "submit_route_job", overloaded)
    assert router.calculate_route("A", "B", 40.0)["error"] == \
        "Spansh is overloaded (HTTP 502). Try again in a minute."


def test_success_carries_note_and_start_system(router, monkeypatch):
    seen, polled = {}, {}
    monkeypatch.setattr(neutron, "submit_route_job",
                        fake_submit(FakeResponse(202, {"job": "j1"}), "NOTE", "Near Sys", seen))

    def fake_poll(job_id, *, max_wait, on_progress=None):
        polled.update(job_id=job_id, max_wait=max_wait)
        return DONE
    monkeypatch.setattr(neutron, "poll_route_result", fake_poll)
    result = router.calculate_route("Far Sys", "Sol", 40.0, start_coords=(1.0, 2.0, 3.0))
    assert result["success"] and result["note"] == "NOTE" and result["start_system"] == "Near Sys"
    assert seen["params"]["from"] == "Far Sys" and seen["start_coords"] == (1.0, 2.0, 3.0)
    assert seen["timeout"] == 30 and seen["url"] == "https://spansh.co.uk/api/route"
    assert polled == {"job_id": "j1", "max_wait": 180}
    assert [w["system"] for w in router.last_route] == ["Near Sys", "Sol"]


def test_poll_failure_is_readable(router, monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job", fake_submit(FakeResponse(202, {"job": "j1"})))

    def timed_out(job_id, *, max_wait, on_progress=None):
        raise ApiError(ApiFailure("Spansh", "timeout", None, "180 s; the job may still finish on Spansh's website"))
    monkeypatch.setattr(neutron, "poll_route_result", timed_out)
    assert router.calculate_route("A", "B", 40.0)["error"] == \
        "Spansh did not answer within 180 s; the job may still finish on Spansh's website."


def test_progress_reports_elapsed_seconds(router, monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job", fake_submit(FakeResponse(202, {"job": "j1"})))

    def fake_poll(job_id, *, max_wait, on_progress=None):
        on_progress(12.4)
        return DONE
    monkeypatch.setattr(neutron, "poll_route_result", fake_poll)
    messages = []
    router.calculate_route("A", "B", 40.0, progress_callback=messages.append)
    assert "Calculating... (12 s)" in messages


def test_bug_in_processing_is_not_shown_raw(router, monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job", fake_submit(FakeResponse(202, {"job": "j1"})))
    monkeypatch.setattr(neutron, "poll_route_result", lambda job_id, **k: {"result": {"system_jumps": [None]}})
    assert router.calculate_route("A", "B", 40.0)["error"] == "Unexpected error talking to Spansh."


def _eol_prou(first_neutron=True):
    import copy

    from _neutron_helpers import EOL_PROU
    data = copy.deepcopy(EOL_PROU)
    data["result"]["system_jumps"][0]["neutron_star"] = first_neutron
    return data


def test_start_boost_applies_after_poll(router, monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job",
                        fake_submit(FakeResponse(202, {"job": "j1"}), None, "Eol Prou WN-T d3-2412"))
    monkeypatch.setattr(neutron, "poll_route_result", lambda job_id, *, max_wait, on_progress=None: _eol_prou())
    result = router.calculate_route("Eol Prou WN-T d3-2412", "Colonia", 75.0, start_boost=6.0)
    assert result["success"] and len(result["waypoints"]) == 2
    assert "Supercharged start (×6)" in result["note"]


def test_journal_boost_dropped_when_start_substituted(router, monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job",
                        fake_submit(FakeResponse(202, {"job": "j1"}), "SUBST", "Near Sys"))
    monkeypatch.setattr(neutron, "poll_route_result",
                        lambda job_id, *, max_wait, on_progress=None: _eol_prou(first_neutron=False))
    result = router.calculate_route("Eol Prou WN-T d3-2412", "Colonia", 75.0, start_boost=6.0)
    assert result["success"] and len(result["waypoints"]) == 3
    assert result["note"] == "SUBST"


def test_substituted_start_falls_back_to_the_spansh_flag_with_the_request_multiplier(router, monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job",
                        fake_submit(FakeResponse(202, {"job": "j1"}), "SUBST", "Near Sys"))
    monkeypatch.setattr(neutron, "poll_route_result",
                        lambda job_id, *, max_wait, on_progress=None: _eol_prou(first_neutron=True))
    result = router.calculate_route("Eol Prou WN-T d3-2412", "Colonia", 75.0, fsd_boost="x6", start_boost=1.0)
    assert result["success"] and len(result["waypoints"]) == 2
    assert "(×6)" in result["note"]
