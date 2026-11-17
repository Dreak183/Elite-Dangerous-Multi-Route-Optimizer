"""Hook functions and hook lines for the location buttons (spec L3, L7, L10)."""
import inspect
import logging
from types import SimpleNamespace

import pytest

from edmrn.ext.location import LocationState
from edmrn.ext.location_fill import (
    BUTTON_TEXT, NOT_IN_CSV, UNKNOWN_LOCATION, add_coords_dialog_button, add_custom_route_buttons,
    add_galaxy_tab_buttons, add_neutron_tab_buttons, add_optimizer_start_button, add_system_info_button)

ctk = pytest.importorskip("customtkinter")

SYSTEM = "Blae Phoe XT-A e1459"
COORDS = (768.375, -565.90625, 14325.34375)


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


def _app():
    state = LocationState(system=SYSTEM, coords=COORDS)
    return SimpleNamespace(root=ImmediateRoot(), location_tracker=SimpleNamespace(state=state))


def _buttons(widget):
    return [c for c in widget.winfo_children()
            if isinstance(c, ctk.CTkButton) and c.cget("text") == BUTTON_TEXT]


def test_hook_lines_in_upstream_builders():
    from edmrn.custom_route import CustomRouteTab
    from edmrn.system_info_section import SystemInfoSection
    from edmrn.ui_components import UIComponents
    neutron = inspect.getsource(UIComponents.create_neutron_tab)
    assert "add_neutron_tab_buttons(self.app)" in neutron
    assert "ctk.CTkScrollableFrame(main_frame" in neutron
    assert "add_optimizer_start_button(self.app)" in inspect.getsource(UIComponents.create_optimizer_tab)
    assert "add_galaxy_tab_buttons(self.app)" in inspect.getsource(UIComponents.create_galaxy_plotter_tab)
    assert "add_custom_route_buttons(self)" in inspect.getsource(CustomRouteTab._build_ui)
    assert "add_coords_dialog_button(self.app, frame, name_entry, (x_entry, y_entry, z_entry))" in \
        inspect.getsource(CustomRouteTab._add_by_coordinates)
    assert "add_system_info_button(app, self)" in inspect.getsource(SystemInfoSection._build_ui)


def test_neutron_hook_adds_buttons(_tk_root):
    from edmrn.autocomplete_entry import AutocompleteEntry
    frm = ctk.CTkFrame(_tk_root)
    frm.pack()
    app = _app()
    app.neutron_from_autocomplete = AutocompleteEntry(frm)
    app.neutron_from_autocomplete.pack()
    app.neutron_to_autocomplete = AutocompleteEntry(frm)
    app.neutron_to_autocomplete.pack()
    nearest = ctk.CTkFrame(frm)
    nearest.pack()
    app.nearest_x_entry, app.nearest_y_entry, app.nearest_z_entry = (ctk.CTkEntry(nearest) for _ in range(3))
    app.nearest_find_btn = ctk.CTkButton(nearest, text="FIND")
    try:
        add_neutron_tab_buttons(app)
        for field in (app.neutron_from_autocomplete, app.neutron_to_autocomplete):
            buttons = _buttons(field)
            assert len(buttons) == 1
            assert int(buttons[0].grid_info()["column"]) == 1
        coords = _buttons(nearest)
        assert len(coords) == 1
        info = coords[0].grid_info()
        assert (int(info["row"]), int(info["column"])) == (0, 8)
    finally:
        frm.destroy()


def test_system_info_hook_fetches(_tk_root):
    from edmrn.autocomplete_entry import AutocompleteEntry
    calls = []
    field = AutocompleteEntry(_tk_root)
    field.pack()
    app = _app()
    section = SimpleNamespace(system_info_entry=field, fetch_callback=lambda: calls.append(1))
    try:
        add_system_info_button(app, section)
        _buttons(field)[0].invoke()
        assert field.get() == SYSTEM
        assert calls == [1]
    finally:
        field.destroy()


def test_system_info_hook_without_app_does_nothing(_tk_root):
    from edmrn.autocomplete_entry import AutocompleteEntry
    field = AutocompleteEntry(_tk_root)
    field.pack()
    section = SimpleNamespace(system_info_entry=field, fetch_callback=lambda: None)
    try:
        assert add_system_info_button(None, section) is None
        assert _buttons(field) == []
    finally:
        field.destroy()


