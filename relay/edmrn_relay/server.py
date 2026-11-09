"""asyncio WebSocket server of the crew relay (ADR-0009).

The engine decides; this module moves bytes. Each connection has one FIFO outbox drained by one
writer task, so frames reach every client in engine order and a stalled peer delays only itself.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from edmrn.ext import crew_events as ev
from edmrn.ext import crew_protocol as proto

from . import engine
from .config import RelayConfig
from .engine import Action, Close, Relay, Send
from .join_page import JOIN_HEADERS, JOIN_HTML, greeting
from .store import RelayStore

log = logging.getLogger("edmrn_relay")

MAX_WIRE_BYTES = 2 * proto.MAX_FRAME_BYTES
_TEXT = (("Content-Type", "text/plain; charset=utf-8"),)
_PREFIX, _SUFFIX = "/v1/groups/", "/ws"
SHUTDOWN_GRACE = 15.0  # seconds to let queued closes reach the peers
WRITE_TIMEOUT = 60.0  # a peer that does not take a frame in this time is dropped
# A peer whose queued outbound bytes reach the high-water mark is not read from until its writer has
# drained below it, so TCP pushes back on a sender that reads nothing. Past the hard cap (reachable
# only through frames other members cause, such as forwards) the connection is aborted. The cap
# exceeds the high-water mark by far more than any step can add: the largest frame is an `evs` of
# 512 KiB (proto.MAX_EVS_BYTES) plus one maximal item, a catch-up page is at most 100 maximal items
# (about 2.2 MB), and a page is built only while the queue is below the high-water mark.
OUTBOX_HIGH_WATER = 1 << 20
OUTBOX_HARD_CAP = 8 << 20


@dataclass(frozen=True)
class HttpReply:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


def group_of(path: str) -> str | None:
    path = path.split("?", 1)[0]
    if not (path.startswith(_PREFIX) and path.endswith(_SUFFIX)):
        return None
    group_id = path[len(_PREFIX):-len(_SUFFIX)]
    return group_id if ev.is_uuid7(group_id) else None


def route(method: str, path: str, upgrade: bool, source_url: str) -> HttpReply | None:
    """None: go on with the WebSocket handshake."""
    path = path.split("?", 1)[0]
    if method == "GET":
        if path == "/":
            return HttpReply(200, _TEXT, greeting(source_url).encode())
        if path == "/join":
            return HttpReply(200, JOIN_HEADERS, JOIN_HTML.encode())
        if group_of(path) is not None:
            if upgrade:
                return None
            return HttpReply(426, _TEXT, b"expected a WebSocket upgrade\n")
    return HttpReply(404, _TEXT, b"not found\n")


class Peer:
    """One connection's outbox and its writer task. Needs a running loop."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.queue: asyncio.Queue[str | tuple[int, str]] = asyncio.Queue()
        self.queued_bytes = 0  # frames queued or being sent; frames are ASCII, so chars are bytes
        self.room = asyncio.Event()  # set while queued_bytes is below the high-water mark
        self.room.set()
        self._closing = False
        self.writer = asyncio.get_running_loop().create_task(self._write())

    def enqueue(self, item: str | tuple[int, str]) -> None:
        if self._closing:
            return  # a close is queued already: later items, a second close included, are no-ops
        if isinstance(item, tuple):
            self._closing = True
        else:
            self.queued_bytes += len(item)
            if self.queued_bytes > OUTBOX_HARD_CAP:
                self._overflow()
                return
            if self.queued_bytes >= OUTBOX_HIGH_WATER:
                self.room.clear()
        self.queue.put_nowait(item)

    def _overflow(self) -> None:
        """The peer does not read what is queued for it: drop the queue and the connection. The
        handler then ends the session through the normal disconnect path."""
        log.info("outbox over %d bytes, dropping the connection", OUTBOX_HARD_CAP)
        self._closing = True
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queued_bytes = 0
        self.room.set()  # a handler waiting for room must see the connection end
        self.abort()

    def _sent(self, size: int) -> None:
        self.queued_bytes = max(0, self.queued_bytes - size)
        if self.queued_bytes < OUTBOX_HIGH_WATER:
            self.room.set()

    async def _write(self) -> None:
        try:
            while True:
                item = await self.queue.get()
                if isinstance(item, tuple):
                    await asyncio.wait_for(self.ws.close(*item), WRITE_TIMEOUT)
                    return
                await asyncio.wait_for(self.ws.send(item), WRITE_TIMEOUT)
                self._sent(len(item))
        except ConnectionClosed:
            return
        except TimeoutError:
            log.info("write timeout, dropping the connection")
            self.abort()
        finally:
            self.room.set()  # a handler waiting for room must not outlive the writer

    def abort(self) -> None:
        """Drop the connection without a closing handshake."""
        transport = getattr(self.ws, "transport", None)
        if transport is not None:
            transport.abort()

    async def finish(self, closing: bool) -> None:
        """closing: wait for the queued close to go out; otherwise cancel the writer."""
        if closing:
            await asyncio.wait([self.writer])
            return
        self.writer.cancel()
        await asyncio.wait([self.writer])


