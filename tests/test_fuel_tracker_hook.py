import json
import threading
from types import SimpleNamespace

import pytest

from edmrn.fuel_tracker import FuelTracker
from journal_helpers import wait_for


@pytest.fixture(autouse=True)
def listeners(monkeypatch):
    fresh = []
    monkeypatch.setattr(FuelTracker, "status_listeners", fresh)
    return fresh


def test_status_dicts_reach_listeners_and_a_raising_listener_stops_nothing(tmp_path, listeners):
    status_file = tmp_path / "Status.json"
    status_file.write_text(json.dumps({"timestamp": "2026-10-12T10:00:00Z", "event": "Status",
                                       "Flags": 1 << 24, "Fuel": {"FuelMain": 16.0, "FuelReservoir": 0.5}}),
                           encoding="utf-8")
    tracker = FuelTracker(SimpleNamespace())
    tracker.status_file = str(status_file)
    seen = []

    def boom(status):
        raise RuntimeError("listener failed")

    listeners.extend([boom, seen.append])
    tracker.is_tracking = True
    thread = threading.Thread(target=tracker._tracking_loop, daemon=True)
    thread.start()
    try:
        assert wait_for(lambda: seen and tracker.fuel_capacity == 16.0)
    finally:
        tracker.is_tracking = False
        thread.join(3)
    assert seen[0]["Fuel"]["FuelMain"] == 16.0


def test_the_hook_is_a_class_attribute_shared_by_every_tracker():
    assert FuelTracker.status_listeners is FuelTracker(SimpleNamespace()).status_listeners


def test_listeners_run_before_upstreams_fuel_handling(tmp_path, listeners):
    status_file = tmp_path / "Status.json"
    status_file.write_text(json.dumps({"timestamp": "2026-10-12T10:00:00Z", "event": "Status",
                                       "Flags": 1 << 24, "Fuel": {"FuelMain": 16.0, "FuelReservoir": 0.5}}),
                           encoding="utf-8")
    tracker = FuelTracker(SimpleNamespace())
    tracker.status_file = str(status_file)
    capacity_at_call = []
    listeners.append(lambda status: capacity_at_call.append(tracker.fuel_capacity))
    tracker.is_tracking = True
    thread = threading.Thread(target=tracker._tracking_loop, daemon=True)
    thread.start()
    try:
        assert wait_for(lambda: capacity_at_call and tracker.fuel_capacity == 16.0)
    finally:
        tracker.is_tracking = False
        thread.join(3)
    assert capacity_at_call[0] is None


def test_status_listener_error_logs_the_listener_name_and_traceback(listeners, caplog):
    def broken_status_listener(status):
        raise RuntimeError("listener failed")

    listeners.extend([broken_status_listener, lambda s: None])
    tracker = FuelTracker(SimpleNamespace())
    with caplog.at_level("DEBUG"):
        tracker._dispatch_status({"Flags": 0})

    [record] = [r for r in caplog.records if r.exc_info]
    assert "broken_status_listener" in record.getMessage()
    assert record.exc_info[0] is RuntimeError
