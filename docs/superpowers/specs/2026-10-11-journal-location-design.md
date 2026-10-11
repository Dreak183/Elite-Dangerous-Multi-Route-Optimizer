# Journal location — design

Status: Accepted (2026-10-11)
Date: 2026-10-11
Feature 1 of 4 (location → bookmarks/route library → exchange). Rules: ADR-0001, ADR-0002.
Upstream facts: `../../knowledge/upstream-baseline.md`. Journal facts:
`../../../../knowledge/reference-data.md` §6.

## Purpose

EDMRN knows the commander's current position reliably, at system plus station or body
resolution. The position is used for four things:

1. The header shows the correct place. That includes after a carrier jump, at game start, and
   after a game restart while EDMRN keeps running.
2. Routes start from the current system: Custom Route, Galaxy Plotter, Neutron.
3. A later feature uses it to bookmark the current place.
4. A later feature shares it with the wing over the exchange channel.

This feature builds 1 and 2 and the state object that 3 and 4 consume. It does not build 3 or 4.

## Requirements

- R1. The location state holds:
  - commander;
  - system name, `SystemAddress` and coordinates (`StarPos`);
  - station: name, `MarketID` and type, while docked;
  - body: name, `BodyID` and type, while near or on a body;
  - the timestamp of the event that last changed it.
- R2. At app start the state reflects the journal, even when the game is not running.
- R3. Live updates are processed within one monitor cycle for `FSDJump`, `CarrierJump`,
  `Location`, `Docked`, `Undocked`, `ApproachBody`, `LeaveBody`, `SupercruiseExit`, `Touchdown`,
  `LoadGame` and `Commander`.
- R4. A game restart that writes a new journal file is followed without restarting EDMRN.
- R5. An idle journal monitor uses negligible CPU.
- R6. Upstream's existing consumers of `get_current_system()`/`get_current_coordinates()` see
  carrier jumps.
- R7. No real user folder is read or written by any test.

## Design

### Upstream changes in `edmrn/journal.py` (offered upstream as PRs)

Each change is a minimal diff with no reformatting (ADR-0002):

- **Idle sleep.** When the file has not grown, `_tail_file` sleeps (0.5 s) before polling again.
- **Rotation.** While tailing, at most once per second, `_tail_file` compares
  `_get_latest_journal_file()` with the file it tails. If a different file is now newest, it returns
  to `run`, which picks up the new file.
  - A new file is read from its beginning, not seeked to its end. A file that appears while
    EDMRN runs was written by the running game, so all of its events are live.
  - The first file seen at monitor start is still seeked to its end, as today.
- **`CarrierJump`.**
  - It is handled live like `FSDJump`: `current_system` is set and `callback(system_name)` fires.
    Upstream's route visited-marking and neutron tracking therefore follow carrier jumps.
  - `get_current_system()` and `get_current_coordinates()` accept it alongside `FSDJump` and
    `Location`.
- **Listener hook.**
  - A class attribute `JournalMonitor.event_listeners: list` holds listeners. The tail loop calls a
    new method instead of `_process_line`. That method runs `_process_line(line)` first and then
    passes the parsed event dict to every listener.
  - Each listener call is wrapped in its own try/except and logged.
  - The hook is class-level because `app.py`, `JournalHandler` and `JournalOperations` construct
    live monitors in six places. One registration survives every restart of the monitor.
  - Listeners run after upstream's handling, so anything a listener schedules on the Tk loop runs
    after what upstream scheduled for the same line.

### `edmrn/ext/location.py` (ours)

- **`LocationState`** is a frozen dataclass with the fields of R1. All fields may be `None`; the
  "unknown" state has every field `None`.
- **`LocationTracker`** is a thread-safe state machine. A lock covers `feed` and `adopt`. It has no
  threads, no Tk and no file I/O of its own.
  - `feed(event: dict)` applies one event, and `state` is the current `LocationState`.
  - `subscribe(fn)` registers an observer. Observers are called with the new state only when the
    state changed, outside the lock.
