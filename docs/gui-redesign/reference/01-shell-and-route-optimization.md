# GUI functional reference 01 - window shell and Route Optimization

Scope: the main window shell (window, header, tab bar, footer, startup), the tab "Route Optimization"
with everything it opens (Processing dialog, Optional Columns dialog, Visited Systems dialog, backup
picker, message dialogs), the "Custom Route" tab (`custom_route.py`) and the shared autocomplete
entry. Purely functional; no visual judgement. All paths are relative to
`X:\Projects\EDProjects\EDMRN\` and all UI strings are quoted exactly as in code (emoji included).

Conventions: `Source` is `file:line` under `edmrn/` unless stated. "Tk thread" means the Tk main
loop thread. IDs are invented for this document only.

Reading note on dead code: `app.py` contains a second, older copy of the optimizer tab
(`_create_optimizer_tab`, line 856) and the neutron tab (`_create_neutron_tab`, line 971) that is
never called; the live optimizer tab is `ui_components.py:create_optimizer_tab` (line 52). The
header in `ui_components.py:create_header` (line 11) is also never called; the live header is
`app.py:_create_header` (line 197). Only the live code is documented here.

---

## 1. Main window shell (WIN)

- **Purpose**: one native OS window that hosts header, tab bar and footer; closing it ends the
  program.
- **Layout today**: native title bar (OS chrome) > `main_container` (1 px border, rounded) >
  `content_root` with three grid rows: row 0 header, row 1 tab view (expands), row 2 footer.

```
+--[ native title bar: icon + "ED Multi Route Navigation (EDMRN)" + min/max/close ]-+
| +-- header (fixed height 45) -------------------------------------------------+  |
| | [logo] CMDR: .. CR: .. Location: .. Fuel: ..                [Check Update]   |  |
| +------------------------------------------------------------------------------+  |
| +-- tab view ------------------------------------------------------------------+ |
| | [Route Optimization][Route Tracking][Neutron Highway][Galaxy Plotter]        | |
| | [Custom Route][System Info][Log][Settings]                                   | |
| |   ... content of the selected tab (expands with the window) ...              | |
| +------------------------------------------------------------------------------+ |
| donation text [Ko-fi][Patreon] ........ [GitHub][Discord][About][User Manual]    |
+------------------------------------------------------------------------------------+
```

### Elements

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| WIN-01 | `ED Multi Route Navigation (EDMRN)` | window title (native) | Title of the OS window | always | app.py:138 |
| WIN-02 | (app icon) | window icon | `assets/explorer_icon.ico` / `.png`, re-applied 100 ms after every `<Map>` and `<FocusIn>` event (OS shows the Python icon otherwise) | always | app.py:143-196, 509-569 |
| WIN-03 | native minimize / maximize / close | OS buttons | Standard OS behaviour. Close calls `_on_closing` (WIN-05) | always | app.py:196 |
| WIN-04 | `Ctrl+O` | keyboard shortcut | Toggles the overlay (same as the Start/Stop Overlay button in Settings; logs `Cross-Platform Overlay started!` or `Overlay stopped` to the console). Fails with an exception in the Tk callback if the Settings tab was never built (`overlay_btn` missing) | Settings tab built (it is preloaded about 3-5 s after start) | app.py:1765-1766, 1280-1294 |
| WIN-05 | (window close) | behaviour | `_cleanup`: stops overlay (if running), journal monitor, autosave manager; then `root.destroy()`. No confirmation, no "unsaved" check, window size/position is not saved | always | app.py:1769-1782 |

### Inputs and validation

- Initial geometry string `1196x730`; minimum size `1200x700` (app.py:139-140). The minimum width is
  larger than the initial width, so the window opens at 1200 px wide. No maximum. The window is a
  normal resizable OS window; content expands (header fixed height, tab view row weight 1).
- `AppConfig.window_geometry` exists (`'1100x800+100+100'`) but is only written when a theme change
  is confirmed (app.py:2379) and is never read to restore the window.
- A custom borderless title bar (min/max/close buttons, drag area, resize grips) is implemented in
  `app.py:670-830` and `app_window.py`, but `self._borderless = False` is hard-coded (app.py:339),
  so it is never shown. `AppConfig.borderless_mode` (default `True`) has no effect. The Settings tab
  has a switch that calls `_toggle_borderless_setting` (settings_manager.py:262).

### Data shown / States / Automatic behaviour

- Window title is static. No title change for state, route or location.
- States: only normal; no window-level empty/error state. Errors appear in dialogs (section 9).
- Automatic: root `<FocusIn>`/`<FocusOut>` call `_update_chrome_border_focus` which recolors the
  1 px `main_container` border (primary_hover when focused, border colour otherwise)
  (app.py:650-654, 1750-1757).

### Observed functional problems

- `_toggle_borderless_setting` (app.py:831-854): after the `try/except` there are 17 stray lines
  (`ctk.CTkLabel(cmdr_info_frame, ...)`, `update_btn = ...`) that reference undefined names; the
  method shows the InfoDialog `Restart Required` / `Please restart the app to apply borderless mode
  change.` and then raises `NameError`. Restart would not change anything anyway (`_borderless`
  hard-coded `False`).
- `AppWindow` (app_window.py) is a full duplicate of the borderless/icon code in `app.py` and is
  instantiated (`app.py:367`) but its methods are not called by the live code.
- Ctrl+O has no feedback when the overlay manager fails to start besides the exception.

---

## 2. Header bar (HDR)

- **Purpose**: always-visible commander status line (name, credits, location, fuel) plus the manual
  update check.
- **Layout today**: one row in a fixed-height (45 px, no propagate) bordered frame: logo at left,
  a label group that grows in column 1, the Check Update button at the right.

```
[logo 32x32] CMDR: <name>   CR: <credits>   Location: <system[ · station|body]>   Fuel: <value>   [🔄 Check Update]
```

### Elements

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| HDR-01 | (logo) | image label 32x32 | `assets/explorer_icon.png`; absent if file missing | file exists | app.py:211-221 |
| HDR-02 | `CMDR:` | label (static) | Caption | always | app.py:225 |
| HDR-03 | `[CMDR Name: Loading...]` (initial) | label bound to `cmdr_name` | Commander name from the latest journal's `Commander` event | always | app.py:228, 354, 1685-1735 |
| HDR-04 | `CR:` | label (static) | Caption | always | app.py:231 |
| HDR-05 | `N/A Cr` (initial) | label bound to `cmdr_cash` | Credits from the latest journal's last `LoadGame` event; formatted with `.` as thousands separator, e.g. `1.234.567.890 Cr` | always | app.py:234, 355, 1737-1742 |
| HDR-06 | `Location:` | label (static) | Caption | always | app.py:237 |
| HDR-07 | `[Location: Unknown]` (initial) | label bound to `cmdr_location` | Current location. Written by upstream (bare system) and rewritten by `edmrn/ext/location.py` whenever the tracker knows a system (format `System`, `System · Station`, or `System · Body` when the body is not a star and not the system name) | always | app.py:240, 356; ext/location.py:193-201, 228-240 |
| HDR-08 | `Fuel:` | label (static) | Caption | always | app.py:243 |
| HDR-09 | `---%` (initial) | label | Main-tank fuel from `Status.json`: `{pct:.0f}% ({current:.1f}/{capacity:.1f}t)` e.g. `78% (24.9/32.0t)`; `On Foot` (grey `#888888`) when not in a ship; `---%` when capacity unknown. Text colour comes from the fuel tracker (default `#4CAF50`) | fuel tracker started (about 0.5 s after start) | app.py:247, 263-281, 3028-3029 |
| HDR-10 | `🔄 Check Update` | button | Manual update check: calls `UpdateManager.manual_check()`; if no update manager exists yet (first ~0.5 s), opens `https://github.com/NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer/releases` in the browser and logs `GitHub releases Page` | always | app.py:252-261, 2330-2336; updater.py:234-252 |

### Inputs and validation

None (read-only labels; one button).

### Data shown

