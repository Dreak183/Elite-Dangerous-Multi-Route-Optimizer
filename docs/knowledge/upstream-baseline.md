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
