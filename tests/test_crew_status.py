import base64
import gzip
import json

import pytest

from edmrn.ext.crew_status import IN_MAIN_SHIP, CrewStatus, ShipStatus, edsy_url
from journal_helpers import event

LOADOUT = event("Loadout", "2026-10-12T10:00:00Z", Ship="krait_light", ShipID=7, ShipName="Wanderer",
                ShipIdent="DR-01", HullValue=1, ModulesValue=2, HullHealth=0.87, UnladenMass=400.0,
                CargoCapacity=0, MaxJumpRange=62.3, FuelCapacity={"Main": 32.0, "Reserve": 0.63},
                Rebuy=1000, Modules=[{"Slot": "Armour", "Item": "krait_light_armour_grade1", "On": True,
                                      "Priority": 1, "Health": 1.0}])


def fed(*events):
    status = CrewStatus()
    for e in events:
        status.feed(e)
    return status


def in_ship(ts, main, reserve):
    return {"timestamp": ts, "event": "Status", "Flags": IN_MAIN_SHIP | 8,
            "Fuel": {"FuelMain": main, "FuelReservoir": reserve}}


def test_loadout_sets_ship_hull_capacity_and_edsy_link():
    s = fed(event("LoadGame", Commander="Jameson", FID="F1"), LOADOUT).state
    assert (s.commander, s.ship, s.ship_name, s.ship_ident) == ("Jameson", "krait_light", "Wanderer", "DR-01")
    assert s.hull == 0.87 and (s.fuel_capacity_main, s.fuel_capacity_reserve) == (32.0, 0.63)
    assert s.journal_at == s.timestamp == "2026-10-12T10:00:00Z"
    assert s.edsy_url.startswith("https://edsy.org/#/I=")


def test_edsy_link_round_trips_to_the_loadout():
    url = edsy_url(LOADOUT)
    payload = url[len("https://edsy.org/#/I="):]
    assert "=" not in payload
    assert json.loads(gzip.decompress(base64.urlsafe_b64decode(payload.replace("%3D", "=")))) == LOADOUT


@pytest.mark.parametrize("fields,expected", [
    ({"Health": 0.55, "PlayerPilot": True}, 0.55),
    ({"Health": 0.55, "PlayerPilot": True, "Fighter": False}, 0.55),
    ({"Health": 0.55, "PlayerPilot": False, "Fighter": False}, 0.55),
    ({"Health": 0.55, "PlayerPilot": False}, 0.55),
    ({"Health": 0.55, "PlayerPilot": True, "Fighter": True}, 0.87),
    ({"Health": 0.55, "PlayerPilot": False, "Fighter": True}, 0.87),
    ({"Health": float("nan"), "PlayerPilot": True}, 0.87),
    ({"Health": 1.5, "PlayerPilot": True}, 0.87),
    ({"Health": True, "PlayerPilot": True}, 0.87),
])
def test_hull_damage(fields, expected):
    assert fed(LOADOUT, event("HullDamage", "2026-10-12T10:05:00Z", **fields)).state.hull == expected


@pytest.mark.parametrize("repair,expected", [
    (event("RepairAll", Cost=100), 1.0),
    (event("Repair", Cost=10, Items=["Wear", "Hull"]), 1.0),
    (event("Repair", Cost=10, Item="all"), 1.0),
    (event("Repair", Cost=10, Item="HULL"), 1.0),
    (event("Repair", Cost=10, Items=["Wear", "$int_cockpit_name;"]), 0.4),
    (event("Repair", Cost=10, Items="Hull"), 0.4),
])
def test_repairs(repair, expected):
    damaged = event("HullDamage", "2026-10-12T10:01:00Z", Health=0.4, PlayerPilot=True)
    assert fed(LOADOUT, damaged, repair).state.hull == expected


def test_status_json_fuel_only_while_in_the_ship():
    status = fed(LOADOUT)
    status.feed_status(in_ship("2026-10-12T10:01:00Z", 20.5, 0.4))
    s = status.state
    assert (s.fuel_main, s.fuel_reserve) == (20.5, 0.4)
    assert s.timestamp == "2026-10-12T10:01:00Z" and s.journal_at == "2026-10-12T10:00:00Z"
    status.feed_status({"timestamp": "2026-10-12T10:02:00Z", "event": "Status", "Flags": 0,
                        "Fuel": {"FuelMain": 1.0, "FuelReservoir": 0.1}})
    status.feed_status({"timestamp": "2026-10-12T10:03:00Z", "event": "Status", "Flags": IN_MAIN_SHIP,
                        "Fuel": {"FuelMain": True, "FuelReservoir": float("nan")}})
    status.feed_status({"timestamp": "2026-10-12T10:04:00Z", "Flags": True, "Fuel": {"FuelMain": 1.0}})
    status.feed_status("garbage")
    status.feed_status(None)
    assert (status.state.fuel_main, status.state.fuel_reserve) == (20.5, 0.4)


