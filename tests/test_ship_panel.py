import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest

from edmrn.ext import ship as ship_module
from edmrn.ext.fsd import drive_from_loadout
from edmrn.ext.ship import ShipState, install
from edmrn.ext.ship_panel import (RangeBinding, add_range_panel, binding_for, key_handler,
                                  range_text)
from edmrn.fuel_tracker import FuelTracker
from edmrn.journal import JournalMonitor
from test_fsd import MANDALAY_BOOSTED

DRIVE = drive_from_loadout(MANDALAY_BOOSTED)
MK2 = replace(DRIVE, mk2=True)


class FakeVar:
    """Mimics tk.StringVar and counts the writes."""

    def __init__(self, value=""):
        self._value, self.sets = value, 0

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        self.sets += 1

    def trace_add(self, mode, callback):
        pass


class FakeLabel:
    def __init__(self):
        self.text = None

    def configure(self, **kw):
        self.text = kw.get("text", self.text)


class ImmediateRoot:
    def after(self, ms, fn, *args):
        fn(*args)


def make_app(**extra):
    return SimpleNamespace(jump_range=FakeVar(), **extra)


def state(**kw):
    return ShipState(drive=DRIVE, cargo=0.0, **kw)


def test_range_text():
    s = state(fuel_main=20.0)
    assert range_text(s) == "Laden 87.1 LY · now 90.6 · max 95.4 · neutron ×4 348.5"
    assert range_text(state()) == "Laden 87.1 LY · max 95.4 · neutron ×4 348.5"
    assert "neutron ×6 " in range_text(replace(s, drive=MK2))
    assert range_text(ShipState(max_jump_range=58.7)) == \
        "Max 58.7 LY (drive not recognised, laden range unknown)"
    assert range_text(ShipState()) == "Waiting for ship data from the journal"


def test_auto_writes_floored_laden_range_once():
    app = make_app()
    binding = RangeBinding(app)
    binding.on_state(state())
    binding.on_state(state(system="Elsewhere"))
    assert app.jump_range.get() == "87.1" and app.jump_range.sets == 1
    binding.on_state(replace(state(), cargo=100.0))
    assert app.jump_range.sets == 2 and app.jump_range.get() != "87.1"


def test_unrecognised_drive_fills_max():
    app = make_app()
    RangeBinding(app).on_state(ShipState(max_jump_range=58.73))
    assert app.jump_range.get() == "58.7"


def test_typing_turns_auto_off_and_survives_updates():
    app = make_app()
    binding = RangeBinding(app)
    binding.on_state(state())
    app.jump_range.set("60")
    binding.on_key()
    assert binding.auto is False
    binding.on_state(replace(state(), cargo=100.0))
    assert app.jump_range.get() == "60"
    binding.set_auto(True)
    assert binding.auto is True
    assert app.jump_range.get() == range_text(binding.state).split()[1]


def test_key_without_change_keeps_auto():
    app = make_app()
    binding = RangeBinding(app)
    binding.on_state(state())
    binding.on_key()
    assert binding.auto is True
    other = FakeVar("87.1")
    binding.on_key(other)
    assert binding.auto is True
    other.set("5")
    binding.on_key(other)
    assert binding.auto is False


def test_new_drive_sets_boost_label_while_auto():
    boost = FakeVar()
    app = make_app(neutron_boost_var=boost)
    binding = RangeBinding(app)
    binding.on_state(replace(state(), drive=MK2))
    assert boost.get() == "x6 (Caspian)"
    binding.on_state(state())
    assert boost.get() == "x4 (Normal)"
    binding.set_auto(False)
    binding.on_state(replace(state(), drive=MK2))
    assert boost.get() == "x4 (Normal)"
    RangeBinding(make_app()).on_state(state())   # no neutron_boost_var: no error


def test_edit_before_first_write_turns_auto_off():
    app = SimpleNamespace(jump_range=FakeVar("75"))
    binding = RangeBinding(app)
    app.jump_range.set("60")
    binding.on_key()
    assert binding.auto is False
    binding.on_state(state())
    assert app.jump_range.get() == "60"


def test_unchanged_text_before_first_write_keeps_auto():
    app = SimpleNamespace(jump_range=FakeVar("75"))
    binding = RangeBinding(app)
    binding.on_key()
    assert binding.auto is True


