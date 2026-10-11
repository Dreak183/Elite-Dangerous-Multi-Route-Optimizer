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
