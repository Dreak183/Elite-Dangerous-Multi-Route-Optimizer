import os
import time

from edmrn.ext.location import LocationState, LocationTracker, apply_event, cold_start
from journal_helpers import append_events, event, journal_file


def feed_all(*events):
    tracker = LocationTracker()
    for e in events:
        tracker.feed(e)
    return tracker.state


JUMP = event("FSDJump", "2026-10-11T12:00:00Z", StarSystem="Sol", SystemAddress=10477373803,
             StarPos=[0.0, 0.0, 0.0], Body="Sol", BodyID=0, BodyType="Star")


def test_unknown_state_is_all_none():
    assert LocationState() == LocationState(None, None, None, None, None, None, None, None, None, None, None)


def test_fsd_jump_sets_system_and_arrival_star():
    s = feed_all(JUMP)
    assert (s.system, s.system_address, s.coords) == ("Sol", 10477373803, (0.0, 0.0, 0.0))
    assert (s.body, s.body_id, s.body_type) == ("Sol", 0, "Star")
    assert s.station is None
    assert s.timestamp == "2026-10-11T12:00:00Z"


def test_location_docked_sets_station_not_station_body():
    s = feed_all(event("Location", StarSystem="Sol", SystemAddress=1, StarPos=[0, 0, 0],
                       Docked=True, StationName="Abraham Lincoln", MarketID=128016640,
                       StationType="Orbis", Body="Abraham Lincoln", BodyID=5, BodyType="Station"))
    assert (s.station, s.market_id, s.station_type) == ("Abraham Lincoln", 128016640, "Orbis")
    assert s.body is None


def test_location_in_space_has_no_station():
    s = feed_all(event("Location", StarSystem="Sol", SystemAddress=1, StarPos=[0, 0, 0], Docked=False))
    assert s.system == "Sol" and s.station is None


def test_carrier_jump_while_docked_moves_system_and_keeps_station():
    s = feed_all(JUMP,
                 event("Docked", StarSystem="Sol", SystemAddress=10477373803, StationName="X7Z-1TQ",
                       MarketID=3700000000, StationType="FleetCarrier"),
                 event("CarrierJump", "2026-10-11T12:20:00Z", StarSystem="Alpha Centauri",
                       SystemAddress=1, StarPos=[3.03, -0.09, 3.16], Docked=True,
                       StationName="X7Z-1TQ", MarketID=3700000000, StationType="FleetCarrier"))
    assert s.system == "Alpha Centauri"
    assert s.coords == (3.03, -0.09, 3.16)
    assert s.station == "X7Z-1TQ"


def test_jump_clears_station_and_body():
    s = feed_all(JUMP, event("ApproachBody", StarSystem="Sol", Body="Earth", BodyID=3),
                 event("FSDJump", StarSystem="Barnard's Star", SystemAddress=2, StarPos=[-3.0, 1.0, 5.0]))
    assert s.system == "Barnard's Star" and s.body is None and s.station is None


def test_docked_and_undocked():
    docked = event("Docked", StarSystem="Sol", SystemAddress=10477373803,
                   StationName="Abraham Lincoln", MarketID=1, StationType="Orbis")
    s = feed_all(JUMP, docked)
    assert s.station == "Abraham Lincoln" and s.coords == (0.0, 0.0, 0.0)
    s = feed_all(JUMP, docked, event("Undocked", StationName="Abraham Lincoln"))
    assert s.station is None and s.system == "Sol"


def test_docked_in_other_system_replaces_system_and_clears_coords():
    s = feed_all(JUMP, event("Docked", StarSystem="Lave", SystemAddress=99,
                             StationName="Lave Station", MarketID=2, StationType="Coriolis"))
    assert (s.system, s.system_address, s.coords) == ("Lave", 99, None)
    assert s.station == "Lave Station"


