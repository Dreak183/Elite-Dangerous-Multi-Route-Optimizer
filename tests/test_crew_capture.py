import os

import pytest

from edmrn.ext import crew_events as ev
from edmrn.ext.crew_capture import (JournalMapper, LiveCapture, MapperState, commander_key, find_revision,
                                    note_revision, replay)
from edmrn.ext.crew_identity import KeyRing
from edmrn.ext.crew_status import CrewStatus
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.store import Store
from journal_helpers import append_events, event, journal_file

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

BIO = {"Type": "$SAA_SignalType_Biological;", "Type_Localised": "Biologisch", "Count": 2}


def body_signals(name="FSSBodySignals", body_id=3, body_name="Sol 3", ts="2026-10-12T10:02:00Z",
                 signals=(BIO,), genuses=None):
    fields = {"BodyName": body_name, "BodyID": body_id, "SystemAddress": SOL,
              "Signals": [dict(s) for s in signals]}
    if genuses is not None:
        fields["Genuses"] = [{"Genus": g, "Genus_Localised": g.strip("$;")} for g in genuses]
    return event(name, ts, **fields)


def test_fss_body_signals_give_counts_and_a_biological_find():
    drafts = run(LOAD, jump(), body_signals(signals=(
        BIO, {"Type": "$SAA_SignalType_Geological;", "Count": 1},
        {"Type": "$PlanetaryMiningLocation_Name;", "Type_Localised": "Planetary mining location", "Count": 3})))
    assert drafts[1].payload == {"system": "Sol", "address": str(SOL), "body_id": 3, "body_name": "Sol 3",
                                 "source": "fss", "counts": {"biological": 2, "geological": 1, "mining": 3},
                                 "genera": []}
    [find] = [d for d in drafts if d.kind == "find"]
    assert (find.payload["find_kind"], find.payload["body_name"], find.payload["detail"]) == (
        "biological", "Sol 3", {"count": 2})


def test_saa_signals_classify_types_and_carry_sorted_unique_genera():
    signals = [BIO] + [{"Type": f"$SAA_SignalType_{t};", "Count": 1}
                       for t in ("Human", "Guardian", "Thargoid", "PlanetAnomaly", "Other")]
    signals.append({"Type": "$SAA_SignalType_FutureThing;", "Count": 2})
    drafts = run(LOAD, jump(), body_signals("SAASignalsFound", signals=signals, genuses=[
        "$Codex_Ent_Stratum_Genus_Name;", "$Codex_Ent_Bacterial_Genus_Name;", "$Codex_Ent_Bacterial_Genus_Name;"]))
    payload = drafts[1].payload
    assert payload["source"] == "saa"
    assert payload["counts"] == {"biological": 2, "human": 1, "guardian": 1, "thargoid": 2, "other": 3}
    assert payload["genera"] == ["$Codex_Ent_Bacterial_Genus_Name;", "$Codex_Ent_Stratum_Genus_Name;"]


def test_biological_from_fss_then_saa_is_one_find():
    drafts = run(LOAD, jump(), body_signals(),
                 body_signals("SAASignalsFound", ts="2026-10-12T10:09:00Z",
                              genuses=["$Codex_Ent_Bacterial_Genus_Name;", "$Codex_Ent_Stratum_Genus_Name;"]))
    finds = [d for d in drafts if d.kind == "find"]
    assert len(finds) == 2
    assert finds[0].resolve_id(AUTHOR) == finds[1].resolve_id(AUTHOR)
    signal_ids = {d.resolve_id(AUTHOR) for d in drafts if d.kind == "signals"}
    assert len(signal_ids) == 2


def test_ring_signal_events_are_skipped():
    drafts = run(LOAD, jump(),
                 body_signals("SAASignalsFound", body_id=7, body_name="Sol 3 A Ring", genuses=[]),
                 body_signals("SAASignalsFound", body_id=9, body_name="Sol 9 B",
                              signals=({"Type": "Painite", "Count": 4},), genuses=[]))
    assert [d.kind for d in drafts] == ["visit"]


