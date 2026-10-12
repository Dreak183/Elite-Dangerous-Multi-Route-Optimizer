# Architecture overview

Layers, data flow and invariants of the system, each derived from a spec section or an ADR.

## Fork layout (ADR-0002)

| Path | Owner |
|---|---|
| `edmrn/` (except `ext/`), `main.py`, `run.py`, `setup.py`, `edmrn.spec`, `requirements.txt`, `assets/`, `sounds/` | upstream — hook points only |
| `edmrn/ext/`, `requirements-edmrn.txt`, `tests/` | EDMRN |
| `CLAUDE.md`, `docs/`, `.claude/` | EDMRN project documentation |

## Invariants

- Journal and `Status.json` data enter only through upstream's journal layer (ADR-0001).
- Every live external HTTP request goes through `edmrn/ext/api.py` (API hardening spec A1; rule
  and guard test in `patterns.md`).

## Upstream hook points (ADR-0002)

Every change we make in an upstream file. On a merge conflict, these are the only lines that may be
ours. "Upstream PR" marks changes meant to be offered upstream.

| File | Change | Why | Upstream PR |
|---|---|---|---|
| `edmrn/neutron.py` | Route submission and result polling go through `edmrn.ext.spansh` (`submit_route_job`, `poll_route_result` for 180 s); the error text is the `ApiFailure` sentence; `start_coords` and the result keys `note` and `start_system`; records `last_request` (from, to, range, boost) on success | Spansh answers bad input with 400 and a JSON reason, and 502 or timeouts when overloaded; EDMRN showed `API Error: 400` and gave up after 20 polls (API hardening spec A5, A6, A8) | yes |
| `edmrn/neutron_manager.py` | Range check requires more than 10 LY; passes the journal start coordinates; shows the substituted start; `calculation_done` runs via `root.after` | Spansh rejects ranges of 10 LY or less; a start system unknown to Spansh (explored, undiscovered) is plotted from the nearest known one; the result callback configured Tk widgets from the worker thread (A8) | yes |
| `edmrn/journal.py` | `CarrierJump` handled live like `FSDJump`; the `get_current_*` getters accept it | Location spec R6: after a carrier jump the app showed the previous system | yes |
| `edmrn/journal.py` | `_tail_file`: idle sleep; switches to a newer journal file; processes only newline-terminated lines; the first file is read from its end, later files from their start, a file tailed before resumes at its last offset, and lines are read as bytes and decoded per complete line | Location spec R4/R5: an idle monitor spun a full core, a game restart was never followed, and a half-written line was lost | yes |
| `edmrn/journal.py` | Class attribute `event_listeners` and `_dispatch_line`: each tailed event goes to every listener after upstream's own handling | Extension point for `edmrn/ext/` without a second journal reader (ADR-0001). It is class-level because monitors are re-created in several places across `app.py`, `JournalHandler` and `JournalOperations` | yes |
| `edmrn/galaxy_plotter.py` | `submit_route_job` and `poll_route_results` delegate to `edmrn.ext.spansh` and raise `ApiError`; `plot_route` takes `start_coords` and returns `{"error": <reason>}` instead of `None`, and the substitution `note` on success | One 502 or timeout aborted a job still running on Spansh; an unknown start had no fallback; the reason was replaced by "No route data returned." (API hardening spec A5–A7) | yes |
| `edmrn/app.py` (Galaxy) | Import of `journal_start_coords`; `_calculate_galaxy_route` disables the Calculate button, reads the Tk option variables before the worker starts and passes the journal coordinates; `_display_galaxy_route_result` re-enables the button first and shows the failure reason and the substitution note | The worker read Tk variables; the button stayed clickable during a job; the reason was cleared (A7) | yes |
| `edmrn/custom_route.py` (coordinates) | Imports; `_fetch_coordinates` uses the journal coordinates for the current system, else `edmrn.ext.api.lookup_system`, and records the A9 sentence in `lookup_errors`; `_add_system_async` shows it; `optimize_route` stops with `optimize_error` when a start or end has no coordinates, and `_on_optimize_complete` shows it | Every lookup fell through to EDSM because the Spansh reply was misread; every failure said "System Not Found"; an unknown start or end was silently dropped (API hardening spec A9, ruling 4 of the plan) | yes |
| `edmrn/app.py` (System Info) | Import of `fetch_system_data`; `fetch_edsm_system_data` delegates to `edmrn.ext.system_info`; `_system_info_fetch_callback` shows the failure sentence without a prefix and lists the per-part notes under "Data fetched" | Raw names in EDSM URLs broke on `&`, `#`, `+`; an unknown system was reported as fetched; one failed request discarded all five; network errors went to `edmrn_crash.log` (API hardening spec A10) | yes |
| `edmrn/app.py` (Nearest) | Import of `ApiError`, `nearest_system`; `_find_nearest_system_by_coordinates` asks `edmrn.ext.api.nearest_system` and shows the A2 sentence; `_show_nearest_results` reads the distance from `system` | The distance was read from the top level and always showed 0; failures showed raw exception text, and the error dialogs raised `NameError` (API hardening spec A11) | yes |
| `edmrn/system_autocomplete.py` | Requests go through `edmrn.ext.api.call` without retries; a `ServiceCooldown` skips a service for 60 s after a transient failure | During an outage every keystroke waited up to 5 s + 5 s (API hardening spec A12) | yes |
| `edmrn/app.py` (suggestions), `edmrn/custom_route.py` (`_get_suggestions`), `edmrn/system_info_section.py` (one line) | `_get_system_suggestions` hands results to the widget via `root.after`; Custom Route and System Info use the app's single autocompleter | Results reached widgets on worker threads; Custom Route built a new autocompleter, with an empty cache, per keystroke, and System Info a second one (A12) | yes |
| `edmrn/updater.py` | The release check goes through `edmrn.ext.api.call` with one attempt; `last_error` keeps the reason; a failed manual check says `Could not check for updates: <reason>` | A failed manual check claimed the app was up to date (A13) | yes |
| `edmrn/ui_components.py` | Two lines in `create_neutron_tab` add the "Saved routes" button (`edmrn.ext.neutron_resume`) | Neutron resume spec N3 | no |
| `edmrn/app.py` (neutron resume) | Two lines after the first `JournalMonitor(...)` in `EDMRN_App.__init__`: `edmrn.ext.neutron_resume.install(self)` | Opens `edmrn.db` and starts the neutron autosave | no |
| `edmrn/app.py` (location) | Two lines directly after `install_edmrn_neutron_resume(self)` in `EDMRN_App.__init__`: `edmrn.ext.location.install(self)` | Wires the location tracker, header binding and cold start | no |