| Field | Unit / format | Example | Realistic maximum |
|---|---|---|---|
| Commander name | text | `Dreak` | about 30 chars |
| Credits | integer, `.` thousands separator, suffix ` Cr` | `1.234.567.890 Cr` | about 14 digits plus separators (trillions possible) |
| Location | `System` or `System · Station` or `System · Body` | `Eorgh Prou JH-V d2-5 · Ohm City` | system about 30 chars + station about 40 chars (about 75 chars); frame has fixed height and no wrapping, clipping behaviour UNVERIFIED |
| Fuel | `NN% (cur/cap t)` | `78% (24.9/32.0t)` | about 20 chars |

### States

- Loading (before journal read): name `[CMDR Name: Loading...]`, credits `N/A Cr`, location
  `[Location: Unknown]`, fuel `---%`.
- No journal found: name `CMDR Not Found (CMDR NoName)`, credits `Where is the bank? (Saved Data)`
  (app.py:1693-1694). Location and fuel stay at their initial text.
- Journal found but no `Commander`/`LoadGame`/location event in the latest file: name
  `CMDR NoName`, credits `0 Cr`, location `Unknown`.
- On foot: fuel `On Foot`.
- Update check results (dialogs, section 9): update found (UPD), `Could not check for updates: <reason>`,
  `✅ EDMRN is already up to date!` + versions.

### Automatic behaviour

- At start a daemon thread (`_get_latest_cmdr_data`) reads the whole latest journal file line by
  line and sets name, credits, location on the Tk thread; it also logs `Checking Journal file: <name>`
  and `CMDR Status Loaded: <name>, <credits>, Location: <system>` to the console, and (if
  `_auto_system_info_enabled`) fills the System Info entry with the location and fetches system data.
  The same thread is started again after the journal settings are applied (app.py:2328).
- Each new journal location event (jump, dock, undock, approach body, etc.) updates HDR-07 through
  the location tracker (`root.after`). A write trace on `cmdr_location` restores the tracker text
  whenever upstream writes a bare system name.
- Fuel label updates on every fuel-tracker callback (Status.json is polled about every 1 s by the
  fuel tracker).
- Also on a jump: `_handle_system_jump` sets the location variable and the System Info entry
  (app.py:1591-1600).

### User flows

1. Check for updates: click HDR-10 > the UI blocks while the request runs (timeout 10 s) > one of
   three dialogs appears (update available / error / up to date).

### Observed functional problems

- `manual_check` performs the HTTP request on the Tk thread (updater.py:238): the window freezes up
  to 10 s on a slow network.
- The update check queries the upstream author's repository
  (`NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer`), not this fork's own repository; the
  hard-coded fallback URL is the same.
- Credits come only from the last `LoadGame` in the latest file; they are not updated by market
  events, so they go stale during a session.
- Header values are written from several places (journal thread, cold start, jump handler); the
  only protection is the write trace for the location.

---

## 3. Tab bar (TAB)

- **Purpose**: switch between the eight functional areas. A `CTkTabview` segmented button row; the
  content area below it shows one tab at a time.
- **Layout today**: segmented button row at the top of the tab view, in this order. Tab view fills the
  window below the header.

### Elements

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| TAB-01 | `Route Optimization` | tab | Default selected tab at start; section 6 | always | app.py:614, 625 |
| TAB-02 | `Route Tracking` | tab | Progress of the loaded route (3D map, list, statistics). Content built when a route exists (`Optimized Route Not Found.\\nPlease create a new route in the 'Route Optimization' tab.` otherwise; the `\\n` is literal in the string) | always | app.py:615; route_management.py:339-371 |
| TAB-03 | `Neutron Highway` | tab | Neutron route planner (other reference file) | always | app.py:616 |
| TAB-04 | `Galaxy Plotter` | tab | Lazy built (other reference file) | always | app.py:617 |
| TAB-05 | `Custom Route` | tab | Manual multi-stop route builder; section 8 | always | app.py:618 |
| TAB-06 | `System Info` | tab | EDSM system data (other reference file); lazy built | always | app.py:619 |
| TAB-07 | `Log` | tab | Journal log viewer (other reference file) | always | app.py:620 |
| TAB-08 | `Settings` | tab | Settings (other reference file); lazy built | always | app.py:621 |

### Automatic behaviour

- On tab change (`on_tab_change`, app.py:627-645): caches the tab name for the overlay; builds
  `System Info`, `Galaxy Plotter`, `Settings`, `Custom Route` on first visit if not yet built; if
  `Neutron Highway` is selected and a neutron route exists and there is no journal monitor, starts
  one (logs `Journal monitor started for neutron tracking`); tells the overlay which tab is active.
- Preload: 1.5 s after the widgets exist, the four lazy tabs are built one by one, 700 ms apart, in
  the order System Info, Galaxy Plotter, Settings, Custom Route (app.py:665, 1135-1152); a second
  preload is scheduled by the background startup (app.py:3031-3036, guarded by `_preload_started`).
- A successful optimization switches to `Route Tracking` programmatically (section 6).

### Observed functional problems

- Lines 646-647 (`if self.overlay_enabled: self.overlay_manager.update_data()`) sit outside
  `on_tab_change` and run once while the tab view is built (overlay is never enabled then), so the
  overlay data is not refreshed on tab changes by that code.
- A failed lazy build is only logged (`Failed to lazy-load <tab>: <error>`); the tab stays empty
  with no message to the user.

---

## 4. Footer (FTR)

- **Purpose**: always-visible support and information links (outside the tab view, row 2 of the
  content root).
- **Layout today**: one row, left group then flexible spacer then right group.

```
Would you like to support? Donations help EDMRN development! [Ko-fi][Patreon] ........ [GitHub][Discord][About][User Manual]
```

### Elements

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| FTR-01 | `Would you like to support? Donations help EDMRN development!` | label | Static text (orange `#FFAE00`) | always | ui_components.py:849-853 |
| FTR-02 | `Ko-fi` | button | Opens `https://ko-fi.com/ninurtakalhu` in the default browser (`webbrowser.open`); on failure logs `ERROR: Failed to open link <url>: <error>` | always | ui_components.py:854-859; app.py:1744-1748 |
| FTR-03 | `Patreon` | button | Opens `https://www.patreon.com/c/NinurtaKalhu` | always | ui_components.py:860-865 |
| FTR-04 | `GitHub` | button | Opens `https://github.com/NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer` | always | ui_components.py:869-874 |
| FTR-05 | `Discord` | button | Opens `https://discord.gg/DWvCEXH7ae` | always | ui_components.py:875-880 |
| FTR-06 | `About` | button | Opens the About window (ABT) | always | ui_components.py:881-886; app.py:1759 |
| FTR-07 | `User Manual` | button | Opens the User Manual window (MAN) | always | ui_components.py:887-892; app.py:1762 |

### Observed functional problems

- All donation and GitHub/Discord targets are the upstream author's accounts (not this fork's).
- About and User Manual each create a new window on every click (no check for an already open
  one); both grab input (`grab_set`) so a second instance cannot be reached while one is open.

---

## 5. Startup sequence as the user sees it (STR)

- **Purpose**: documents what appears and loads between launching and an interactive window.

### Steps

1. `main.py` writes `debug_startup.log`, creates a hidden Tk root and shows the splash.
2. Splash `EDMRN - Loading` (500x400, centred, always on top, not resizable): dark gradient, 80
   horizontally drifting stars, logo `assets/edmrn_logo.png` 100x100 (fallback text `EDMRN`), caption
   `Elite Dangerous Multi Route Navigation`, tagline `Welcome home, Commander. EDMRN routing services
   are at your disposal`, a thin progress bar (loops, not tied to real progress), footer
   `v3.3.0 | CMDR Ninurta KALHU`. Closing the splash (X) destroys it (splash.py:7-122).
3. A background thread imports `edmrn.app` and constructs `EDMRN_App` (settings, theme, widgets,
   journal monitor, extension installs: neutron resume, location, crew, ship). Widgets are therefore
   built off the main thread. Timings are printed to the console as `[Startup] ...`.
