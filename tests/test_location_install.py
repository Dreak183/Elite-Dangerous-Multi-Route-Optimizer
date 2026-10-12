import tkinter as tk
from types import SimpleNamespace

import pytest

from edmrn.ext.location import LocationState, format_location, install
from edmrn.journal import JournalMonitor
from journal_helpers import append_events, event, journal_file, wait_for


class FakeVar:
    """Mimics tk.StringVar: a write trace may set the variable without recursing."""

    def __init__(self, value=""):
        self._value, self._traces, self._in_trace = value, [], False

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        if self._in_trace:
            return
        self._in_trace = True
        try:
            for callback in list(self._traces):
                callback("cmdr_location", "", "write")
        finally:
            self._in_trace = False

    def trace_add(self, mode, callback):
        self._traces.append(callback)


class ImmediateRoot:
    def after(self, ms, fn, *args):
        fn(*args)


class DeadRoot:
    def after(self, ms, fn, *args):
        raise RuntimeError("main thread is not in main loop")


@pytest.fixture(autouse=True)
def listeners(monkeypatch):
    fresh = []
    monkeypatch.setattr(JournalMonitor, "event_listeners", fresh)
    return fresh


def make_app(journal_dir, root=None):
    return SimpleNamespace(root=root or ImmediateRoot(),
                           cmdr_location=FakeVar("[Location: Unknown]"),
                           config=SimpleNamespace(journal_path=str(journal_dir)))


DOCKED = event("Location", StarSystem="Sol", SystemAddress=1, StarPos=[0, 0, 0], Docked=True,
               StationName="Abraham Lincoln", MarketID=1, StationType="Orbis")


def test_format_location():
    assert format_location(LocationState()) is None
    assert format_location(LocationState(system="Sol")) == "Sol"
    assert format_location(LocationState(system="Sol", station="Abraham Lincoln", body="Earth")) == "Sol · Abraham Lincoln"
    assert format_location(LocationState(system="Sol", body="Earth", body_type="Planet")) == "Sol · Earth"
    assert format_location(LocationState(system="Sol", body="Sol", body_type="Star")) == "Sol"
    assert format_location(LocationState(system="Sol", body="Sol A", body_type="Star")) == "Sol"


def test_install_cold_start_sets_header(tmp_path):
    append_events(journal_file(tmp_path), DOCKED)
    app = make_app(tmp_path)

    install(app)

    assert wait_for(lambda: app.cmdr_location.get() == "Sol · Abraham Lincoln")


def test_install_is_idempotent(tmp_path, listeners):
    app = make_app(tmp_path)
    first = install(app)
    assert install(app) is first
    assert app.location_tracker is first
    assert len(listeners) == 1


def test_live_event_through_listener_updates_header(tmp_path, listeners):
    app = make_app(tmp_path)
    install(app)

    listeners[0](event("FSDJump", StarSystem="Alpha", StarPos=[1, 2, 3]))

    assert app.cmdr_location.get() == "Alpha"


def test_upstream_write_is_overridden(tmp_path, listeners):
    app = make_app(tmp_path)
    install(app)
    listeners[0](DOCKED)

    app.cmdr_location.set("Sol")          # what upstream's startup scan writes

    assert app.cmdr_location.get() == "Sol · Abraham Lincoln"


def test_unknown_state_leaves_upstream_label(tmp_path):
    app = make_app(tmp_path)
    install(app)
    app.cmdr_location.set("[Location: Unknown]")
    assert app.cmdr_location.get() == "[Location: Unknown]"


def test_dead_root_does_not_raise_into_monitor(tmp_path, listeners):
    app = make_app(tmp_path, root=DeadRoot())
    install(app)
    listeners[0](event("FSDJump", StarSystem="Alpha"))   # must not raise
    assert app.location_tracker.state.system == "Alpha"


def test_real_tk_trace_reasserts(tmp_path, listeners):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no Tk display")
    root.withdraw()
    try:
        app = SimpleNamespace(root=root, cmdr_location=tk.StringVar(root, value="[Location: Unknown]"),
                              config=SimpleNamespace(journal_path=str(tmp_path)))
        install(app)
        listeners[0](DOCKED)
        assert wait_for(lambda: (root.update(), app.cmdr_location.get())[1] == "Sol · Abraham Lincoln")
        app.cmdr_location.set("Sol")
        assert app.cmdr_location.get() == "Sol · Abraham Lincoln"
    finally:
        root.destroy()


def test_tracked_system_helper():
    from edmrn.ext.location import LocationTracker, tracked_system
    assert tracked_system(SimpleNamespace()) is None
    tracker = LocationTracker()
    assert tracked_system(SimpleNamespace(location_tracker=tracker)) is None
    tracker.feed(DOCKED)
    assert tracked_system(SimpleNamespace(location_tracker=tracker)) == "Sol"


def test_install_uses_live_monitor_directory(tmp_path):
    append_events(journal_file(tmp_path), DOCKED)
    app = make_app(tmp_path / "elsewhere")
    app.journal_monitor = SimpleNamespace(journal_path=str(tmp_path))

    install(app)

    assert wait_for(lambda: app.cmdr_location.get() == "Sol · Abraham Lincoln")


def test_app_startup_monitor_receives_the_configured_journal_path():
    """app.py's first JournalMonitor(...) in EDMRN_App.__init__ must pass the manual path.

    Source-level check: importing edmrn.app needs Tk and replaces sys.excepthook.
    """
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).parent.parent / "edmrn" / "app.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "EDMRN_App")
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    calls = [n for n in ast.walk(init) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "JournalMonitor"]
    calls.sort(key=lambda n: n.lineno)
    assert calls, "no JournalMonitor( call in __init__"
    keywords = {k.arg for k in calls[0].keywords}
    assert "manual_journal_path" in keywords