def test_neutron_hook_sizes_scroll_column_to_content(_tk_root):
    from edmrn.autocomplete_entry import AutocompleteEntry
    left = ctk.CTkScrollableFrame(_tk_root)
    left.pack()
    wide = ctk.CTkFrame(left, width=500, height=20)
    wide.grid(row=0, column=0)
    wide.grid_propagate(False)
    app = _app()
    app.neutron_from_autocomplete = AutocompleteEntry(left)
    app.neutron_from_autocomplete.grid(row=1, column=0)
    app.neutron_to_autocomplete = AutocompleteEntry(left)
    app.neutron_to_autocomplete.grid(row=2, column=0)
    nearest = ctk.CTkFrame(left)
    nearest.grid(row=3, column=0)
    app.nearest_x_entry, app.nearest_y_entry, app.nearest_z_entry = (ctk.CTkEntry(nearest) for _ in range(3))
    app.nearest_find_btn = ctk.CTkButton(nearest, text="FIND")
    try:
        add_neutron_tab_buttons(app)
        assert left.cget("width") >= 500 / left._get_widget_scaling() - 1
    finally:
        left.destroy()


def test_coords_dialog_hook_names_row(_tk_root):
    frm = ctk.CTkFrame(_tk_root)
    frm.pack()
    name, x, y, z = (ctk.CTkEntry(frm) for _ in range(4))
    try:
        add_coords_dialog_button(_app(), frm, name, (x, y, z))
        button = _buttons(frm)[0]
        button.invoke()
        assert name.get() == SYSTEM
        assert x.get() == "768.375"
    finally:
        frm.destroy()


def test_hook_failure_does_not_raise(caplog):
    with caplog.at_level(logging.ERROR):
        assert add_neutron_tab_buttons(SimpleNamespace()) is None
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and errors[0].exc_info is not None


def test_custom_route_has_two_buttons(_tk_root):
    from edmrn.autocomplete_entry import AutocompleteEntry
    frm = ctk.CTkFrame(_tk_root)
    frm.pack()
    tab = SimpleNamespace(app=_app(), starting_entry=AutocompleteEntry(frm),
                          ending_entry=AutocompleteEntry(frm), system_entry=AutocompleteEntry(frm))
    try:
        add_custom_route_buttons(tab)
        assert len(_buttons(tab.starting_entry)) == 1
        assert len(_buttons(tab.system_entry)) == 1
        assert _buttons(tab.ending_entry) == []
    finally:
        frm.destroy()


class _Recorder:
    calls = []

    def __init__(self, app, field, *, check_spansh, **kw):
        _Recorder.calls.append((field, check_spansh))


def test_spansh_check_wiring(monkeypatch):
    from edmrn.ext import location_fill
    monkeypatch.setattr(location_fill, "SystemFieldFill", _Recorder)
    monkeypatch.setattr(location_fill, "add_coords_button", lambda *a, **k: None)
    monkeypatch.setattr(location_fill, "_size_scroll_column", lambda w: None)
    f = {n: object() for n in ("nf", "nt", "gs", "gd", "cs", "ce", "cy", "si")}
    app = SimpleNamespace(
        neutron_from_autocomplete=f["nf"], neutron_to_autocomplete=f["nt"],
        galaxy_source_autocomplete=f["gs"], galaxy_dest_autocomplete=f["gd"],
        nearest_find_btn=SimpleNamespace(master=None), nearest_x_entry=None,
        nearest_y_entry=None, nearest_z_entry=None)
    _Recorder.calls = []
    add_neutron_tab_buttons(app)
    add_galaxy_tab_buttons(app)
    add_custom_route_buttons(SimpleNamespace(app=app, starting_entry=f["cs"], ending_entry=f["ce"],
                                             system_entry=f["cy"]))
    add_system_info_button(app, SimpleNamespace(system_info_entry=f["si"], fetch_callback=None))
    assert add_optimizer_start_button(SimpleNamespace()) is None   # logs, no raise
    assert _Recorder.calls == [(f["nf"], True), (f["nt"], True), (f["gs"], True), (f["gd"], True),
                               (f["cs"], False), (f["cy"], False), (f["si"], False)]


def _optimizer_app(state, listed=None):
    app = _app()
    app.location_tracker.state = state
    app.starting_system = SimpleNamespace(value=None, set=lambda v: setattr(app.starting_system, "value", v))
    app.logs = []
    app._log = app.logs.append
    if listed is not None:
        app.start_systems_list = listed
    return app


@pytest.fixture
def optimizer(_tk_root):
    container = ctk.CTkFrame(_tk_root)
    container.pack()
    ctk.CTkButton(container, text="v").grid(row=0, column=1)

    def make(state=LocationState(system=SYSTEM, coords=COORDS), listed=None):
        app = _optimizer_app(state, listed)
        app.start_entry = ctk.CTkEntry(container)
        app.start_entry.grid(row=0, column=0)
        add_optimizer_start_button(app)
        return app, _buttons(container)[0]
    yield make
    container.destroy()


def test_optimizer_button_placement(optimizer):
    app, button = optimizer()
    info = button.grid_info()
    assert (int(info["row"]), int(info["column"])) == (0, 2)
    assert button.cget("text") == "📍"


def test_optimizer_fills_start_without_csv(optimizer):
    app, button = optimizer()
    button.invoke()
    assert app.starting_system.value == SYSTEM
    assert app.logs == []


