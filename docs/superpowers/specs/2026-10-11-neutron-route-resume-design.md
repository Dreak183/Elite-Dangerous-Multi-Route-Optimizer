# Neutron routes: save and resume — design

Status: Draft, awaiting review
Date: 2026-10-11
Rules: ADR-0002 (upstream hook points only), ADR-0003 (SQLite store). Upstream facts:
`../../knowledge/upstream-baseline.md` ("Neutron Highway and Spansh").

## Purpose

A commander plots a neutron route, flies part of it, closes the game or EDMRN, and later continues
where they stopped without plotting again. Upstream only saves a neutron route into the backup
folder of an optimised CSV route, so a stand-alone neutron route is lost.

## Requirements

- N1. Every successfully plotted neutron route is saved automatically, without user action.
- N2. Progress (visited waypoints and current position) is saved within 3 s of any change, whatever
  changed it: arrow buttons, journal jump detection, or loading.
- N3. The Neutron tab offers a list of saved routes, showing from → to, progress `n/m`, last change
  and state (active or completed). Each route can be resumed or deleted.
- N4. Resuming uses the current system from the journal:
  - **Unknown or on route:** if the current system is unknown, or is on the route at or after the
    waypoint before the saved position, the route continues. If the commander is further ahead,
    the skipped waypoints become visited, using upstream's jump handling.
  - **Elsewhere:** otherwise the commander chooses between two options:
    - **Back to the last waypoint.** The position stays, and the waypoint name goes to the
      clipboard so it can be set as the in-game target.
    - **New route from here.** A new neutron route is plotted from the current system to the saved
      destination with the saved range and boost, including the nearest-known-start fallback. It
      replaces the saved route's waypoints.
- N5. A route whose last waypoint is visited is marked completed.
- N6. A corrupt or newer database never prevents EDMRN from starting. The saved-routes feature
  reports the problem; the rest of the app works.
- N7. Tests never touch the real data dir (CLAUDE.md).

## Design

### Store: `edmrn/ext/store.py` (ADR-0003)

- **`Store(path)`** opens one connection with `check_same_thread=False`, guarded by an
  `RLock`.
  - It applies migrations by `user_version`.
  - `Store.open_default()` uses `AppConfig.get_app_data_path()/edmrn.db`.
  - If opening or migrating fails, it raises `StoreError` with the reason.
- **Schema version 1:**

  ```sql
  CREATE TABLE neutron_routes (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL,           -- ISO 8601 UTC
    updated_at    TEXT NOT NULL,
    from_system   TEXT NOT NULL,
    to_system     TEXT NOT NULL,
    range_ly      REAL NOT NULL CHECK (range_ly > 0),
    boost         TEXT NOT NULL CHECK (boost IN ('x4', 'x6')),
    current_index INTEGER NOT NULL CHECK (current_index >= 0),
    state         TEXT NOT NULL CHECK (state IN ('active', 'completed'))
  );
  CREATE TABLE neutron_waypoints (
    route_id INTEGER NOT NULL REFERENCES neutron_routes(id) ON DELETE CASCADE,
    seq      INTEGER NOT NULL,
    system   TEXT NOT NULL,
    type     TEXT NOT NULL,
    distance REAL NOT NULL,
    jumps    INTEGER NOT NULL,
    status   TEXT NOT NULL CHECK (status IN ('visited', 'unvisited')),
    PRIMARY KEY (route_id, seq)
  );
  ```

- **Functions.** Each runs in one transaction, and all values are bound parameters:
  - `add_neutron_route(meta, waypoints) -> int`
  - `update_neutron_progress(route_id, current_index, statuses)`
  - `replace_neutron_waypoints(route_id, meta, waypoints)`
  - `list_neutron_routes() -> list[RouteSummary]`
  - `load_neutron_route(route_id) -> SavedRoute`
  - `delete_neutron_route(route_id)`

  Input is validated before SQL. Waypoints are dicts with `system` (non-empty str), `type`
  (str), `distance` (number), `jumps` (int) and `status` (`visited`/`unvisited`); anything else
  raises `ValueError`.

### Upstream hook: `edmrn/neutron.py` (one line)

