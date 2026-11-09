"""The relay over real loopback sockets."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
import websockets.exceptions
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from edmrn_relay import engine, server as server_module
from edmrn_relay.__main__ import run
from edmrn_relay.config import RelayConfig
from edmrn_relay.engine import Send
from edmrn_relay.join_page import JOIN_CSP, JOIN_HTML, greeting
from edmrn_relay.server import MAX_WIRE_BYTES, Peer, RelayServer
from edmrn_relay.store import RelayStore
from relay_fakes import auth_frame, envelope_item, key_for, new_group, stamp_at, status_frame

pytestmark = pytest.mark.loopback

V7 = "01920000-0000-7000-8000-0000000000a2"
SRC = "https://example.invalid/edmrn"


def make_config(tmp_path, **extra):
    return RelayConfig(port=0, db=tmp_path / "relay.db",
                       leaders=frozenset({key_for("leader").author}), source_url=SRC, **extra)


async def http(port, request_line, headers=()):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    lines = [request_line, "Host: localhost", "Connection: close", *headers, "", ""]
    writer.write("\r\n".join(lines).encode())
    raw = await reader.read()
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode().split("\r\n")
    got = {k.lower(): v for k, v in (line.split(": ", 1) for line in header_lines)}
    return int(status_line.split()[1]), got, body


UPGRADE = ("Upgrade: websocket", "Connection: Upgrade", "Sec-WebSocket-Version: 13",
           "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==")


async def open_group(port, g, leader):
    ws = await connect(f"ws://127.0.0.1:{port}/v1/groups/{g}/ws?x=1")
    hello = json.loads(await ws.recv())
    genesis = envelope_item(leader, 1, "group",
                            {"group_id": g, "name": "Crew", "commander": "L"},
                            stamp_at(time.time()))
    await ws.send(json.dumps(auth_frame(leader, g, hello["nonce"], genesis=genesis)))
    return ws, hello


def test_routes_over_the_wire(tmp_path):
    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()
        try:
            status, _, body = await http(port, "GET / HTTP/1.1")
            assert (status, body) == (200, greeting(SRC).encode())
            status, headers, body = await http(port, "GET /join HTTP/1.1")
            assert status == 200
            assert headers["content-security-policy"] == JOIN_CSP
            assert headers["content-length"] == str(len(body))
            assert body == JOIN_HTML.encode()
            assert (await http(port, "GET /nope HTTP/1.1"))[0] == 404
            assert (await http(port, f"GET /v1/groups/{V7}/ws HTTP/1.1"))[0] == 426
            assert (await http(port, "POST / HTTP/1.1"))[0] == 404
            status, _, _ = await http(port, "GET /v1/groups/not-a-uuid/ws HTTP/1.1", UPGRADE)
            assert status == 404
            assert server.relay.groups == {}
        finally:
            await server.stop()
    asyncio.run(scenario())


def test_a_session_over_the_wire(tmp_path):
    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()
        leader, g = key_for("leader"), new_group()
        try:
            ws, _ = await open_group(port, g, leader)
            assert json.loads(await ws.recv())["t"] == "have"
            await ws.send(json.dumps({"t": "have", "heads": {}}))
            assert json.loads(await ws.recv())["t"] == "synced"
            ws2 = await connect(f"ws://127.0.0.1:{port}/v1/groups/{g}/ws")
            hello = json.loads(await ws2.recv())
            await ws2.send(json.dumps(auth_frame(leader, g, hello["nonce"])))
            with pytest.raises(ConnectionClosed) as closed:
                await asyncio.wait_for(ws.recv(), 5)
            assert closed.value.rcvd.code == 4000
            await ws2.close()
        finally:
            await server.stop()
    asyncio.run(scenario())


def test_auth_timer_closes_an_idle_socket(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "AUTH_TIMEOUT", 0.2)

    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()
        try:
            ws = await connect(f"ws://127.0.0.1:{port}/v1/groups/{new_group()}/ws")
            assert json.loads(await ws.recv())["t"] == "hello"
            with pytest.raises(ConnectionClosed) as closed:
                await asyncio.wait_for(ws.recv(), 5)
            assert closed.value.rcvd.code == 4001
        finally:
            await server.stop()
    asyncio.run(scenario())


def test_a_1_2_mb_text_frame_closes_4008_not_1009(tmp_path):
    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()
        try:
            ws, _ = await open_group(port, new_group(), key_for("leader"))
            await ws.recv()
            await ws.send("é" * 600_000)
            with pytest.raises(ConnectionClosed) as closed:
                await asyncio.wait_for(ws.recv(), 5)
            assert closed.value.rcvd.code == 4008
        finally:
            await server.stop()
    asyncio.run(scenario())


def test_a_frame_over_the_wire_limit_closes_1009(tmp_path):
    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()
        try:
            ws, _ = await open_group(port, new_group(), key_for("leader"))
            await ws.recv()
            await ws.send("a" * (MAX_WIRE_BYTES + 1))
            with pytest.raises(ConnectionClosed) as closed:
                await asyncio.wait_for(ws.recv(), 5)
            assert closed.value.rcvd.code == 1009
        finally:
            await server.stop()
    asyncio.run(scenario())


def test_a_stalled_peer_does_not_delay_others(tmp_path):
    class Stalled:
        def __init__(self):
            self.sent, self.gate = [], asyncio.Event()

        async def send(self, text):
            await self.gate.wait()
            self.sent.append(text)

    class Recording:
        def __init__(self):
            self.sent = []

        async def send(self, text):
            self.sent.append(text)

    async def scenario():
        server = RelayServer(make_config(tmp_path))
        await server.start()
        b, c = Stalled(), Recording()
        pb, pc = Peer(b), Peer(c)
        try:
            server.dispatch([Send(SimpleNamespace(conn=pb), "x"),
                             Send(SimpleNamespace(conn=pc), "y"),
                             Send(SimpleNamespace(conn=pc), "z")])
            deadline = time.monotonic() + 1
            while c.sent != ["y", "z"] and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert c.sent == ["y", "z"]
            assert b.sent == []
        finally:
            for p in (pb, pc):
                await p.finish(False)
                assert p.writer.done()
            await server.stop()
    asyncio.run(scenario())


def test_a_second_close_for_a_closing_peer_is_a_no_op():
    class Closing:
        def __init__(self):
            self.closes = []

        async def close(self, code, reason):
            self.closes.append(code)

    async def scenario():
        ws = Closing()
        peer = Peer(ws)
        peer.enqueue((4000, "a"))
        peer.enqueue((1001, "b"))
        peer.enqueue("late")
        assert peer.queue.qsize() == 1
        await peer.finish(True)
        assert ws.closes == [4000]
    asyncio.run(scenario())


def test_run_stops_on_the_event_and_persists_statuses(tmp_path):
    config = make_config(tmp_path)

    async def scenario():
        stop, port_future = asyncio.Event(), asyncio.get_running_loop().create_future()
        task = asyncio.create_task(run(config, stop, started=port_future.set_result))
        port = await port_future
        leader, g = key_for("leader"), new_group()
        ws, _ = await open_group(port, g, leader)
        await ws.recv()
        await ws.send(json.dumps({"t": "have", "heads": {}}))
        await ws.recv()
        await ws.send(json.dumps(status_frame(leader, 0.7, at=stamp_at(time.time()))))
        await asyncio.sleep(0.2)
        stop.set()
        with pytest.raises(ConnectionClosed) as closed:
            await asyncio.wait_for(ws.recv(), 5)
        assert closed.value.rcvd.code == 1001
        assert closed.value.rcvd.reason == "relay shutting down"
        await asyncio.wait_for(task, 10)
        return g, leader.author

    g, author = asyncio.run(scenario())
    store = RelayStore(config.db)
    try:
        assert [a for a, _, _ in store.statuses(g)] == [author]
    finally:
        store.close()


def test_stop_refuses_new_connections_during_the_grace(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "SHUTDOWN_GRACE", 1.0)

    class Stuck:  # never answers its close, so the grace stays open
        async def close(self, code, reason):
            await asyncio.Event().wait()

    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()
        peer = Peer(Stuck())
        server._peers.add(peer)
        peer.enqueue((1001, "x"))
        stopping = asyncio.create_task(server.stop())
        await asyncio.sleep(0.2)
        assert not stopping.done()
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", port)
        await asyncio.wait_for(stopping, 5)
    asyncio.run(scenario())


def test_a_handshake_finishing_after_stop_began_is_closed_1001(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "SHUTDOWN_GRACE", 0.5)

    async def scenario():
        loop = asyncio.get_running_loop()
        server = RelayServer(make_config(tmp_path))
        real = server._process_request
        started = []

        def process_request(conn, req):
            if not started and req.path.startswith("/v1/"):
                # stop()'s first step runs in the iteration that completes this handshake
                started.append(asyncio.Task(server.stop(), loop=loop, eager_start=True))
            return real(conn, req)

        server._process_request = process_request
        port = await server.start()
        ws = await connect(f"ws://127.0.0.1:{port}/v1/groups/{new_group()}/ws")
        with pytest.raises(ConnectionClosed) as closed:
            await asyncio.wait_for(ws.recv(), 5)
        assert (closed.value.rcvd.code, closed.value.rcvd.reason) == (1001, "relay shutting down")
        await asyncio.wait_for(started[0], 5)
        assert server.relay.groups == {} and not server._peers
    asyncio.run(scenario())


def test_stop_returns_when_a_peer_never_takes_its_close(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "SHUTDOWN_GRACE", 0.3)

    class Stuck:
        aborted = False
        transport = SimpleNamespace(abort=lambda: setattr(Stuck, "aborted", True))

        async def close(self, code, reason):
            await asyncio.Event().wait()

    async def scenario():
        server = RelayServer(make_config(tmp_path))
        await server.start()
        peer = Peer(Stuck())
        server._peers.add(peer)
        peer.enqueue((1001, "x"))
        await asyncio.wait_for(server.stop(), 5)
        await asyncio.wait([peer.writer], timeout=1)
        assert Stuck.aborted and peer.writer.done()
    asyncio.run(scenario())


def test_a_send_over_the_write_timeout_aborts_the_connection(monkeypatch):
    monkeypatch.setattr(server_module, "WRITE_TIMEOUT", 0.1)

    class Stuck:
        aborted = False
        transport = SimpleNamespace(abort=lambda: setattr(Stuck, "aborted", True))

        async def send(self, text):
            await asyncio.Event().wait()

    async def scenario():
        peer = Peer(Stuck())
        peer.enqueue("x")
        await asyncio.wait_for(peer.writer, 2)
        assert Stuck.aborted
    asyncio.run(scenario())


def test_two_upgrade_headers_are_a_plain_get_not_a_500(tmp_path):
    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()
        try:
            status, _, body = await http(port, f"GET /v1/groups/{V7}/ws HTTP/1.1",
                                         (*UPGRADE, "Upgrade: websocket"))
            assert (status, body) == (426, b"expected a WebSocket upgrade\n")
        finally:
            await server.stop()
    asyncio.run(scenario())


def test_no_server_header(tmp_path):
    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()
        try:
            _, plain, _ = await http(port, "GET / HTTP/1.1")
            ws = await connect(f"ws://127.0.0.1:{port}/v1/groups/{V7}/ws")
            assert "server" not in plain
            assert "Server" not in ws.response.headers
            await ws.close()
        finally:
            await server.stop()
    asyncio.run(scenario())


def test_a_failing_connect_leaks_no_writer(tmp_path, monkeypatch):
    made = []
    real_peer = server_module.Peer
    monkeypatch.setattr(server_module, "Peer", lambda ws: made.append(real_peer(ws)) or made[-1])

    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()

        def boom(*args):
            raise RuntimeError("connect failed")
        monkeypatch.setattr(server.relay, "connect", boom)
        try:
            ws = await connect(f"ws://127.0.0.1:{port}/v1/groups/{V7}/ws")
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await asyncio.wait_for(ws.recv(), 5)
            await asyncio.sleep(0.1)
            assert len(made) == 1 and made[0].writer.done()
            assert not server._peers
        finally:
            await server.stop()
    asyncio.run(scenario())


# -- backpressure and the outbox cap -------------------------------------------------------

import logging  # noqa: E402

from edmrn.ext import crew_events as ev  # noqa: E402
from edmrn.ext import crew_protocol as proto  # noqa: E402
from relay_fakes import Harness  # noqa: E402


def big_visit(key, seq, size=3000):
    return envelope_item(key, seq, "visit", {"commander": "A", "system": "Sol",
                                             "address": "x" * size, "coords": None},
                         stamp_at(time.time() - 60))


async def eventually(predicate, timeout=15.0):
    end = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < end, "condition not met in time"
        await asyncio.sleep(0.01)


async def authed_ws(port, g, key, **extra):
    ws = await connect(f"ws://127.0.0.1:{port}/v1/groups/{g}/ws", max_queue=1, max_size=None)
    hello = json.loads(await ws.recv())
    await ws.send(proto.encode(auth_frame(key, g, hello["nonce"], **extra)))
    return ws


async def seeded_group(server_port, g, leader, count, size=3000):
    """The leader creates the group over the wire and stores `count` envelopes of ~`size` bytes."""
    ws = await authed_ws(server_port, g, leader, genesis=envelope_item(
        leader, 1, "group", {"group_id": g, "name": "Crew", "commander": "L"},
        stamp_at(time.time() - 60)))
    await ws.recv()  # have
    await ws.send(proto.encode({"t": "have", "heads": {}}))
    assert json.loads(await ws.recv())["t"] == "synced"
    items = [big_visit(leader, s, size) for s in range(2, count + 2)]
    for start in range(0, len(items), 50):
        await ws.send(proto.encode({"t": "evs", "items": items[start:start + 50]}))
    return ws


def peers_of(server):
    return list(server._peers)


def test_a_non_reading_member_flooding_have_stays_bounded(tmp_path):
    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()
        leader, g = key_for("leader"), new_group()
        try:
            first = await seeded_group(port, g, leader, 100)
            await eventually(lambda: server.relay.groups[g].heads.get(leader.author) == 101)
            first.transport.abort()
            await eventually(lambda: not server._peers)
            flood = await authed_ws(port, g, leader)
            have = proto.encode({"t": "have", "heads": {}})
            for _ in range(600):
                await flood.send(have)
                await asyncio.sleep(0.001)  # a have that is not replaced has its page built
            peer = peers_of(server)[0]
            await eventually(lambda: peer.queued_bytes >= server_module.OUTBOX_HIGH_WATER)
            await asyncio.sleep(0.3)  # the server must not go on queueing
            bound = server_module.OUTBOX_HIGH_WATER + 3 * proto.MAX_FRAME_BYTES
            assert peer.queued_bytes <= bound
            # still responsive to a second client
            other_g = new_group()
            other = await seeded_group(port, other_g, key_for("leader"), 0)
            await eventually(lambda: other_g in server.relay.groups)
            other.transport.abort()
            flood.transport.abort()
        finally:
            await server.stop()
    asyncio.run(scenario())


def test_a_non_reading_unauthenticated_garbage_flood_stays_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "OUTBOX_HIGH_WATER", 16 * 1024)

    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()
        try:
            ws = await connect(f"ws://127.0.0.1:{port}/v1/groups/{new_group()}/ws",
                               max_queue=1)
            await ws.recv()  # hello
            peer = peers_of(server)[0]
            end = time.monotonic() + 60
            while peer.queued_bytes < 16 * 1024:  # the socket buffers absorb the first MBs of errs
                assert time.monotonic() < end, "the outbox never filled"
                for _ in range(5000):
                    await ws.send("x")
            try:  # a server that goes on reading would queue all of this too
                await asyncio.wait_for(asyncio.gather(*[ws.send("x") for _ in range(20000)]), 2)
            except TimeoutError:
                pass  # the server stopped reading: TCP pushed back
            await asyncio.sleep(0.3)
            assert peer.queued_bytes <= 16 * 1024 + 1024
            ws.transport.abort()
        finally:
            await server.stop()
    asyncio.run(scenario())


def seeded_roster(tmp_path, names=("alice", "bob")):
    """A stored group with the leader and the named members; no server involved."""
    h = Harness(tmp_path / "relay.db")
    g = new_group()
    leader = key_for("leader")
    lc = h.create_group_and_upload(g, leader)
    for i, name in enumerate(names):
        token = f"{i + 1:02x}" * 32
        invite_id = h.invite(lc, g, leader, 2 + i, token=token)
        lc.take()
        h.authed(g, key_for(name), join=h.join_envelope(key_for(name), g, invite_id, 1, token=token))
    h.store.close()
    return g


def test_a_member_over_the_hard_cap_is_aborted_and_the_others_are_unaffected(
        tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(server_module, "OUTBOX_HIGH_WATER", 256 * 1024)
    monkeypatch.setattr(server_module, "OUTBOX_HARD_CAP", 1024 * 1024)
    g = seeded_roster(tmp_path)
    leader, alice, bob = key_for("leader"), key_for("alice"), key_for("bob")
    caplog.set_level(logging.INFO, logger="edmrn_relay")

    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()
        try:
            stuck = await authed_ws(port, g, alice)   # reads nothing
            reader = await authed_ws(port, g, bob)
            up = await authed_ws(port, g, leader)
            await asyncio.wait_for(reader.recv(), 5)  # have
            await eventually(lambda: len(server.relay.groups[g].sessions) == 3
                             and all(s.state == "member" for s in server.relay.groups[g].sessions))
            seen = 0

            async def drain():
                nonlocal seen
                try:
                    async for message in reader:
                        frame = json.loads(message)
                        if frame["t"] == "evs":
                            seen += len(frame["items"])
                except ConnectionClosed:
                    pass
            reading = asyncio.create_task(drain())
            seq, group = 10, server.relay.groups[g]
            alice_session = next(s for s in group.sessions if s.author == alice.author)
            while alice_session in group.sessions and seq < 3000:
                items = [big_visit(leader, seq + i, 5000) for i in range(50)]
                seq += 50
                await up.send(proto.encode({"t": "evs", "items": items}))
                await asyncio.sleep(0.01)
            await eventually(lambda: alice_session not in group.sessions, timeout=30)
            assert alice_session.state == "closed"
            assert sum("outbox over" in r.message for r in caplog.records) == 1
            sent = seq - 10
            await eventually(lambda: seen >= sent - 50, timeout=30)  # bob keeps receiving
            assert up.close_code is None and reader.close_code is None
            assert {s.author for s in group.sessions} == {leader.author, bob.author}
            reading.cancel()
            for ws in (stuck, reader, up):
                ws.transport.abort()
        finally:
            await server.stop()
    asyncio.run(scenario())


def test_a_slow_reader_below_the_cap_is_not_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "OUTBOX_HIGH_WATER", 64 * 1024)
    monkeypatch.setattr(server_module, "OUTBOX_HARD_CAP", 600 * 1024)

    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()
        leader, g = key_for("leader"), new_group()
        try:
            first = await seeded_group(port, g, leader, 100)
            await eventually(lambda: server.relay.groups[g].heads.get(leader.author) == 101)
            first.transport.abort()
            await eventually(lambda: not server._peers)
            ws = await authed_ws(port, g, leader)
            await ws.recv()
            synced = items = 0
            while synced < 5:
                await ws.send(proto.encode({"t": "have", "heads": {}}))
                while True:
                    frame = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    items += len(frame["items"]) if frame["t"] == "evs" else 0
                    await asyncio.sleep(0.002)
                    if frame["t"] == "synced":
                        synced += 1
                        break
            assert items == 5 * 101
            ws.transport.abort()
        finally:
            await server.stop()
    asyncio.run(scenario())


def test_the_hard_cap_leaves_room_for_the_largest_step():
    def b64len(n):
        return -(-n * 4 // 3)
    item = b64len(ev.MAX_ENVELOPE_BYTES) + b64len(proto.SIGNATURE_BYTES) + proto._ITEM_OVERHEAD
    page = proto.MAX_EVS_ITEMS * item + 64  # a catch-up page: 100 maximal items
    forward = proto.MAX_EVS_BYTES + item + 64  # an evs frame: its byte limit plus the item that spills
    assert server_module.OUTBOX_HIGH_WATER + page + forward < server_module.OUTBOX_HARD_CAP


def test_no_frame_is_dispatched_after_a_policy_close(tmp_path):
    class Scripted:
        request = SimpleNamespace(path=f"/v1/groups/{V7}/ws")

        def __init__(self):
            self.closed = []

        def __aiter__(self):
            async def frames():
                yield b"binary"  # a policy close (4008)
                yield json.dumps({"t": "have", "heads": {}})
            return frames()

        async def send(self, text):
            pass

        async def close(self, code, reason):
            self.closed.append(code)

    async def scenario():
        server = RelayServer(make_config(tmp_path))
        await server.start()
        try:
            calls = []
            real = server.relay.receive
            server.relay.receive = lambda session, message: calls.append(message) or real(
                session, message)
            ws = Scripted()
            await server._handler(ws)
            assert calls == [b"binary"]
            assert ws.closed == [4008]
        finally:
            await server.stop()
    asyncio.run(scenario())


def test_a_large_catch_up_is_paged_by_the_outbox_and_completes(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "OUTBOX_HIGH_WATER", 64 * 1024)

    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()
        leader, g = key_for("leader"), new_group()
        try:
            first = await seeded_group(port, g, leader, 300)
            await eventually(lambda: server.relay.groups[g].heads.get(leader.author) == 301)
            first.transport.abort()
            await eventually(lambda: not server._peers)
            ws = await authed_ws(port, g, leader)
            await ws.recv()
            await ws.send(proto.encode({"t": "have", "heads": {}}))
            seqs = []
            while True:
                frame = json.loads(await asyncio.wait_for(ws.recv(), 10))
                if frame["t"] == "synced":
                    break
                seqs += [json.loads(proto.unb64(i["e"], 1 << 20))["seq"] for i in frame["items"]]
            assert seqs == list(range(1, 302))
            ws.transport.abort()
        finally:
            await server.stop()
    asyncio.run(scenario())


def test_a_disconnect_mid_catch_up_leaves_no_task_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "OUTBOX_HIGH_WATER", 16 * 1024)

    async def scenario():
        server = RelayServer(make_config(tmp_path))
        port = await server.start()
        leader, g = key_for("leader"), new_group()
        try:
            first = await seeded_group(port, g, leader, 300)
            await eventually(lambda: server.relay.groups[g].heads.get(leader.author) == 301)
            first.transport.abort()
            await eventually(lambda: not server._peers)
            baseline = len(asyncio.all_tasks())
            ws = await authed_ws(port, g, leader)
            await ws.recv()
            await ws.send(proto.encode({"t": "have", "heads": {}}))
            await ws.recv()  # the first page; the rest waits for room
            session = server.relay.groups[g].sessions[0]
            assert session.catchup is not None
            ws.transport.abort()
            await eventually(lambda: not server._peers and session.catchup is None)
            await eventually(lambda: len(asyncio.all_tasks()) <= baseline)
        finally:
            await server.stop()
    asyncio.run(scenario())
