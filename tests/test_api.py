"""edmrn.ext.api: one HTTP path, retries, readable failures. No network."""
import pytest
import requests

import edmrn
from edmrn.ext.api import (
    USER_AGENT, ApiError, ApiFailure, call, failure_from_response, json_body,
)
from fakes import FakeResponse, Script, Sleeps

URL = "https://spansh.co.uk/api/route"


def run(transport, **kwargs):
    sleeps = Sleeps()
    kwargs.setdefault("timeout", 30)
    try:
        return call("POST", URL, service="Spansh", transport=transport, sleep=sleeps, **kwargs), sleeps
    except ApiError as e:
        return e, sleeps


# --- call: retry matrix ------------------------------------------------------

def test_user_agent_params_and_timeout_on_every_attempt():
    transport = Script(FakeResponse(502), FakeResponse(202, {"job": "j"}))
    params = {"from": "Jackson's & #Lighthouse+", "to": "Sol"}
    response, _ = run(transport, params=params)
    assert response.status_code == 202
    assert USER_AGENT == f"EDMRN/{edmrn.__version__}"
    for args, kwargs in transport.calls:
        assert args == ("POST", URL)
        assert kwargs["headers"] == {"User-Agent": USER_AGENT}
        assert kwargs["params"] == params and kwargs["json"] is None
        assert kwargs["timeout"] == 30


def test_json_body_is_passed_through():
    transport = Script(FakeResponse(200, {"results": []}))
    run(transport, json={"filters": {"name": {"value": "Sol"}}})
    assert transport.calls[0][1]["json"] == {"filters": {"name": {"value": "Sol"}}}


def test_502_then_202_retries_once_after_2s():
    transport, seen = Script(FakeResponse(502), FakeResponse(202)), []
    response, sleeps = run(transport, on_retry=lambda n, f: seen.append((n, f.kind)))
    assert response.status_code == 202
    assert sleeps == [2.0] and seen == [(1, "overloaded")]


def test_502_three_times_is_overloaded_after_2s_and_5s():
    transport = Script(FakeResponse(502), FakeResponse(503), FakeResponse(502, text="<html>Bad Gateway</html>"))
    error, sleeps = run(transport)
    assert isinstance(error, ApiError) and len(transport.calls) == 3
    assert sleeps == [2.0, 5.0]
    assert error.failure == ApiFailure("Spansh", "overloaded", 502, "")
    assert str(error.failure) == "Spansh is overloaded (HTTP 502). Try again in a minute."


def test_timeout_three_times():
    transport = Script(*[requests.exceptions.Timeout("read timed out") for _ in range(3)])
    error, _ = run(transport)
    assert error.failure.kind == "timeout"
    assert str(error.failure) == "Spansh did not answer within 30 s."


def test_connection_error_then_200():
    transport = Script(requests.exceptions.ConnectionError("refused"), FakeResponse(200, {}))
    response, sleeps = run(transport)
    assert response.status_code == 200 and sleeps == [2.0]


def test_chunked_encoding_error_then_200_is_retried_like_a_connection_error():
    transport = Script(requests.exceptions.ChunkedEncodingError("connection broken"), FakeResponse(200, {}))
    response, sleeps = run(transport)
    assert response.status_code == 200 and sleeps == [2.0]


def test_content_decoding_error_stays_unexpected():
    error, sleeps = run(Script(requests.exceptions.ContentDecodingError("bad gzip")))
    assert error.failure.kind == "unexpected" and sleeps == []


def test_connection_error_three_times_is_offline():
    transport = Script(*[requests.exceptions.ConnectionError() for _ in range(3)])
    error, _ = run(transport)
    assert str(error.failure) == "No connection to Spansh."


def test_400_is_returned_without_retry():
    transport = Script(FakeResponse(400, {"error": "Could not find starting system"}))
    response, sleeps = run(transport)
    assert response.status_code == 400 and len(transport.calls) == 1 and sleeps == []


def test_500_is_unexpected_without_retry():
    transport = Script(FakeResponse(500, text="<html>oops</html>"))
    error, sleeps = run(transport)
    assert len(transport.calls) == 1 and sleeps == []
    assert str(error.failure) == "Unexpected error talking to Spansh (HTTP 500)."


def test_retries_zero_means_one_attempt():
    transport = Script(requests.exceptions.Timeout())
    error, sleeps = run(transport, retries=0, timeout=5)
    assert len(transport.calls) == 1 and sleeps == []
    assert str(error.failure) == "Spansh did not answer within 5 s."


def test_transport_bug_is_unexpected_not_raw():
    transport = Script(TypeError("internal detail"))
    error, _ = run(transport)
    assert str(error.failure) == "Unexpected error talking to Spansh."
    assert "internal detail" not in str(error)


def test_failing_on_retry_callback_does_not_abort():
    def boom(n, f):
        raise RuntimeError("widget gone")
    response, _ = run(Script(FakeResponse(503), FakeResponse(202)), on_retry=boom)
    assert response.status_code == 202


# --- failure sentences -------------------------------------------------------

def test_spansh_json_reason():
    failure = failure_from_response(FakeResponse(400, {"error": "Could not find starting system"}), "Spansh")
    assert failure == ApiFailure("Spansh", "rejected", 400, "Could not find starting system")
    assert str(failure) == "Spansh rejected the request: Could not find starting system"


def test_edsm_msg_reason():
    failure = failure_from_response(FakeResponse(429, {"msgnum": 429, "msg": "Rate limit exceeded"}), "EDSM")
    assert str(failure) == "EDSM rejected the request: Rate limit exceeded"


def test_html_502_never_shows_html():
    failure = failure_from_response(FakeResponse(502, text="<html><h1>502 Bad Gateway</h1></html>"), "Spansh")
    assert str(failure) == "Spansh is overloaded (HTTP 502). Try again in a minute."


def test_rejection_without_reason_names_status():
    assert str(failure_from_response(FakeResponse(404, text="<html>"), "GitHub")) == \
        "GitHub rejected the request (HTTP 404)."


def test_long_multiline_reason_becomes_one_short_line():
    reason = "first line\n\n" + "x" * 600
    text = str(failure_from_response(FakeResponse(400, {"error": reason}), "Spansh"))
    assert "\n" not in text and len(text) < 260 and text.endswith("...")


@pytest.mark.parametrize("failure,text", [
    (ApiFailure("EDSM", "offline"), "No connection to EDSM."),
    (ApiFailure("EDAstro", "timeout", None, "15 s"), "EDAstro did not answer within 15 s."),
    (ApiFailure("GitHub", "overloaded"), "GitHub is overloaded. Try again in a minute."),
    (ApiFailure("EDSM", "unexpected"), "Unexpected error talking to EDSM."),
])
def test_sentences(failure, text):
    assert str(failure) == text and str(ApiError(failure)) == text


def test_json_body_rejects_html_200():
    with pytest.raises(ApiError) as info:
        json_body(FakeResponse(200, text="<html>maintenance</html>"), "Spansh")
    assert str(info.value) == "Unexpected error talking to Spansh (HTTP 200)."