def test_unrecognised_drive_loadout_sets_x4():
    boost = FakeVar()
    binding = RangeBinding(make_app(neutron_boost_var=boost))
    binding.on_state(replace(state(), drive=MK2))
    assert boost.get() == "x6 (Caspian)"
    binding.on_state(ShipState(max_jump_range=40.0))
    assert boost.get() == "x4 (Normal)"


def test_set_auto_true_leaves_the_boost_alone():
    boost = FakeVar("x6 (Caspian)")
    app = make_app(neutron_boost_var=boost)
    binding = RangeBinding(app)
    binding.on_state(state())
    assert boost.get() == "x4 (Normal)"
    boost.set("x6 (Caspian)")
    binding.set_auto(False)
    binding.on_state(replace(state(), drive=MK2))   # a Loadout while Auto is off
    app.jump_range.set("1")
    binding.set_auto(True)
    assert boost.get() == "x6 (Caspian)"
    assert app.jump_range.get() == "87.1"
    binding.on_state(replace(state(), drive=MK2, cargo=5.0))   # same loadout: still no rewrite
    assert boost.get() == "x6 (Caspian)"


def test_key_handlers_use_their_own_variable():
    route, neutron = FakeVar("1"), FakeVar("1")
    app = SimpleNamespace(jump_range=route, neutron_range_var=neutron)
    binding = RangeBinding(app)
    route.set("2")
    key_handler(binding, app, "neutron_range_var")()
    assert binding.auto is True      # the neutron var still holds the baseline
    key_handler(binding, app, "jump_range")()
    assert binding.auto is False


def test_on_state_never_raises():
    app = SimpleNamespace(jump_range=None)
    RangeBinding(app).on_state(state())


def test_binding_for_is_get_or_create():
    app = make_app()
    assert binding_for(app) is binding_for(app) and app.ship_range is binding_for(app)


def test_label_registered_before_any_state_shows_waiting():
    binding = RangeBinding(make_app())
    label = FakeLabel()
    binding.register_panel(label, FakeVar())
    assert label.text == "Waiting for ship data from the journal"
    binding.on_state(state())
    assert label.text.startswith("Laden 87.1")


def test_install_to_field_path(monkeypatch, tmp_path):
    monkeypatch.setattr(JournalMonitor, "event_listeners", [])
    monkeypatch.setattr(FuelTracker, "status_listeners", [])
    monkeypatch.setattr(ship_module, "journal_dir_for", lambda app: str(tmp_path))
    app = SimpleNamespace(root=ImmediateRoot(), config=SimpleNamespace(journal_path=""),
                          journal_monitor=None, jump_range=FakeVar())
    binding_for(app)   # the panel is built before install runs
    install(app)
    app.ship_tracker.feed(MANDALAY_BOOSTED)
    assert app.jump_range.get() == "87.1"


ctk = pytest.importorskip("customtkinter")


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


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def test_add_range_panel_builds_widgets(_tk_root):
    frame = ctk.CTkFrame(_tk_root)
    var = ctk.StringVar(value="")
    entry = ctk.CTkEntry(frame, textvariable=var)
    entry.pack()
    app = SimpleNamespace(root=_tk_root, jump_range=var, range_entry=entry)
    add_range_panel(app, frame)
    labels = [w for w in walk(frame) if isinstance(w, ctk.CTkLabel)]
    boxes = [w for w in walk(frame) if isinstance(w, ctk.CTkCheckBox)]
    assert len(labels) == 1 and len(boxes) == 1
    assert boxes[0].cget("text") == "Auto"
    assert labels[0].cget("text") == "Waiting for ship data from the journal"
    binding = app.ship_range
    binding.on_state(state())
    assert var.get() == "87.1" and labels[0].cget("text").startswith("Laden 87.1")
    var.set("60")
    assert entry._entry.bind("<KeyRelease>")   # key events need a focused, mapped window
    binding.on_key(var)
    assert binding.auto is False
    binding.set_auto(True)
    assert var.get() == "87.1"
    boxes[0].toggle()
    assert binding.auto is False


