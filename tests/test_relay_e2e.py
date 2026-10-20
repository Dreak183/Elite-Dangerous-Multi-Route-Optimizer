"""End to end: two EDMRN crew clients through `wrangler dev` on localhost (spec B1, Testing).

Opt-in, never in the default run:
    cd crew-relay && npm ci && cd ..
    EDMRN_RELAY_E2E=1 .venv/Scripts/python.exe -m pytest tests/test_relay_e2e.py -q
Needs Node; no Cloudflare account. Everything else is real: signing, the relay, the protocol.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from crew_helpers import visit
from edmrn.ext.crew import Writer
from edmrn.ext.crew_group import CrewGroup
from edmrn.ext.crew_identity import KeyRing
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.store import Store

pytestmark = [pytest.mark.relay_e2e,
              pytest.mark.skipif(os.environ.get("EDMRN_RELAY_E2E") != "1", reason="opt-in: EDMRN_RELAY_E2E=1")]

ROOT = Path(__file__).resolve().parents[1]
PORT = 8799
RELAY = f"ws://127.0.0.1:{PORT}"


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
def relay(tmp_path):
    leader_ring = KeyRing(tmp_path / "leader_keys")
    leader_author = leader_ring.for_commander("Leader").author
    npx = "npx.cmd" if sys.platform == "win32" else "npx"
    proc = subprocess.Popen(
        [npx, "wrangler", "dev", "--ip", "127.0.0.1", "--port", str(PORT),
         "--var", f"CREW_LEADERS:{leader_author}", "--persist-to", str(tmp_path / "wrangler-state")],
        cwd=ROOT / "crew-relay", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def up():
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
            return True
        except OSError:
            return False
    try:
        wait_for(up, timeout=90)
        yield
    finally:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.terminate()
        proc.wait(10)


def test_two_clients_exchange_a_visit_and_a_status(relay, tmp_path):
    ship = SimpleNamespace(commander="Friend", ship="anaconda", ship_name=None, ship_ident=None,
                           edsy_url=None, hull=0.75, fuel_main=10.0, fuel_reserve=0.5,
                           fuel_capacity_main=32.0, fuel_capacity_reserve=0.63)
    leader = world(tmp_path, "leader", "Leader")
    friend = world(tmp_path, "friend", "Friend", status=lambda: (None, ship))
    try:
        leader.group.create_group("E2E", RELAY)
        wait_for(lambda: leader.group.state == "live")
        invite = leader.group.create_invite("Friend", 1)
        wait_for(lambda: not leader.group.sync._flush_requested)
        friend.group.join(invite.link)

        def friend_is_live():
            if friend.group.state == "refused":
                friend.group.reconnect()
            return friend.group.state == "live"
        wait_for(friend_is_live)
        friend.service.writer.enqueue(visit(cmdr="Friend", at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                            address="42", system="E2E System"))
        friend.service.writer.drain()

        def leader_has_it():
            leader.group.inbox.drain()
            row = leader.crew.system("42")
            return row is not None and row.name == "E2E System"
        wait_for(leader_has_it)
        friend_author = friend.ring.for_commander("Friend").author
        wait_for(lambda: friend_author in leader.group.board.snapshot())
        assert leader.group.board.snapshot()[friend_author].status["hull"] == 0.75
        assert friend_author in leader.group.roster.members
    finally:
        leader.group.stop()
        friend.group.stop()
        leader.store.close()
        friend.store.close()