## Neutron route persistence (neutron resume spec, ADR-0004)

- `NeutronAutosave` polls upstream's `NeutronRouter` every 2 s on the Tk loop. It compares the
  route object, the current index and the statuses.
- A new route object becomes a row in `edmrn.db`, or replaces a row that asked for a replot to the
  same destination. A change to an existing route updates that row's progress.
- Resume loads a row back into the router. `decide_resume` chooses whether to continue, to advance
  through upstream's jump handler, or to ask the commander.
- `install()` opens and migrates `edmrn.db` during `EDMRN_App.__init__` (on main.py's load thread,
  before any tick); every later read and write happens on the Tk thread.

## Location data flow (location spec)

Each journal line follows this path:

1. Upstream's `JournalMonitor` tails the line and handles it first.
2. `event_listeners` passes it to `LocationTracker.feed`.
3. The tracker computes an immutable `LocationState`.
4. Observers run; the header binding is one of them.

Listeners and observers run on the journal monitor thread, so anything that touches Tk goes through
`root.after`; the header binding does.

At startup, `cold_start` replays the newest journal files once in a daemon thread and hands the
result to `LocationTracker.adopt`. `adopt` never overwrites a newer live state.

`app.location_tracker` is the single source of the current location for our features. The header
label `app.cmdr_location` is also written by upstream; a write trace restores the tracker's text
whenever the tracker knows a system.

Invariant: `edmrn/ext/` code never tails journals. Live data comes only through
`JournalMonitor.event_listeners` (ADR-0001).
