"""edmrn.ext.spansh: route job submission with start fallback (A5) and polling (A6). No network."""
import pytest

from edmrn.ext.api import ApiError, ApiFailure, Lookup
from edmrn.ext.spansh import RouteSubmission, is_unknown_start, poll_route_result, submit_route_job
from fakes import Clock, FakeResponse, Script

URL = "https://spansh.co.uk/api/route"
UNKNOWN = {"error": "Could not find starting system"}
PARAMS = {"efficiency": 60, "range": 40.0, "from": "Unknown Sys", "to": "Sol", "supercharge_multiplier": 4}
NEAREST_OK = FakeResponse(200, {"system": {"name": "Blae Phoe SS-B d751", "x": 1, "y": 2, "z": 3,
                                           "distance": 10.6}})
NOTE = ("Start system 'Unknown Sys' is not known to Spansh yet - plotted from nearest known system "
        "'Blae Phoe SS-B d751' (10.6 ly away).")


class Lookups:
    """Fake coords_lookup that records names."""

    def __init__(self, result):
        self.result, self.names = result, []

    def __call__(self, name, *, call):
        self.names.append(name)
        return self.result


def no_lookup(name, *, call):
    raise AssertionError("coords_lookup must not be called")


def submit(fake, coords=(1.0, 2.0, 3.0), coords_lookup=no_lookup, params=None):
    return submit_route_job(URL, dict(params or PARAMS), start_coords=coords, coords_lookup=coords_lookup,
                            timeout=30, call=fake)


# --- RouteSubmission.job_id ------------------------------------------------

def test_job_id_of_accepted_submission():
    assert RouteSubmission(FakeResponse(202, {"job": "j1", "status": "queued"}), None, "A").job_id() == "j1"


@pytest.mark.parametrize("response,text", [
    (FakeResponse(400, UNKNOWN), "Spansh rejected the request: Could not find starting system"),
    (FakeResponse(400, {"error": "range must be greater than 10 LY"}),
     "Spansh rejected the request: range must be greater than 10 LY"),
    (FakeResponse(202, {"status": "queued"}), "Unexpected error talking to Spansh (HTTP 202)."),
    (FakeResponse(202, text="<html>"), "Unexpected error talking to Spansh (HTTP 202)."),
])
def test_job_id_failures_are_readable(response, text):
    with pytest.raises(ApiError) as info:
        RouteSubmission(response, None, "A").job_id()
    assert str(info.value) == text


def test_is_unknown_start_only_for_that_400():
    assert is_unknown_start(FakeResponse(400, {"error": "could not find starting SYSTEM"}))
    assert not is_unknown_start(FakeResponse(400, {"error": "range must be greater than 10 LY"}))
    assert not is_unknown_start(FakeResponse(200, UNKNOWN))


# --- submit_route_job --------------------------------------------------------

def test_known_start_is_submitted_once_with_params_and_timeout():
    fake = Script(FakeResponse(202, {"job": "j"}))
    result = submit(fake)
    assert result.response.status_code == 202 and result.note is None and result.used_from == "Unknown Sys"
    args, kwargs = fake.calls[0]
    assert args == ("POST", URL)
    assert kwargs["params"] == PARAMS and kwargs["timeout"] == 30 and kwargs["service"] == "Spansh"


def test_unknown_start_with_journal_coords_uses_nearest():
    fake = Script(FakeResponse(400, UNKNOWN), NEAREST_OK, FakeResponse(202, {"job": "j"}))
    result = submit(fake)
    assert result.job_id() == "j" and result.note == NOTE and result.used_from == "Blae Phoe SS-B d751"
    assert fake.calls[1][1]["params"] == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert fake.calls[2][1]["params"] == {**PARAMS, "from": "Blae Phoe SS-B d751"}


def test_typed_unknown_start_uses_looked_up_coords():
    lookups = Lookups(Lookup("Unknown Sys", (7.0, 8.0, 9.0), "EDSM"))
    fake = Script(FakeResponse(400, UNKNOWN), NEAREST_OK, FakeResponse(202, {"job": "j"}))
    result = submit(fake, coords=None, coords_lookup=lookups)
    assert lookups.names == ["Unknown Sys"] and result.note == NOTE
    assert fake.calls[1][1]["params"] == {"x": 7.0, "y": 8.0, "z": 9.0}


def test_lookup_not_found_keeps_original_400():
    fake = Script(FakeResponse(400, UNKNOWN))
    result = submit(fake, coords=None, coords_lookup=Lookups(Lookup.not_found("Unknown Sys")))
    assert result.response.status_code == 400 and result.note is None and result.used_from == "Unknown Sys"
    assert len(fake.calls) == 1


def test_nearest_is_the_same_system_is_not_resubmitted():
    same = FakeResponse(200, {"system": {"name": "unknown sys", "x": 1, "y": 2, "z": 3, "distance": 0.5}})
    fake = Script(FakeResponse(400, UNKNOWN), same)
    result = submit(fake)
    assert result.response.status_code == 400 and result.note is None and len(fake.calls) == 2