class RelayServer:
    store: RelayStore
    relay: Relay

    def __init__(self, config: RelayConfig, *, now: Callable[[], float] = time.time,
                 token: Callable[[], bytes] | None = None) -> None:
        self.config = config
        self._now = now
        self._token = token
        self.port = 0
        self._server: Any = None
        self._peers: set[Peer] = set()
        self._stopping = False

    # -- HTTP ----------------------------------------------------------------------------

    def _process_request(self, connection: Any, request: Any):
        upgrades = request.headers.get_all("Upgrade")  # two Upgrade headers are no upgrade
        reply = route(getattr(request, "method", "GET"), request.path,
                      len(upgrades) == 1 and upgrades[0].lower() == "websocket",
                      self.config.source_url)
        if reply is None:
            return None
        headers = Headers([*reply.headers, ("Content-Length", str(len(reply.body))),
                           ("Connection", "close")])
        return Response(reply.status, HTTPStatus(reply.status).phrase, headers, reply.body)

    # -- WebSocket -----------------------------------------------------------------------

    def dispatch(self, actions: list[Action]) -> None:
        for action in actions:
            peer = action.session.conn
            if isinstance(action, Send):
                peer.enqueue(action.text)
            elif isinstance(action, Close):
                peer.enqueue((action.code, action.reason))

    async def _handler(self, ws: Any) -> None:
        if self._stopping:  # a handshake that finished after stop() began
            await ws.close(engine.CLOSE_GOING_AWAY, engine.SHUTDOWN_REASON)
            return
        peer = Peer(ws)
        self._peers.add(peer)
        try:
            session = timer = None
            try:
                session, actions = self.relay.connect(group_of(ws.request.path), peer)
                self.dispatch(actions)
                timer = asyncio.get_running_loop().call_later(
                    engine.AUTH_TIMEOUT, lambda: self.dispatch(self.relay.expire_auth(session)))
                try:
                    async for message in ws:
                        self.dispatch(self.relay.receive(session, message))
                        if session.closing:
                            break
                        await peer.room.wait()  # read the next frame only once the peer drained
                except ConnectionClosed:
                    pass
            finally:
                if timer is not None:
                    timer.cancel()
                if session is not None:
                    self.relay.disconnect(session)
                await peer.finish(session is not None and session.closing)
        finally:
            self._peers.discard(peer)

    # -- lifecycle -----------------------------------------------------------------------

    async def start(self) -> int:
        self.store = RelayStore(self.config.db)
        kwargs = {} if self._token is None else {"token": self._token}
        self.relay = Relay(self.store, self.config, now=self._now, **kwargs)
        self._server = await serve(self._handler, self.config.host, self.config.port,
                                   process_request=self._process_request,
                                   max_size=MAX_WIRE_BYTES, server_header=None)
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("listening host=%s port=%d db=%s leaders=%d", self.config.host, self.port,
                 self.config.db, len(self.config.leaders))
        return self.port

    async def stop(self) -> None:
        self._stopping = True
        self._server.close(close_connections=False)  # no new sessions after the shutdown
        await asyncio.sleep(0)  # lets websockets' close task stop the listener
        self.dispatch(self.relay.shutdown())
        peers = list(self._peers)
        if peers:
            await asyncio.wait([p.writer for p in peers], timeout=SHUTDOWN_GRACE)
        for peer in peers:
            if not peer.writer.done():  # a peer that does not drain
                peer.writer.cancel()
                peer.abort()
        await self._server.wait_closed()
        self.store.close()
