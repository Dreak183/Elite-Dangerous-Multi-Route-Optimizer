"""Location buttons (docs/superpowers/specs/2026-11-17-location-fill-design.md).

Covers system fields (L1-L6, L11) and coordinate fields (L7-L9); the hook functions and hook lines
are in test_location_fill_hooks.py.
"""
import tkinter as tk
from types import SimpleNamespace

import pytest

ctk = pytest.importorskip("customtkinter")

from edmrn.ext.location import LocationState  # noqa: E402
from edmrn.ext.location_fill import (  # noqa: E402
    BUTTON_TEXT, BUTTON_WIDTH, CHECKING, NO_COORDINATES, UNKNOWN_LOCATION, SystemFieldFill,
    add_coords_button, current_location, fill_coordinates, format_coord)
from edmrn.ext.start_check import StartCheck  # noqa: E402

SYSTEM = "Blae Phoe XT-A e1459"
COORDS = (768.375, -565.90625, 14325.34375)
KNOWN_COLOR = "#4CAF50"
WARN_COLOR = "#FFA500"


class ImmediateRoot:
    def after(self, ms, fn, *args):
        fn(*args)


@pytest.fixture
def _tk_root(tk_root):
    """A per-test container on the shared root: its leftovers die with it."""
    container = ctk.CTkFrame(tk_root)
    container.pack()
    yield container
    container.destroy()


class Rig:
    def __init__(self, root, state=None, results=(), check_spansh=True, after_fill=None):
        from edmrn.autocomplete_entry import AutocompleteEntry
        self.field = AutocompleteEntry(root)
        self.field.pack()
        self.jobs = []
        self.calls = []
        self.results = list(results)
        state = LocationState(system=SYSTEM, coords=COORDS) if state is None else state
        self.app = SimpleNamespace(root=ImmediateRoot(), location_tracker=SimpleNamespace(state=state))
        self.fill = SystemFieldFill(self.app, self.field, check_spansh=check_spansh,
                                    after_fill=after_fill, check=self._check, runner=self.jobs.append)

    def _check(self, system, coords):
        self.calls.append((system, coords))
        return self.results.pop(0)

    @property
    def text(self):
        return self.fill.hint.cget("text")

    @property
    def visible(self):
        return self.fill.hint.winfo_manager() == "grid"

    @property
    def color(self):
        return self.fill.hint.cget("text_color")


@pytest.fixture
def rig(_tk_root):
    made = []

    def make(**kw):
        r = Rig(_tk_root, **kw)
        made.append(r)
        return r
    yield make
    for r in made:
        try:
            r.field.destroy()
        except Exception:
            pass


def test_format_coord():
    assert format_coord(768.375) == "768.375"
    assert format_coord(-565.90625) == "-565.90625"
    assert format_coord(0) == "0"
    assert format_coord(-0.0) == "0"
    assert format_coord(-0.000001) == "0"
    assert format_coord(19808.125) == "19808.125"
    assert format_coord(12.0) == "12"


def test_current_location():
    assert current_location(SimpleNamespace(location_tracker=SimpleNamespace(state=LocationState()))) is None
    assert current_location(SimpleNamespace()) is None
    state = LocationState(system=SYSTEM)
    assert current_location(SimpleNamespace(location_tracker=SimpleNamespace(state=state))) is state


def test_layout(rig):
    r = rig()
    assert r.field.entry.grid_info()["row"] == 0 and r.field.entry.grid_info()["column"] == 0
    info = r.fill.button.grid_info()
    assert info["row"] == 0 and info["column"] == 1
    assert r.fill.button.cget("text") == BUTTON_TEXT == "📍"
    assert not r.visible
    assert r.fill.hint.cget("wraplength") == 300
    assert BUTTON_WIDTH == 28
    assert r.fill.button.cget("width") == 28
    assert r.fill.button.cget("font").cget("size") == 15


class FakeThemeManager:
    def __init__(self):
        self.calls = []

    def apply_button_theme(self, button, button_type):
        self.calls.append((button, button_type))


def test_hint_wraps_to_field_width(rig):
    r = rig()
    scaling = r.field._get_widget_scaling()
    r.fill._on_field_configure(SimpleNamespace(width=240))
    assert r.fill.hint.cget("wraplength") == max(80, int(240 / scaling) - 8)
    r.fill._on_field_configure(SimpleNamespace(width=50))
    assert r.fill.hint.cget("wraplength") == 80


def test_field_configure_is_bound(rig):
    # a canvas <Configure> does not fire while the root is withdrawn, so pin the binding script
    r = rig()
    assert "_on_field_configure" in r.field._canvas.bind("<Configure>")


