"""Supercharged start: drop waypoints that a supercharged first jump already reaches.

Spansh's neutron plotter never supercharges at the start system, even when it reports the start as
a neutron star; it first hops to a neighbouring neutron star. If the commander is supercharged at
the start (journal boost, or the start is a neutron star), that hop is wasted whenever a later
waypoint lies within boost * range of the start (ship range spec J5).
"""
import copy
import math
from typing import Any, Optional

from edmrn.logger import get_logger

logger = get_logger('NeutronStart')


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _point(entry) -> Optional[tuple]:
    if not isinstance(entry, dict):
        return None
    p = (entry.get("x"), entry.get("y"), entry.get("z"))
    return p if all(_num(v) for v in p) else None


def _boost(start_boost, multiplier, first) -> Optional[float]:
    if _num(start_boost):
        return float(start_boost) if start_boost > 1 else None   # the journal knows: no fallback
    if first.get("neutron_star") is True and _num(multiplier) and multiplier > 1:
        return float(multiplier)
    return None


def optimize_start(data: Any, range_ly: float, start_boost: Optional[float],
                   multiplier: float) -> tuple[Any, Optional[str]]:
    """Return (data, note); data is a modified deep copy, or the input itself when nothing changes."""
    try:
        if not _num(range_ly) or range_ly <= 0:
            return data, None
        jumps = data["result"]["system_jumps"]
        if not isinstance(jumps, list) or len(jumps) < 3:
            return data, None
        origin = _point(jumps[0])
        boost = _boost(start_boost, multiplier, jumps[0])
        if origin is None or boost is None:
            return data, None
        reach = boost * range_ly
        for k in range(len(jumps) - 1, 1, -1):
            target = _point(jumps[k])
            if target is None:
                continue
            dist = math.dist(origin, target)
            if dist <= reach:
                out = copy.deepcopy(data)
                chain = out["result"]["system_jumps"]
                chain[k]["jumps"] = 1
                chain[k]["distance_jumped"] = dist
                del chain[1:k]
                n = k - 1
                note = (f"Supercharged start (×{boost:g}): skipped {n} waypoint{'s' if n != 1 else ''}, "
                        f"first jump {dist:.1f} LY")
                return out, note
        return data, None
    except Exception:
        logger.warning("Supercharged start skipped: unexpected route data", exc_info=True)
        return data, None