def test_body_events():
    s = feed_all(JUMP, event("ApproachBody", StarSystem="Sol", Body="Earth", BodyID=3))
    assert (s.body, s.body_id) == ("Earth", 3)
    s = feed_all(JUMP, event("Touchdown", StarSystem="Sol", Body="Moon", BodyID=4))
    assert s.body == "Moon"
    s = feed_all(JUMP, event("SupercruiseExit", StarSystem="Sol", Body="Mars", BodyID=6, BodyType="Planet"))
    assert (s.body, s.body_type) == ("Mars", "Planet")
    s = feed_all(JUMP, event("ApproachBody", Body="Earth", BodyID=3), event("LeaveBody", Body="Earth", BodyID=3))
    assert s.body is None and s.system == "Sol"


def test_supercruise_exit_at_station_keeps_body():
    s = feed_all(JUMP, event("ApproachBody", Body="Earth", BodyID=3),
                 event("SupercruiseExit", Body="Abraham Lincoln", BodyID=5, BodyType="Station"))
    assert s.body == "Earth"


def test_commander_switch_resets_location():
    s = feed_all(event("LoadGame", Commander="Alice"), JUMP, event("LoadGame", Commander="Bob"))
    assert s.commander == "Bob" and s.system is None


def test_same_commander_reload_keeps_location():
    s = feed_all(event("LoadGame", Commander="Alice"), JUMP, event("Commander", Name="Alice"),
                 event("LoadGame", Commander="Alice"))
    assert s.commander == "Alice" and s.system == "Sol"


def test_malformed_and_unknown_input_is_ignored():
    before = feed_all(JUMP)
    tracker = LocationTracker()
    tracker.feed(JUMP)
    for bad in (None, "text", 42, [], {}, {"event": "Music"}, {"event": "FSDJump"},
                {"event": "FSDJump", "StarSystem": 7}):
        tracker.feed(bad)
    assert tracker.state == before


def test_missing_fields_are_left_empty_not_invented():
    s = feed_all(event("FSDJump", StarSystem="Lave", StarPos=[1, "x", 3]))
    assert s.system == "Lave" and s.coords is None and s.system_address is None


def test_apply_event_returns_same_object_when_unchanged():
    s = apply_event(LocationState(), JUMP)
    assert apply_event(s, event("Music", MusicTrack="Exploration")) is s
    assert apply_event(s, dict(JUMP, timestamp="2026-10-11T13:00:00Z")) is s


def test_observers_called_only_on_change():
    tracker = LocationTracker()
    calls = []
    tracker.subscribe(calls.append)
    tracker.feed(JUMP)
    tracker.feed(JUMP)
    tracker.feed(event("Music"))
    assert [c.system for c in calls] == ["Sol"]


def test_raising_observer_does_not_block_others():
    tracker = LocationTracker()
    calls = []

    def broken(_state):
        raise RuntimeError("observer bug")

    tracker.subscribe(broken)
    tracker.subscribe(calls.append)
    tracker.feed(JUMP)
    assert len(calls) == 1


def _set_mtime(path, seconds_ago):
    t = time.time() - seconds_ago
    os.utime(path, (t, t))


def test_cold_start_reads_newest_file(tmp_path):
    older = journal_file(tmp_path, "2026-10-10T120000")
    newer = journal_file(tmp_path, "2026-10-11T120000")
    append_events(older, event("FSDJump", StarSystem="Old", StarPos=[0, 0, 0]))
    append_events(newer, event("Location", StarSystem="New", StarPos=[1, 1, 1], Docked=True,
                               StationName="Dock", MarketID=1, StationType="Coriolis"))
    _set_mtime(older, 120)
    tracker = LocationTracker()

    result = cold_start(str(tmp_path), tracker)

    assert result.system == "New" and tracker.state.station == "Dock"


