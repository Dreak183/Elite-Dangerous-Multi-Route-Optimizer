import importlib.util
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from crew_helpers import sealed, visit
from edmrn.ext import crew_events as ev
from edmrn.ext import crew_group as crew_group_module
from edmrn.ext.api import ApiError, ApiFailure
from edmrn.ext.crew import Writer
from edmrn.ext.crew_group import CrewGroup, GroupError
from edmrn.ext.crew_identity import KeyRing
from edmrn.ext.crew_roster import GroupConfig, decode_invite, load_group, save_group
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.crew_sync import Inbox
from edmrn.ext.store import Store
from fakes import Root as QueueRoot


class Root(QueueRoot):
    """tests/fakes.Root that also remembers the delay of each callback and runs only the ones
    due at once (the Discord result); periodic ticks (inbox, writer) wait."""

    def __init__(self):
        super().__init__()
        self.delays = []

    def after(self, ms, fn=None, *args):
        self.delays.append(ms)
        super().after(ms, lambda: fn(*args))

    def run_now(self):
        queued, self.queue = self.queue, []
        for fn in queued:
            fn()


class FakeSync:
    instances = []
    events = []

    def __init__(self, store, config, identity, inbox, board, source, connect=None):
        self.config, self.identity, self.source, self.connect = config, identity, source, connect
        self.started = self.stopped = False
        self.paused = None
        self.dirty = 0
        self.flushes = 0
        self.observers = []
        self.state = "live"
        FakeSync.instances.append(self)

    def subscribe(self, observer):
        self.observers.append(observer)

    def start(self):
        self.started = True
        FakeSync.events.append("sync start")

    def stop(self):
        self.stopped = True
        FakeSync.events.append("sync stop")

    def set_paused(self, paused):
        self.paused = paused

    def status_changed(self, *_):
        self.dirty += 1

    def flush_soon(self):
        self.flushes += 1


class Ship:
    def __init__(self):
        self.observers = []
        self.state = SimpleNamespace(commander=None)

    def subscribe(self, fn):
        self.observers.append(fn)


def peer(tmp_path, name, commander, commander_name=None):
    """One EDMRN installation: its own database, key folder and controller. `commander` is the
    mapper key (the Frontier ID in real life), `commander_name` the in-game name."""
    (tmp_path / name).mkdir()
    store = Store(tmp_path / name / "edmrn.db")
    crew = CrewStore(store)
    ring = KeyRing(tmp_path / name / "crew_keys")
    root = Root()
    service = SimpleNamespace(store=crew, keyring=ring, writer=Writer(crew, ring, root), ship=Ship(),
                              commander=commander, commander_name=commander_name or commander,
                              status=lambda: (None, None))
    service.identity = lambda: ring.for_commander(service.commander) if service.commander else None
    tracker = SimpleNamespace(subscriptions=[], subscribe=lambda fn: tracker.subscriptions.append(fn))
    group = CrewGroup(service, SimpleNamespace(root=root, location_tracker=tracker), sync_factory=FakeSync)
    group.start()
    return SimpleNamespace(store=store, crew=crew, ring=ring, service=service, group=group, root=root,
                           tracker=tracker,
                           dir=tmp_path / name / "crew_keys")


@pytest.fixture
def world(tmp_path):
    FakeSync.instances, FakeSync.events = [], []
    leader = peer(tmp_path, "leader", "Leader")
    yield leader
    leader.store.close()


@pytest.fixture
def friend(tmp_path):
    friend = peer(tmp_path, "friend", "Alice")
    yield friend
    friend.store.close()


def author_of(p, commander):
    return p.ring.for_commander(commander).author


def deliver_join(friend, leader):
    """What the relay does: the friend's sealed join arrives in the leader's store."""
    gid = friend.group.config.group_id
    pair = friend.crew.roster_envelope(author_of(friend, friend.service.commander), "join", gid)
    assert pair is not None
    assert leader.crew.insert_received_batch([pair]).inserted == 1


def test_create_group_records_genesis_saves_config_and_starts_sync(world):
    world.group.create_group("Deep Space Crew")
    cfg = load_group(world.dir)
    leader = author_of(world, "Leader")
    assert cfg.leader == cfg.member == leader and cfg.url == "wss://galroute.the-hub.cloud"
    assert world.crew.roster_envelope(leader, "group", cfg.group_id) is not None
    assert world.group.roster.name == "Deep Space Crew"
    assert FakeSync.instances[-1].started and FakeSync.instances[-1].connect is crew_group_module.websockets_connect


