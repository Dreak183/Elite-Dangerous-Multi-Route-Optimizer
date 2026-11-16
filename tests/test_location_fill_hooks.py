"""Hook functions and hook lines for the location buttons (spec L3, L7, L10)."""
import inspect
from types import SimpleNamespace

import pytest

from edmrn.ext.location import LocationState
from edmrn.ext.location_fill import (
    BUTTON_TEXT, add_coords_dialog_button, add_neutron_tab_buttons, add_system_info_button)

ctk = pytest.importorskip("customtkinter")

SYSTEM = "Blae Phoe XT-A e1459"
COORDS = (768.375, -565.90625, 14325.34375)


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


def test_hook_failure_does_not_raise():
    assert add_neutron_tab_buttons(SimpleNamespace()) is None
