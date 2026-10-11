"""Fakes shared by the API-hardening tests: responses, scripted transports, clocks, a Tk root.

Imported by test modules only, so tests/conftest.py has redirected the home folder before any
edmrn module loads (docs/architecture/patterns.md).
"""
import threading
from types import SimpleNamespace


class FakeResponse:
    def __init__(self, status_code, body=None, text=None, reason=""):
        self.status_code = status_code
        self._body = body
        self.reason = reason
        self.text = text if text is not None else ("" if body is None else str(body))

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class Script:
    """Callable fake for `transport` or `call`: records each call and replays queued items.

    An item that is an exception is raised; anything else is returned.
    """

    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if not self.items:
            raise AssertionError(f"unexpected call {args} {kwargs}")
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class Sleeps(list):
    def __call__(self, seconds):
        self.append(seconds)


class Clock:
    """Fake monotonic clock; its sleep() advances the time."""

    def __init__(self, now=1000.0):
        self.now = now
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Root:
    """Fake Tk root: after() queues callbacks, run() drains them like the Tk loop does."""

    def __init__(self):
        self.queue = []

    def after(self, ms, fn=None):
        self.queue.append(fn)

    def run(self):
        while self.queue:
            self.queue.pop(0)()

    def update_idletasks(self):
        pass


class SyncThread:
    """Stand-in for threading.Thread that runs its target inside start()."""

    def __init__(self, target=None, daemon=None, args=(), kwargs=None):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


def sync_threading():
    """A replacement for a module's `threading` name whose threads run synchronously."""
    return SimpleNamespace(Thread=SyncThread, Lock=threading.Lock,
                           current_thread=threading.current_thread, main_thread=threading.main_thread)


class Var:
    """StringVar/BooleanVar/AutocompleteEntry stand-in; records reads into an optional log."""

    def __init__(self, value="", log=None, name=None):
        self.value, self.log, self.name = value, log, name

    def get(self, *args):
        if self.log is not None:
            self.log.append(("get", self.name))
        return self.value

    def set(self, value):
        self.value = value


class Widget:
    """Button/label stand-in: configure() keeps the latest state and the history."""

    def __init__(self, **state):
        self.state = dict(state)
        self.history = []

    def configure(self, **kwargs):
        self.state.update(kwargs)
        self.history.append(kwargs)


class Text:
    """CTkTextbox stand-in for the Galaxy output box."""

    def __init__(self):
        self.content = ""

    def configure(self, **kwargs):
        pass

    def delete(self, start, end):
        self.content = ""

    def insert(self, index, text):
        self.content = text + self.content if index == "1.0" else self.content + text

    def see(self, index):
        pass
