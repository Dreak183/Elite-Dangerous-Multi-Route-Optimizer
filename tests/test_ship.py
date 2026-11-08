import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from edmrn.ext import ship as ship_module
from edmrn.ext.fsd import drive_from_loadout, jump_range
from edmrn.ext.ship import (ShipState, ShipTracker, apply_event, apply_status, cold_start,
                            install, start_boost)
from edmrn.fuel_tracker import FuelTracker
from edmrn.journal import JournalMonitor
from journal_helpers import append_events, event, journal_file
from test_fsd import MANDALAY_BOOSTED

MAIN_SHIP = 1 << 24
UNKNOWN_DRIVE = {**MANDALAY_BOOSTED, "Modules": [
    {"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_size9_class9", "On": True}]}


def loaded(*events) -> ShipState:
    state = ShipState()
    for e in events:
        state = apply_event(state, e)
    return state


def test_loadout_sets_drive_and_max():
    state = loaded(MANDALAY_BOOSTED)
    assert state.drive.boost == 10.5
    assert state.max_jump_range == 95.444145
    assert state.laden == pytest.approx(87.1401, abs=1e-4)
    assert state.current is None


def test_unrecognised_drive_keeps_max():
    state = loaded(UNKNOWN_DRIVE)
    assert state.drive is None
    assert state.max_jump_range == 95.444145
    assert state.laden is None


def test_status_updates_fuel_and_cargo():
    state = loaded(MANDALAY_BOOSTED)
    state = apply_status(state, {"Flags": MAIN_SHIP,
                                 "Fuel": {"FuelMain": 20.0, "FuelReservoir": 0.4}, "Cargo": 10.0})
    assert state.fuel_main == 20.0 and state.cargo == 10.0
    assert state.current == pytest.approx(87.7033, abs=1e-4)
    assert state.laden == pytest.approx(jump_range(state.drive, 10, 32), abs=1e-9)


GOOD_FUEL = {"FuelMain": 20.0}


@pytest.mark.parametrize("status", [
    {"Flags": 0, "Fuel": GOOD_FUEL, "Cargo": 5.0},
    {"Fuel": GOOD_FUEL, "Cargo": 5.0},
    {"Flags": True, "Fuel": GOOD_FUEL},
    {"Flags": MAIN_SHIP, "Fuel": "20"},
    {"Flags": MAIN_SHIP, "Fuel": None},
    {"Flags": MAIN_SHIP, "Cargo": -1},
    {"Flags": MAIN_SHIP, "Cargo": 20000},
    {"Flags": MAIN_SHIP, "Cargo": "5"},
    {"Flags": MAIN_SHIP, "Cargo": True},
    {"Flags": MAIN_SHIP, "Cargo": float("nan")},
    {"Flags": MAIN_SHIP, "Cargo": float("inf")},
    {"Flags": MAIN_SHIP, "Cargo": float("-inf")},
    {"Flags": MAIN_SHIP, "Fuel": {"FuelMain": float("nan")}},
    {"Flags": MAIN_SHIP, "Fuel": {"FuelMain": float("inf")}},
    {"Flags": MAIN_SHIP, "Fuel": {"FuelMain": -1.0}},
    {"Flags": MAIN_SHIP, "Fuel": {"FuelMain": True}},
    "not a dict", None,
])
def test_status_outside_main_ship_is_ignored(status):
    state = loaded(MANDALAY_BOOSTED)
    assert apply_status(state, status) is state


def test_cargo_event_is_a_fallback():
    assert loaded(event("Cargo", Vessel="Ship", Count=7)).cargo == 7
    assert loaded(event("Cargo", Vessel="SRV", Count=7)).cargo is None
    for bad in (-1, 20000, "7", True, float("nan"), float("inf")):
        assert loaded(event("Cargo", Vessel="Ship", Count=bad)).cargo is None


def test_nonfinite_values_never_reach_the_range_model():
    state = ShipState(drive=drive_from_loadout(MANDALAY_BOOSTED), cargo=float("nan"),
                      fuel_main=float("nan"))
    assert state.laden == pytest.approx(87.1401 + 0, abs=1e-4)
    assert state.current is None