On success, `calculate_route` records `self.last_request = {"from": used_from, "to": to_system,
"range": jump_range, "boost": fsd_boost}`. The autosave reads it, because the router stores neither
the endpoints nor the range.

### Autosave: `edmrn/ext/neutron_resume.py`

- **`NeutronAutosave(app, store)`** runs every 2 s on the Tk loop (`root.after`), so all store
  writes happen on the Tk thread.
  - It compares the router's route object identity, `current_waypoint_index` and the tuple of
    waypoint statuses with its last snapshot.
  - **New route object:** a non-empty route object that differs from the last one is inserted as a
    new row (meta from `last_request`, falling back to the first and last waypoint and the tab's
    range and boost). There are two exceptions:
    - a route the autosave loaded itself (resume) is mapped to its row and not inserted again;
    - a route that answers a pending "new route from here" replaces the waypoints of the pending row.
  - **Same object, changed index or statuses:** `update_neutron_progress`; `state` becomes
    `completed` when the last waypoint is visited.
  - A store error is logged and retried at the next tick. It is never raised into Tk.

### Resume decision: a pure function in `neutron_resume.py`

`decide_resume(systems: list[str], saved_index: int, current: str | None) -> Decision`
- `kind` is one of:
  - `continue`: current is unknown, or on the route at `saved_index - 1` or `saved_index`;
  - `advance`: current is on the route after `saved_index`; `index` is that position;
  - `ask`: anything else.
- The name comparison ignores case and surrounding whitespace.

### UI

- **Upstream hook in `ui_components.create_neutron_tab`:** two lines after the navigation buttons
  call `edmrn.ext.neutron_resume.add_saved_routes_button(app, button_frame)`.
- **The button "Saved routes"** opens a `CTkToplevel` list with *Resume* and *Delete* per row.
- **Resume:**
  - Loads the waypoints and index into `app.neutron_router`, maps the route to its row and calls
    `app.neutron_manager.update_neutron_navigation()`.
  - Then applies the decision. `advance` calls `app.neutron_manager.handle_neutron_system_jump(current)`.
    `ask` shows a dialog with the two N4 options.
  - "New route from here" sets the tab's From, To, range and boost fields and calls the tab's
    calculate command. The autosave replaces the pending row's waypoints when the result arrives.
- **Wiring:** `edmrn.ext.neutron_resume.install(app)` creates the store and starts the autosave.
  Upstream hook in `edmrn/app.py`: two lines directly after
  `self.journal_monitor = JournalMonitor(self._journal_callback)` in `EDMRN_App.__init__`. The
  location feature later adds its own install call next to them. If the
  store cannot be opened, `install` logs the error and the button shows the reason instead of the
  list (N6).

## Error handling

- Database locked or I/O error on save: logged; the next tick retries. On delete or resume: the
  dialog shows the error.
- Corrupt database (`sqlite3.DatabaseError`) or a newer `user_version`: `StoreError`. The feature is
  disabled with a visible reason and nothing is overwritten.
- A route with zero waypoints is never saved.

## Testing

pytest. Every database lives in `tmp_path`, and the conftest home redirect stays active.
- **Store:**
  - migrations from empty, and reopening the same file;
  - add, list, load, progress, replace, delete, and cascade of waypoints;
  - validation rejects bad waypoints and bad boost;
  - hostile system names (quotes, `; DROP TABLE`) are stored verbatim;
  - a newer `user_version` and a corrupt file raise `StoreError`.
- **Autosave with a fake router and a fake root:**
  - a new route is inserted once;
  - a progress change updates the row;
  - completion sets the state;
  - a resumed route is not duplicated;
  - a pending replace updates the row instead of inserting;
  - a store error is swallowed and retried.
- **`decide_resume`:** unknown current; at `saved_index - 1`; at `saved_index`; ahead; behind;
  not on route; case and whitespace differences.
- **NeutronRouter:** `last_request` is set on success and not on failure.
- **UI:** one isolated smoke start (`../../how-to/run.md`). Plot is not possible offline, so the list
  is checked with a pre-seeded `edmrn.db` in the isolated home.

## Not in scope

The general route library (CSV routes), Galaxy Plotter routes, renaming routes, exporting or sending
routes to friends.