def test_observers_hear_only_changes_and_a_raising_observer_stops_nothing():
    status = CrewStatus()
    seen = []
    status.subscribe(lambda s: (_ for _ in ()).throw(RuntimeError("observer failed")))
    status.subscribe(seen.append)
    status.feed(LOADOUT)
    status.feed(LOADOUT)
    status.feed(event("Music", MusicTrack="Exploration"))
    status.feed("not a dict")
    assert len(seen) == 1 and seen[0].ship == "krait_light"


def test_a_commander_switch_resets_the_ship():
    s = fed(event("LoadGame", Commander="Jameson"), LOADOUT, event("LoadGame", Commander="Other")).state
    assert s.commander == "Other" and s.ship is None and s.hull is None


def test_adopt_keeps_live_fuel_and_takes_newer_journal_fields():
    live = CrewStatus()
    live.feed_status(in_ship("2026-10-12T10:00:05Z", 12.0, 0.5))
    assert live.state.journal_at is None
    replayed = fed(event("LoadGame", "2026-10-12T09:59:00Z", Commander="Jameson"), LOADOUT).state
    assert live.adopt(replayed) is True
    s = live.state
    assert s.ship == "krait_light" and s.commander == "Jameson" and s.fuel_capacity_main == 32.0
    assert (s.fuel_main, s.fuel_reserve) == (12.0, 0.5)
    assert s.timestamp == "2026-10-12T10:00:05Z" and s.journal_at == "2026-10-12T10:00:00Z"


def test_adopt_rejects_an_older_or_empty_replay():
    live = fed(event("Loadout", "2026-10-12T11:00:00Z", Ship="anaconda", ShipName="Big", ShipIdent="AB-01",
                     HullHealth=1.0, FuelCapacity={"Main": 64.0, "Reserve": 1.0}))
    assert live.adopt(fed(LOADOUT).state) is False
    assert live.state.ship == "anaconda"
    assert live.adopt(ShipStatus()) is False


def test_adopt_fills_missing_ship_fields_when_a_live_change_got_there_first():
    damage = event("HullDamage", "2026-10-12T10:00:30Z", Health=0.61, PlayerPilot=True)
    live = fed(damage)
    assert live.state.ship is None
    replayed = fed(LOADOUT, damage).state
    assert live.adopt(replayed) is True
    s = live.state
    assert s.ship == "krait_light" and (s.fuel_capacity_main, s.fuel_capacity_reserve) == (32.0, 0.63)
    assert s.edsy_url and s.hull == 0.61
    assert s.journal_at == s.timestamp == "2026-10-12T10:00:30Z"
    assert live.adopt(replayed) is False


def test_adopt_does_not_fill_across_commanders():
    live = fed(event("LoadGame", "2026-10-12T10:00:30Z", Commander="Other"))
    replayed = fed(event("LoadGame", "2026-10-12T09:00:00Z", Commander="Jameson"), LOADOUT).state
    assert live.adopt(replayed) is False
    assert live.state.ship is None and live.state.commander == "Other"


def test_adopt_never_mixes_two_ships():
    live = fed(event("LoadGame", "2026-10-12T10:59:00Z", Commander="Jameson"),
               event("Loadout", "2026-10-12T11:00:00Z", Ship="anaconda", ShipName="", ShipIdent="",
                     HullHealth=1.0, FuelCapacity={"Main": 64.0, "Reserve": 1.0}))
    replayed = fed(event("LoadGame", "2026-10-12T09:59:00Z", Commander="Jameson"), LOADOUT).state
    assert live.adopt(replayed) is False
    s = live.state
    assert (s.ship, s.ship_name, s.ship_ident) == ("anaconda", None, None)
    assert (s.fuel_capacity_main, s.fuel_capacity_reserve) == (64.0, 1.0)
