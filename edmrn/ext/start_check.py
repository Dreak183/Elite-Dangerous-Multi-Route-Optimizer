"""Spansh check for route-plotter fields filled from the current location.

Implements docs/superpowers/specs/2026-11-17-location-fill-design.md, requirement L4: a field
holds the current system if Spansh knows it, else Spansh's nearest known system, with a one-line
hint saying which.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from edmrn.ext.api import ApiError, ApiFailure, call, nearest_system, spansh_coords
from edmrn.logger import get_logger

logger = get_logger('StartCheck')


@dataclass(frozen=True)
class StartCheck:
    name: str   # what the field should hold
    hint: str
    known: bool


def _known(system: str) -> StartCheck:
    return StartCheck(system, f"✓ '{system}' is known to Spansh.", True)


def _unknown(system: str, reason: str) -> StartCheck:
    return StartCheck(system, f"⚠ '{system}' is not known to Spansh, and {reason}.", False)


def failure_result(system: str, failure: ApiFailure) -> StartCheck:
    return StartCheck(system, f"⚠ Could not check '{system}' at Spansh: {failure}", False)


def check_plot_system(system: str, coords: Optional[tuple], *, call=call) -> StartCheck:
    """Decide the field value and hint for `system`. Never raises."""
    try:
        if spansh_coords(system, call=call):
            return _known(system)
        if coords is None:
            return _unknown(system, "the journal has no coordinates for it")
        x, y, z = coords
        nearest = nearest_system(x, y, z, call=call)
        if nearest is None:
            return _unknown(system, "Spansh named no known system near it")
        if nearest["name"].strip().casefold() == system.strip().casefold():
            return _known(system)
        return StartCheck(
            nearest["name"],
            f"⚠ '{system}' is not known to Spansh - using the nearest known system "
            f"'{nearest['name']}' ({nearest['distance']:.1f} ly away).", False)
    except ApiError as e:
        return failure_result(system, e.failure)
    except Exception:
        logger.exception("Spansh check for %r failed unexpectedly", system)
        return failure_result(system, ApiFailure("Spansh", "unexpected"))