@pytest.mark.parametrize("name", ["", "x" * 65, "Ünïcode"])
def test_create_group_refuses_bad_names(world, name):
    with pytest.raises(GroupError):
        world.group.create_group(name)
    assert load_group(world.dir) is None


def test_no_commander_yet_is_a_sentence(world):
    world.service.commander = None
    with pytest.raises(GroupError, match="No commander"):
        world.group.create_group("Crew")


def test_an_unrecorded_action_is_refused_and_leaves_no_group(world):
    world.service.writer.drain = lambda limit=None: 0  # the log write does not happen
    with pytest.raises(GroupError, match="could not record"):
        world.group.create_group("Crew")
    assert world.group.config is None and load_group(world.dir) is None


def test_an_invite_is_recorded_in_the_leaders_roster_and_flushes_the_sync(world):
    world.group.create_group("Crew")
    invite = world.group.create_invite("Alice", 24)
    code = decode_invite(invite.link)
    assert code.leader == author_of(world, "Leader")
    info = world.group.roster.invites[code.invite_id]
    assert info.label == "Alice" and info.redeemed_by is None and info.expires_at == invite.expires_at
    assert FakeSync.instances[-1].flushes == 1  # B4
    with pytest.raises(GroupError):
        world.group.create_invite("Alice", 5)


def test_a_joined_friend_is_admitted_on_the_leaders_side(world, friend):
    world.group.create_group("Crew")
    invite = world.group.create_invite(None, 1)
    friend.group.join(invite.link)
    deliver_join(friend, world)
    alice = author_of(friend, "Alice")
    assert world.group.roster.members[alice].commander == "Alice"
    assert world.group.roster.invites[decode_invite(invite.link).invite_id].redeemed_by == alice


def test_join_records_a_join_and_saves_the_leader_from_the_invite(world, friend):
    world.group.create_group("Crew")
    invite = world.group.create_invite(None, 1)
    friend.group.join(f"<{invite.link}>")
    cfg = load_group(friend.dir)
    assert cfg.leader == author_of(world, "Leader")
    assert cfg.member == author_of(friend, "Alice") and not cfg.is_leader
    assert friend.crew.roster_envelope(cfg.member, "join", cfg.group_id) is not None
    assert FakeSync.instances[-1].started and FakeSync.instances[-1].config == cfg


def test_joining_your_own_invite_is_refused(world):
    world.group.create_group("Crew")
    invite = world.group.create_invite(None, 24)
    world.group.leave()
    with pytest.raises(GroupError, match="own invite"):
        world.group.join(invite.code)


def test_joining_twice_and_garbage_are_refused_with_a_sentence(world, friend):
    world.group.create_group("Crew")
    with pytest.raises(GroupError, match="already in a group"):
        world.group.join("edmrn-crew:1:abc")
    with pytest.raises(GroupError, match="not an EDMRN crew invite"):
        friend.group.join("hello")


def test_only_the_leader_invites_removes_and_sets_the_webhook(world):
    world.group.create_group("Crew")
    world.service.commander = "Alt"
    for action in (lambda: world.group.create_invite(None, 24),
                   lambda: world.group.set_webhook("https://discord.com/api/webhooks/1/x"),
                   lambda: world.group.remove("A" * 43)):
        with pytest.raises(GroupError, match="leader"):
            action()


def test_remove_records_the_members_last_seq_and_caps_the_roster(world, friend):
    world.group.create_group("Crew")
    friend.group.join(world.group.create_invite(None, 24).link)
    deliver_join(friend, world)
    alice = friend.ring.for_commander("Alice")
    world.crew.insert_received_batch([
        sealed(alice, visit(at="2026-10-13T09:00:00Z", address="1"), 2),
        sealed(alice, visit(at="2026-10-13T09:01:00Z", address="2"), 3)])
    flushes = world.group.sync.flushes
    world.group.remove(alice.author)
    [row] = [ev.parse_envelope(data) for _, data, _ in world.crew.envelopes_after(author_of(world, "Leader"), 0)
             if ev.parse_envelope(data)["kind"] == "remove"]
    assert row["payload"] == {"group_id": world.group.config.group_id, "member": alice.author,
                              "last_seq": 3}
    roster = world.group.roster
    assert roster.members[alice.author].last_seq == 3
    assert roster.admits(alice.author, 3) and not roster.admits(alice.author, 4)
    assert world.group.sync.flushes == flushes + 1  # B4
    with pytest.raises(GroupError):  # already removed
        world.group.remove(alice.author)


def test_the_leader_and_strangers_cannot_be_removed(world):
    world.group.create_group("Crew")
    for member in (author_of(world, "Leader"), "A" * 43):
        with pytest.raises(GroupError, match="current member"):
            world.group.remove(member)


