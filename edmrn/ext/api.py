"""The one HTTP path for every live external request (API hardening spec A1-A4).

Spansh answers bad input with HTTP 400 and {"error": "<reason>"}, and an overloaded server with an
nginx 502/503/504 HTML page or a timeout (../../../knowledge/bulk-data.md, "Spansh API"). EDSM's
system API answers an unknown system with [] (../../../knowledge/edsm-api.md).
"""
from __future__ import annotations

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
