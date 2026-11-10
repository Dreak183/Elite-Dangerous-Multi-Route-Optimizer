# 02 - Route Tracking tab, in-game overlay, fuel and exobiology

Functional reference of the current GUI. Facts only, taken from the code; no visual judgements. All
paths are relative to `X:\Projects\EDProjects\EDMRN\`. Source references are `file:line` inside
`edmrn/`.

Scope of this file:

1. Tab "Route Tracking" (map, Navigation list, buttons, statistics, progress, dialogs it opens).
2. Backup selection window, visited-systems dialog, status update dialog, generic message dialogs
   as used from Route Tracking.
3. The in-game overlay window (`overlay.py`) and the Settings cards that control it, auto-save and
   backup behaviour.
4. Fuel tracking and exobiology as far as they show in any UI.

Conventions: "Console" = the read-only log textbox in the tab "Route Optimization"
(`ui_components.py:224`, header "Console"). Every `app._log(...)` message is appended there
(`app.py:1262`). The tab "Route Tracking" has no message area of its own; its feedback (copy
confirmations, auto-detected jumps, errors) is visible only in the Console of another tab.
"Status" of a waypoint is one of `unvisited`, `visited`, `skipped` (`tracker.py:11-13`).
"Route" = the list held by `ThreadSafeRouteManager` (`tracker.py:17`), items:
`name`, `status`, `coords [x,y,z]`, `bodies_to_scan [str]`, `body_count`.

---

## 1. Route Tracking tab

- **Purpose** Follow an optimised multi-system route while flying: see all waypoints and their state
  in a list and on a 3D map, mark them visited/skipped (by hand, from the overlay, or automatically
  from the game journal), copy the next system name for the in-game galaxy map, save and reload
  progress.
- **Layout today** The tab is empty (no widgets at all) until a route exists. When a route is loaded
  the tab is rebuilt completely (`route_management.py:339-507`): two framed columns with weights
  60 / 40, 20 px outer padding.

```
+--------------------------------------------+-----------------------------+
| LEFT FRAME (60%)                           | RIGHT FRAME (40%)           |
| +----------------------------------------+ |        Navigation:          |
| | 3D MAP (matplotlib canvas, 85% height) | | +-------------------------+ |
| |  title "Selected: <name>" (after click)| | | 1. Sol -> A 1 > B 2     | |
| |  axes X/Y/Z (LY), route line, points   | | | 2. Colonia              | |
| |  legend: Visited / Skipped / Pending   | | | 3. ...  (scrolls)       | |
| +----------------------------------------+ | |  (rows tinted by status)| |
| +----------------------------------------+ | |                         | |
| | [Copy Next][Data Folder][Open Excel]   | | |                         | |
| | [Load Backup][Quick Save]   (15%)      | | |                         | |
| | Route Statistics | Total | Traveled |..| | |                         | |
| | Progress Status  | Total | Visited |..| | +-------------------------+ |
| +----------------------------------------+ |                             |
+--------------------------------------------+-----------------------------+
```

### Elements

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| TRK-01 | `Route Tracking` | tab button (CTkTabview, 2nd of 8) | Switches to this tab. Tab order: Route Optimization, Route Tracking, Neutron Highway, Galaxy Plotter, Custom Route, System Info, Log, Settings. | Always | app.py:614-621 |
| TRK-02 | (none) | canvas, matplotlib 3D axes (`MiniMapFrame`), figure 6x4 in at 100 dpi, background `#0A0A0F` | Shows every route system with valid coordinates as a point (size 30, black 1 px edge) joined by one orange line (`#FF8C00`, 1 px, alpha 0.8) in route order. Point colour = status: visited `#32B837`, skipped `#FF5D5D`, pending `#E0E0E0`. Axis labels `X (LY)`, `Y (LY)`, `Z (LY)`, dotted orange grid. View: elevation 20, azimuth -60; limits are a cube around the route centre sized to the largest extent. | Route loaded and first system has a `coords` key; plot is drawn in a background thread after the tab is built | minimap.py:12-57, 109-212; route_management.py:497-505 |
| TRK-03 | `Selected: <system name>` | canvas title (matplotlib, bold, orange, 11 pt) | Appears above the map after a system is highlighted (map click, list click). No title before first selection. | After first highlight | minimap.py:395 |
| TRK-04 | Legend: `Visited`, `Skipped`, `Pending` | canvas legend (3 columns, bottom centre of the plot) | Colour key for TRK-02 points. Static, not clickable. | Whenever a plot is drawn | minimap.py:308-334 |
| TRK-05 | highlight ring | canvas marker | Gold (`#FFD700`) hollow ring, size 150, around the highlighted point. Only one at a time. | After a highlight | minimap.py:373-401 |
| TRK-06 | `No route data` / `No valid coordinates` / `Error: <first 50 chars>` | canvas text (centre) | Messages drawn into the empty plot: empty list; no row with 3 numeric coordinates; exception while plotting (also sets an internal error state that disables zoom and click until the next plot). | See state | minimap.py:137-143, 172-178, 204-213 |
| TRK-07 | `Matplotlib not available\n\nRequired packages:\nmatplotlib\nnumpy` | label (red `#FF6B6B`, 12 pt) | Replaces the canvas when `matplotlib.figure` / the Tk agg backend cannot be imported inside the frame constructor. | ImportError in `MiniMapFrame.__init__` | minimap.py:32-43 |
| TRK-08 | `3D Map Module Not Available\n\nRequired packages:\nmatplotlib\nnumpy` | label (red) | Fallback frame used when constructing `MiniMapFrame` raises anything. Has no-op `plot_route`, `highlight_system`, etc.; the map is then inert. Note: `minimap.py:4` imports `matplotlib.pyplot` at module level, so with matplotlib missing the app fails at import time before this fallback can be chosen (route_management.py:9 imports minimap at module level). | Constructor exception | minimap.py:455-473; route_management.py:386-389 |
| TRK-09 | Map interaction: drag | mouse (matplotlib built-in 3D behaviour, no toolbar) | Rotates the 3D view (left button), matplotlib's default 3D controls otherwise. UNVERIFIED: exact default bindings for right/middle button depend on the installed matplotlib version; the code binds nothing for drag. | Map visible | minimap.py:23-31 |
| TRK-10 | Map interaction: mouse wheel | mouse | Zoom around the centre of the current axes limits: wheel up = limits x1.15 (zooms out), wheel down = x0.85 (zooms in). Only when the pointer is inside the axes. Disabled in map error state. There is no reset-view control. | Map visible | minimap.py:50, 86-108 |
| TRK-11 | Map interaction: left click on a point | mouse | Highlights the nearest picked point (pick radius 10) with TRK-05 and TRK-03 and calls the selection callback, which only highlights (`handle_system_click`). It does NOT open the status dialog, does not select/scroll the list row, does not change status. Clicks that start a drag-rotate also fire this handler. | Map visible, no error state | minimap.py:335-372; route_management.py:537-539 |
| TRK-12 | `Copy Next` | button (primary colour, bold 11 pt, height 22) | Copies the name of the first route item whose status is `unvisited` to the clipboard (pyperclip if installed, else Tk clipboard). | Always (visible when route loaded) | route_management.py:401, 584-601 |
| TRK-13 | `Data Folder` | button | Opens the app data folder (`~/Documents/EDMRN_Route_Data`, falls back to home / cwd) in the OS file manager. | Always | route_management.py:406; file_operations.py:34-46 |
| TRK-14 | `Open Excel` | button | Opens a CSV file with the OS default application. Choice order: first `*.csv` (unsorted glob) in `current_backup_folder`; else newest `*.csv` anywhere under `<appdata>/backups`; else newest `Optimized_Route_*.csv` in the app data folder. The label says Excel but the OS default handler is used. | Always | route_management.py:411; file_operations.py:47-87 |
| TRK-15 | `Load Backup` | button | Opens the backup selection window (BKP-xx) if any backup folder with a CSV exists. | Always | route_management.py:416; file_operations.py:119-144 |
| TRK-16 | `💾 Quick Save` | button (success colour, bold) | Writes the current route + status to a new folder `QuickSave_<visited>of<total>_<YYYYmmdd_HHMMSS>` under `<appdata>/backups` (`current_route.csv`, `route_status.json`) and makes that folder the active backup folder for later status saves. | Always; warns when no route | route_management.py:421; file_operations.py:100-135 |
| TRK-17 | `📊 Route Statistics \| Total: 0.00 LY \| Traveled: 0.00 LY \| Average: 0.0 LY` | label (Consolas 12, accent colour, left aligned) | One-line route statistics, see "Data shown". Initial text is the literal shown; replaced right after build. | Route loaded | route_management.py:434-438, 560-568 |
| TRK-18 | `🎯 Progress Status \| Loading...` | label (Consolas 12) | One-line progress counts, replaced by `🎯 Progress Status \| Total: N \| Visited: N \| Skipped: N \| Remaining: N`. | Route loaded | route_management.py:439-443, 549-559 |
| TRK-19 | `Navigation:` | label (Segoe UI 14 bold, accent) | Heading of the right column. | Route loaded | route_management.py:452 |
| TRK-20 | (waypoint list) | scrollable frame, width 300 | One row per route item, in route order, vertical scrollbar when long. No search, no filter, no sort, no auto-scroll to the current waypoint, no "current" marker. | Route loaded | route_management.py:455-496 |
| TRK-21 | `<n>. <system name>` or `<n>. <system name> → <body> > <body> ...` | label per row (Segoe UI 13, left, `hand2` cursor) | Row text: 1-based index, system name; if `bodies_to_scan` is not empty, ` → ` plus body names joined by ` > ` with the system-name prefix removed from each body name; if the joined body text is longer than 100 characters it is cut to 80 characters plus `...`. Row background: visited `#32B837` (green fill), skipped `#FF5D5D` (red fill), unvisited transparent with text `#D1CFCF`. A cleared row keeps its text colour reset to `#D1CFCF`; a visited/skipped row keeps the default text colour. | Per route item | route_management.py:463-496, 540-547 |
| TRK-22 | Row interaction: left click | mouse (`<Button-1>`) | Highlights the system on the map (TRK-05) and opens the Status Update dialog (SUD-xx). No double-click binding (a double click is two presses; the first press opens a modal dialog). No right-click or context menu, no keyboard navigation of the list, no hover change except the pointer cursor. | Row exists | route_management.py:494, 508-536 |
| TRK-23 | `Optimized Route Not Found.\\nPlease create a new route in the 'Route Optimization' tab.` | label (empty state) | Shown when the tab is rebuilt with no route. Note: the string contains a literal backslash followed by `n` (escaped in the source), so the user sees `\n` printed in the text, not a line break. | Route empty and rebuild requested | route_management.py:370 |

