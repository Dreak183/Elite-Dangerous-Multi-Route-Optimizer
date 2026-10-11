# API Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every live external request (Spansh, EDSM, EDAstro, GitHub) survives transient failures,
reports the real reason in one plain-English sentence, tells "not found" apart from "service down",
and Neutron and Galaxy plot from the nearest known system when Spansh does not know the start.

**Architecture:**
- `edmrn/ext/api.py` is the one HTTP path:
  - `call` (User-Agent, encoded params, timeout, retries);
  - `ApiFailure`/`ApiError` (the A2 sentences);
  - `lookup_system` and `nearest_system`;
  - `ServiceCooldown`.
- `edmrn/ext/spansh.py` is rebuilt on it. It holds the route-job submission with the start
  fallback and the wall-clock polling, shared by Neutron and Galaxy.
- `edmrn/ext/system_info.py` holds the System Info fetch.
- Upstream files get small, quoted hook edits that call into `edmrn/ext/` (ADR-0002).
- Worker results reach Tk only through `root.after`.

**Tech Stack:** Python 3.13, requests, customtkinter (untouched by tests), pytest.

**Spec:** `docs/superpowers/specs/2026-10-11-api-hardening-design.md` (Accepted), requirements
A1–A14. Read it with this plan; the spec wins where they disagree. Call-site survey:
`docs/knowledge/upstream-baseline.md`, section "External APIs".

## Global Constraints

- **Upstream files:**
  - The upstream files are `edmrn/neutron.py`, `edmrn/neutron_manager.py`,
    `edmrn/galaxy_plotter.py`, `edmrn/app.py`, `edmrn/custom_route.py`,
    `edmrn/system_autocomplete.py`, `edmrn/system_info_section.py` and `edmrn/updater.py`.
  - Change only the lines a task quotes. No reformatting, no renaming, no "tidying" (ADR-0002).
  - Line numbers in this plan are those of commit `1018269`. Earlier edits in the same file shift
    them, so find each block by its quoted text. Every quoted "before" block occurs exactly once in
    its file.
  - Keep CRLF line endings. Check with `file <path>` ("with CRLF line terminators").
  - Several upstream blank lines carry trailing spaces. Never include a whitespace-only line in an
    edit that this plan does not quote. A line outside the quoted blocks must not show up in
    `git diff`.
  - Every changed upstream file gets or extends a row in the hook-point table of
    `docs/architecture/overview.md`, with Upstream PR `yes`, in the same commit.
- **Dead code is not touched:** `edmrn/galaxy_handler.py`, `edmrn/journal_handler.py` and
  `EDMRN_App._create_neutron_tab`.
- **Messages** are English and use the A2 sentences exactly as `ApiFailure.__str__` produces them.
  Raw HTML and raw exception text never reach the UI; they go to the log at debug (raw bodies) or
  error level (tracebacks via `logger.exception`).
