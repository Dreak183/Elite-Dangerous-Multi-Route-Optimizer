import logging

import pytest

from edmrn.ext import fsd
from edmrn.ext.fsd import drive_from_loadout, jump_range, supercharge_multiplier

ASP = {"event": "Loadout", "Ship": "asp", "UnladenMass": 404.0, "FuelCapacity": {"Main": 32.0, "Reserve": 0.63}, "MaxJumpRange": 14.104039, "Modules": [{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_size5_class1", "On": True}]}
CONDA_T9M = {"event": "Loadout", "Ship": "type9_military", "UnladenMass": 1474.0, "FuelCapacity": {"Main": 64.0, "Reserve": 0.77}, "MaxJumpRange": 6.983821, "Modules": [{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_size6_class1", "On": True}]}
KRAIT_LIGHT = {"event": "Loadout", "Ship": "mediumtransport01", "UnladenMass": 528.900024, "FuelCapacity": {"Main": 32.0, "Reserve": 0.75}, "MaxJumpRange": 40.911518, "Modules": [{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_overcharge_size5_class5", "On": True, "Engineering": {"Modifiers": [{"Label": "FSDOptimalMass", "Value": 1894.099976, "OriginalValue": 1175.0, "LessIsGood": 0}]}}, {"Slot": "Slot02_Size5", "Item": "int_guardianfsdbooster_size5", "On": True}]}
CASPIAN = {"event": "Loadout", "Ship": "explorer_nx", "UnladenMass": 1256.649902, "FuelCapacity": {"Main": 128.0, "Reserve": 1.14}, "MaxJumpRange": 88.20607, "Modules": [{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_overcharge_size8_class5_overchargebooster_mkii", "On": True, "Engineering": {"Modifiers": [{"Label": "FSDOptimalMass", "Value": 7528.039551, "OriginalValue": 4670.0, "LessIsGood": 0}]}}, {"Slot": "Slot04_Size5", "Item": "int_guardianfsdbooster_size5", "On": True}]}
MANDALAY_BOOSTED = {"event": "Loadout", "Ship": "mandalay", "UnladenMass": 242.143997, "FuelCapacity": {"Main": 32.0, "Reserve": 0.5}, "MaxJumpRange": 95.444145, "Modules": [{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_overcharge_size5_class5", "On": True, "Engineering": {"Modifiers": [{"Label": "FSDOptimalMass", "Value": 1821.25, "OriginalValue": 1175.0, "LessIsGood": 0}]}}, {"Slot": "Slot02_Size5", "Item": "int_guardianfsdbooster_size5", "On": True}]}
MANDALAY_BOOSTER_OFF = {**MANDALAY_BOOSTED, "MaxJumpRange": 84.944153, "Modules": [MANDALAY_BOOSTED["Modules"][0], {"Slot": "Slot02_Size5", "Item": "int_guardianfsdbooster_size5", "On": False}]}

ALL = [ASP, CONDA_T9M, KRAIT_LIGHT, CASPIAN, MANDALAY_BOOSTED, MANDALAY_BOOSTER_OFF]


@pytest.fixture
def fsd_log(caplog):
    fsd._warned.clear()
    with caplog.at_level(logging.DEBUG, logger="EDMRN.Fsd"):
        yield caplog
    fsd._warned.clear()


@pytest.mark.parametrize("loadout", ALL)
def test_max_jump_range_is_reproduced(loadout):
    d = drive_from_loadout(loadout)
    assert jump_range(d, 0, d.max_fuel) == pytest.approx(loadout["MaxJumpRange"], abs=1e-6)


def test_booster_in_effect_is_read_from_max_jump_range():
    assert drive_from_loadout(MANDALAY_BOOSTED).boost == 10.5
    assert drive_from_loadout(MANDALAY_BOOSTER_OFF).boost == 0.0
    assert drive_from_loadout(KRAIT_LIGHT).boost == 0.0
    assert drive_from_loadout(CASPIAN).boost == 10.5
    assert drive_from_loadout(ASP).boost == 0.0


def test_laden_and_current_ranges():
    d = drive_from_loadout(MANDALAY_BOOSTED)
    assert jump_range(d, 0, 32.0) == pytest.approx(87.14010365, abs=1e-6)
    assert jump_range(d, 24, 32.0) == pytest.approx(80.97072743, abs=1e-6)
    assert jump_range(d, 10, 20.0) == pytest.approx(87.70333565, abs=1e-6)
    assert jump_range(d, 0, 2.6) == pytest.approx(75.19241882, abs=1e-6)
    assert jump_range(drive_from_loadout(CASPIAN), 0, 128.0) == pytest.approx(81.40436823, abs=1e-6)


def test_inputs_are_clamped():
    d = drive_from_loadout(MANDALAY_BOOSTED)
    assert jump_range(d, -5, 40.0) == jump_range(d, 0, 32.0)
    assert jump_range(d, 0, -1.0) == d.boost


def test_mk2_flag_and_multipliers():
    caspian = drive_from_loadout(CASPIAN)
    asp = drive_from_loadout(ASP)
    assert caspian.mk2 is True
    assert all(drive_from_loadout(x).mk2 is False for x in ALL if x is not CASPIAN)
    assert supercharge_multiplier(caspian, "N") == 6.0
    assert supercharge_multiplier(caspian, "DA") == 3.0
    assert supercharge_multiplier(asp, "N") == 4.0
    assert supercharge_multiplier(asp, "DC") == 1.5
    assert supercharge_multiplier(asp, "D") == 1.5
    assert supercharge_multiplier(asp, "K") == 1.0
    assert supercharge_multiplier(None, "N") == 4.0
    assert supercharge_multiplier(asp, None) == 1.0


def test_engineered_max_fuel_replaces_the_table():
    loadout = {**ASP, "MaxJumpRange": (3.63 / 0.011) ** (1 / 2.45) * 560 / (404 + 3.63),
               "Modules": [{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_size5_class1", "On": True,
                            "Engineering": {"Modifiers": [{"Label": "MaxFuelPerJump", "Value": 3.63}]}}]}
    d = drive_from_loadout(loadout)
    assert d.max_fuel == 3.63
    assert jump_range(d, 0, d.max_fuel) == pytest.approx(loadout["MaxJumpRange"], abs=1e-6)


def test_unexplained_max_jump_range_assumes_switched_on_boosters(fsd_log):
    loadout = {**MANDALAY_BOOSTED, "MaxJumpRange": 120.0}
    assert drive_from_loadout(loadout).boost == 10.5
    assert len([r for r in fsd_log.records if r.levelno == logging.WARNING]) == 1
    assert drive_from_loadout(loadout).boost == 10.5
    assert len([r for r in fsd_log.records if r.levelno == logging.WARNING]) == 1


def _with(**changes):
    return {**ASP, **changes}


@pytest.mark.parametrize("loadout", [
    _with(Modules=[{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_size9_class9", "On": True}]),
    _with(Modules=[]),
    {k: v for k, v in ASP.items() if k != "UnladenMass"},
    _with(UnladenMass=0), _with(UnladenMass=True), _with(UnladenMass="404"),
    {k: v for k, v in ASP.items() if k != "FuelCapacity"},
    _with(FuelCapacity={"Main": 0}),
    _with(MaxJumpRange=0), _with(MaxJumpRange=None), _with(MaxJumpRange=float("nan")),
    _with(Modules="nope"), _with(Modules=None),
    None, [], "Loadout", 5,
])
def test_unknown_or_broken_loadouts(loadout):
    assert drive_from_loadout(loadout) is None


def test_string_modifier_value_is_ignored():
    loadout = _with(Modules=[{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_size5_class1", "On": True,
                              "Engineering": {"Modifiers": [{"Label": "FSDOptimalMass", "Value": "9999"}]}}])
    d = drive_from_loadout(loadout)
    assert d is not None
    assert jump_range(d, 0, d.max_fuel) == pytest.approx(ASP["MaxJumpRange"], abs=1e-6)
