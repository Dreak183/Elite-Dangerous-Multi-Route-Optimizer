import pytest

from edmrn.ext import crew_events as ev
from edmrn.ext.crew_capture import JournalMapper, MapperState, commander_key
from journal_helpers import event

SOL = 10477373803
AUTHOR = "A" * 43
LOAD = event("LoadGame", "2026-10-12T09:59:00Z", Commander="Jameson", FID="F100")


def jump(name="FSDJump", system="Sol", address=SOL, ts="2026-10-12T10:00:00Z", pos=(0, 0, 0)):
    return event(name, ts, StarSystem=system, SystemAddress=address, StarPos=list(pos))


def scan(body_id, body_name, scan_type="Detailed", ts="2026-10-12T10:01:00Z", address=SOL, **fields):
    return event("Scan", ts, ScanType=scan_type, BodyName=body_name, BodyID=body_id, StarSystem="Sol",
                 SystemAddress=address, DistanceFromArrivalLS=10.0, WasDiscovered=True, WasMapped=False,
                 **fields)


def planet(body_id=3, body_name="Sol 3", planet_class="Rocky body", scan_type="Detailed", landable=True,
           terraform="", parents=({"Star": 0},), **kw):
    fields = {"PlanetClass": planet_class, "Parents": [dict(p) for p in parents]}
    if scan_type != "Basic":
        fields.update(Landable=landable, TerraformState=terraform)
    return scan(body_id, body_name, scan_type, **fields, **kw)


def run(*events, mapper=None):
    """Map events through one mapper; every draft must pass its validator."""
    mapper = mapper or JournalMapper()
    drafts = []
    for e in events:
        drafts.extend(mapper.map(e))
    for d in drafts:
        ev.validate_payload(d.kind, 1, d.payload)
    return drafts


def test_events_before_the_first_commander_are_dropped():
    assert run(jump()) == []


def test_jumps_are_visits_and_location_is_not():
    drafts = run(LOAD, event("Location", StarSystem="Sol", SystemAddress=SOL), jump(),
                 jump("CarrierJump", "Alpha", 42, "2026-10-12T10:05:00Z", (1.5, 2, 3)))
    assert [d.kind for d in drafts] == ["visit", "visit"]
    assert drafts[0].payload == {"commander": "Jameson", "system": "Sol", "address": str(SOL),
                                 "coords": [0.0, 0.0, 0.0]}
    assert drafts[1].payload["address"] == "42" and drafts[1].payload["coords"] == [1.5, 2.0, 3.0]
    assert all(d.commander == "F100" and d.event_id is None for d in drafts)
    assert drafts[0].created_at == "2026-10-12T10:00:00Z"


def test_addresses_above_2_53_travel_as_strings():
    [d] = run(LOAD, jump(address=2**60))
    assert d.payload["address"] == str(2**60)


def test_honk_and_all_bodies():
    drafts = run(LOAD,
                 event("FSSDiscoveryScan", Progress=0.3, BodyCount=12, NonBodyCount=30, SystemName="Sol",
                       SystemAddress=SOL),
                 event("FSSAllBodiesFound", SystemName="Sol", SystemAddress=SOL, Count=12))
    assert [(d.kind, d.payload) for d in drafts] == [
        ("honk", {"system": "Sol", "address": str(SOL), "body_count": 12, "non_body_count": 30}),
        ("all_bodies", {"system": "Sol", "address": str(SOL), "count": 12})]


def test_star_and_planet_scans_are_bodies_with_tiers():
    drafts = run(LOAD, jump(), scan(0, "Sol", "AutoScan", StarType="G"), planet(),
                 planet(4, "Sol 3 a", parents=({"Planet": 3}, {"Star": 0})),
                 planet(5, "Sol 5", scan_type="Basic"))
    bodies = [d.payload for d in drafts if d.kind == "body"]
    assert [(b["body_id"], b["tier"]) for b in bodies] == [(0, "full"), (3, "full"), (4, "full"), (5, "basic")]
    star, rocky, moon, basic = bodies
    assert star["star_type"] == "G" and star["planet_class"] is None and star["landable"] is None
    assert rocky["planet_class"] == "Rocky body" and rocky["landable"] is True and rocky["moon"] is False
    assert moon["moon"] is True
    assert basic["landable"] is None and basic["terraform"] is None and basic["scan_type"] == "Basic"
    assert rocky["system"] == "Sol" and rocky["was_discovered"] is True and rocky["was_mapped"] is False


