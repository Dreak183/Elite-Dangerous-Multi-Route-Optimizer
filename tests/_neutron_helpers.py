"""Spansh result fixtures shared by the neutron tests (plain data, no pytest fixtures)."""

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