def test_signals_without_biology_give_no_find():
    drafts = run(LOAD, jump(), body_signals(signals=({"Type": "$SAA_SignalType_Geological;", "Count": 1},)))
    assert [d.kind for d in drafts] == ["visit", "signals"]


@pytest.mark.parametrize("signals", [
    "not a list",
    [{"Type": "$SAA_SignalType_Biological;", "Count": "2"}],
    [{"Type": 5, "Count": 2}],
    ["not a dict"],
])
def test_malformed_signal_lists_give_nothing(signals):
    bad = event("FSSBodySignals", BodyName="Sol 3", BodyID=3, SystemAddress=SOL, Signals=signals)
    assert run(LOAD, bad) == []


def nsp(ts, name="$Fixed_Event_Life_Cloud;", signal_type="Codex"):
    return event("FSSSignalDiscovered", ts, SystemAddress=SOL, SignalName=name,
                 SignalName_Localised="Bemerkenswerte Sternenphänomene", SignalType=signal_type)


def test_several_nsp_markers_in_one_system_give_one_find():
    drafts = run(LOAD, jump(), nsp("2026-10-12T10:00:10Z"), nsp("2026-10-12T10:00:11Z"),
                 nsp("2026-10-12T10:00:12Z", "$MULTIPLAYER_SCENARION_TITLE;", "ResourceExtraction"),
                 nsp("2026-10-12T10:00:13Z", "$Codex_Something_Else;"))
    finds = [d for d in drafts if d.kind == "find"]
    assert len(finds) == 2 and len({f.resolve_id(AUTHOR) for f in finds}) == 1
    p = finds[0].payload
    assert (p["find_kind"], p["system"], p["body_id"], p["body_name"], p["detail"]) == (
        "stellar_phenomenon", "Sol", None, None, {"signal": "$Fixed_Event_Life_Cloud;"})


def codex(**fields):
    base = dict(EntryID=2420101, Name="$Codex_Ent_Gas_Clds_Yellow_Name;", Name_Localised="Croceum Lagrange Cloud",
                SubCategory="$Codex_SubCategory_Organic_Structures;", Category="$Codex_Category_Biology;",
                Region="$Codex_RegionName_18;", System="Sol", SystemAddress=SOL, BodyID=3)
    base.update(fields)
    return event("CodexEntry", "2026-10-12T10:04:00Z", **base)


def test_a_codex_find_at_a_phenomenon_carries_the_body_name_from_the_earlier_scan():
    drafts = run(LOAD, jump(), planet(), codex(NearestDestination="$Fixed_Event_Life_Cloud;"))
    [find] = [d for d in drafts if d.kind == "find"]
    p = find.payload
    assert (p["find_kind"], p["body_name"], p["body_id"], p["system"]) == ("codex", "Sol 3", 3, "Sol")
    assert p["detail"] == {"entry_id": 2420101, "name": "$Codex_Ent_Gas_Clds_Yellow_Name;",
                           "category": "$Codex_Category_Biology;",
                           "subcategory": "$Codex_SubCategory_Organic_Structures;", "new_entry": False}


def test_a_new_codex_entry_is_a_find_and_other_entries_are_not():
    drafts = run(LOAD, jump(), codex(IsNewEntry=True),
                 codex(EntryID=1, NearestDestination="$Ancient:#index=1;"),
                 codex(EntryID=2, IsNewEntry=False), codex(EntryID=3))
    finds = [d for d in drafts if d.kind == "find"]
    assert len(finds) == 1
    assert finds[0].payload["detail"]["new_entry"] is True and finds[0].payload["body_name"] is None


def test_a_location_entered_system_names_signals_and_phenomena():
    drafts = run(LOAD, event("Location", StarSystem="Sol", SystemAddress=SOL), body_signals(),
                 nsp("2026-10-12T10:00:10Z"))
    [signals] = [d for d in drafts if d.kind == "signals"]
    assert signals.payload["system"] == "Sol"
    [phenomenon] = [d for d in drafts if d.kind == "find" and d.payload["find_kind"] == "stellar_phenomenon"]
    assert phenomenon.payload["system"] == "Sol"


