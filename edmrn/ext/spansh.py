"""Spansh route API helpers: readable errors, retry of transient failures, nearest-start fallback.

Spansh answers invalid input with HTTP 400 and a JSON body {"error": "<reason>"}; an overloaded
server answers with an nginx 502/503/504 (HTML body) or times out
(../../../knowledge/bulk-data.md, section "Spansh API").
"""
import time
from typing import Callable, Optional, Tuple

import requests

TRANSIENT_STATUSES = (502, 503, 504)
NEAREST_URL = "https://spansh.co.uk/api/nearest"
USER_AGENT = "EDMRN 3.0"
UNKNOWN_START_ERROR = "could not find starting system"


def spansh_error(response) -> str:
    """Human-readable reason of a failed response. Never raises."""
    try:
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("error"), str) and body["error"]:
            return body["error"]
    except Exception:
        pass
    reason = getattr(response, "reason", "") or ""
    return f"HTTP {response.status_code} {reason}".strip()


def post_with_retry(url, params, headers, timeout, retries=2, delays=(2.0, 5.0), on_retry=None,
                    post=requests.post, sleep=time.sleep):
    """POST, retrying transient statuses, timeouts and connection errors.

    A 4xx is returned at once. If the last attempt is transient, its response is returned or its
    exception re-raised.
    """
    for attempt in range(retries + 1):
        last = attempt == retries
        try:
            response = post(url, params=params, headers=headers, timeout=timeout)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            if last:
                raise
            reason = f"{type(exc).__name__}"
        else:
            if response.status_code not in TRANSIENT_STATUSES or last:
                return response
            reason = f"HTTP {response.status_code}"
        if on_retry:
            on_retry(attempt + 1, reason)
        sleep(delays[min(attempt, len(delays) - 1)])


def is_unknown_start(response) -> bool:
    return (response.status_code == 400
            and spansh_error(response).strip().lower() == UNKNOWN_START_ERROR)


def nearest_known_system(x, y, z, get=requests.get, timeout=10) -> Optional[Tuple[str, float]]:
    """Closest system Spansh knows to the coordinates, as (name, distance in ly), else None."""
    try:
        response = get(NEAREST_URL, params={"x": x, "y": y, "z": z},
                       headers={"User-Agent": USER_AGENT}, timeout=timeout)
        if response.status_code != 200:
            return None
        system = response.json()["system"]
        return str(system["name"]), float(system["distance"])
    except Exception:
        return None


def _is_coords(value) -> bool:
    return (isinstance(value, (tuple, list)) and len(value) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value))


def submit_route_job(url, params, headers, timeout, start_coords=None, on_retry=None,
                     post=requests.post, get=requests.get, sleep=time.sleep):
    """Submit a route job; if the start is unknown to Spansh, retry from the nearest known system.

    Returns (response, note, used_from). note is None unless the start was substituted.
    """
    response = post_with_retry(url, params, headers, timeout, on_retry=on_retry,
                               post=post, sleep=sleep)
    original = params.get("from")
    if not (is_unknown_start(response) and _is_coords(start_coords)):
        return response, None, original
    nearest = nearest_known_system(*start_coords, get=get)
    if not nearest or str(nearest[0]).lower() == str(original).lower():
        return response, None, original
    name, distance = nearest
    retried = post_with_retry(url, {**params, "from": name}, headers, timeout,
                              on_retry=on_retry, post=post, sleep=sleep)
    note = (f"Start system '{original}' is not known to Spansh yet - plotted from nearest "
            f"known system '{name}' ({distance:.1f} ly away).")
    return retried, note, name