- **Rules** (field shapes per `reference-data.md` §6.1):
  - **System change.** `FSDJump`, `CarrierJump` and `Location` set system, address and
    coordinates, and clear the station and body.
    - The body is then set from `Body`/`BodyID`/`BodyType` when present.
    - The station is set from `StationName`/`MarketID`/`StationType` when `Docked` is true.
  - **Station.**
    - `Docked` sets the station, but only if its `SystemAddress` matches the current system.
      Otherwise it sets the system name and address from the event and clears the coordinates.
    - `Undocked` clears the station.
  - **Body.** `ApproachBody`, `SupercruiseExit` and `Touchdown` set the body. `LeaveBody` clears it.
    A `SupercruiseExit` with `BodyType` `Station` leaves the body unchanged, because a station is
    not a body.
  - **Commander.**
    - `LoadGame` and `Commander` set the commander.
    - A change to a different known commander resets every other field to `None`. A wrong place is
      worse than an unknown one; the `Location` that follows at game load fills it again.
  - **Malformed input.** Missing fields are skipped field by field. Unknown events and non-dict
    input are ignored.
- **Cold start.** `cold_start(journal_dir, tracker)` reads the journal files newest first, up to 5
  files.
  - Each file is replayed in line order through a fresh tracker. The search stops at the first file
    that yields a system.
  - The result goes to `tracker.adopt(state)`, which applies it only if the tracker's own state has
    no timestamp or an older one. A cold start that finishes after live events have arrived
    therefore never overwrites them.
  - The newest file is chosen by modification time, as in upstream's `_get_latest_journal_file`.
  - The journal directory is the same one the live monitor uses, including the manual
    `journal_path` setting.

### Wiring in `edmrn/app.py`: one hook point

After the services are created in `EDMRN_App.__init__`, one call `edmrn.ext.location.install(app)`
does the following:

- Creates the tracker and stores it as `app.location_tracker`.
- Appends `tracker.feed` to `JournalMonitor.event_listeners`.
- Subscribes a header observer that sets `app.cmdr_location` via `root.after(0, …)`. The text is
  `System · Station` when docked, `System · Body` when near a body, and `System` otherwise. A body
  of type `Star`, or one named like the system, is not shown: `FSDJump` sets the arrival star as the
  body. An unknown state leaves the label untouched.
- Adds a write trace on `app.cmdr_location`. Upstream writes the label too: the startup scan in
  `_get_latest_cmdr_data` and the jump handler. When the tracker knows a system, the trace writes
  the tracker's text back, so the header always shows the tracker's view.
- Starts `cold_start` in a daemon thread.

`install` does nothing on a second call.

## Error handling

- A listener exception is logged per listener. Upstream processing and other listeners continue.
- Missing journal directory or file: the state stays unknown and the header keeps upstream's label.
- Rotation to a file that is empty or not yet readable is retried on the next cycle. The state is
  kept meanwhile.

## Testing

pytest under `tests/`. A fixture redirects `HOME`, `USERPROFILE`, `APPDATA` and `LOCALAPPDATA` to
`tmp_path`, and every journal directory is passed explicitly (R7).

- **Tracker unit tests**, one per rule:
  - jump, carrier jump while docked, `Location` docked and in space;
  - dock and undock, approach and leave body;
  - commander switch;
  - malformed and unknown events;
  - observers called only on change;
  - `adopt` ordering.
- **Cold start**:
  - newest file empty and the previous one has the location;
  - no files;
  - a live event arriving before the cold start finishes wins.
- **Monitor integration** against fake journal files in `tmp_path`:
  - `CarrierJump` reaches the callback and `get_current_system()`;
  - rotation to a new file is followed, including that file's first events;
  - an idle monitor uses under 0.1 s CPU per second;
  - listeners run after the upstream callback;
  - a raising listener stops nothing.
- **Header**: no automated GUI test. One isolated smoke start per `../../how-to/run.md` with a
  fake journal directory configured.

## Not in scope

Bookmarks, route library, sharing the position with the wing, surface coordinates, settlements,
`Status.json`, and upstream's autosave defect (route library spec).