4. The splash stays for at least 3.0 s; then it is destroyed, the root is shown (`deiconify`) and
   `mainloop` starts (main.py:56-63, 73-90).
5. +0.5 s: `_complete_startup_in_background` (app.py:3016-3040): starts the update check thread
   (5 s delay; shows the UPD dialog only if a newer version exists), creates a backup of the app
   data (`backup_manager.create_backup`, content UNVERIFIED), loads a saved `temp_route_status.json`
   if present and then builds the Route Tracking content, starts the fuel tracker.
6. +1.5 s: lazy tabs preloaded one every 700 ms (section 3).
7. In parallel: header values fill in (section 2), the location and ship trackers replay the newest
   journals in daemon threads, the Route Optimization range fields fill from ship data (section 6).

### States

- Console (Route Optimization tab) is the only place where startup progress is visible to the user:
  `Auto-save interval: 5 minutes`, `Checking Journal file: <name>`, `CMDR Status Loaded: ...`,
  `Auto-Detected: '<file>.csv' file selected.` or `Warning: CSV file not found. Please use
  'Browse' to select one.`.
- Error at construction: printed to the terminal as `[ERROR] Application could not be started: ...`
  and `sys.exit(1)` is called inside the worker thread.

### Observed functional problems

- `sys.exit(1)` in the worker thread (main.py:69) ends only that thread; the splash and the hidden
  root stay on screen with no message.
- The window is built in a non-main thread (Tk is not thread-safe); the code relies on it working.
- Splash progress bar is decorative; no loading text is shown (`set_status` is a no-op,
  splash.py:121).

---

## 6. Route Optimization tab (OPT)

- **Purpose**: take a CSV of systems (for example a Spansh body search export), a ship jump range and
  an optional starting system; reorder the systems into a short route (travelling-salesman
  optimization), save it as a backup and hand it to Route Tracking for auto-tracked travel.
  It is the start tab and the main working screen.
- **Layout today**: one bordered frame with a 3-column input band, then a status strip, then two
  buttons, then the console.

```
Route Data File (CSV):          Ship Jump Range (LY):        Starting System (Optional):
[ path entry ..................][ range entry     ]          [ Type or select system... ][▼]
[Browse / Reset][Load Backup]   Laden .. LY · now .. · ...   [🎯 Find Nearest]
[Spansh Route Search 🌐][Alien Life Wiki 👽]   [x] Auto
CSV Column Status:
+---------------------------------------------------------------------------------------+
| [System Name (Required) Found ✓][Body Name (Optional) ..][X Coordinate (Required) ..] |
| [Y Coordinate (Required) ..][Z Coordinate (Required) ..]   (single row of 5 cells)    |
+---------------------------------------------------------------------------------------+
[Optional Columns]
                         [ Optimize & Track ]
Console
+---------------------------------------------------------------------------------------+
| (scrolling status text, word-wrapped, expands to the window height)                   |
+---------------------------------------------------------------------------------------+
```

### Elements

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| OPT-01 | `Route Data File (CSV):` | label | Caption of OPT-02 | always | ui_components.py:64 |
| OPT-02 | (path; initial `<package>\edmrn\default.csv`) | entry, editable, bound to `csv_file_path` | Path of the CSV. Every write (typing, browse, backup) triggers the column check (OPT-16..21). No validation on typing | always | ui_components.py:70; app.py:350 |
| OPT-03 | `Browse / Reset` | button | Destroys all widgets of the Route Tracking tab, then opens the OS "open file" dialog (filter `CSV files (*.csv)` / `All files`); on a file sets OPT-02 and logs `File: <path>`. Cancelling the dialog leaves the Route Tracking tab empty (it is not rebuilt) | always | ui_components.py:85-92; file_operations.py:19-28 |
| OPT-04 | `Load Backup` | button | Opens the backup picker (BKP) listing folders in `Documents\EDMRN_Route_Data\backups`. If the folder does not exist: InfoDialog `Info` / `No backup folder found.`; if no subfolder has a CSV: `Info` / `No backup routes found.\n\nBackup routes will be created automatically when you optimize a new route.` | always | ui_components.py:93-100; file_operations.py:141-167 |
| OPT-05 | `Spansh Route Search 🌐` | button | Opens `https://www.spansh.co.uk/bodies` in the browser | always | ui_components.py:102-112 |
| OPT-06 | `Alien Life Wiki 👽` | button | Opens `https://elite-dangerous.fandom.com/wiki/Category:Alien_life` | always | ui_components.py:114-123 |
| OPT-07 | `Ship Jump Range (LY):` | label | Caption of OPT-08 | always | ui_components.py:124 |
| OPT-08 | (value, default `70.0`) | entry, editable, bound to `jump_range` | Jump range in light-years. Every write: if the text parses as a number > 0 it is saved to `settings.json` (`ship_jump_range`), and copied to the Neutron tab's range field. Auto mode (OPT-10) overwrites it with ship data | always | ui_components.py:130-133; app.py:2338-2351, 2534-2536 |
| OPT-09 | `Waiting for ship data from the journal` (initial) | label (fork addition) | Ship range summary, see "Data shown". Under OPT-08 | always (text changes with ship data) | ext/ship_panel.py:19, 31-45, 165-185 |
| OPT-10 | `Auto` | checkbox (fork addition) | On (default): OPT-08 follows the laden jump range from the ship tracker. Typing in OPT-08 or in the Neutron range field turns it off. Re-checking it writes the current ship value into OPT-08 again. Same switch is also shown on the Neutron tab (shared state) | always | ext/ship_panel.py:57-139, 165-185 |
| OPT-11 | `Starting System (Optional):` | label | Caption of OPT-12 | always | ui_components.py:139 |
| OPT-12 | placeholder `Type or select system...` | entry, bound to `starting_system` | Name of the system the route should start at. Plain entry, no autocomplete. Case-insensitive, trimmed match against the CSV system names | always | ui_components.py:151-157 |
| OPT-13 | `▼` | button | Toggles the system dropdown (DD) under OPT-12 | always | ui_components.py:159-166, 894-981 |
| OPT-14 | `🎯 Find Nearest` | button | Sets OPT-12 to the CSV system nearest to the commander's current journal position and shows `Nearest System Found` (see flows). Also runs silently (`auto=True`) after each valid CSV load | always | ui_components.py:175-184; app.py:2561-2628 |
| OPT-15 | `CSV Column Status:` | label | Caption of the status strip | always | ui_components.py:185 |
| OPT-16 | `Select a CSV file to check columns` (very first state) / `Select a CSV file to check column status` (after any path change to a missing file) | panel (80 px high, no propagate) | Shows the five column indicators or a message, see States | always | ui_components.py:187-198; app.py:1784-1849 |
| OPT-17 | `System Name (Required)` + `Found`/`MISSING` + `✓`/`✗` | indicator | CSV has a column named `System Name` | CSV readable | app.py:1816, 1822-1837 |
| OPT-18 | `Body Name (Optional)` + `Found`/`MISSING` + `✓`/`✗` | indicator | CSV has `Body Name`, `Name`, `BodyName` or `body_name`. Shown in red `MISSING` although optional | CSV readable | app.py:1817; optimizer.py:235 |
| OPT-19 | `X Coordinate (Required)` + status | indicator | Column `X` present | CSV readable | app.py:1818 |
| OPT-20 | `Y Coordinate (Required)` + status | indicator | Column `Y` present | CSV readable | app.py:1819 |
| OPT-21 | `Z Coordinate (Required)` + status | indicator | Column `Z` present | CSV readable | app.py:1820 |
| OPT-22 | `Optional Columns` | button | Opens the Optional Columns dialog (OCD); a second click while it is open closes it | always | ui_components.py:202-208; app.py:1881-2065 |
| OPT-23 | `Optimize & Track` / `Optimizing...` / `❌ Missing required columns` | button (primary) | Starts the optimization in a worker thread, see flows | enabled unless: optimization running (`Optimizing...`, disabled) or the last column check found a required column missing (`❌ Missing required columns`, disabled) | ui_components.py:209-215; route_management.py:188-301; app.py:1838-1843 |
| OPT-24 | `Console` | label | Caption of OPT-25 | always | ui_components.py:216 |
| OPT-25 | (text, starts empty) | multi-line text box, word wrap, read-only after the first message | Status/event console fed by `app._log` from the whole app (not only this tab). Appends one line per message, scrolls to the end, no limit, no clear/copy/save control. Mouse selection and scrolling work | always | ui_components.py:224-225; app.py:1262-1270 |