def test_system_button_follows_theme(_tk_root):
    from edmrn.autocomplete_entry import AutocompleteEntry
    field = AutocompleteEntry(_tk_root)
    field.pack()
    theme = FakeThemeManager()
    app = SimpleNamespace(root=ImmediateRoot(), theme_manager=theme,
                          location_tracker=SimpleNamespace(state=LocationState()))
    try:
        fill = SystemFieldFill(app, field, check_spansh=False)
        assert theme.calls == [(fill.button, "secondary")]
    finally:
        field.destroy()


def test_theme_failure_is_ignored(_tk_root):
    from edmrn.autocomplete_entry import AutocompleteEntry
    field = AutocompleteEntry(_tk_root)
    field.pack()

    class Broken:
        def apply_button_theme(self, *a):
            raise RuntimeError("boom")
    app = SimpleNamespace(root=ImmediateRoot(), theme_manager=Broken(),
                          location_tracker=SimpleNamespace(state=LocationState()))
    try:
        SystemFieldFill(app, field, check_spansh=False)
    finally:
        field.destroy()


def test_unknown_location(rig):
    r = rig(state=LocationState())
    r.field.set("Sol")
    r.fill.fill()
    assert r.field.get() == "Sol"
    assert r.text == UNKNOWN_LOCATION and r.visible and r.color == WARN_COLOR
    assert r.jobs == []


def test_unknown_location_hides_on_change(rig):
    r = rig(state=LocationState())
    r.fill.fill()
    r.field.set("x")
    assert not r.visible


def test_fill_replaces_text(rig):
    got = []
    r = rig(check_spansh=False, after_fill=got.append)
    r.field.set("Sol")
    r.fill.fill()
    assert r.field.get() == SYSTEM
    assert not r.visible
    assert got == [SYSTEM]
    assert r.jobs == []


def test_check_known(rig):
    r = rig(results=[StartCheck(SYSTEM, "✓ known", True)])
    r.fill.fill()
    assert r.text == CHECKING.format(system=SYSTEM) == f"Checking '{SYSTEM}' at Spansh..."
    assert r.visible and r.color == "gray60"
    r.jobs[0]()
    assert r.text == "✓ known" and r.color == KNOWN_COLOR and r.visible
    assert r.field.get() == SYSTEM


def test_check_substitutes(rig):
    r = rig(results=[StartCheck("Blae Phoe SS-B d751", "⚠ nearest", False)])
    r.fill.fill()
    r.jobs[0]()
    assert r.field.get() == "Blae Phoe SS-B d751"
    assert r.visible and r.text == "⚠ nearest" and r.color == WARN_COLOR
    assert r.calls == [(SYSTEM, COORDS)]


def test_stale_result_is_dropped(rig):
    # results are handed out in the order the jobs run: the newer job (run first) gets the known
    # result, the older job (run second) a substitution that must be dropped as stale.
    r = rig(results=[StartCheck(SYSTEM, "✓ known", True),
                     StartCheck("Other", "⚠ substituted", False)])
    r.fill.fill()
    r.fill.fill()
    assert len(r.jobs) == 2
    r.jobs[1]()
    r.jobs[0]()
    assert r.field.get() == SYSTEM
    assert r.text == "✓ known" and r.color == KNOWN_COLOR


def test_result_dropped_after_typing(rig):
    r = rig(results=[StartCheck("Other", "⚠ substituted", False)])
    r.fill.fill()
    r.field.set("Sol")
    r.jobs[0]()
    assert r.field.get() == "Sol"
    assert not r.visible


def test_hint_clears_when_text_changes(rig):
    r = rig(results=[StartCheck(SYSTEM, "✓ known", True)])
    r.fill.fill()
    r.jobs[0]()
    assert r.visible
    r.field.set("Colonia")
    assert not r.visible


def test_result_after_destroy_is_ignored(rig):
    r = rig(results=[StartCheck(SYSTEM, "✓ known", True)])
    queued = []

    class QueueRoot:
        def after(self, ms, fn, *args):
            queued.append((fn, args))
    r.app.root = QueueRoot()
    r.fill.fill()
    r.jobs[0]()
    assert len(queued) == 1
    r.field.destroy()
    fn, args = queued[0]
    fn(*args)   # _apply on a destroyed field must not raise


def test_job_exception_shows_a_sentence(rig):
    r = rig(results=[])   # check raises IndexError
    r.fill.fill()
    r.jobs[0]()
    assert r.text == f"⚠ Could not check '{SYSTEM}' at Spansh: Unexpected error talking to Spansh."
    assert r.color == WARN_COLOR and r.visible


def test_fill_hides_previous_hint_without_check(rig):
    r = rig(state=LocationState(), check_spansh=False)
    r.field.set(SYSTEM)
    r.fill.fill()
    assert r.text == UNKNOWN_LOCATION and r.visible
    r.app.location_tracker.state = LocationState(system=SYSTEM, coords=COORDS)
    r.fill.fill()
    assert not r.visible