### Inputs and validation

- No text entry on this tab. The only inputs are clicks and the map wheel.
- Ship jump range used for the "Average" statistic is read from the field `jump_range` of the
  Route Optimization tab (`app.jump_range`, default config `ship_jump_range = '70.0'`). Non-numeric
  value falls back to `70.0`. It can also be overwritten from journal `Loadout` events
  (`app.py:87-101`). The statistic is not recomputed when that field changes, only on the next
  status change or rebuild.
- Persisted between sessions: only what is written to a backup folder (see "Backup and autosave" in
  section 5): `route_status.json` (`[{"name","status"}]`), `current_route.csv`. The route itself is
  NOT restored at program start (see Observed problems). Overlay opacity/size, auto-start flag,
  autosave interval, fuel settings live in `~/Documents/EDMRN_Route_Data/settings.json`
  (`config.py`). Visit history in `visited_systems_history.json` in the same folder.

### Data shown

| Field | Unit / format | Realistic example | Realistic maximum |
|---|---|---|---|
| Row index | `<n>.` 1-based | `37.` | 4 digits (2000 rows) |
| System name | text, procedural names | `Eorgh Prou JD-J d9-12345` | about 35 characters |
| Bodies suffix | `body > body`, system prefix removed | `A 1 a > A 2 > B 3 c` | cut at 100 -> 80 chars + `...` |
| Row width | label width not wrapped (no `wraplength`) | - | UNVERIFIED: how CTkLabel clips a text longer than the 300 px frame |
| Stats `Total` | `%.2f LY`, sum of straight-line distances between consecutive items | `1843.27 LY` | 6 digits before the point for very long routes |
| Stats `Traveled` | `%.2f LY`, sum of segments from item 0 up to the LAST item with status `visited` (position-based; skipped items in between count as travelled) | `412.05 LY` | as Total |
| Stats `Average` | `%.1f LY` = Total / sum over segments of ceil(segment / jump range) | `63.4 LY` | 3 digits |
| Progress counts | integers `Total`, `Visited`, `Skipped`, `Remaining` (= Total - Visited - Skipped) | `Total: 120 \| Visited: 31 \| Skipped: 2 \| Remaining: 87` | route of 2000 -> 4 digits |
| Map points | one per item with 3 numeric coords | 120 | 2000 (rotate and redraw cost not measured; UNVERIFIED) |
| Map title | `Selected: <name>` | `Selected: Sol` | 35 chars |

When the route has fewer than 2 items: Total = Traveled = 0.00, Average = the jump range value.

### States

| State | What the user sees |
|---|---|
| No route ever loaded | Tab is completely blank (nothing is created until a route exists). |
| Rebuild with empty route | TRK-23 text only. |
| Loading | While a backup loads: Console lines `Loading backup... Please wait.`, `Reading backup from: <folder>`, `Parsing route data...`, `Processing <n> systems...`, `Loaded <n> systems`, `Loading route into tracker...`, `Building UI components...`, `Backup loaded: <folder>`. The tab content is rebuilt after about 100 ms; a success dialog follows after about 800 ms. The map plots in a background thread, so the list and buttons appear before the points. |
| Populated | As in the layout. |
| Map error | TRK-06 text, zoom and click inert. |
| Matplotlib missing | TRK-07 / TRK-08 text in the map area; list and buttons still work. |
| Route complete | No special state; `Copy Next` logs `INFO: Route complete. Nothing to copy.` in the Console. All rows green/red. |
| Duplicate system names in the route | Only the last row with that name is tracked for colour updates (labels stored in a dict by name); status updates hit the first matching item. |

Messages and dialogs triggered from this tab (exact texts):

