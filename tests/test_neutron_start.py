"""optimize_start: skip waypoints a supercharged first jump reaches (ship range spec J5)."""
import copy
import math

import pytest

from edmrn.ext.neutron_start import optimize_start

EOL_PROU = {"result": {"system_jumps": [
    {"system": "Eol Prou WN-T d3-2412", "x": -9269.75, "y": -995.8125, "z": 19737.09375, "distance_jumped": 0, "jumps": 0, "neutron_star": True},
    {"system": "Eol Prou WN-T d3-304", "x": -9344.21875, "y": -994.09375, "z": 19738.40625, "distance_jumped": 74.5001441902967, "jumps": 1, "neutron_star": True},
    {"system": "Colonia", "x": -9530.5, "y": -910.28125, "z": 19808.125, "distance_jumped": 215.83777092848, "jumps": 1, "neutron_star": False}]}}
GRIA = {"result": {"system_jumps": [
    {"system": "Gria Eaec IO-X d2-406", "x": -2567.03125, "y": -100.90625, "z": 15857.40625, "distance_jumped": 0, "jumps": 0, "neutron_star": True},
    {"system": "Gria Eaec IO-X d2-3574", "x": -2618.03125, "y": -100.0625, "z": 15875.625, "distance_jumped": 54.1630387406855, "jumps": 1, "neutron_star": True},
    {"system": "Boeths FH-S d5-3517", "x": -2988.46875, "y": -84.75, "z": 16084.6875, "distance_jumped": 425.635457837749, "jumps": 1, "neutron_star": True},
    {"system": "Boeths KO-O d7-4279", "x": -3381.75, "y": -166.9375, "z": 16238.34375, "distance_jumped": 430.157145612943, "jumps": 1, "neutron_star": True}]}}


def names(data):
    return [e["system"] for e in data["result"]["system_jumps"]]


def test_spansh_neutron_flag_skips_the_hop():
    original = copy.deepcopy(EOL_PROU)
    out, note = optimize_start(EOL_PROU, 75.0, None, 4)
    assert names(out) == ["Eol Prou WN-T d3-2412", "Colonia"]
    last = out["result"]["system_jumps"][1]
    assert last["jumps"] == 1
    assert last["distance_jumped"] == pytest.approx(283.4636, abs=1e-3)
    assert note == "Supercharged start (×4): skipped 1 waypoint, first jump 283.5 LY"
    assert EOL_PROU == original


def test_no_skip_beyond_reach():
    out, note = optimize_start(GRIA, 75.0, None, 6)
    assert names(out) == names(GRIA) and note is None
    out, note = optimize_start(GRIA, 80.0, None, 6)
    assert names(out) == ["Gria Eaec IO-X d2-406", "Boeths FH-S d5-3517", "Boeths KO-O d7-4279"]


def test_journal_boost_wins_and_white_dwarf_counts():
    data = copy.deepcopy(EOL_PROU)
    data["result"]["system_jumps"][0]["neutron_star"] = False
    assert names(optimize_start(data, 75.0, None, 4)[0]) == names(data)
    assert names(optimize_start(data, 75.0, 1.5, 4)[0]) == names(data)
    out, note = optimize_start(data, 75.0, 6.0, 4)
    assert len(names(out)) == 2 and "(×6)" in note


def test_furthest_reachable_waypoint_wins():
    data = {"result": {"system_jumps": [
        {"system": f"S{x}", "x": float(x), "y": 0.0, "z": 0.0, "distance_jumped": 0, "jumps": 0, "neutron_star": False}
        for x in (0, 30, 150, 290, 600)]}}
    out, note = optimize_start(data, 75.0, 4.0, 4)
    assert names(out) == ["S0", "S290", "S600"]
    assert "skipped 2 waypoints" in note


def _entry(**kw):
    e = {"system": "A", "x": 0.0, "y": 0.0, "z": 0.0, "distance_jumped": 0, "jumps": 0, "neutron_star": True}
    e.update(kw)
    return e


@pytest.mark.parametrize("data", [
    None, {}, {"result": {}},
    {"result": {"system_jumps": [_entry(), _entry(x=10.0)]}},
    {"result": {"system_jumps": [_entry(), _entry(x=10.0), {"system": "C", "y": 0.0, "z": 0.0}]}},
    {"result": {"system_jumps": [_entry(), _entry(x=10.0), _entry(x="1")]}},
    {"result": {"system_jumps": [_entry(), _entry(x=10.0), _entry(x=True)]}},
])
def test_malformed_results_pass_through(data):
    out, note = optimize_start(data, 75.0, None, 4)
    assert out is data and note is None


@pytest.mark.parametrize("rng", [0, float("nan")])
def test_bad_range_passes_through(rng):
    out, note = optimize_start(GRIA, rng, None, 4)
    assert out is GRIA and note is None
