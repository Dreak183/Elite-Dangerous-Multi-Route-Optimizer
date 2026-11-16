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


def _exc_text(exc, log_url) -> str:
    return type(exc).__name__ if log_url is not None else repr(exc)


def _timeout_seconds(timeout) -> float:
    """The read timeout of a `timeout` argument (a positive finite number or a (connect, read)
    pair of them); ValueError otherwise. Spec A1: every request has an explicit timeout, so None
    (which `requests` reads as "wait forever") is refused."""
    parts = timeout if isinstance(timeout, tuple) and len(timeout) == 2 else (timeout,)
    if not all(_is_number(p) and p > 0 for p in parts):
        raise ValueError(f"timeout must be a positive number of seconds or a (connect, read) pair, got {timeout!r}")
    return float(parts[-1])


def call(method, url, *, service, params=None, json=None, timeout, retries=2, delays=(2.0, 5.0),
         on_retry=None, transport=requests.request, sleep=time.sleep, log_url=None):
    """Send one request; retry 502/503/504, timeouts and connection errors.

    Responses below 500 are returned; the caller decides what a 4xx means. Everything else ends in
    ApiError. on_retry(attempt, failure) is called before each retry. `log_url` replaces `url` in
    log lines when the URL itself is a secret (a Discord webhook, crew sync spec B1 S5a); log lines
    then carry only the exception class, because `requests` errors embed the full URL.
    `timeout` is seconds or a (connect, read) pair; anything else raises ValueError.
    """
    wait = _timeout_seconds(timeout)
    headers = {"User-Agent": USER_AGENT}
    shown = url if log_url is None else log_url
    attempt = 0
    while True:
        try:
            response = transport(method, url, params=params, json=json, headers=headers, timeout=timeout)
        except requests.exceptions.Timeout as exc:
            logger.debug(f"{service} {method} {shown}: {_exc_text(exc, log_url)}")
            failure = ApiFailure(service, "timeout", None, f"{wait:g} s")
        except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError) as exc:
            logger.debug(f"{service} {method} {shown}: {_exc_text(exc, log_url)}")
            failure = ApiFailure(service, "offline")
        except Exception as exc:
            if log_url is None:
                logger.exception(f"{service} {method} {url} failed unexpectedly")
                raise ApiError(ApiFailure(service, "unexpected"))
            logger.error(f"{service} {method} {shown} failed unexpectedly: {type(exc).__name__}")
            # `from None`: the original exception carries the secret URL and must not chain.
            raise ApiError(ApiFailure(service, "unexpected")) from None
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


# --- Coordinates and nearest system (spec A3, A4) ---------------------------

SPANSH_SEARCH_URL = "https://spansh.co.uk/api/systems/search"
SPANSH_NEAREST_URL = "https://spansh.co.uk/api/nearest"
EDSM_SYSTEM_URL = "https://www.edsm.net/api-v1/system"
LOOKUP_SERVICES = ("Spansh", "EDSM")


def _is_number(value) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int too large for a float
        return False


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
                    json={"filters": {"name": {"value": name}},
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


def spansh_coords(name: str, *, call=call) -> Optional[tuple]:
    """Coordinates if Spansh knows the system by exactly this name (case-insensitive), else None.

    Service failures raise ApiError.
    """
    name = (name or "").strip()
    if not name:
        return None
    return _spansh_coords(name, call)


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