| Trigger | Dialog / Console text |
|---|---|
| Copy Next ok | Console `'<name>' (Next System) copied to clipboard.` |
| Copy Next, nothing left | Console `INFO: Route complete. Nothing to copy.` |
| Copy Next, clipboard failure | Console `ERROR: Failed to copy system name to clipboard.` |
| Data Folder ok | Console `Opened data folder: '<path>'` |
| Data Folder fail | Error dialog `Error` / `Could not open data folder:\n<path>`; Console `ERROR opening data folder: <e>` |
| Open Excel ok | Console `Opened CSV file: '<file>'` |
| Open Excel, none found | Info dialog `Info` / `No optimized route file found.\n\nPlease optimize a route first, then try again.` |
| Open Excel, OS open failed | falls back to a browser `file://` open; then Info dialog `Info` / `File: <file>\n\nCould not open automatically.\nPlease open manually from:\n<path>` |
| Open Excel, exception | Error dialog `Error` / `Could not open CSV file:\n<e>` |
| Load Backup, no folder | Info `Info` / `No backup folder found.` |
| Load Backup, none with CSV | Info `Info` / `No backup routes found.\n\nBackup routes will be created automatically when you optimize a new route.` |
| Load Backup, exception | Error `Error` / `Error loading backups:\n<e>` |
| Quick Save, no route | Warning dialog `Warning` / `No route to save!` |
| Quick Save ok | Console `Quick save created: <name>`; Info dialog `Quick Save` / `Route saved!\n\nName: <name>\nProgress: <visited>/<total> systems` |
| Quick Save fail | Error `Error` / `Quick save failed:\n<e>` |
| Backup loaded | Success dialog `Success` / `Backup loaded successfully!\n\nRoute: <folder>\nSystems: <n>\nVisited: <n>` |
| Backup load error | Error `Error` / `Error loading backup:\n<msg>` (e.g. `No CSV file found in backup folder`, `No system name column found in CSV`, `Missing coordinate columns in CSV`, `Selected path is not a folder`) |
| Manual status change | Console `'<name>' status updated to: VISITED` / `SKIPPED` / `UNVISITED` |

Generic dialogs (all modal, always on top for 100 ms, centred on the main window, single `OK`
button; Confirm has `Yes` / `No`; icons are large emoji): Info `ℹ️`, Success `✅`, Warning `⚠️`
(orange button `#FFA500`), Error `❌` (red button `#FF6B6B`), Confirm `❓` (`gui.py:977-2143`). Body
text is centred, wrapped at 350 px. Confirm is not used from Route Tracking.

### Automatic behaviour

- **Journal jump (the main automation).** The journal monitor (`journal.py`) reports `FSDJump` and
  `CarrierJump` events (not `Location`) with the system name. `_handle_system_jump` (`app.py:1522`)
  processes a new system once (duplicates ignored via `_last_handled_system`). Unless the current
  main tab is `System Info`, `Neutron Highway` or `Galaxy Plotter`, it schedules
  `update_system_status_from_monitor(system, 'visited')` (`app.py:1637-1638`). That tab condition
  includes `Route Optimization`, `Custom Route`, `Log`, `Settings` and `Route Tracking`, so the
  route advances from any of those tabs, also when Route Tracking is not on screen.
- `update_system_status_from_monitor` (`route_management.py:569-583`): if the system is not in the
  route -> Console `Jumped to '<name>' (not on current route).` and stop. Otherwise set status
  `visited` (only that one item; earlier unvisited items stay `unvisited`, nothing is auto-skipped),
  recolour the row, update the map point, progress and statistics. If an active backup folder exists
  (always, after an optimisation/load/quick save; `None` before) it then (always, even when the
  status did not change) rewrites `route_status.json`, copies the NEXT unvisited system to the
  clipboard and logs `Auto-Detected Jump to '<name>'. Status updated to VISITED.`
- A new system also triggers, outside this tab: System Info refresh, location label update, overlay
  exobiology/bodies reset (`app.py:1560-1590`).
- Journal `Scan`, `SAASignalsFound`, `CodexEntry` only update the System Info log. `Loadout`
  updates `jump_range` (when no ship tracker extension is active) and the fuel capacity
  (`app.py:87-112`). `Exobiology`, `ScanOrganic` feed exobiology (section 4).
- Marking a system `visited` (any path) also writes the visit history (VIS section).
- Optimisation completion (`route_management.py:233-284`): tab switches to Route Tracking, journal
  monitor (re)starts, next system is copied to the clipboard, overlay auto-starts if the setting is on
  (default on), Info dialog `Success` / `Route optimization complete and Auto-Tracking is ready.\nFile: <name>`.
- Journal monitor callback after "Apply" in Settings or a commander switch is `_handle_system_jump`
  directly instead of `_journal_callback` (`app.py:2162, 2320`); see Observed problems.
- No sound is played by Route Tracking. Only the fuel warning has a sound (section 4).

### User flows

1. **Follow a freshly optimised route.** Optimise in Route Optimization -> visited-systems dialog if
   history matches (VIS) -> tab switches to Route Tracking, next system is in the clipboard ->
   paste it into the game's galaxy map -> jump -> the row turns green, map point turns green,
   statistics and progress update, the next system is copied -> repeat.
2. **Mark by hand.** Click a row -> map ring highlights -> Status Update dialog -> `Visited`,
   `Skipped`, `Clear Status` or `Cancel`.
3. **Inspect on the map.** Rotate with the mouse, zoom with the wheel, click a point to see its
   name in the map title (cannot change status from the map).
4. **Save progress now.** Click `💾 Quick Save` -> new folder; Info dialog.
5. **Resume an older route.** `Load Backup` -> select a folder (BKP) -> `Load Selected` -> loading
   messages -> Success dialog. The tab is NOT switched automatically and the journal monitor is not
   restarted by this flow (it runs from startup anyway).
6. **Open data.** `Data Folder` (file manager) or `Open Excel` (CSV in the default app).

### Observed functional problems

1. Browse in Route Optimization destroys all widgets of the Route Tracking tab
   (`file_operations.py:20-21`), leaving it blank until a rebuild, even though the route is still in
   memory.
2. The route is not restored at startup: `_complete_startup_in_background` calls
   `load_route_status()` (`app.py:3024`) which only applies statuses to an already loaded (empty)
   route and reads `temp_route_status.json`, which nothing creates (`BackupManager.restore_backup`
   is never called). The tab stays blank after restart until `Load Backup`.
3. `_perform_autosave` passes the route list as the `backup_folder` argument
   (`app.py:1274-1275`); `Path(list)` raises inside `save_route_status`, which returns False, but
   `Auto-save` still logs `Route status auto-saved` and the manual `💾 Save` still logs
   `Manual save completed`. Auto-save therefore never writes. Real persistence is only the save on each
   status change to `current_backup_folder` (route_management.py:534-536, 580-581).
4. Without an active backup folder (`current_backup_folder is None`), status changes are not saved at
   all and the auto-copy of the next system after a journal jump does not run
   (`route_management.py:580-583`).
5. Map click highlights only (TRK-11) while list click also opens a dialog: the two selection paths
   are not symmetric; the map selection does not select the list row.
6. `update_label_color` for `visited`/`skipped` only sets the background; the text colour is not
   reset, while the theme refresh path recolours with different colours (`#4CAF50`, `#FFA500`,
   `app.py:2175-2185`) than the initial ones (`#32B837`, `#FF5D5D`).
7. `plot_route` runs in a background thread (`route_management.py:499-505`) against a Tk-embedded
   matplotlib canvas.
8. Empty-state text prints a literal `\n` (TRK-23).
9. Duplicate system names collapse in the label dict (see States).
10. "Open Excel" picks `glob("*.csv")[0]` of the active backup folder (arbitrary file if several).
11. `copy_prev_system_to_clipboard` (`route_management.py:603-635`) has a `try/except/else` whose
    `else` runs after a SUCCESSFUL pyperclip copy and logs `INFO: Route complete. Nothing to copy.`
    right after `'<name>' (Previous System) copied to clipboard.`: a misleading second line (the
    `ImportError` fallback path does not log it).
12. `route_management.py:9` imports `MiniMapFrame` at module level; the fallback class cannot be
    reached when matplotlib is absent.

---

## 2. Status Update dialog

- **Purpose** Set the status of one waypoint by hand. Opened by clicking a row in TRK-20.
- **Layout today** Small fixed-size modal window (not resizable), title `Status Update`, always on
  top for 100 ms, icon `assets/explorer_icon.ico` if present.