def test_a_jump_to_another_system_forgets_body_names():
    drafts = run(LOAD, jump(), planet(), jump(system="Alpha", address=42, ts="2026-10-12T10:05:00Z"),
                 jump(ts="2026-10-12T10:10:00Z"), codex(NearestDestination="$Fixed_Event_Life_Cloud;"))
    [find] = [d for d in drafts if d.kind == "find"]
    assert find.payload["body_name"] is None


def test_manual_find_and_note_revisions():
    first = find_revision("F100", ev.uuid7(), "other", "Sol", str(SOL), None, None, "Wreck")
    second = find_revision("F100", first.payload["find_id"], "other", "Sol", str(SOL), None, None,
                           "Wreck, 2 pods")
    assert first.event_id != second.event_id and ev.is_uuid7(first.event_id)
    assert first.payload["mode"] == "manual" and ev.stamp(first.created_at)
    assert first.resolve_id(AUTHOR) == first.event_id
    note = note_revision("F100", ev.uuid7(), "Sol", str(SOL), "Nice rings")
    assert note.kind == "note" and note.payload["deleted"] is False
    with pytest.raises(ev.PayloadError):
        note_revision("F100", ev.uuid7(), "Sol", str(SOL), "x‮")
    with pytest.raises(ev.PayloadError):
        find_revision("F100", ev.uuid7(), "treasure", "Sol", str(SOL), None, None, "")


def test_a_phenomenon_signal_of_another_type_is_not_a_find():
    drafts = run(LOAD, jump(), nsp("2026-10-12T10:00:10Z", signal_type="USS"))
    assert [d.kind for d in drafts] == ["visit"]


def test_manual_builders_reject_an_invalid_created_at():
    with pytest.raises(ev.PayloadError):
        find_revision("F100", ev.uuid7(), "other", "Sol", str(SOL), None, None, "x", created_at="garbage")
    with pytest.raises(ev.PayloadError):
        note_revision("F100", ev.uuid7(), "Sol", str(SOL), "x", created_at="2026-10-12 10:00:00")
    ok = note_revision("F100", ev.uuid7(), "Sol", str(SOL), "x", created_at="2026-10-12T10:00:00Z")
    assert ok.created_at == "2026-10-12T10:00:00Z"


def test_live_events_before_seed_are_buffered_and_attributed():
    queued = []
    live = LiveCapture(queued.append)
    live.feed(jump())
    assert queued == [] and not live.seeded
    live.seed(MapperState(commander="F100", commander_name="Jameson"))
    assert live.seeded
    assert [(d.kind, d.commander) for d in queued] == [("visit", "F100")]
    live.feed(jump(system="Alpha", address=42, ts="2026-10-12T10:10:00Z"))
    assert len(queued) == 2 and queued[1].payload["system"] == "Alpha"


def test_a_loadgame_in_the_buffer_overrides_the_seed():
    queued = []
    live = LiveCapture(queued.append)
    live.feed(event("LoadGame", Commander="Other", FID="F200"))
    live.feed(jump())
    live.seed(MapperState(commander="F100", commander_name="Jameson"))
    assert [d.commander for d in queued] == ["F200"]


def test_seed_without_a_state_maps_the_buffer_from_scratch():
    queued = []
    live = LiveCapture(queued.append)
    live.feed(jump())
    live.feed(LOAD)
    live.feed(jump(ts="2026-10-12T10:01:00Z"))
    live.seed(None)
    assert [d.commander for d in queued] == ["F100"]


def test_a_second_seed_changes_nothing():
    queued = []
    live = LiveCapture(queued.append)
    live.seed(MapperState(commander="F100", commander_name="Jameson"))
    live.seed(MapperState(commander="F200", commander_name="Other"))
    live.feed(jump())
    assert [d.commander for d in queued] == ["F100"]


