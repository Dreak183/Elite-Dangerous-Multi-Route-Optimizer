"""Location button for system fields (docs/superpowers/specs/2026-11-17-location-fill-design.md, L1-L6, L11)."""
from types import SimpleNamespace

import pytest

from edmrn.ext.location import LocationState
from edmrn.ext.location_fill import (
    BUTTON_TEXT, CHECKING, UNKNOWN_LOCATION, SystemFieldFill, current_location, format_coord)
from edmrn.ext.start_check import StartCheck

ctk = pytest.importorskip("customtkinter")

SYSTEM = "Blae Phoe XT-A e1459"
COORDS = (768.375, -565.90625, 14325.34375)
KNOWN_COLOR = "#4CAF50"
WARN_COLOR = "#FFA500"


class ImmediateRoot:
    def after(self, ms, fn, *args):
        fn(*args)


@pytest.fixture(scope="module")
def _tk_root():
    try:
        r = ctk.CTk()
    except Exception as e:  # tkinter.TclError without a display
        pytest.skip(f"no Tk display: {e}")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except Exception:
        pass


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
    r.fill.fill()
    r.field.destroy()
    r.jobs[0]()


def test_job_exception_does_not_escape(rig):
    r = rig(results=[])   # check raises IndexError
    r.fill.fill()
    r.jobs[0]()
    assert r.text == CHECKING.format(system=SYSTEM)