Not controls (placeholders): `jump_btn_frame` and `start_btn_frame` are fixed-height 30 px frames;
the second holds OPT-14, the first is empty (ui_components.py:136-138).

### Dropdown DD (opens under OPT-12 when OPT-13 is clicked)

A frame (height 300, 2 px border) placed over the window under the entry; closes when OPT-13 is
clicked again, when a system is chosen, or on a click outside it.

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| DD-01 | placeholder `Filter systems...` | entry | Case-insensitive substring filter over the loaded system names; updates on every keystroke | systems loaded | ui_components.py:943-949, 954-981 |
| DD-02 | (system name per row) | list of buttons, scrollable | Up to the first 100 matches, alphabetical. Click sets `starting_system` and closes | systems loaded | ui_components.py:961-970, 983-985 |
| DD-03 | `Load CSV to see systems...` | label | Shown instead of DD-01/02 when no system list is loaded | no CSV with all required columns loaded | ui_components.py:935-941 |
| DD-04 | `No matching systems` | label | Filter without matches | systems loaded, no match | ui_components.py:972-978 |

### Inputs and validation

- CSV path: no format check on typing; the column check (`pandas.read_csv(path, nrows=1)`) runs on
  every write. Required columns, exact names: `System Name`, `X`, `Y`, `Z`. Optional: body name
  column (`Body Name`, `Name`, `BodyName`, `body_name`). Header names are case-sensitive.
- Jump range: free text. Optimize requires a float > 0 (else dialogs below). Saved to settings
  whenever the text is a number > 0, including partial typing (`6`, `65`, `65.` ...), so every
  keystroke may write `settings.json`.
- Starting system: free text; matched case-insensitively against the grouped CSV system names. If it
  matches, that system becomes position 1 and is removed from the optimisation set; if it does not
  match the route silently starts at the optimizer's choice (only a log-file warning, nothing in the
  console or dialogs).
- Persisted between sessions (in `Documents\EDMRN_Route_Data\settings.json`): jump range only
  (`ship_jump_range`). The CSV path and starting system are not persisted; the CSV path resets to
  `<package>\edmrn\default.csv` (a file that does not exist in the repository) and then to the first
  `*.csv` found in the current working directory at start-up (`Auto-Detected: '<name>' file
  selected.`).
- Documents folder and backups: `Documents\EDMRN_Route_Data` (falls back to the user home or the
  current directory) and `...\backups` (config.py:25-69).

### Data shown

OPT-09 range summary text (ext/ship_panel.py:31-45), values floored to 0.1 LY:

| Situation | Text |
|---|---|
| No ship data | `Waiting for ship data from the journal` |
| Max range known, drive not in EDMRN's table | `Max 74.1 LY (drive not recognised, laden range unknown)` |
| Drive known | `Laden 58.7 LY · now 61.2 · max 74.1 · neutron ×4 234.8` (the `now` part is omitted while no fuel reading exists; neutron factor is `×4` or `×6` per drive) |

Other fields:

| Field | Unit / format | Example | Realistic maximum |
|---|---|---|---|
| CSV path | absolute path | `C:\Users\x\Documents\spansh_export.csv` | about 200 chars |
| CSV content | rows = bodies (or systems) | 300 rows, about 150 systems | 2,000 systems is the practical upper bound (see problems); the Spansh export may have dozens of columns |
| System name | text | `Eorgh Prou JH-V d2-5` | about 30 chars |
| Body name | text | `Eorgh Prou JH-V d2-5 A 3 a` | about 35 chars |
| Jump range | LY, one decimal | `58.7` | 0 to about 500 |
| Column indicator | `Found` green with `✓` / `MISSING` red with `✗` | | 5 cells |
| Console | lines | `Nearest system: Sol (12.34 LY from Alpha Centauri)` | unbounded, one line per event |

### States

Column panel (OPT-16), `_update_column_status_display`, runs on every CSV path write:

| State | What the user sees | Run button |
|---|---|---|
| Path empty or file missing | `Select a CSV file to check column status` | unchanged (can stay enabled) |
| File unreadable | `❌ Unable to read CSV file` (red) | unchanged |
| Unexpected exception | `Error checking CSV: <first 50 chars of error>` (red), plus console `Column status update error: <error>` | unchanged |
| Required columns all present | five cells, `Found`/`✓` green (Body Name may be `MISSING`) | `Optimize & Track`, enabled; the CSV is read fully for the start-system list and Find Nearest runs silently |
| A required column missing | cell shows `MISSING`/`✗` red | `❌ Missing required columns`, disabled |

Run button / optimization:

| State | Text / behaviour |
|---|---|
| Idle | `Optimize & Track` |
| Running | `Optimizing...` disabled; Processing dialog (PRC) open; a second click while flagged logs `Optimization already in progress` |
| Success | dialog `Success` (see flow), tab switches to Route Tracking |
| Cancelled during chunked distance matrix or just before TSP | ErrorDialog `Error` / `Optimization cancelled by user before TSP` (or `... during distance matrix calculation`), see problems |
| Failure returned by the optimizer | ErrorDialog `Error` / the error text; the run button returns to `Optimize & Track` |
| Validation | ErrorDialog `Error` / `Please select a valid CSV file.`; `Ship jump range must be a positive number.`; `Enter a valid number for ship jump range.` |
| ValueError inside the optimizer (see problems) | no dialog; console `Optimization failed: <first 100 chars>` and the button is reset |

Find Nearest (OPT-14) messages (WarningDialog titles in bold):

- **No CSV Data**: `Please load a CSV file first.\n\nThe CSV must contain System Name, X, Y, Z columns.`
- **Journal Not Ready**: `Journal monitoring not initialized.\nConfigure journal path in Settings first.`
- **Journal Path Not Set**: `Journal path not configured.\nConfigure journal path in Settings first.`
- **No Location**: `Could not detect current system.\n\nMake sure Elite Dangerous is running\nand you've jumped recently.`
- **No Coordinates**: `Current system '<name>' detected\nbut coordinates not available.\n\nTry jumping to another system first.`
- Success InfoDialog **Nearest System Found**: `Nearest system from <current>:\n\n<nearest>\n\nDistance: 12.34 LY`
- Error: ErrorDialog `Error` / `Could not find nearest system:\n<error>`
- In silent mode (after a CSV load) none of these dialogs appear; the result is only written to the
  starting-system entry and the console (`Nearest system: <name> (<d> LY from <current>)`).

### Automatic behaviour

- CSV path write: column check; if complete, reads the CSV with pandas to build the system list for
  DD and `csv_systems_data` (columns `System Name`, `X`, `Y`, `Z`, de-duplicated by system), then
  runs Find Nearest silently, which overwrites OPT-12 with the nearest system to the commander
  (even over text the user typed).
- Jump range write: saves to settings; mirrors to the Neutron range field; the ship binding writes
  OPT-08 on every ship-state change while Auto is on (journal `Loadout`, `Status.json` fuel/cargo, FSD
  jumps). When no ship tracker exists, an upstream `Loadout` handler writes `MaxJumpRange` instead
  (app.py:87-101).
- On start: `autodetect_csv` (file_operations.py:29-39) picks the first `*.csv` in the working
  directory if the default path does not exist.
- After a successful optimization the app restarts the journal monitor, copies the first unvisited
  system to the clipboard, starts the overlay if `auto_start_overlay_when_ready` is on (default
  `True`) and writes several console lines (see flow 1).

### User flows