```
+------------------------------------------+
| Have you visited 'Sol'?                  |
| [Visited][Skipped][Clear Status][Cancel] |
| Visited = Mark as visited (Green) | ...  |
+------------------------------------------+
```

| ID | Label | Type | What it does | Enabled when | Source |
|---|---|---|---|---|---|
| SUD-01 | `Have you visited '<system>'?` | label (Segoe UI 13 bold, wraps at 380 px) | Question text with the system name. | Always | route_management.py:50-57 |
| SUD-02 | `Visited` | button, green `#4CAF50` | Sets status `visited`. | Always | :62-71 |
| SUD-03 | `Skipped` | button, red `#FF5D5D` | Sets status `skipped`. | Always | :73-82 |
| SUD-04 | `Clear Status` | button, orange `#FF8C00` | Sets status back to `unvisited`. Does not remove the visit-history entry. | Always | :84-93 |
| SUD-05 | `Cancel` | button, grey `#666666` | Closes without change. | Always | :95-104 |
| SUD-06 | `Visited = Mark as visited (Green)  \|  Skipped = Mark as skipped (Red)  \|  Clear = Remove status  \|  Cancel = No change` | label (9 pt, grey) | Legend. | Always | :109-115 |
| SUD-07 | Escape key | key | Same as Cancel. Window close button (X): no handler besides default destroy, result stays `None` = no change. | Dialog open | :117 |

Flow after a choice (`route_management.py:508-536`): `visited`/`skipped`/`clear` update the route;
if the status actually changed -> row colour, map point, progress, statistics; if an active backup
folder exists -> `route_status.json` is rewritten and Console logs
`'<name>' status updated to: <STATUS>`. Choosing the status an item already has changes nothing but still
re-saves and logs (the log line is inside the folder check, not the changed check).

States: none besides open/closed. Inputs: none. Data: system name up to ~35 characters.

---

## 3. Backup selection window ("Select Backup Route")

- **Purpose** Choose which saved route folder to load (route CSV plus progress file).
- **Layout today** Resizable window, 800x550, centred on the main window, modal (`grab_set`),
  title `Select Backup Route`. Header title and sub-title labels are placed in the same grid cells
  as the column-header frame and the list (see problems), so in practice the visible stack is:
  column header row, scrollable list, button row.

```
+-----------------------------------------------------------+
| [ ] Route Name | Systems | Progress | Modified | Status    |
| (o) Route_85_sys_20261108_2011 | 85 | 31/85 | 11-08 | ✅    |
| ( ) QuickSave_12of85_2026...   | 85 | 12/85 | 11-09 | ✅    |
|  ... scrollable ...                                       |
| [📁 Manual Select] [✕ Cancel] [✅ Load Selected]          |
+-----------------------------------------------------------+
```

| ID | Label | Type | What it does | Enabled when | Source |
|---|---|---|---|---|---|
| BKP-01 | `Select Backup Route to Load` | label (18 bold, primary colour) | Window heading (grid row 0). | - | gui.py:1471-1476 |
| BKP-02 | `Select a backup folder to load both the route CSV and your progress` | label (12) | Sub-heading (grid row 1). | - | gui.py:1477-1482 |
| BKP-03 | Header: (blank), `Route Name`, `Systems`, `Progress`, `Modified`, `Status` | label row | Column headings. | - | gui.py:1483-1496 |
| BKP-04 | radio button per row | radio (CTkRadioButton, shared variable) | Selects the folder. The newest folder is preselected. | Row exists | gui.py:1696, 1549-1550 |
| BKP-05 | Route Name | label | Folder name, cut to 22 characters + `...` when longer than 25. | - | gui.py:1634-1636, 1702 |
| BKP-06 | Systems | label | Number of lines in the first `*.csv` minus 1 (`?` when no CSV or unreadable). Counts raw lines, so quoted multi-line cells would miscount. | - | gui.py:1637-1649 |
| BKP-07 | Progress | label | `<visited>/<total>` from `route_status.json`; `No data` when the file is absent; `Error` (red) when unreadable. Colour by percentage: 0 % default text, <50 % `#FFA500`, <90 % `#FFD700`, >=90 % `#4CAF50`. | - | gui.py:1650-1672 |
| BKP-08 | Modified | label | Folder mtime as `MM-DD` (no year, no time), `Unknown` on error. | - | gui.py:1673-1677 |
| BKP-09 | Status | label (emoji) | `✅` CSV and status file present (green), `📝` CSV only (orange), `❌` neither (red; cannot happen for listed folders). | - | gui.py:1678-1686 |
| BKP-10 | `📁 Manual Select` | button | Opens a folder picker (`Select Backup Folder`, starts in the backups folder). If the chosen folder has no `*.csv`: Warning dialog `Warning` / `No CSV file found in:\n<path>\n\nPlease select a folder containing a route CSV file.` Else loads it immediately and closes. | Always | gui.py:1511-1524, 1707-1724 |
| BKP-11 | `✕ Cancel` | button | Closes the window. | Always | gui.py:1525-1536 |
| BKP-12 | `✅ Load Selected` | button (primary) | Loads the selected folder and closes. No selection: Warning `Warning` / `Please select a backup folder.`; vanished folder: Error `Error` / `Folder no longer exists:\n<path>`. | Always | gui.py:1537-1548, 1725-1734 |

Inputs and validation: the list is every sub-folder of `<appdata>/backups` containing at least one
`*.csv`, sorted newest first by folder mtime. Backup folder names created by the program:
`Route_<n>_sys_<timestamp>` (optimizer.py:400), `QuickSave_<v>of<t>_<timestamp>`
(file_operations.py:109), `Manual_Backup_<timestamp>` (backup.py:87). No delete, rename, search or
preview.

What loading does (`file_operations.py:145-312`, runs in a background thread): reads
`current_route.csv`, else `optimized_route.csv`, else the newest `*.csv`. Accepts name columns
`System Name`, `system_name`, `System`, `Name` and coordinate columns `X/x/Coord_X`, `Y/y/Coord_Y`,
`Z/z/Coord_Z`; rows with non-numeric coordinates are skipped (logged). Bodies are read from
`Body_Names` (list literal), `Body_Count`, or `Bodies_To_Scan_List`. Statuses come from
`route_status.json` by system name (default `unvisited`). The backup folder becomes the active
backup folder. A neutron route and a Galaxy Plotter route stored in the same folder are restored too
(Neutron/Galaxy tabs update). Overlay auto-starts after 500 ms if the setting is on. Note: `Quick Save`, `Manual_Backup` and the filtered `current_route.csv` written after optimisation
write the column `Bodies` (plus `Body_Count`), while the loader reads `Body_Names` /
`Body_Count` / `Bodies_To_Scan_List` and tries `current_route.csv` first, so the bodies list of a
reloaded route is empty (the optimiser's own CSV has `Body_Names` but loses against
`current_route.csv`).

States: empty list is not shown (the Info dialog `No backup routes found...` appears before the
window opens). Error: `Error` in Progress column. Modal: blocks the main window.

Observed functional problems: (a) grid collision: heading (row 0) and sub-heading (row 1) share
cells with the header frame and scroll frame (`gui.py:1476, 1484, 1482, 1498`), so one of each pair
is covered or overlapping; (b) `refresh_list` exists but nothing calls it; (c) no delete or
cleanup of old backups anywhere (`BackupManager._cleanup_old_backups` is never called, section 5);
(d) loading does not switch to the Route Tracking tab.

---

## 4. Visited systems dialog ("Previously Visited Systems") and visit history