def test_leave_pause_and_webhook_persist(world):
    world.group.create_group("Crew")
    world.group.set_paused(True)
    assert load_group(world.dir).paused and FakeSync.instances[-1].paused is True
    world.group.set_webhook("https://discord.com/api/webhooks/1/x")
    assert load_group(world.dir).webhook.endswith("/1/x")
    with pytest.raises(GroupError):
        world.group.set_webhook("https://evil.example/x")
    world.group.set_webhook(None)
    assert load_group(world.dir).webhook is None
    sync = FakeSync.instances[-1]
    world.group.leave()
    assert load_group(world.dir) is None and sync.stopped and world.group.roster is None
    assert world.group.state == "no group"


def test_pause_without_a_group_is_a_sentence(world):
    with pytest.raises(GroupError, match="not in a group"):
        world.group.set_paused(True)


def test_post_invite_reports_through_root_after(world):
    world.group.create_group("Crew")
    world.group.set_webhook("https://discord.com/api/webhooks/1/x")
    invite = world.group.create_invite(None, 24)
    results = []
    world.group._post = lambda url, message: (_ for _ in ()).throw(ApiError(ApiFailure("Discord", "rejected", 401)))
    world.group.post_invite(invite, results.append)
    for thread in threading.enumerate():
        if thread.name == "edmrn-crew-discord":
            thread.join(5)
    assert results == []  # nothing runs on the worker thread
    world.root.run_now()
    assert results == ["Discord rejected the request (HTTP 401)."]


def test_post_invite_without_a_webhook_is_a_sentence(world):
    world.group.create_group("Crew")
    invite = world.group.create_invite(None, 24)
    with pytest.raises(GroupError, match="webhook"):
        world.group.post_invite(invite, lambda result: None)


def test_start_with_a_broken_group_file_sets_a_problem_and_no_sync(world):
    world.group.stop()
    world.dir.mkdir(exist_ok=True)
    (world.dir / "group.json").write_text("{nope", encoding="utf-8")
    FakeSync.instances = []
    world.group.start()
    assert world.group.problem and world.group.config is None and FakeSync.instances == []


def test_without_websockets_sync_does_not_start_and_is_logged_once(world, monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "websockets" else real(name, *a))
    errors = []
    monkeypatch.setattr(crew_group_module.logger, "error", errors.append)
    FakeSync.instances = []
    world.group.create_group("Crew")
    world.group.reconnect()
    assert FakeSync.instances == [] and "websockets" in world.group.problem
    assert len([e for e in errors if "websockets" in e]) == 1


def test_status_changes_reach_the_sync(world):
    world.group.create_group("Crew")
    for observer in world.service.ship.observers:
        observer(None)
    assert FakeSync.instances[-1].dirty >= 1


def test_start_twice_keeps_one_inbox_tick_chain_and_one_sync(world):
    world.group.create_group("Crew")
    world.group.start()
    world.group.start()
    assert world.root.delays.count(Inbox.INTERVAL_MS) == 1
    assert len(FakeSync.instances) == 1


def test_the_inbox_starts_before_the_sync_and_stops_after_it(world, monkeypatch):
    world.group.create_group("Crew")
    world.group.stop()
    FakeSync.events.clear()
    monkeypatch.setattr(world.group.inbox, "start", lambda: FakeSync.events.append("inbox start"))
    monkeypatch.setattr(world.group.inbox, "stop", lambda: FakeSync.events.append("inbox stop"))
    world.group.start()
    world.group.stop()
    assert FakeSync.events == ["inbox start", "sync start", "sync stop", "inbox stop"]


def test_reconnect_replaces_the_sync(world):
    world.group.create_group("Crew")
    first = FakeSync.instances[-1]
    world.group.reconnect()
    second = FakeSync.instances[-1]
    assert first.stopped and second is not first and second.started and world.group.sync is second
    assert len(first.observers) == len(second.observers) == 1


def test_reconnect_without_a_group_is_a_sentence(world):
    with pytest.raises(GroupError, match="not in a group"):
        world.group.reconnect()


def test_sync_state_and_board_observers_reach_tk_only_through_root_after(world):
    world.group.create_group("Crew")
    states, entries = [], []
    world.group.subscribe_state(lambda: states.append(world.group.state))
    world.group.subscribe_board(entries.append)
    sync = FakeSync.instances[-1]
    thread = threading.Thread(target=lambda: (sync.observers[0]("live", None),
                                              world.group.board.update("A" * 43, "2026-10-13T09:00:00Z", {})))
    thread.start()
    thread.join(5)
    assert states == [] and entries == []
    world.root.run_now()
    assert states == ["live"] and [e.author for e in entries] == ["A" * 43]