1. Optimize a route
   1. Click `Browse / Reset` (OPT-03), pick a CSV; OPT-02 fills, column cells appear.
   2. If a required column shows `MISSING`, the run button is disabled; fix the file and re-pick.
   3. Check OPT-08 (follows the ship while `Auto` is on; type a number to take over).
   4. Optionally type a start system or pick one via `▼`, or click `🎯 Find Nearest`
      (it is also auto-set after step 1).
   5. Optionally `Optional Columns` (no functional effect, see problems).
   6. Click `Optimize & Track`. The Processing dialog (PRC) opens; stage texts and progress update.
   7. When finished, if any systems of the new route are in the visit history
      (`visited_systems_history.json`), the Visited Systems dialog (VIS) opens modally.
   8. The app logs to the console: `Route saved to backup: <folder name>`, `OPTIMIZATION COMPLETE`,
      `Total Distance: <d> LY`, `Estimated Jumps: <n> jumps`, `Route successfully saved to: '<path>'`,
      `Switched to Route Tracking tab (with 3D Map).`, `Auto-Tracking STARTED (Monitoring Elite
      Dangerous Journal).`, `Journal monitor started with path: <path or auto-detected>`,
      `'<system>' (Next System) copied to clipboard.`
   9. The tab switches to `Route Tracking`; InfoDialog **Success**: `Route optimization complete and
      Auto-Tracking is ready.\nFile: Optimized_Route_<n>_J<jumps>_M<range>LY.csv`.
2. Resume a saved route: `Load Backup` (OPT-04) > BKP dialog > select > `✅ Load Selected` > console
   `Loading backup... Please wait.` ... > SuccessDialog (800 ms later) `Backup loaded successfully!\n\nRoute:
   <folder>\nSystems: <n>\nVisited: <n>`; the Route Tracking tab is rebuilt, neutron and galaxy
   routes stored in the same folder are restored too.
3. Pick a start from the CSV: `▼` > type in `Filter systems...` > click a name (first 100 matches).
4. Search for targets outside the app: `Spansh Route Search 🌐` / `Alien Life Wiki 👽` open the
   browser; the user returns with a new CSV (flow 1).

### Output files produced (all under `Documents\EDMRN_Route_Data\backups\`)

| File | Created by | Content |
|---|---|---|
| `Route_<n>_sys_<YYYYMMDD_HHMMSS>\optimized_route.csv` | optimizer (optimizer.py:399-406) | One row per system in route order: original columns of the first row per system plus `Body_Names` (list text), `Body_Count`, `Status` |
| `Route_<n>_sys_<ts>\route_status.json` | optimizer, then updated by every status change | `[{"name": ..., "status": "unvisited"/"visited"/"skipped"}]` |
| `Route_<n>_sys_<ts>\current_route.csv` | after the Visited Systems filter (route_management.py:252-268) | Columns `System Name, X, Y, Z, Status, Bodies, Body_Count`; this is the file the backup loader prefers |
| `QuickSave_<v>of<t>_<ts>\...` | Route Tracking tab Quick Save (other tab) | same shape |
| `Optimized_Route_<n>_J<j>_M<r>LY.csv` | **never written**; only its name is logged and shown in the Success dialog | - |

Backup folder name example: `Route_142_sys_20261110_153012` (n = number of unique systems).

### Observed functional problems

- **Optional Columns has no effect.** The checkbox state lives in `app.column_vars`; nothing outside
  the dialog reads it (verified by search in `edmrn/`). The optimizer always uses
  `System Name`, `X`, `Y`, `Z` and the body-name column.
- **A file named `Optimized_Route_<n>_J<j>_M<r>LY.csv` is announced but never written**
  (console `Route successfully saved to: '<path>'`, Success dialog `File: ...`); the real files are
  in the backup subfolder listed above (route_management.py:237-238, 281, 284).
- **ValueErrors give no dialog.** `optimize_route` re-raises `ValueError` (`CSV missing required
  columns: ...`, `At least two unique waypoints are required for routing.`, `No systems left to
  optimize after removing starting system.`, `Jump range must be positive`); the worker's generic
  handler only logs `Optimization failed: <100 chars>` to the console and resets the button
  (route_management.py:293-300; optimizer.py:439-440). The `Optimization cancelled.` InfoDialog
  branch (line 292) is unreachable from the optimizer because it converts its own cancel
  `RuntimeError` into a failure dict, so a cancel shows the ErrorDialog instead.
- **Cancel does not stop the work.** The Cancel button closes the dialog at once and sets an event
  that is checked only in the chunked/simple distance-matrix loops (more than 10,000 systems) and
  once before the TSP; the scipy path (up to 2,000 systems), the vectorized path and the TSP
  process run on, and the result is still loaded, the tab switched and Success shown. The run button
  stays `Optimizing...` until then.
- **TSP time limit is silent.** The TSP solver runs in a separate process for at most 30 s; on
  timeout it falls back to a nearest-neighbour order with no message to the user (optimizer.py:189-221).
  Large inputs (hundreds to 2,000 systems) can reach this limit.
- **Unknown starting system is silently ignored** (log file only, optimizer.py:308).
- **Start system order is not enforced when none is given**: the first route entry is whatever the
  TSP permutation returns; UNVERIFIED whether it always begins at the first CSV system.
- **Find Nearest blocks the Tk thread**: `get_current_system()` and `get_current_coordinates()` each
  read the whole latest journal file synchronously (journal.py:314-354), so a large journal freezes
  the UI; they run again on every valid CSV path change.
- **Silent Find Nearest overwrites the user's starting system** on each valid CSV path write,
  including while a path is being typed character by character.
- **Column panel inconsistencies**: `Body Name (Optional)` is shown red/`MISSING`; the first-run
  text differs from the later text (`columns` vs `column status`); a missing file does not re-enable
  or disable the run button.
- **Dropdown close**: `_close_dropdown` calls `root.unbind("<Button-1>")` which removes every
  `<Button-1>` binding on the root made with `bind`; closing through the `▼` button destroys the
  frame without removing the outside-click binding (ui_components.py:894-991).
- Dialog windows `SuccessDialog`/`InfoDialog` etc. are not blocking calls; code after them keeps
  running while they are open.
- Duplicated or dead code: `JournalHandler.find_nearest_system` / `show_nearest_results`
  (journal_handler.py:346-452; expect a dict-based `csv_systems_data` that the app no longer builds),
  `app._use_current_location` (no caller), `app._run_optimization` (dead copy).
- Route Tracking receives one list label per system; very large routes create thousands of widgets
  (not measured).

---

## 7. Custom Route tab (CR)

(`edmrn/custom_route.py`; tab `Custom Route`; built on first visit or by preload; shown here because
it is the manual route builder.)

- **Purpose**: build a route by hand from system names (with autocomplete and coordinate lookup) or
  coordinates, optionally fix a start and an end, order it by a nearest-neighbour pass, and walk
  through it with previous/next and clipboard copy. Shows a 3D mini-map and jump statistics.
- **Layout today**: top row of inputs; below it two columns (left list and controls, right map and
  navigation).

```
Starting System: [auto-detected    ][Set] Ending System: [Optional destination][Set] Add System: [Enter system name (e.g., Sol, Achenar)][Add][Add by Coords]
+- System List ---------------------------+ +- 3D map (matplotlib) -------------------------+
| 1. Sol | 0.0 ..                         | |                                               |
| 2. Alpha Centauri | 4.4 LY | 1 jumps    | +-----------------------------------------------+
| (scrollable; click rows to select)      | Total: 0 LY | 0 jumps | 0 systems
+-----------------------------------------+ Ready to plan
[Optimize Route] (o) Shortest Distance (o) Neutron Path Boost: [x4 v]
[Remove][Clear All][Export][Import]       [< Previous] 1/5 - Sol [Next >] [Copy System]
```

