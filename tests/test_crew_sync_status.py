import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from crew_sync_fakes import Clock, FakeSocket, hello, relay
from fakes import Root
from edmrn.ext import crew_protocol as proto
from edmrn.ext.crew_identity import KeyRing
from edmrn.ext.crew_roster import GroupConfig
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.crew_sync import (PAUSED_STATUS, CrewBoard, CrewSync, Inbox, make_status_source,
                                 status_from)
from edmrn.ext.store import Store

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
G = "01920000-0000-7000-8000-0000000000a1"
STATUS = {"commander": "Me", "system": "Sol", "address": "10477373803", "coords": [0.0, 0.0, 0.0],
          "ship": "anaconda", "ship_name": None, "ship_ident": None, "edsy_url": None, "hull": 1.0,
          "fuel_main": 32.0, "fuel_reserve": 0.6, "fuel_capacity_main": 32.0,
          "fuel_capacity_reserve": 0.63}


@pytest.fixture
def crew(tmp_path):
    s = Store(tmp_path / "edmrn.db")
    yield CrewStore(s, admit=lambda a, q: True)
    s.close()


@pytest.fixture
def me(tmp_path):
    return KeyRing(tmp_path / "crew_keys").for_commander("ME")


class Ticking(FakeSocket):
    """Advances the clock by `step` seconds on every recv, so the session's timers run."""

    def __init__(self, clock, step, ticks, **kw):
        super().__init__(**kw)
        self.clock, self.step, self.ticks = clock, step, ticks

    def recv(self, timeout):
        self.clock.now += self.step
        if not self.incoming and self.ticks > 0:
            self.ticks -= 1
            raise TimeoutError
        return super().recv(timeout)


def run(crew, me, sock, clock, source, paused=False):
    config = GroupConfig("wss://h", G, "L" * 43, me.author, None, paused)
    board = CrewBoard(now=lambda: NOW)
    sync = CrewSync(crew, config, me, Inbox(crew, Root()), board, source, connect=lambda u: sock,
                    clock=clock, wall=lambda: "2026-10-13T12:00:00Z", rand=lambda: 0.5)
    sync.connect_once()
    return sync, board


def statuses(sock):
    return [json.loads(proto.unb64(f["e"], 8192))["status"] for f in sock.sent_of("st")]


def test_status_goes_out_at_start_then_only_on_change_and_at_most_every_30s(crew, me):
    clock = Clock()
    current = dict(STATUS)
    holder = {}
    answer = relay()

    times = []

    def on_send(sock, frame):
        answer(sock, frame)
        if frame["t"] == "st":
            times.append(clock.now)
        if frame["t"] == "st" and len(sock.sent_of("st")) == 1:
            current["hull"] = 0.5          # the ship changes right after the first status
            holder["sync"].status_changed()

    sock = Ticking(clock, 10, ticks=8, on_send=on_send, frames=[hello()])
    config = GroupConfig("wss://h", G, "L" * 43, me.author, None, False)
    sync = CrewSync(crew, config, me, Inbox(crew, Root()), CrewBoard(), lambda: dict(current),
                    connect=lambda u: sock, clock=clock, wall=lambda: "2026-10-13T12:00:00Z",
                    rand=lambda: 0.5)
    holder["sync"] = sync
    sync.connect_once()
    sent = statuses(sock)
    assert [s["hull"] for s in sent] == [1.0, 0.5]
    assert len(times) == 2 and times[1] - times[0] >= CrewSync.STATUS_INTERVAL   # S8
    assert all(s["paused"] is False for s in sent)


