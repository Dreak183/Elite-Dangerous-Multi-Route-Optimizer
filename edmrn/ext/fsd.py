"""Frame Shift Drive range model: jump range from a ``Loadout`` event, laden and at any fuel level.

Spec: ``docs/superpowers/specs/2026-11-08-ship-range-and-neutron-start-design.md`` (Range model).
Constants and formula: the hub's shared ``../knowledge/fsd-jump-range.md`` (relative to the repo root). Pure functions, no I/O.

The mass term is calibrated on the game's own ``MaxJumpRange`` so a small table error cannot bias
the ranges; the table only decides whether the Guardian booster was counted by the game.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from edmrn.logger import get_logger

logger = get_logger('Fsd')

MK2_ITEM = "int_hyperdrive_overcharge_size8_class5_overchargebooster_mkii"

_MATCH_TOLERANCE = 0.05  # LY

_POWER = {2: 2.00, 3: 2.15, 4: 2.30, 5: 2.45, 6: 2.60, 7: 2.75, 8: 2.90}

# Per size, classes 1..5 (E, D, C, B, A): (optmass, maxfuel).
_STANDARD = {
    2: [(48, 0.6), (54, 0.6), (60, 0.6), (75, 0.8), (90, 0.9)],
    3: [(80, 1.2), (90, 1.2), (100, 1.2), (125, 1.5), (150, 1.8)],
    4: [(280, 2.0), (315, 2.0), (350, 2.0), (438, 2.5), (525, 3.0)],
    5: [(560, 3.3), (630, 3.3), (700, 3.3), (875, 4.1), (1050, 5.0)],
    6: [(960, 5.3), (1080, 5.3), (1200, 5.3), (1500, 6.6), (1800, 8.0)],
    7: [(1440, 8.5), (1620, 8.5), (1800, 8.5), (2250, 10.6), (2700, 12.8)],
}
_STANDARD_MUL = [0.011, 0.010, 0.008, 0.010, 0.012]

# Supercharge-optimised drives; D, C and B share their values.
_SCO = {
    2: [(60, 0.6), (90, 0.9), (90, 0.9), (90, 0.9), (100, 1.0)],
    3: [(100, 1.2), (150, 1.8), (150, 1.8), (150, 1.8), (167, 1.9)],
    4: [(350, 2.0), (525, 3.0), (525, 3.0), (525, 3.0), (585, 3.2)],
    5: [(700, 3.3), (1050, 5.0), (1050, 5.0), (1050, 5.0), (1175, 5.2)],
    6: [(1200, 5.3), (1800, 8.0), (1800, 8.0), (1800, 8.0), (2000, 8.3)],
    7: [(1800, 8.5), (2700, 12.8), (2700, 12.8), (2700, 12.8), (3000, 13.1)],
    8: [(2800, 13.6), (4200, 20.4), (4200, 20.4), (4200, 20.4), (4670, 20.7)],
}
_SCO_MUL = [0.008, 0.012, 0.012, 0.012, 0.013]


def _build_table() -> dict[str, tuple[float, float, float, float]]:
    table: dict[str, tuple[float, float, float, float]] = {}
    for size, rows in _STANDARD.items():
        for i, (opt, fuel) in enumerate(rows):
            table[f"int_hyperdrive_size{size}_class{i + 1}"] = (
                float(opt), fuel, _STANDARD_MUL[i], _POWER[size])
    for size, rows in _SCO.items():
        for i, (opt, fuel) in enumerate(rows):
            table[f"int_hyperdrive_overcharge_size{size}_class{i + 1}"] = (
                float(opt), fuel, _SCO_MUL[i], _POWER[size])
    table[MK2_ITEM] = (4670.0, 6.8, 0.011, 2.5025)
    return table


# item -> (optmass, maxfuel, fuelmul, fuelpower)
FSD_TABLE: dict[str, tuple[float, float, float, float]] = _build_table()

BOOSTERS: dict[str, float] = {
    "int_guardianfsdbooster_size1": 4.0,
    "int_guardianfsdbooster_size2": 6.0,
    "int_guardianfsdbooster_size3": 7.75,
    "int_guardianfsdbooster_size4": 9.25,
    "int_guardianfsdbooster_size5": 10.5,
}

_warned: set[tuple] = set()
_WARNED_LIMIT = 256   # distinct warnings remembered; the set is dropped when it fills


MAX_RANGE = 1000.0   # LY; the longest real jump range is below 100, larger values are hostile


@dataclass(frozen=True)
class Drive:
    item: str
    unladen_mass: float
    fuel_capacity: float   # FuelCapacity.Main
    max_fuel: float        # min(table/engineered maxfuel, fuel_capacity)
    fuel_power: float
    mass_term: float       # K, calibrated on MaxJumpRange
    boost: float           # Guardian bonus in effect
    max_jump_range: float
    mk2: bool


def _num(value: Any) -> Optional[float]:
    """A finite positive number (bool rejected), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value > 0 else None


