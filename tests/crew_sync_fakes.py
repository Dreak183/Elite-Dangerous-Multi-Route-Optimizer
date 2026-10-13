"""A scripted relay socket for crew sync tests: no threads, no network.

`FakeSocket.recv` hands out queued frames; when the queue is empty it raises SocketClosed(1006), so
CrewSync.connect_once() returns in the test thread. `on_send(sock, frame)` may queue replies;
`on_recv(sock)` runs at the start of every recv and may queue frames or change the store.

The Tk root (`tests/fakes.Root`) and the polling helper (`journal_helpers.wait_for`) are reused by
the tests, not copied here.
"""
import json
import time
from collections import deque

from edmrn.ext import crew_protocol as proto
from edmrn.ext.crew_sync import SocketClosed

NONCE = proto.b64(bytes(range(32)))


class FakeSocket:
    def __init__(self, on_send=None, frames=(), on_recv=None):
        self.incoming = deque(proto.encode(f) for f in frames)
        self.sent = []
        self.closed = None
        self.on_send = on_send
        self.on_recv = on_recv

    def push(self, frame):
        self.incoming.append(proto.encode(frame))

    def push_close(self, code, reason=""):
        self.incoming.append(SocketClosed(code, reason))

    def send(self, text):
        frame = json.loads(text)
        self.sent.append(frame)
        if self.on_send:
            self.on_send(self, frame)

    def recv(self, timeout):
        if self.on_recv:
            self.on_recv(self)
        if not self.incoming:
            raise SocketClosed(1006, "script ended")
        item = self.incoming.popleft()
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self, code=1000, reason=""):
        self.closed = code

    def sent_of(self, t):
        return [f for f in self.sent if f["t"] == t]


class BlockingSocket(FakeSocket):
    """Like FakeSocket, but an empty queue idles (TimeoutError) until close(), for thread tests."""

    def recv(self, timeout):
        if self.on_recv:
            self.on_recv(self)
        if self.incoming:
            item = self.incoming.popleft()
            if isinstance(item, BaseException):
                raise item
            return item
        if self.closed is not None:
            raise SocketClosed(self.closed, "closed by us")
        time.sleep(0.01)
        raise TimeoutError()


def relay(heads=None, synced=True, extra=()):
    """on_send that answers auth like the relay: have (relay heads), synced, then `extra` frames."""
    def on_send(sock, frame):
        if frame["t"] == "auth":
            sock.push({"t": "have", "heads": dict(heads or {})})
            for f in extra:
                sock.push(f)
            if synced:
                sock.push({"t": "synced"})
    return on_send


def hello():
    return {"t": "hello", "v": 1, "source": "", "nonce": NONCE}


class Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


class RecordingStop:
    """Stands in for CrewSync._stop: records waits and stops after `limit` of them."""

    def __init__(self, limit):
        self.limit, self.waits = limit, []

    def is_set(self):
        return len(self.waits) >= self.limit

    def set(self):
        self.limit = 0

    def clear(self):
        pass

    def wait(self, seconds):
        self.waits.append(seconds)
        return self.is_set()