### Elements

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| CR-01 | `Starting System:` | label | Caption | always | custom_route.py:403 |
| CR-02 | placeholder `Current location (auto-detected)` | autocomplete entry (AC), width 200 | Start system. Filled automatically with the commander's current system when the tab is built | always | custom_route.py:409-418, 635-641 |
| CR-03 | `Set` (first) | button | Stores the start (`manager.starting_system`); if that name is not in the list, adds it asynchronously (coordinate lookup); logs `Starting system set to: <name>`. Empty entry: nothing happens | always | custom_route.py:420-425, 643-649 |
| CR-04 | `Ending System:` | label | Caption | always | custom_route.py:427 |
| CR-05 | placeholder `Optional destination` | autocomplete entry (AC), width 200 | End system | always | custom_route.py:433-442 |
| CR-06 | `Set` (second) | button | Stores the end; adds the system to the list if missing; logs `Ending system set to: <name>` | always | custom_route.py:444-449, 651-657 |
| CR-07 | `Add System:` | label | Caption | always | custom_route.py:451 |
| CR-08 | placeholder `Enter system name (e.g., Sol, Achenar)` | autocomplete entry (AC), width 300 | System name to add | always | custom_route.py:457-466 |
| CR-09 | `Add` | button | Clears CR-08 and adds the system asynchronously (coordinates from the journal if it is the current system, else Spansh then EDSM). Duplicates (case-insensitive) are refused | always | custom_route.py:468-473, 665-683 |
| CR-10 | `Add by Coords` | button | Opens the Add System by Coordinates dialog (CR-D) | always | custom_route.py:475-480, 685-729 |
| CR-11 | `System List` | label | Heading of the list | always | custom_route.py:496 |
| CR-12 | `No systems added yet` (empty) / one row per system | scrollable list of clickable text rows | Row text, see "Data shown". Click toggles selection (multi-select); the current waypoint row is highlighted (orange `#FF8C00`, dark text), selected rows grey with gold text | always | custom_route.py:502-508, 886-963 |
| CR-13 | `Optimize Route` / `Optimizing...` | button (primary) | Runs the optimization in a thread; button disabled with `Optimizing...` meanwhile | list has at least 2 systems (else WarningDialog) | custom_route.py:513-518, 747-768 |
| CR-14 | `Shortest Distance` | radio (`distance`, default) | Plain ordering | always | custom_route.py:520-525 |
| CR-15 | `Neutron Path` | radio (`neutron`) | Same ordering, plus per-segment normal vs neutron jump counts using the jump range and the Boost multiplier | always | custom_route.py:527-531 |
| CR-16 | `Boost:` | label | Caption of CR-17 | always | custom_route.py:533 |
| CR-17 | `x4` / `x6` | dropdown, default `x4` | Neutron multiplier (4 or 6) used by CR-15 | always (only used in neutron mode) | custom_route.py:539-543 |
| CR-18 | `Remove` | button | Removes the selected rows; with nothing selected removes the last system of the list | always | custom_route.py:548-553, 731-740 |
| CR-19 | `Clear All` | button | Clears systems, optimized route and navigation index | always | custom_route.py:555-560, 742-745 |
| CR-20 | `Export` | button | Save dialog `Export Route` (`.json` default, `.txt` or any); writes the current route; then InfoDialog `Export Successful` / `Route exported to:\n<path>` | always | custom_route.py:562-565, 792-800 |
| CR-21 | `Import` | button | Open dialog `Import Route` (`.json`/`.txt`/all); replaces the list. Success: InfoDialog `Import Successful` / `Imported <n> systems.`; failure: ErrorDialog `Import Failed` / `Failed to import route file.` | always | custom_route.py:567-570, 802-813 |
| CR-22 | (3D scatter map) | embedded matplotlib canvas, 500x350 requested | Points (blue `#4FC3F7`, current waypoint orange), connecting line, system name labels, axes `X`/`Y`/`Z`; shown only if matplotlib is available | route has systems | custom_route.py:577-583, 980-1026 |
| CR-23 | `Total: 0 LY \| 0 jumps \| 0 systems` (initial) | label | Statistics, see "Data shown" | always | custom_route.py:588-594, 855-873 |
| CR-24 | `Ready to plan` (initial) / `Progress: <i>/<n> \| <n-i> remaining` | label | Navigation progress | always | custom_route.py:596-602, 1037-1042 |
| CR-25 | `< Previous` | button | Moves the current waypoint back one | disabled at index 0 | custom_route.py:607-612, 815-822, 1044-1046 |
| CR-26 | `No route` / `<i>/<n> - <system>` | label | Current waypoint | always | custom_route.py:614-619, 1032-1036 |
| CR-27 | `Next >` | button | Moves the current waypoint forward one | disabled at the last index | custom_route.py:621-626, 824-831, 1047-1049 |
| CR-28 | `Copy System` | button | Copies the current waypoint name to the clipboard (`pyperclip`, else Tk clipboard); logs `Copied system: <name>` | route has a current waypoint | custom_route.py:628-633, 833-853 |

Add by Coordinates dialog (CR-D; plain Tk window `Add System by Coordinates`, 350x200, modal):

| ID | Label | Type | What it does | Source |
|---|---|---|---|---|
| CR-D1 | `System Name:` | entry | Name | custom_route.py:697-699 |
| CR-D2 | `X:` / `Y:` / `Z:` | three entries | Coordinates in light-years, parsed as float | custom_route.py:701-711 |
| CR-D3 | `Add` | button | Adds the system without any lookup and closes; non-numeric input: WarningDialog `Invalid Input` / `Please enter valid numeric coordinates.`; empty name: nothing happens (dialog stays) | custom_route.py:715-729 |

Autocomplete (AC, used by CR-02, CR-05, CR-08, and the Neutron/Galaxy tabs): see section 9.

### Inputs and validation

- Names: free text, trimmed; duplicates refused case-insensitively (`add_system` returns false and the
  user gets the lookup warning text below, not a "duplicate" message).
- Coordinates: floats, no range check. System coordinates come from: journal (when the name is the
  current system), then Spansh system search, then EDSM (`ext/api.py:lookup_system`).
- Optimize needs at least 2 systems in the list; a set start and/or end that is not in the list is
  looked up and added to the route (it does not count towards the 2).
- Import `.txt`: lines `Name X Y Z` separated by whitespace (a name with spaces is not supported:
  only the first token is the name), `#` comment lines ignored. Import `.json`: object with
  `systems` array of `{name, x, y, z}`. Persisted: nothing between sessions (the route lives in
  memory only; export is manual).
- The jump range used for statistics is OPT-08's value (`app.jump_range`).

### Data shown

List rows (`custom_route.py:913-932`), example realistic cases:

- Unordered or not optimized: `1. Sol`
- Optimized: `2. Alpha Centauri | 4.4 LY | 1 jumps` (distance from the previous system, jumps =
  ceil(distance / range))
- Neutron mode with a saving: `3. Colonia | 22000.0 LY | ⚡ 94 jumps (was 377)`; without saving
  `<i>. <name> | <d> LY | <n> jumps`
- No jump range (empty field): `<i>. <name> | <d> LY`

Statistics label (CR-23):

- Neutron mode and range set: `Total: 22000.00 LY | 94 jumps (neutron) vs 377 jumps (normal) | 5 systems`
- Distance mode and range set: `Total: 120.50 LY | 3 jumps (70 LY range) | 5 systems`
- No range: `Total: 120.50 LY | 4 systems | 5 systems` (see problems)

Realistic sizes: a hand-built route has 2 to about 100 systems; names up to about 30 chars; row text
up to about 70 chars.

### States

- Empty: list `No systems added yet`, stats `Total: 0 LY | 0 jumps | 0 systems`, progress `Ready to plan`,
  navigation label `No route`, both nav buttons disabled, map empty.
- Lookup running: no indicator; the row appears when the lookup finishes.
- Lookup failure WarningDialog `System Lookup` with one of: `System '<name>' was not found at Spansh or
  EDSM. Check the spelling.`, `Could not look up '<name>': <Spansh reason>; <EDSM reason>.` (reasons
  such as `Spansh is overloaded (HTTP 502). Try again in a minute.`, `No connection to EDSM`,
  `Spansh did not answer in time.`), or the fallback `Could not find system: <name>\n\nCheck the spelling
  and try again.` (also shown for a duplicate).