- **Purpose** After an optimisation, ask whether systems already visited in the past should stay in
  the new route.
- **Layout today** Modal 550x400 (fixed), title `Previously Visited Systems`, opened by
  `_check_visited_systems` after optimisation only (not when loading a backup), before the route is
  loaded into the tracker. Only opens when at least one route system is in the history file.

```
+--------------------------------------------------+
|        Found 12 Previously Visited Systems       |
| These systems are already in your visit history. |
| What would you like to do?                       |
| +----------------------------------------------+ |
| | [x] Sol (visited 3 times)   Last: 2026-11-08 | |
| | [x] Colonia                 Last: 2026-10-01 | |
| +----------------------------------------------+ |
| [Remove Selected]   [Keep All]   [Cancel]        |
+--------------------------------------------------+
```

| ID | Label | Type | What it does | Source |
|---|---|---|---|---|
| VIS-01 | `Found <n> Previously Visited Systems` | label (14 bold) | Count of matches. | visit_history_dialog.py:56-61 |
| VIS-02 | `These systems are already in your visit history. What would you like to do?` | label (grey) | Explanation. | :63-69 |
| VIS-03 | checkbox per system (default ON, no label text) | checkbox list in scroll frame (490x220) | Marks systems to remove. | :82-124 |
| VIS-04 | `<name>` or `<name> (visited <n> times)` (n>1) | label | System name. | :103-113 |
| VIS-05 | `Last: <YYYY-MM-DD HH:MM:SS>` | label (9 pt grey, right) | Last visit timestamp. | :115-122 |
| VIS-06 | `Remove Selected` | button (red `#FF6B6B`) | Removes every checked system from the new route (Console `INFO: Removed <n> previously visited systems from route` and `New route: <n> systems (was <m>)`). With nothing checked the route is returned unchanged. | :129-138, 173-179 |
| VIS-07 | `Keep All` | button (green) | Keeps the route as is (Console `INFO: Keeping all <n> previously visited systems in route`). | :140-149, 181-185 |
| VIS-08 | `Cancel` | button (grey) | Same effect as Keep All (route unchanged), no log line. Escape = Cancel. Window X = no handler, result `None`, route unchanged. | :151-160, 171, 187-191 |

Data and limits: one row per matching system, up to the full route (e.g. 2000); only the scroll
frame scrolls, the dialog does not resize. Source data `visited_systems_history.json` in the app
data folder: `{ "<system>": {first_visit, last_visit, visit_count, source_files[]} }`
(`visit_history.py`). An entry is created or updated whenever a route item changes to `visited`
by any path (`tracker.py:40-45`). There is no UI to browse, edit or clear the history
(`clear_system`, `clear_all` have no callers); `Clear Status` does not touch it. The same JSON
file is also written directly by `ExobioManager._save_exobio_to_history` (`exobiology.py:101-150`),
which adds `body`, `type`, `genus`, `completed` keys to a system's entry through its own
read-modify-write while `VisitHistoryManager` keeps an in-memory copy and rewrites the whole file;
the two writers can overwrite each other's keys (observed from code, not reproduced).

---

## 5. Backup, quick save and auto-save behaviour

- **Purpose** What is saved when, since Route Tracking shows no save indicator.
- Elements are in the sections above (TRK-14..16, BKP-xx) and in the Settings cards ASV-xx.

| What | When it happens | Where it goes | Source |
|---|---|---|---|
| Optimiser backup | Each optimisation | `<appdata>/backups/Route_<n>_sys_<timestamp>/` with CSV and `route_status.json`; `current_route.csv` is rewritten (filtered) after the visited-systems dialog; folder becomes the active backup folder | optimizer.py:400-436; route_management.py:241-271 |
| Status save | Every status change (dialog, journal jump, overlay `>`), and also on a no-op repeat | `route_status.json` in the active backup folder; only when one exists | route_management.py:534, 580; app.py:1665 |
| Quick Save | Button TRK-16 | New `QuickSave_*` folder, becomes active | file_operations.py:100-135 |
| `BackupManager.create_backup` | Once at startup (500 ms after launch) | Does nothing: it returns `None` when the route is empty, and the route is always empty at startup. The scheduler (`start`, daily/weekly/monthly timers), `set_frequency`, `get_backup_list`, `restore_backup`, `_cleanup_old_backups` (max 10) have no callers. | app.py:3022; backup.py |
| Auto-save | Only if the user presses `▶ Start` in Settings; interval from the dropdown. Never starts by itself. | Intended: route status. Actual: see Observed problem 3 in section 1; nothing is written. | app.py:1272-1278, 2091-2103 |

---

## 6. In-game overlay window

- **Purpose** A small, borderless, always-on-top window over the game showing where the commander
  is in the active route, the next system, progress, fuel, scan targets and exobiology progress,
  with buttons to step through the route and to copy the system name.
- **Layout today** A separate top-level Tk window in its own thread (`tk.Tk()` in
  `OverlayUIThread`, `overlay.py:16-28, 167-169`), no title bar (`overrideredirect`), always on
  top, translucent. Colours: background `#0D0D0D`, header `#1A1A1A`, text `#E0E0E0`, accent
  `#FF8C00`. Start geometry `220x220+100+100`, then fitted to content.

```
+------------------------------+
| EDMRN                      ✕ |  <- header, 25 px, drag handle
+------------------------------+
| 📍 Sol                READY  |  <- system (left), status (right)
| ---------------------------- |
| ➡️ Colonia                   |  next system
| 31/120 (25%)                 |  progress line 1
| 🚀 62/188 jumps              |  progress line 2 (route tracking, neutron)
| 🧭 412.05/1843.27 LY         |  distance
| 🚀 Fuel: 68% (27.2/40.0t)    |
| [=========-----]             |  fuel bar, 6 px
| ---------------------------- |
| 🎯 Scan:                     |
| • A 1 a                  ^|  |  scrolling list, 60-220 px high
| • A 2                     |  |
| Exobiology scans:         v|  |
| 🟡 Bacterium (2/3)           |
+------------------------------+
| [RT][NH][GP][CR]             |  tab buttons
| [<] [   EDMRN / name   ] [>] |  step buttons + copy button
+------------------------------+
```

