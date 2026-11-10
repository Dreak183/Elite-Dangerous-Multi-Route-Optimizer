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


def _get(url, service, params=None, call=call, timeout=10, **options):
    """JSON body of a 200 answer; ApiError for anything else. `options` go to `call`."""
    response = call("GET", url, service=service, params=params, timeout=timeout, **options)
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
                            params={"systemName": system_name}, retries=0)
            except ApiError as e:
                data["notes"].append(f"{label} could not be loaded: {e.failure}")
                body = None
            data[key] = _list_part(body, key)
        data["gmp"] = ""
        id64 = data.get("id64")
        if isinstance(id64, int) and not isinstance(id64, bool):
            try:
                data["gmp"] = _gec_text(_get(EDASTRO_GEC_URL.format(id64=id64), "EDAstro", call=call, timeout=15,
                                              retries=0))
            except ApiError as e:
                if e.failure.status != 404:  # EDAstro has no entry for most systems
                    data["notes"].append(f"Galactic POI data could not be loaded: {e.failure}")
        data["url"] = _edsm_page_url(system_name, data.get("id"))
        return data
    except Exception:
        logger.exception(f"System Info for {system_name!r} failed")
        return {"error": str(ApiFailure("EDSM", "unexpected"))}
