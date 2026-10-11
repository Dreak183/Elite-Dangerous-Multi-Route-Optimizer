import sqlite3
from types import SimpleNamespace

import pytest

ctk = pytest.importorskip("customtkinter")

from edmrn.ext.neutron_resume import NeutronAutosave, open_saved_routes_dialog
from edmrn.ext.store import RouteMeta, Store


def wp(name, status="unvisited"):
    return {"system": name, "type": "Normal", "distance": 10.0, "jumps": 1, "status": status}


@pytest.fixture
def root():
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


def make_gui_app(root, store):
    router = SimpleNamespace(last_route=[], current_waypoint_index=0)
    manager = SimpleNamespace(
        update_neutron_navigation=lambda: None, handle_neutron_system_jump=lambda n: None,
        copy_current_neutron_system=lambda auto=False: None,
        get_current_system_from_journal=lambda: None)
    app = SimpleNamespace(root=root, edmrn_store=store, edmrn_store_error=None,
                          theme_manager=SimpleNamespace(apply_button_theme=lambda *a, **k: None),
                          neutron_router=router, neutron_manager=manager)
    app.neutron_autosave = NeutronAutosave(app, store)
    return app


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def texts(root, cls):
    out = []
    for w in walk(root):
        if isinstance(w, cls):
            try:
                out.append(w.cget("text"))
            except Exception:
                pass
    return out


def toplevels(root):
    return [w for w in root.winfo_children() if isinstance(w, ctk.CTkToplevel)]


def buttons(root, label):
    return [w for w in walk(root) if isinstance(w, ctk.CTkButton) and w.cget("text") == label]


@pytest.fixture
def seeded(root, tmp_path):
    store = Store(tmp_path / "edmrn.db")
    store.add_neutron_route(RouteMeta("Sol", "Colonia", 70.0, "x4"),
                            [wp("Sol", "visited"), wp("A"), wp("Colonia")], 1)
    store.add_neutron_route(RouteMeta("Achenar", "Sag A*", 60.0, "x6"),
                            [wp("Achenar", "visited"), wp("Sag A*", "visited")], 1)
    app = make_gui_app(root, store)
    try:
        yield app
    finally:
        store.close()


def test_rows_delete_and_error(root, seeded, monkeypatch):
    open_saved_routes_dialog(seeded)
    root.update()
    [dialog] = toplevels(root)
    rows = texts(dialog, ctk.CTkLabel)
    assert any("Sol → Colonia" in t and "1/3" in t for t in rows)
    assert any("Achenar → Sag A*" in t and "done" in t for t in rows)

    store = seeded.edmrn_store
    original = store.delete_neutron_route
    monkeypatch.setattr(store, "delete_neutron_route",
                        lambda rid: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    buttons(dialog, "Delete")[0].invoke()
    root.update()
    assert any(t == "Delete failed: database is locked" for t in texts(dialog, ctk.CTkLabel))
    assert len(buttons(dialog, "Delete")) == 2

    monkeypatch.setattr(store, "delete_neutron_route", original)
    buttons(dialog, "Delete")[0].invoke()
    root.update()
    assert len(buttons(dialog, "Delete")) == 1
    assert len(store.list_neutron_routes()) == 1
    dialog.destroy()


def test_resume_closes_dialog(root, seeded):
    open_saved_routes_dialog(seeded)
    root.update()
    [dialog] = toplevels(root)
    # newest route first or not, resume whichever button comes first
    buttons(dialog, "Resume")[0].invoke()
    root.update()
    assert not dialog.winfo_exists()


def test_resume_of_deleted_route_says_so(root, seeded):
    open_saved_routes_dialog(seeded)
    root.update()
    [dialog] = toplevels(root)
    for summary in seeded.edmrn_store.list_neutron_routes():
        seeded.edmrn_store.delete_neutron_route(summary.id)
    buttons(dialog, "Resume")[0].invoke()
    root.update()
    assert "This route no longer exists." in texts(dialog, ctk.CTkLabel)
    dialog.destroy()


def test_second_open_reuses_dialog(root, seeded):
    open_saved_routes_dialog(seeded)
    open_saved_routes_dialog(seeded)
    root.update()
    [dialog] = toplevels(root)
    dialog.destroy()


def test_unavailable_store_shows_reason(root):
    app = SimpleNamespace(root=root, edmrn_store=None, neutron_autosave=None,
                          edmrn_store_error="edmrn.db is corrupt")
    open_saved_routes_dialog(app)
    root.update()
    [dialog] = toplevels(root)
    assert any("edmrn.db is corrupt" in t for t in texts(dialog, ctk.CTkLabel))
    dialog.destroy()