def _modifier(module: dict, label: str) -> Optional[float]:
    engineering = module.get("Engineering")
    if not isinstance(engineering, dict):
        return None
    modifiers = engineering.get("Modifiers")
    if not isinstance(modifiers, list):
        return None
    for mod in modifiers:
        if isinstance(mod, dict) and mod.get("Label") == label:
            value = _num(mod.get("Value"))
            if value is not None:
                return value
    return None


def drive_from_loadout(loadout: Any) -> Optional[Drive]:
    """Describe the fitted FSD of a ``Loadout`` event, or None when it cannot be modelled."""
    try:
        return _drive_from_loadout(loadout)
    except Exception as e:  # hostile data must never raise into the journal thread
        _warn_once(("failure", type(e).__name__, str(e)[:200]),
                   "Loadout not modelled: %r", e, exc_info=True)
        return None


def _drive_from_loadout(loadout: Any) -> Optional[Drive]:
    if not isinstance(loadout, dict):
        return None
    modules = loadout.get("Modules")
    if not isinstance(modules, list):
        return None
    mass = _num(loadout.get("UnladenMass"))
    fuel_cap = loadout.get("FuelCapacity")
    capacity = _num(fuel_cap.get("Main")) if isinstance(fuel_cap, dict) else None
    max_range = _num(loadout.get("MaxJumpRange"))
    if mass is None or capacity is None or max_range is None or max_range > MAX_RANGE:
        return None

    fsd: Optional[dict] = None
    on_bonus = 0.0
    for module in modules:
        if not isinstance(module, dict):
            continue
        item = module.get("Item")
        if not isinstance(item, str):
            continue
        item = item.lower()
        if module.get("Slot") == "FrameShiftDrive" and item in FSD_TABLE:
            fsd = module
        elif item in BOOSTERS and module.get("On") is not False:
            on_bonus += BOOSTERS[item]
    if fsd is None:
        return None
    item = fsd["Item"].lower()
    optmass, maxfuel, fuelmul, fuelpower = FSD_TABLE[item]
    optmass = _modifier(fsd, "FSDOptimalMass") or optmass
    maxfuel = _modifier(fsd, "MaxFuelPerJump") or maxfuel
    mf = min(maxfuel, capacity)

    predicted = (mf / fuelmul) ** (1 / fuelpower) * optmass / (mass + mf)
    boost = 0.0
    if on_bonus > 0:
        err_without = abs(predicted - max_range)
        err_with = abs(predicted + on_bonus - max_range)
        if min(err_without, err_with) > _MATCH_TOLERANCE:
            boost = on_bonus
            _warn_unexplained(item, max_range, predicted, boosters=True)
        elif err_with < err_without:
            boost = on_bonus
    elif abs(predicted - max_range) > _MATCH_TOLERANCE:
        _warn_unexplained(item, max_range, predicted, boosters=False)

    base = max_range - boost
    if base <= 0:
        return None
    return Drive(item=item, unladen_mass=mass, fuel_capacity=capacity, max_fuel=mf,
                 fuel_power=fuelpower, mass_term=base * (mass + mf), boost=boost,
                 max_jump_range=max_range, mk2=item == MK2_ITEM)


def _warn_once(key: tuple, message: str, *args: Any, exc_info: bool = False) -> None:
    if key in _warned:
        return
    if len(_warned) >= _WARNED_LIMIT:
        _warned.clear()
    _warned.add(key)
    logger.warning(message, *args, exc_info=exc_info)


def _warn_unexplained(item: str, max_range: float, predicted: float, boosters: bool) -> None:
    """MaxJumpRange matches neither candidate; one warning per item, range and booster situation."""
    outcome = "assuming switched-on boosters" if boosters else "assuming no booster"
    _warn_once(("range", item, round(max_range, 1), boosters),
               "MaxJumpRange %.3f of %s is not explained by the table (%.3f); %s",
               max_range, item, predicted, outcome)


def jump_range(drive: Drive, cargo: float, fuel: float) -> float:
    """Jump range in LY with ``cargo`` t and ``fuel`` t in the main tank (reservoir ignored)."""
    cargo = max(0.0, cargo)
    fuel = min(max(0.0, fuel), drive.fuel_capacity)
    ratio = min(fuel, drive.max_fuel) / drive.max_fuel
    return (drive.mass_term * ratio ** (1 / drive.fuel_power)
            / (drive.unladen_mass + cargo + fuel) + drive.boost)


def supercharge_multiplier(drive: Optional[Drive], star_class: Optional[str]) -> float:
    """Range multiplier of the next jump after a jet cone at ``star_class``; 1.0 when none."""
    mk2 = drive is not None and drive.mk2
    if not isinstance(star_class, str):
        return 1.0
    if star_class == "N":
        return 6.0 if mk2 else 4.0
    if star_class.startswith("D"):
        return 3.0 if mk2 else 1.5
    return 1.0
