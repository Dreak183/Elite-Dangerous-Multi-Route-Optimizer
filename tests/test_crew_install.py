import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from crew_helpers import ADDR, ts, visit
from edmrn.ext import crew as crew_module
from edmrn.ext import crew_events as ev
from edmrn.ext.crew import Writer, install
from edmrn.ext.crew_identity import KeyRing
from edmrn.ext.crew_status import IN_MAIN_SHIP
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.location import journal_dir_for
from edmrn.ext.store import Store
from edmrn.fuel_tracker import FuelTracker
from edmrn.journal import JournalMonitor
from journal_helpers import append_events, event, journal_file, wait_for

SOL = 10477373803
LOAD = event("LoadGame", "2026-10-12T09:59:00Z", Commander="Jameson", FID="F100")
JUMP = event("FSDJump", "2026-10-12T10:00:00Z", StarSystem="Sol", SystemAddress=SOL, StarPos=[0, 0, 0])


class ManualRoot:
    """Records after() calls; fire() runs the oldest one, like one turn of the Tk loop."""

    def __init__(self):
        self.calls = []

    def after(self, ms, fn, *args):
        self.calls.append((ms, fn, args))

    def fire(self):
        ms, fn, args = self.calls.pop(0)
        fn(*args)


class DeadRoot:
    def after(self, ms, fn, *args):
        raise RuntimeError("main thread is not in main loop")


@pytest.fixture(autouse=True)
def listeners(monkeypatch):
    lists = SimpleNamespace(journal=[], status=[])
    monkeypatch.setattr(JournalMonitor, "event_listeners", lists.journal)
    monkeypatch.setattr(FuelTracker, "status_listeners", lists.status)
    return lists


@pytest.fixture
def store(tmp_path):
    (tmp_path / "data").mkdir()
    s = Store(tmp_path / "data" / "edmrn.db")
    yield s
    s.close()


def make_app(store, journal_dir, root=None):
    journal_dir.mkdir(exist_ok=True)
    return SimpleNamespace(root=root or ManualRoot(), edmrn_store=store, edmrn_store_error=None,
                           config=SimpleNamespace(journal_path=str(journal_dir)), location_tracker=None)


def start(store, tmp_path, root=None):
    app = make_app(store, tmp_path / "journals", root)
    service = install(app)
    assert service is not None and wait_for(lambda: service.live.seeded)
    return app, service


def feed(listeners, *events):
    for e in events:
        for listener in list(listeners.journal):
            listener(e)


def log_kinds(store):
    with store.read() as conn:
        return [row[0] for row in conn.execute("SELECT kind FROM crew_log ORDER BY created_at, seq")]


def writer_for(store, tmp_path, crew_store=None):
    root = ManualRoot()
    return Writer(crew_store or CrewStore(store), KeyRing(tmp_path / "keys"), root), root


# --- install ----------------------------------------------------------------------------

def test_install_registers_listeners_and_schedules_the_writer(store, tmp_path, listeners):
    app, service = start(store, tmp_path)
    assert app.crew is service
    assert listeners.journal == [service.live.feed]
    assert listeners.status == [service.ship.feed_status]
    assert app.root.calls[0][0] == Writer.INTERVAL_MS and service.writer.running


def test_listeners_only_enqueue_and_the_writer_drains_on_the_tk_loop(store, tmp_path, listeners):
    app, service = start(store, tmp_path)
    feed(listeners, LOAD, JUMP)
    assert log_kinds(store) == [] and service.writer.pending == 1
    app.root.fire()
    assert log_kinds(store) == ["visit"] and service.writer.pending == 0
    assert len(app.root.calls) == 1
    assert len(list((tmp_path / "data" / "crew_keys").glob("cmdr-*.json"))) == 1


def test_install_is_idempotent(store, tmp_path, listeners):
    app, service = start(store, tmp_path)
    assert install(app) is service
    assert len(listeners.journal) == 1 and len(listeners.status) == 1


def test_an_unusable_store_disables_crew_without_raising(tmp_path, listeners):
    app = make_app(None, tmp_path / "journals")
    app.edmrn_store_error = "database disk image is malformed"
    assert install(app) is None
    assert app.crew is None and listeners.journal == [] and listeners.status == []


def test_writer_stops_when_root_is_gone(store, tmp_path, listeners):
    app, service = start(store, tmp_path, root=DeadRoot())
    assert service.writer.running is False
    feed(listeners, LOAD, JUMP)
    assert service.writer.pending == 1


def test_status_json_reaches_the_ship_status(store, tmp_path, listeners):
    app, service = start(store, tmp_path)
    for listener in listeners.status:
        listener({"timestamp": "2026-10-12T10:00:00Z", "Flags": IN_MAIN_SHIP,
                  "Fuel": {"FuelMain": 8.0, "FuelReservoir": 0.4}})
    assert service.ship.state.fuel_main == 8.0


def test_status_combines_location_and_ship(store, tmp_path, listeners):
    app, service = start(store, tmp_path)
    app.location_tracker = SimpleNamespace(state="LOCATION")
    feed(listeners, event("Loadout", "2026-10-12T10:00:00Z", Ship="sidewinder", HullHealth=1.0,
                          FuelCapacity={"Main": 2.0, "Reserve": 0.3}))
    location, ship = service.status()
    assert location == "LOCATION" and ship.ship == "sidewinder"