def test_pause_sends_one_paused_status_and_no_own_envelopes(crew, me):
    from crew_helpers import sealed, visit
    crew.insert_received_batch([sealed(me, visit(at="2026-10-13T09:00:00Z"), 1)])
    clock = Clock()
    holder = {}

    def on_send(sock, frame):
        relay(heads={me.author: 0})(sock, frame)
        if frame["t"] == "st":
            holder["sync"].status_changed()   # pause means once, not once per change

    sock = Ticking(clock, 40, ticks=4, on_send=on_send, frames=[hello()])
    config = GroupConfig("wss://h", G, "L" * 43, me.author, None, True)
    sync = CrewSync(crew, config, me, Inbox(crew, Root()), CrewBoard(), lambda: dict(STATUS),
                    connect=lambda u: sock, clock=clock, wall=lambda: "2026-10-13T12:00:00Z",
                    rand=lambda: 0.5)
    holder["sync"] = sync
    sync.connect_once()
    assert statuses(sock) == [PAUSED_STATUS]
    assert sock.sent_of("evs") == []


def test_an_alt_commander_never_sends_status(tmp_path):
    ring = KeyRing(tmp_path / "keys")
    member = ring.for_commander("Main")
    ship = SimpleNamespace(commander="Alt", ship="sidewinder", ship_name=None, ship_ident=None,
                           edsy_url=None, hull=1.0, fuel_main=2.0, fuel_reserve=0.3,
                           fuel_capacity_main=2.0, fuel_capacity_reserve=0.3)
    service = SimpleNamespace(commander="Alt", commander_name="Alt", keyring=ring, status=lambda: (None, ship))
    assert make_status_source(service, member.path)() is None
    service.commander = service.commander_name = ship.commander = "Main"
    assert make_status_source(service, member.path)()["ship"] == "sidewinder"


def test_status_from_maps_location_and_ship():
    location = SimpleNamespace(commander="Me", system="Sol", system_address=10477373803,
                               coords=(1.5, -2.0, 3.25))
    ship = SimpleNamespace(commander="Me", ship="anaconda", ship_name="Mule", ship_ident="MU-01",
                           edsy_url="https://edsy.org/#/I=" + "a" * 7000, hull=0.9, fuel_main=10.0,
                           fuel_reserve=0.5, fuel_capacity_main=32.0, fuel_capacity_reserve=0.63)
    status = status_from(location, ship)
    assert status["address"] == "10477373803" and status["coords"] == [1.5, -2.0, 3.25]
    assert status["edsy_url"] is None
    assert status_from(None, None)["system"] is None


def test_received_status_updates_the_board_and_older_ones_are_dropped(crew, me, tmp_path):
    friend = KeyRing(tmp_path / "f").for_commander("FR")

    def st(at, hull):
        doc = {"author": friend.author, "at": at, "status": {**STATUS, "hull": hull, "paused": False}}
        data = proto.status_bytes(doc)
        return proto.status_frame(data, friend.sign(data))
    clock = Clock()
    sock = FakeSocket(relay(extra=[st("2026-10-13T12:00:10Z", 0.7), st("2026-10-13T12:00:05Z", 0.1)]),
                      [hello()])
    _, board = run(crew, me, sock, clock, lambda: None)
    assert board.snapshot()[friend.author].status["hull"] == 0.7


def test_status_source_is_not_consulted_on_every_tick(crew, me):
    """Review of Task 6: the session ticks ~4x/s; maybe_status is gated by the 30 s interval."""
    clock = Clock()
    calls = []

    def source():
        calls.append(clock.now)
        return dict(STATUS)

    sock = Ticking(clock, 0.25, ticks=400, on_send=relay(), frames=[hello()])  # 100 s of idle ticks
    run(crew, me, sock, clock, source)
    assert len(calls) <= 1 + 100 // 30 and len(statuses(sock)) == 1


def test_a_source_without_status_is_retried_only_every_30s(crew, me):
    clock = Clock()
    calls = []
    sock = Ticking(clock, 0.25, ticks=400, on_send=relay(), frames=[hello()])
    run(crew, me, sock, clock, lambda: calls.append(clock.now))
    assert len(calls) <= 1 + 100 // 30 and sock.sent_of("st") == []


