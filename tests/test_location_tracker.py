from edmrn.ext.location import LocationState, LocationTracker, apply_event
from journal_helpers import event


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