- Not enough systems: WarningDialog `Not Enough Systems` / `Please add at least 2 systems to optimize
  the route.`
- Optimization failure: console `Route optimization failed`; if a start/end lookup failed, a
  WarningDialog `Route Optimization` with the lookup sentence (`Could not look up '<name>' ...`).
- Optimization success: console `Route optimized: <n> systems, <d> LY, <j> jumps`; overlay auto-start
  if enabled.
- Populated: the first row is highlighted as current even before optimization.

### Automatic behaviour

- On tab build a daemon thread reads the current system (journal monitor, then the location tracker,
  then the header text) and fills CR-02 and sets it as the start (it does not add it to the list).
- Overlay (`_get_custom_route_overlay_data`) shows current/next system, `<i>/<n> | <d> LY total`
  and `<d> LY | <j> jumps` while the Custom Route tab is the active tab.
- No journal-driven auto-advance: the current waypoint moves only by Previous/Next.

### User flows

1. Plan a trip: wait for CR-02 to fill (or type) > `Set` > type destination in CR-05 > `Set` > type
   systems in CR-08 (suggestions appear after 3 characters) > `Add` each > choose `Shortest Distance`
   or `Neutron Path` + Boost > `Optimize Route` > read the list/stats > `Next >` /
   `Copy System` as you travel.
2. Add an unknown/permit-locked system: `Add by Coords` > enter name, X, Y, Z > `Add`.
3. Share: `Export` (JSON or TXT) / `Import`.
4. Remove entries: click rows (they toggle) > `Remove`; or `Clear All`.

### Observed functional problems

- **Selection indices vs data**: row indices come from the displayed route (optimized order), but
  `Remove` calls `manager.remove_system(index)` on the unoptimized `systems` list; after an
  optimization the wrong systems can be removed. A removal also resets `is_optimized`, so the list
  reverts to the unoptimized order. `Remove` with no selection deletes the last system silently.
- `Neutron Path` summary uses a fixed factor 6 for the neutron total (`get_statistics`) while the
  per-row values use the Boost menu; the numbers can disagree for `x4`.
- The ordering is a nearest-neighbour pass (`solve_tsp`, custom_route.py:360-380), not an optimal
  tour; the user-facing wording elsewhere calls it TSP optimization.
- Start/end names given in CR-02/CR-05 are used even when they are not in the list; the route may
  have more systems than the list shows during Add.
- No-range statistics text prints `<n> systems | <m> systems` (custom_route.py:871).
- `Export` writes the currently displayed route; the start system is written only as a comment/field,
  and there is no export of the neutron statistics.
- `_add_system_async` mutates the system list from a worker thread (not on the Tk thread).
- The autocomplete callback `_on_suggestion_selected` is a no-op.

---

## 8. Shared autocomplete entry (AC)

Used by CR-02, CR-05, CR-08 (and Neutron/Galaxy tabs). `autocomplete_entry.py` + `system_autocomplete.py`.

- **Purpose**: text entry that suggests Elite Dangerous system names from Spansh (primary) and EDSM
  (fallback).

### Elements

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| AC-01 | (placeholder per use) | entry | Typing; 200 ms after the last key a lookup starts if the text has at least 3 characters (trimmed) | always | autocomplete_entry.py:10-40, 184-202 |
| AC-02 | (suggestion list) | popup list (borderless, always on top, entry-wide, 200 px high, 8 visible rows, scrollbar) | Up to 10 names; click or `Enter` on a highlighted row fills the entry, closes the list, calls the callback and focuses the entry. `Up`/`Down` move the highlight, `Enter` without highlight closes the list | at least one suggestion | autocomplete_entry.py:49-144, 244-299 |
| AC-03 | `System data by Spansh (primary) / EDSM (fallback)` | label (list footer, 7 pt) | Attribution | list open | autocomplete_entry.py:133-143 |

### Inputs and validation / data

- Minimum 3 characters; maximum 10 suggestions; query trimmed.
- Spansh `POST /api/systems/search` (name filter, size 50, one attempt, 5 s timeout); if no result, EDSM
  `GET /api-v1/systems?systemName=` (one attempt, 5 s). After a transient failure the failing service
  is skipped for 60 s. Results are ranked exact, then prefix, then contains. Cache per query text for
  1 h, in memory only.
- Local suggestion list (`local_suggestions`) exists in the widget but nothing fills it.

### States / automatic behaviour

- No suggestion, service error or cooldown: no popup and no error message (silent).
- The popup follows the window when it moves/resizes and closes 200 ms after focus leaves the entry.
- Results arrive on a worker thread and are handed to the widget through `root.after`.

### Observed functional problems

- No visible indication of a failed lookup, of a running lookup or of the cooldown.
- The theme colour lookup inside the popup uses `self.master.master.master.theme_manager`; if that
  chain fails, built-in fallback colours (`#2b2b2b`, `#ffffff`, `#0078d7`) are used (UNVERIFIED which
  occurs for each host widget).

---

## 9. Dialogs opened by this slice

All are separate top-level windows, centred on the main window, with the app icon.

### 9.1 Processing dialog (PRC) - `gui.py:837-975`

- **Purpose**: shows optimization progress and offers Cancel.
- Window `Processing…`, 360x170, fixed size, modal (`grab_set`), closing with the window X equals
  Cancel.

| ID | Label | Type | What it does / shows | Source |
|---|---|---|---|---|
| PRC-01 | `Preparing…` (initial) | label | Stage text. Stage names from the optimizer are shown with `_` replaced by a space and the first letter capitalized: `Distance matrix` (with fraction), `Distance matrix done`, `Tsp start` (indeterminate), `Tsp done` | gui.py:910-912, 941-956; route_management.py:196-203; optimizer.py:134, 315, 326, 335 |
| PRC-02 | (progress bar 280x14) | progress bar | Determinate 0..1 for matrix blocks, indeterminate while TSP runs. For up to 10,000 systems no per-block fraction is sent, so the user sees `Preparing…` then `Distance matrix done`, `Tsp start`, `Tsp done` | gui.py:914-917 |
| PRC-03 | `Cancel` | button | Sets the cancel event and closes the dialog (see problems of section 6) | gui.py:919-928, 933-939 |

### 9.2 Optional Columns Selection dialog (OCD) - `app.py:1881-2065`

- **Purpose**: let the user mark which extra CSV columns to "include in your workflow"; has no effect
  today (section 6 problems).
- Opened by OPT-22. Preconditions: path must exist, else WarningDialog `Warning` / `Please select a CSV
  file first.`; CSV unreadable: ErrorDialog `Error` / `Cannot read CSV: <error>`; no non-required
  columns: InfoDialog `Info` / `No optional columns found in CSV.` Window title `Optional Columns
  Selection`; size from 500x400, resized to width `60 + columns * col_width` and height between 600
  and 700; modal.

| ID | Label | Type | What it does / shows | Source |
|---|---|---|---|---|
| OCD-01 | `Optional Column Selection` / `Select which optional columns to include in your workflow.` | header text | Title and subtitle | app.py:1951-1956 |
| OCD-02 | (column name per checkbox, alphabetical, filled top-to-bottom 15 per column) | checkbox grid, scrollable | One checkbox per non-required CSV column (everything except `System Name`, `X`, `Y`, `Z`, so `Body Name` appears too); state kept in memory across openings | app.py:1958-1981 |
| OCD-03 | `Select All` | button | Checks every box | app.py:1992-1995, 2067 |
| OCD-04 | `Deselect All` | button | Unchecks every box | app.py:1996-2000, 2071 |
| OCD-05 | `Close` | button (red) | Closes the dialog | app.py:2001-2011 |

### 9.3 Visited Systems dialog (VIS) - `visit_history_dialog.py`

- **Purpose**: after optimization, let the user drop systems that the visit history already knows.
- Title `Previously Visited Systems`, 550x400, fixed, modal, Esc = Cancel. Shown only when at least one
  system of the new route is in `Documents\EDMRN_Route_Data\visited_systems_history.json`.