def test_the_buffer_is_bounded():
    live = LiveCapture(lambda d: None)
    for _ in range(LiveCapture.BUFFER_LIMIT + 10):
        live.feed(jump())
    assert len(live._buffer) == LiveCapture.BUFFER_LIMIT


def test_status_is_fed_live_even_before_the_seed():
    status = CrewStatus()
    live = LiveCapture(lambda d: None, status)
    live.feed(event("Loadout", "2026-10-12T10:00:00Z", Ship="sidewinder", HullHealth=1.0,
                    FuelCapacity={"Main": 2.0, "Reserve": 0.3}))
    assert status.state.ship == "sidewinder"


def test_a_raising_enqueue_does_not_reach_the_monitor():
    def refuse(draft):
        raise RuntimeError("queue gone")

    live = LiveCapture(refuse)
    live.seed(MapperState(commander="F100", commander_name="Jameson"))
    live.feed(jump())


def write_journal(directory, stamp, mtime, *events):
    directory.mkdir(exist_ok=True)
    path = journal_file(directory, stamp)
    append_events(path, *events)
    os.utime(path, (mtime, mtime))
    return path


def test_replay_reads_the_newest_five_files_oldest_first(tmp_path):
    journals = tmp_path / "journals"
    for i in range(6):
        write_journal(journals, f"2026-10-0{i + 1}T100000", 1_000_000 + i * 100, LOAD,
                      jump(system=f"S{i}", address=100 + i, ts=f"2026-10-0{i + 1}T10:00:00Z"))
    queued = []
    state = replay(str(journals), queued.append)
    assert [d.payload["system"] for d in queued] == ["S1", "S2", "S3", "S4", "S5"]
    assert state.commander == "F100" and state.system == "S5"


def test_replay_chooses_by_modification_time_not_by_name(tmp_path):
    journals = tmp_path / "journals"
    write_journal(journals, "220315152335", 2_000_000, LOAD, jump(system="Horizons"))
    write_journal(journals, "2026-10-01T100000", 1_000_000, LOAD, jump(system="Odyssey"))
    queued = []
    replay(str(journals), queued.append, max_files=1)
    assert [d.payload["system"] for d in queued] == ["Horizons"]


def test_replay_seeds_the_status_from_the_startup_loadout(tmp_path):
    journals = tmp_path / "journals"
    write_journal(journals, "2026-10-12T100000", 1_000_000, LOAD,
                  event("Loadout", "2026-10-12T10:00:01Z", Ship="krait_light", HullHealth=0.9,
                        FuelCapacity={"Main": 32.0, "Reserve": 0.63}))
    status = CrewStatus()
    replay(str(journals), lambda d: None, status)
    assert status.state.ship == "krait_light" and status.state.hull == 0.9


def test_replay_of_a_missing_folder_or_no_files_returns_none(tmp_path):
    assert replay(str(tmp_path / "missing"), lambda d: None) is None
    assert replay(str(tmp_path), lambda d: None) is None
    assert replay(None, lambda d: None) is None


def test_replay_skips_unreadable_files_and_bad_lines(tmp_path):
    journals = tmp_path / "journals"
    path = write_journal(journals, "2026-10-12T100000", 1_000_000, LOAD)
    (journals / "Journal.2026-10-12T090000.01.log").mkdir()
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"event": broken\n["event"]\n')
    append_events(path, jump())
    queued = []
    replay(str(journals), queued.append)
    assert [d.kind for d in queued] == ["visit"]


def _session(journals):
    write_journal(journals, "2026-10-12T100000", 1_000_000, LOAD, jump(),
                  event("FSSDiscoveryScan", "2026-10-12T10:00:30Z", Progress=0.5, BodyCount=1,
                        NonBodyCount=0, SystemName="Sol", SystemAddress=SOL),
                  planet(), event("FSSAllBodiesFound", "2026-10-12T10:02:00Z", SystemName="Sol",
                                  SystemAddress=SOL, Count=1))