def test_star_class_from_startjump_and_scan():
    state = loaded(event("StartJump", JumpType="Hyperspace", StarSystem="B", StarClass="N"),
                   event("FSDJump", StarSystem="B"))
    assert state.system == "B" and state.star_class == "N"
    state = apply_event(state, event("StartJump", JumpType="Hyperspace", StarSystem="B", StarClass="N"))
    state = apply_event(state, event("FSDJump", StarSystem="C"))
    assert state.star_class is None
    same = apply_event(state, event("Scan", StarSystem="C", DistanceFromArrivalLS=1200, StarType="K"))
    assert same is state
    assert apply_event(state, event("Scan", StarSystem="Z", DistanceFromArrivalLS=0.0,
                                    StarType="K")) is state
    scanned = apply_event(state, event("Scan", StarSystem="C", DistanceFromArrivalLS=0.0, StarType="DA"))
    assert scanned.star_class == "DA"
    # a non-hyperspace StartJump is not remembered
    assert apply_event(state, event("StartJump", JumpType="Supercruise", StarSystem="C",
                                    StarClass="N")) is state


def test_supercharge_lifecycle():
    state = loaded(event("StartJump", JumpType="Hyperspace", StarSystem="B", StarClass="N"),
                   event("FSDJump", StarSystem="B"), event("JetConeBoost", BoostValue=6.0))
    assert state.supercharge == 6.0
    for bad in (0, -1, "6", True, float("nan")):
        assert apply_event(state, event("JetConeBoost", BoostValue=bad)) is state
    here = apply_event(state, event("Location", StarSystem="B"))
    assert here.supercharge == 6.0 and here.star_class == "N"
    away = apply_event(state, event("Location", StarSystem="Q"))
    assert away.supercharge is None and away.star_class is None and away.system == "Q"
    jumped = apply_event(state, event("FSDJump", StarSystem="C"))
    assert jumped.supercharge is None
    carrier = apply_event(state, event("CarrierJump", StarSystem="C"))
    assert carrier.supercharge is None and carrier.star_class is None and carrier.system == "C"


def test_commander_switch_resets():
    state = loaded(event("LoadGame", Commander="A"), MANDALAY_BOOSTED,
                   event("LoadGame", Commander="B"))
    assert replace(state, journal_at=None) == ShipState(commander="B")


def test_journal_at_follows_journal_events_only():
    tracker = ShipTracker()
    tracker.feed(event("FSDJump", ts="2026-10-11T12:00:05Z", StarSystem="A"))
    tracker.feed_status({"Flags": MAIN_SHIP, "Fuel": {"FuelMain": 5.0}})
    assert tracker.state.journal_at == "2026-10-11T12:00:05Z"


def test_adopt_keeps_live_status_and_newer_journal():
    live = ShipTracker()
    live.feed_status({"Flags": MAIN_SHIP, "Fuel": {"FuelMain": 20.0}})
    replay = ShipTracker()
    replay.feed(event("Loadout", ts="2026-10-11T12:00:00Z", **{k: v for k, v in MANDALAY_BOOSTED.items()
                                                               if k not in ("event", "timestamp")}))
    replay.feed_status({"Flags": MAIN_SHIP, "Fuel": {"FuelMain": 3.0}, "Cargo": 4.0})
    seen = []
    live.subscribe(seen.append)

    assert live.adopt(replay.state) is True
    assert live.state.fuel_main == 20.0
    assert live.state.cargo == 4.0
    assert live.state.drive is not None
    assert len(seen) == 1

    older = replay.state
    live.feed(event("FSDJump", ts="2026-10-11T13:00:00Z", StarSystem="X"))
    assert live.adopt(older) is False
    assert live.state.system == "X"


def test_observer_failure_does_not_stop_feed():
    tracker = ShipTracker()
    got = []
    tracker.subscribe(lambda s: 1 / 0)
    tracker.subscribe(got.append)
    tracker.feed(event("FSDJump", StarSystem="A"))
    assert got and got[0].system == "A"


def test_cold_start_finds_the_loadout(tmp_path):
    append_events(journal_file(tmp_path, "2026-10-10T120000"), event("FSDJump", StarSystem="Old"))
    newest = journal_file(tmp_path, "2026-10-11T120000")
    append_events(newest, event("LoadGame", Commander="A"), MANDALAY_BOOSTED,
                  event("FSDJump", ts="2026-10-11T12:30:00Z", StarSystem="Sol"))
    tracker = ShipTracker()

    state = cold_start(str(tmp_path), tracker)

    assert state.drive is not None and state.system == "Sol"
    assert tracker.state.drive is not None
    assert cold_start(str(tmp_path / "missing"), ShipTracker()) is None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cold_start(str(empty), ShipTracker()) is None
    assert cold_start(None, ShipTracker()) is None


def app_with(state: ShipState):
    tracker = ShipTracker()
    tracker.adopt(state)
    return SimpleNamespace(ship_tracker=tracker)


