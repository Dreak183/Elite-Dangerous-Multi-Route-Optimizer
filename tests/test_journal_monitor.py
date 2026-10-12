import json
import os
import time

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


def test_start_does_not_replay_existing_lines(journal_dir, run_monitor):
    path = journal_file(journal_dir)
    append_events(path, event("Fileheader", part=1), event("FSDJump", StarSystem="Old"))
    seen = []
    monitor = run_monitor(lambda system, data=None: seen.append(system))
    assert wait_for(lambda: attached(monitor, path))

    append_events(path, event("FSDJump", StarSystem="New"))

    assert wait_for(lambda: "New" in seen)
    assert "Old" not in seen


def test_idle_monitor_uses_little_cpu(journal_dir, run_monitor):
    path = journal_file(journal_dir)
    append_events(path, event("Fileheader", part=1))
    monitor = run_monitor(lambda system, data=None: None)
    assert wait_for(lambda: attached(monitor, path))

    start = time.process_time()
    time.sleep(2.0)
    used = time.process_time() - start

    assert used < 0.2, f"idle monitor used {used:.2f} s CPU in 2 s"


def test_follows_rotation_to_newer_file(journal_dir, run_monitor):
    old = journal_file(journal_dir, "2026-10-11T120000")
    append_events(old, event("Fileheader", part=1))
    seen = []
    monitor = run_monitor(lambda system, data=None: seen.append(system))
    assert wait_for(lambda: attached(monitor, old))
    past = time.time() - 60
    os.utime(old, (past, past))

    new = journal_file(journal_dir, "2026-10-11T130000")
    append_events(new, event("Fileheader", part=1), event("FSDJump", StarSystem="Delta"))

    assert wait_for(lambda: "Delta" in seen, timeout=6.0)
    assert os.path.basename(monitor.current_journal_file) == new.name


def test_half_written_line_is_processed_once_when_complete(journal_dir, run_monitor):
    path = journal_file(journal_dir)
    append_events(path, event("Fileheader", part=1))
    seen = []
    monitor = run_monitor(lambda system, data=None: seen.append(system))
    assert wait_for(lambda: attached(monitor, path))

    line = '{"timestamp":"2026-10-11T12:00:01Z","event":"FSDJump","StarSystem":"Split"}\n'
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line[:30])
    time.sleep(1.0)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line[30:])

    assert wait_for(lambda: "Split" in seen)
    time.sleep(0.5)
    assert seen.count("Split") == 1


def test_trailing_fragment_does_not_block_rotation(journal_dir, run_monitor):
    old = journal_file(journal_dir, "2026-10-11T120000")
    append_events(old, event("Fileheader", part=1))
    seen = []
    monitor = run_monitor(lambda system, data=None: seen.append(system))
    assert wait_for(lambda: attached(monitor, old))
    with open(old, "a", encoding="utf-8", newline="\n") as f:
        f.write('{"timestamp":"x","event":"FSDJ')
    time.sleep(0.7)
    past = time.time() - 60
    os.utime(old, (past, past))

    new = journal_file(journal_dir, "2026-10-11T130000")
    append_events(new, event("Fileheader", part=1), event("FSDJump", StarSystem="Delta"))

    assert wait_for(lambda: "Delta" in seen, timeout=6.0)
    assert os.path.basename(monitor.current_journal_file) == new.name


def test_fragment_split_inside_multibyte_character(journal_dir, run_monitor):
    path = journal_file(journal_dir)
    append_events(path, event("Fileheader", part=1))
    seen = []
    monitor = run_monitor(lambda system, data=None: seen.append(system))
    assert wait_for(lambda: attached(monitor, path))

    complete = json.dumps(event("FSDJump", StarSystem="A")) + "\n"
    raw = (json.dumps(event("FSDJump", StarSystem="Gröt"), ensure_ascii=False)
           + "\n").encode("utf-8")
    cut = raw.index("ö".encode("utf-8")) + 1
    with open(path, "ab") as f:
        f.write(complete.encode("utf-8") + raw[:cut])
    time.sleep(1.5)
    with open(path, "ab") as f:
        f.write(raw[cut:])

    assert wait_for(lambda: "Gröt" in seen)
    time.sleep(0.5)
    assert seen == ["A", "Gröt"]


def test_file_tailed_before_resumes_instead_of_replaying(journal_dir, run_monitor):
    a = journal_file(journal_dir, "2026-10-11T120000")
    append_events(a, event("Fileheader", part=1))
    seen = []
    monitor = run_monitor(lambda system, data=None: seen.append(system))
    assert wait_for(lambda: attached(monitor, a))
    append_events(a, event("FSDJump", StarSystem="A1"))
    assert wait_for(lambda: "A1" in seen)

    b = journal_file(journal_dir, "2026-10-11T130000")
    append_events(b, event("Fileheader", part=1), event("FSDJump", StarSystem="B1"))
    past = time.time() - 60
    os.utime(a, (past, past))
    assert wait_for(lambda: "B1" in seen, timeout=6.0)

    future = time.time() + 60
    os.utime(a, (future, future))
    assert wait_for(lambda: os.path.basename(monitor.current_journal_file) == a.name,
                    timeout=6.0)
    append_events(a, event("FSDJump", StarSystem="A2"))

    assert wait_for(lambda: "A2" in seen)
    assert seen == ["A1", "B1", "A2"]


def test_retail_of_same_file_neither_replays_nor_loses(journal_dir, run_monitor,
                                                       monkeypatch):
    path = journal_file(journal_dir)
    append_events(path, event("Fileheader", part=1), event("FSDJump", StarSystem="Old"))
    seen = []
    monitor = run_monitor(lambda system, data=None: seen.append(system))
    assert wait_for(lambda: attached(monitor, path))
    append_events(path, event("FSDJump", StarSystem="One"))
    assert wait_for(lambda: "One" in seen)

    real = os.path.getsize
    state = {"raised": False}

    def flaky(p):
        if not state["raised"] and str(p) == str(monitor.current_journal_file):
            state["raised"] = True
            raise FileNotFoundError(p)
        return real(p)

    monkeypatch.setattr(os.path, "getsize", flaky)
    assert wait_for(lambda: state["raised"])
    append_events(path, event("FSDJump", StarSystem="Two"))

    assert wait_for(lambda: "Two" in seen, timeout=8.0)
    time.sleep(0.5)
    assert seen == ["One", "Two"]
