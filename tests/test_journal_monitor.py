import pytest

from edmrn.journal import JournalMonitor
from journal_helpers import append_events, attached, event, journal_file, wait_for


@pytest.fixture
def journal_dir(tmp_path):
    d = tmp_path / "journal"
    d.mkdir()
    return d


@pytest.fixture
def run_monitor(journal_dir):
    monitors = []

    def start(callback):
        monitor = JournalMonitor(callback, manual_journal_path=str(journal_dir))
        monitor.start()
        monitors.append(monitor)
        return monitor

    yield start
    for monitor in monitors:
        monitor.stop()
        monitor.join(timeout=5)


def test_carrier_jump_reaches_callback(journal_dir, run_monitor):
    path = journal_file(journal_dir)
    append_events(path, event("Fileheader", part=1))
    seen = []
    monitor = run_monitor(lambda system, data=None: seen.append(system))
    assert wait_for(lambda: attached(monitor, path))

    append_events(path, event("CarrierJump", StarSystem="Gamma", SystemAddress=3,
                              StarPos=[7.0, 8.0, 9.0], Docked=True))

    assert wait_for(lambda: "Gamma" in seen)
    assert monitor.current_system == "Gamma"


def test_getters_include_carrier_jump(journal_dir):
    path = journal_file(journal_dir)
    append_events(path,
                  event("Location", StarSystem="Beta", SystemAddress=2, StarPos=[4.0, 5.0, 6.0]),
                  event("CarrierJump", StarSystem="Gamma", SystemAddress=3, StarPos=[7.0, 8.0, 9.0]))
    monitor = JournalMonitor(None, manual_journal_path=str(journal_dir))

    assert monitor.get_current_system() == "Gamma"
    assert monitor.get_current_coordinates() == (7.0, 8.0, 9.0)