def test_belt_clusters_rings_and_unknown_scan_types_are_skipped():
    drafts = run(LOAD, jump(),
                 scan(6, "Sol A Belt Cluster 1", "AutoScan", Parents=[{"Ring": 5}]),
                 scan(7, "Sol 3 A Ring", "Detailed"),
                 planet(8, "Sol 8", scan_type="NavBeacon"))
    assert [d.kind for d in drafts] == ["visit"]


@pytest.mark.parametrize("planet_class,terraform,expected", [
    ("Earthlike body", "Terraformed", True),
    ("Water world", "", True),
    ("Ammonia world", "", True),
    ("High metal content body", "Terraformable", True),
    ("Rocky body", "", False),
    ("Icy body", "Terraformed", False),
])
def test_valuable_bodies(planet_class, terraform, expected):
    drafts = run(LOAD, jump(), planet(planet_class=planet_class, terraform=terraform))
    finds = [d for d in drafts if d.kind == "find"]
    assert bool(finds) is expected
    if expected:
        f = finds[0].payload
        assert (f["mode"], f["find_kind"], f["body_id"], f["body_name"], f["system"]) == (
            "auto", "valuable_body", 3, "Sol 3", "Sol")
        assert f["detail"] == {"planet_class": planet_class, "terraform": terraform}


def test_a_basic_scan_is_never_a_valuable_find():
    drafts = run(LOAD, jump(), planet(planet_class="Water world", scan_type="Basic"))
    assert [d.kind for d in drafts] == ["visit", "body"]


def test_two_commanders_in_one_sequence_are_attributed_to_whoever_was_active():
    drafts = run(LOAD, jump(), event("Commander", Name="Other", FID="F200"),
                 jump(ts="2026-10-12T11:00:00Z"),
                 event("LoadGame", Commander="Jameson", FID="F100"), jump(ts="2026-10-12T12:00:00Z"))
    assert [d.commander for d in drafts] == ["F100", "F200", "F100"]
    assert [d.payload["commander"] for d in drafts] == ["Jameson", "Other", "Jameson"]


def test_a_commander_switch_forgets_the_system_and_body_names():
    mapper = JournalMapper()
    run(LOAD, jump(), planet(), mapper=mapper)
    assert mapper.state.body_names
    run(event("LoadGame", Commander="Other", FID="F200"), mapper=mapper)
    assert mapper.state.system is None and mapper.state.commander == "F200"
    assert mapper.state.body_names == {}


def test_journals_without_fid_use_a_name_hash():
    [d] = run(event("LoadGame", Commander="Old Timer"), jump())
    assert d.commander == commander_key(None, "Old Timer") and d.commander.startswith("name:")
    assert "Old Timer" not in d.commander


def test_a_snapshot_continues_the_commander_and_system():
    mapper = JournalMapper()
    run(LOAD, jump(), mapper=mapper)
    copy = JournalMapper(mapper.snapshot())
    [d] = run(planet(), mapper=copy)
    assert d.commander == "F100" and d.payload["system"] == "Sol"
    copy.state.body_names[("x", 1)] = "y"
    assert ("x", 1) not in mapper.state.body_names


def test_replaying_the_same_journal_gives_the_same_ids():
    events = [LOAD, jump(), planet(planet_class="Water world")]
    first = [d.resolve_id(AUTHOR) for d in run(*events)]
    second = [d.resolve_id(AUTHOR) for d in run(*events)]
    assert first == second and len(set(first)) == len(first) == 3


@pytest.mark.parametrize("bad", [
    "not a dict",
    None,
    event("FSDJump", ts="yesterday", StarSystem="Sol", SystemAddress=SOL),
    {"event": "FSDJump", "StarSystem": "Sol", "SystemAddress": SOL},
    event("FSDJump", StarSystem="Sol", SystemAddress=True),
    event("FSDJump", StarSystem="Sol", SystemAddress=-5),
    event("FSDJump", StarSystem="", SystemAddress=SOL),
    event("FSSDiscoveryScan", SystemName="Sol", SystemAddress=SOL, BodyCount="12", NonBodyCount=0),
    event("Scan", ScanType="Detailed", BodyName="X", BodyID="3", SystemAddress=SOL, PlanetClass="Rocky body"),
])
def test_malformed_events_give_nothing_and_never_raise(bad):
    assert run(LOAD, bad) == []


def test_garbage_parents_mean_not_a_moon():
    [d] = run(LOAD, event("Scan", ScanType="Detailed", BodyName="X", BodyID=3, SystemAddress=SOL,
                          PlanetClass="Rocky body", Parents="garbage", WasDiscovered=True, WasMapped=False))
    assert d.payload["moon"] is False and d.payload["system"] is None