### Elements

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| OVL-01 | `EDMRN` | label (Segoe UI 9 bold, accent) in header | Window title text; part of the drag handle. | Always | overlay.py:185-201 |
| OVL-02 | header bar | drag area (cursor `fleur`) | Press and drag moves the window; clamped so the whole window stays on the primary screen. There is no resize handle; size comes from OVL-30 and content. Position is not saved (always opens at +100+100). | Always | overlay.py:185-190, 446-465 |
| OVL-03 | `✕` | label acting as button (hand cursor) | Closes the overlay: hides the window, then asks the main app to toggle the overlay, which stops it and sets the Settings button to `Stop Overlay` -> `Start Overlay`. Restart from Settings, Ctrl+O, or auto-start. | Always | overlay.py:202-211, 497-504; app.py:1280-1294 |
| OVL-04 | `📍 <system>` (initial `📍 Loading...`) | label (Segoe UI 9) | Current system name, cut to 18 / 25 / 30 characters + `...` for size Small / Medium / Large. | Always | overlay.py:215-223, 722-732 |
| OVL-05 | `READY` / `VISITED` / `SKIPPED` / `NEUTRON` | label (8 bold), top-right of the content area | Status of the current waypoint. Colours: `READY` `#E0E0E0` (also any unknown status, e.g. `GALAXY`, `current`, `pending`), `VISITED` `#4CAF50`, `SKIPPED` `#FFA500`, `NEUTRON` `#00AEFF`. Route Tracking data only ever yields `READY` or `VISITED`. | Always | overlay.py:224-232, 737-751 |
| OVL-06 | `➡️ <next system>` (initial `➡️ ...`) | label (accent colour) | Next system, same truncation. | Always | overlay.py:234-242, 752-756 |
| OVL-07 | `<visited>/<total> (<pct>%)` or tab specific (initial `0/0`) | label (Consolas 9) | First line of `progress` (text before the first newline). | Always | overlay.py:243-251, 757-767 |
| OVL-08 | `🚀 <traveled>/<total> jumps` | label (Consolas 9) | Second line of `progress` if present; empty otherwise. | Route Tracking, Neutron data | overlay.py:252-260, 759-767 |
| OVL-09 | `🧭 <traveled>/<total> LY` (initial `🧭 0.00/0.00`) | label (Consolas 9) | `distance` field, or `traveled_distance/total_distance`. | Always | overlay.py:261-269, 768-773 |
| OVL-10 | `🚀 Fuel: <pct>% (<current>/<capacity>t)` / `🚀 Fuel: ---%` / `🚀 On Foot` (initial `🚀 Fuel: ---%`) | label (Consolas 9) | Fuel in percent and tonnes (`%.0f`, `%.1f/%.1f`). Colour from the fuel colour rule (section 8); `On Foot` and `---%` are grey `#888888`. | Always (data from fuel tracker) | overlay.py:270-278, 774-789 |
| OVL-11 | (fuel bar) | canvas, height 6 | Filled proportion of the bar in the fuel colour on a `#333333` track; cleared when on foot. | Always | overlay.py:279-285, 790-802 |
| OVL-12 | `🎯 Scan:` | label (9 bold, accent) | Heading of the scan list. | Always | overlay.py:287-295 |
| OVL-13 | `• <body>` | labels in a scroll canvas (Segoe UI 8) | Bodies to scan of the current system (names without the system prefix): up to 6 rows, each cut to 18 / 25 / 30 characters + `...`, then `... +<n> more` (7 pt grey). Empty or placeholder -> `• No bodies` (grey). | Always | overlay.py:597-645 |
| OVL-14 | scrollbar | ttk vertical scrollbar | Scrolls OVL-13 / OVL-15 when taller than the canvas (60-220 px, grows to content). The mouse wheel is not bound. | Content taller than canvas | overlay.py:296-314 |
| OVL-15 | `Exobiology scans:` + `<icon> <species> (<n>/3)` | labels appended below the bodies inside the same scroll area | Heading (8 bold, gold `#FFD700`) and one row per species of the current system: up to 6 rows, then `... +<n> more`. `🟢` n>=3 (`#A3FFB0`), `🟡` n=2 (`#FFD700`), `🟠` n=1 (`#FFA500`), `🔴` n=0 (`#FF5D5D`). Counts are capped at 3. Cleared on every new system. Heading and rows only exist when at least one species is known. | Species list not empty | overlay.py:803-841; app.py:1339-1346 |
| OVL-16 | `RT` | button (3 chars wide) | Switch the main window to the tab `Route Tracking` and make this the data source. Active tab button: orange background `#FF8C00`, dark text; inactive: `#2A2A2A` background, `#888888` text. | Always; briefly disabled | overlay.py:329-338 |
| OVL-17 | `NH` | button | Same for `Neutron Highway`. | same | overlay.py:340-349 |
| OVL-18 | `GP` | button | Same for `Galaxy Plotter`. | same | overlay.py:351-360 |
| OVL-19 | `CR` | button | Same for `Custom Route`. Not in the brief's RT/NH/GP list but present. | same | overlay.py:362-371 |
| OVL-20 | `<` | button | Previous waypoint. In Route Tracking: copies the system before the first unvisited one to the clipboard (no status change). NH/GP/CR: step back in that route (CR also copies the current system). | Needs a valid cached tab | overlay.py:387-393, 868-896 |
| OVL-21 | `<current system name>` (initial `EDMRN`) | button (accent background, 8 bold, width 8, stretches) | Label shows the current system name (cut to size limit minus 5 + `...`), but the action copies: Route Tracking -> NEXT unvisited system (Copy Next); NH/GP -> that route's current system; CR -> current system. | Needs a valid cached tab | overlay.py:395-408, 733-736, 935-950 |
| OVL-22 | `>` | button | Next waypoint. In Route Tracking: marks the first unvisited system as `visited` (same effects as a jump: row, map, progress, statistics, save, Console `Overlay: '<name>' marked as visited.`) and copies the following next system. NH/GP: next waypoint. CR: next waypoint plus copy. | Needs a valid cached tab | overlay.py:410-416, 898-926; app.py:1643-1674 |

Hotkeys: none inside the overlay. Main window `Ctrl+O` starts/stops the overlay (`app.py:1766`),
only while the main window has keyboard focus (Tk binding, not global). No other hotkeys exist in
overlay.py or app.py (grep of `bind(`).

### Inputs and validation (Settings cards that control the overlay, `settings_manager.py:53-107`)

| ID | Label | Type | Behaviour | Default / persisted |
|---|---|---|---|---|
| OVS-01 | `📺 Overlay` | card heading | - | - |
| OVS-02 | `▶ Start Overlay` / `⏹ Stop Overlay` | button | `_toggle_overlay`: starts or stops the overlay window. After the first toggle the text becomes `Start Overlay` / `Stop Overlay` (without symbols, `app.py:1285, 1293`). Console `Cross-Platform Overlay started!` / `Overlay stopped`. | Starts stopped |
| OVS-03 | `Opacity:` + value `<n>%` | label + label | Shows current opacity. | `overlay_opacity`, default 80 |
| OVS-04 | (slider) | slider 50-100, 10 steps (5 % each) | Saves immediately to settings.json and applies `-alpha` to the running overlay. Initial alpha is read when the overlay starts. | persisted |
| OVS-05 | `Size:` | label | - | - |
| OVS-06 | `Small` / `Medium` / `Large` | option menu | Saves `overlay_size`; if the overlay runs: resize to 200x320 / 220x380 / 260x440, scan canvas height 40 / 80 / 100 px (later overwritten by `max(60, h-140)` = 180 / 240 / 300 then limited by the content-fit rule), and switches the text truncation limits 18 / 25 / 30. Console `Overlay size: <size>`. The saved size is NOT applied when the overlay starts; the truncation limit defaults to Medium (25) until the menu is used. The height is re-fitted to content every second, so effectively the preset changes width (200/220/260), limits and list height. | persisted, default `Medium` |
| OVS-07 | `Auto-start:` | label | - | - |
| OVS-08 | (switch, no text) | switch | `auto_start_overlay_when_ready`: if on, the overlay starts by itself when a route becomes ready (optimisation done, backup loaded, Galaxy Plotter or Custom Route or Neutron route ready). Console `Auto-start overlay when route ready: ON/OFF`. | persisted, default ON |

There is no setting for position, always-on-top (always on), click-through, font size, or which
sections to show. On Linux the window type is set to `dock`.

### Data shown