def test_cold_start_skips_newest_file_without_location(tmp_path):
    older = journal_file(tmp_path, "2026-10-10T120000")
    newer = journal_file(tmp_path, "2026-10-11T120000")
    append_events(older, event("FSDJump", StarSystem="Previous", StarPos=[0, 0, 0]))
    append_events(newer, event("Fileheader", part=1))
    _set_mtime(older, 120)
    tracker = LocationTracker()

    cold_start(str(tmp_path), tracker)

    assert tracker.state.system == "Previous"


def test_cold_start_orders_by_mtime_not_by_name(tmp_path):
    horizons = tmp_path / "Journal.261011150000.01.log"
    odyssey = journal_file(tmp_path, "2026-10-10T120000")
    append_events(horizons, event("FSDJump", StarSystem="HorizonsNewest", StarPos=[0, 0, 0]))
    append_events(odyssey, event("FSDJump", StarSystem="OdysseyOlder", StarPos=[0, 0, 0]))
    _set_mtime(odyssey, 120)
    tracker = LocationTracker()

    cold_start(str(tmp_path), tracker)

    assert tracker.state.system == "HorizonsNewest"


def test_cold_start_without_files_or_folder(tmp_path):
    tracker = LocationTracker()
    assert cold_start(str(tmp_path), tracker) is None
    assert cold_start(str(tmp_path / "missing"), tracker) is None
    assert cold_start(None, tracker) is None
    assert tracker.state == LocationState()


def test_cold_start_tolerates_garbage_lines(tmp_path):
    path = journal_file(tmp_path)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("not json\n{\"event\": \"FSDJump\"\n")
    append_events(path, event("FSDJump", StarSystem="Survivor", StarPos=[0, 0, 0]))
    tracker = LocationTracker()
    cold_start(str(tmp_path), tracker)
    assert tracker.state.system == "Survivor"


def test_live_state_beats_older_cold_start(tmp_path):
    path = journal_file(tmp_path)
    append_events(path, event("FSDJump", "2026-10-11T12:00:00Z", StarSystem="Stale", StarPos=[0, 0, 0]))
    tracker = LocationTracker()
    tracker.feed(event("FSDJump", "2026-10-11T12:05:00Z", StarSystem="Live", StarPos=[1, 1, 1]))

    cold_start(str(tmp_path), tracker)

    assert tracker.state.system == "Live"


def test_adopt_notifies_and_reports():
    tracker = LocationTracker()
    calls = []
    tracker.subscribe(calls.append)
    newer = LocationState(system="A", timestamp="2026-10-11T12:00:00Z")
    assert tracker.adopt(newer) is True
    assert tracker.adopt(LocationState(system="B", timestamp="2026-10-11T11:00:00Z")) is False
    assert tracker.state.system == "A" and len(calls) == 1


def test_overflowing_star_pos_does_not_drop_the_event():
    s = feed_all(event("FSDJump", StarSystem="Huge", StarPos=[10**400, 0, 0]))
    assert s.system == "Huge" and s.coords is None


def test_event_without_timestamp_keeps_previous_timestamp():
    s = feed_all(JUMP, {"event": "Docked", "StarSystem": "Sol", "SystemAddress": 10477373803,
                        "StationName": "Dock", "MarketID": 1, "StationType": "Orbis"})
    assert s.station == "Dock" and s.timestamp == "2026-10-11T12:00:00Z"


def test_late_cold_start_does_not_overwrite_state_changed_by_timestampless_event():
    tracker = LocationTracker()
    tracker.feed(JUMP)
    tracker.feed({"event": "Undocked"})
    tracker.feed({"event": "ApproachBody", "Body": "Earth", "BodyID": 3})
    assert tracker.adopt(LocationState(system="Stale", timestamp="2026-10-11T11:00:00Z")) is False
    assert tracker.state.system == "Sol"


def test_notify_delivers_current_state_not_a_superseded_argument():
    tracker = LocationTracker()
    calls = []
    tracker.subscribe(calls.append)
    tracker.feed(JUMP)
    tracker.feed(event("FSDJump", "2026-10-11T12:10:00Z", StarSystem="Lave", StarPos=[1, 1, 1]))
    calls.clear()
    tracker._notify(LocationState(system="Superseded"))
    assert [c.system for c in calls] == ["Lave"]