def test_optimizer_logs_when_csv_lacks_system(optimizer):
    app, button = optimizer(listed=["Sol", "Colonia"])
    button.invoke()
    assert app.starting_system.value == SYSTEM
    assert app.logs == [NOT_IN_CSV.format(system=SYSTEM)]
    assert app.logs[0] == (f"'{SYSTEM}' is not in the loaded CSV - the optimizer will choose the start; "
                           "🎯 Find Nearest picks the CSV system nearest to you.")


def test_optimizer_silent_when_csv_lists_system_any_case(optimizer):
    app, button = optimizer(listed=["Sol", f"  {SYSTEM.upper()} "])
    button.invoke()
    assert app.starting_system.value == SYSTEM
    assert app.logs == []


def test_optimizer_silent_for_empty_csv_list(optimizer):
    app, button = optimizer(listed=[])
    button.invoke()
    assert app.logs == []


def test_optimizer_unknown_location(optimizer):
    app, button = optimizer(state=LocationState(), listed=["Sol"])
    button.invoke()
    assert app.starting_system.value is None
    assert app.logs == [UNKNOWN_LOCATION]


def _two_field_frame(root):
    from edmrn.autocomplete_entry import AutocompleteEntry
    outer = ctk.CTkFrame(root)
    outer.pack()
    outer.columnconfigure((0, 1), weight=1)
    fields = []
    for column in (0, 1):
        frame = ctk.CTkFrame(outer)
        frame.grid(row=0, column=column, sticky="ew")
        field = AutocompleteEntry(frame)
        field.pack(fill="x")
        fields.append(field)
    return outer, fields


def _realized_widths(root, *widgets):
    for _ in range(3):
        root.update_idletasks()
    return [w.master.winfo_width() for w in widgets]


@pytest.mark.parametrize("hook, names", [
    (add_neutron_tab_buttons, ("neutron_from_autocomplete", "neutron_to_autocomplete")),
    (add_galaxy_tab_buttons, ("galaxy_source_autocomplete", "galaxy_dest_autocomplete"))])
def test_hint_never_changes_column_widths(_tk_root, hook, names):
    outer, (left, right) = _two_field_frame(_tk_root)
    app = _app()
    for name, field in zip(names, (left, right)):
        setattr(app, name, field)
    app.nearest_find_btn = None   # the Neutron coordinates step fails and is logged; irrelevant here
    try:
        hook(app)
        before = _realized_widths(_tk_root, left, right)
        left.winfo_children()
        hint = [c for c in left.winfo_children() if isinstance(c, ctk.CTkLabel)][0]
        hint.configure(text="x" * 200, wraplength=600)
        hint.grid()
        after = _realized_widths(_tk_root, left, right)
        assert after[1] == before[1]
        assert after[0] == after[1]
    finally:
        outer.destroy()


def test_custom_route_row_keeps_upstream_width(_tk_root):
    from edmrn.autocomplete_entry import AutocompleteEntry
    holder = ctk.CTkFrame(_tk_root)
    holder.pack()
    bare = AutocompleteEntry(holder)
    bare.pack()
    start, add = AutocompleteEntry(holder), AutocompleteEntry(holder)
    start.pack()
    add.pack()
    tab = SimpleNamespace(app=_app(), starting_entry=start, ending_entry=None, system_entry=add)
    try:
        add_custom_route_buttons(tab)
        for field in (start, add):
            field.update_idletasks()
            assert abs(field.winfo_reqwidth() - bare.winfo_reqwidth()) <= 1
            assert field.entry.cget("width") < bare.entry.cget("width")
    finally:
        holder.destroy()


def test_neutron_status_lines_wrap_to_their_frame(_tk_root):
    from edmrn.ext.location_fill import _follow_width
    frame = ctk.CTkFrame(_tk_root)
    frame.pack()
    labels = [ctk.CTkLabel(frame, text="x", wraplength=0) for _ in range(3)]
    try:
        handler = _follow_width(frame, *labels)
        scaling = frame._get_widget_scaling()
        handler(SimpleNamespace(width=400))
        assert [l.cget("wraplength") for l in labels] == [int(400 / scaling) - 16] * 3
        handler(SimpleNamespace(width=40))
        assert [l.cget("wraplength") for l in labels] == [100] * 3
        assert "on_configure" in frame._canvas.bind("<Configure>")
    finally:
        frame.destroy()


def test_neutron_hook_wraps_status_labels(_tk_root):
    app = _app()
    frames = []
    for name in ("neutron_info_label", "neutron_stats_label", "neutron_progress_label"):
        frame = ctk.CTkFrame(_tk_root)
        frames.append(frame)
        setattr(app, name, ctk.CTkLabel(frame, text="x"))
    try:
        add_neutron_tab_buttons(app)   # other steps fail and are logged; the wrapping step still runs
        for frame in frames:
            assert "on_configure" in frame._canvas.bind("<Configure>")
    finally:
        for frame in frames:
            frame.destroy()
