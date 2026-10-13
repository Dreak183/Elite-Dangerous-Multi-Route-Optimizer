# Upstream baseline — what ED Multi Route Navigation already does

What upstream offers in the four areas the fork extends: location, saved routes, bookmarks,
exchange. It also lists the upstream defects that our features would sit on top of.

**Source:** upstream source at `1e8cda4` (v3.3.0), read and executed 2026-10-11. Markers:
**[community]** read in upstream's source; **[probe]** observed by running it in an isolated home
(`../how-to/run.md`). Re-check this file after every upstream merge that touches the named modules.

## Data locations

- App data dir: `Path.home()/Documents/EDMRN_Route_Data`, falling back to `~/EDMRN_Route_Data` when
  `Documents` is missing (`config.AppConfig.get_app_data_path`). There is no env var or setting to
  override it, and `Path.home()` follows `USERPROFILE` on Windows **[community, probe]**.
- Files inside it: `settings.json` (the `AppConfig` dataclass), `logs/edmrn_YYYYMMDD.log`,
  `backups/`, `visited_systems_history.json` **[community, probe]**.
- `slef_store.py` writes `~/.edmrn_slef_store.json`, outside the app data dir **[community]**.
- Journal folder: `~/Saved Games/Frontier Developments/Elite Dangerous`, derived from `~` in four
  places (`journal.JournalMonitor._find_journal_dir`, `utils.get_ed_journal_dir`, `journal_cache`,
  `fuel_tracker` for `Status.json`). Only the `JournalMonitor` honours the `journal_path` setting
  override; the other three ignore it. No Known Folder API is used, so a relocated Saved Games
  folder is not found **[community]**.
- Every start contacts the GitHub releases API of the upstream repository
  (`updater.SimpleUpdateChecker`, a hard-coded repo name). A fork build would therefore check for
  upstream releases **[community, probe]**.

## Current location

- `journal.JournalMonitor` is a polling `threading.Thread`. It picks the newest `Journal.*.log` by
  mtime, seeks to the end of the file and tails it, with no history replay **[community]**.
- Live events: the callback fires only for `FSDJump` (system name) and `Loadout` (jump range, fuel
  capacity). `Location`, `CarrierJump` and `Docked` written while the app runs never reach the
  callback **[probe]**.
- `get_current_system()` re-reads the whole newest file and returns the last `FSDJump` or `Location`.
  It ignores `CarrierJump`: after a carrier jump it returns the previous system **[probe]**.
- **Defect — idle busy-spin:** when the file size is unchanged, `_tail_file` loops without sleeping
  while holding its lock. An idle monitor used 2.88 s CPU in 3 s of wall time **[probe]**.
- **Defect — no file rotation:** `_tail_file` never looks for a newer journal file. After a game
  restart, events in the new file are not seen until the old file disappears **[probe]**.
- The commander filter in `_process_line` only applies when `current_commander` is known. Tailing
  starts at end of file, so `LoadGame` has usually already passed and the filter is inactive
  **[community]**.
- Where the location is used:
  - The header's `cmdr_location` `StringVar`.
  - The jump handler `EDMRN_App._handle_system_jump`. It fills System Info, advances neutron and
    galaxy tracking, and marks the route system visited.
  - `neutron_manager`.

  The monitor is constructed in four places in `app.py`. Two of the restarts pass
  `_handle_system_jump` instead of `_journal_callback`, so `Loadout` takes a different path after a
  settings change **[community]**.
- `Status.json` is read only by `fuel_tracker` (fuel and flags, 1 s polling thread) **[community]**.
- The journal layer hands the fuel tracker only `Loadout.FuelCapacity.Main`: `_process_line` reduces
  the `{Main, Reserve}` object to a float before the callback, and the reserve tank is not passed on.
  Without a `Loadout` (the first journal is tailed from its end) the tracker takes the first `FuelMain`
  it reads from `Status.json` as the capacity **[community, probe]** (2026-10-13).

## Saved routes

- There is exactly one active route: a list of `{name, status, coords, bodies_to_scan, body_count}`
  in `tracker.ThreadSafeRouteManager`. Input is a CSV with `System Name`, `X`, `Y`, `Z` plus
  optional body columns, in the Spansh export shape **[community]**.
- Every optimisation creates `backups/Route_{N}_sys_{timestamp}/` with `optimized_route.csv`,
  `route_status.json` and `current_route.csv`, plus `neutron_route.json`/`galaxy_route.json` when
  used. Quick save creates `QuickSave_*` folders. "Load from backup" lists these folders by name. That
  is the closest thing to a route library. It has no names, metadata or index **[community]**.
- **Defect — autosave writes nothing:** `_perform_autosave` passes the route list as the
  `backup_folder` argument. `save_route_status` fails with `TypeError` inside its own try, returns
  `False` and writes no file, while the app logs "Route status auto-saved" **[probe]**.
- Defect — the writers emit a `Bodies` column but the reload parses `Body_Names` /
  `Bodies_To_Scan_List`, so bodies are lost when a backup is reloaded **[community]**.
- Defect — `BackupManager` writes `current_route.csv` but lists and restores `optimized_route.csv`.
  Its cleanup is never called, so the backup count is not capped **[community]**.