def test_start_boost_only_for_current_system():
    drive = drive_from_loadout(MANDALAY_BOOSTED)
    mk2 = drive_from_loadout({**MANDALAY_BOOSTED, "MaxJumpRange": 88.20607, "UnladenMass": 1256.649902,
                              "FuelCapacity": {"Main": 128.0, "Reserve": 1.0},
                              "Modules": [{"Slot": "FrameShiftDrive",
                                           "Item": "int_hyperdrive_overcharge_size8_class5_overchargebooster_mkii",
                                           "On": True}]})
    assert mk2 is not None and mk2.mk2
    base = dict(system="Sol", drive=drive, journal_at="2026-10-11T12:00:00Z")

    charged = app_with(ShipState(**base, supercharge=6.0))
    assert start_boost(charged, "sol ") == 6.0
    assert start_boost(charged, "Achenar") is None

    assert start_boost(app_with(ShipState(**{**base, "drive": mk2}, star_class="N")), "Sol") == 6.0
    assert start_boost(app_with(ShipState(**base, star_class="N")), "Sol") == 4.0
    assert start_boost(app_with(ShipState(**base, star_class="DA")), "Sol") == 1.5
    assert start_boost(app_with(ShipState(**{**base, "drive": mk2}, star_class="DA")), "Sol") == 3.0
    assert start_boost(app_with(ShipState(**base, star_class="K")), "Sol") == 1.0
    assert start_boost(app_with(ShipState(**base)), "Sol") is None   # journal knows nothing
    assert start_boost(SimpleNamespace(), "Sol") is None
    assert start_boost(charged, None) is None


class ImmediateRoot:
    def after(self, ms, fn, *args):
        fn(*args)


class Var:
    def __init__(self):
        self.calls = []

    def set(self, value):
        self.calls.append(value)


def test_install_registers_listeners_and_skips_upstream_fill(monkeypatch, tmp_path):
    monkeypatch.setattr(JournalMonitor, "event_listeners", [])
    monkeypatch.setattr(FuelTracker, "status_listeners", [])
    monkeypatch.setattr(ship_module, "journal_dir_for", lambda app: str(tmp_path))
    app = SimpleNamespace(root=ImmediateRoot(), config=SimpleNamespace(journal_path=""),
                          journal_monitor=None)

    tracker = install(app)

    assert app.ship_tracker is tracker
    assert install(app) is tracker
    assert JournalMonitor.event_listeners == [tracker.feed]
    assert FuelTracker.status_listeners == [tracker.feed_status]

    hook = sys.excepthook
    import edmrn.app as app_module
    sys.excepthook = hook

    def fake_self(**extra):
        return SimpleNamespace(root=ImmediateRoot(), jump_range=Var(),
                               _save_jump_range=lambda: None, **extra)

    loadout = {"event": "Loadout", "MaxJumpRange": 50.0, "FuelCapacity": 0}
    with_tracker = fake_self(ship_tracker=tracker)
    app_module.EDMRN_App._journal_callback(with_tracker, None, loadout)
    assert with_tracker.jump_range.calls == []

    without = fake_self()
    app_module.EDMRN_App._journal_callback(without, None, loadout)
    assert without.jump_range.calls == ["50.0"]


def test_huge_int_cargo_does_not_drop_valid_fuel():
    state = loaded(MANDALAY_BOOSTED)
    new = apply_status(state, {"Flags": MAIN_SHIP, "Fuel": {"FuelMain": 20.0}, "Cargo": 10**400})
    assert new.fuel_main == 20.0 and new.cargo is None
    assert apply_status(state, {"Flags": MAIN_SHIP, "Fuel": {"FuelMain": 10**400}}) is state
    assert loaded(event("JetConeBoost", BoostValue=10**400)).supercharge is None


def _loadout(ts, max_range, **extra):
    return {**MANDALAY_BOOSTED, "timestamp": ts, "MaxJumpRange": max_range, **extra}


def test_adopt_takes_the_drive_when_live_events_are_newer_but_no_loadout_seen():
    live = ShipTracker()
    live.feed(event("FSDJump", ts="2026-10-11T13:00:00Z", StarSystem="X"))
    replay = loaded(event("LoadGame", Commander="A"), MANDALAY_BOOSTED)
    replay = replace(replay, journal_at="2026-10-11T12:00:00Z", system="Old")
    seen = []
    live.subscribe(seen.append)

    assert live.adopt(replay) is True

    assert live.state.drive is not None and live.state.max_jump_range == 95.444145
    assert live.state.system == "X" and live.state.commander is None
    assert live.state.journal_at == "2026-10-11T13:00:00Z"
    assert len(seen) == 1
    assert live.adopt(replay) is False   # nothing left to take