def test_scheduling_after_shutdown_is_ignored(rig):
    r = rig(results=[StartCheck(SYSTEM, "✓ known", True)])

    class DeadRoot:
        def after(self, *a):
            raise RuntimeError("main thread is not in main loop")
    r.app.root = DeadRoot()
    r.fill.fill()
    r.jobs[0]()


def _coord_app(state=None):
    state = LocationState(system=SYSTEM, coords=COORDS) if state is None else state
    return SimpleNamespace(root=ImmediateRoot(), location_tracker=SimpleNamespace(state=state))


def test_coordinates_fill(_tk_root):
    frame = ctk.CTkFrame(_tk_root)
    entries = tuple(ctk.CTkEntry(frame) for _ in range(3))
    assert fill_coordinates(_coord_app(), entries) is True
    assert [e.get() for e in entries] == ["768.375", "-565.90625", "14325.34375"]
    frame.destroy()


def test_coordinates_replace_text(_tk_root):
    frame = tk.Frame(_tk_root)
    entries = tuple(tk.Entry(frame) for _ in range(3))
    name = tk.Entry(frame)
    for e in entries:
        e.insert(0, "1")
    name.insert(0, "old")
    assert fill_coordinates(_coord_app(), entries, name) is True
    assert [e.get() for e in entries] == ["768.375", "-565.90625", "14325.34375"]
    assert name.get() == SYSTEM
    frame.destroy()


def test_coordinates_without_location(_tk_root):
    frame = tk.Frame(_tk_root)
    entries = tuple(tk.Entry(frame) for _ in range(3))
    entries[0].insert(0, "5")
    warned = []
    assert fill_coordinates(_coord_app(LocationState()), entries, warn=warned.append) is False
    assert warned == [UNKNOWN_LOCATION]
    assert [e.get() for e in entries] == ["5", "", ""]
    frame.destroy()


def test_coordinates_without_coords(_tk_root):
    frame = tk.Frame(_tk_root)
    entries = tuple(tk.Entry(frame) for _ in range(3))
    warned = []
    app = _coord_app(LocationState(system="Docked Only", coords=None))
    assert fill_coordinates(app, entries, warn=warned.append) is False
    assert warned == ["Current coordinates unknown - the journal has no position for 'Docked Only' yet."]
    assert warned == [NO_COORDINATES.format(system="Docked Only")]
    assert [e.get() for e in entries] == ["", "", ""]
    frame.destroy()


def test_coordinates_go_through_format_coord(_tk_root):
    frame = tk.Frame(_tk_root)
    entries = tuple(tk.Entry(frame) for _ in range(3))
    app = _coord_app(LocationState(system=SYSTEM, coords=(1.0, 0.123456789, -0.000001)))
    assert fill_coordinates(app, entries) is True
    assert [e.get() for e in entries] == ["1", "0.12346", "0"]
    frame.destroy()


def test_coordinates_destroyed_entry_returns_false(_tk_root):
    frame = tk.Frame(_tk_root)
    entries = tuple(tk.Entry(frame) for _ in range(3))
    entries[1].destroy()
    assert fill_coordinates(_coord_app(), entries) is False
    frame.destroy()


def test_coordinates_default_warning(_tk_root, monkeypatch):
    import edmrn.gui
    shown = []
    monkeypatch.setattr(edmrn.gui, "WarningDialog", lambda *a: shown.append(a))
    frame = tk.Frame(_tk_root)
    entries = tuple(tk.Entry(frame) for _ in range(3))
    app = _coord_app(LocationState())
    assert fill_coordinates(app, entries) is False
    assert shown == [(app, "Current Location", UNKNOWN_LOCATION)]
    frame.destroy()


def test_coords_button_follows_theme(_tk_root):
    frame = tk.Frame(_tk_root)
    entries = tuple(tk.Entry(frame) for _ in range(3))
    theme = FakeThemeManager()
    app = _coord_app()
    app.theme_manager = theme
    button = add_coords_button(app, frame, entries, row=0, column=0)
    assert theme.calls == [(button, "secondary")]
    assert button.cget("width") == 28
    frame.destroy()


def test_add_coords_button(_tk_root):
    frame = tk.Frame(_tk_root)
    entries = tuple(tk.Entry(frame) for _ in range(3))
    button = add_coords_button(_coord_app(), frame, entries, warn=lambda m: None, row=0, column=8)
    info = button.grid_info()
    assert info["row"] == 0 and info["column"] == 8
    assert button.cget("text") == "📍"
    button.invoke()
    assert [e.get() for e in entries] == ["768.375", "-565.90625", "14325.34375"]
    frame.destroy()