| ID | Label | Type | What it does / shows | Source |
|---|---|---|---|---|
| VIS-01 | `Found <n> Previously Visited Systems` | label | Count | visit_history_dialog.py:73-78 |
| VIS-02 | `These systems are already in your visit history. What would you like to do?` | label | Hint | visit_history_dialog.py:80-86 |
| VIS-03 | rows: checkbox (all checked by default) + `<name>` or `<name> (visited <n> times)` + `Last: <YYYY-MM-DD HH:MM:SS>` | scrollable list | Per visited system; checked = will be removed | visit_history_dialog.py:83-124 |
| VIS-04 | `Remove Selected` | button | Removes the checked systems from the route (console `INFO: Removed <n> previously visited systems from route` and `New route: <m> systems (was <n>)`) | visit_history_dialog.py:129-138, 173-179; route_management.py:323-331 |
| VIS-05 | `Keep All` | button | Keeps every system (console `INFO: Keeping all <n> previously visited systems in route`) | visit_history_dialog.py:140-149 |
| VIS-06 | `Cancel` | button | Same effect as Keep All (route unchanged, nothing logged) | visit_history_dialog.py:151-160; route_management.py:316-317 |

### 9.4 Backup selection window (BKP) - `gui.py:1407-1738`

- Title `Select Backup Route`, 800x550, resizable, modal. Opened by OPT-04 and by the Route Tracking tab's
  `Load Backup`.

| ID | Label | Type | What it does / shows | Source |
|---|---|---|---|---|
| BKP-01 | `Select Backup Route to Load` + `Select a backup folder to load both the route CSV and your progress` | labels | Heading and hint. Both are placed in the same grid cells as the column header row and the list (row 0 and row 1), so they overlap those widgets | gui.py:1471-1482, 1483-1498 |
| BKP-02 | table header: (radio), `Route Name`, `Systems`, `Progress`, `Modified`, `Status` | header row | Column captions | gui.py:1491-1496 |
| BKP-03 | one row per backup folder that contains a CSV, newest first | scrollable table with a radio per row | `Route Name` = folder name cut to 22 chars + `...` above 25; `Systems` = CSV line count minus 1 (or `?`); `Progress` = `<visited>/<total>` from `route_status.json` (`No data` without it, `Error` when unreadable) coloured orange below 50 %, yellow below 90 %, green from 90 %; `Modified` = `MM-DD`; `Status` icon `✅` (csv and status file), `📝` (csv only), `❌`. First row preselected | gui.py:1625-1706 |
| BKP-04 | `📁 Manual Select` | button | Folder chooser `Select Backup Folder`; a folder without a CSV gives WarningDialog `Warning` / `No CSV file found in:\n<path>\n\nPlease select a folder containing a route CSV file.`; otherwise loads it and closes | gui.py:1511-1524, 1707-1724 |
| BKP-05 | `✕ Cancel` | button | Closes | gui.py:1525-1536 |
| BKP-06 | `✅ Load Selected` | button | Loads the selected folder (`Please select a backup folder.` / `Folder no longer exists:\n<path>` on problems) and closes; loading runs in a thread (flow 2 of section 6) | gui.py:1537-1548, 1725-1734 |

Loader notes (file_operations.py:168-333): reads `current_route.csv`, else `optimized_route.csv`, else
the newest CSV; accepts system-name columns `System Name`/`system_name`/`System`/`Name` and coordinate
columns `X|x|Coord_X`, `Y|y|Coord_Y`, `Z|z|Coord_Z`; rows with unreadable coordinates are skipped with
a log line; error dialog `Error` / `Error loading backup:\n<error>`. Also restores a stored neutron
route and galaxy route if present.

### 9.5 Message dialogs (MSG) - `gui.py:977-1141, 1740-2143`

Same skeleton: large emoji icon, message (wrapped at 350 px, centred), buttons; non-blocking calls
(except Confirm which waits for the result); modal.

| Dialog | Icon | Buttons | Used here for |
|---|---|---|---|
| SuccessDialog | `✅` | `OK` | `Success` after loading a backup |
| InfoDialog | `ℹ️` | `OK` | `Success` (optimization), `Info`, `Nearest System Found`, `Update Check`, `Export Successful`, `Import Successful` |
| WarningDialog | `⚠️` | `OK` | `Warning`, `No CSV Data`, `Journal Not Ready`, `Journal Path Not Set`, `No Location`, `No Coordinates`, `System Lookup`, `Not Enough Systems`, `Invalid Input`, `Route Optimization` |
| ErrorDialog | `❌` | `OK` | `Error`, `Import Failed` |
| ConfirmDialog | `❓` | `Yes`, `No` | not used by this slice (neutron nearest and theme change) |

### 9.6 Update available dialog (UPD) - `updater.py:51-177`

Window `EDMRN - New Version Available`, modal, fixed size. Content: `🎉 New Version Available!`,
`Current: v<ver>`, `New: v<ver>`, `Download the new version from GitHub.`, buttons
`🐙 Download from GitHub` (opens the release page and closes) and `Close`. Shown automatically 5 s
after start when a newer version exists, and from HDR-10.

### 9.7 About window (ABT) - `gui.py:1143-1405`

Window `ED Multi Route Navigation (EDMRN)`, 500x600, fixed, modal. Content: logo 80x80, `Elite
Dangerous Multi-Route Navigation`, `EDMRN`, a text block (`Version 3.3.0 - AGPL-3 Licensed`,
`January 2026`, `Developed by CMDR Ninurta Kalhu`, Frontier copyright and non-affiliation notice, data
credits Spansh, EDSM, EdAstro, thanks), button `Show User Manual` (opens MAN), buttons `GitHub`,
`Discord`, `Donate` (`https://ko-fi.com/ninurtakalhu`), and `© 2025-2026 Ninurta Kalhu (S.C.) | All
Rights Reserved (Licence AGPL-3)`. The version is hard-coded text (matches `edmrn.__version__ =
"3.3.0"` today; two copies).

### 9.8 User Manual window (MAN) - `gui.py:35-835`

Window `EDMRN - User Manual`, 640x850 (minimum 600x800), modal. Left navigation of 16 buttons, right
a read-only monospace text box. Sections in order: `What's New`, `Quick Start`, `System Info`,
`Log Viewer`, `Route Opt`, `Custom Route`, `Neutron`, `Tracking`, `Fuel Tracker`, `Galaxy`,
`Settings & Overlay`, `Shortcuts`, `Backup`, `Troubleshooting`, `API Credits`, `Support`. The first
section is shown first; the active button is highlighted. Content is static English text in code
(`WHAT'S NEW IN v3.3.0`, ...). The manual does not describe any fork feature (UNVERIFIED for every
section; checked only the first ones).

---

## 10. Out-of-slice items touched by this slice

- `column_display_names.py`: `COLUMN_DISPLAY_NAMES` (about 200 journal field names to display names,
  e.g. `StarSystem` > `System`, `DistanceFromArrivalLS` > `Distance (ls)`, `JumpDist` >
  `Jump Distance`); it is imported only by `log_viewer.py` (Log tab). The Route Optimization tab
  does not use it.
- Neutron tab `Find Nearest System:` coordinate search (X/Y/Z entries + `FIND`) is a different
  "nearest" function (`app._find_nearest_system_by_coordinates`, app.py:2400) and belongs to the
  Neutron reference.
- Route Tracking, Settings (overlay start, borderless switch, auto-save, journal path), System Info and
  Galaxy Plotter are covered by other reference files.

## 11. Unverified

- Initial visual behaviour of the header when text is longer than the available width (clipping or
  overlap) and of the optimizer tab at 1200x700.
- Content of the startup backup (`backup_manager.create_backup`).
- Whether the TSP library always returns a permutation starting at index 0 (affects the first
  system when no start is given).
- Whether `AutocompleteEntry` popups pick theme colours or the fallback ones in each host.
- The complete text of every User Manual section; only the structure was read.
- Behaviour for `Spansh` CSV exports with the real column set (the optional list size and names).