def test_replaying_a_journal_twice_adds_no_envelope(tmp_path):
    journals = tmp_path / "journals"
    _session(journals)
    store = Store(tmp_path / "edmrn.db")
    ring = KeyRing(tmp_path / "crew_keys")
    try:
        crew = CrewStore(store)
        for _ in range(2):
            queued = []
            replay(str(journals), queued.append)
            crew.append_own([(ring.for_commander(d.commander), d) for d in queued])
        with store.read() as conn:
            assert conn.execute("SELECT kind FROM crew_log ORDER BY seq").fetchall() == [
                ("visit",), ("honk",), ("body",), ("all_bodies",)]
    finally:
        store.close()


def test_two_commanders_in_one_journal_sequence_give_two_authors(tmp_path):
    journals = tmp_path / "journals"
    write_journal(journals, "2026-10-12T100000", 1_000_000, LOAD, jump(),
                  event("LoadGame", "2026-10-12T11:00:00Z", Commander="Other", FID="F200"),
                  jump(ts="2026-10-12T11:01:00Z"))
    store = Store(tmp_path / "edmrn.db")
    ring = KeyRing(tmp_path / "crew_keys")
    try:
        queued = []
        replay(str(journals), queued.append)
        CrewStore(store).append_own([(ring.for_commander(d.commander), d) for d in queued])
        with store.read() as conn:
            authors = conn.execute("SELECT DISTINCT author FROM crew_log").fetchall()
        assert len(authors) == 2
        assert len(list((tmp_path / "crew_keys").glob("cmdr-*.json"))) == 2
    finally:
        store.close()


def test_seed_skips_buffered_events_the_replay_already_read(tmp_path):
    journals = tmp_path / "journals"
    write_journal(journals, "2026-10-12T100000", 1_000_000, LOAD,
                  jump(ts="2026-10-12T10:00:00Z"),
                  event("LoadGame", "2026-10-12T11:00:00Z", Commander="Other", FID="F200"))
    queued = []
    state = replay(str(journals), queued.append)
    assert [d.commander for d in queued] == ["F100"]
    live = LiveCapture(queued.append)
    live.feed(jump(ts="2026-10-12T10:00:00Z"))
    live.feed(event("LoadGame", "2026-10-12T11:00:00Z", Commander="Other", FID="F200"))
    live.seed(state)
    assert [d.commander for d in queued] == ["F100"]


def test_seed_still_maps_buffered_events_newer_than_the_replay(tmp_path):
    journals = tmp_path / "journals"
    write_journal(journals, "2026-10-12T100000", 1_000_000, LOAD, jump(ts="2026-10-12T10:00:00Z"))
    state = replay(str(journals), lambda d: None)
    queued = []
    live = LiveCapture(queued.append)
    live.feed(jump(system="Alpha", address=42, ts="2026-10-12T10:05:00Z"))
    live.seed(state)
    assert [(d.commander, d.payload["system"]) for d in queued] == [("F100", "Alpha")]


def test_a_raising_enqueue_does_not_abort_the_replay(tmp_path):
    journals = tmp_path / "journals"
    write_journal(journals, "2026-10-12T100000", 1_000_000, LOAD, jump(),
                  event("Loadout", "2026-10-12T10:00:01Z", Ship="krait_light", HullHealth=0.9,
                        FuelCapacity={"Main": 32.0, "Reserve": 0.63}))

    def refuse(draft):
        raise RuntimeError("queue gone")

    status = CrewStatus()
    state = replay(str(journals), refuse, status)
    assert state is not None and state.system == "Sol"
    assert status.state.ship == "krait_light"


def test_replay_finds_journals_in_a_folder_with_glob_characters(tmp_path):
    journals = tmp_path / "Saved [Games]"
    write_journal(journals, "2026-10-12T100000", 1_000_000, LOAD, jump())
    queued = []
    assert replay(str(journals), queued.append) is not None
    assert [d.kind for d in queued] == ["visit"]