def test_unpausing_sends_the_live_status_again(crew, me):
    clock = Clock()
    holder = {}

    def on_send(sock, frame):
        relay()(sock, frame)
        if frame["t"] == "st" and len(sock.sent_of("st")) == 1:
            holder["sync"].set_paused(False)

    sock = Ticking(clock, 10, ticks=8, on_send=on_send, frames=[hello()])
    config = GroupConfig("wss://h", G, "L" * 43, me.author, None, True)
    sync = CrewSync(crew, config, me, Inbox(crew, Root()), CrewBoard(), lambda: dict(STATUS),
                    connect=lambda u: sock, clock=clock, wall=lambda: "2026-10-13T12:00:00Z",
                    rand=lambda: 0.5)
    holder["sync"] = sync
    sync.connect_once()
    assert [s["paused"] for s in statuses(sock)] == [True, False]


def test_an_own_status_echo_does_not_enter_the_board(crew, me):
    doc = {"author": me.author, "at": "2026-10-13T12:00:10Z", "status": {**STATUS, "paused": False}}
    data = proto.status_bytes(doc)
    frame = proto.status_frame(data, me.sign(data))
    clock = Clock()
    sock = FakeSocket(relay(extra=[frame]), [hello()])
    _, board = run(crew, me, sock, clock, lambda: None)
    assert board.snapshot() == {}


def test_a_forged_status_is_dropped(crew, me, tmp_path):
    friend = KeyRing(tmp_path / "f").for_commander("FR")
    doc = {"author": friend.author, "at": "2026-10-13T12:00:10Z", "status": {**STATUS, "paused": False}}
    data = proto.status_bytes(doc)
    frame = proto.status_frame(data, me.sign(data))  # signed by the wrong key
    sock = FakeSocket(relay(extra=[frame]), [hello()])
    _, board = run(crew, me, sock, Clock(), lambda: None)
    assert board.snapshot() == {}


def _sync_with(crew, me, sock, clock, source, paused=False):
    config = GroupConfig("wss://h", G, "L" * 43, me.author, None, paused)
    return CrewSync(crew, config, me, Inbox(crew, Root()), CrewBoard(now=lambda: NOW), source,
                    connect=lambda u: sock, clock=clock, wall=lambda: "2026-10-13T12:00:00Z",
                    rand=lambda: 0.5)


def test_every_status_gap_is_at_least_30s_even_for_constant_changes(crew, me):
    clock = Clock()
    n = {"i": 0}

    def source():
        n["i"] += 1
        return {**STATUS, "fuel_main": float(n["i"])}

    times = []

    def on_send(sock, frame):
        relay()(sock, frame)
        if frame["t"] == "st":
            times.append(clock.now)

    sock = Ticking(clock, 1, ticks=200, on_send=on_send, frames=[hello()])
    sync = _sync_with(crew, me, sock, clock, source)
    orig = sock.recv

    def recv(timeout):
        sync.status_changed()   # a change on every single tick
        return orig(timeout)
    sock.recv = recv
    sync.connect_once()
    assert len(times) >= 5
    assert all(b - a >= CrewSync.STATUS_INTERVAL for a, b in zip(times, times[1:]))


def test_an_unchanged_dirty_status_does_not_spend_the_interval(crew, me):
    clock = Clock()
    current = dict(STATUS)
    times = []

    def on_send(sock, frame):
        relay()(sock, frame)
        if frame["t"] == "st":
            times.append(clock.now)

    sock = Ticking(clock, 1, ticks=100, on_send=on_send, frames=[hello()])
    sync = _sync_with(crew, me, sock, clock, lambda: dict(current))
    orig = sock.recv

    def recv(timeout):
        if clock.now == 1034:
            sync.status_changed()            # a no-op change at t=35
        if clock.now == 1039:
            current["hull"] = 0.5            # a real change at t=40
            sync.status_changed()
        return orig(timeout)
    sock.recv = recv
    sync.connect_once()
    assert len(times) == 2 and times[1] <= 1041   # sent right after the real change, not at t=66