def test_adopt_equal_timestamp_is_refused_for_everything_but_the_missing_drive():
    live = ShipTracker()
    live.feed(event("FSDJump", ts="2026-10-11T12:00:00Z", StarSystem="X"))
    replay = replace(loaded(MANDALAY_BOOSTED), journal_at="2026-10-11T12:00:00Z", system="Old",
                     cargo=9.0)
    assert live.adopt(replay) is True
    assert live.state.system == "X" and live.state.cargo is None
    # a live Loadout seen: the replay is refused outright
    live.feed(_loadout("2026-10-11T12:00:01Z", 50.0))
    other = replace(loaded(UNKNOWN_DRIVE), journal_at="2026-10-11T12:00:00Z")
    before = live.state
    assert live.adopt(other) is False
    assert live.state is before


class StoringRoot:
    def __init__(self):
        self.callbacks = []

    def after(self, ms, fn, *args):
        self.callbacks.append(lambda: fn(*args))


def test_tk_callback_uses_the_tracker_state_not_the_notified_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(JournalMonitor, "event_listeners", [])
    monkeypatch.setattr(FuelTracker, "status_listeners", [])
    monkeypatch.setattr(ship_module, "journal_dir_for", lambda app: str(tmp_path))
    seen = []

    class Binding:
        def on_state(self, state):
            seen.append(state)

    panel = SimpleNamespace(binding_for=lambda app: Binding())
    monkeypatch.setitem(sys.modules, "edmrn.ext.ship_panel", panel)
    root = StoringRoot()
    app = SimpleNamespace(root=root, config=SimpleNamespace(journal_path=""), journal_monitor=None)
    tracker = install(app)

    tracker.feed(event("FSDJump", StarSystem="A"))
    tracker.feed(event("FSDJump", StarSystem="B"))
    assert len(root.callbacks) == 2
    for callback in reversed(root.callbacks):
        callback()

    assert [s.system for s in seen] == ["B", "B"]


def test_tk_callback_failure_is_logged_not_raised(monkeypatch, tmp_path):
    monkeypatch.setattr(JournalMonitor, "event_listeners", [])
    monkeypatch.setattr(FuelTracker, "status_listeners", [])
    monkeypatch.setattr(ship_module, "journal_dir_for", lambda app: str(tmp_path))

    def broken(app):
        raise RuntimeError("no binding")

    monkeypatch.setitem(sys.modules, "edmrn.ext.ship_panel", SimpleNamespace(binding_for=broken))
    root = StoringRoot()
    tracker = install(SimpleNamespace(root=root, config=SimpleNamespace(journal_path=""),
                                      journal_monitor=None))
    tracker.feed(event("FSDJump", StarSystem="A"))
    root.callbacks[0]()   # must not raise


def test_replay_newest_is_newest_first_and_honours_max_files(tmp_path):
    import os
    from edmrn.ext.journal_replay import replay_newest
    older = journal_file(tmp_path, "2026-10-10T120000")
    newer = journal_file(tmp_path, "2026-10-11T120000")
    append_events(older, _loadout("2026-10-10T12:00:00Z", 40.0))
    append_events(newer, _loadout("2026-10-11T12:00:00Z", 50.0))
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))

    def wanted(state):
        return state.max_jump_range is not None

    assert replay_newest(str(tmp_path), ShipTracker, wanted).state.max_jump_range == 50.0
    # the newest file has no match: the older one is reached only with max_files >= 2
    only_older = lambda state: state.max_jump_range == 40.0
    assert replay_newest(str(tmp_path), ShipTracker, only_older, max_files=1) is None
    assert replay_newest(str(tmp_path), ShipTracker, only_older, max_files=2).state.max_jump_range == 40.0
    assert replay_newest(str(tmp_path / "nope"), ShipTracker, wanted) is None


@pytest.mark.parametrize("bad", [1e308, 10.5, 1.0, 0.5])
def test_jet_cone_boost_outside_one_to_ten_is_ignored(bad):
    assert loaded(event("JetConeBoost", BoostValue=bad)).supercharge is None
    assert loaded(event("JetConeBoost", BoostValue=10.0)).supercharge == 10.0


def test_absurd_loadout_max_jump_range_is_not_stored():
    state = loaded(event("Loadout", **{**MANDALAY_BOOSTED, "MaxJumpRange": 1e300}))
    assert state.drive is None and state.max_jump_range is None