| Field | Unit / format | Realistic example | Realistic maximum |
|---|---|---|---|
| System / next system | text | `Eorgh Prou JD-J d9-12345` | ~35 chars, cut at 18/25/30 |
| Status | one word | `VISITED` | 7 chars |
| Progress (RT) | `<v>/<t> (<p>%)` | `31/120 (25%)` | `2000/2000 (100%)` |
| Jumps (RT) | `🚀 <traveled>/<total> jumps`, per segment `ceil(distance / jump range)` | `🚀 62/188 jumps` | 5 digits each |
| Distance | `<traveled>/<total> LY` | `412.05/1843.27 LY` | `123456.78/123456.78 LY` |
| Neutron progress | `⚡ <i>/<n> neutrons` + jumps | - | - |
| Galaxy progress | `🎯 <i>/<n> waypoints` + `⚡ <k> neutron boosts` | - | - |
| Custom route | `<i>/<n> \| <d> LY total`, distance `<d> LY \| <j> jumps` | `3/12 \| 840 LY total` | - |
| Fuel | `<pct>% (<cur>/<cap>t)` | `68% (27.2/40.0t)` | `100% (64.0/64.0t)` |
| Bodies | up to 6 rows | `A 1 a` | 6 + `... +<n> more` |
| Species | up to 6 rows | `🟡 Bacterium (2/3)` | 6 + `... +<n> more` |
| Window width / height | px | 220 x about 380 | width 200-260 by preset; height fitted to content |

Data source selection (`app.py:1325-1352`): by `_cached_tab_name`: `Neutron Highway` -> neutron
router; `Galaxy Plotter` -> galaxy plotter; `Custom Route` -> custom route; anything else ->
Route Tracking. Exobiology and fuel are appended for all tabs. Route Tracking "current system"
= the LAST item (in list order) with status `visited`, else the first `unvisited` item, else the first
item; "next system" = the item right after it in list order (not the first unvisited item;
differs from Copy Next when items were skipped or marked out of order) (`tracker.py:187-241`).
Empty route: `No Route` / `READY` / `Load route...` -> `• No bodies`, next `N/A`, progress `0/0 (0%)`.

### States

| State | What the user sees |
|---|---|
| Not running | No window. |
| Starting | First paint with initial texts (`📍 Loading...`, `READY`, `➡️ ...`, `0/0`, `🧭 0.00/0.00`, `🚀 Fuel: ---%`); first refresh after 100 ms, then every 1000 ms. |
| No route | `No Route` / `N/A` / `0/0 (0%)` / `• No bodies` (Route Tracking); `No neutron route` / `Calculate first` style texts (`Calculate route first`, `Calculate neutron route`), `No galaxy route`, `No route` for the other tabs. |
| Populated | As layout. |
| Data error | Data callback exception: previous values stay (no error text). |
| Fuel unknown | `🚀 Fuel: ---%` grey (no capacity yet). On foot / landed / before first reading: `🚀 On Foot`, bar empty. |
| Hidden | `toggle_visibility` hides (`withdraw`) without stopping; only the close button uses it, and it immediately stops the overlay as well. |
| Buttons busy | After a tab button press RT/NH/GP are disabled for 600 ms (CR is not disabled but re-enabled with the rest). |
| Invalid cached tab | `<`, `>` do nothing and log a warning to the log file (not the Console); the copy button does nothing silently (see problems). |

### Automatic behaviour

- Redraw every 1000 ms (`update_loop`); every redraw re-fits the window height to content and
  rebuilds the scan and exobiology labels.
- Commands from the main app arrive through a queue (max 50): opacity, size, toggle visibility,
  update, prev, next, copy, switch tab, stop. Overlay buttons themselves post `prev`, `next`,
  `copy_current` into the same queue; the main thread executes them and the overlay redraws 200 ms
  after prev/next. Prev/Next are rate limited (300 ms, non-blocking lock).
- Tab sync: when the user changes the main window's tab (user click), `_cached_tab_name` and the
  overlay's tab buttons update (`app.py:627-648`, `_update_overlay_tab_state` ignores calls within
  500 ms of the previous one).
- On every new system the main app clears the overlay's species and bodies immediately.
- Exobiology changes: `ExobioManager.update_bio_summary` stores the capped species list on the app
  and tries to refresh the overlay (see problems; the 1 s loop picks it up anyway).
- Fuel tracker thread reads `Status.json` once per second (section 8).
- Overlay start on route ready (OVS-08); restart on theme refresh when running (`app.py:424-428`);
  stop on application exit (`app.py:1775`).

### User flows

1. **Open the overlay.** Settings -> `▶ Start Overlay` (or Ctrl+O in the main window, or by
   auto-start when a route becomes ready).
2. **Fly a route.** Jump in game -> overlay current/next/progress/fuel update within a second.
   Press the middle button to copy the next system into the clipboard -> paste in game.
3. **Advance by hand.** Press `>` to mark the next system visited without a jump (route tracking),
   `<` to copy the previous system.
4. **Switch route type.** `RT` / `NH` / `GP` / `CR` -> main window changes tab, overlay content
   switches source.
5. **Move.** Drag the header. **Change opacity/size.** Settings card (no controls in the overlay).
6. **Close.** `✕` in the header (stops the overlay).

### Observed functional problems

1. `_cached_tab_name` starts as `Route Optimization` and is only updated by a USER tab click
   (`CTkTabview.set()` does not call the command; checked in
   `.venv/Lib/site-packages/customtkinter/windows/widgets/ctk_tabview.py:109-115, 445-452`). After
   an optimisation/backup load the program calls `tabview.set("Route Tracking")`, the overlay shows
   Route Tracking data (else-branch) but `<`, `>` and the copy button are inert (they accept only the
   four tab names) until the user clicks a tab or an overlay tab button.
2. The copy button shows the current system but copies the next one (Route Tracking).
3. Overlay "next system" (item after the current one in list order) differs from Copy Next
   (first unvisited item).
4. `ExobioManager.update_bio_summary` refreshes the overlay through `self.app.overlay_manager`
   (`exobiology.py:314`) but `ExobioManager` has no `app` attribute; the exception is swallowed, so the
   immediate refresh never happens (the 1 s loop covers it).
5. Overlay widgets are touched from other threads: `update_display` is invoked from the main thread
   (`app.py:1569`) while the overlay runs its own `tk.Tk()` in a different thread; `set_size`
   and `update_tab_buttons` are also called from the main thread.
6. Saved size is not applied at overlay start; preset heights are overridden every second.
7. `_disable_tab_buttons` omits `CR`; `handle_close` hides and then stops the overlay (the hide path
   is redundant).
8. The Settings button text changes from `▶ Start Overlay` to `Start Overlay` after the first toggle.
9. Position is not persisted; opening always uses +100+100.
10. A `SKIPPED` colour exists but Route Tracking never reports it; `GALAXY`/`current`/`pending`
    statuses all render as `READY`.
11. `app.py:646-647`: `if self.overlay_enabled: self.overlay_manager.update_data()` sits outside
    `on_tab_change` (executes once during construction, not on tab change).

---

## 7. Settings cards for auto-save (belongs to Settings tab, listed here because it concerns tracking)

Created in `settings_manager.py:108-155`; the Settings tab is lazy-loaded on first visit.

| ID | Label | Type | Behaviour | Source |
|---|---|---|---|---|
| ASV-01 | `💾 Auto-save` | card heading | - | settings_manager.py:109 |
| ASV-02 | `Interval:` + `1 minute` / `5 minutes` / `10 minutes` / `Never` | option menu | Saves `autosave_interval` (default `5 minutes`), applies to the manager (`Never` = 0), Console `Auto-save interval: <value>`. Does not start the timer by itself. | app.py:1407-1415 |
| ASV-03 | `Status:` + `Stopped` / `Running` / `Running (<m>m <s>s)` | label (green when running) | Set on Start/Stop; the countdown text is computed only when `_update_autosave_status` runs (at start-up and Stop), not by a timer. | app.py:2273-2302 |
| ASV-04 | `▶ Start` / `Stop` | button | Starts/stops the timer; the label changes to `Stop` (red) / `Start` after the first state change. Start with interval `Never` -> Warning dialog `Auto-save` / `Already running` (wrong text for that case). Console `Auto-save started` / `Auto-save stopped`. | app.py:2091-2097, 2304-2310 |
| ASV-05 | `💾 Save` | button | Immediate save callback; Console `Manual save completed`, or Error dialog `Save Error` / `Manual save failed!` (only on exception). Does not actually persist anything (problem 3, section 1). | app.py:2099-2103 |