def test_journal_dir_for_prefers_the_live_monitor_then_the_setting(tmp_path):
    with_monitor = SimpleNamespace(journal_monitor=SimpleNamespace(journal_path="X:/live"),
                                   config=SimpleNamespace(journal_path=str(tmp_path)))
    assert journal_dir_for(with_monitor) == "X:/live"
    assert journal_dir_for(SimpleNamespace(config=SimpleNamespace(journal_path=str(tmp_path)))) == str(tmp_path)


def test_app_installs_crew_right_after_location():
    """Source-level check: importing edmrn.app needs Tk and replaces sys.excepthook."""
    tree = ast.parse((Path(__file__).parent.parent / "edmrn" / "app.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "EDMRN_App")
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    calls = {getattr(n.func, "id", None): n.lineno for n in ast.walk(init) if isinstance(n, ast.Call)}
    assert calls["install_edmrn_crew"] > calls["install_edmrn_location"]
    imports = [n for n in ast.walk(init) if isinstance(n, ast.ImportFrom) and n.module == "edmrn.ext.crew"]
    assert imports and imports[0].names[0].name == "install"


# --- writer -----------------------------------------------------------------------------

def test_the_writer_takes_at_most_500_drafts_per_tick(store, tmp_path):
    writer, root = writer_for(store, tmp_path)
    for i in range(600):
        writer.enqueue(visit(at=ts(i // 60, i % 60)))
    writer.start()
    root.fire()
    assert len(log_kinds(store)) == 500 and writer.pending == 100
    root.fire()
    assert len(log_kinds(store)) == 600 and writer.pending == 0


class PoisonStore(CrewStore):
    def append_own(self, items):
        if any(draft.payload["system"] == "Poison" for _, draft in items):
            raise RuntimeError("poison")
        return super().append_own(items)


def test_a_poison_draft_is_dropped_without_losing_the_rest_of_its_batch(store, tmp_path):
    writer, root = writer_for(store, tmp_path, PoisonStore(store))
    for i, system in enumerate(["A", "Poison", "B", "C"]):
        writer.enqueue(visit(at=ts(0, i), system=system, address=str(100 + i)))
    assert writer.drain() == 3 and writer.pending == 0
    with store.read() as conn:
        assert sorted(r[0] for r in conn.execute("SELECT name FROM crew_systems")) == ["A", "B", "C"]


def test_queue_overflow_drops_the_oldest(store, tmp_path):
    writer, root = writer_for(store, tmp_path)
    writer.QUEUE_CAP = 3
    for i in range(5):
        writer.enqueue(visit(at=ts(0, i)))
    writer.drain()
    with store.read() as conn:
        assert [r[0] for r in conn.execute("SELECT created_at FROM crew_log ORDER BY seq")] == [
            ts(0, 2), ts(0, 3), ts(0, 4)]


def test_invalid_drafts_are_dropped_and_logged_once_per_kind(store, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(crew_module.logger, "warning", warnings.append)
    writer, root = writer_for(store, tmp_path)
    for _ in range(3):
        writer.enqueue(ev.Draft("visit", "F1", ts(0), {"system": "Sol"}))
    assert writer.pending == 0 and len(warnings) == 1


def test_a_broken_key_file_disables_only_that_commander(store, tmp_path):
    writer, root = writer_for(store, tmp_path)
    broken = KeyRing(tmp_path / "keys").path_for("F1")
    broken.parent.mkdir(parents=True)
    broken.write_text("garbage", encoding="utf-8")
    writer.enqueue(visit("F1"))
    writer.enqueue(visit("F2"))
    writer.enqueue(visit("F1", at=ts(1)))
    assert writer.drain() == 1
    assert broken.read_text(encoding="utf-8") == "garbage"


# --- manual API -------------------------------------------------------------------------

def test_manual_api_round_trip(store, tmp_path, listeners):
    app, service = start(store, tmp_path)
    feed(listeners, LOAD)
    find_id = service.report_find("other", "Sol", ADDR, note="Wreck")
    service.edit_find(find_id, note="Wreck, two pods")
    service.writer.drain()
    [find] = service.store.finds()
    assert (find.find_id, find.note, find.author) == (find_id, "Wreck, two pods", service.identity().author)
    service.delete_find(find_id)
    service.writer.drain()
    assert service.store.finds() == []
    note_id = service.set_note("Sol", ADDR, "Nice rings")
    assert service.set_note("Sol", ADDR, "Nicer rings", note_id=note_id) == note_id
    service.writer.drain()
    assert [n.text for n in service.store.notes(ADDR)] == ["Nicer rings"]
    service.delete_note(note_id)
    service.writer.drain()
    assert service.store.notes() == []


def test_manual_api_rejects_unknown_ids_fields_and_bad_text(store, tmp_path, listeners):
    app, service = start(store, tmp_path)
    feed(listeners, LOAD)
    with pytest.raises(KeyError):
        service.edit_find(ev.uuid7(), note="x")
    with pytest.raises(KeyError):
        service.delete_note(ev.uuid7())
    find_id = service.report_find("other", "Sol", ADDR)
    with pytest.raises(TypeError):
        service.edit_find(find_id, colour="red")
    with pytest.raises(ev.PayloadError):
        service.set_note("Sol", ADDR, "bad\u202e")


def test_manual_api_without_a_commander_raises(store, tmp_path):
    app, service = start(store, tmp_path)
    with pytest.raises(RuntimeError):
        service.report_find("other", "Sol", ADDR)
