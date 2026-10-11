# Journal Location Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** EDMRN knows the commander's current system plus station or body from the journal: reliably,
from app start, across carrier jumps and game restarts. The header shows it.

**Architecture:**
- Three small fixes plus a class-level listener hook go into upstream's `edmrn/journal.py`. They are
  offered upstream later.
- A pure state machine in `edmrn/ext/location.py` turns journal events into an immutable
  `LocationState`. A cold start replays the newest journal files.
- One two-line hook in `edmrn/app.py` installs it and binds the header.

**Tech Stack:** Python 3.13, threading, tkinter/customtkinter (`StringVar` traces), pytest.

**Spec:** `docs/superpowers/specs/2026-10-11-journal-location-design.md` (Accepted). Read it with
this plan; the spec wins where they disagree.

## Global Constraints

- **Upstream diffs are minimal** (ADR-0002, `docs/architecture/decisions/0002-stay-close-to-upstream.md`):
  - No reformatting, renaming or reordering in `edmrn/journal.py` and `edmrn/app.py`. Change only
    the lines each task names.
  - Both files use **CRLF** line endings, and they must stay CRLF. Check with
    `git diff --stat` (only the intended line counts) and `file edmrn/journal.py` ("with CRLF line
    terminators").
- **Own code** lives in `edmrn/ext/`, `tests/` and `pytest.ini`.
- **Journal data enters only through upstream's journal layer** (ADR-0001). `edmrn/ext/location.py`
  never tails journals itself; only `cold_start` reads files, once, at startup.
- **No test touches real user folders** (spec R7). `tests/conftest.py` (since commit `23903c2`) is mandatory.
  Run tests only with `.venv/Scripts/python.exe -m pytest` from the repository root.
- **No new runtime dependencies.** `pytest` is already in `requirements-edmrn.txt`.
- **Staging:**
  - Stage explicit paths. Never use `git add -A`, `git add .` or `git add -u`.
  - End every commit message with the line
    `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Documentation is part of the change**, in the same commit (`docs/README.md` rules). Each task
  lists the docs it updates.

## Review Focus

1. **Half-written journal line.** The game may have written only part of a line when the monitor
   reads. The event must be processed once, when the line is complete; it must not be lost.
   Pinned in Task 2.
2. **Monitor restarted by Settings → Apply or a commander switch.** `app.py` creates a fresh
   `JournalMonitor` instance, and the location listener must keep receiving events. Pinned in
   Task 3.
3. **Horizons-style journal names** (`Journal.220315152335.01.log`) next to Odyssey names. Cold
   start must choose by modification time, not by name. Pinned in Task 5.
4. **App closing while the cold start or a live event is in flight.** `root.after` raises, but no
   exception may escape into the monitor thread or the cold-start thread. Pinned in Task 6.
5. **The same commander reloads from the main menu** (second `LoadGame`, same name). The location
   must not be wiped. Pinned in Task 4.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `tests/journal_helpers.py` | Fake journal writing and waiting helpers shared by tests | 1 |
| `tests/test_journal_monitor.py` | Behaviour of the upstream `JournalMonitor` that we changed | 1, 2, 3 |
| `edmrn/journal.py` (upstream) | Carrier jump; idle sleep, rotation, complete lines; listener hook | 1, 2, 3 |
| `edmrn/ext/location.py` | `LocationState`, rules, `LocationTracker`, `cold_start`, `format_location`, `install` | 4, 5, 6 |
| `tests/test_location_tracker.py` | Rule and tracker tests | 4, 5 |
| `tests/test_location_install.py` | Header binding and install tests | 6 |
| `edmrn/app.py` (upstream) | Two-line install hook | 6 |
| `docs/architecture/overview.md` | Upstream hook-point table; location data flow | 1, 2, 3, 6 |
| `docs/how-to/run.md` | "Run the tests" section | 1 |

---

### Task 1: Test scaffold and carrier jumps in the journal monitor

**Files:**
- Create: `tests/journal_helpers.py`, `tests/test_journal_monitor.py`. The scaffold
  (`pytest.ini`, `tests/conftest.py`, `tests/test_isolation.py`) exists since commit `23903c2`.
- Modify: `edmrn/journal.py`. Change the live `if event == 'FSDJump':` in `_process_line`, and both
  `if event in ['FSDJump', 'Location']:` in `get_current_system` and `get_current_coordinates`.
- Docs: `docs/architecture/overview.md`, `docs/how-to/run.md`

**Interfaces:**
- Produces (test helpers, used by Tasks 2–6):
  - `journal_helpers.event(name: str, ts: str = "2026-10-11T12:00:00Z", **fields) -> dict`
  - `journal_helpers.journal_file(directory: Path, stamp: str = "2026-10-11T120000") -> Path`
  - `journal_helpers.append_events(path: Path, *events: dict) -> None`
  - `journal_helpers.wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool`
  - `journal_helpers.attached(monitor, path: Path) -> bool`
  - Existing: the conftest fixture `isolated_home -> Path` (autouse).
- Produces (behaviour): `CarrierJump` triggers `callback(system_name)` and sets
  `monitor.current_system`. `get_current_system()`/`get_current_coordinates()` honour `CarrierJump`.

- [ ] **Step 1: Add the journal test helpers**

The Spansh fix (commit `23903c2`) already created `pytest.ini`, `tests/conftest.py` and
`tests/test_isolation.py`. Do not recreate or overwrite them; read `tests/conftest.py` once so you
know what the `isolated_home` fixture does. Create only `tests/journal_helpers.py`:

```python
"""Fake journal files and polling helpers for tests."""
import json
import os
import time
from pathlib import Path


def event(name, ts="2026-10-11T12:00:00Z", **fields):
    return {"timestamp": ts, "event": name, **fields}


def journal_file(directory: Path, stamp: str = "2026-10-11T120000") -> Path:
    return Path(directory) / f"Journal.{stamp}.01.log"


def append_events(path: Path, *events) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def wait_for(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def attached(monitor, path: Path) -> bool:
    """True once the monitor tails `path` and has positioned itself at its end."""
    current = monitor.current_journal_file
    return (current is not None
            and os.path.basename(current) == Path(path).name
            and monitor.last_tell == Path(path).stat().st_size)
```

- [ ] **Step 2: Run the existing suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all existing tests pass (37 at commit `23903c2`).

- [ ] **Step 3: Write the failing carrier-jump tests**

`tests/test_journal_monitor.py`:

```python
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
```

- [ ] **Step 4: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_journal_monitor.py -v`
Expected: both FAIL:
- `test_carrier_jump_reaches_callback` fails because `wait_for` returns False.
- `test_getters_include_carrier_jump` fails with `'Beta' == 'Gamma'`.

- [ ] **Step 5: Change `edmrn/journal.py`. Only these lines change; keep CRLF.**

In `_process_line`:

```python
            if event == 'FSDJump':
```
becomes
```python
            if event in ('FSDJump', 'CarrierJump'):
```

In `get_current_system` and `get_current_coordinates`, the identical line appears in both places:
```python
                        if event in ['FSDJump', 'Location']:
```
becomes
```python
                        if event in ['FSDJump', 'Location', 'CarrierJump']:
```

- [ ] **Step 6: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: all pass, including the 2 new tests. Then `git diff --stat edmrn/journal.py` should show 3 changed lines
(3 insertions, 3 deletions).

- [ ] **Step 7: Update the docs**

`docs/architecture/overview.md` already has the section "Upstream hook points (ADR-0002)", with
rows for `neutron.py` and `neutron_manager.py`. Add one row to its table:

```markdown
| `edmrn/journal.py` | `CarrierJump` handled live like `FSDJump`; the `get_current_*` getters accept it | Location spec R6: after a carrier jump the app showed the previous system | yes |
```

`docs/architecture/patterns.md` (test isolation) and the "Run the tests" section of
`docs/how-to/run.md` already exist. Extend the latter by one sentence: "Monitor tests start real
threads and poll for up to 5 s each."

- [ ] **Step 8: Commit**

```bash
git add tests/journal_helpers.py tests/test_journal_monitor.py edmrn/journal.py docs/architecture/overview.md docs/how-to/run.md
git commit -m "feat(journal): handle CarrierJump live and in location getters; add test scaffold

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Idle sleep, journal rotation, complete lines only

**Files:**
- Modify: `edmrn/journal.py`, in `_tail_file` only
- Test: `tests/test_journal_monitor.py` (append)
- Docs: `docs/architecture/overview.md` (one table row)

**Interfaces:**
- Consumes: the Task 1 fixtures `journal_dir`, `run_monitor` and the helpers.
- Produces (behaviour; Tasks 3 and 6 rely on it):
  - The first file a monitor instance tails is read from its end.
  - Every later, different file is read from its beginning.
  - A re-tail of the same file resumes at `last_tell`.
  - An idle monitor sleeps.
  - A newer journal file is picked up within about 2 s.
  - Only newline-terminated lines are processed.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_journal_monitor.py`)

```python
import os
import time


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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_journal_monitor.py -v`
Expected:
- `test_idle_monitor_uses_little_cpu` FAILS (about 2 s CPU).
- `test_follows_rotation_to_newer_file` FAILS (timeout).
- `test_half_written_line_is_processed_once_when_complete` FAILS. Upstream reads the fragment,
  drops it as broken JSON and advances past it, so the event is lost.
- `test_start_does_not_replay_existing_lines` passes already; it guards the new first-file logic.

- [ ] **Step 3: Rewrite the body of `_tail_file` in `edmrn/journal.py`**

Keep the method signature, indentation style (4 spaces) and CRLF. Replace the whole method with:

```python
    def _tail_file(self, filename):
        if self.current_journal_file != filename:
            previous_file = getattr(self, '_last_tailed_file', None)
            self.current_journal_file = filename
            self._last_tailed_file = filename
            logger.info(f"New journal file: {os.path.basename(filename)}")
            if previous_file is None:
                with open(filename, 'r', encoding='utf-8') as f:
                    f.seek(0, 2)
                    self.last_tell = f.tell()
            elif previous_file != filename:
                self.last_tell = 0
        retry_count = 0
        last_rotation_check = time.monotonic()
        while not self._stop_event.is_set() and retry_count < self.max_retries:
            try:
                with self._file_lock:
                    current_size = os.path.getsize(filename)
                    if current_size < self.last_tell:
                        logger.info("Journal file size reset")
                        self.last_tell = 0
                        continue
                    if current_size > self.last_tell:
                        with open(filename, 'r', encoding='utf-8') as f:
                            f.seek(self.last_tell)
                            while True:
                                line_start = f.tell()
                                line = f.readline()
                                if not line:
                                    break
                                if not line.endswith('\n'):
                                    f.seek(line_start)
                                    break
                                self._process_line(line)
                            self.last_tell = f.tell()
                        retry_count = 0
                        time.sleep(0.1)
                    else:
                        time.sleep(0.5)
                        if time.monotonic() - last_rotation_check >= 1.0:
                            last_rotation_check = time.monotonic()
                            latest_file = self._get_latest_journal_file()
                            if latest_file and latest_file != filename:
                                logger.info(f"Newer journal file: {os.path.basename(latest_file)}")
                                break
            except FileNotFoundError:
                logger.warning("Journal file not found during tailing")
                break
            except PermissionError:
                logger.error("Permission denied reading journal file")
                retry_count += 1
                time.sleep(1)
            except Exception as e:
                retry_count += 1
                logger.error(f"Journal file reading error (attempt {retry_count}): {e}")
                time.sleep(1)
        self.current_journal_file = None
```

Why each change matters:
- `_last_tailed_file` separates "first file of this monitor": seek to the end, as upstream does, so
  startup does not replay. A "new file appeared while running" is read from 0, because those
  events are live. A "re-tail after an error" resumes at `last_tell`.
- The readline loop stops before an unterminated line, so a half-written event is read again once
  it is complete. Text-mode `tell()`/`seek()` are valid here because the loop uses `readline()`, not
  iteration.
- The `else` branch is the idle sleep plus the rotation check.

- [ ] **Step 4: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: all pass (8 tests). `git diff --stat edmrn/journal.py` shows changes only inside
`_tail_file` plus Task 1's lines.

- [ ] **Step 5: Docs.** Add a row to the hook-point table in `docs/architecture/overview.md`:

```markdown
| `edmrn/journal.py` | `_tail_file`: idle sleep; switches to a newer journal file; processes only newline-terminated lines; the first file is read from its end, later files from their start | Location spec R4/R5: an idle monitor spun a full core, a game restart was never followed, and a half-written line was lost | yes |
```

- [ ] **Step 6: Commit**

```bash
git add edmrn/journal.py tests/test_journal_monitor.py docs/architecture/overview.md
git commit -m "fix(journal): idle sleep, follow journal rotation, read only complete lines

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Class-level event listener hook

**Files:**
- Modify: `edmrn/journal.py`. Add a class attribute and a `_dispatch_line` method. In `_tail_file`,
  the line `self._process_line(line)` becomes `self._dispatch_line(line)`.
- Test: `tests/test_journal_monitor.py` (append)
- Docs: `docs/architecture/overview.md` (one row)

**Interfaces:**
- Consumes: Task 2's `_tail_file`.
- Produces:
  - `JournalMonitor.event_listeners: list[Callable[[dict], None]]` (class attribute, shared by all
    instances).
  - Every newline-terminated line that a tailing monitor reads and that parses to a JSON object is
    passed to each listener as a `dict`, after `_process_line` has run. A listener that raises is
    logged and skipped.

- [ ] **Step 1: Write the failing tests** (append)

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_journal_monitor.py -k listener -v`
Expected: 3 errors at fixture setup: `AttributeError: <class 'edmrn.journal.JournalMonitor'> has no
attribute 'event_listeners'`.

- [ ] **Step 3: Implement in `edmrn/journal.py`** (CRLF; no other changes)

Directly below `class JournalMonitor(threading.Thread):`, before `def __init__`, insert:

```python
    event_listeners = []
```

Directly before `def _tail_file(self, filename):`, insert:

```python
    def _dispatch_line(self, line):
        self._process_line(line)
        if not JournalMonitor.event_listeners or '"event"' not in line:
            return
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        for listener in list(JournalMonitor.event_listeners):
            try:
                listener(data)
            except Exception as e:
                logger.error(f"[JournalMonitor] Event listener error: {e}")
```

In `_tail_file`, inside the readline loop, replace `self._process_line(line)` with
`self._dispatch_line(line)`.

- [ ] **Step 4: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: all pass (11 tests).

- [ ] **Step 5: Docs.** Add a row to the hook-point table:

```markdown
| `edmrn/journal.py` | Class attribute `event_listeners` and `_dispatch_line`: each tailed event goes to every listener after upstream's own handling | Extension point for `edmrn/ext/` without a second journal reader (ADR-0001). It is class-level because `app.py` re-creates monitors in six places | yes |
```

- [ ] **Step 6: Commit**

```bash
git add edmrn/journal.py tests/test_journal_monitor.py docs/architecture/overview.md
git commit -m "feat(journal): class-level event listener hook

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `LocationState`, rules and `LocationTracker`

**Files:**
- Create: `edmrn/ext/location.py` (`edmrn/ext/__init__.py` exists since commit `23903c2`)
- Test: `tests/test_location_tracker.py`

**Interfaces:**
- Produces (`edmrn.ext.location`):
  - `LocationState`: frozen dataclass with the fields `commander`, `system`, `system_address`,
    `coords`, `station`, `market_id`, `station_type`, `body`, `body_id` and `body_type`, plus
    `timestamp`.
    - All fields are `Optional` and default to `None`.
    - `coords` is a `tuple[float, float, float]`.
    - `timestamp` is the journal's ISO string.
  - `apply_event(state: LocationState, event: object) -> LocationState` returns the *same object*
    when nothing changed.
  - `LocationTracker()` has:
    - the `.state` property;
    - `.feed(event: object) -> None`;
    - `.subscribe(observer: Callable[[LocationState], None]) -> None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_location_tracker.py`:

```python
from edmrn.ext.location import LocationState, LocationTracker, apply_event
from journal_helpers import event


def feed_all(*events):
    tracker = LocationTracker()
    for e in events:
        tracker.feed(e)
    return tracker.state


JUMP = event("FSDJump", "2026-10-11T12:00:00Z", StarSystem="Sol", SystemAddress=10477373803,
             StarPos=[0.0, 0.0, 0.0], Body="Sol", BodyID=0, BodyType="Star")


def test_unknown_state_is_all_none():
    assert LocationState() == LocationState(None, None, None, None, None, None, None, None, None, None, None)


def test_fsd_jump_sets_system_and_arrival_star():
    s = feed_all(JUMP)
    assert (s.system, s.system_address, s.coords) == ("Sol", 10477373803, (0.0, 0.0, 0.0))
    assert (s.body, s.body_id, s.body_type) == ("Sol", 0, "Star")
    assert s.station is None
    assert s.timestamp == "2026-10-11T12:00:00Z"


def test_location_docked_sets_station_not_station_body():
    s = feed_all(event("Location", StarSystem="Sol", SystemAddress=1, StarPos=[0, 0, 0],
                       Docked=True, StationName="Abraham Lincoln", MarketID=128016640,
                       StationType="Orbis", Body="Abraham Lincoln", BodyID=5, BodyType="Station"))
    assert (s.station, s.market_id, s.station_type) == ("Abraham Lincoln", 128016640, "Orbis")
    assert s.body is None


def test_location_in_space_has_no_station():
    s = feed_all(event("Location", StarSystem="Sol", SystemAddress=1, StarPos=[0, 0, 0], Docked=False))
    assert s.system == "Sol" and s.station is None


def test_carrier_jump_while_docked_moves_system_and_keeps_station():
    s = feed_all(JUMP,
                 event("Docked", StarSystem="Sol", SystemAddress=10477373803, StationName="X7Z-1TQ",
                       MarketID=3700000000, StationType="FleetCarrier"),
                 event("CarrierJump", "2026-10-11T12:20:00Z", StarSystem="Alpha Centauri",
                       SystemAddress=1, StarPos=[3.03, -0.09, 3.16], Docked=True,
                       StationName="X7Z-1TQ", MarketID=3700000000, StationType="FleetCarrier"))
    assert s.system == "Alpha Centauri"
    assert s.coords == (3.03, -0.09, 3.16)
    assert s.station == "X7Z-1TQ"


def test_jump_clears_station_and_body():
    s = feed_all(JUMP, event("ApproachBody", StarSystem="Sol", Body="Earth", BodyID=3),
                 event("FSDJump", StarSystem="Barnard's Star", SystemAddress=2, StarPos=[-3.0, 1.0, 5.0]))
    assert s.system == "Barnard's Star" and s.body is None and s.station is None


def test_docked_and_undocked():
    docked = event("Docked", StarSystem="Sol", SystemAddress=10477373803,
                   StationName="Abraham Lincoln", MarketID=1, StationType="Orbis")
    s = feed_all(JUMP, docked)
    assert s.station == "Abraham Lincoln" and s.coords == (0.0, 0.0, 0.0)
    s = feed_all(JUMP, docked, event("Undocked", StationName="Abraham Lincoln"))
    assert s.station is None and s.system == "Sol"


def test_docked_in_other_system_replaces_system_and_clears_coords():
    s = feed_all(JUMP, event("Docked", StarSystem="Lave", SystemAddress=99,
                             StationName="Lave Station", MarketID=2, StationType="Coriolis"))
    assert (s.system, s.system_address, s.coords) == ("Lave", 99, None)
    assert s.station == "Lave Station"


def test_body_events():
    s = feed_all(JUMP, event("ApproachBody", StarSystem="Sol", Body="Earth", BodyID=3))
    assert (s.body, s.body_id) == ("Earth", 3)
    s = feed_all(JUMP, event("Touchdown", StarSystem="Sol", Body="Moon", BodyID=4))
    assert s.body == "Moon"
    s = feed_all(JUMP, event("SupercruiseExit", StarSystem="Sol", Body="Mars", BodyID=6, BodyType="Planet"))
    assert (s.body, s.body_type) == ("Mars", "Planet")
    s = feed_all(JUMP, event("ApproachBody", Body="Earth", BodyID=3), event("LeaveBody", Body="Earth", BodyID=3))
    assert s.body is None and s.system == "Sol"


def test_supercruise_exit_at_station_keeps_body():
    s = feed_all(JUMP, event("ApproachBody", Body="Earth", BodyID=3),
                 event("SupercruiseExit", Body="Abraham Lincoln", BodyID=5, BodyType="Station"))
    assert s.body == "Earth"


def test_commander_switch_resets_location():
    s = feed_all(event("LoadGame", Commander="Alice"), JUMP, event("LoadGame", Commander="Bob"))
    assert s.commander == "Bob" and s.system is None


def test_same_commander_reload_keeps_location():
    s = feed_all(event("LoadGame", Commander="Alice"), JUMP, event("Commander", Name="Alice"),
                 event("LoadGame", Commander="Alice"))
    assert s.commander == "Alice" and s.system == "Sol"


def test_malformed_and_unknown_input_is_ignored():
    before = feed_all(JUMP)
    tracker = LocationTracker()
    tracker.feed(JUMP)
    for bad in (None, "text", 42, [], {}, {"event": "Music"}, {"event": "FSDJump"},
                {"event": "FSDJump", "StarSystem": 7}):
        tracker.feed(bad)
    assert tracker.state == before


def test_missing_fields_are_left_empty_not_invented():
    s = feed_all(event("FSDJump", StarSystem="Lave", StarPos=[1, "x", 3]))
    assert s.system == "Lave" and s.coords is None and s.system_address is None


def test_apply_event_returns_same_object_when_unchanged():
    s = apply_event(LocationState(), JUMP)
    assert apply_event(s, event("Music", MusicTrack="Exploration")) is s
    assert apply_event(s, dict(JUMP, timestamp="2026-10-11T13:00:00Z")) is s


def test_observers_called_only_on_change():
    tracker = LocationTracker()
    calls = []
    tracker.subscribe(calls.append)
    tracker.feed(JUMP)
    tracker.feed(JUMP)
    tracker.feed(event("Music"))
    assert [c.system for c in calls] == ["Sol"]


def test_raising_observer_does_not_block_others():
    tracker = LocationTracker()
    calls = []

    def broken(_state):
        raise RuntimeError("observer bug")

    tracker.subscribe(broken)
    tracker.subscribe(calls.append)
    tracker.feed(JUMP)
    assert len(calls) == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_location_tracker.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'edmrn.ext'`.

- [ ] **Step 3: Implement**

`edmrn/ext/location.py`:

```python
"""Current location of the commander, derived from journal events.

Spec: docs/superpowers/specs/2026-10-11-journal-location-design.md.
Event field shapes: knowledge/reference-data.md section 6 in the EDProjects hub.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from edmrn.logger import get_logger

logger = get_logger('Location')

Coords = tuple[float, float, float]


@dataclass(frozen=True)
class LocationState:
    commander: Optional[str] = None
    system: Optional[str] = None
    system_address: Optional[int] = None
    coords: Optional[Coords] = None
    station: Optional[str] = None
    market_id: Optional[int] = None
    station_type: Optional[str] = None
    body: Optional[str] = None
    body_id: Optional[int] = None
    body_type: Optional[str] = None
    timestamp: Optional[str] = None


Observer = Callable[[LocationState], None]

_SYSTEM_EVENTS = ('FSDJump', 'CarrierJump', 'Location')
_BODY_EVENTS = ('ApproachBody', 'SupercruiseExit', 'Touchdown')
_NO_STATION = {'station': None, 'market_id': None, 'station_type': None}
_NO_BODY = {'body': None, 'body_id': None, 'body_type': None}


def _str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _coords(value: Any) -> Optional[Coords]:
    if (isinstance(value, (list, tuple)) and len(value) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        return (float(value[0]), float(value[1]), float(value[2]))
    return None


def _station_fields(event: dict) -> dict:
    return {'station': _str(event.get('StationName')),
            'market_id': _int(event.get('MarketID')),
            'station_type': _str(event.get('StationType'))}


def _body_fields(event: dict, current: LocationState) -> Optional[dict]:
    """Body fields of an event, or None when it names no body or names a station."""
    body = _str(event.get('Body'))
    if body is None or event.get('BodyType') == 'Station':
        return None
    body_type = _str(event.get('BodyType'))
    if body_type is None and body == current.body:
        body_type = current.body_type
    return {'body': body, 'body_id': _int(event.get('BodyID')), 'body_type': body_type}


def _apply_rules(state: LocationState, event: dict) -> LocationState:
    name = event.get('event')
    if name in ('LoadGame', 'Commander'):
        commander = _str(event.get('Commander' if name == 'LoadGame' else 'Name'))
        if commander is None:
            return state
        if state.commander is not None and state.commander != commander:
            return LocationState(commander=commander)
        return replace(state, commander=commander)
    if name in _SYSTEM_EVENTS:
        system = _str(event.get('StarSystem'))
        if system is None:
            return state
        new = LocationState(commander=state.commander, system=system,
                            system_address=_int(event.get('SystemAddress')),
                            coords=_coords(event.get('StarPos')))
        body = _body_fields(event, new)
        if body:
            new = replace(new, **body)
        if event.get('Docked') is True:
            new = replace(new, **_station_fields(event))
        return new
    if name == 'Docked':
        address = _int(event.get('SystemAddress'))
        if state.system is None or (address is not None and address != state.system_address):
            base = LocationState(commander=state.commander, system=_str(event.get('StarSystem')),
                                 system_address=address)
        else:
            base = state
        return replace(base, **_station_fields(event))
    if name == 'Undocked':
        return replace(state, **_NO_STATION)
    if name in _BODY_EVENTS:
        body = _body_fields(event, state)
        return replace(state, **body) if body else state
    if name == 'LeaveBody':
        return replace(state, **_NO_BODY)
    return state


def apply_event(state: LocationState, event: Any) -> LocationState:
    """Next state after one journal event; the same object when nothing changed."""
    if not isinstance(event, dict):
        return state
    new = _apply_rules(state, event)
    if replace(new, timestamp=state.timestamp) == state:
        return state
    return replace(new, timestamp=_str(event.get('timestamp')))


class LocationTracker:
    """Thread-safe holder of the current LocationState; notifies observers on change."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = LocationState()
        self._observers: list[Observer] = []

    @property
    def state(self) -> LocationState:
        return self._state

    def subscribe(self, observer: Observer) -> None:
        self._observers.append(observer)

    def feed(self, event: Any) -> None:
        try:
            with self._lock:
                old = self._state
                new = apply_event(old, event)
                self._state = new
        except Exception as e:
            logger.error(f"Location event ignored: {e}")
            return
        if new is not old:
            self._notify(new)

    def _notify(self, state: LocationState) -> None:
        for observer in list(self._observers):
            try:
                observer(state)
            except Exception as e:
                logger.error(f"Location observer failed: {e}")
```

- [ ] **Step 4: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add edmrn/ext/location.py tests/test_location_tracker.py
git commit -m "feat(location): LocationState, journal rules and LocationTracker

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Cold start and `adopt`

**Files:**
- Modify: `edmrn/ext/location.py`. Add `LocationTracker.adopt` and the module function
  `cold_start`, plus the imports `glob`, `json` and `os`.
- Test: `tests/test_location_tracker.py` (append)

**Interfaces:**
- Consumes: Task 4's `LocationTracker`, `LocationState`.
- Produces:
  - `LocationTracker.adopt(state: LocationState) -> bool` applies the state only when the
    tracker's own timestamp is `None` or older. It notifies observers and returns `True` when it
    applied the state.
  - `cold_start(journal_dir: Optional[str], tracker: LocationTracker, max_files: int = 5) -> Optional[LocationState]`
    replays the newest files by modification time until one yields a system, then calls
    `tracker.adopt`. It returns the replayed state, or `None`.

- [ ] **Step 1: Write the failing tests** (append)

```python
import os
import time

from edmrn.ext.location import cold_start
from journal_helpers import append_events, journal_file


def _set_mtime(path, seconds_ago):
    t = time.time() - seconds_ago
    os.utime(path, (t, t))


def test_cold_start_reads_newest_file(tmp_path):
    older = journal_file(tmp_path, "2026-10-10T120000")
    newer = journal_file(tmp_path, "2026-10-11T120000")
    append_events(older, event("FSDJump", StarSystem="Old", StarPos=[0, 0, 0]))
    append_events(newer, event("Location", StarSystem="New", StarPos=[1, 1, 1], Docked=True,
                               StationName="Dock", MarketID=1, StationType="Coriolis"))
    _set_mtime(older, 120)
    tracker = LocationTracker()

    result = cold_start(str(tmp_path), tracker)

    assert result.system == "New" and tracker.state.station == "Dock"


def test_cold_start_skips_newest_file_without_location(tmp_path):
    older = journal_file(tmp_path, "2026-10-10T120000")
    newer = journal_file(tmp_path, "2026-10-11T120000")
    append_events(older, event("FSDJump", StarSystem="Previous", StarPos=[0, 0, 0]))
    append_events(newer, event("Fileheader", part=1))
    _set_mtime(older, 120)
    tracker = LocationTracker()

    cold_start(str(tmp_path), tracker)

    assert tracker.state.system == "Previous"


def test_cold_start_orders_by_mtime_not_by_name(tmp_path):
    horizons = tmp_path / "Journal.261011150000.01.log"
    odyssey = journal_file(tmp_path, "2026-10-10T120000")
    append_events(horizons, event("FSDJump", StarSystem="HorizonsNewest", StarPos=[0, 0, 0]))
    append_events(odyssey, event("FSDJump", StarSystem="OdysseyOlder", StarPos=[0, 0, 0]))
    _set_mtime(odyssey, 120)
    tracker = LocationTracker()

    cold_start(str(tmp_path), tracker)

    assert tracker.state.system == "HorizonsNewest"


def test_cold_start_without_files_or_folder(tmp_path):
    tracker = LocationTracker()
    assert cold_start(str(tmp_path), tracker) is None
    assert cold_start(str(tmp_path / "missing"), tracker) is None
    assert cold_start(None, tracker) is None
    assert tracker.state == LocationState()


def test_cold_start_tolerates_garbage_lines(tmp_path):
    path = journal_file(tmp_path)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("not json\n{\"event\": \"FSDJump\"\n")
    append_events(path, event("FSDJump", StarSystem="Survivor", StarPos=[0, 0, 0]))
    tracker = LocationTracker()
    cold_start(str(tmp_path), tracker)
    assert tracker.state.system == "Survivor"


def test_live_state_beats_older_cold_start(tmp_path):
    path = journal_file(tmp_path)
    append_events(path, event("FSDJump", "2026-10-11T12:00:00Z", StarSystem="Stale", StarPos=[0, 0, 0]))
    tracker = LocationTracker()
    tracker.feed(event("FSDJump", "2026-10-11T12:05:00Z", StarSystem="Live", StarPos=[1, 1, 1]))

    cold_start(str(tmp_path), tracker)

    assert tracker.state.system == "Live"


def test_adopt_notifies_and_reports():
    tracker = LocationTracker()
    calls = []
    tracker.subscribe(calls.append)
    newer = LocationState(system="A", timestamp="2026-10-11T12:00:00Z")
    assert tracker.adopt(newer) is True
    assert tracker.adopt(LocationState(system="B", timestamp="2026-10-11T11:00:00Z")) is False
    assert tracker.state.system == "A" and len(calls) == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_location_tracker.py -v`
Expected: collection error, `ImportError: cannot import name 'cold_start'`.

- [ ] **Step 3: Implement in `edmrn/ext/location.py`**

Extend the import block to:

```python
import glob
import json
import os
import threading
```

Add to `LocationTracker`, after `feed`:

```python
    def adopt(self, state: LocationState) -> bool:
        """Take a replayed state unless the tracker already holds a newer one."""
        with self._lock:
            current = self._state
            if current.timestamp is not None and (
                    state.timestamp is None or state.timestamp <= current.timestamp):
                return False
            if state == current:
                return False
            self._state = state
        self._notify(state)
        return True
```

Add at module level, after the class:

```python
def cold_start(journal_dir: Optional[str], tracker: LocationTracker,
               max_files: int = 5) -> Optional[LocationState]:
    """Replay the newest journal files until one yields a system; hand it to the tracker."""
    if not journal_dir or not os.path.isdir(journal_dir):
        return None
    try:
        files = sorted(glob.glob(os.path.join(journal_dir, 'Journal.*.log')),
                       key=os.path.getmtime, reverse=True)
    except OSError as e:
        logger.error(f"Cold start could not list journals: {e}")
        return None
    for path in files[:max_files]:
        replay = LocationTracker()
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if '"event"' not in line:
                        continue
                    try:
                        replay.feed(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            logger.warning(f"Cold start skipped {os.path.basename(path)}: {e}")
            continue
        if replay.state.system is not None:
            tracker.adopt(replay.state)
            return replay.state
    return None
```

- [ ] **Step 4: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add edmrn/ext/location.py tests/test_location_tracker.py
git commit -m "feat(location): cold start from the newest journal files

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Install hook, header binding, app wiring

**Files:**
- Modify: `edmrn/ext/location.py`. Add `format_location` and `install`, plus the import of
  `JournalMonitor`.
- Modify: `edmrn/app.py`. Add two lines directly after
  `self.journal_monitor = JournalMonitor(self._journal_callback)` in `EDMRN_App.__init__`.
- Test: `tests/test_location_install.py`
- Docs: `docs/architecture/overview.md` (row + "Location data flow" section)

**Interfaces:**
- Consumes: `LocationTracker`, `cold_start` (Tasks 4–5); `JournalMonitor.event_listeners`
  (Task 3).
- Uses these upstream app attributes: `app.root.after(ms, fn)`, the Tk `StringVar`
  `app.cmdr_location` (`get`/`set`/`trace_add`), and `app.config.journal_path` (`str`, `''` means
  auto-detect).
- Produces:
  - `format_location(state: LocationState) -> Optional[str]`.
  - `install(app) -> LocationTracker` sets `app.location_tracker` and is idempotent.
  - This is the object that the bookmark and exchange features consume later.

- [ ] **Step 1: Write the failing tests**

`tests/test_location_install.py`:

```python
import tkinter as tk
from types import SimpleNamespace

import pytest

from edmrn.ext.location import LocationState, format_location, install
from edmrn.journal import JournalMonitor
from journal_helpers import append_events, event, journal_file, wait_for


class FakeVar:
    """Mimics tk.StringVar: a write trace may set the variable without recursing."""

    def __init__(self, value=""):
        self._value, self._traces, self._in_trace = value, [], False

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        if self._in_trace:
            return
        self._in_trace = True
        try:
            for callback in list(self._traces):
                callback("cmdr_location", "", "write")
        finally:
            self._in_trace = False

    def trace_add(self, mode, callback):
        self._traces.append(callback)


class ImmediateRoot:
    def after(self, ms, fn, *args):
        fn(*args)


class DeadRoot:
    def after(self, ms, fn, *args):
        raise RuntimeError("main thread is not in main loop")


@pytest.fixture(autouse=True)
def listeners(monkeypatch):
    fresh = []
    monkeypatch.setattr(JournalMonitor, "event_listeners", fresh)
    return fresh


def make_app(journal_dir, root=None):
    return SimpleNamespace(root=root or ImmediateRoot(),
                           cmdr_location=FakeVar("[Location: Unknown]"),
                           config=SimpleNamespace(journal_path=str(journal_dir)))


DOCKED = event("Location", StarSystem="Sol", SystemAddress=1, StarPos=[0, 0, 0], Docked=True,
               StationName="Abraham Lincoln", MarketID=1, StationType="Orbis")


def test_format_location():
    assert format_location(LocationState()) is None
    assert format_location(LocationState(system="Sol")) == "Sol"
    assert format_location(LocationState(system="Sol", station="Abraham Lincoln", body="Earth")) == "Sol · Abraham Lincoln"
    assert format_location(LocationState(system="Sol", body="Earth", body_type="Planet")) == "Sol · Earth"
    assert format_location(LocationState(system="Sol", body="Sol", body_type="Star")) == "Sol"
    assert format_location(LocationState(system="Sol", body="Sol A", body_type="Star")) == "Sol"


def test_install_cold_start_sets_header(tmp_path):
    append_events(journal_file(tmp_path), DOCKED)
    app = make_app(tmp_path)

    install(app)

    assert wait_for(lambda: app.cmdr_location.get() == "Sol · Abraham Lincoln")


def test_install_is_idempotent(tmp_path, listeners):
    app = make_app(tmp_path)
    first = install(app)
    assert install(app) is first
    assert app.location_tracker is first
    assert len(listeners) == 1


def test_live_event_through_listener_updates_header(tmp_path, listeners):
    app = make_app(tmp_path)
    install(app)

    listeners[0](event("FSDJump", StarSystem="Alpha", StarPos=[1, 2, 3]))

    assert app.cmdr_location.get() == "Alpha"


def test_upstream_write_is_overridden(tmp_path, listeners):
    app = make_app(tmp_path)
    install(app)
    listeners[0](DOCKED)

    app.cmdr_location.set("Sol")          # what upstream's startup scan writes

    assert app.cmdr_location.get() == "Sol · Abraham Lincoln"


def test_unknown_state_leaves_upstream_label(tmp_path):
    app = make_app(tmp_path)
    install(app)
    app.cmdr_location.set("[Location: Unknown]")
    assert app.cmdr_location.get() == "[Location: Unknown]"


def test_dead_root_does_not_raise_into_monitor(tmp_path, listeners):
    app = make_app(tmp_path, root=DeadRoot())
    install(app)
    listeners[0](event("FSDJump", StarSystem="Alpha"))   # must not raise
    assert app.location_tracker.state.system == "Alpha"


def test_real_tk_trace_reasserts(tmp_path, listeners):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no Tk display")
    root.withdraw()
    try:
        app = SimpleNamespace(root=root, cmdr_location=tk.StringVar(root, value="[Location: Unknown]"),
                              config=SimpleNamespace(journal_path=str(tmp_path)))
        install(app)
        listeners[0](DOCKED)
        assert wait_for(lambda: (root.update(), app.cmdr_location.get())[1] == "Sol · Abraham Lincoln")
        app.cmdr_location.set("Sol")
        assert app.cmdr_location.get() == "Sol · Abraham Lincoln"
    finally:
        root.destroy()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_location_install.py -v`
Expected: collection error, `ImportError: cannot import name 'format_location'`.

- [ ] **Step 3: Implement in `edmrn/ext/location.py`**

Add to the imports:

```python
from edmrn.journal import JournalMonitor
```

Add at module level, after `cold_start`:

```python
def format_location(state: LocationState) -> Optional[str]:
    """Header text, or None when the system is unknown. The arrival star is not shown."""
    if state.system is None:
        return None
    if state.station:
        return f"{state.system} · {state.station}"
    if state.body and state.body_type != 'Star' and state.body != state.system:
        return f"{state.system} · {state.body}"
    return state.system


def install(app) -> LocationTracker:
    """Wire the tracker into a running EDMRN_App. Called once from EDMRN_App.__init__."""
    existing = getattr(app, 'location_tracker', None)
    if isinstance(existing, LocationTracker):
        return existing
    tracker = LocationTracker()
    app.location_tracker = tracker
    JournalMonitor.event_listeners.append(tracker.feed)

    def reassert_header(*_):
        text = format_location(tracker.state)
        if text is not None and app.cmdr_location.get() != text:
            app.cmdr_location.set(text)

    def on_change(_state: LocationState) -> None:
        try:
            app.root.after(0, reassert_header)
        except Exception as e:
            logger.debug(f"Header update skipped: {e}")

    tracker.subscribe(on_change)
    app.cmdr_location.trace_add('write', reassert_header)

    manual_path = getattr(getattr(app, 'config', None), 'journal_path', '') or None
    journal_dir = JournalMonitor(None, manual_journal_path=manual_path).journal_path
    threading.Thread(target=cold_start, args=(journal_dir, tracker),
                     name='edmrn-location-cold-start', daemon=True).start()
    return tracker
```

The `reassert_header` set inside the trace does not recurse: Tcl suspends a variable's traces
while one of them runs. `test_real_tk_trace_reasserts` proves it on real Tk.

- [ ] **Step 4: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: all pass. `test_real_tk_trace_reasserts` may be SKIPPED only where no display exists; on
the Windows dev machine it must PASS.

- [ ] **Step 5: Add the hook to `edmrn/app.py`** (CRLF; exactly two lines added)

After

```python
        install_edmrn_neutron_resume(self)
```

(the neutron resume install lines, which sit directly after
`self.journal_monitor = JournalMonitor(self._journal_callback)`), insert

```python
        from edmrn.ext.location import install as install_edmrn_location
        install_edmrn_location(self)
```

Check: `git diff --stat edmrn/app.py` shows `2 insertions(+)`, and `file edmrn/app.py` still says
CRLF.

- [ ] **Step 6: Isolated smoke start with a fake journal**

Git Bash, from the repository root (`H` is a fresh temp dir; nothing touches real folders):

```bash
H="$(mktemp -d)"; mkdir -p "$H/journal" "$H/EDMRN_Route_Data"
printf '%s\n' '{"timestamp":"2026-10-11T12:00:00Z","event":"Location","StarSystem":"Sol","SystemAddress":1,"StarPos":[0,0,0],"Docked":true,"StationName":"Abraham Lincoln","MarketID":1,"StationType":"Orbis"}' > "$H/journal/Journal.2026-10-11T120000.01.log"
printf '{"journal_path": "%s"}\n' "$(cygpath -m "$H/journal")" > "$H/EDMRN_Route_Data/settings.json"
HOME="$H" USERPROFILE="$H" APPDATA="$H" LOCALAPPDATA="$H" timeout 20 .venv/Scripts/python.exe -u main.py > "$H/smoke.log" 2>&1; echo "exit $?"
grep -iE "error|traceback|Location" "$H/smoke.log" "$H"/EDMRN_Route_Data/logs/*.log
```

Expected:
- `exit 124` (the timeout ended a healthy run).
- No `Traceback`, and no `Location event ignored` or `Location observer failed`.
- While the window is up, the header reads `Sol · Abraham Lincoln`.

Report what the header showed. If no one can look at the window, say so; do not claim it.

- [ ] **Step 7: Docs.** In `docs/architecture/overview.md`, add a hook-point row:

```markdown
| `edmrn/app.py` | Two lines after the first `JournalMonitor(...)` in `EDMRN_App.__init__`: `edmrn.ext.location.install(self)` | Wires the location tracker, header binding and cold start | no |
```

and append:

```markdown
## Location data flow (location spec)

Each journal line follows this path:

1. Upstream's `JournalMonitor` tails the line and handles it first.
2. `event_listeners` passes it to `LocationTracker.feed`.
3. The tracker computes an immutable `LocationState`.
4. Observers run; the header binding is one of them.

At startup, `cold_start` replays the newest journal files once in a daemon thread and hands the
result to `LocationTracker.adopt`. `adopt` never overwrites a newer live state.

`app.location_tracker` is the single source of the current location for our features. The header
label `app.cmdr_location` is also written by upstream; a write trace restores the tracker's text
whenever the tracker knows a system.

Invariant: `edmrn/ext/` code never tails journals. Live data comes only through
`JournalMonitor.event_listeners` (ADR-0001).
```

- [ ] **Step 8: Commit**

```bash
git add edmrn/ext/location.py edmrn/app.py tests/test_location_install.py docs/architecture/overview.md
git commit -m "feat(location): install hook binds tracker to header with cold start

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