def test_add_range_panel_logs_instead_of_raising(_tk_root, monkeypatch, caplog):
    frame = ctk.CTkFrame(_tk_root)

    def boom(self, *a, **k):
        raise RuntimeError("pack failed")

    monkeypatch.setattr(ctk.CTkLabel, "pack", boom)
    with caplog.at_level(logging.ERROR, logger="EDMRN.ShipPanel"):
        add_range_panel(SimpleNamespace(root=_tk_root, jump_range=FakeVar()), frame)
    assert any("pack failed" in r.getMessage() for r in caplog.records)


class TracingVar(FakeVar):
    """Like a Tk StringVar with upstream's write trace: every set(), even a same-value one, fires."""

    def __init__(self, value="", on_write=None):
        super().__init__(value)
        self.on_write = on_write

    def set(self, value):
        super().set(value)
        if self.on_write:
            self.on_write(value)


def test_reticking_auto_overwrites_a_typed_neutron_field():
    neutron = FakeVar("")
    route = TracingVar("", on_write=neutron.set)   # upstream's _sync_neutron_jump_range
    app = SimpleNamespace(jump_range=route, neutron_range_var=neutron)
    binding = RangeBinding(app)
    binding.on_state(state())
    assert neutron.get() == "87.1"
    neutron.set("60")
    key_handler(binding, app, "neutron_range_var")()
    assert binding.auto is False
    binding.set_auto(True)
    assert neutron.get() == "87.1"


def test_by_hand_boost_survives_reticking_auto_and_status_updates():
    boost = FakeVar()
    binding = RangeBinding(make_app(neutron_boost_var=boost))
    binding.on_state(replace(state(), drive=MK2))
    assert boost.get() == "x6 (Caspian)"
    boost.set("x4 (Normal)")                 # picked by hand
    binding.set_auto(False)
    binding.on_state(replace(state(), drive=MK2, max_jump_range=50.0))   # a Loadout while Auto is off
    binding.set_auto(True)
    assert boost.get() == "x4 (Normal)"
    binding.on_state(replace(state(), drive=MK2, max_jump_range=50.0, cargo=3.0))   # status-only change, same loadout
    binding.on_state(replace(state(), drive=MK2, max_jump_range=50.0, cargo=3.0, fuel_main=10.0))
    assert boost.get() == "x4 (Normal)"


def test_each_range_entry_is_bound_to_its_own_variable(_tk_root):
    route_frame, neutron_frame = ctk.CTkFrame(_tk_root), ctk.CTkFrame(_tk_root)
    route_var, neutron_var = ctk.StringVar(value="75"), ctk.StringVar(value="75")
    route_entry = ctk.CTkEntry(route_frame, textvariable=route_var)
    neutron_entry = ctk.CTkEntry(neutron_frame, textvariable=neutron_var)
    route_entry.pack()
    neutron_entry.pack()
    app = SimpleNamespace(root=_tk_root, jump_range=route_var, neutron_range_var=neutron_var,
                          range_entry=route_entry, neutron_range_entry=neutron_entry)
    add_range_panel(app, route_frame)
    add_range_panel(app, neutron_frame)


def test_each_range_entry_is_bound_to_its_own_variable(_tk_root, monkeypatch):
    from edmrn.ext import ship_panel
    route_frame, neutron_frame = ctk.CTkFrame(_tk_root), ctk.CTkFrame(_tk_root)
    route_var, neutron_var = ctk.StringVar(value="75"), ctk.StringVar(value="75")
    route_entry = ctk.CTkEntry(route_frame, textvariable=route_var)
    neutron_entry = ctk.CTkEntry(neutron_frame, textvariable=neutron_var)
    route_entry.pack()
    neutron_entry.pack()
    bound = {"route": [], "neutron": []}
    route_entry.bind = lambda seq, fn, add=None: bound["route"].append(fn)
    neutron_entry.bind = lambda seq, fn, add=None: bound["neutron"].append(fn)
    monkeypatch.setattr(ship_panel, "key_handler", lambda binding, app, name: name)
    app = SimpleNamespace(root=_tk_root, jump_range=route_var, neutron_range_var=neutron_var,
                          range_entry=route_entry, neutron_range_entry=neutron_entry)
    add_range_panel(app, route_frame)
    add_range_panel(app, neutron_frame)
    assert bound == {"route": ["jump_range"], "neutron": ["neutron_range_var"]}