def test_commander_event_switch_resets_location():
    s = feed_all(event("LoadGame", Commander="Alice"), JUMP, event("Commander", Name="Bob"))
    assert s.commander == "Bob" and s.system is None


def test_docked_without_system_address_keeps_system_and_coords():
    s = feed_all(JUMP, event("Docked", StarSystem="Sol", StationName="Dock", MarketID=1,
                             StationType="Orbis"))
    assert s.system == "Sol" and s.coords == (0.0, 0.0, 0.0)
    assert s.station == "Dock"


def test_cold_start_survives_file_vanishing_before_getmtime(tmp_path, monkeypatch):
    vanishing = journal_file(tmp_path, "2026-10-11T120000")
    survivor = journal_file(tmp_path, "2026-10-10T120000")
    append_events(vanishing, event("FSDJump", StarSystem="Gone", StarPos=[0, 0, 0]))
    append_events(survivor, event("FSDJump", StarSystem="Survivor", StarPos=[0, 0, 0]))
    real = os.path.getmtime

    def flaky(path):
        if os.fspath(path) == os.fspath(vanishing):
            raise FileNotFoundError(path)
        return real(path)

    monkeypatch.setattr("edmrn.ext.location.os.path.getmtime", flaky)
    tracker = LocationTracker()

    cold_start(str(tmp_path), tracker)

    assert tracker.state.system == "Survivor"


def test_cold_start_survives_hostile_lines(tmp_path):
    path = journal_file(tmp_path)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write('{"event": "x", "n": ' + "9" * 6000 + "}\n")
        f.write('{"event": "x", "n": ' + "[" * 200000 + "\n")
    append_events(path, event("FSDJump", StarSystem="Survivor", StarPos=[0, 0, 0]))
    tracker = LocationTracker()

    cold_start(str(tmp_path), tracker)

    assert tracker.state.system == "Survivor"


def test_supercruise_entry_clears_body_so_a_later_station_shows_no_ring():
    s = feed_all(
        event("FSDJump", StarSystem="Lave", SystemAddress=5, StarPos=[1, 2, 3]),
        event("SupercruiseExit", StarSystem="Lave", SystemAddress=5, Body="Lave A Ring",
              BodyID=7, BodyType="PlanetaryRing"),
        event("SupercruiseEntry", StarSystem="Lave", SystemAddress=5),
        event("SupercruiseExit", StarSystem="Lave", SystemAddress=5, Body="Lave Station",
              BodyID=9, BodyType="Station"),
        event("Docked", StarSystem="Lave", SystemAddress=5, StationName="Lave Station",
              MarketID=3, StationType="Coriolis"),
        event("Undocked", StationName="Lave Station"),
    )
    assert (s.body, s.body_id, s.body_type) == (None, None, None)
    assert s.station is None and s.system == "Lave"


def test_non_finite_star_pos_gives_no_coords_but_the_system_applies():
    import json
    for literal in ("NaN", "Infinity", "-Infinity"):
        line = ('{"timestamp": "2026-10-11T12:00:00Z", "event": "FSDJump", "StarSystem": "Lave",'
                ' "SystemAddress": 5, "StarPos": [1.0, %s, 3.0]}' % literal)
        s = feed_all(json.loads(line))
        assert s.system == "Lave" and s.coords is None


def test_cold_start_in_a_folder_with_glob_metacharacters(tmp_path):
    folder = tmp_path / "Saved [Games]"
    folder.mkdir()
    append_events(journal_file(folder), event("FSDJump", StarSystem="Bracket", StarPos=[0, 0, 0]))
    tracker = LocationTracker()

    assert cold_start(str(folder), tracker).system == "Bracket"