---

## 8. Fuel tracking

- **Purpose** Show remaining main-tank fuel from the game's `Status.json` and warn when low.
- **Where it shows**: (a) main window header, (b) overlay OVL-10/OVL-11, (c) Settings card
  `⛽ Fuel Tracker`. No fuel element exists on the Route Tracking tab itself.

| ID | Label | Type | What it does / shows | Source |
|---|---|---|---|---|
| FUE-01 | `Fuel:` + value (initial `---%`) | two labels in the global header row next to `CMDR:`, `CR:`, `Location:` | Value text `<pct>% (<cur>/<cap>t)` (e.g. `68% (27.2/40.0t)`), `---%` when capacity unknown, `On Foot` (grey) when on foot. The colour is read from a `color` key that the tracker does not provide, so it is always `#4CAF50` (green) except grey for `On Foot`. | app.py:243-250, 263-281 |
| FUE-02 | overlay fuel line and bar | see OVL-10, OVL-11 | Colour from `get_fuel_color`: `>50 %` `#4CAF50`, `>25 %` `#FFC107`, `>warning level` `#FF9800`, else `#F44336`. | fuel_tracker.py:212-220; app.py:1342-1346 |
| FUE-03 | `⛽ Fuel Tracker` | card heading in Settings (row 2, column 2) | - | settings_manager.py:267 |
| FUE-04 | `Warning Level:` `%` + option menu `5` `10` `15` `20` `25` `30` | option menu | Default 15. Saved and applied immediately (also shifts the orange/red colour boundary and sound trigger). | settings_manager.py:270-279, 329-334 |
| FUE-05 | `Critical Level:` `%` + option menu `1` `2` `3` `5` `10` | option menu | Default 5. Saved to settings.json only; the running tracker is not updated and no display or sound uses the critical level (colour rule has no critical step beyond `<= warning`). | settings_manager.py:283-292, 336-339 |
| FUE-06 | `Sound Alert:` | switch | Default ON; applied to the tracker immediately. | settings_manager.py:296-304, 341-347 |
| FUE-07 | `Volume:` + slider 0-100 (10 steps) + `<n>%` | slider + label | Default 100. Applied immediately; 0 mutes. Volume only scales the beep fallback; the shipped WAV is played at system volume. | settings_manager.py:306-319, 349-356; fuel_tracker.py:154-172 |
| FUE-08 | `Status:` + `---` | label | Created but never updated by any code (always `---`). | settings_manager.py:321-327 |
| FUE-09 | Fuel warning sound | audio | `sounds/fuel-low.wav` via `winsound` (async) when percentage <= warning level, with a 60 s cooldown; if the file is missing, a `winsound.Beep` whose pitch and length scale with the volume; nothing on non-Windows (import error is caught and logged). Not tied to any dialog. | fuel_tracker.py:145-172 |

Data and rules: `Status.json` is polled every 1 s in a daemon thread started 500 ms after launch.
Capacity: set from the journal `Loadout` event (`FuelCapacity.Main`) via the main app callback;
if unknown, the first non-zero `FuelMain` reading is taken as capacity (so a half-full tank at that
moment gives a wrong capacity until the next `Loadout`). `On Foot` is shown when `Fuel` is absent or
`FuelMain` is 0, when the Landed flag (bit 2) is set, or before any reading (current and percentage both 0).
The same `Status.json` stream is passed to registered listeners of the crew and ship extensions
(`FuelTracker.status_listeners`). No fuel history, no jump-fuel estimate and no per-route fuel
data exist in these files.

---

## 9. Exobiology (as visible in UI)

- **Purpose** Track the organic samples taken on foot (journal `Exobiology` / `ScanOrganic`) per
  species, three samples complete a species.
- **Where it shows**: overlay OVL-15 and the System Info tab's card `Exobiology Scans`
  (`system_info_section.py:366-371`; the card itself belongs to another reference file).

| ID | Label | Type | What it does / shows | Source |
|---|---|---|---|---|
| EXO-01 | `Exobiology Scans` + summary `-` | card heading + label in System Info | `-` when nothing known; otherwise lines `[COMPLETED] <system> / <body>: <genus>` for completed records of the system, then one row per species. | exobiology.py:199-336 |
| EXO-02 | species row | row of icon + name + circles + link | Icon `🟢` complete / `🟡` 2 of 3 / `🟠` 1 of 3 / `🔴` incomplete; with at most 5 species: bold name, three circles (blue = scanned, light green when complete, grey = open), tooltips `Complete`, `2/3 samples`, `1/3 samples`, `Incomplete`, `Sample <n> scanned` / `Sample <n> not scanned`, `Open wiki page`; with 6 or more species: `<species> [<n>/3]` and no circles or tooltips. | exobiology.py:345-462 |
| EXO-03 | `🔗` | label acting as link | Opens `https://elite-dangerous.fandom.com/wiki/<Species_Name>` in the browser. | exobiology.py:448-460 |
| EXO-04 | overlay species rows | see OVL-15 | `🟢/🟡/🟠/🔴 <species> (<n>/3)`, max 6 rows. | overlay.py:803-841 |

Data and rules: sample names come from the journal codex keys (`$Codex_Ent_Bacterial_Genus_Name;`)
translated through `codex_translation.py` (about 190 entries, e.g. `Bacterium`, `Clypeus`,
`Aleoida`); unknown keys are shown as the raw key and appended to `unknown_codex_keys.log` in the
working directory. Count per species is the number of samples reported, capped at 3. The sample list
resets when the body changes (inside a system) and on every new system. `ScanOrganic` with
`ScanType` `Analyse` marks a record `completed`. A species list is sorted: complete first, then
alphabetical. Records are also written to `visited_systems_history.json` (see section 4).
Realistic size: 1-8 species per system; display cap 6 in the overlay.

States: none yet -> `-` (System Info) / no heading (overlay). Updates run through a 100 ms polling
queue; duplicate states are skipped by an MD5 of the species list unless the widgets are gone.

Observed problems: (a) overlay immediate refresh broken (section 6, problem 4); (b) summary only
lists completed records although the code and the variable names say "incomplete"; (c) history file
shared with the visit history (section 4).

---

## 10. Cross-references (not in this file)

- Route Optimization tab (CSV, jump range, Optimize & Track button, Console) - another reference.
- Neutron Highway / Galaxy Plotter / Custom Route tabs: they feed the overlay but their own
  controls are described elsewhere.
- Processing dialog (`Processing…`, progress bar, `Cancel`, 360x170) is opened from the
  optimisation, not from Route Tracking (`gui.py:837-974`).
- `edmrn/journal_handler.py` and `edmrn/journal_operations.py` are partly duplicated by methods in
  `app.py` (`_handle_system_jump`, `_get_latest_cmdr_data`, `_start_journal_monitor`,
  `_detect_all_commanders`, `_apply_journal_settings`, `_switch_commander`); `JournalHandler`
  is constructed (`app.py:368`) but its `handle_system_jump` is not wired to the monitor; the live
  jump path is the one in `app.py`. `JournalOperations.handle_system_jump` is likewise unused by the
  monitor. `edmrn_sheet.py` (`EDMRNSheet`, a styled tksheet table) is used only in System Info and
  the Log viewer, not in Route Tracking.
