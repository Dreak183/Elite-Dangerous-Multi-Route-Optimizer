"""Two EDMRN crew clients through the real relay on 127.0.0.1 (spec R5)."""
import time
from types import SimpleNamespace

import pytest

from crew_helpers import visit
from edmrn.ext.crew import Writer
from edmrn.ext.crew_group import CrewGroup
from edmrn.ext.crew_identity import KeyRing
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.crew_sync import CrewSync
from edmrn.ext.store import Store
from edmrn_relay.config import RelayConfig
from relay_fakes import ServerThread

pytestmark = pytest.mark.loopback


class Root:
    def after(self, ms, fn, *args):
        if ms == 0:
            fn(*args)


class Ship:
    def __init__(self):
        self.state = SimpleNamespace(commander=None)

    def subscribe(self, fn):
        pass


def wait_for(predicate, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError("condition not met in time")


def world(tmp, name, commander, status=None):
    store = Store(tmp / f"{name}.db")
    crew = CrewStore(store)
    ring = KeyRing(tmp / f"{name}_keys")
    service = SimpleNamespace(store=crew, keyring=ring, writer=Writer(crew, ring, Root()), ship=Ship(),
                              commander=commander, commander_name=commander,
                              status=status or (lambda: (None, None)), group=None)
    service.identity = lambda: ring.for_commander(service.commander)
    group = CrewGroup(service, SimpleNamespace(root=Root()))
    group.start()
    return SimpleNamespace(store=store, crew=crew, ring=ring, service=service, group=group)


@pytest.fixture
def relay_url(tmp_path, monkeypatch):
    monkeypatch.setattr(CrewSync, "FLUSH_INTERVAL", 0.5)
    leader = KeyRing(tmp_path / "leader_keys").for_commander("Leader").author
    server = ServerThread(RelayConfig(port=0, db=tmp_path / "relay.db", leaders=frozenset({leader}),
                                      source_url="https://example.invalid/edmrn"))
    try:
        yield f"ws://127.0.0.1:{server.port}"
    finally:
        server.stop()


def stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def test_two_clients_exchange_envelopes_and_a_status(relay_url, tmp_path):
    ship = SimpleNamespace(commander="Friend", ship="anaconda", ship_name=None, ship_ident=None,
                           edsy_url=None, hull=0.75, fuel_main=10.0, fuel_reserve=0.5,
                           fuel_capacity_main=32.0, fuel_capacity_reserve=0.63)
    leader = world(tmp_path, "leader", "Leader")
    friend = world(tmp_path, "friend", "Friend", status=lambda: (None, ship))
    try:
        leader.group.create_group("Loopback", relay_url)
        wait_for(lambda: leader.group.state == "live")
        invite = leader.group.create_invite("Friend", 1)
        wait_for(lambda: not leader.group.sync._flush_requested)
        friend.group.join(invite.link)

        def friend_is_live():
            if friend.group.state == "refused":
                friend.group.reconnect()
            return friend.group.state == "live"
        wait_for(friend_is_live)

        friend.service.writer.enqueue(visit(cmdr="Friend", at=stamp(), address="42", system="Loopback A"))
        friend.service.writer.drain()

        def leader_has_it():
            leader.group.inbox.drain()
            row = leader.crew.system("42")
            return row is not None and row.name == "Loopback A"
        wait_for(leader_has_it)
        assert leader.crew.system("42").name == "Loopback A"

        leader.service.writer.enqueue(visit(cmdr="Leader", at=stamp(), address="43", system="Loopback B"))
        leader.service.writer.drain()

        def friend_has_it():
            friend.group.inbox.drain()
            row = friend.crew.system("43")
            return row is not None and row.name == "Loopback B"
        wait_for(friend_has_it)
        assert friend.crew.system("43").name == "Loopback B"

        friend_author = friend.ring.for_commander("Friend").author
        wait_for(lambda: friend_author in leader.group.board.snapshot())
        assert leader.group.board.snapshot()[friend_author].status["hull"] == 0.75
        assert friend_author in leader.group.roster.members
    finally:
        leader.group.stop()
        friend.group.stop()
        leader.store.close()
        friend.store.close()