- **Our modules:**
  - They live under `edmrn/ext/` with LF line endings.
  - New dependencies: none (`requests` is already upstream's).
  - AGPL-3.0 stays.
- **Tests:**
  - pytest, no network: inject `transport`/`call`, `sleep` and `clock`, or monkeypatch the name a
    module imported.
  - Tests never touch real user folders. `tests/conftest.py` already redirects every home variable
    before `edmrn` is imported (`docs/architecture/patterns.md`).
  - Where Tk would be needed, the test drives the unbound upstream method with a fake app (see
    `tests/test_neutron_manager.py`), and threads run synchronously via `tests/fakes.py`.
  - Test modules that import `edmrn.app` restore `sys.excepthook` afterwards, because `edmrn.app`
    replaces it at import.
  - Run with `.venv/Scripts/python.exe -m pytest` from the repo root. The suite is green after
    every task.
- **Git:**
  - Stage explicit paths only (never `git add -A`, `.` or `-u`).
  - Every commit message ends with these two lines:
    ```
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa
    ```
- **Documentation** goes in the same commit as the change it documents (`docs/README.md`).

## Spec rulings

The spec is the authority; these rulings record where the code's shape forced a reading, and why.

1. **Polling tolerance (A6).**
   - "Up to 3 consecutive transient failures are tolerated" is taken literally: the fourth
     consecutive transient failure ends the poll.
   - "A 200 resets the count" is read as "any successful answer resets the count", because a 200
     ends the poll; in practice that answer is a 202.
   - The spec's test line "transient×3" is pinned as "transient×3 then 200 succeeds" plus
     "transient×4 fails".
   - This loosens `23903c2`, which gave up at the third failure.
2. **`submit_route_job` takes a `timeout` keyword** that the spec signature lacks. Neutron keeps its
   30 s, Galaxy its 60 s. Spansh submissions took 15–50 s under load
   (`../knowledge/bulk-data.md`), so one fixed value would hurt one of them.
3. **Galaxy return shapes.**
   - `GalaxyPlotter.submit_route_job` returns `(job_id, note)` and raises `ApiError`.
   - `GalaxyPlotter.poll_route_results` raises `ApiError`.
   - `GalaxyPlotter.plot_route` returns `{"error": <A2 sentence>}` instead of `None`; that dict is
     the "failure reason" of the spec's table. `_display_galaxy_route_result` shows it.
   - On success the Spansh body gains the key `note` (the substitution sentence, or `None`).
4. **Custom Route consults the journal first (A9 with A5's coordinate rule).** When a name equals
   the commander's current journal system, its journal coordinates are used without a lookup.
   - Reason: the tab pre-fills the start from the journal.
   - Without this, a freshly visited system unknown to both Spansh and EDSM would stop every
     optimisation (A9's stop rule) although the app knows where the commander is.
5. **`ApiFailure.detail` for `timeout`** holds the time waited (`"30 s"`), since the spec's four
   fields have no other place for it. The poll deadline uses
   `detail = "<max_wait> s; the job may still finish on Spansh's website"`.
6. **A finished job (200) whose body carries an `error` string** raises `ApiError(rejected)` with
   that reason. Otherwise it would show as an empty "route calculated" (Review Focus 5).
7. **System Info notes (A10)** appear in the status line, one per line under
   `Data fetched for <name>`. A failure shows the A2 sentence alone, without the old `Error: `
   prefix.
8. **Updater headers.** `call` sends only the User-Agent (the spec signature has no headers
   parameter), so the GitHub request drops `Accept: application/vnd.github.v3+json`. GitHub's
   default media type is the same v3 JSON.

## Review Focus

1. **System names with `'`, `&`, `#`, `+`** (`Jackson's Lighthouse`, `Sagittarius A*`).
   - They must travel as encoded parameters in every EDSM request and be quoted in the EDSM web
     link.
   - A non-integer `id64` from EDSM must never reach the EDAstro URL, and a Spansh job id is
     path-quoted.
   - Pinned in Task 6 (`test_full_fetch_sends_encoded_params_and_builds_data`,
     `test_non_integer_id64_never_reaches_a_url`) and Task 3 (`test_job_id_is_quoted_into_the_path`).
2. **A service reason that is long or multi-line, or a 200 answer that is an HTML maintenance
   page.**
   - Expected: one line of at most 200 characters, no HTML, and an "unexpected" failure rather than
     a crash or a silent "not found".
   - Pinned in Task 1 (`test_long_multiline_reason_becomes_one_short_line`,
     `test_json_body_rejects_html_200`) and Task 2 (`test_lookup_spansh_html_200_counts_as_failure`).
3. **The Custom Route start pre-filled from the journal is a system neither service knows.**
   - Expected: the journal's coordinates are used, nothing is looked up, and the optimisation runs.
   - Pinned in Task 5
     (`test_journal_start_unknown_to_both_services_uses_journal_coords`, Spec ruling 4).
4. **Displaying a Galaxy result fails** (malformed Spansh data makes an upstream list update
   raise).
   - Expected: the Calculate button is enabled again anyway.
   - Pinned in Task 4 (`test_button_comes_back_even_if_display_fails`).
5. **A route job finishes (200) with an `error` in its body.**
   - Expected: the reason is shown as a rejection, not an empty "Route calculated: 0 jumps".
   - Pinned in Task 3 (`test_finished_job_with_error_is_a_rejection`, Spec ruling 6).

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `edmrn/ext/api.py` | `call`, `ApiFailure`/`ApiError`, `failure_from_response`, `json_body`, `lookup_system`, `nearest_system`, `nearest_known_system`, `ServiceCooldown` | 1, 2, 8 |
| `edmrn/ext/spansh.py` (rebuilt) | `RouteSubmission`, `submit_route_job`, `poll_route_result`, `journal_start_coords` | 3, 4 |
| `edmrn/ext/system_info.py` | `fetch_system_data` for System Info | 6 |
| `tests/fakes.py` | Shared fakes: responses, scripted calls, clock, Tk root, sync threads, widgets | 1 |
| `tests/test_api.py`, `tests/test_lookup.py` | `api.py` | 1, 2 |
| `tests/test_spansh.py` (rewritten), `tests/test_neutron.py` (new) | `ext/spansh.py`; `NeutronRouter` | 3 |
| `tests/test_neutron_manager.py` (one test added) | `calculation_done` on the Tk loop | 3 |
| `tests/test_galaxy.py`, `tests/test_custom_route.py`, `tests/test_system_info.py`, `tests/test_nearest.py`, `tests/test_autocomplete.py`, `tests/test_updater.py` | Call sites | 4–8 |
| `tests/test_http_path.py` | Guard: no module but `api.py` sends HTTP | 9 |
| `edmrn/neutron.py`, `edmrn/neutron_manager.py` (upstream) | Neutron hooks | 3 |
| `edmrn/galaxy_plotter.py`, `edmrn/app.py` (upstream) | Galaxy hooks | 4 |
| `edmrn/custom_route.py` (upstream) | Custom Route hooks | 5, 8 |
| `edmrn/app.py` (upstream) | System Info, Nearest, suggestion hooks | 6, 7, 8 |
| `edmrn/system_autocomplete.py`, `edmrn/system_info_section.py`, `edmrn/updater.py` (upstream) | Autocomplete and updater hooks | 8 |
| `docs/architecture/overview.md` | Hook-point rows; the HTTP invariant | 3–9 |
| `docs/architecture/patterns.md`, `docs/how-to/run.md` | HTTP rule; manual live check | 9 |
| `docs/superpowers/plans/2026-10-11-neutron-route-resume.md` | Its Task 2 test adapted to the rebuilt `spansh.py` | 3 |

---

### Task 1: `edmrn/ext/api.py` core — `call` and readable failures

**Files:**
- Create: `edmrn/ext/api.py`, `tests/fakes.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `edmrn.__version__`, `edmrn.logger.get_logger`.
- Produces (`edmrn/ext/api.py`):
  - `USER_AGENT: str`, equal to `f"EDMRN/{edmrn.__version__}"`.
  - `TRANSIENT_STATUSES = frozenset({502, 503, 504})`.
  - `TRANSIENT_KINDS = frozenset({"overloaded", "timeout", "offline"})`.
  - `ApiFailure(service: str, kind: str, status: int | None = None, detail: str = "")`, a frozen
    dataclass.
    - `kind` is one of `rejected`, `overloaded`, `timeout`, `offline` or `unexpected`.
    - `str(failure)` is the A2 sentence.
  - `ApiError(Exception)` with `.failure: ApiFailure`; `str(error) == str(error.failure)`.
  - `one_line(text: str) -> str`: whitespace collapsed, at most 200 characters.
  - `failure_from_response(response, service: str) -> ApiFailure`.
  - `json_body(response, service: str) -> Any`, which raises `ApiError(unexpected)` for a non-JSON
    body.
  - `call(method, url, *, service, params=None, json=None, timeout, retries=2, delays=(2.0, 5.0),
    on_retry=None, transport=requests.request, sleep=time.sleep) -> response`.
    - It returns every response with a status below 500.
    - It raises `ApiError` otherwise.
    - It calls `on_retry(attempt: int, failure: ApiFailure)` before each retry.
- Produces (`tests/fakes.py`):
  - `FakeResponse(status_code, body=None, text=None, reason="")`.
  - `Script(*items)`, a callable that records `.calls` as `(args, kwargs)` and raises or returns
    each item in turn.
  - `Sleeps`, `Clock` (`clock()`, `clock.sleep(s)`, `.now`, `.sleeps`), `Root` (`after`, `run`).
  - `SyncThread`, `sync_threading()`, `Var(value, log=None, name=None)`, `Widget(**state)` with
    `.state` and `.history`, and `Text` with `.content`.

- [ ] **Step 1: Write the shared fakes** (`tests/fakes.py`)

```python
"""Fakes shared by the API-hardening tests: responses, scripted transports, clocks, a Tk root.

Imported by test modules only, so tests/conftest.py has redirected the home folder before any
edmrn module loads (docs/architecture/patterns.md).
"""
import threading
from types import SimpleNamespace


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
    """Callable fake for `transport` or `call`: records each call and replays queued items.

    An item that is an exception is raised; anything else is returned.
    """

    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if not self.items:
            raise AssertionError(f"unexpected call {args} {kwargs}")
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class Sleeps(list):
    def __call__(self, seconds):
        self.append(seconds)


class Clock:
    """Fake monotonic clock; its sleep() advances the time."""

    def __init__(self, now=1000.0):
        self.now = now
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Root:
    """Fake Tk root: after() queues callbacks, run() drains them like the Tk loop does."""

    def __init__(self):
        self.queue = []

    def after(self, ms, fn=None):
        self.queue.append(fn)

    def run(self):
        while self.queue:
            self.queue.pop(0)()

    def update_idletasks(self):
        pass


class SyncThread:
    """Stand-in for threading.Thread that runs its target inside start()."""

    def __init__(self, target=None, daemon=None, args=(), kwargs=None):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


def sync_threading():
    """A replacement for a module's `threading` name whose threads run synchronously."""
    return SimpleNamespace(Thread=SyncThread, Lock=threading.Lock,
                           current_thread=threading.current_thread, main_thread=threading.main_thread)


class Var:
    """StringVar/BooleanVar/AutocompleteEntry stand-in; records reads into an optional log."""

    def __init__(self, value="", log=None, name=None):
        self.value, self.log, self.name = value, log, name

    def get(self, *args):
        if self.log is not None:
            self.log.append(("get", self.name))
        return self.value

    def set(self, value):
        self.value = value


class Widget:
    """Button/label stand-in: configure() keeps the latest state and the history."""

    def __init__(self, **state):
        self.state = dict(state)
        self.history = []

    def configure(self, **kwargs):
        self.state.update(kwargs)
        self.history.append(kwargs)


class Text:
    """CTkTextbox stand-in for the Galaxy output box."""

    def __init__(self):
        self.content = ""

    def configure(self, **kwargs):
        pass

    def delete(self, start, end):
        self.content = ""

    def insert(self, index, text):
        self.content = text + self.content if index == "1.0" else self.content + text

    def see(self, index):
        pass
```

- [ ] **Step 2: Write the failing tests** (`tests/test_api.py`)

```python
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
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'edmrn.ext.api'`.

- [ ] **Step 4: Implement `edmrn/ext/api.py`**

```python
"""The one HTTP path for every live external request (API hardening spec A1-A4).

Spansh answers bad input with HTTP 400 and {"error": "<reason>"}, and an overloaded server with an
nginx 502/503/504 HTML page or a timeout (../../../knowledge/bulk-data.md, "Spansh API"). EDSM's
system API answers an unknown system with [] (../../../knowledge/edsm-api.md).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

import edmrn
from edmrn.logger import get_logger

logger = get_logger('Api')

USER_AGENT = f"EDMRN/{edmrn.__version__}"
TRANSIENT_STATUSES = frozenset({502, 503, 504})
TRANSIENT_KINDS = frozenset({"overloaded", "timeout", "offline"})
DETAIL_LIMIT = 200


@dataclass(frozen=True)
class ApiFailure:
    service: str
    kind: str
    status: Optional[int] = None
    detail: str = ""

    def __str__(self) -> str:
        s = self.service
        if self.kind == "rejected":
            if self.detail:
                return f"{s} rejected the request: {self.detail}"
            return f"{s} rejected the request (HTTP {self.status})."
        if self.kind == "overloaded":
            if self.status:
                return f"{s} is overloaded (HTTP {self.status}). Try again in a minute."
            return f"{s} is overloaded. Try again in a minute."
        if self.kind == "timeout":
            return f"{s} did not answer within {self.detail}." if self.detail else f"{s} did not answer in time."
        if self.kind == "offline":
            return f"No connection to {s}."
        if self.status:
            return f"Unexpected error talking to {s} (HTTP {self.status})."
        return f"Unexpected error talking to {s}."


class ApiError(Exception):
    """A request failed for good; `failure` says why."""

    def __init__(self, failure: ApiFailure) -> None:
        super().__init__(str(failure))
        self.failure = failure


def one_line(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= DETAIL_LIMIT else text[:DETAIL_LIMIT - 3].rstrip() + "..."


def _body_snippet(response) -> str:
    try:
        return str(response.text)[:500]
    except Exception:
        return "<no text>"


def failure_from_response(response, service: str) -> ApiFailure:
    """The failure a non-success response stands for. Reads Spansh's `error` or EDSM's `msg`."""
    status = getattr(response, "status_code", None)
    detail = ""
    try:
        body = response.json()
    except Exception:
        body = None
    if isinstance(body, dict):
        for key in ("error", "msg"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                detail = one_line(value)
                break
    if status in TRANSIENT_STATUSES:
        kind = "overloaded"
    elif isinstance(status, int) and 400 <= status < 500:
        kind = "rejected"
    else:
        kind = "unexpected"
    logger.debug(f"{service} answered HTTP {status}: {_body_snippet(response)}")
    return ApiFailure(service, kind, status, detail)


def json_body(response, service: str) -> Any:
    """Parsed JSON of a response, or ApiError(unexpected) when the body is not JSON."""
    try:
        return response.json()
    except Exception:
        logger.debug(f"{service} sent no JSON: {_body_snippet(response)}")
        raise ApiError(ApiFailure(service, "unexpected", getattr(response, "status_code", None)))


def call(method, url, *, service, params=None, json=None, timeout, retries=2, delays=(2.0, 5.0),
         on_retry=None, transport=requests.request, sleep=time.sleep):
    """Send one request; retry 502/503/504, timeouts and connection errors.

    Responses below 500 are returned; the caller decides what a 4xx means. Everything else ends in
    ApiError. on_retry(attempt, failure) is called before each retry.
    """
    headers = {"User-Agent": USER_AGENT}
    attempt = 0
    while True:
        try:
            response = transport(method, url, params=params, json=json, headers=headers, timeout=timeout)
        except requests.exceptions.Timeout as exc:
            logger.debug(f"{service} {method} {url}: {exc!r}")
            failure = ApiFailure(service, "timeout", None, f"{timeout:g} s")
        except requests.exceptions.ConnectionError as exc:
            logger.debug(f"{service} {method} {url}: {exc!r}")
            failure = ApiFailure(service, "offline")
        except Exception:
            logger.exception(f"{service} {method} {url} failed unexpectedly")
            raise ApiError(ApiFailure(service, "unexpected"))
        else:
            if response.status_code in TRANSIENT_STATUSES:
                failure = failure_from_response(response, service)
            elif response.status_code >= 500:
                raise ApiError(failure_from_response(response, service))
            else:
                return response
        if attempt >= retries:
            raise ApiError(failure)
        attempt += 1
        if on_retry:
            try:
                on_retry(attempt, failure)
            except Exception:
                logger.exception("on_retry callback failed")
        sleep(delays[min(attempt - 1, len(delays) - 1)])
```

- [ ] **Step 5: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `59 passed` (37 existing + 22 new).

- [ ] **Step 6: Commit**

```bash
git add edmrn/ext/api.py tests/fakes.py tests/test_api.py
git commit -m "feat(api): one HTTP path with retries and readable failures" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

No doc changes: the rule that all HTTP goes through `api.py` becomes true only in Task 9, which
documents it.

---

### Task 2: `lookup_system` and the nearest known system

**Files:**
- Modify: `edmrn/ext/api.py` (append)
- Create: `tests/test_lookup.py`

**Interfaces:**
- Consumes (Task 1): `call`, `ApiError`, `ApiFailure`, `failure_from_response`, `json_body`.
- Produces (`edmrn/ext/api.py`):
  - `Lookup(name: str, coords: tuple[float, float, float] | None = None, source: str | None = None,
    failures: tuple[ApiFailure, ...] = ())`, a frozen dataclass.
    - Constructors: `Lookup.not_found(name)` and `Lookup.failed(name, failures)`.
    - `.found: bool`.
    - `.message: str | None`, the A9 sentence; `None` when found.
  - `lookup_system(name: str, *, call=call) -> Lookup`.
    - Spansh `POST /api/systems/search` (exact, case-insensitive name match on `results[]` with
      top-level `x,y,z`), then EDSM `GET api-v1/system` (`[]` = unknown).
    - Source is `"Spansh"` or `"EDSM"`.
  - `nearest_system(x, y, z, *, call=call) -> dict | None`.
    - Returns `{"name", "x", "y", "z", "distance"}`, with the distance read inside `system`.
    - A service failure raises `ApiError`; a body without a usable system gives `None`.
  - `nearest_known_system(x, y, z, *, call=call) -> tuple[str, float] | None`, with the same
    failure rules.
  - Constants `SPANSH_SEARCH_URL`, `SPANSH_NEAREST_URL`, `EDSM_SYSTEM_URL`.

- [ ] **Step 1: Write the failing tests** (`tests/test_lookup.py`)

```python
"""edmrn.ext.api lookup_system and nearest: Spansh first, EDSM fallback, A3 distinction. No network."""
import pytest

from edmrn.ext.api import ApiError, ApiFailure, Lookup, lookup_system, nearest_known_system, nearest_system
from fakes import FakeResponse, Script

BLAE = "Blae Phoe XT-A e1459"
BLAE_COORDS = (768.375, -565.90625, 14325.34375)


def spansh_hits(*records):
    return FakeResponse(200, {"count": len(records), "results": list(records)})


def test_lookup_spansh_hit_sends_json_search():
    fake = Script(spansh_hits({"name": "Sol", "x": 0, "y": 0, "z": 0}))
    assert lookup_system("Sol", call=fake) == Lookup("Sol", (0.0, 0.0, 0.0), "Spansh")
    args, kwargs = fake.calls[0]
    assert args == ("POST", "https://spansh.co.uk/api/systems/search")
    assert kwargs["service"] == "Spansh" and kwargs["json"]["filters"] == {"name": {"value": "Sol"}}
    assert kwargs.get("params") is None


def test_lookup_name_match_is_exact_and_case_insensitive():
    fake = Script(spansh_hits({"name": "Solati", "x": 9, "y": 9, "z": 9},
                              {"name": "SOL", "x": 0, "y": 0, "z": 0}))
    assert lookup_system(" sol ", call=fake).coords == (0.0, 0.0, 0.0)


def test_lookup_spansh_miss_then_edsm_hit():
    fake = Script(spansh_hits({"name": "Blae Phoe XT-A e1458", "x": 1, "y": 1, "z": 1}),
                  FakeResponse(200, {"name": BLAE, "coords": dict(zip("xyz", BLAE_COORDS))}))
    result = lookup_system(BLAE, call=fake)
    assert result == Lookup(BLAE, BLAE_COORDS, "EDSM") and result.message is None
    args, kwargs = fake.calls[1]
    assert args == ("GET", "https://www.edsm.net/api-v1/system")
    assert kwargs["params"] == {"systemName": BLAE, "showCoordinates": 1}


def test_lookup_both_miss_is_not_found():
    result = lookup_system("Sool", call=Script(spansh_hits(), FakeResponse(200, [])))
    assert result == Lookup.not_found("Sool") and not result.found
    assert result.message == "System 'Sool' was not found at Spansh or EDSM. Check the spelling."


def test_lookup_spansh_502_and_edsm_miss_is_failed_not_not_found():
    fake = Script(ApiError(ApiFailure("Spansh", "overloaded", 502)), FakeResponse(200, []))
    result = lookup_system("Sool", call=fake)
    assert result.failures == (ApiFailure("Spansh", "overloaded", 502),) and not result.found
    assert result.message == ("Could not look up 'Sool': Spansh is overloaded (HTTP 502). Try again in "
                              "a minute; System 'Sool' is not known to EDSM.")


def test_lookup_both_fail_carries_both_reasons():
    fake = Script(ApiError(ApiFailure("Spansh", "timeout", None, "10 s")),
                  ApiError(ApiFailure("EDSM", "offline")))
    assert lookup_system("Sol", call=fake).message == \
        "Could not look up 'Sol': Spansh did not answer within 10 s; No connection to EDSM."


def test_lookup_spansh_html_200_counts_as_failure():
    fake = Script(FakeResponse(200, text="<html>maintenance</html>"), FakeResponse(200, []))
    result = lookup_system("Sol", call=fake)
    assert [f.service for f in result.failures] == ["Spansh"] and "<html>" not in result.message


def test_lookup_ignores_records_without_numeric_coords():
    fake = Script(spansh_hits({"name": "Sol", "x": None, "y": 0, "z": 0}),
                  FakeResponse(200, {"name": "Sol", "coords": {"x": "0", "y": 0, "z": 0}}))
    assert lookup_system("Sol", call=fake) == Lookup.not_found("Sol")


def test_lookup_empty_name_does_not_call():
    assert lookup_system("  ", call=Script()) == Lookup.not_found("")


NEAREST_BODY = {"system": {"name": "Sol", "id64": 10477373803, "x": 0, "y": 0, "z": 0, "distance": 0.0}}


def test_nearest_system_reads_distance_inside_system():
    fake = Script(FakeResponse(200, {"system": {**NEAREST_BODY["system"], "distance": 4.38}}))
    assert nearest_system(1, 2, 3, call=fake) == {"name": "Sol", "x": 0.0, "y": 0.0, "z": 0.0, "distance": 4.38}
    args, kwargs = fake.calls[0]
    assert args == ("GET", "https://spansh.co.uk/api/nearest")
    assert kwargs["params"] == {"x": 1, "y": 2, "z": 3}


def test_nearest_known_system_pair():
    assert nearest_known_system(0, 0, 0, call=Script(FakeResponse(200, NEAREST_BODY))) == ("Sol", 0.0)


@pytest.mark.parametrize("body", [
    {}, {"system": None},
    {"system": {"name": "", "x": 0, "y": 0, "z": 0, "distance": 1}},
    {"system": {"name": "X", "x": 0, "y": 0, "z": 0}},
])
def test_nearest_without_usable_system_is_none(body):
    assert nearest_known_system(0, 0, 0, call=Script(FakeResponse(200, body))) is None


def test_nearest_rejection_raises_readable_error():
    with pytest.raises(ApiError) as info:
        nearest_system(0, 0, 0, call=Script(FakeResponse(400, {"error": "x must be a number"})))
    assert str(info.value) == "Spansh rejected the request: x must be a number"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_lookup.py -v`
Expected: collection error, `ImportError: cannot import name 'Lookup' from 'edmrn.ext.api'`.

- [ ] **Step 3: Append to `edmrn/ext/api.py`** (after `call`, separated by two blank lines)

```python
# --- Coordinates and nearest system (spec A3, A4) ---------------------------

SPANSH_SEARCH_URL = "https://spansh.co.uk/api/systems/search"
SPANSH_NEAREST_URL = "https://spansh.co.uk/api/nearest"
EDSM_SYSTEM_URL = "https://www.edsm.net/api-v1/system"
LOOKUP_SERVICES = ("Spansh", "EDSM")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _coords(record) -> Optional[tuple]:
    if not isinstance(record, dict):
        return None
    values = (record.get("x"), record.get("y"), record.get("z"))
    if not all(_is_number(v) for v in values):
        return None
    return tuple(float(v) for v in values)


@dataclass(frozen=True)
class Lookup:
    """Result of lookup_system: coordinates with their source, not found, or failed."""
    name: str
    coords: Optional[tuple] = None
    source: Optional[str] = None
    failures: tuple = ()

    @classmethod
    def not_found(cls, name: str) -> "Lookup":
        return cls(name)

    @classmethod
    def failed(cls, name: str, failures) -> "Lookup":
        return cls(name, failures=tuple(failures))

    @property
    def found(self) -> bool:
        return self.coords is not None

    @property
    def message(self) -> Optional[str]:
        """The A9 sentence for a lookup that gave no coordinates; None when found."""
        if self.found:
            return None
        if not self.failures:
            return f"System '{self.name}' was not found at Spansh or EDSM. Check the spelling."
        by_service = {f.service: f for f in self.failures}
        reasons = [str(by_service[s]).rstrip(".") if s in by_service
                   else f"System '{self.name}' is not known to {s}" for s in LOOKUP_SERVICES]
        return f"Could not look up '{self.name}': {'; '.join(reasons)}."


def _spansh_coords(name: str, call) -> Optional[tuple]:
    response = call("POST", SPANSH_SEARCH_URL, service="Spansh", timeout=10,
                    json={"filters": {"name": {"value": name}}, "sort": [{"name": {"order": "asc"}}],
                          "size": 50})
    if response.status_code != 200:
        raise ApiError(failure_from_response(response, "Spansh"))
    body = json_body(response, "Spansh")
    results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(results, list):
        raise ApiError(ApiFailure("Spansh", "unexpected", 200))
    key = name.casefold()
    for record in results:
        if isinstance(record, dict) and isinstance(record.get("name"), str) \
                and record["name"].strip().casefold() == key:
            coords = _coords(record)
            if coords:
                return coords
    return None


def _edsm_coords(name: str, call) -> Optional[tuple]:
    response = call("GET", EDSM_SYSTEM_URL, service="EDSM", timeout=10,
                    params={"systemName": name, "showCoordinates": 1})
    if response.status_code != 200:
        raise ApiError(failure_from_response(response, "EDSM"))
    body = json_body(response, "EDSM")
    if isinstance(body, list):
        return None
    if not isinstance(body, dict):
        raise ApiError(ApiFailure("EDSM", "unexpected", 200))
    return _coords(body.get("coords"))


def lookup_system(name: str, *, call=call) -> Lookup:
    """Coordinates of a system: Spansh system search first, then EDSM (spec A4).

    Not found only when both services answered and neither knows the name; when one failed and
    the other does not know it, the result is failed (A3).
    """
    name = (name or "").strip()
    if not name:
        return Lookup.not_found(name)
    failures = []
    for service, fetch in (("Spansh", _spansh_coords), ("EDSM", _edsm_coords)):
        try:
            coords = fetch(name, call)
        except ApiError as e:
            failures.append(e.failure)
            continue
        if coords:
            return Lookup(name, coords, service)
    return Lookup.failed(name, failures) if failures else Lookup.not_found(name)


def nearest_system(x, y, z, *, call=call) -> Optional[dict]:
    """Spansh's nearest known system to the coordinates as {name, x, y, z, distance}, else None.

    Spansh answers exactly one system and nests `distance` inside `system`
    (../../../knowledge/bulk-data.md). Service failures raise ApiError.
    """
    response = call("GET", SPANSH_NEAREST_URL, service="Spansh", timeout=10,
                    params={"x": x, "y": y, "z": z})
    if response.status_code != 200:
        raise ApiError(failure_from_response(response, "Spansh"))
    body = json_body(response, "Spansh")
    system = body.get("system") if isinstance(body, dict) else None
    coords = _coords(system)
    if coords is None or not isinstance(system.get("name"), str) or not system["name"].strip() \
            or not _is_number(system.get("distance")):
        return None
    return {"name": system["name"], "x": coords[0], "y": coords[1], "z": coords[2],
            "distance": float(system["distance"])}


def nearest_known_system(x, y, z, *, call=call) -> Optional[tuple]:
    """(name, distance in ly) of Spansh's nearest known system, else None. Failures raise ApiError."""
    system = nearest_system(x, y, z, call=call)
    return (system["name"], system["distance"]) if system else None
```

- [ ] **Step 4: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `75 passed`.

- [ ] **Step 5: Commit**

```bash
git add edmrn/ext/api.py tests/test_lookup.py
git commit -m "feat(api): system coordinate lookup with Spansh and EDSM, nearest known system" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 3: Rebuild `edmrn/ext/spansh.py` on `api.py` and move Neutron onto it

`spansh_error`, `post_with_retry`, `TRANSIENT_STATUSES`, `USER_AGENT` and `nearest_known_system`
leave `spansh.py` (the last two now live in `api.py`). Their tests leave with them:
- **`tests/test_spansh.py` is rewritten** against the new functions. Every old test is replaced;
  the cases it covered are carried over:
  - reason text;
  - 4xx without retry;
  - transient retries (now in `tests/test_api.py`);
  - unknown start;
  - same-name nearest;
  - other 400;
  - polling.
- **`NeutronRouter` tests move** to the new `tests/test_neutron.py`.

Neutron keeps working at this commit; Galaxy and the other sites still use their old code until
their tasks.

**Files:**
- Replace: `edmrn/ext/spansh.py`, `tests/test_spansh.py`
- Create: `tests/test_neutron.py`
- Modify: `tests/test_neutron_manager.py` (append one test)
- Modify (upstream): `edmrn/neutron.py`, `edmrn/neutron_manager.py`
- Modify: `docs/architecture/overview.md`,
  `docs/superpowers/plans/2026-10-11-neutron-route-resume.md`

**Interfaces:**
- Consumes (Tasks 1–2):
  - `call`, `ApiError`, `ApiFailure`, `TRANSIENT_KINDS`, `failure_from_response`, `json_body` and
    `one_line`;
  - `lookup_system`, `Lookup` and `nearest_known_system`.
- Produces (`edmrn/ext/spansh.py`):
  - `RouteSubmission(response, note: str | None, used_from)`, a frozen dataclass.
    `.job_id() -> str` raises `ApiError` with the A2 reason unless the response is a 202 carrying
    a job.
  - `is_unknown_start(response) -> bool`.
  - `submit_route_job(url, params, *, start_coords=None, coords_lookup=lookup_system,
    on_retry=None, timeout=60, call=call) -> RouteSubmission`.
    - `coords_lookup(name, *, call) -> Lookup`.
    - It never mutates `params`.
    - 4xx responses come back inside the submission; service failures raise `ApiError`.
  - `poll_route_result(job_id, *, max_wait, on_progress=None, call=call, clock=time.monotonic,
    sleep=time.sleep) -> dict`.
    - `on_progress(elapsed_seconds: float)`.
    - It returns the 200 body and raises `ApiError` otherwise.
  - Constants: `RESULTS_URL`, `POLL_TIMEOUT = 15`, `MAX_TRANSIENT_POLLS = 3`.
- Produces (`edmrn/neutron.py`):
  - `NeutronRouter.calculate_route` keeps its signature and result keys (`note`, `start_system`).
    Its `error` is the `ApiFailure` sentence.
  - `_wait_for_result(job_id, progress_callback=None)` keeps its signature and polls for 180 s.
- Produces (`edmrn/neutron_manager.py`): `calculation_done` runs via `app.root.after(0, …)`.

- [ ] **Step 1: Rewrite `tests/test_spansh.py`** (replace the whole file)

```python
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
```

- [ ] **Step 2: Create `tests/test_neutron.py`**

```python
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
```

- [ ] **Step 3: Append to `tests/test_neutron_manager.py`** (two blank lines before it)

```python
def test_calculation_done_runs_on_the_tk_loop(errors, monkeypatch):
    from fakes import Root
    result = {"success": False, "error": "Spansh is overloaded (HTTP 502). Try again in a minute."}
    app, _ = make_app(result=result)
    app.root = Root()
    nm.NeutronManager(app).calculate_neutron_route()
    assert errors == [] and app.neutron_calculate_btn.text == "Calculating..."
    app.root.run()
    assert app.neutron_calculate_btn.text == "🚀 Calculate Neutron Route"
    assert app.neutron_info_label.text == "❌ Error: Spansh is overloaded (HTTP 502). Try again in a minute."
    assert errors == ["Failed to calculate route:\nSpansh is overloaded (HTTP 502). Try again in a minute."]
```

- [ ] **Step 4: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_spansh.py tests/test_neutron.py tests/test_neutron_manager.py -v`
Expected: 2 collection errors, `ImportError: cannot import name 'RouteSubmission' from
'edmrn.ext.spansh'` (they stop the run).

Then run `.venv/Scripts/python.exe -m pytest tests/test_neutron_manager.py -v`.
Expected: `test_calculation_done_runs_on_the_tk_loop` fails, because `calculation_done` ran at once
and the button text is already `🚀 Calculate Neutron Route`.

- [ ] **Step 5: Replace `edmrn/ext/spansh.py`** (whole file)

```python
"""Spansh route jobs: submit with a nearest-known-start fallback, poll by wall-clock time.

Shared by Neutron and Galaxy (API hardening spec A5, A6). Every request goes through
edmrn.ext.api.call. Spansh facts: ../../../knowledge/bulk-data.md, section "Spansh API".
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

from edmrn.ext.api import (
    TRANSIENT_KINDS, ApiError, ApiFailure, call, failure_from_response, json_body, lookup_system,
    nearest_known_system, one_line,
)
from edmrn.logger import get_logger

logger = get_logger('Spansh')

RESULTS_URL = "https://spansh.co.uk/api/results"
UNKNOWN_START_ERROR = "could not find starting system"
POLL_TIMEOUT = 15
MAX_TRANSIENT_POLLS = 3
FIRST_POLL_INTERVAL = 1.0
MAX_POLL_INTERVAL = 3.0


@dataclass(frozen=True)
class RouteSubmission:
    """The answer to a route job submission; note and used_from describe a substituted start."""
    response: Any
    note: Optional[str]
    used_from: Any

    def job_id(self) -> str:
        """The job id of an accepted (202) submission; ApiError with the A2 reason otherwise."""
        if self.response.status_code != 202:
            raise ApiError(failure_from_response(self.response, "Spansh"))
        body = json_body(self.response, "Spansh")
        job = body.get("job") if isinstance(body, dict) else None
        if not isinstance(job, str) or not job.strip():
            raise ApiError(ApiFailure("Spansh", "unexpected", 202))
        return job


def _is_coords(value) -> bool:
    return (isinstance(value, (tuple, list)) and len(value) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value))


def is_unknown_start(response) -> bool:
    return (response.status_code == 400
            and failure_from_response(response, "Spansh").detail.strip().lower() == UNKNOWN_START_ERROR)


def submit_route_job(url, params, *, start_coords=None, coords_lookup=lookup_system, on_retry=None,
                     timeout=60, call=call) -> RouteSubmission:
    """Submit a route job. If Spansh does not know the start, plot once more from the nearest
    system it knows (A5): coordinates come from start_coords, else from coords_lookup(from).

    Returns the final response; 4xx responses are returned, service failures raise ApiError.
    """
    original = params.get("from")
    response = call("POST", url, service="Spansh", params=params, timeout=timeout, on_retry=on_retry)
    if not is_unknown_start(response):
        return RouteSubmission(response, None, original)
    coords = tuple(start_coords) if _is_coords(start_coords) else None
    if coords is None:
        found = coords_lookup(str(original), call=call)
        if not found.found:
            logger.info(f"No coordinates for unknown start '{original}': {found.message}")
            return RouteSubmission(response, None, original)
        coords = found.coords
    try:
        nearest = nearest_known_system(*coords, call=call)
    except ApiError as e:
        logger.info(f"Nearest known system for '{original}' unavailable: {e.failure}")
        return RouteSubmission(response, None, original)
    if not nearest or nearest[0].strip().casefold() == str(original).strip().casefold():
        return RouteSubmission(response, None, original)
    name, distance = nearest
    retried = call("POST", url, service="Spansh", params={**params, "from": name}, timeout=timeout,
                   on_retry=on_retry)
    note = (f"Start system '{original}' is not known to Spansh yet - plotted from nearest "
            f"known system '{name}' ({distance:.1f} ly away).")
    return RouteSubmission(retried, note, name)


def poll_route_result(job_id, *, max_wait, on_progress=None, call=call, clock=time.monotonic,
                      sleep=time.sleep) -> dict:
    """Wait for a route job by wall-clock time (A6) and return its finished (200) body.

    Polls every 1 to 3 s. Up to MAX_TRANSIENT_POLLS consecutive transient failures are tolerated;
    any successful answer resets the count. Raises ApiError on any other failure or at max_wait.
    """
    url = f"{RESULTS_URL}/{quote(str(job_id), safe='')}"
    started = clock()
    interval = FIRST_POLL_INTERVAL
    transient = 0
    while True:
        try:
            response = call("GET", url, service="Spansh", timeout=POLL_TIMEOUT, retries=0)
        except ApiError as e:
            if e.failure.kind not in TRANSIENT_KINDS:
                raise
            transient += 1
            if transient > MAX_TRANSIENT_POLLS:
                raise
            logger.info(f"Spansh poll failed ({transient}/{MAX_TRANSIENT_POLLS} tolerated): {e.failure}")
        else:
            transient = 0
            if response.status_code == 200:
                body = json_body(response, "Spansh")
                if not isinstance(body, dict):
                    raise ApiError(ApiFailure("Spansh", "unexpected", 200))
                error = body.get("error")
                if isinstance(error, str) and error.strip():
                    raise ApiError(ApiFailure("Spansh", "rejected", 200, one_line(error)))
                return body
            if response.status_code != 202:
                raise ApiError(failure_from_response(response, "Spansh"))
        elapsed = clock() - started
        if elapsed >= max_wait:
            raise ApiError(ApiFailure("Spansh", "timeout", None,
                                      f"{max_wait:g} s; the job may still finish on Spansh's website"))
        if on_progress:
            on_progress(elapsed)
        sleep(interval)
        interval = min(MAX_POLL_INTERVAL, interval + 0.5)
```

- [ ] **Step 6: Edit `edmrn/neutron.py`** (CRLF; exactly these blocks)

**E3.1** `edmrn/neutron.py`, line 8. Replace:

```python
from edmrn.ext.spansh import submit_route_job, spansh_error, TRANSIENT_STATUSES
```

with:

```python
from edmrn.ext.spansh import submit_route_job, poll_route_result
from edmrn.ext.api import ApiError
```

**E3.2** `edmrn/neutron.py`, line 35. Replace:

```python
            response, start_note, used_from = submit_route_job(
```

with:

```python
            submission = submit_route_job(
```

**E3.3** `edmrn/neutron.py`, lines 44–54. Replace:

```python
                headers={'User-Agent': "EDMRN 3.0"},
                timeout=30,
                start_coords=start_coords,
                on_retry=lambda n, why: progress_callback(f"Spansh busy, retrying ({n}/2)...") if progress_callback else None
            )
            if response.status_code != 202:
                return {"success": False, "error": f"API Error {response.status_code}: {spansh_error(response)}"}
            data = response.json()
            job_id = data.get("job")
            if not job_id:
                return {"success": False, "error": "No job ID received"}
```

with:

```python
                timeout=30,
                start_coords=start_coords,
                on_retry=lambda n, why: progress_callback(f"Spansh busy, retrying ({n}/2)...") if progress_callback else None
            )
            start_note, used_from = submission.note, submission.used_from
            job_id = submission.job_id()
```

**E3.4** `edmrn/neutron.py`, lines 73–79. Replace:

```python
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Request timeout - Spansh API not responding"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection error - Check internet connection"}
        except Exception as e:
            logger.error(f"Neutron route calculation error: {e}")
            return {"success": False, "error": f"Calculation error: {str(e)}"}
```

with:

```python
        except ApiError as e:
            return {"success": False, "error": str(e.failure)}
        except Exception as e:
            logger.exception(f"Neutron route calculation error: {e}")
            return {"success": False, "error": "Unexpected error talking to Spansh."}
```

**E3.5** `edmrn/neutron.py`, lines 83–116. Replace:

```python
        max_attempts = 20
        attempt = 0
        transient_failures = 0
        while attempt < max_attempts:
            try:
                try:
                    response = requests.get(f"{self.results_api_url}/{job_id}", timeout=10)
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    transient_failures += 1
                    if transient_failures >= 3:
                        return {"success": False, "error": f"Result API Error: {e}"}
                    time.sleep(1)
                    attempt += 1
                    continue
                if response.status_code in TRANSIENT_STATUSES:
                    transient_failures += 1
                    if transient_failures >= 3:
                        return {"success": False, "error": f"Result API Error: {response.status_code}"}
                    time.sleep(1)
                    attempt += 1
                    continue
                transient_failures = 0
                if response.status_code == 200:
                    data = response.json()
                    return {"success": True, "data": data}
                elif response.status_code != 202:
                    return {"success": False, "error": f"Result API Error {response.status_code}: {spansh_error(response)}"}
                if progress_callback:
                    progress_callback(f"Calculating... ({attempt + 1}/20)")
                time.sleep(1)
                attempt += 1
            except Exception as e:
                return {"success": False, "error": f"Result check error: {str(e)}"}
        return {"success": False, "error": "Calculation timeout"}
```

with:

```python
        on_progress = (lambda elapsed: progress_callback(f"Calculating... ({elapsed:.0f} s)")) if progress_callback else None
        try:
            return {"success": True, "data": poll_route_result(job_id, max_wait=180, on_progress=on_progress)}
        except ApiError as e:
            return {"success": False, "error": str(e.failure)}
```

`import requests` and `import time` stay, although they are now unused: removing them is
merge-conflict surface for nothing.

- [ ] **Step 7: Edit `edmrn/neutron_manager.py`**

**E3.6** `edmrn/neutron_manager.py`, line 106. Replace:

```python
            calculation_done, progress_update, start_coords=start_coords
```

with:

```python
            lambda result: self.app.root.after(0, lambda: calculation_done(result)), progress_update, start_coords=start_coords
```

- [ ] **Step 8: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `80 passed`.

Then run `git diff --stat edmrn/`. Expected: only `neutron.py` (14 insertions, 49 deletions) and
`neutron_manager.py` (1, 1); `file edmrn/neutron.py edmrn/neutron_manager.py` reports CRLF.

- [ ] **Step 9: Adapt the neutron-resume plan.** In
  `docs/superpowers/plans/2026-10-11-neutron-route-resume.md`, Task 2 Step 1, lines 545–576, its
  test still fakes the old tuple-returning `submit_route_job` and `spansh_error`. Replace exactly
  these lines:

```python
from types import SimpleNamespace

import edmrn.neutron as neutron


def _fake_submit(status, from_used="Sol"):
    response = SimpleNamespace(status_code=status, json=lambda: {"job": "J1"}, text="", reason="")
    return lambda *a, **k: (response, None, from_used)


def test_last_request_set_on_success(monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job", _fake_submit(202, "Nearest"))
    router = neutron.NeutronRouter()
    monkeypatch.setattr(router, "_wait_for_result", lambda job, cb=None: {
        "success": True, "data": {"result": {"system_jumps": [
            {"system": "Nearest", "distance_jumped": 0, "jumps": 0, "neutron_star": False},
            {"system": "Colonia", "distance_jumped": 100, "jumps": 2, "neutron_star": True}]}}})

    result = router.calculate_route("Unknown", "Colonia", 70.0, "x6")

    assert result["success"]
    assert router.last_request == {"from": "Nearest", "to": "Colonia", "range": 70.0, "boost": "x6"}


def test_last_request_untouched_on_failure(monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job", _fake_submit(400))
    monkeypatch.setattr(neutron, "spansh_error", lambda r: "Could not find starting system")
    router = neutron.NeutronRouter()
    router.last_request = {"keep": True}

    assert not router.calculate_route("Sool", "Colonia", 70.0)["success"]
    assert router.last_request == {"keep": True}
```

with:

```python
from types import SimpleNamespace

import edmrn.neutron as neutron
from edmrn.ext.spansh import RouteSubmission


def _fake_submit(status, from_used="Sol"):
    body = {"job": "J1"} if status == 202 else {"error": "Could not find starting system"}
    response = SimpleNamespace(status_code=status, json=lambda: body, text="", reason="")
    return lambda *a, **k: RouteSubmission(response, None, from_used)


def test_last_request_set_on_success(monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job", _fake_submit(202, "Nearest"))
    router = neutron.NeutronRouter()
    monkeypatch.setattr(router, "_wait_for_result", lambda job, cb=None: {
        "success": True, "data": {"result": {"system_jumps": [
            {"system": "Nearest", "distance_jumped": 0, "jumps": 0, "neutron_star": False},
            {"system": "Colonia", "distance_jumped": 100, "jumps": 2, "neutron_star": True}]}}})

    result = router.calculate_route("Unknown", "Colonia", 70.0, "x6")

    assert result["success"]
    assert router.last_request == {"from": "Nearest", "to": "Colonia", "range": 70.0, "boost": "x6"}


def test_last_request_untouched_on_failure(monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job", _fake_submit(400))
    router = neutron.NeutronRouter()
    router.last_request = {"keep": True}

    assert not router.calculate_route("Sool", "Colonia", 70.0)["success"]
    assert router.last_request == {"keep": True}
```

This test was run against the rebuilt router while writing this plan, with that plan's one-line
`last_request` hook inserted; both tests pass.

- [ ] **Step 10: Docs.** In `docs/architecture/overview.md`, hook-point table, replace the
  `edmrn/neutron.py` row and extend the `edmrn/neutron_manager.py` row:

```markdown
| `edmrn/neutron.py` | Route submission and result polling go through `edmrn.ext.spansh` (`submit_route_job`, `poll_route_result` for 180 s); the error text is the `ApiFailure` sentence; `start_coords` and the result keys `note` and `start_system` | Spansh answers bad input with 400 and a JSON reason, and 502 or timeouts when overloaded; EDMRN showed `API Error: 400` and gave up after 20 polls (API hardening spec A5, A6, A8) | yes |
| `edmrn/neutron_manager.py` | Range check requires more than 10 LY; passes the journal start coordinates; shows the substituted start; `calculation_done` runs via `root.after` | Spansh rejects ranges of 10 LY or less; a start system unknown to Spansh (explored, undiscovered) is plotted from the nearest known one; the result callback configured Tk widgets from the worker thread (A8) | yes |
```

- [ ] **Step 11: Commit**

```bash
git add edmrn/ext/spansh.py edmrn/neutron.py edmrn/neutron_manager.py tests/test_spansh.py tests/test_neutron.py tests/test_neutron_manager.py docs/architecture/overview.md docs/superpowers/plans/2026-10-11-neutron-route-resume.md
git commit -m "feat(spansh): route jobs on ext/api with start lookup and wall-clock polling; Neutron uses it" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 4: Galaxy Plotter

**Files:**
- Modify: `edmrn/ext/spansh.py` (append `journal_start_coords`)
- Create: `tests/test_galaxy.py`
- Modify (upstream): `edmrn/galaxy_plotter.py`; `edmrn/app.py` (import,
  `_calculate_galaxy_route`, `_display_galaxy_route_result`)
- Modify: `docs/architecture/overview.md`

**Interfaces:**
- Consumes (Task 3): `submit_route_job`, `poll_route_result`, `RouteSubmission`; Task 1:
  `ApiError`, `ApiFailure`.
- Produces:
  - `journal_start_coords(journal_monitor, name) -> tuple[float, float, float] | None`.
    - It returns the coordinates when `name` equals the journal's current system (case- and
      space-insensitive).
    - It never raises.
  - `GalaxyPlotter.submit_route_job(..., progress_callback=None, start_coords=None)
    -> tuple[str, str | None]`, which raises `ApiError` (Spec ruling 3).
  - `GalaxyPlotter.poll_route_results(job_id, max_wait=300, progress_callback=None) -> dict`,
    which raises `ApiError`.
  - `GalaxyPlotter.plot_route(..., range_ly=None, start_coords=None) -> dict`.
    - On success it is Spansh's body plus the key `note`.
    - On failure it is `{"error": str}`, and it never raises.
- Uses these upstream app attributes:
  - `galaxy_calculate_btn`, `galaxy_output` and `journal_monitor`;
  - `galaxy_already_supercharged`, `galaxy_use_injections`, `galaxy_exclude_secondary`,
    `galaxy_refuel_every` and `galaxy_algorithm_var`.

- [ ] **Step 1: Write the failing tests** (`tests/test_galaxy.py`)

```python
"""Galaxy Plotter on edmrn.ext.spansh (A5-A7): reasons stay visible, button state, Tk reads. No network."""
import sys
from types import SimpleNamespace

import pytest

import edmrn.galaxy_plotter as gp
from edmrn.ext.api import ApiError, ApiFailure
from edmrn.ext.spansh import RouteSubmission
from fakes import FakeResponse, Root, SyncThread, Text, Var, Widget

_hook = sys.excepthook
import edmrn.app as app_module  # noqa: E402  (sets sys.excepthook at import)
sys.excepthook = _hook

DONE = {"job": "j1", "result": {"distance": 100.0, "system_jumps": [
    {"system": "Near Sys", "distance_jumped": 0.0, "jumps": 0, "neutron_star": False, "refuel": False},
    {"system": "Sol", "distance_jumped": 100.0, "jumps": 2, "neutron_star": True, "refuel": True}]}}


def plot(plotter=None, **kwargs):
    plotter = plotter or gp.GalaxyPlotter()
    return plotter.plot_route("Far Sys", "Sol", ship_build="", range_ly=50.0, **kwargs)


# --- GalaxyPlotter -----------------------------------------------------------

def test_rejection_is_returned_as_reason(monkeypatch):
    monkeypatch.setattr(gp, "submit_spansh_job", lambda url, params, **k: RouteSubmission(
        FakeResponse(400, {"error": "range must be greater than 10 LY"}), None, "Far Sys"))
    assert plot() == {"error": "Spansh rejected the request: range must be greater than 10 LY"}


def test_success_passes_start_coords_and_carries_note(monkeypatch):
    seen, polled = {}, {}

    def fake_submit(url, params, **kwargs):
        seen.update(url=url, params=params, **kwargs)
        return RouteSubmission(FakeResponse(202, {"job": "j1"}), "NOTE", "Near Sys")

    def fake_poll(job_id, *, max_wait, on_progress=None):
        polled.update(job_id=job_id, max_wait=max_wait)
        return dict(DONE)

    monkeypatch.setattr(gp, "submit_spansh_job", fake_submit)
    monkeypatch.setattr(gp, "poll_route_result", fake_poll)
    result = plot(start_coords=(1.0, 2.0, 3.0))
    assert result["note"] == "NOTE" and result["result"] == DONE["result"]
    assert seen["start_coords"] == (1.0, 2.0, 3.0) and seen["timeout"] == 60
    assert seen["url"] == "https://spansh.co.uk/api/route"
    assert seen["params"]["from"] == "Far Sys" and seen["params"]["range"] == 50.0
    assert polled == {"job_id": "j1", "max_wait": 300}


def test_poll_timeout_reason(monkeypatch):
    monkeypatch.setattr(gp, "submit_spansh_job",
                        lambda url, params, **k: RouteSubmission(FakeResponse(202, {"job": "j1"}), None, "A"))

    def timed_out(job_id, *, max_wait, on_progress=None):
        raise ApiError(ApiFailure("Spansh", "timeout", None, "300 s; the job may still finish on Spansh's website"))
    monkeypatch.setattr(gp, "poll_route_result", timed_out)
    assert plot() == {"error": "Spansh did not answer within 300 s; the job may still finish on Spansh's website."}


def test_overloaded_submit_reason(monkeypatch):
    def overloaded(url, params, **k):
        raise ApiError(ApiFailure("Spansh", "overloaded", 503))
    monkeypatch.setattr(gp, "submit_spansh_job", overloaded)
    assert plot() == {"error": "Spansh is overloaded (HTTP 503). Try again in a minute."}


def test_bug_is_unexpected_not_raw(monkeypatch):
    def broken(url, params, **k):
        raise KeyError("internal")
    monkeypatch.setattr(gp, "submit_spansh_job", broken)
    assert plot() == {"error": "Unexpected error talking to Spansh."}


# --- EDMRN_App galaxy flow (fake app, no Tk) ---------------------------------

class Journal:
    def __init__(self, system, coords):
        self.system, self.coords = system, coords

    def get_current_system(self):
        return self.system

    def get_current_coordinates(self):
        return self.coords


class StartRecordingThread(SyncThread):
    log = None

    def start(self):
        StartRecordingThread.log.append(("thread-start",))
        super().start()


def make_app(monkeypatch, result, source="Far Sys", journal=None):
    log = []
    StartRecordingThread.log = log
    monkeypatch.setattr(app_module, "threading", SimpleNamespace(Thread=StartRecordingThread))
    plotter = gp.GalaxyPlotter()
    calls = []

    def fake_plot_route(**kwargs):
        log.append(("plot",))
        calls.append(kwargs)
        return result

    plotter.plot_route = fake_plot_route
    app = SimpleNamespace(
        galaxy_source_autocomplete=Var(source), galaxy_dest_autocomplete=Var("Sol"),
        galaxy_jump_range_var=Var("50"), galaxy_ship_build=Var(""), galaxy_boost_var=Var("x6 (Caspian)"),
        galaxy_cargo_var=Var("0"), galaxy_reserve_fuel_var=Var("0"),
        galaxy_already_supercharged=Var(False, log, "already_supercharged"),
        galaxy_use_injections=Var(False, log, "use_injections"),
        galaxy_exclude_secondary=Var(True, log, "exclude_secondary"),
        galaxy_refuel_every=Var(True, log, "refuel_every"),
        galaxy_algorithm_var=Var("optimistic", log, "algorithm"),
        galaxy_output=Text(), galaxy_calculate_btn=Widget(state="normal", text="Optimize & Track"),
        galaxy_current_btn=Widget(), galaxy_prev_btn=Widget(), galaxy_next_btn=Widget(),
        galaxy_export_btn=Widget(), galaxy_plotter=plotter, journal_monitor=journal,
        current_backup_folder=None, root=Root(), logged=[],
        galaxy_route_waypoints=[], galaxy_current_waypoint_index=0,
    )
    app._log = app.logged.append
    for name in ("_update_galaxy_systems_list", "_update_galaxy_statistics", "_update_galaxy_navigation",
                 "_copy_current_galaxy_system", "_start_journal_monitor", "_ensure_overlay_started"):
        setattr(app, name, lambda *a, **k: None)
    app._display_galaxy_route_result = lambda r: app_module.EDMRN_App._display_galaxy_route_result(app, r)
    app._display_galaxy_route_error = lambda m: app_module.EDMRN_App._display_galaxy_route_error(app, m)
    return app, calls, log


def calculate(app):
    app_module.EDMRN_App._calculate_galaxy_route(app)


def test_failure_reason_stays_visible_and_button_comes_back(monkeypatch):
    app, _, _ = make_app(monkeypatch, {"error": "Spansh rejected the request: Could not find starting system"})
    calculate(app)
    assert app.galaxy_calculate_btn.state["state"] == "disabled"
    app.root.run()
    assert app.galaxy_calculate_btn.state["state"] == "normal"
    assert app.galaxy_calculate_btn.state["text"] == "Optimize & Track"
    assert "Spansh rejected the request: Could not find starting system" in app.galaxy_output.content
    assert "No route data returned" not in app.galaxy_output.content


def test_tk_variables_are_read_before_the_worker_starts(monkeypatch):
    app, calls, log = make_app(monkeypatch, {"error": "x"})
    calculate(app)
    start = log.index(("thread-start",))
    reads = [i for i, entry in enumerate(log) if entry[0] == "get"]
    assert len(reads) == 5 and max(reads) < start
    assert calls[0]["routing_algorithm"] == "optimistic" and calls[0]["exclude_secondary"] is True


def test_journal_coords_passed_when_source_is_current_system(monkeypatch):
    app, calls, _ = make_app(monkeypatch, {"error": "x"}, source="far sys",
                             journal=Journal("Far Sys", (1.0, 2.0, 3.0)))
    calculate(app)
    assert calls[0]["start_coords"] == (1.0, 2.0, 3.0)


def test_no_journal_coords_for_another_source(monkeypatch):
    app, calls, _ = make_app(monkeypatch, {"error": "x"}, source="Other",
                             journal=Journal("Far Sys", (1.0, 2.0, 3.0)))
    calculate(app)
    assert calls[0]["start_coords"] is None


def test_success_shows_substitution_note(monkeypatch):
    app, _, _ = make_app(monkeypatch, {**DONE, "note": "NOTE TEXT"})
    calculate(app)
    app.root.run()
    assert "=== ROUTE SUMMARY ===" in app.galaxy_output.content and "NOTE TEXT" in app.galaxy_output.content
    assert app.galaxy_calculate_btn.state["state"] == "normal"
    assert "Galaxy Plotter: NOTE TEXT" in app.logged


def test_button_comes_back_even_if_display_fails(monkeypatch):
    app, _, _ = make_app(monkeypatch, dict(DONE))

    def broken():
        raise RuntimeError("malformed jump")
    app._update_galaxy_systems_list = broken
    calculate(app)
    with pytest.raises(RuntimeError):
        app.root.run()
    assert app.galaxy_calculate_btn.state["state"] == "normal"


# --- journal_start_coords ------------------------------------------------------

from edmrn.ext.spansh import journal_start_coords  # noqa: E402


class BrokenJournal:
    def get_current_system(self):
        raise OSError("journal locked")


@pytest.mark.parametrize("journal,name,expected", [
    (Journal("Far Sys", (1, 2, 3)), " FAR SYS ", (1.0, 2.0, 3.0)),
    (Journal("Far Sys", (1, 2, 3)), "Other", None),
    (Journal(None, (1, 2, 3)), "Far Sys", None),
    (Journal("Far Sys", None), "Far Sys", None),
    (Journal("Far Sys", ("1", 2, 3)), "Far Sys", None),
    (BrokenJournal(), "Far Sys", None),
    (None, "Far Sys", None),
    (Journal("Far Sys", (1, 2, 3)), "", None),
])
def test_journal_start_coords(journal, name, expected):
    assert journal_start_coords(journal, name) == expected
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_galaxy.py -v`
Expected: collection error, `ImportError: cannot import name 'journal_start_coords' from
'edmrn.ext.spansh'`. After Step 3 the plotter and app tests still fail: `AttributeError: <module
'edmrn.galaxy_plotter' ...> has no attribute 'submit_spansh_job'`, or assertion errors on the
button state and the output text.

- [ ] **Step 3: Append to `edmrn/ext/spansh.py`** (after `poll_route_result`, two blank lines)

```python
def journal_start_coords(journal_monitor, name) -> Optional[tuple]:
    """Journal coordinates of `name` when it is the commander's current system, else None.

    Reads upstream's JournalMonitor getters (ADR-0001); never raises.
    """
    if not journal_monitor or not isinstance(name, str) or not name.strip():
        return None
    try:
        current = journal_monitor.get_current_system()
        if not isinstance(current, str) or current.strip().casefold() != name.strip().casefold():
            return None
        coords = journal_monitor.get_current_coordinates()
    except Exception as e:
        logger.debug(f"Journal coordinates unavailable: {e}")
        return None
    return tuple(float(v) for v in coords) if _is_coords(coords) else None
```

- [ ] **Step 4: Edit `edmrn/galaxy_plotter.py`** (CRLF; exactly these blocks)

**E4.1** `edmrn/galaxy_plotter.py`, line 5. Replace:

```python
from edmrn.logger import get_logger
```

with:

```python
from edmrn.logger import get_logger
from edmrn.ext.api import ApiError, ApiFailure
from edmrn.ext.spansh import submit_route_job as submit_spansh_job, poll_route_result
```

**E4.2** `edmrn/galaxy_plotter.py`, line 32. Replace:

```python
                        progress_callback: Optional[Callable[[str], None]] = None) -> Optional[str]:
```

with:

```python
                        progress_callback: Optional[Callable[[str], None]] = None,
                        start_coords=None):
```

**E4.3** `edmrn/galaxy_plotter.py`, lines 58–90. Replace:

```python
            response = requests.post(
                self.route_api,
                params=params,
                timeout=60,
                headers={'User-Agent': "EDMRN 3.3"}
            )
            logger.info(f"[SPNSH-RESP] Status: {response.status_code}, Body: {response.text[:1000]}")
            if response.status_code != 202:
                error_msg = response.text
                logger.error(f"Failed to submit route job: {response.status_code} - {error_msg}")
                if progress_callback:
                    progress_callback(f"Error: {error_msg}")
                return None
            data = response.json()
            job_id = data.get("job")
            if not job_id:
                logger.error(f"No job ID in response: {data}")
                if progress_callback:
                    progress_callback("Error: No job ID received from Spansh")
                return None
            logger.info(f"Route job submitted: {job_id}")
            return job_id
            
        except requests.RequestException as e:
            logger.error(f"Network error submitting route: {e}")
            if progress_callback:
                progress_callback(f"Network error: {e}")
            return None
        except Exception as e:
            logger.error(f"Error submitting route: {e}")
            if progress_callback:
                progress_callback(f"Error: {e}")
            return None
```

with:

```python
            submission = submit_spansh_job(
                self.route_api, params, start_coords=start_coords, timeout=60,
                on_retry=lambda n, failure: progress_callback(f"{failure} Retrying ({n}/2)...") if progress_callback else None
            )
            response = submission.response
            logger.info(f"[SPNSH-RESP] Status: {response.status_code}, Body: {response.text[:1000]}")
            job_id = submission.job_id()
            logger.info(f"Route job submitted: {job_id}")
            return job_id, submission.note
        except ApiError:
            raise
        except Exception as e:
            logger.exception(f"Error submitting route: {e}")
            raise ApiError(ApiFailure("Spansh", "unexpected"))
```

**E4.4** `edmrn/galaxy_plotter.py`, lines 150–197. Replace:

```python
        try:
            if progress_callback:
                progress_callback(f"Waiting for route calculation...\nJob ID: {job_id}")
            
            results_url = f"{self.results_api}/{job_id}"
            elapsed = 0
            poll_interval = 1
            
            while elapsed < max_wait:
                try:
                    response = requests.get(results_url, timeout=30)
                    
                    if response.status_code == 200:
                        data = response.json()
                        logger.info(f"Route calculation complete")
                        return data
                    
                    elif response.status_code == 202:
                        elapsed += poll_interval
                        if progress_callback:
                            progress_callback(f"Calculating route...\nElapsed: {elapsed}s")
                        time.sleep(poll_interval)
                        poll_interval = min(5, poll_interval + 0.5)
                        continue
                    
                    else:
                        error_msg = response.text
                        logger.error(f"Error polling route: {response.status_code} - {error_msg}")
                        if progress_callback:
                            progress_callback(f"Error: {error_msg}")
                        return None
                
                except requests.RequestException as e:
                    logger.error(f"Network error polling route: {e}")
                    if progress_callback:
                        progress_callback(f"Network error: {e}")
                    return None
            
            logger.error(f"Route calculation timeout after {max_wait}s")
            if progress_callback:
                progress_callback(f"Timeout: Route calculation took too long")
            return None
            
        except Exception as e:
            logger.error(f"Error polling route: {e}")
            if progress_callback:
                progress_callback(f"Error: {e}")
            return None
```

with:

```python
        if progress_callback:
            progress_callback(f"Waiting for route calculation...\nJob ID: {job_id}")
        on_progress = (lambda elapsed: progress_callback(f"Calculating route...\nElapsed: {elapsed:.0f}s")) if progress_callback else None
        return poll_route_result(job_id, max_wait=max_wait, on_progress=on_progress)
```

**E4.5** `edmrn/galaxy_plotter.py`, line 212. Replace:

```python
                   range_ly: float = None) -> Optional[Dict[str, Any]]:
```

with:

```python
                   range_ly: float = None,
                   start_coords=None) -> Optional[Dict[str, Any]]:
```

**E4.6** `edmrn/galaxy_plotter.py`, line 216. Replace:

```python
            job_id = self.submit_route_job(
```

with:

```python
            job_id, note = self.submit_route_job(
```

**E4.7** `edmrn/galaxy_plotter.py`, lines 228–239. Replace:

```python
                progress_callback=progress_callback
            )
            if not job_id:
                return None
            route_data = self.poll_route_results(job_id, max_wait=300, progress_callback=progress_callback)
            logger.info(f"[SPNSH-RESP] route_data: {json.dumps(route_data)[:1000]}")
            return route_data
        except Exception as e:
            logger.error(f"Error plotting route: {e}")
            if progress_callback:
                progress_callback(f"Error: {e}")
            return None
```

with:

```python
                progress_callback=progress_callback,
                start_coords=start_coords
            )
            route_data = self.poll_route_results(job_id, max_wait=300, progress_callback=progress_callback)
            route_data["note"] = note
            logger.info(f"[SPNSH-RESP] route_data: {json.dumps(route_data)[:1000]}")
            return route_data
        except ApiError as e:
            logger.warning(f"Galaxy route failed: {e.failure!r}")
            return {"error": str(e.failure)}
        except Exception as e:
            logger.exception(f"Error plotting route: {e}")
            return {"error": str(ApiFailure("Spansh", "unexpected"))}
```

- [ ] **Step 5: Edit `edmrn/app.py`** (CRLF; exactly these blocks)

**E4.8** `edmrn/app.py`, line 74. Replace:

```python
from edmrn.neutron import NeutronRouter
```

with:

```python
from edmrn.neutron import NeutronRouter
from edmrn.ext.spansh import journal_start_coords
```

**E4.9** `edmrn/app.py`, lines 2789–2790. Replace:

```python
        self.galaxy_output.insert("end", "⏳ Calculating route...\n\n")
        self.galaxy_output.configure(state="disabled")
```

with:

```python
        self.galaxy_output.insert("end", "⏳ Calculating route...\n\n")
        self.galaxy_output.configure(state="disabled")
        self.galaxy_calculate_btn.configure(state="disabled")
        options = dict(already_supercharged=self.galaxy_already_supercharged.get(), use_injections=self.galaxy_use_injections.get(), exclude_secondary=self.galaxy_exclude_secondary.get(), refuel_every_scoopable=self.galaxy_refuel_every.get(), routing_algorithm=self.galaxy_algorithm_var.get())
```

**E4.10** `edmrn/app.py`, lines 2804–2811. Replace:

```python
                    already_supercharged=self.galaxy_already_supercharged.get(),
                    use_supercharge=use_supercharge,
                    use_injections=self.galaxy_use_injections.get(),
                    exclude_secondary=self.galaxy_exclude_secondary.get(),
                    refuel_every_scoopable=self.galaxy_refuel_every.get(),
                    routing_algorithm=self.galaxy_algorithm_var.get(),
                    progress_callback=lambda msg: self.root.after(0, lambda: progress_callback(msg)),
                    range_ly=jump_range
```

with:

```python
                    use_supercharge=use_supercharge,
                    **options,
                    progress_callback=lambda msg: self.root.after(0, lambda: progress_callback(msg)),
                    range_ly=jump_range,
                    start_coords=journal_start_coords(self.journal_monitor, source)
```

**E4.11** `edmrn/app.py`, line 2822. Replace:

```python
    def _display_galaxy_route_result(self, result):
```

with:

```python
    def _display_galaxy_route_result(self, result):
        self.galaxy_calculate_btn.configure(state="normal")
```

**E4.12** `edmrn/app.py`, line 2827. Replace:

```python
            self.galaxy_output.insert("1.0", "No route data returned.\n")
```

with:

```python
            self.galaxy_output.insert("1.0", f"❌ {result['error']}\n" if isinstance(result, dict) and result.get('error') else "No route data returned.\n")
```

**E4.13** `edmrn/app.py`, line 2841. Replace:

```python
        self.galaxy_output.insert("end", summary + "\n\n")
```

with:

```python
        self.galaxy_output.insert("end", summary + "\n\n")
        if result.get('note'):
            self.galaxy_output.insert("end", f"{result['note']}\n\n")
            self._log(f"Galaxy Plotter: {result['note']}")
```

`_display_galaxy_route_error` stays as it is. `plot_route` no longer raises, so the worker
does not reach it.

- [ ] **Step 6: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `99 passed`.

Then `git diff --stat edmrn/galaxy_plotter.py edmrn/app.py` should show 29 insertions and 87
deletions for `galaxy_plotter.py`, and 11 and 7 for `app.py`. `file` reports CRLF for both.

- [ ] **Step 7: Docs.** Add two rows to the hook-point table in `docs/architecture/overview.md`:

```markdown
| `edmrn/galaxy_plotter.py` | `submit_route_job` and `poll_route_results` delegate to `edmrn.ext.spansh` and raise `ApiError`; `plot_route` takes `start_coords` and returns `{"error": <reason>}` instead of `None`, and the substitution `note` on success | One 502 or timeout aborted a job still running on Spansh; an unknown start had no fallback; the reason was replaced by "No route data returned." (API hardening spec A5–A7) | yes |
| `edmrn/app.py` (Galaxy) | Import of `journal_start_coords`; `_calculate_galaxy_route` disables the Calculate button, reads the Tk option variables before the worker starts and passes the journal coordinates; `_display_galaxy_route_result` re-enables the button first and shows the failure reason and the substitution note | The worker read Tk variables; the button stayed clickable during a job; the reason was cleared (A7) | yes |
```

- [ ] **Step 8: Commit**

```bash
git add edmrn/ext/spansh.py edmrn/galaxy_plotter.py edmrn/app.py tests/test_galaxy.py docs/architecture/overview.md
git commit -m "feat(galaxy): Spansh start fallback, wall-clock polling, visible reasons, button state" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 5: Custom Route coordinates

**Files:**
- Create: `tests/test_custom_route.py`
- Modify (upstream): `edmrn/custom_route.py`
- Modify: `docs/architecture/overview.md`

**Interfaces:**
- Consumes: `lookup_system`, `Lookup` (Task 2); `journal_start_coords` (Task 4).
- Produces (`CustomRouteManager`):
  - `.lookup_errors: dict[str, str]` maps a casefolded name to its A9 sentence. It is filled by
    `_fetch_coordinates` and popped by the consumer that shows it.
  - `.optimize_error: str | None` is set when `optimize_route` stops on a start or end without
    coordinates.
- `add_system` and `optimize_route` keep their `bool` returns.

- [ ] **Step 1: Write the failing tests** (`tests/test_custom_route.py`)

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_custom_route.py -v`
Expected: `1 failed, 8 errors`.
- The 8 errors come from the `lookups` fixture:
  `AttributeError: <module 'edmrn.custom_route' ...> has no attribute 'lookup_system'`.
- `test_optimize_failure_dialog_shows_the_reason` fails because no dialog is shown.

- [ ] **Step 3: Edit `edmrn/custom_route.py`** (CRLF; exactly these blocks)

**E5.1** `edmrn/custom_route.py`, line 14. Replace:

```python
from edmrn.gui import InfoDialog, WarningDialog, ErrorDialog
```

with:

```python
from edmrn.gui import InfoDialog, WarningDialog, ErrorDialog
from edmrn.ext.api import lookup_system
from edmrn.ext.spansh import journal_start_coords
```

**E5.2** `edmrn/custom_route.py`, line 32. Replace:

```python
        self._optimize_lock = threading.Lock()
```

with:

```python
        self._optimize_lock = threading.Lock()
        self.lookup_errors = {}
        self.optimize_error = None
```

**E5.3** `edmrn/custom_route.py`, line 107. Replace:

```python
    def optimize_route(self, mode: str = 'distance', boost_multiplier: float = 6.0) -> bool:
```

with:

```python
    def optimize_route(self, mode: str = 'distance', boost_multiplier: float = 6.0) -> bool:
        self.optimize_error = None
```

**E5.4** `edmrn/custom_route.py`, lines 132–133. Replace:

```python
                            'added_order': -1
                        }
```

with:

```python
                            'added_order': -1
                        }
                    else:
                        self.optimize_error = self.lookup_errors.pop(self.starting_system.strip().casefold(), None) or f"Could not look up '{self.starting_system}'."
                        return False
```

**E5.5** `edmrn/custom_route.py`, lines 147–148. Replace:

```python
                            'added_order': 999999
                        }
```

with:

```python
                            'added_order': 999999
                        }
                    else:
                        self.optimize_error = self.lookup_errors.pop(self.ending_system.strip().casefold(), None) or f"Could not look up '{self.ending_system}'."
                        return False
```

**E5.6** `edmrn/custom_route.py`, lines 336–369. Replace:

```python
    def _fetch_coordinates(self, system_name: str) -> Optional[tuple]:
        try:
            import requests
            resp = requests.post(
                'https://spansh.co.uk/api/systems/search',
                json={'filters': {'name': {'value': [system_name]}}, 'size': 1},
                headers={'User-Agent': 'EDMRN 3.3'},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get('result', [])
                if results:
                    coords = results[0].get('coords', {})
                    return (coords.get('x', 0), coords.get('y', 0), coords.get('z', 0))
        except Exception:
            pass

        try:
            import requests
            resp = requests.get(
                f'https://edsm.net/api-v1/system?systemName={system_name}&showCoordinates=1',
                headers={'User-Agent': 'EDMRN 3.3'},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                coords = data.get('coords', {})
                if coords:
                    return (coords.get('x', 0), coords.get('y', 0), coords.get('z', 0))
        except Exception:
            pass

        return None
```

with:

```python
    def _fetch_coordinates(self, system_name: str) -> Optional[tuple]:
        coords = journal_start_coords(getattr(self.app, 'journal_monitor', None), system_name)
        if coords:
            return coords
        found = lookup_system(system_name)
        if not found.found:
            self.lookup_errors[system_name.strip().casefold()] = found.message
        return found.coords
```

**E5.7** `edmrn/custom_route.py`, lines 697–701. Replace:

```python
            else:
                self.app.root.after(0, lambda: WarningDialog(
                    self.app, "System Not Found",
                    f"Could not find system: {name}\n\nCheck the spelling and try again."
                ))
```

with:

```python
            else:
                message = self.manager.lookup_errors.pop(name.strip().casefold(), None) or f"Could not find system: {name}\n\nCheck the spelling and try again."
                self.app.root.after(0, lambda: WarningDialog(
                    self.app, "System Lookup",
                    message
                ))
```

**E5.8** `edmrn/custom_route.py`, lines 799–800. Replace:

```python
        else:
            self.app._log("Route optimization failed")
```

with:

```python
        else:
            self.app._log("Route optimization failed")
            if self.manager.optimize_error:
                WarningDialog(self.app, "Route Optimization", self.manager.optimize_error)
```

The dialog title changes from "System Not Found" to "System Lookup", because the message may now
report an outage rather than a typo (A3).

- [ ] **Step 4: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `108 passed`. `git diff --stat edmrn/custom_route.py` shows 23 insertions and 35
deletions; `file` reports CRLF.

- [ ] **Step 5: Docs.** Add a row to the hook-point table:

```markdown
| `edmrn/custom_route.py` (coordinates) | Imports; `_fetch_coordinates` uses the journal coordinates for the current system, else `edmrn.ext.api.lookup_system`, and records the A9 sentence in `lookup_errors`; `_add_system_async` shows it; `optimize_route` stops with `optimize_error` when a start or end has no coordinates, and `_on_optimize_complete` shows it | Every lookup fell through to EDSM because the Spansh reply was misread; every failure said "System Not Found"; an unknown start or end was silently dropped (API hardening spec A9, ruling 4 of the plan) | yes |
```

- [ ] **Step 6: Commit**

```bash
git add edmrn/custom_route.py tests/test_custom_route.py docs/architecture/overview.md
git commit -m "feat(custom-route): coordinates via lookup_system, readable messages, stop on missing start or end" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 6: System Info

**Files:**
- Create: `edmrn/ext/system_info.py`, `tests/test_system_info.py`
- Modify (upstream): `edmrn/app.py` (import, `fetch_edsm_system_data`, two status lines in
  `_system_info_fetch_callback`)
- Modify: `docs/architecture/overview.md`

**Interfaces:**
- Consumes (Task 1): `call`, `ApiError`, `ApiFailure`, `failure_from_response`, `json_body`.
- Produces: `fetch_system_data(system_name: str, *, call=call) -> dict`. It never raises and
  returns one of:
  - `{"error": <A2 sentence>}`;
  - EDSM's system dict plus:
    - `bodies`, `stations` and `factions` (lists);
    - `gmp` (str) and `url` (str);
    - `notes`: a list of strings, one per missing part.
- `EDMRN_App.fetch_edsm_system_data(system_name)` keeps its signature and returns that dict.

- [ ] **Step 1: Write the failing tests** (`tests/test_system_info.py`)

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_system_info.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'edmrn.ext.system_info'`.

- [ ] **Step 3: Implement `edmrn/ext/system_info.py`**

```python
"""System Info data from EDSM and EDAstro (API hardening spec A10).

EDSM's system API answers an unknown system with [] and the per-system APIs with {} or []
(../../../knowledge/edsm-api.md). Every request goes through edmrn.ext.api.call.
"""
from __future__ import annotations

from urllib.parse import quote_plus, urlencode

from edmrn.ext.api import ApiError, ApiFailure, call, failure_from_response, json_body
from edmrn.logger import get_logger

logger = get_logger('SystemInfo')

EDSM = "https://www.edsm.net"
EDASTRO_GEC_URL = "https://edastro.com/gec/json/id64/{id64}"
PARTS = (("bodies", "Bodies"), ("stations", "Stations"), ("factions", "Factions"))


def _get(url, service, params=None, call=call, timeout=10):
    """JSON body of a 200 answer; ApiError for anything else."""
    response = call("GET", url, service=service, params=params, timeout=timeout)
    if response.status_code != 200:
        raise ApiError(failure_from_response(response, service))
    return json_body(response, service)


def _list_part(body, key):
    if isinstance(body, list):
        return body
    if isinstance(body, dict) and isinstance(body.get(key), list):
        return body[key]
    return []


def _gec_text(gec) -> str:
    if not (isinstance(gec, dict) and gec.get('name')):
        return ""
    text = (f"{gec.get('name', '')}\nType: {gec.get('type', '')}\nRegion: {gec.get('region', '')}\n\n"
            f"{gec.get('summary', '')}\n\n{gec.get('descriptionMardown', '')}")
    if gec.get('poiUrl'):
        text += f"\n\nLink: {gec['poiUrl']}"
    return text.strip()


def _edsm_page_url(system_name, system_id) -> str:
    if isinstance(system_id, int) and not isinstance(system_id, bool):
        return f"{EDSM}/en/system/id/{system_id}/name/{quote_plus(system_name)}"
    return f"{EDSM}/en/system?{urlencode({'systemName': system_name})}"


def fetch_system_data(system_name: str, *, call=call) -> dict:
    """Everything System Info shows for a system, or {"error": <A2 sentence>}.

    A failed bodies, stations, factions or EDAstro request keeps the rest and adds a line to
    data["notes"]. Never raises.
    """
    try:
        try:
            data = _get(f"{EDSM}/api-v1/system", "EDSM", call=call, params={
                "systemName": system_name, "showInformation": 1, "showCoordinates": 1,
                "showPrimaryStar": 1, "showTraffic": 1, "showPermit": 1, "showId": 1})
        except ApiError as e:
            return {"error": str(e.failure)}
        if not isinstance(data, dict) or not data:
            return {"error": f"System '{system_name}' is not known to EDSM."}
        data["notes"] = []
        for key, label in PARTS:
            try:
                body = _get(f"{EDSM}/api-system-v1/{key}", "EDSM", call=call,
                            params={"systemName": system_name})
            except ApiError as e:
                data["notes"].append(f"{label} could not be loaded: {e.failure}")
                body = None
            data[key] = _list_part(body, key)
        data["gmp"] = ""
        id64 = data.get("id64")
        if isinstance(id64, int) and not isinstance(id64, bool):
            try:
                data["gmp"] = _gec_text(_get(EDASTRO_GEC_URL.format(id64=id64), "EDAstro", call=call, timeout=15))
            except ApiError as e:
                if e.failure.kind != "rejected":
                    data["notes"].append(f"Galactic POI data could not be loaded: {e.failure}")
        data["url"] = _edsm_page_url(system_name, data.get("id"))
        return data
    except Exception:
        logger.exception(f"System Info for {system_name!r} failed")
        return {"error": str(ApiFailure("EDSM", "unexpected"))}
```

- [ ] **Step 4: Edit `edmrn/app.py`** (CRLF; exactly these blocks)

**E6.1** `edmrn/app.py`, the line `from edmrn.ext.spansh import journal_start_coords`. Replace:

```python
from edmrn.ext.spansh import journal_start_coords
```

with:

```python
from edmrn.ext.spansh import journal_start_coords
from edmrn.ext.system_info import fetch_system_data
```

**E6.2** `edmrn/app.py`, lines 422–507. Replace:

```python
        """Fetch full system details from EDSM API (traffic, factions, permit, gmp included)."""
        import traceback
        headers = {'User-Agent': 'EDMRN 3.3'}
        try:
            url_basic = (
                f'https://www.edsm.net/api-v1/system?systemName={system_name}'
                f'&showInformation=1&showCoordinates=1&showPrimaryStar=1&showTraffic=1&showPermit=1&showId=1'
            )
            response_basic = requests.get(url_basic, headers=headers, timeout=10)
            if response_basic.status_code != 200:
                return {"error": f"EDSM API error: {response_basic.status_code}"}
            data = response_basic.json()
            if isinstance(data, list):
                data = {"bodies": data}

            url_bodies = f'https://www.edsm.net/api-system-v1/bodies?systemName={system_name}'
            response_bodies = requests.get(url_bodies, headers=headers, timeout=10)
            if response_bodies.status_code == 200:
                bodies_data = response_bodies.json()
                if isinstance(bodies_data, list):
                    data['bodies'] = bodies_data
                elif 'bodies' in bodies_data:
                    data['bodies'] = bodies_data['bodies']
                else:
                    data['bodies'] = []
            else:
                data['bodies'] = []

            url_stations = f'https://www.edsm.net/api-system-v1/stations?systemName={system_name}'
            response_stations = requests.get(url_stations, headers=headers, timeout=10)
            if response_stations.status_code == 200:
                stations_data = response_stations.json()
                if isinstance(stations_data, list):
                    data['stations'] = stations_data
                elif 'stations' in stations_data:
                    data['stations'] = stations_data['stations']
                else:
                    data['stations'] = []
            else:
                data['stations'] = []

            url_factions = f'https://www.edsm.net/api-system-v1/factions?systemName={system_name}'
            response_factions = requests.get(url_factions, headers=headers, timeout=10)
            if response_factions.status_code == 200:
                factions_data = response_factions.json()
                if 'factions' in factions_data:
                    data['factions'] = factions_data['factions']

            try:
                system_id64 = data.get('id64')
                if system_id64:
                    gec_url = f'https://edastro.com/gec/json/id64/{system_id64}'
                    response_gec = requests.get(gec_url, headers=headers, timeout=15)
                    if response_gec.status_code == 200:
                        gec_data = response_gec.json()
                        if gec_data and isinstance(gec_data, dict) and gec_data.get('name'):
                            gmp_text = f"{gec_data.get('name', '')}\nType: {gec_data.get('type', '')}\nRegion: {gec_data.get('region', '')}\n\n{gec_data.get('summary', '')}\n\n{gec_data.get('descriptionMardown', '')}"
                            poi_url = gec_data.get('poiUrl', '')
                            if poi_url:
                                gmp_text += f"\n\nLink: {poi_url}"
                            data['gmp'] = gmp_text.strip()
                        else:
                            data['gmp'] = ""
                    else:
                        data['gmp'] = ""
                else:
                    data['gmp'] = ""
            except Exception as e:
                logger.warning(f"Failed to fetch GEC data for {system_name}: {e}")
                data['gmp'] = ""

            if data.get("id"):
                data['url'] = f'https://www.edsm.net/en/system/id/{data.get("id")}/name/{system_name.replace(" ", "+")}'
            else:
                data['url'] = f'https://www.edsm.net/en/system?systemName={system_name.replace(" ", "+")}'

            return data
        except Exception as e:
            tb = traceback.format_exc()
            try:
                with open("edmrn_crash.log", "a", encoding="utf-8") as f:
                    f.write(f"[fetch_edsm_system_data] Exception: {str(e)}\n{tb}\n")
                    f.flush()
            except Exception:
                pass
            return {"error": str(e)}
```

with:

```python
        """Fetch full system details from EDSM API (traffic, factions, permit, gmp included)."""
        return fetch_system_data(system_name)
```

**E6.3** `edmrn/app.py`, line 1284. Replace:

```python
                        status_label.configure(text=f"Error: {system_data['error']}", text_color="#FF6B6B")
```

with:

```python
                        status_label.configure(text=system_data['error'], text_color="#FF6B6B")
```

**E6.4** `edmrn/app.py`, line 1293. Replace:

```python
                        status_label.configure(text=f"Data fetched for {system_name}", text_color="#4CAF50")
```

with:

```python
                        status_label.configure(text="\n".join([f"Data fetched for {system_name}", *system_data.get('notes', [])]), text_color="#4CAF50")
```

The thread wrapper in `_system_info_fetch_callback` keeps its own `except` (and its
`edmrn_crash.log` write). `fetch_system_data` never raises, so network failures no longer reach
it (A10).

- [ ] **Step 5: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `122 passed`. `git diff --stat edmrn/app.py` adds 4 insertions and 87 deletions to
Task 4's numbers; `file` reports CRLF.

- [ ] **Step 6: Docs.** Add a row to the hook-point table:

```markdown
| `edmrn/app.py` (System Info) | Import of `fetch_system_data`; `fetch_edsm_system_data` delegates to `edmrn.ext.system_info`; `_system_info_fetch_callback` shows the failure sentence without a prefix and lists the per-part notes under "Data fetched" | Raw names in EDSM URLs broke on `&`, `#`, `+`; an unknown system was reported as fetched; one failed request discarded all five; network errors went to `edmrn_crash.log` (API hardening spec A10) | yes |
```

- [ ] **Step 7: Commit**

```bash
git add edmrn/ext/system_info.py edmrn/app.py tests/test_system_info.py docs/architecture/overview.md
git commit -m "feat(system-info): encoded EDSM requests, unknown systems, partial failures with notes" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 7: Nearest system by coordinates

**Files:**
- Create: `tests/test_nearest.py`
- Modify (upstream): `edmrn/app.py` (import, `_find_nearest_system_by_coordinates`,
  `_show_nearest_results`)
- Modify: `docs/architecture/overview.md`

**Interfaces:**
- Consumes (Task 2): `nearest_system(x, y, z) -> dict | None`, which raises `ApiError`.
- `_show_nearest_results(data, x, y, z)` keeps its signature. `data` is `{"system": <record>}`.

- [ ] **Step 1: Write the failing tests** (`tests/test_nearest.py`)

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_nearest.py -v`
Expected: `5 failed`, each with
`AttributeError: <module 'edmrn.app' ...> has no attribute 'nearest_system'`.

- [ ] **Step 3: Edit `edmrn/app.py`** (CRLF; exactly these blocks)

**E7.1** `edmrn/app.py`, the line `from edmrn.ext.system_info import fetch_system_data`. Replace:

```python
from edmrn.ext.system_info import fetch_system_data
```

with:

```python
from edmrn.ext.system_info import fetch_system_data
from edmrn.ext.api import ApiError, nearest_system
```

**E7.2** `edmrn/app.py`, lines 2492–2503. Replace:

```python
                    import requests
                    url = "https://spansh.co.uk/api/nearest"
                    params = {
                        'x': x_val,
                        'y': y_val,
                        'z': z_val,
                        'limit': 5
                    }
                    
                    response = requests.get(url, params=params, timeout=10)
                    response.raise_for_status()
                    data = response.json()
```

with:

```python
                    system = nearest_system(x_val, y_val, z_val)
                    data = {'system': system} if system else None
```

The whitespace-only line that follows (before `if data and 'system' in data:`) stays untouched.

**E7.3** `edmrn/app.py`, lines 2510–2513. Replace:

```python
                except requests.RequestException as e:
                    self.root.after(0, lambda: ErrorDialog(self, "API Error", f"Failed to search: {str(e)[:100]}"))
                except Exception as e:
                    self.root.after(0, lambda: ErrorDialog(self, "Error", f"Search failed: {str(e)[:100]}"))
```

with:

```python
                except ApiError as e:
                    message = str(e.failure)
                    self.root.after(0, lambda: ErrorDialog(self, "API Error", message))
                except Exception as e:
                    logger.exception(f"Nearest system search failed: {e}")
                    self.root.after(0, lambda: ErrorDialog(self, "Error", "Unexpected error talking to Spansh."))
```

`message` is bound inside the `except` block on purpose. The old lambdas referenced `e` after
the `except` block had ended, where Python 3 has already deleted it, so the old error dialogs
raised `NameError` instead of appearing.

**E7.4** `edmrn/app.py`, line 2523. Replace:

```python
            distance = data.get('distance', 0)
```

with:

```python
            distance = (system or {}).get('distance', 0)
```

- [ ] **Step 4: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `127 passed`. This task adds 9 insertions and 16 deletions to `app.py`; `file`
reports CRLF.

- [ ] **Step 5: Docs.** Add a row to the hook-point table:

```markdown
| `edmrn/app.py` (Nearest) | Import of `ApiError`, `nearest_system`; `_find_nearest_system_by_coordinates` asks `edmrn.ext.api.nearest_system` and shows the A2 sentence; `_show_nearest_results` reads the distance from `system` | The distance was read from the top level and always showed 0; failures showed raw exception text, and the error dialogs raised `NameError` (API hardening spec A11) | yes |
```

- [ ] **Step 6: Commit**

```bash
git add edmrn/app.py tests/test_nearest.py docs/architecture/overview.md
git commit -m "fix(nearest): distance from the system record, readable failures" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 8: Autocomplete and update check

**Files:**
- Modify: `edmrn/ext/api.py` (append `ServiceCooldown`)
- Create: `tests/test_autocomplete.py`, `tests/test_updater.py`
- Modify (upstream):
  - `edmrn/system_autocomplete.py`;
  - `edmrn/app.py` (`_get_system_suggestions`);
  - `edmrn/custom_route.py` (`_get_suggestions`);
  - `edmrn/system_info_section.py` (one line);
  - `edmrn/updater.py`.
- Modify: `docs/architecture/overview.md`

**Interfaces:**
- Consumes (Task 1): `call`, `ApiError`, `ApiFailure`, `TRANSIENT_KINDS`,
  `failure_from_response`.
- Produces:
  - `ServiceCooldown(seconds=60.0, clock=time.monotonic)` with `.active(service) -> bool` and
    `.note(failure: ApiFailure) -> None`. Only transient kinds start a cooldown.
  - `SystemAutocompleter.cooldown: ServiceCooldown`.
  - `EDMRN_App._get_system_suggestions(query, callback)` delivers `callback(results)` through
    `root.after(0, …)`.
  - `SimpleUpdateChecker.last_error: str | None`, the reason of the last failed check.

- [ ] **Step 1: Write the failing tests** (`tests/test_autocomplete.py`, `tests/test_updater.py`)

```python
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
```

```python
"""Update check (A13): one attempt through edmrn.ext.api, honest failure message. No network."""
from types import SimpleNamespace

import pytest

import edmrn.updater as updater
from edmrn.ext.api import ApiError, ApiFailure
from fakes import FakeResponse, Script

RELEASES = "https://api.github.com/repos/NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer/releases/latest"


def checker(monkeypatch, *items):
    fake = Script(*items)
    monkeypatch.setattr(updater, "call", fake)
    return updater.SimpleUpdateChecker("3.3.0"), fake


def test_single_attempt_to_the_upstream_repository(monkeypatch):
    c, fake = checker(monkeypatch, FakeResponse(200, {"tag_name": "v3.4.0", "html_url": "https://x"}))
    assert c.check_for_updates() is True and c.last_error is None
    args, kwargs = fake.calls[0]
    assert args == ("GET", RELEASES) and kwargs["service"] == "GitHub" and kwargs["retries"] == 0


@pytest.mark.parametrize("item,reason", [
    (ApiError(ApiFailure("GitHub", "offline")), "No connection to GitHub."),
    (ApiError(ApiFailure("GitHub", "timeout", None, "10 s")), "GitHub did not answer within 10 s."),
    (FakeResponse(403, {"message": "API rate limit exceeded"}), "GitHub rejected the request (HTTP 403)."),
])
def test_failure_reason_is_kept(monkeypatch, item, reason):
    c, _ = checker(monkeypatch, item)
    assert c.check_for_updates() is False and c.last_error == reason


def manual(monkeypatch, *items):
    shown = []
    monkeypatch.setattr(updater, "InfoDialog", lambda app, title, text: shown.append((title, text)))
    manager = updater.UpdateManager.__new__(updater.UpdateManager)
    manager.app = SimpleNamespace(root=None)
    manager.update_checker, _ = checker(monkeypatch, *items)
    manager.manual_check()
    return shown


def test_failed_manual_check_says_so(monkeypatch):
    assert manual(monkeypatch, ApiError(ApiFailure("GitHub", "offline"))) == [
        ("Update Check", "Could not check for updates: No connection to GitHub.")]


def test_up_to_date_manual_check(monkeypatch):
    [(title, text)] = manual(monkeypatch, FakeResponse(200, {"tag_name": "v3.3.0", "html_url": "https://x"}))
    assert title == "Update Check" and "already up to date" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_autocomplete.py tests/test_updater.py -v`
Expected: collection error, `ImportError: cannot import name 'ServiceCooldown' from
'edmrn.ext.api'`, which stops the run. Run `tests/test_updater.py` alone as well; it fails with
`AttributeError: <module 'edmrn.updater' ...> has no attribute 'call'`.

- [ ] **Step 3: Append to `edmrn/ext/api.py`** (at the end, two blank lines before)

```python
class ServiceCooldown:
    """Skips a service for `seconds` after a transient failure (spec A12: autocomplete)."""

    def __init__(self, seconds: float = 60.0, clock=time.monotonic) -> None:
        self.seconds = seconds
        self.clock = clock
        self._until = {}

    def active(self, service: str) -> bool:
        until = self._until.get(service)
        return until is not None and self.clock() < until

    def note(self, failure: ApiFailure) -> None:
        if failure.kind in TRANSIENT_KINDS:
            self._until[failure.service] = self.clock() + self.seconds
```

- [ ] **Step 4: Edit `edmrn/system_autocomplete.py`** (CRLF; exactly these blocks)

**E8.1** `edmrn/system_autocomplete.py`, line 1. Replace:

```python
import requests
```

with:

```python
from edmrn.ext.api import ApiError, ServiceCooldown, call
```

**E8.2** `edmrn/system_autocomplete.py`, line 16. Replace:

```python
        self.cache_ttl = 3600
```

with:

```python
        self.cache_ttl = 3600
        self.cooldown = ServiceCooldown(60.0)
```

**E8.3** `edmrn/system_autocomplete.py`, lines 44–45. Replace:

```python
    def _fetch_from_edsm(self, query: str, max_results: int) -> List[str]:
        try:
```

with:

```python
    def _fetch_from_edsm(self, query: str, max_results: int) -> List[str]:
        if self.cooldown.active("EDSM"):
            return []
        try:
```

**E8.4** `edmrn/system_autocomplete.py`, lines 55–60. Replace:

```python
            response = requests.get(
                self.edsm_api_url,
                params=params,
                headers={'User-Agent': "EDMRN_AutoComplete/1.0"},
                timeout=5
            )
```

with:

```python
            response = call("GET", self.edsm_api_url, service="EDSM", params=params, timeout=5, retries=0)
```

**E8.5** `edmrn/system_autocomplete.py`, lines 77–78. Replace:

```python
        except Exception as e:
            logger.debug(f"EDSM API error for '{query}': {str(e)}")
```

with:

```python
        except ApiError as e:
            self.cooldown.note(e.failure)
            logger.debug(f"EDSM autocomplete failed for '{query}': {e.failure}")
            return []
        except Exception as e:
            logger.debug(f"EDSM API error for '{query}': {str(e)}")
```

**E8.6** `edmrn/system_autocomplete.py`, lines 81–82. Replace:

```python
    def _fetch_from_spansh(self, query: str, max_results: int) -> List[str]:
        try:
```

with:

```python
    def _fetch_from_spansh(self, query: str, max_results: int) -> List[str]:
        if self.cooldown.active("Spansh"):
            return []
        try:
```

**E8.7** `edmrn/system_autocomplete.py`, lines 93–101. Replace:

```python
            response = requests.post(
                self.spansh_api_url,
                json=payload,
                headers={
                    'User-Agent': "EDMRN_AutoComplete/1.0",
                    'Content-Type': 'application/json'
                },
                timeout=5
            )
```

with:

```python
            response = call("POST", self.spansh_api_url, service="Spansh", json=payload, timeout=5, retries=0)
```

**E8.8** `edmrn/system_autocomplete.py`, lines 118–119. Replace:

```python
        except Exception as e:
            logger.debug(f"Spansh API error for '{query}': {str(e)}")
```

with:

```python
        except ApiError as e:
            self.cooldown.note(e.failure)
            logger.debug(f"Spansh autocomplete failed for '{query}': {e.failure}")
            return []
        except Exception as e:
            logger.debug(f"Spansh API error for '{query}': {str(e)}")
```

- [ ] **Step 5: Edit `edmrn/app.py`, `edmrn/custom_route.py`, `edmrn/system_info_section.py`**

**E8.9** `edmrn/app.py`, line 2732. Replace:

```python
            callback,
```

with:

```python
            lambda results: self.root.after(0, lambda: callback(results)),
```

**E8.10** `edmrn/custom_route.py`, lines 671–680. Replace:

```python
    def _get_suggestions(self, query: str, callback: Callable):
        def fetch():
            try:
                from edmrn.system_autocomplete import SystemAutocompleter
                completer = SystemAutocompleter()
                suggestions = completer.get_suggestions(query, max_results=8)
                callback(suggestions)
            except Exception:
                callback([])
        threading.Thread(target=fetch, daemon=True).start()
```

with:

```python
    def _get_suggestions(self, query: str, callback: Callable):
        self.app._get_system_suggestions(query, callback)
```

**E8.11** `edmrn/system_info_section.py`, line 267. Replace:

```python
        app = getattr(self.parent, 'app', None)
```

with:

```python
        app = self.app
```

`SystemInfoSection` looked for `app` on its parent tab frame, which never has one. It therefore
built a second autocompleter, with its own cache, that delivered results on a worker thread.
`self.app` is the app passed in by `_create_system_info_tab`. Tk is needed to exercise this line,
so Task 9's smoke start covers it.

- [ ] **Step 6: Edit `edmrn/updater.py`** (CRLF; exactly these blocks)

**E8.12** `edmrn/updater.py`, line 1. Replace:

```python
import requests
```

with:

```python
from edmrn.ext.api import ApiError, call, failure_from_response
```

**E8.13** `edmrn/updater.py`, lines 17–25. Replace:

```python
        self.release_url = None
    def check_for_updates(self):
        try:
            logger.info("Checking for updates from GitHub...")
            response = requests.get(
                self.latest_release_url,
                headers={'Accept': 'application/vnd.github.v3+json'},
                timeout=10
            )
```

with:

```python
        self.release_url = None
        self.last_error = None
    def check_for_updates(self):
        self.last_error = None
        try:
            logger.info("Checking for updates from GitHub...")
            response = call("GET", self.latest_release_url, service="GitHub", timeout=10, retries=0)
```

**E8.14** `edmrn/updater.py`, lines 40–50. Replace:

```python
                logger.warning(f"GitHub API error: {response.status_code}")
                self.update_available = False
        except requests.exceptions.Timeout:
            logger.warning("GitHub API timeout")
            self.update_available = False
        except requests.exceptions.ConnectionError:
            logger.warning("GitHub API connection error")
            self.update_available = False
        except Exception as e:
            logger.error(f"Update check error: {e}")
            self.update_available = False
```

with:

```python
                logger.warning(f"GitHub API error: {response.status_code}")
                self.last_error = str(failure_from_response(response, "GitHub"))
                self.update_available = False
        except ApiError as e:
            logger.warning(f"Update check failed: {e.failure}")
            self.last_error = str(e.failure)
            self.update_available = False
        except Exception as e:
            logger.error(f"Update check error: {e}")
            self.last_error = "Unexpected error talking to GitHub."
            self.update_available = False
```

**E8.15** `edmrn/updater.py`, lines 240–242. Replace:

```python
            if update_available:
                self.update_checker.show_update_dialog(self.app.root)
            else:
```

with:

```python
            if update_available:
                self.update_checker.show_update_dialog(self.app.root)
            elif self.update_checker.last_error:
                InfoDialog(self.app, "Update Check", f"Could not check for updates: {self.update_checker.last_error}")
            else:
```

The repository URL stays upstream's (A13).

- [ ] **Step 7: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `139 passed`.

Then run `git diff --stat edmrn/`. This task should change:
- `system_autocomplete.py`: 16 insertions, 16 deletions;
- `updater.py`: 11, 11;
- `system_info_section.py`: 1, 1;
- `custom_route.py`: 1, 9;
- `app.py`: 1, 1.

`file` reports CRLF for all five.

- [ ] **Step 8: Docs.** Add three rows to the hook-point table:

```markdown
| `edmrn/system_autocomplete.py` | Requests go through `edmrn.ext.api.call` without retries; a `ServiceCooldown` skips a service for 60 s after a transient failure | During an outage every keystroke waited up to 5 s + 5 s (API hardening spec A12) | yes |
| `edmrn/app.py` (suggestions), `edmrn/custom_route.py` (`_get_suggestions`), `edmrn/system_info_section.py` (one line) | `_get_system_suggestions` hands results to the widget via `root.after`; Custom Route and System Info use the app's single autocompleter | Results reached widgets on worker threads; Custom Route built a new autocompleter, with an empty cache, per keystroke, and System Info a second one (A12) | yes |
| `edmrn/updater.py` | The release check goes through `edmrn.ext.api.call` with one attempt; `last_error` keeps the reason; a failed manual check says `Could not check for updates: <reason>` | A failed manual check claimed the app was up to date (A13) | yes |
```

- [ ] **Step 9: Commit**

```bash
git add edmrn/ext/api.py edmrn/system_autocomplete.py edmrn/app.py edmrn/custom_route.py edmrn/system_info_section.py edmrn/updater.py tests/test_autocomplete.py tests/test_updater.py docs/architecture/overview.md
git commit -m "feat(autocomplete, updater): cooldown per service, shared cache, Tk-thread delivery, honest update failures" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

### Task 9: Guard test, binding rule, manual live check

**Files:**
- Create: `tests/test_http_path.py`
- Modify: `docs/architecture/patterns.md`, `docs/architecture/overview.md`, `docs/how-to/run.md`

**Interfaces:**
- Consumes: everything above. Upstream code is not modified in this task.

- [ ] **Step 1: Write the guard test** (`tests/test_http_path.py`)

```python
"""Binding rule: every live external request goes through edmrn/ext/api.py (spec A1)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "edmrn"
# Dead upstream copies that nothing in the UI reaches (spec "Not in scope").
DEAD = {"journal_handler.py", "galaxy_handler.py"}
DIRECT_HTTP = re.compile(r"\brequests\.(get|post|put|delete|head|patch|request|Session)\s*\(|\burlopen\s*\(")


def test_no_module_but_api_sends_http_itself():
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        if path.name in DEAD or path == ROOT / "ext" / "api.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DIRECT_HTTP.search(line):
                offenders.append(f"{path.relative_to(ROOT.parent)}:{number}: {line.strip()}")
    assert offenders == []
```

- [ ] **Step 2: Prove it bites.** Temporarily add the line `requests.get("https://example.org")`
  to the end of `edmrn/ext/spansh.py`, then run
  `.venv/Scripts/python.exe -m pytest tests/test_http_path.py -q`.
  - Expected: 1 failed, naming `edmrn\ext\spansh.py` (or `edmrn/ext/spansh.py`).
  - Remove the line again. `git diff edmrn/ext/spansh.py` must be empty.

- [ ] **Step 3: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `140 passed`.

- [ ] **Step 4: Binding rule.** Append to `docs/architecture/patterns.md`:

```markdown
## All external HTTP goes through `edmrn/ext/api.py`

Rule (API hardening spec A1–A3):
- `edmrn.ext.api.call` is the only code that sends a request. It sets the one User-Agent and an
  explicit timeout, and retries 502/503/504, timeouts and connection errors.
- Query values travel only as `params` or a `json` body, never formatted into a URL.
- A path segment taken from service data is validated (an `int` id64) or quoted (a job id).
- Failures become one `ApiFailure` sentence. "Not found" stays distinct from "service failed":
  `lookup_system` returns not found only when every service answered.
- Code that shows a result in Tk receives it through `root.after`.
- Tests inject `call`/`transport`, `sleep` and `clock`; no test uses the network.
- `tests/test_http_path.py` fails when any module other than `api.py` calls `requests` or
  `urlopen` directly. The dead copies `galaxy_handler.py` and `journal_handler.py` are exempt.

Defect it prevents (`../knowledge/upstream-baseline.md`, "External APIs"):
- Each call site had its own User-Agent, timeout and error handling, and only Neutron retried.
- EDSM URLs interpolated raw system names, so `&`, `#` and `+` broke System Info.
- One 502 aborted Galaxy jobs that were still running.
- Raw HTML and exception text reached the UI.
- A Spansh outage looked like "System Not Found".
```

- [ ] **Step 5: Invariant.** In `docs/architecture/overview.md`, section "Invariants", add:

```markdown
- Every live external HTTP request goes through `edmrn/ext/api.py` (API hardening spec A1; rule
  and guard test in `patterns.md`).
```

- [ ] **Step 6: Manual live check.** Append to `docs/how-to/run.md`:

````markdown
## Manual live check of the external APIs

API hardening spec A14. Tests never use the network, so this check is the only proof that Spansh,
EDSM and EDAstro still answer in the shapes `edmrn/ext/` expects. Run it after changing
`edmrn/ext/api.py`, `spansh.py` or `system_info.py`, and after an upstream merge that touches a
call site.

The test system is `Blae Phoe XT-A e1459` (768.375, -565.90625, 14325.34375). Spansh does not know
it and EDSM does (`../../../knowledge/bulk-data.md`, "Spansh API").

Save this script outside the repository, for example as `$H/live_check.py`:

```python
import os, sys
sys.path.insert(0, os.getcwd())
from types import SimpleNamespace
from edmrn.ext.api import lookup_system, nearest_known_system
from edmrn.ext.system_info import fetch_system_data
from edmrn.custom_route import CustomRouteManager
from edmrn.neutron import NeutronRouter
from edmrn.galaxy_plotter import GalaxyPlotter

BLAE = "Blae Phoe XT-A e1459"
for name in (BLAE, "Sol", "Xqzzy Nonsense 404"):
    r = lookup_system(name)
    print("lookup", name, "->", r.coords, r.source, r.message)
print("nearest 0,0,0 ->", nearest_known_system(0, 0, 0))
mgr = CustomRouteManager(SimpleNamespace(journal_monitor=None))
print("custom add", BLAE, "->", mgr.add_system(BLAE), mgr.systems[-1] if mgr.systems else mgr.lookup_errors)
print("custom add nonsense ->", mgr.add_system("Xqzzy Nonsense 404"), mgr.lookup_errors)
sol = fetch_system_data("Sol")
print("system info Sol ->", sol.get("error"), sol.get("name"), len(sol.get("bodies", [])), "bodies", sol.get("notes"))
print("system info nonsense ->", fetch_system_data("Xqzzy Nonsense 404"))
n = NeutronRouter().calculate_route(BLAE, "Sol", 50.0, progress_callback=lambda m: print("  neutron:", m))
print("neutron ->", n.get("success"), n.get("error"), n.get("note"), len(n.get("waypoints", [])), "waypoints")
g = GalaxyPlotter().plot_route(BLAE, "Sol", ship_build="", range_ly=50.0, progress_callback=lambda m: print("  galaxy:", m.replace("\n", " | ")))
print("galaxy ->", g.get("error"), g.get("note"), len(g.get("result", {}).get("system_jumps", [])), "jumps")
os._exit(0)
```

Run it from the repository root with an isolated home (Git Bash):

```
H="$(mktemp -d)"
# save the script above as "$H/live_check.py", then:
HOME="$H" USERPROFILE="$H" APPDATA="$H" LOCALAPPDATA="$H" timeout 900 .venv/Scripts/python.exe -I -u "$H/live_check.py"
```

Expected (2026-10-11; waypoint and jump counts vary with Spansh's data):
- `lookup Blae Phoe XT-A e1459 -> (768.375, -565.90625, 14325.34375) EDSM None`.
- `lookup Sol -> (0.0, 0.0, 0.0) Spansh None`.
- The nonsense name gives
  `None None System 'Xqzzy Nonsense 404' was not found at Spansh or EDSM. Check the spelling.`
- `nearest 0,0,0 -> ('Sol', 0.0)`.
- Custom Route adds Blae Phoe with the EDSM coordinates; the nonsense name gives `False` and the
  same sentence.
- System Info for Sol: no error and about 40 bodies. For the nonsense name:
  `{'error': "System 'Xqzzy Nonsense 404' is not known to EDSM."}`.
- Neutron and Galaxy each succeed with the note
  `Start system 'Blae Phoe XT-A e1459' is not known to Spansh yet - plotted from nearest known
  system 'Blae Phoe SS-B d751' (10.6 ly away).`.

A line that reads `... is overloaded (HTTP 502). Try again in a minute.` or `... did not answer
within ...` is Spansh under load, not a defect: rerun later. Any `Traceback`, any HTML in a
message, or a nonsense name reported as anything but "not found" is a defect.

Then start the app isolated (section "Start it isolated" above) and check its output and the log
under `$H/EDMRN_Route_Data/logs/` for `Traceback`.

If someone can look at the window, also check by hand:
- **Neutron and Galaxy:** plot from `Blae Phoe XT-A e1459` to `Sol`; both show the substitution
  note. While Galaxy runs, its button is disabled.
- **Typed unknown start:** Neutron from `Xqzzy Nonsense 404` shows
  `Spansh rejected the request: Could not find starting system`.
- **Custom Route:** add `Blae Phoe XT-A e1459`.
- **System Info:** `Sol` loads; the nonsense name shows `System '…' is not known to EDSM.`
- **Nearest:** `0,0,0` offers `Sol` at `0.00 LY`.
````

- [ ] **Step 7: Run the live check and the smoke start.**
  - Run the script exactly as documented, then the isolated start:

    ```bash
    H="$(mktemp -d)"
    HOME="$H" USERPROFILE="$H" APPDATA="$H" LOCALAPPDATA="$H" timeout 25 .venv/Scripts/python.exe -u main.py > "$H/smoke.log" 2>&1; echo "exit $?"
    grep -iE "traceback" "$H/smoke.log" "$H"/EDMRN_Route_Data/logs/*.log
    ```

  - Expected: `exit 124`, and `grep` prints nothing.
  - Report the live output verbatim in the task report. Say honestly whether anyone looked at the
    window; if nobody did, say that the GUI checks were not done.
  - Do not edit `docs/handover/HANDOVER.md`; the orchestrator writes it after the branch review.

- [ ] **Step 8: Commit**

```bash
git add tests/test_http_path.py docs/architecture/patterns.md docs/architecture/overview.md docs/how-to/run.md
git commit -m "docs(api): binding HTTP rule with guard test; manual live check" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016pAbHYEXvYT1Q3mcpjvXFa"
```

---

## Spec coverage

| Requirement | Task |
|---|---|
| A1 one HTTP path: User-Agent, params/json only, timeout, retries 2 s/5 s on 502/503/504, timeout and connection error, never on 4xx | 1 (`call`), 3–8 (sites), 9 (guard) |
| A2 readable one-line failures; raw HTML and exception text only in the log | 1 (`ApiFailure`), every site task |
| A3 not found distinct from service failure | 2 (`Lookup`), 5, 6 |
| A4 `lookup_system` | 2 |
| A5 start fallback (journal coordinates, `lookup_system`, re-plot once with a note; other 400s verbatim) | 3 (`submit_route_job`), 4 (Galaxy journal coordinates); Neutron journal coordinates exist since `23903c2` |
| A6 wall-clock polling 180 s / 300 s, every 1–3 s, 3 transient tolerated, website hint | 3 (ruling 1), 4 |
| A7 Galaxy: reason visible, button, Tk reads on the main thread | 4 |
| A8 Neutron: typed start, A6 replaces 20 attempts, `calculation_done` on Tk | 3 |
| A9 Custom Route messages and stop rule | 5 (ruling 4) |
| A10 System Info: encoded name, unknown system, partial notes, list-shaped factions, no crash log | 6 |
| A11 Nearest: distance from `system`, A1/A2 | 7 |
| A12 Autocomplete: 60 s cooldown, shared autocompleter, Tk-thread delivery, no retries | 8 |
| A13 Update check: one attempt, honest message, upstream URL | 8 |
| A14 no network in tests; documented manual check | all tests; 9 |