- Route export: only Galaxy Plotter routes can be exported to CSV. There is no JSON format for a
  route **[community]**.

## Neutron Highway and Spansh

- **Defect — Spansh error reason discarded:** `neutron.NeutronRouter.calculate_route` turns any
  non-202 answer into `API Error: <status>` and drops the JSON body. That body names the reason
  (`../../../knowledge/bulk-data.md`, "Spansh API") **[community]**.
- **Start system:** an empty *From* field is filled from `journal_monitor.get_current_system()`.
  When exploring, that system is often not yet known to Spansh, so the request fails with 400
  `Could not find starting system`. There is no fallback to the nearest known system
  (`/api/nearest` is used only by the manual coordinate search in `app.py`) **[community]**.
- There are no retries: one 502 or timeout from an overloaded Spansh ends the calculation
  **[community]**.
- **Saving:** `save_neutron_route` writes `neutron_route.json` (waypoints, statuses, current index)
  only into `app.current_backup_folder`. That folder exists only after a CSV route was optimised or
  loaded, so a stand-alone neutron route is never saved. From, to, range and boost are not stored.
  Reloading happens only as part of "Load from backup", which lists folders that contain a CSV
  **[community]**.

## External APIs

Survey of 2026-10-11 (an agent read every `requests` call). The four marked **[probe]** were checked
against the live services.

- Live call sites:
  - EDSM: System Info (`app.fetch_edsm_system_data`) and the Custom Route fallback.
  - Spansh:
    - Galaxy Plotter (`galaxy_plotter.py`, via `app._calculate_galaxy_route`);
    - Neutron;
    - Nearest by coordinates (`app._find_nearest_system_by_coordinates`);
    - Custom Route coordinates (`custom_route._fetch_coordinates`);
    - autocomplete (`system_autocomplete.py`, with an EDSM fallback).
  - EDAstro: in System Info.
  - GitHub: the updater.

  Nothing retries. User-Agents differ per site, and some send none **[community]**.
- Dead copies: nothing in the UI reaches `galaxy_handler.py` or `journal_handler.py`. The live Galaxy
  code is in `app.py` **[community]**.
- **Defect — Custom Route never uses Spansh:** it reads `result`/`coords`, but the search answers
  `results[]` with top-level `x,y,z`. Every lookup therefore falls through to EDSM, and every
  failure shows "System Not Found" **[probe]**.
- **Defect — Galaxy Plotter hides the reason:**
  - It writes Spansh's error into the output box, then `_display_galaxy_route_result(None)` clears
    it and shows "No route data returned." **[community]**.
  - Its source defaults to the journal system with no fallback.
  - One 502 or timeout while polling aborts a job that is still running on Spansh.
  - The Calculate button is never disabled **[community]**.
- **Defect — Nearest distance is always 0:** `_show_nearest_results` reads a top-level `distance`.
  Spansh nests it in `system`, and `limit` is ignored **[probe]**.
- **Defects in System Info:**
  - EDSM URLs interpolate the raw system name, so `&`, `#` and `+` break the request.
  - An unknown system (`[]`, **[probe]**) is reported as fetched.
  - A failure in any of the five requests discards all of them, and network errors are written to
    `edmrn_crash.log`.
  - A list-shaped factions body raises **[community]**.
- **Autocomplete:** failures are not cached, so each keystroke during an outage waits up to 5 s +
  5 s. The Custom Route tab builds a new autocompleter, and with it an empty cache, per keystroke
  **[community]**.
- **Updater:** a failed manual check reports "already up to date" **[community]**.
- **Tk from worker threads:**
  - The Neutron `calculation_done` configures widgets.
  - The Galaxy thread reads Tk variables.
  - Autocomplete callbacks reach widgets.

  All three run on worker threads **[community]**.

## Bookmarks

- There is no bookmark feature. `visit_history` keeps
  `{system: {first_visit, last_visit, visit_count, source_files}}` and rewrites the file
  non-atomically on every visit. After an optimisation it offers to drop systems that were already
  visited **[community]**.
- `slef_store` (named ship-build codes, written with `utils.atomic_write_json`) is the existing
  pattern for a small named-entry JSON store **[community]**.

## Exchange

- There is no sharing or peer code. Outbound traffic is `requests` to EDSM, Spansh, EDAstro and
  GitHub. The clipboard is used only to copy the next system name **[community]**.

## GUI hook points

- There is no menu bar. `EDMRN_App` in `app.py` builds a `CTkTabview`. A new tab needs three
  things: a `tabview.add` line, an entry in the tab colour list that follows it, and a branch in
  `_lazy_load_tab` (plus `_preload_lazy_tabs` if it should preload). `custom_route.CustomRouteTab(app)`
  is the model for a self-contained tab class **[community]**.
- Services are attributes of `EDMRN_App`, created in its `__init__`. `app.py`, `JournalHandler` and
  `JournalOperations` hold near-duplicate journal logic. Check which copy is live before hooking in
  **[community]**.
- Workers report back with `root.after(0, …)`. `_journal_callback` checks for the main thread before
  doing so **[community]**.
