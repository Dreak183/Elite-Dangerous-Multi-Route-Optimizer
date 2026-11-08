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