def test_nearest_failure_keeps_original_400():
    fake = Script(FakeResponse(400, UNKNOWN), ApiError(ApiFailure("Spansh", "timeout", None, "10 s")))
    result = submit(fake)
    assert result.response.status_code == 400 and result.note is None


def test_other_400_has_no_fallback():
    fake = Script(FakeResponse(400, {"error": "range must be greater than 10 LY"}))
    result = submit(fake)
    assert result.note is None and len(fake.calls) == 1


def test_service_failure_on_submit_raises():
    fake = Script(ApiError(ApiFailure("Spansh", "overloaded", 502)))
    with pytest.raises(ApiError):
        submit(fake)


# --- poll_route_result -------------------------------------------------------

def poll(fake, max_wait=180, clock=None, progress=None):
    clock = clock or Clock()
    return poll_route_result("job-1", max_wait=max_wait, on_progress=progress, call=fake,
                             clock=clock, sleep=clock.sleep), clock


def overloaded():
    return ApiError(ApiFailure("Spansh", "overloaded", 502))


def test_202_then_200_returns_body_and_polls_without_retries():
    done = {"status": "ok", "result": {"system_jumps": []}}
    fake, seen = Script(FakeResponse(202, {}), FakeResponse(202, {}), FakeResponse(200, done)), []
    body, clock = poll(fake, progress=seen.append)
    assert body == done and clock.sleeps == [1.0, 1.5] and seen == [0.0, 1.0]
    args, kwargs = fake.calls[0]
    assert args == ("GET", "https://spansh.co.uk/api/results/job-1") and kwargs["retries"] == 0


def test_job_id_is_quoted_into_the_path():
    fake = Script(FakeResponse(200, {"result": {}}))
    poll_route_result("a/b?c", max_wait=180, call=fake, clock=Clock(), sleep=lambda s: None)
    assert fake.calls[0][0][1] == "https://spansh.co.uk/api/results/a%2Fb%3Fc"


def test_transient_twice_then_200():
    fake = Script(overloaded(), ApiError(ApiFailure("Spansh", "timeout", None, "15 s")),
                  FakeResponse(200, {"result": {}}))
    assert poll(fake)[0] == {"result": {}}


def test_chunked_encoding_error_during_polling_is_tolerated():
    import requests
    from edmrn.ext import api
    transport = Script(requests.exceptions.ChunkedEncodingError("connection broken"),
                       FakeResponse(200, {"result": {}}))
    real_call = lambda *a, **k: api.call(*a, transport=transport, sleep=lambda s: None, **k)  # noqa: E731
    assert poll(real_call)[0] == {"result": {}}
    assert len(transport.calls) == 2


def test_three_transient_tolerated_fourth_fails():
    assert poll(Script(overloaded(), overloaded(), overloaded(), FakeResponse(200, {"r": 1})))[0] == {"r": 1}
    with pytest.raises(ApiError) as info:
        poll(Script(overloaded(), overloaded(), overloaded(), overloaded()))
    assert info.value.failure.kind == "overloaded"


def test_202_resets_the_transient_count():
    fake = Script(overloaded(), overloaded(), overloaded(), FakeResponse(202, {}),
                  overloaded(), overloaded(), overloaded(), FakeResponse(200, {"r": 1}))
    assert poll(fake)[0] == {"r": 1}


def test_other_status_is_fatal_with_reason():
    with pytest.raises(ApiError) as info:
        poll(Script(FakeResponse(404, {"error": "Job not found"})))
    assert str(info.value) == "Spansh rejected the request: Job not found"


def test_unexpected_failure_is_not_tolerated():
    with pytest.raises(ApiError):
        poll(Script(ApiError(ApiFailure("Spansh", "unexpected", 500)), FakeResponse(200, {})))


def test_finished_job_with_error_is_a_rejection():
    with pytest.raises(ApiError) as info:
        poll(Script(FakeResponse(200, {"status": "failed", "error": "No route found\nbetween systems"})))
    assert str(info.value) == "Spansh rejected the request: No route found between systems"


def test_wall_clock_timeout_mentions_the_website():
    class Always202:
        calls = 0

        def __call__(self, *a, **k):
            Always202.calls += 1
            return FakeResponse(202, {})

    with pytest.raises(ApiError) as info:
        poll(Always202(), max_wait=180)
    assert str(info.value) == ("Spansh did not answer within 180 s; the job may still finish on "
                               "Spansh's website.")
    assert 55 < Always202.calls < 70


def test_poll_interval_stays_between_1_and_3_seconds():
    fake = Script(*[FakeResponse(202, {}) for _ in range(8)], FakeResponse(200, {}))
    _, clock = poll(fake)
    assert clock.sleeps == [1.0, 1.5, 2.0, 2.5, 3.0, 3.0, 3.0, 3.0]