def test_unsubscribed_observers_receive_nothing_more(world):
    world.group.create_group("Crew")
    states, entries = [], []
    off_state = world.group.subscribe_state(lambda: states.append(1))
    off_board = world.group.subscribe_board(entries.append)
    off_state()
    off_board()
    off_state()  # idempotent
    FakeSync.instances[-1].observers[0]("live", None)
    world.group.board.update("A" * 43, "2026-10-13T09:00:00Z", {})
    world.root.run_now()
    assert states == [] and entries == []


def test_a_failing_observer_does_not_break_the_others(world):
    world.group.create_group("Crew")
    seen = []

    def bad():
        raise RuntimeError("widget gone")
    world.group.subscribe_state(bad)
    world.group.subscribe_state(lambda: seen.append(1))
    FakeSync.instances[-1].observers[0]("live", None)
    world.root.run_now()
    assert seen == [1]


def test_the_group_file_round_trips_through_a_restart(world):
    world.group.create_group("Crew")
    FakeSync.instances = []
    world.group.stop()
    world.group.start()
    assert world.group.config == load_group(world.dir) and FakeSync.instances[-1].started
    assert json.loads((world.dir / "group.json").read_text(encoding="utf-8"))["paused"] is False


# --- fix round 1 -------------------------------------------------------------------------

def test_the_roster_carries_the_in_game_name_never_the_frontier_id(tmp_path):
    FakeSync.instances, FakeSync.events = [], []
    leader = peer(tmp_path, "keyed", "F100", "Jameson")
    friend = peer(tmp_path, "keyed_friend", "F200", "Alice")
    try:
        leader.group.create_group("Crew")
        friend.group.join(leader.group.create_invite(None, 24).link)
        deliver_join(friend, leader)
        assert sorted(m.commander for m in leader.group.roster.members.values()) == ["Alice", "Jameson"]
        for p in (leader, friend):
            with p.store.read() as conn:
                blobs = [bytes(r[0]) for r in conn.execute("SELECT envelope FROM crew_log")]
            assert blobs and not any(b"F100" in b or b"F200" in b for b in blobs)
    finally:
        leader.store.close()
        friend.store.close()


def test_no_group_action_without_the_in_game_name(world):
    world.service.commander_name = None
    with pytest.raises(GroupError, match="No commander"):
        world.group.create_group("Crew")


def test_create_group_twice_is_refused(world):
    world.group.create_group("Crew")
    with pytest.raises(GroupError, match="already in a group"):
        world.group.create_group("Other")
    assert world.group.roster.name == "Crew"


def test_create_group_refuses_a_bad_relay_address(world):
    with pytest.raises(GroupError, match="relay address"):
        world.group.create_group("Crew", relay="http://evil.example")
    assert world.group.config is None and load_group(world.dir) is None


def test_an_invite_label_is_checked(world):
    world.group.create_group("Crew")
    for label in ("Ünï", "x" * 65):
        with pytest.raises(GroupError, match="label"):
            world.group.create_invite(label, 24)


@pytest.mark.parametrize("hours", [True, 24.0, "24", None])
def test_invite_hours_must_be_one_of_the_integers(world, hours):
    world.group.create_group("Crew")
    with pytest.raises(GroupError, match="lasts"):
        world.group.create_invite(None, hours)


def test_a_missing_member_key_is_a_problem_logged_once_and_starts_no_sync(world, tmp_path, monkeypatch):
    stranger = KeyRing(tmp_path / "stranger_keys").for_commander("X").author
    world.dir.mkdir(exist_ok=True)
    save_group(world.dir, GroupConfig("wss://galroute.the-hub.cloud", "01920000-0000-7000-8000-0000000000a1",
                                      stranger, stranger))
    errors = []
    monkeypatch.setattr(crew_group_module.logger, "error", errors.append)
    world.group.stop()
    world.group.start()
    world.group.reconnect()
    world.group.reconnect()
    assert "key" in world.group.problem and FakeSync.instances == [] and world.group.sync is None
    assert len([e for e in errors if "missing from crew_keys" in e]) == 1


def test_starting_clears_a_stale_problem(world):
    world.group.stop()
    world.dir.mkdir(exist_ok=True)
    (world.dir / "group.json").write_text("{nope", encoding="utf-8")
    world.group.start()
    assert world.group.problem
    (world.dir / "group.json").unlink()
    world.group.stop()
    world.group.start()
    assert world.group.problem is None


