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
    os.utime(journal_dir, (future, future))  # the directory mtime gates the rotation scan
    assert wait_for(lambda: os.path.basename(monitor.current_journal_file or '') == a.name,
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


@pytest.fixture
def listeners(monkeypatch):
    fresh = []
    monkeypatch.setattr(JournalMonitor, "event_listeners", fresh)
    return fresh


def test_listener_runs_after_upstream_callback(journal_dir, run_monitor, listeners):
    path = journal_file(journal_dir)
    append_events(path, event("Fileheader", part=1))
    order = []
    listeners.append(lambda e: order.append(("listener", e["event"])))
    monitor = run_monitor(lambda system, data=None: order.append(("callback", system)))
    assert wait_for(lambda: attached(monitor, path))

    append_events(path, event("FSDJump", StarSystem="Alpha"), event("Docked", StationName="Port"))

    assert wait_for(lambda: ("listener", "Docked") in order)
    assert order.index(("callback", "Alpha")) < order.index(("listener", "FSDJump"))


def test_raising_listener_does_not_stop_processing(journal_dir, run_monitor, listeners):
    path = journal_file(journal_dir)
    append_events(path, event("Fileheader", part=1))
    received, seen = [], []

    def broken(_event):
        raise RuntimeError("listener bug")

    listeners.extend([broken, lambda e: received.append(e.get("StarSystem"))])
    monitor = run_monitor(lambda system, data=None: seen.append(system))
    assert wait_for(lambda: attached(monitor, path))

    append_events(path, event("FSDJump", StarSystem="A"))
    append_events(path, event("FSDJump", StarSystem="B"))

    assert wait_for(lambda: received[-2:] == ["A", "B"])
    assert seen[-2:] == ["A", "B"]


def test_listener_survives_monitor_restart(journal_dir, run_monitor, listeners):
    path = journal_file(journal_dir)
    append_events(path, event("Fileheader", part=1))
    received = []
    listeners.append(lambda e: received.append(e.get("StarSystem")))
    first = run_monitor(lambda system, data=None: None)
    assert wait_for(lambda: attached(first, path))
    first.stop()
    first.join(timeout=5)

    second = run_monitor(lambda system, data=None: None)
    assert wait_for(lambda: attached(second, path))
    append_events(path, event("FSDJump", StarSystem="AfterRestart"))

    assert wait_for(lambda: "AfterRestart" in received)


def test_first_file_in_an_empty_folder_is_read_from_its_start(journal_dir, run_monitor, listeners):
    seen = []
    listeners.append(seen.append)
    run_monitor(lambda system, data=None: None)
    time.sleep(0.3)
    path = journal_file(journal_dir)

    append_events(path, event("Fileheader", part=1), event("Location", StarSystem="Sol"))

    assert wait_for(lambda: any(e["event"] == "Location" for e in seen), timeout=8.0)
    assert seen[0]["event"] == "Fileheader"


def test_stop_ends_an_idle_monitor_promptly(journal_dir):
    path = journal_file(journal_dir)
    append_events(path, event("Fileheader", part=1))
    monitor = JournalMonitor(None, manual_journal_path=str(journal_dir))
    monitor.start()
    assert wait_for(lambda: attached(monitor, path))
    time.sleep(0.1)

    began = time.monotonic()
    monitor.stop()
    monitor.join(timeout=5)

    assert not monitor.is_alive()
    assert time.monotonic() - began < 0.3


def test_idle_cpu_stays_low_with_thousands_of_journals(journal_dir, run_monitor, monkeypatch):
    past = time.time() - 3600
    for i in range(3000):
        old = journal_file(journal_dir, f"2020-01-01T{i:06d}")
        append_events(old, event("Fileheader", part=1))
        os.utime(old, (past, past))
    newest = journal_file(journal_dir, "2026-10-11T120000")
    append_events(newest, event("Fileheader", part=1))
    scans = []
    original = JournalMonitor._get_latest_journal_file
    monkeypatch.setattr(JournalMonitor, "_get_latest_journal_file",
                        lambda self: scans.append(1) or original(self))
    monitor = run_monitor(lambda system, data=None: None)
    assert wait_for(lambda: attached(monitor, newest), timeout=15.0)
    scans.clear()

    start = time.process_time()
    time.sleep(4.0)
    used = time.process_time() - start

    # The directory scan is linear in the journal count: with an unchanged directory it must
    # not run every second. (CPU is process-wide, so other test threads add noise; the scan
    # count is the exact check, the CPU bound the spec's R5 limit with margin for that noise.)
    assert len(scans) <= 1, f"{len(scans)} directory scans in 4 s with an unchanged folder"
    assert used < 0.8, f"idle monitor used {used:.2f} s CPU in 4 s with 3000 journals"


def test_failing_directory_stat_does_not_delay_rotation(journal_dir, run_monitor, monkeypatch):
    old = journal_file(journal_dir, "2026-10-11T120000")
    append_events(old, event("Fileheader", part=1))
    seen = []
    monitor = run_monitor(lambda system, data=None: seen.append(system))
    assert wait_for(lambda: attached(monitor, old))
    past = time.time() - 60
    os.utime(old, (past, past))
    real = os.stat

    def flaky(path, *args, **kwargs):
        if str(path) == str(journal_dir):
            raise OSError("directory stat failed")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", flaky)
    new = journal_file(journal_dir, "2026-10-11T130000")
    append_events(new, event("Fileheader", part=1), event("FSDJump", StarSystem="Delta"))

    # One rotation check is 1.5 s away; the retry path would need 3 failed attempts first.
    assert wait_for(lambda: "Delta" in seen, timeout=3.0)


def test_stop_is_prompt_after_an_error_in_the_run_loop(journal_dir, monkeypatch):
    calls = []

    def boom(self):
        calls.append(1)
        raise RuntimeError("lookup bug")

    monkeypatch.setattr(JournalMonitor, "_get_latest_journal_file", boom)
    monitor = JournalMonitor(None, manual_journal_path=str(journal_dir))
    monitor.start()
    assert wait_for(lambda: calls)
    time.sleep(0.1)

    began = time.monotonic()
    monitor.stop()
    monitor.join(timeout=8)

    assert not monitor.is_alive()
    assert time.monotonic() - began < 0.5


def test_listener_error_logs_the_listener_name_and_traceback(journal_dir, listeners, caplog):
    def broken_listener(_event):
        raise RuntimeError("listener bug")

    listeners.extend([broken_listener, lambda e: None])
    monitor = JournalMonitor(None, manual_journal_path=str(journal_dir))
    with caplog.at_level("DEBUG"):
        monitor._dispatch_line(json.dumps(event("FSDJump", StarSystem="A")))

    [record] = [r for r in caplog.records if "listener bug" in r.getMessage() or r.exc_info]
    assert "broken_listener" in record.getMessage()
    assert record.exc_info and record.exc_info[0] is RuntimeError


def test_a_failed_lookup_at_start_is_not_an_empty_folder(journal_dir, run_monitor, listeners,
                                                         monkeypatch):
    import glob as glob_module
    path = journal_file(journal_dir)
    append_events(path, event("Fileheader", part=1), event("Location", StarSystem="Old"))
    seen = []
    listeners.append(seen.append)
    real = glob_module.glob
    state = {"failed": False}

    def flaky(pattern, *args, **kwargs):
        if not state["failed"] and str(journal_dir) in str(pattern):
            state["failed"] = True
            raise OSError("transient")
        return real(pattern, *args, **kwargs)

    monkeypatch.setattr(glob_module, "glob", flaky)
    monitor = run_monitor(lambda system, data=None: None)
    assert wait_for(lambda: attached(monitor, path), timeout=9.0)
    append_events(path, event("FSDJump", StarSystem="New"))

    assert wait_for(lambda: any(e.get("StarSystem") == "New" for e in seen))
    assert state["failed"]
    assert [e.get("StarSystem") for e in seen if e["event"] in ("Location", "FSDJump")] == ["New"]