def test_a_raising_status_source_still_reaches_live_and_fills_the_board(crew, me, tmp_path):
    friend = KeyRing(tmp_path / "f").for_commander("FR")
    doc = {"author": friend.author, "at": "2026-10-13T12:00:10Z", "status": {**STATUS, "paused": False}}
    data = proto.status_bytes(doc)
    frame = proto.status_frame(data, friend.sign(data))

    def source():
        raise RuntimeError("boom")

    sock = FakeSocket(relay(extra=[frame]), [hello()])
    sync = _sync_with(crew, me, sock, Clock(), source)
    assert sync.connect_once() == "live"
    assert friend.author in sync._board.snapshot()


def test_a_far_future_status_does_not_freeze_the_board():
    board = CrewBoard(now=lambda: NOW)
    assert not board.update("A", "2099-01-01T00:00:00Z", {"system": "X"})
    assert board.update("A", "2026-10-13T12:00:00Z", {"system": "Sol"})
    assert board.update("A", "2026-10-13T13:00:00Z", {"system": "Next"})
    assert board.snapshot()["A"].status == {"system": "Next"}


def test_one_invalid_field_does_not_drop_the_own_status(crew, me):
    bad = {**STATUS, "edsy_url": "https://evil.example/x", "hull": 7.0}
    clock = Clock()
    sock = Ticking(clock, 1, ticks=2, on_send=relay(), frames=[hello()])
    run(crew, me, sock, clock, lambda: dict(bad))
    sent = statuses(sock)
    assert len(sent) == 1 and sent[0]["edsy_url"] is None and sent[0]["hull"] is None
    assert sent[0]["system"] == "Sol" and sent[0]["ship"] == "anaconda"


@pytest.mark.parametrize("switched", ["ship", "location"])
def test_a_status_with_mismatched_holders_is_not_sent(tmp_path, switched):
    ring = KeyRing(tmp_path / "keys")
    member = ring.for_commander("Main")
    ship = SimpleNamespace(commander="Alt" if switched == "ship" else "Main", ship="sidewinder",
                           ship_name=None, ship_ident=None, edsy_url=None, hull=1.0, fuel_main=2.0,
                           fuel_reserve=0.3, fuel_capacity_main=2.0, fuel_capacity_reserve=0.3)
    location = SimpleNamespace(commander="Alt" if switched == "location" else "Main",
                               system="Alt home", system_address=1, coords=(0, 0, 0))
    service = SimpleNamespace(commander="Main", commander_name="Main", keyring=ring,
                              status=lambda: (location, ship))
    source = make_status_source(service, member.path)
    assert source() is None
    ship.commander = location.commander = "Main"
    assert source()["system"] == "Alt home"


def test_the_commander_changing_while_building_sends_nothing(tmp_path):
    ring = KeyRing(tmp_path / "keys")
    member = ring.for_commander("Main")
    service = SimpleNamespace(commander="Main", commander_name="Main", keyring=ring)

    def status():
        service.commander = "Alt"      # the switch lands between reading and returning
        return None, None
    service.status = status
    assert make_status_source(service, member.path)() is None


def test_a_paused_client_does_not_offer_its_own_join(crew, me):
    """S8: paused means no own envelopes, the re-seed offer of the member's join included."""
    pair = (b"{}", b"x" * 64)
    crew.roster_envelope = lambda author, kind, group: pair
    crew.heads = lambda: {me.author: 1}
    clock = Clock()
    sock = Ticking(clock, 1, ticks=2, on_send=relay(heads={}), frames=[hello()])
    _sync_with(crew, me, sock, clock, lambda: None, paused=True).connect_once()
    assert sock.sent_of("evs") == []


def test_a_raising_status_source_is_logged_in_one_record_with_the_traceback(crew, me, caplog):
    clock = Clock()

    def source():
        raise RuntimeError("boom")

    sock = Ticking(clock, 0.25, ticks=400, on_send=relay(), frames=[hello()])
    with caplog.at_level("DEBUG"):
        run(crew, me, sock, clock, source)
    failed = [r for r in caplog.records if "status source failed" in r.getMessage()]
    assert len(failed) == 1 and failed[0].exc_info