def test_creating_a_group_clears_a_stale_problem(world):
    world.group.problem = "left over"
    world.group.create_group("Crew")
    assert world.group.problem is None


def test_reconnecting_does_not_pile_up_status_subscriptions(world):
    world.group.create_group("Crew")
    for _ in range(3):
        world.group.reconnect()
    assert len(world.service.ship.observers) == 1 and len(world.tracker.subscriptions) == 1


def test_start_while_a_sync_runs_keeps_the_config_and_the_sync(world):
    world.group.create_group("Crew")
    sync, config = world.group.sync, world.group.config
    (world.dir / "group.json").unlink()  # disk and memory disagree; a running sync wins
    world.group.start()
    assert world.group.sync is sync and world.group.config is config and world.group.problem is None


def test_stop_then_start_leaves_exactly_one_inbox_tick_chain(world):
    world.group.stop()
    world.group.start()
    world.root.run_now()  # the dead chain's pending tick fires and ends; the live one reschedules
    assert len(world.root.queue) == 1
    world.root.run_now()
    assert len(world.root.queue) == 1


def test_only_the_leader_can_post_an_invite(world):
    world.group.create_group("Crew")
    world.group.set_webhook("https://discord.com/api/webhooks/1/x")
    invite = world.group.create_invite(None, 24)
    world.service.commander = "Alt"
    with pytest.raises(GroupError, match="leader"):
        world.group.post_invite(invite, lambda result: None)


def deny(*args, **kwargs):
    raise PermissionError("denied")


def disk_and_memory(world):
    cfg = load_group(world.dir)
    return cfg, world.group.config


def test_a_failing_pause_save_changes_nothing(world, monkeypatch):
    world.group.create_group("Crew")
    sync = world.group.sync
    monkeypatch.setattr(crew_group_module, "save_group", deny)
    with pytest.raises(GroupError, match="group file"):
        world.group.set_paused(True)
    assert not world.group.config.paused and not load_group(world.dir).paused and sync.paused is None


def test_a_failing_webhook_save_changes_nothing(world, monkeypatch):
    world.group.create_group("Crew")
    monkeypatch.setattr(crew_group_module, "save_group", deny)
    with pytest.raises(GroupError, match="group file"):
        world.group.set_webhook("https://discord.com/api/webhooks/1/x")
    assert world.group.config.webhook is None and load_group(world.dir).webhook is None


def test_a_failing_leave_keeps_the_group_and_the_sync(world, monkeypatch):
    world.group.create_group("Crew")
    sync = world.group.sync
    monkeypatch.setattr(crew_group_module, "delete_group", deny)
    with pytest.raises(GroupError, match="group file"):
        world.group.leave()
    assert world.group.config is not None and world.group.sync is sync and not sync.stopped
    assert load_group(world.dir) is not None


def test_a_gate_failure_while_leaving_is_a_sentence_and_the_group_is_gone(world, monkeypatch):
    world.group.create_group("Crew")
    sync = world.group.sync

    def locked(*args):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(world.crew, "set_gate", locked)
    with pytest.raises(GroupError, match="close the group"):
        world.group.leave()
    assert world.group.config is None and sync.stopped and load_group(world.dir) is None


def test_a_gate_failure_while_creating_leaves_no_group_behind(world, monkeypatch):
    def locked(*args):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(world.crew, "set_gate", locked)
    with pytest.raises(GroupError, match="open the group"):
        world.group.create_group("Crew")
    assert world.group.config is None and load_group(world.dir) is None and world.group.sync is None


def test_a_failing_group_file_save_while_creating_starts_nothing(world, monkeypatch):
    monkeypatch.setattr(crew_group_module, "save_group", deny)
    with pytest.raises(GroupError, match="group file"):
        world.group.create_group("Crew")
    assert world.group.config is None and world.group.sync is None


def test_a_log_failure_while_recording_is_a_sentence(world, monkeypatch):
    def locked(*args):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(world.crew, "has_event", locked)
    with pytest.raises(GroupError, match="record this"):
        world.group.create_group("Crew")
    assert world.group.config is None


def test_a_failing_sync_factory_is_a_problem_not_an_exception(world):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")
    world.group._sync_factory = broken
    world.group.create_group("Crew")
    assert world.group.config is not None and world.group.sync is None and world.group.problem


def test_a_log_failure_while_removing_is_a_sentence(world, friend, monkeypatch):
    world.group.create_group("Crew")
    friend.group.join(world.group.create_invite(None, 24).link)
    deliver_join(friend, world)

    def locked(*args):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(world.crew, "max_seq", locked)
    with pytest.raises(GroupError, match="read the log"):
        world.group.remove(author_of(friend, "Alice"))
