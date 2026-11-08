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
        self._closing = False
        self.writer = asyncio.get_running_loop().create_task(self._write())

    def enqueue(self, item: str | tuple[int, str]) -> None:
        if self._closing:
            return  # a close is queued already: later items, a second close included, are no-ops
        if isinstance(item, tuple):
            self._closing = True
        self.queue.put_nowait(item)

    async def _write(self) -> None:
        try:
            while True:
                item = await self.queue.get()
                if isinstance(item, tuple):
                    await self.ws.close(*item)
                    return
                await self.ws.send(item)
        except ConnectionClosed:
            return

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

    # -- HTTP ----------------------------------------------------------------------------

    def _process_request(self, connection: Any, request: Any):
        reply = route(getattr(request, "method", "GET"), request.path,
                      request.headers.get("Upgrade", "").lower() == "websocket",
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
        peer = Peer(ws)
        self._peers.add(peer)
        session, actions = self.relay.connect(group_of(ws.request.path), peer)
        timer = None
        try:
            self.dispatch(actions)
            timer = asyncio.get_running_loop().call_later(
                engine.AUTH_TIMEOUT, lambda: self.dispatch(self.relay.expire_auth(session)))
            try:
                async for message in ws:
                    self.dispatch(self.relay.receive(session, message))
                    if session.closing:
                        break
            except ConnectionClosed:
                pass
        finally:
            if timer is not None:
                timer.cancel()
            self.relay.disconnect(session)
            await peer.finish(session.closing)
            self._peers.discard(peer)

    # -- lifecycle -----------------------------------------------------------------------

    async def start(self) -> int:
        self.store = RelayStore(self.config.db)
        kwargs = {} if self._token is None else {"token": self._token}
        self.relay = Relay(self.store, self.config, now=self._now, **kwargs)
        self._server = await serve(self._handler, self.config.host, self.config.port,
                                   process_request=self._process_request,
                                   max_size=MAX_WIRE_BYTES)
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("listening host=%s port=%d db=%s leaders=%d", self.config.host, self.port,
                 self.config.db, len(self.config.leaders))
        return self.port

    async def stop(self) -> None:
        self.dispatch(self.relay.shutdown())
        writers = [p.writer for p in self._peers]
        if writers:
            await asyncio.wait(writers, timeout=SHUTDOWN_GRACE)
        self._server.close(close_connections=False)
        await self._server.wait_closed()
        self.store.close()
