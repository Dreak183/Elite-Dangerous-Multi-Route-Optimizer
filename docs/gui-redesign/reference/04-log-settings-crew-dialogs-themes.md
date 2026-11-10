# GUI functional reference 04: Log, Settings, Crew dialog, themes, secondary windows, cross-cutting

Scope: tab "Log", tab "Settings", the Crew dialog, the theme system, the secondary windows
(Manual, About, Processing, Success/Info/Warning/Error/Confirm, update dialog, splash) and the
cross-cutting items (shortcuts, sounds, icons, fonts, window icon). Functional description only.
All paths are relative to `X:\Projects\EDProjects\EDMRN`. Line numbers refer to the working tree at
branch `main` (2026-11-10). "UNVERIFIED" marks anything not determined from code.

ID prefixes: `LOG-` Log tab, `SET-` Settings tab, `CRW-` Crew dialog, `THM-` theme system and theme
editor, `DLG-` secondary dialogs, `SPL-` splash, `UPD-` update flow, `XC-` cross-cutting.

Common facts used below:

- Main window title: `ED Multi Route Navigation (EDMRN)`. Tabs of the main tab view, in order:
  "Route Optimization", "Route Tracking", "Neutron Highway", "Galaxy Plotter", "Custom Route",
  "System Info", "Log", "Settings" (`edmrn/app.py:614-621`).
- App data folder: `~/Documents/EDMRN_Route_Data` (falls back to `~`, then to the working directory).
  Settings file: `settings.json` in it (`edmrn/config.py:25-41`). Log files:
  `<app data>/logs/edmrn_YYYYMMDD.log` (`edmrn/logger.py`).
- `self._log(text)` (used by many handlers below) appends a line to the output textbox of the
  "Route Optimization" tab and to the log file. It is the only in-app "message log"; it is not
  shown in the Log tab (`edmrn/app.py:1262-1270`).
- Every popup message below is shown through the dialogs of section "Secondary windows" (DLG-*).

---

## Log tab (tab "Log")

- **Purpose**: a table of journal events that carry a body name (scans, signals, codex, ...) for the
  current system, to review bodies already scanned. It is not an application log: the name "Log"
  refers to the Elite Dangerous journal ("Log Viewer" in the manual).
- **Where built**: `LogViewer` (`edmrn/log_viewer.py`) is owned by `SystemInfoSection`
  (`edmrn/system_info_section.py:236`), which is created when the "System Info" tab is first opened
  or preloaded; the Log tab frame (`app.tab_log`) is passed in and filled by `LogViewer.update_log`.
  Until the System Info tab has been built the Log tab is empty. UNVERIFIED: exact visual state of
  the empty Log tab (no code draws anything into it before the first `update_log`).

**Layout today**

```
+--------------------------------------------------------------------------+
| Time: [All v] [ ⋮ ] [ Search...                                        ] |  filter row
+--------------------------------------------------------------------------+
| - | System | Body | <chosen columns ...>                         | Note   |  header (drag to reorder)
|---+--------+------+-----------------------------------------------+-------|
| 🔍| Sol    | 3 a  | ...                                           |       |  one row per body,
| 🪐| Sol    | Earth| ...                                           |       |  newest first
+--------------------------------------------------------------------------+
```

The whole tab content is one frame with two grid rows: row 0 filter row, row 1 table
(`log_viewer.py:380-383`, `488-507`, `590-594`).

**Elements**

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| LOG-01 | `Time:` | label | Caption of LOG-02 | always | log_viewer.py:491 |
| LOG-02 | `All` (values `1h`, `6h`, `24h`, `All`) | dropdown (tk.OptionMenu) | Restricts rows to events whose timestamp is not older than 1/6/24 hours before now (UTC); `All` shows everything. Change rebuilds the table at once. Rows without a parseable timestamp are hidden for 1h/6h/24h. | always | log_viewer.py:493-499, 615-625, 632-634 |
| LOG-03 | `⋮` | button | Opens the column selector (LOG-20..) | always | log_viewer.py:500-505 |
| LOG-04 | placeholder `Search...` | entry (width 320, expands) | Case-insensitive substring filter over the currently shown columns of every row; applied 450 ms after the last keystroke (debounce). Focus returns to the entry after filtering. | always | log_viewer.py:506-540 |
| LOG-05 | (no label; column headings from `COLUMN_DISPLAY_NAMES`, first heading `-`) | table (tksheet `EDMRNSheet`) | Event table. First column is an event icon (heading `-`), then `System`, `Body`, then the chosen columns, last column `Note`. Cells are editable/copy/paste/undo enabled by the sheet's bindings, but edits are not persisted anywhere. | always | log_viewer.py:590-594; edmrn_sheet.py:35-57 |
| LOG-06 | column header drag | interaction | Reordering columns persists the new order to `settings.json` key `log_columns`; `System` and `Body` are forced back to the first two positions. | always | log_viewer.py:574-593 |
| LOG-07 | double-click on a row | interaction | Opens the "Log Detail" window (LOG-30) for that row. | row exists | log_viewer.py:727-806 |
| LOG-08 | right-click on a row | interaction | Selects the row, then pops up an empty `tk.Menu` (no entries). The sheet additionally has its own built-in right-click popup menu enabled (`right_click_popup_menu`); its entries are those of the tksheet library. | always | log_viewer.py:713-725; edmrn_sheet.py:50 |

Column selector window ("Select Columns", `tk.Toplevel`, 1000x600, resizable, opened by LOG-03,
`log_viewer.py:385-482`):

| ID | Label | Type | What it does | Source |
|---|---|---|---|---|
| LOG-20 | `Search:` + entry (width 16) | entry | Filters the checkbox list by case-insensitive substring of the raw key name. | :407-409, 436 |
| LOG-21 | `Normal` | button | Switches the list to the recommended keys only (`RECOMMENDED_KEYS`, 48 keys, `log_viewer.py:14-24`) that exist in the data. Default mode. The button does not change its appearance when active. | :410, 400-401 |
| LOG-22 | `Advanced` | button | Switches the list to every key found in the journal data (technical keys shown in grey italic). | :412, 459-461 |
| LOG-23 | `Select All` | button | Ticks every checkbox of the current list (respects mode, ignores search text). | :414 |
| LOG-24 | `Deselect All` | button | Unticks every checkbox of the current list. | :415 |
| LOG-25 | `Selected: N` | label | Number of ticked boxes in the current list. | :416, 404-406 |
| LOG-26 | `Apply` | button | Stores the ticked keys (in dict order of the data keys, not in click order) to `settings.json` `log_columns`, rebuilds the table, closes the window. | :419, 469-477 |
| LOG-27 | `Cancel` | button | Closes the window without change. | :420 |
| LOG-28 | checkbox grid, 6 per row, labels from `COLUMN_DISPLAY_NAMES` | checkboxes | One per key, under group headers `Basic Info`, `Surface Properties`, `Atmosphere`, `Biology`, `Orbit & Physics`, `Signals & Rings`, `Technical/Other` (empty groups are not shown). A key can appear under more than one header (the groups are matched by substring). | :432-468 |

Log Detail window (`tk.Toplevel`, title `Log Detail`, 380x260 minimum, 900x900 maximum, resizable;
`log_viewer.py:727-805`):

| ID | Label | Type | What it does | Source |
|---|---|---|---|---|
| LOG-30 | two columns of `<display name>` (bold) / `<value>` pairs | labels in scroll canvas | Every key of the underlying merged event row except technical keys (`TECHNICAL_KEYS`, 19 internal names), split into a left and right half; values are formatted by `format_value` (Yes/No for 0/1, multi-line lists for Signals, Materials, Genuses, Composition; numbers shortened). | :747-780 |
| LOG-31 | `Note:` + entry (width 40) + `Save` | label, entry, button | Sets a note for the row's body name, closes the window, rebuilds the table so the `Note` column shows it. The note is held only in memory (see problems). Shown whenever the `Note` column exists (always). | :791-805 |

**Inputs and validation**

- LOG-02: one of `1h`, `6h`, `24h`, `All`; default `All` on every rebuild; not persisted.
- LOG-04: free text, no length limit; default empty on every rebuild; not persisted.
- Column choice: persisted in `settings.json` key `log_columns` (list of journal key names, default
  `null`). On load, keys not present in the current data are dropped; if none remain the recommended
  keys present in the data are used (`log_viewer.py:367-374`).
- Hard limit: the table shows at most 2000 rows (after sorting, before the time filter)
  (`log_viewer.py:627-628`).
- Notes: free text, per body name, memory only.

**Data shown**

Source rows: journal events from the last journal files that carry a `BodyName`, grouped per body
and merged into one row per body (later events overwrite earlier keys). Bodies are skipped if
their name contains "belt", "cluster" or "ring", or if the part after the system name is a single
character or a number (`edmrn/journal_cache.py:212-229`). The journal folder is the auto-detected
one (`get_ed_journal_dir()`), not the manual path from Settings (see problems).

| Field (heading) | Unit / format | Realistic example | Realistic maximum |
|---|---|---|---|
| event icon (`-`) | one emoji from the `event` name: 🔍 Scan, 🌐 FSS, ✨ Discovery, 📡 Signal, 💧 Reservoir, 🪐 Body, 📝 other | 🔍 | 1 glyph |
| `System` | text | `Colonia` | about 40 chars |
| `Body` | system name stripped from body name | `Ab 2 a` | about 30 chars |
| `Distance (ls)` | `1,234.56 LS` | `2,451.00 LS` | about 12 chars |
| `Gravity (g)` | `0.98 g` | `0.98 g` | 8 chars |
| `Temperature (K)` | `288 K` | `288 K` | 9 chars |
| `Mass (Earths)` | `1.000 EM` | `0.350 EM` | 12 chars |
| `Radius (km)` | `6,371 km` | `6,371 km` | 14 chars |
| `Orbital Period (d)` / `Rotation Period (d)` | days, `12.50 d` | `12.50 d` | 14 chars |
| `Age (MY)` | `4,600 Myr` | `4,600 Myr` | 16 chars |
| `Stellar Mass` | `0.870 M☉` | `0.870 M☉` | 12 chars |
| `Abs. Magnitude` | `4.83 mag` | `4.83 mag` | 10 chars |
| `Eccentricity`, `Inclination (°)`, `Periapsis (°)`, `Axial Tilt (°)` | 3 decimals | `0.017` | 8 chars |
| `Semi-Major Axis (km)` | value converted to light-seconds, shown `1.23 LS` | `499.00 LS` | 14 chars |
| `Signals` | `3 Biological, 2 Geological` | `3 Biological` | list of up to about 8 types, a few hundred chars |
| `Composition` | `Ice: 50.0%, Rock: 30.0%` | | about 80 chars |
| `Landable` | `Yes` / `No` / `-` | `Yes` | 3 chars |
| `Ring`/`Rings` | `A Belt (Metallic), Mass: 1.2 Mt` | | several hundred chars |
| Any other chosen key | raw value formatted by `format_number`; `-` for empty | `Tidally locked` | free text |
| `Note` | free text from LOG-31 | `visit later` | about 40 chars typed |
| `Timestamp` | raw ISO string, only if chosen as column | `2026-11-10T18:22:01Z` | 20 chars |

Row ordering: newest timestamp first (`parse_ts`). Realistic row counts: tens per system, up to the
2000-row cap (a system with 60+ bodies and several scans each).

**States**

- Empty (no rows): table shows only the header; no message text exists. If the new parse returns no
  rows but a previous rebuild had rows, the previous rows are re-shown (`log_viewer.py:359-365`).
- Loading: parsing runs in a background thread; the old table stays until the new data arrives. If
  another update is requested meanwhile only the last one is kept. No loading indicator exists.
- Populated: as above.
- Error: errors inside parsing or formatting are swallowed; a failing `update_log` returns without
  changing the tab. No error text is shown.
- Disabled: nothing is ever disabled.
- Time filter yields nothing: empty table, no message.

**Automatic behaviour**

- Rebuild trigger: `update_log` runs when the System Info section is created, and on journal events
  `Scan`, `SAASignalsFound`, `CodexEntry`, and on system jumps (`edmrn/app.py:113-129`, `1625`,
  `edmrn/journal_handler.py:129`). Each rebuild destroys and recreates the whole tab content, so
  LOG-02 returns to `All`, LOG-04 is emptied, scroll position and open detail windows' notes are
  lost.
- Journal cache: `JournalCache` reads all journal files once in the background at start, then reads
  only the tail (last 100 lines) of the newest file on each rebuild (`journal_cache.py:90-152`).
  Before the cache is ready the 10 newest journal files are read synchronously inside the worker.
- Column widths are auto-sized to the content (min 60, max 400 px) after each fill
  (`edmrn_sheet.py:5-17`).

**User flows**

1. Review scanned bodies: open the Log tab; rows already present for the current system; set
   LOG-02 to `1h` to see only the last hour; type a word in LOG-04 to narrow.
2. Change columns: click `⋮`, optionally switch to `Advanced`, tick or untick boxes, `Apply`.
3. Reorder columns: drag a header; order is saved.
4. Inspect one body: double-click the row; read both columns; optionally type a note and `Save`.

**Observed functional problems**

- Notes are lost: `note_dict` is created inside `update_log` (`log_viewer.py:486`), so each rebuild
  (every Scan, SAA, Codex event, jump) empties all notes. They are never written to disk.
- The time filter hides rows in the table but the detail window looks up the row by table index
  in the unfiltered `table_rows` (`log_viewer.py:744`), so with the time filter active the detail
  of a different row can be shown. The 2000-row cap is applied before the time filter.
- Each event triggers a full destroy/recreate of the tab, resetting the user's search and time
  filter.
- The right-click handler pops up an empty menu (`tk.Menu` with no entries).
- "Normal"/"Advanced" buttons show no active state; "Select All" ignores the search text.
- The manual (`ManualWindow` section "Log Viewer") promises "quick copy actions", "sticky
  columns" and "Incremental loading"; none of these exist as controls in `log_viewer.py`.
- `JournalCache` uses `get_ed_journal_dir()`, ignoring `journal_path` from Settings, so a manual
  journal path does not affect the Log tab.
- `selected` colour for table rows is hard-coded `#333300`; table colours come from the theme but
  the column selector and detail windows use `EliteDangerousTheme.COLORS` (fixed orange palette),
  not the selected theme.

---

## Settings tab (tab "Settings")

- **Purpose**: configure overlay, auto-save, journal access and commander, theme, fuel warnings, and
  open the Crew dialog. Built lazily when first opened or by the preloader (about 1.5 s + 3 x 0.7 s
  after start) (`edmrn/app.py:636-637, 1118-1120, 1135-1152`). Rebuilt never; built once per run.
- **Window width note**: the tab content is a scrollable frame (height 550) holding a 3-column grid.

**Layout today**

```
+---------------------------------------------------------------+
|                     Application Settings                      |
| +-----------------------------------------------------------+ |
| | scrollable frame                                          | |
| | +-----------+  +------------+  +------------+             | |
| | | 📺 Overlay|  | 💾 Auto-save|  | 📝 Journal |             | |
| | +-----------+  +------------+  +------------+             | |
| | +-----------+  +------------+  +------------+             | |
| | | 🎨 Theme  |  | ⛽ Fuel    |  | Crew       |             | |
| | +-----------+  | Tracker    |  +------------+             | |
| |                +------------+                              | |
| +-----------------------------------------------------------+ |
+---------------------------------------------------------------+
```

Three equal-width columns (`uniform="settings_col"`); cards are grid cells (row 0: Overlay, Auto-save,
Journal; row 1: Theme, Fuel Tracker, Crew) (`settings_manager.py:8-51`). Card frames use theme colours
`frame` and `border`.

**Elements**

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| SET-00 | `Application Settings` | label (bold 16) | Heading | always | settings_manager.py:12 |
| SET-10 | `📺 Overlay` | label | Card title | always | :54 |
| SET-11 | `▶ Start Overlay` / `⏹ Stop Overlay` (text at build time), after first toggle `Start Overlay` / `Stop Overlay` | button | Starts or stops the in-game overlay window; logs `Cross-Platform Overlay started!` / `Overlay stopped`. Also triggered by Ctrl+O (XC-01). | always | settings_manager.py:57-65; app.py:1280-1294 |
| SET-12 | `Opacity:` + value label `80%` + slider | slider, 50 to 100, 10 steps (5-percent increments) | Sets overlay opacity; every movement saves `overlay_opacity` and, if the overlay runs, applies it live. | always | :70-81; app.py:2075-2082 |
| SET-13 | `Size:` + dropdown (`Small`, `Medium`, `Large`), width 95 | dropdown | Saves `overlay_size`, logs `Overlay size: <size>`, resizes a running overlay (Small 200x320, Medium 220x380, Large 260x440 px). | always | :85-98; app.py:2084-2089; overlay.py:646-650 |
| SET-14 | `Auto-start:` + switch (no text) | switch | `auto_start_overlay_when_ready`; when on, the overlay starts automatically when a route becomes ready. Logs `Auto-start overlay when route ready: ON/OFF`. | always | :99-107; app.py:1296-1323 |
| SET-20 | `💾 Auto-save` | label | Card title | always | :109 |
| SET-21 | `Interval:` + dropdown (`1 minute`, `5 minutes`, `10 minutes`, `Never`), width 110 | dropdown | Saves `autosave_interval` and sets the timer interval; `Never` sets interval 0. Restarts a running auto-save. Logs `Auto-save interval: <value>`. | always | :111-129; app.py:1407-1415, 2105 |
| SET-22 | `Status:` + `Stopped` / `Running` / `Running (Nm Ns)` | label | Auto-save state; green when running, theme `secondary` colour when stopped. The countdown text is only refreshed on start/stop events, not by a timer. | always | :130-139; app.py:2273-2302 |
| SET-23 | `▶ Start` (text), after use `Stop` / `Start` | button | Starts or stops the auto-save timer. Does nothing but warn if the interval is `Never` or already running. | always | :142-148; app.py:2091-2097, 2304-2310 |
| SET-24 | `💾 Save` | button | Saves route status now; logs `Manual save completed`. | always | :149-155; app.py:2099-2103 |
| SET-30 | `📝 Journal` | label | Card title | always | :157 |
| SET-31 | `Path:` + entry, placeholder `Auto-detected`, font 10 | entry bound to `journal_path_var` | Manual journal folder. Empty means auto-detect (`~/Saved Games/Frontier Developments/Elite Dangerous` on Windows). Typing alone saves nothing; `Apply` persists it. | always | :161-168 |
| SET-32 | `CMDR:` + dropdown, width 110 | dropdown bound to `selected_commander` | Choose commander. Values built at card build time: `Auto`, every commander name found in the last 200 journal files, and `Auto (Current: <name>)` inserted at position 2 when the current commander is not in the list. Selecting saves `selected_commander`, stops and restarts the journal monitor and shows an info dialog. | always | :170-185; app.py:2108-2122, 2153-2168 |
| SET-33 | `🔍 Test` | button | Checks the path (typed or auto-detected) and reports in a dialog. | always | :188-194; app.py:2135-2151 |
| SET-34 | `✓ Apply` | button | Saves `journal_path` (empty string when blank), restarts the journal monitor for the selected commander, re-reads commander data, shows an info dialog. | always | :195-201; app.py:2312-2328 |
| SET-35 | `🔄` | button | Re-detects commanders and refills SET-32 values; if the current selection is no longer valid it selects the first value. Logs `Refreshed commanders list: N found`. | always | :202-208; app.py:2263-2271 |
| SET-40 | `🎨 Theme` | label | Card title | always | :210 |
| SET-41 | `Select Theme:` + dropdown (`Elite Dangerous`, `Aisling Duval`, `Archon Delaine`, `Arissa Lavigny Duval`, `Denton Patreus`, `Edmund Mahon`, `Felicia Winters`, `Li Yong Rui`, `Pranav Antal`, `Zachary Hudson`, `Zemina Torval`) | dropdown | Selecting a theme saves `current_theme`, then asks to restart the app (see flows). | always | :216-252; app.py:2372-2396 |
| SET-42 | `Borderless Mode:` + switch | switch | Saves `borderless_mode` and shows "Restart Required" info dialog. Has no effect on the window (see problems). | always | :254-264; app.py:831-837 |
| SET-50 | `⛽ Fuel Tracker` | label | Card title | always | :267 |
| SET-51 | `Warning Level:` + dropdown (`5`,`10`,`15`,`20`,`25`,`30`) + `%` | dropdown, default 15 | Fuel percentage at or below which the fuel colour turns orange/red and the warning sound plays. Saved to `fuel_warning_level`, applied live. | always | :270-281, 329-334 |
| SET-52 | `Critical Level:` + dropdown (`1`,`2`,`3`,`5`,`10`) + `%` | dropdown, default 5 | Saved to `fuel_critical_level`. | always | :283-294, 336-339 |
| SET-53 | `Sound Alert:` + switch | switch, default on | `fuel_sound_enabled`; applied live. | always | :296-304, 341-347 |
| SET-54 | `Volume:` + slider (0 to 100, 10 steps) + value label `100%` | slider | `fuel_sound_volume`, applied live; label updates. | always | :306-319, 349-356 |
| SET-55 | `Status:` + `---` | label | Intended fuel status text. Never updated by code (see problems). | always | :321-327 |
| SET-60 | `Crew` | label (bold 13) | Card title | crew card built | ext/crew_dialog.py:43 |
| SET-61 | `Crew...` | button | Opens the Crew dialog (CRW-*). If it is already open it is raised and focused instead. | always | crew_dialog.py:44, 62-72 |
| SET-62 | `Crew is unavailable.` | label | Replaces the card when it could not be built; the error is logged once. | card build failed | crew_dialog.py:47-56 |

**Inputs and validation**

| Setting | Type, range | Default | Persisted in `settings.json` key | Applied |
|---|---|---|---|---|
| Overlay opacity | int 50..100, step 5 | 80 | `overlay_opacity` | live |
| Overlay size | `Small`/`Medium`/`Large` | `Medium` | `overlay_size` | live |
| Overlay auto-start | bool | true | `auto_start_overlay_when_ready` | live |
| Auto-save interval | `1 minute`/`5 minutes`/`10 minutes`/`Never` | `5 minutes` | `autosave_interval` | live; timer is not started automatically (see problems) |
| Journal path | free text folder path, no validation until `Test`/`Apply` | `` (auto) | `journal_path` | on `Apply` |
| Commander | `Auto` or a commander name | `Auto` | `selected_commander` | on selection |
| Theme | one of 11 names | `elite_dangerous` | `current_theme` | after restart |
| Borderless | bool | true | `borderless_mode` | never (see problems) |
| Fuel warning level | 5,10,15,20,25,30 | 15 | `fuel_warning_level` | live |
| Fuel critical level | 1,2,3,5,10 | 5 | `fuel_critical_level` | not applied live, no visible effect (see problems) |
| Fuel sound alert | bool | true | `fuel_sound_enabled` | live |
| Fuel sound volume | int 0..100, step 10 | 100 | `fuel_sound_volume` | live |

Other `settings.json` keys defined in `AppConfig` that are not edited on this tab: `appearance_mode`
(`Dark`), `color_theme` (`green`), `ship_jump_range` (`70.0`, written from the optimizer's jump-range
field), `window_geometry` (`1100x800+100+100`, written only on theme change, never read back),
`log_columns` (Log tab). The file is plain JSON (`indent=4`); load ignores unknown keys; a read error
silently yields defaults (`config.py:42-61`). Crew group data is not in `settings.json`; it is in
`crew_keys/group.json` (see Crew dialog). Every save rewrites the whole file.

**Data shown**

| Field | Format | Example | Maximum |
|---|---|---|---|
| Opacity value | `<int>%` | `80%` | 4 chars |
| Volume value | `<int>%` | `100%` | 4 chars |
| Auto-save status | `Running (4m 12s)` | `Stopped` | 20 chars |
| Journal path | folder path | `C:\Users\x\Saved Games\Frontier Developments\Elite Dangerous` | up to about 260 chars; entry scrolls |
| Commander list | names | `Auto`, `Auto (Current: Dreak)`, `Dreak`, `Alt` | a handful of entries, each up to about 30 chars |

**States**

- Loading: the tab is built on first open; building calls `_detect_all_commanders`, which scans up to
  200 journal files on the Tk thread, so the first open of Settings can freeze the UI briefly. No
  indicator exists.
- Empty commander list: dropdown shows only `Auto`.
- Dialog texts triggered here (title / message):
  - Test, no path at all: Error `Error` / `No journal path found`.
  - Test, path missing: Error `Error` / `Path does not exist:\n<path>`.
  - Test OK: Info `Success` / `Journal path is valid!\n\nFound <n> files\nLatest: <file name>`.
  - Test, no files: Warning `Warning` / `Path exists but no journal files found`.
  - Apply: Info `Success` / `Journal monitor restarted with new settings!`.
  - Commander switch: Info `Success` / `Journal monitor restarted for commander: <name>`.
  - Borderless: Info `Restart Required` / `Please restart the app to apply borderless mode change.`
  - Auto-save Start refused: Warning `Auto-save` / `Already running` (also shown when interval is
    `Never`).
  - Manual save failed: Error `Save Error` / `Manual save failed!`
  - Theme selection: Confirm `Theme Change` / `Theme '<file name>' selected!\n\nApplication will
    restart in 2 seconds to apply the new theme.\n\nContinue?` (`Yes` / `No`); if the theme file is
    missing: Warning `Theme Not Found` / `Theme file '<file name>.json' not found.`; on failure to
    switch: Error `Error` / `Failed to change theme: <error>`; failure to restart: Error `Error` /
    `Could not restart application: <error>`.

**Automatic behaviour**

- Slider and dropdown changes save to `settings.json` immediately (no Save button).
- Fuel settings update the running fuel tracker, except Critical Level.
- A restart after theme change reopens the program (frozen build: new process, then exit; source
  run: `os.execl`); `window_geometry` is saved before it but not restored.
- Settings card button colours are re-applied by `_refresh_settings_tab_buttons` only from
  `refresh_ui_theme`, which has no caller.

**User flows**

1. Change theme: open Settings, pick a theme in SET-41, confirm `Yes`, the app restarts after
   100 ms (the text says 2 seconds), new colours apply.
2. Set a manual journal folder: type path in SET-31, `Test`, read result, `Apply`.
3. Switch commander: `🔄` to refresh the list, choose a name in SET-32 (restarts monitor at once).
4. Configure overlay: choose size/opacity/auto-start, then `Start Overlay` (or Ctrl+O).
5. Auto-save: choose an interval, press `Start` (must be repeated each session), optionally `Save`.
6. Fuel alert: set warning level, switch sound on/off, move Volume.
7. Crew: `Crew...` opens the dialog (next section).

**Observed functional problems**

- `borderless_mode` is written but never read: `self._borderless` is hard-coded `False`
  (`app.py:339`), so SET-42 changes nothing, although the dialog says a restart applies it.
- There is no browse button for the journal path; `_browse_journal_path` (`app.py:2128`) exists but
  is not connected to any control.
- Auto-save is never started automatically (`autosave_manager.start()` is called only by SET-23);
  the interval setting alone does not start it.
- Auto-save Start/Stop button text is `▶ Start` at build, then `Start`/`Stop`;
  `_reapply_theme_to_widgets` and `_refresh_settings_tab_buttons` compare against `"▶ Start"`
  (`app.py:2197, 2217`) and mis-detect the state after the first toggle. The Overlay button also
  changes between `▶ Start Overlay` and `Start Overlay`.
- `Critical Level` (SET-52) is stored and exposed as `is_critical` in the fuel data but no consumer
  of that flag was found, and the dropdown does not update the running tracker; UNVERIFIED whether
  the overlay reads `is_critical` (grep found no use outside `fuel_tracker.py`).
- Fuel `Volume` slider has no effect on the shipped `.wav`: it is played with `winsound` without
  volume control; volume only changes the pitch/length of the fallback beep and `0` suppresses
  the sound (`fuel_tracker.py:154-170`).
- Fuel `Status:` label (SET-55) is never updated (grep: no writer of `fuel_status_label`).
- `Auto (Current: <name>)` is a dropdown value; selecting it stores that literal string in
  `selected_commander`, and the journal monitor compares that string with the commander name
  (`journal.py:93`), so jumps would be ignored. UNVERIFIED in a live run.
- `_switch_commander` and `_apply_journal_settings` create the monitor with callback
  `_handle_system_jump`, whereas start-up uses `_journal_callback` (`app.py:401` vs `2161, 2319`).
- Theme change writes `current_theme` to `settings.json` before the confirm; answering `No`
  leaves the dropdown on the new theme, the file changed, and no restart (the new theme applies at
  the next start).
- Text says "restart in 2 seconds" but the restart is scheduled after 100 ms.
- The theme dropdown's list is hard-coded; `_get_available_themes` (dynamic list) has no caller.
- Journal path entry is not trimmed on typing; `Apply` trims whitespace.

---

## Crew dialog (Settings > Crew...)

- **Purpose**: let the commander join or run a small group ("crew") that shares location and ship
  status through a relay server, with a signed event log. Create a group, join with an invite
  string, invite friends (leader), pause sharing, remove members, leave.
- **Window**: `CTkToplevel`, title `Crew`, size 560x640, non-modal, no icon set, not transient. One
  instance only (SET-61 raises an existing one). Source: `edmrn/ext/crew_dialog.py`.
- Controller: `CrewGroup` (`edmrn/ext/crew_group.py`); identity: `crew_identity.py`; roster rules:
  `crew_roster.py`; connection: `crew_sync.py`. Spec: `docs/superpowers/specs/2026-10-13-crew-sync-core-design.md`
  S20.

**Layout today**

```
+-- Crew ----------------------------------------------+
| (scrollable body)                                    |
| +-- You ---------------------------------------+     |
| | Commander: Dreak                              |     |
| | Your crew key (public; the relay lists ...):  |     |
| | [ AbC...43 chars ........................ ][Copy]    |
| +-----------------------------------------------+     |
| (orange problem text, if any)                         |
|  --- no group ---           |  --- in a group ---     |
| +-- Create a group ----+    | +-- Group: <name> ---+  |
| | [Group name]         |    | | Connection: ...    |  |
| | [wss://...relay]     |    | | [Reconnect]        |  |
| |      [Create group]  |    | | [ ] Pause sharing  |  |
| +-- Join with invite --+    | +-- Members ---------+  |
| | [ multi-line box ]   |    | | name (leader) status|  |
| |             [Join]   |    | | name  status [Remove]| |
|                              | +-- Invite (leader) --+ |
|                              | | [For whom] (1h|24h|7d)|
|                              | |   [Create invite]    |
|                              | | [code][Copy] [link][Copy link]
|                              | | Expires ...  [Post to Discord]
|                              | | +-- Discord webhook --+ |
|                              | |  [url (hidden)] [Save webhook]
|                              |            [Leave group]|
+------------------------------------------------------+
| (message line, wrapped, grey/default colour)          |
+------------------------------------------------------+
```

The dialog is a scrollable body plus one message label at the bottom (`crew_dialog.py:129-133`). Which
sections exist depends on three states: crew unavailable, outside a group, inside a group (leader or
member).

**Elements**

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| CRW-01 | `You` | section title | Identity section | crew available | :325 |
| CRW-02 | `Commander: <name>` (`not known yet` when no commander is known) | label | Active in-game commander name (plain, max 80 chars) | crew available | :326 |
| CRW-03 | `Your crew key (public; the relay lists leaders by it):` | label | Caption of CRW-04 | an author key exists (commander known and key file readable) | :328-330 |
| CRW-04 | read-only entry with the author key | entry (readonly) | The commander's public Ed25519 key, base64url, 43 characters; the operator of the relay lists leaders by it (spec S20). | as CRW-03 | :331, 248-257; crew_identity.py:42-61 |
| CRW-05 | `Copy` (width 70) | button | Copies the key to the clipboard | as CRW-03 | :255 |
| CRW-06 | (orange text, the problem sentence) | label | Shown when the controller has a problem (texts below) | `group.problem` set | :314-316 |
| CRW-07 | `Crew features are unavailable; the log file says why.` | label | Whole dialog body when crew is unavailable (cryptography missing, store failed, etc.) | `app.crew` is None | :309-312 |
| CRW-10 | `Create a group` | section title | Group creation (no group) | not in a group and no problem | :343 |
| CRW-11 | placeholder `Group name` | entry | Group name | no group | :344 |
| CRW-12 | prefilled `wss://galroute.the-hub.cloud` | entry | Relay address | no group | :346-347; crew_roster.py:28 |
| CRW-13 | `Create group` | button | Creates the group, you become leader; message `Group created.` | no group | :349-352; crew_group.py:255-266 |
| CRW-14 | `Join with invite` | section title | Join section | no group | :354 |
| CRW-15 | (no label) | multi-line textbox, height 70 | Paste an invite string, or the invite link, or either wrapped in Discord decoration (`<...>`, backticks) | no group | :355-356; crew_roster.py:227-233 |
| CRW-16 | `Join` | button | Joins; message `Joined.` | no group | :357-359; crew_group.py:268-281 |
| CRW-20 | `Group: <name>` (`Group: crew` when the name is unknown) | section title | Group section header | in a group | :387-388 |
| CRW-21 | `Connection: <state>` (+ ` (<reason>)` unless connected) | label | Sync state, see state table | in a group | :361-365, 389-391 |
| CRW-22 | `Reconnect` (width 100) | button | New connection attempt; message `Reconnecting...`. Appears only in the state `Refused by the relay`. | state `refused` | :374-378; crew_group.py:321-328 |
| CRW-23 | `Pause sharing` | switch | Stops sending your own status/events to the group; saved in `group.json` so it survives restart; messages `Sharing paused.` / `Sharing resumed.`. On error the switch reverts. | in a group | :392-395, 421-431 |
| CRW-30 | `Members` | section title | Roster | in a group | :397 |
| CRW-31 | `<commander>` or `<commander> (leader)`; first 12 characters of the key if the name is unknown | label per member | Active members only (leader first, then by name, removed members hidden) | in a group | :400-404; crew_roster.py:102-106 |
| CRW-32 | `<system> - <ship>` | label per member | Latest status of the friend: system name, then ship name (or ship type); empty when none yet. Updates in place when a status arrives. | in a group | :405-408, 192-198, 101-105 |
| CRW-33 | `Remove` (width 70); armed state `Click again to remove` | button per member | Two-click confirmation within 4 s; removes the member; message `Member removed.` | you are the leader and the member is not the leader | :409-415 |
| CRW-40 | `Invite` | section title | Invite creation | you are the leader | :435 |
| CRW-41 | placeholder `For whom (optional)` | entry | Label for the invite (1 to 64 printable ASCII characters), shown in the Discord message and roster | leader | :436 |
| CRW-42 | segmented `1 hour` / `24 hours` / `7 days` (default `24 hours`) | segmented button | Validity of the invite (1, 24, 168 hours) | leader | :438-440; crew_roster.py:26 |
| CRW-43 | `Create invite` | button | Signs and records an invite; message `Invite created; it works once.`; clears CRW-41 | leader | :447-450 |
| CRW-44 | read-only entry + `Copy` | entry, button | The invite string `edmrn-crew:1:<base64url>` | an invite was created for this group | :451-452 |
| CRW-45 | read-only entry + `Copy link` | entry, button | `https://<relay host>/join#<invite string>` | as CRW-44 | :453-454 |
| CRW-46 | `Expires <YYYY-MM-DD HH:MM:SSZ with T replaced by a space>` | label | UTC expiry | as CRW-44 | :455 |
| CRW-47 | `Post to Discord` | button | Posts the invite through the webhook in a background thread; message `Posting to Discord...`, then `Invite posted to Discord.` or the error sentence | as CRW-44 and a webhook is saved | :457-459, 473-483 |
| CRW-48 | `A webhook posts into a channel, not a DM: whoever reads the channel first can use the invite. Use a private channel and a short expiry.` | label | Warning next to CRW-47 | as CRW-47 | :27-28, 460 |
| CRW-49 | `Discord webhook (leader only, stays on this PC)` | section title | Webhook section | leader | :463 |
| CRW-50 | placeholder `https://discord.com/api/webhooks/...`, text hidden with `*` | entry | Webhook URL (secret) | leader | :464-468 |
| CRW-51 | `Save webhook` | button | Validates and saves (empty clears); message `Webhook saved.` | leader | :469-471 |
| CRW-60 | `Leave group`; armed state `Click again to leave` | button (grey) | Two-click confirmation within 4 s; deletes `group.json`, stops sync, withdraws friends' projected data; message `Left the group.` Also the only control when `group.json` is unusable. | in a group, or a group-file problem | :333-337, 339-342, 417-419 |
| CRW-70 | (message line, no label) | label, wraps at 520 px | Result of the last action or its error sentence | always (empty at start) | :123, 131-132 |

**Inputs and validation**

| Field | Rule | Error sentence (shown in CRW-70) |
|---|---|---|
| Group name | 1 to 64 printable ASCII characters (U+0020 to U+007E) after trimming | `A group name is 1 to 64 plain ASCII characters.` |
| Relay address | `wss://host[:port]` only; `ws://` accepted for `localhost`/`127.0.0.1`; no user info, query, fragment or path; max 200 chars | `The relay address must look like wss://galroute.the-hub.cloud.` |
| Invite text | must start with `edmrn-crew:1:` after removing decoration and everything up to the first `#`; at most 2000 chars; valid base64url JSON with keys `g,i,k,l,u` | `This is not an EDMRN crew invite.` / `The invite is damaged; ask the leader for a new one.` |
| Join with own invite | the invite's leader equals your key | `This is your own invite; send it to a friend.` |
| Invite label | optional, 1 to 64 printable ASCII | `A label is 1 to 64 plain ASCII characters.` |
| Expiry | 1, 24 or 168 hours | `An invite lasts 1 hour, 24 hours or 7 days.` |
| Webhook | `https://[ptb.|canary.]discord[app].com/api/webhooks/<digits>/<token>`; empty clears | `This is not a Discord webhook URL (https://discord.com/api/webhooks/...).` |
| Create/Join without commander | no commander known | `No commander is known yet; start Elite Dangerous or wait until EDMRN has read the journal.` |
| Create/Join when in a group | | `This EDMRN is already in a group; leave it first.` |
| Leader-only actions by non-leader | | `Only the group leader can do this; switch to the commander who created the group.` |
| Remove | member must be current and not the leader | `Only a current member other than the leader can be removed.` |
| Post without webhook | | `Set a Discord webhook first.` |
| Any action when not in a group | | `This EDMRN is not in a group.` |
| Storage failures | | `EDMRN could not <record this / save the group file / open the group / delete the group file / close the group / read the log>; the log file says why.` and `EDMRN could not record this; the log file says why.` |
| Discord post failure | | `The post to Discord failed; the log file says why.` or the service sentence (e.g. `Discord is overloaded. Try again in a minute.`) |

Persistence: group membership and options in `crew_keys/group.json` next to `edmrn.db`
(keys `v, url, group_id, leader, member, webhook, paused`); key files `cmdr-<hash>.json` per
commander in `crew_keys/`; events in the SQLite store. Nothing is stored in `settings.json`.
One installation belongs to at most one group. Typed text (name, relay, label, webhook, expiry,
invite box) survives a re-render of the same group; invite string and link are lost when the group
changes.

**Data shown**

| Field | Format | Example | Maximum |
|---|---|---|---|
| Commander | plain text | `Dreak` | 80 chars (truncated with `...`) |
| Crew key | base64url | `Qm0f...` (43 chars) | 43 chars |
| Group name | printable ASCII | `Explorers` | 64 chars |
| Member name | printable ASCII, `?` replaces other characters | `CMDR Alt` | 64 chars |
| Member status | `<system> - <ship>` | `Colonia - Anaconda Dragonfly` | 80 chars per part, whitespace normalised |
| Member count | rows | 3 | UNVERIFIED (a group of friends; no hard limit found in the dialog) |
| Invite string | `edmrn-crew:1:<base64url>` | | 2000 chars max (about 330 typical) |
| Invite link | URL | `https://galroute.the-hub.cloud/join#edmrn-crew:1:...` | about 370 chars |
| Expiry | `YYYY-MM-DD HH:MM:SSZ` | `2026-11-11 18:22:01Z` | 20 chars |
| Connection reason | last error from the sync, max 80 chars | `cannot reach the relay: [Errno 11001] getaddrinfo failed` | 80 chars |

Connection states (`STATE_TEXT`, `crew_dialog.py:24-26`; states from `crew_sync.py`/`crew_group.py:89-93`):

| State | Text shown after `Connection: ` | Meaning |
|---|---|---|
| `connecting` | `Connecting...` | A connection attempt is running |
| `syncing` | `Catching up...` | Connected, exchanging the event log |
| `live` | `Connected` | In sync; reason text is never appended |
| `waiting` | `Offline, retrying` | Last attempt failed; retry with backoff 1 s doubling to 300 s (jitter); reason appended in parentheses |
| `refused` | `Refused by the relay` | Relay rejected this member (reason `The relay refused this member (<reason or code>).`); Reconnect button appears; no automatic retry |
| `stopped` | `Not connected` | No sync running (for example websockets package missing or key missing) |
| `no group` | `Not in a group` | Not displayed in group view (only a state value) |

Reason fragments appended while `waiting`: `cannot reach the relay: <error>`,
`the connection closed (<code>)`, `the connection failed: <error>`,
`another EDMRN with this commander took over the connection`,
`unexpected <ErrorName> in the connection` (`crew_sync.py:646-668`).

Problem sentences (CRW-06):

- `The group file cannot be used (<detail>); leave the group to start over.`
- `Crew sync needs the websockets package (pip install websockets).`
- `The key of this group membership is missing from crew_keys.`
- `Crew sync could not start; the log file says why.`

**States**

- Unavailable: CRW-07 only (no identity section).
- No commander yet: `Commander: not known yet`, no key row; Create/Join give the "No commander" error.
- Outside a group: CRW-10 to CRW-16.
- Group file unusable: CRW-06 plus only `Leave group` (CRW-60).
- Inside, member: CRW-20 to CRW-32 and `Leave group`.
- Inside, leader: additionally CRW-33, CRW-40 to CRW-51.
- Connecting/connected/offline/refused: label text in CRW-21; Reconnect only when refused.
- Invite created: CRW-44 to CRW-46 appear under the button; CRW-47/48 only with a webhook.
- Armed two-click buttons: `Click again to remove` / `Click again to leave` for 4 seconds, then the
  original text returns.
- Posting: message `Posting to Discord...`.

**Automatic behaviour**

- Re-render of the whole dialog when its structure changes: group config, problem, roster (names,
  leader, removal), active commander or key; checked every 1 s and on each state notification.
- Connection label and Reconnect button update in place on sync state changes.
- Friend status labels update in place when a status is accepted.
- The sync starts at app start if `group.json` exists; sync sends own status (location and ship) on
  change, at most every 30 s, unless paused (paused sends one "paused" status).
- On `Leave group` the friends' projected data is withdrawn but the local log kept.
- Closing the dialog unsubscribes its observers and cancels two-click timers.

**User flows**

1. Create a group: Settings > `Crew...`; type a name; keep or change the relay address;
   `Create group`; the dialog switches to the group view; connection state appears.
2. Invite a friend (leader): optional label, choose expiry, `Create invite`, `Copy` or `Copy link`
   and send it; or, with a webhook saved, `Post to Discord`.
3. Join (friend): `Crew...`, paste the string or link into the box, `Join`.
4. Pause: toggle `Pause sharing`.
5. Remove a member (leader): `Remove`, then click again within 4 s.
6. Leave: `Leave group`, then click again within 4 s.
7. Recover from refusal: read the reason, `Reconnect`.
8. Save a Discord webhook (leader): paste URL, `Save webhook`; empty field clears it.

**Observed functional problems**

- The dialog has no window icon (other dialogs set the app icon) and is not modal or transient.
- Invite validity text under the segmented control has no explanation of "works once" before
  creation (only the message after creation).
- A removed member disappears from the list without any trace in the dialog; there is no list of
  open/expired invites although the roster tracks them (`Roster.invites`).
- The webhook entry shows the stored URL as asterisks after re-render; there is no show/hide
  control.
- `Remove` exists only for non-leader rows and only for the leader; the key copy button exists
  only in the `You` section (other members' keys are not shown or copyable).
- UNVERIFIED: the maximum number of members a relay accepts (not stated in the dialog code).

---

## Theme system

- **Purpose**: switch the colour scheme of the application between 11 PowerPlay-inspired themes.
  The change takes effect after an application restart (SET-41).
- **Files**: `edmrn/theme_manager.py` (reads a theme file into a small colour dictionary),
  `edmrn/ed_theme.py` (fixed `EliteDangerousTheme.COLORS` palette and style helpers), `edmrn/themes/`
  (12 JSON files), `edmrn/theme_editor.py` (editor window, see below).

**Themes that ship** (`edmrn/themes/*.json`; selectable names from `settings_manager.py:219-224`):

| Selector name | File | Button colour (primary) | Button hover | Frame/background | Frame border |
|---|---|---|---|---|---|
| Elite Dangerous (default) | elite_dangerous.json | `#FF8C00` | `#FFA500` | `#1a1a1a` | `#7f4600` |
| Aisling Duval | aisling_duval.json | `#0099FF` | `#0077CC` | `#000f1a` | `#004c7f` |
| Archon Delaine | archon_delaine.json | `#7FFF00` | `#66CC00` | `#0c1a00` | `#3f7f00` |
| Arissa Lavigny Duval | arissa_lavigny_duval.json | `#7F00FF` | `#6600CC` | `#0c001a` | `#3f007f` |
| Denton Patreus | denton_patreus.json | `#FFD700` | `#DAA520` | `#1a1500` | `#7f6b00` |
| Edmund Mahon | edmund_mahon.json | `#00CED1` | `#00A3A6` | `#001515` | `#006768` |
| Felicia Winters | felicia_winters.json | `#87CEEB` | `#6CA5CE` | `#0d1517` | `#436775` |
| Li Yong Rui | li_yong_rui.json | `#FF0000` | `#CC0000` | `#1a0000` | `#7f0000` |
| Pranav Antal | pranav_antal.json | `#FFD700` | `#DAA520` | `#1a1500` | `#7f6b00` |
| Zachary Hudson | zachary_hudson.json | `#32CD32` | `#28A428` | `#051405` | `#196619` |
| Zemina Torval | zemina_torval.json | `#4B0082` | `#3C0068` | `#07000d` | `#250041` |
| (not selectable) | elite_dangerous_backup.json | `#3B8ED0` | `#36719F` | `gray86` | `gray65` |

Pranav Antal and Denton Patreus have identical colours. Appearance mode is always dark
(`ctk.set_appearance_mode("dark")`, `app.py:337`); the JSON light/dark pairs are identical in all
selectable themes. Default selection: `elite_dangerous`; unknown stored names fall back to
`elite_dangerous` display.

**Colour roles actually used by the application** (`theme_manager.py:11-64`). The theme file is
re-read from disk on every call of `get_theme_colors()`. Roles:

| Role key | Meaning | Source in theme JSON | Fallback value (when file missing) |
|---|---|---|---|
| `primary` | Main accent: primary buttons, active tab, header labels (`CMDR:`, `CR:`...), sliders | `CTkButton.fg_color[0]` | `#FF8C00` |
| `primary_hover` | Hover/selected variant of the accent; selected tab; window border when focused | `CTkButton.hover_color[0]` | `#FFA500` |
| `secondary` | Secondary buttons, unselected tab, muted text, stopped status | `CTkFrame.fg_color[0]` | `#2E2E2E` |
| `secondary_hover` | Hover of secondary controls | `CTkFrame.fg_color[1]` (same as secondary in all themes) | `#3E3E3E` |
| `background` | Window and tab background | `CTkToplevel.fg_color[0]` | `#212121` |
| `frame` | Card/panel background | `CTkFrame.fg_color[0]` (equals `background` in all selectable themes) | `#2E2E2E` |
| `border` | Frame/card border, idle window border | `CTkFrame.border_color[0]` | `#424242` |
| `text` | Normal text | fixed `#E0E0E0` (not from the file) | `#E0E0E0` |
| `accent` | Table text colour in the Log tab, general accent | same as `primary` | `#FF8C00` |
| `success` | Success colour | fixed `#4CAF50` | `#4CAF50` |
| `success_hover` | Hover of success | fixed `#45A049` | `#45A049` |

Button styles (`ThemeManager.apply_button_theme`, `theme_manager.py:65-89`):

| Style | fg | hover | text | border |
|---|---|---|---|---|
| `primary` | `primary` | `primary_hover` | white | `border`, width 1 |
| `success` | `primary` (not green) | `primary_hover` | white | none |
| `secondary` | `secondary` | `secondary_hover` | `text` | `border`, width 1 |

Fixed colours used outside the theme files (hard-coded in code): status green `#4CAF50`, error red
`#FF6B6B`, warning orange `#FFA500`, grey `#888888`, skipped orange `#FFA500`, fuel colours
`#4CAF50`/`#FFC107`/`#FF9800`/`#F44336`, selected table row `#333300`, splash backgrounds
`#0d0d14` and `#ff8c00`, Patreon button `#FF424D`, stop buttons `#FF6B6B` / hover `#CC5555`.

**Fixed palette `EliteDangerousTheme.COLORS`** (`ed_theme.py:5-20`), used by the Log column
selector and detail window and some style helpers; it does not follow the selected theme:

| Key | Value | Meaning |
|---|---|---|
| `primary_orange` | `#FF7F00` | Main orange accent |
| `secondary_orange` | `#CC6600` | Darker orange (hover) |
| `dark_orange` | `#994D00` | Dark orange (border) |
| `light_orange` | `#FFAA55` | Light orange (info border, stats text) |
| `background_dark` | `#0D0D0D` | Darkest background |
| `panel_dark` | `#1A1A1A` | Panel background |
| `panel_medium` | `#262626` | Raised panel, input background |
| `text_orange` | `#FF7F00` | Orange text |
| `text_white` | `#E0E0E0` | Normal text |
| `text_gray` | `#888888` | Muted text |
| `accent_blue` | `#00AEFF` | Group header blue |
| `success_green` | `#4CAF50` | Success |
| `warning_red` | `#FF6B6B` | Error/warning |
| `border_orange` | `#FF7F00` | Orange border |

The style helper tables `get_button_style` (default, secondary, success, warning), `get_frame_style`
(default, main, info) and `get_text_style` (default, header, stats, navigation; fonts `Segoe UI` and
`Consolas`) exist in `ed_theme.py:50-122`. UNVERIFIED which of them is used by a visible window
(`apply_elite_dangerous_theme` is imported by `app.py:36` but never called; `EliteDangerousTheme.COLORS`
is used in `log_viewer.py`).

**Full token set of a theme JSON file** (each key is a CustomTkinter widget theme block; colours
are `[light, dark]` pairs, identical in the shipped themes). Only the three blocks marked "read" are
consumed by `ThemeManager`; the rest is not applied to widgets because `ed_theme.apply_theme()` is
never called and the app sets `ctk.set_default_color_theme("blue")` (`app.py:338`). UNVERIFIED:
whether any widget picks up the other blocks at runtime; code search found no loader.

| Block | Keys | Meaning |
|---|---|---|
| `CTk` | `fg_color` | Root window background |
| `CTkToplevel` (read: `fg_color` -> `background`) | `fg_color` | Dialog window background |
| `CTkFrame` (read: `fg_color`, `border_color`) | `corner_radius` (8), `border_width` (1), `fg_color`, `top_fg_color`, `border_color` | Panels |
| `CTkButton` (read: `fg_color`, `hover_color`) | `corner_radius` (6), `border_width` (1), `fg_color`, `hover_color`, `border_color`, `text_color`, `text_color_disabled` | Buttons |
| `CTkLabel` | `corner_radius`, `fg_color` (`transparent`), `text_color` | Labels |
| `CTkEntry` | `corner_radius`, `border_width` (2), `fg_color`, `border_color`, `text_color`, `placeholder_text_color` | Text fields |
| `CTkCheckBox` | `corner_radius`, `border_width`, `fg_color`, `border_color`, `hover_color`, `checkmark_color`, `text_color`, `text_color_disabled` | Checkboxes |
| `CTkSwitch` | `corner_radius`, `border_width`, `button_length`, `fg_color`, `progress_color`, `button_color`, `button_hover_color`, `text_color`, `text_color_disabled` | Switches |
| `CTkRadioButton` | `corner_radius`, `border_width_checked`, `border_width_unchecked`, `fg_color`, `border_color`, `hover_color`, `text_color`, `text_color_disabled` | Radio buttons |
| `CTkProgressBar` | `corner_radius`, `border_width`, `fg_color`, `progress_color`, `border_color` | Progress bars |
| `CTkSlider` | `corner_radius`, `button_corner_radius`, `border_width`, `button_length`, `fg_color`, `progress_color`, `button_color`, `button_hover_color` | Sliders |
| `CTkOptionMenu` | `corner_radius`, `fg_color`, `button_color`, `button_hover_color`, `text_color`, `text_color_disabled` | Dropdowns |
| `CTkComboBox` | `corner_radius`, `border_width`, `fg_color`, `border_color`, `button_color`, `button_hover_color`, `text_color`, `text_color_disabled` | Combo boxes |
| `CTkScrollbar` | `corner_radius`, `border_spacing`, `fg_color`, `button_color`, `button_hover_color` | Scrollbars |
| `CTkSegmentedButton` | `corner_radius`, `border_width`, `fg_color`, `selected_color`, `selected_hover_color`, `unselected_color`, `unselected_hover_color`, `text_color`, `text_color_disabled` | Segmented buttons |
| `CTkTextbox` | `corner_radius`, `border_width`, `fg_color`, `border_color`, `text_color`, `scrollbar_button_color`, `scrollbar_button_hover_color` | Multi-line text |
| `CTkScrollableFrame` | `label_fg_color` | Scrollable frame label |
| `DropdownMenu` | `fg_color`, `hover_color`, `text_color` | Dropdown popup |
| `CTkFont` | per OS `family`, `size`, `weight` (Windows: Segoe UI, 13, normal; macOS: SF Display; Linux: Roboto) | Default font |
| `CTkTabview` | `corner_radius`, `border_width`, `fg_color`, `border_color`, `segmented_button_*` (fg, selected, selected_hover, unselected, unselected_hover), `text_color`, `text_color_disabled` | Tab view (present in the 11 selectable themes, absent in the backup) |

Example (Elite Dangerous): `CTkEntry.fg_color #1a0e00`, `CTkSwitch.fg_color #7f4600`,
`progress_color #FF8C00`, `CTkTextbox.border_color #FF8C00`, `CTkLabel.text_color #E0E0E0`.

### Theme editor window (`ThemeEditor`)

- **Purpose**: edit five groups of colours of `elite_dangerous.json`.
- **Reachability**: the only opener is `EDMRN_App._open_theme_editor` (`app.py:2353`), which has no
  caller in the code base (no button or menu entry exists). The window is not reachable from the
  GUI today. It also references `self.ed_theme`, an attribute that is never assigned, so opening it
  would raise and show Error `Error` / `Could not open theme editor:\n<error>`.
- **Window**: `CTkToplevel`, title `Theme Editor`, 600x500, transient, modal (`grab_set`).

**Layout today**

```
+-- Theme Editor ----------------------------+
|            Theme Editor (20 bold)          |
| +---------------------------------------+  |
| | Background Colors                     |  |
| |  Main Background  [swatch][Pick Color][#hex]
| |  Frame Background [swatch][Pick Color][#hex]
| | Button Colors  (Button Color, Button Hover)
| | Text Colors    (Label Text, Button Text)
| | Border Colors  (Frame Border)          |  |
| +---------------------------------------+  |
| [Save Theme] [Reset to Default]  [Close]   |
+--------------------------------------------+
```

| ID | Label | Type | What it does | Source |
|---|---|---|---|---|
| THM-01 | `Main Background` (`CTk.fg_color`), `Frame Background` (`CTkFrame.fg_color`), `Button Color` (`CTkButton.fg_color`), `Button Hover` (`CTkButton.hover_color`), `Label Text` (`CTkLabel.text_color`), `Button Text` (`CTkButton.text_color`), `Frame Border` (`CTkFrame.border_color`) | 7 colour rows under section titles `Background Colors`, `Button Colors`, `Text Colors`, `Border Colors` | Each row: label (width 150), colour swatch (50x30), `Pick Color` button, hex entry (width 100) | theme_editor.py:52-97 |
| THM-02 | `Pick Color` | button | Opens the system colour chooser (title `Pick Color`); the chosen colour is stored upper-case and the swatch updates. The hex entry is not updated. | :91-92, 106-112 |
| THM-03 | hex entry | entry | On Enter: accepts `#` followed by hex digits (no length check) and updates swatch and theme; otherwise Error `Invalid Format` / `Color must be in hex format (e.g., #FF8C00)` or `Invalid Color` / `Please enter a valid hex color (e.g., #FF8C00)` | :94-97, 113-123 |
| THM-04 | `Save Theme` | button | Writes the edited values into `elite_dangerous.json` (always this file); Info `Success` / `Theme saved successfully!\nRestart the application to see all changes.` or Error `Error` / `Failed to save theme: <error>` | :69-70, 128-135 |
| THM-05 | `Reset to Default` | button | Confirm `Reset Theme` / `Are you sure you want to reset to default theme?`; on Yes loads a built-in default and reopens the editor (not written to disk until Save) | :71-72, 136-141 |
| THM-06 | `Close` | button | Closes the window | :73-74 |

Observed functional problems: the editor always edits and saves `elite_dangerous.json` regardless
of the selected theme; the `ErrorDialog` calls in `update_color_from_entry` use `self.app`, which
does not exist (raises `AttributeError`); edited roles `CTk.fg_color`, `CTkLabel.text_color`,
`CTkButton.text_color` are not read by `ThemeManager`; reset passes `None` as theme manager; the
background of the editor canvas is hard-coded `#1A1A2E`.

---

## Secondary windows (`edmrn/gui.py`) and update flow

All secondary windows are `CTkToplevel`. Common behaviour: title-bar icon set from
`assets/explorer_icon.ico` (+ PNG), re-applied on every `<Map>`/`<FocusIn>` (section "Icons, images
and fonts"); line numbers given for `AboutWindow` rows are approximate (+/- 5); colours from
`theme_manager.get_theme_colors()` when the parent is the app object, otherwise fallback colours
(frame `#2E2E2E`, primary `#FF8C00`, secondary `#666666`, text `#E0E0E0`, background `#212121`,
primary_hover `#FFB060`); positioned centred over the parent; the four message dialogs are modal
(`grab_set`), briefly `-topmost`, close with the window close button the same as `OK`
(`ConfirmDialog` close = `No`).

### Message dialogs (Info, Warning, Error, Confirm, Success)

| ID | Dialog | Icon (emoji, size 48) | Buttons | Return | Layout / source |
|---|---|---|---|---|---|
| DLG-01 | `InfoDialog(master, title, message)` | `ℹ️` | `OK` (primary colour) | none | gui.py:1740-1887 |
| DLG-02 | `WarningDialog(master, title, message)` | `⚠️` | `OK` (orange `#FFA500`) | none | gui.py:1888-1964 |
| DLG-03 | `ErrorDialog(master, title, message)` | `❌` | `OK` (red `#FF6B6B`) | none | gui.py:1965-2041 |
| DLG-04 | `ConfirmDialog(master, title, message)` | `❓` | `Yes` (primary), `No` (secondary) | `get_result()` blocks (`wait_window`) and returns True/False | gui.py:2042-2143 |
| DLG-05 | `SuccessDialog(master, title, message)` | `✅` | `OK` (primary) | none | gui.py:977-1141; used after loading a backup (`file_operations.py:324`) |

Layout of all five: icon on top, message text (wraps at about 350 px, centred, size 12, theme `text`),
button row at the bottom; size 400x200 initial then fitted to content. Title and message are
free text supplied by the caller; `message` may contain `\n`.

```
+-- <title> -----------------+
|          (icon)            |
|   message, wrapped         |
|        [ OK ]              |   or  [Yes] [No]
+----------------------------+
```

### Processing dialog

| ID | Element | Behaviour | Source |
|---|---|---|---|
| DLG-10 | window `Processing…`, 360x170, fixed size, modal | Shown while a route optimisation runs (`route_management.py:195`) | gui.py:837-975 |
| DLG-11 | status label, default `Preparing…`, bold 14 | Shows the stage name (underscores replaced, capitalised), updated through `update(message, fraction)` | :910, 941-956 |
| DLG-12 | progress bar 280x14 | Determinate with `fraction` 0..1, indeterminate animation when `fraction` is None | :914-917, 946-953 |
| DLG-13 | `Cancel` (width 120) | Calls the cancel callback (sets the cancel event of the optimisation) and closes; the window close button does the same | :919-931, 933-939 |

### About window (`AboutWindow`, opened by the bottom button `About`)

500x600 fixed, modal, title `ED Multi Route Navigation (EDMRN)`, centred over the main window
(`gui.py:1143-1405`).

| ID | Element | Content | Source |
|---|---|---|---|
| DLG-20 | logo | `explorer_icon.png` at 80x80 | :1230-1240 |
| DLG-21 | `Elite Dangerous Multi-Route Navigation` | label bold 16 | :1241-1247 |
| DLG-22 | `EDMRN` | label bold 28 in primary colour | :1248-1254 |
| DLG-23 | info box | Lines: `Version 3.3.0 - AGPL-3 Licensed`, `January 2026`, `Developed by CMDR Ninurta Kalhu`, `Elite Dangerous © Frontier Developments plc.`, `*This tool is not affiliated with, endorsed by, or connected to Frontier Developments plc.*`, `Data & Services:` with `• Route calculations by Spansh (spansh.co.uk)`, `• System data by EDSM (edsm.net)`, `• GMP data source by EdAstro (EdAstro.com)`, `Thank you to all contributors and the Elite Dangerous exploration community!`, `Fly safe, Commander! o7` (text is hard-coded; it names the upstream author and version `3.3.0`) | :1255-1290 |
| DLG-24 | `Show User Manual` | button; closes nothing, calls the manual opener | :1290-1300 |
| DLG-25 | `GitHub` | opens `https://github.com/NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer` in the browser | :1300-1313 |
| DLG-26 | `Discord` | opens `https://discord.gg/DWvCEXH7ae` | :1314-1326 |
| DLG-27 | `Donate` | opens `https://ko-fi.com/ninurtakalhu` | :1327-1338 |
| DLG-28 | `© 2025-2026 Ninurta Kalhu (S.C.) | All Rights Reserved (Licence AGPL-3)` | label | :1339-1345 |

The same About, Manual, GitHub, Discord and Patreon (`https://www.patreon.com/c/NinurtaKalhu`)
buttons also exist in the main window's bottom bar (`ui_components.py:860-892`; outside this slice).

### Manual window (`ManualWindow`, opened by `User Manual` or DLG-24)

Title `EDMRN - User Manual`, 640x850 (height re-fitted to the navigation list after 80 ms, minimum
600 wide), modal. Left navigation of 16 buttons (150 px wide), right a read-only monospace
(Consolas 12) textbox. Every section renders the header
`ED MULTI ROUTE NAVIGATION (EDMRN) v3.3.0 - USER MANUAL` and
`Optimized routes, journal tracking, Spansh primary autocomplete.` followed by the section title
and lines (`gui.py:35-835`). The active navigation button uses the primary colour. Esc does not
close it despite the text (see XC-02).

| ID | Navigation button | Section title in the text | Topics |
|---|---|---|---|
| DLG-30 | `What's New` | WHAT'S NEW IN v3.3.0 | Fuel tracker, other v3.3.0 changes |
| DLG-31 | `Quick Start` | QUICK START GUIDE | Steps to prepare a route and run it |
| DLG-32 | `System Info` | SYSTEM INFO TAB | System lookup features |
| DLG-33 | `Log Viewer` | LOG VIEWER | Journal view, filters, column selector, detail view, notes, performance |
| DLG-34 | `Route Opt` | ROUTE OPTIMIZATION | Optimiser |
| DLG-35 | `Custom Route` | CUSTOM ROUTE PLANNER | Custom routes |
| DLG-36 | `Neutron` | NEUTRON HIGHWAY | Neutron routing |
| DLG-37 | `Tracking` | ROUTE TRACKING | Visited/skipped tracking |
| DLG-38 | `Fuel Tracker` | FUEL TRACKER | Fuel monitoring |
| DLG-39 | `Galaxy` | GALAXY PLOTTER | Galaxy plotter |
| DLG-40 | `Settings & Overlay` | SETTINGS & OVERLAY | Theme customisation (11 themes, borderless), journal configuration, overlay settings, autosave options |
| DLG-41 | `Shortcuts` | KEYBOARD SHORTCUTS | Ctrl+O, Ctrl+S, Ctrl+L, F5, Esc (see XC-02) |
| DLG-42 | `Backup` | BACKUP SYSTEM | Automatic backups and restore |
| DLG-43 | `Troubleshooting` | TROUBLESHOOTING | Common problems (starts with "NO JOURNAL DATA DETECTED") |
| DLG-44 | `API Credits` | API CREDITS & ATTRIBUTION | Data sources |
| DLG-45 | `Support` | SUPPORT & COMMUNITY | Links and support |

Section text is static, hard-coded in code; it describes the upstream application and states
overlay opacity "0-100%" although the control is 50-100%, and `Intervals: 1 minute, 5 minutes,
10 minutes` (no `Never`).

### Update check flow (`edmrn/updater.py`)

1. Start-up: `setup_auto_updates(app, delay_seconds=5)` runs from `_complete_startup_in_background`
   (500 ms after window creation, `app.py:400, 3016-3018`). A daemon thread waits 5 s, asks the
   GitHub API `releases/latest` of the repository `NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer`
   (timeout 10 s, no retries), compares the tag with `__version__` (`3.3.0`) and, only if newer,
   shows the update dialog (UPD-01). No message is shown if up to date or on error (logged).
2. Manual: header button `🔄 Check Update` (`app.py:252-261`, command `_check_for_updates`) runs the
   same check on the Tk thread (the window blocks until the request ends, up to about 10 s) and
   shows:
   - newer version: update dialog UPD-01;
   - error: Info `Update Check` / `Could not check for updates: <sentence>`, with sentences such
     as `GitHub's latest release has no usable version number ('<tag>').`, `No connection to GitHub.`,
     `GitHub did not answer within <n>.`, `GitHub is overloaded. Try again in a minute.`,
     `GitHub rejected the request: <detail>`, `Unexpected error talking to GitHub.`;
   - current: Info `Update Check` / `✅ EDMRN is already up to date!\n\nCurrent Version: v3.3.0\nLatest Version: v<latest or Unknown>`.
   - if no update manager exists the browser opens the GitHub releases page and the output line
     `GitHub releases Page` is logged.

| ID | Element | Behaviour | Source |
|---|---|---|---|
| UPD-01 | window `EDMRN - New Version Available`, fixed size fitted to content, modal, centred, topmost for 100 ms | Dialog shown when a newer release exists | updater.py:51-177 |
| UPD-02 | `🎉 New Version Available!` | green bold 18 | :89-94 |
| UPD-03 | `Current: v<current>` and `New: v<latest>` | grey 12 / green bold 14 | :97-108 |
| UPD-04 | `Download the new version from GitHub.` | label | :111-117 |
| UPD-05 | `🐙 Download from GitHub` | green button; opens the release page and closes the dialog | :121-131 |
| UPD-06 | `Close` | secondary-colour button; closes | :143-153 |

Problem: the check targets the upstream repository, not EDMRN's own remote (ADR-0010 states
EDMRN is independent); the version is the upstream `3.3.0` string.

### Splash screen (`SplashScreen`, `edmrn/splash.py`, started by `main.py`)

| ID | Element | Behaviour | Source |
|---|---|---|---|
| SPL-01 | window `EDMRN - Loading`, 500x400, not resizable, centred on screen, topmost, background `#0d0d14` | Shown while the app is built in a background thread; the main window stays hidden until it is ready | splash.py:7-27; main.py:41-71 |
| SPL-02 | logo `assets/edmrn_logo.png` at 100x100 (fallback text `EDMRN` in orange bold 28) | | splash.py:42-48 |
| SPL-03 | `Elite Dangerous Multi Route Navigation` (Arial 10, grey `#555555`) | | :52 |
| SPL-04 | `Welcome home, Commander. EDMRN routing services are at your disposal` (Arial 8, orange) | | :54 |
| SPL-05 | progress bar 150x2 px | Animated loop independent of real progress (restarts every cycle); `update_progress`/`set_status` are never called, `set_status` does nothing | :56-57, 106-122 |
| SPL-06 | `v3.3.0 | CMDR Ninurta KALHU` (Arial 7) | hard-coded | :59 |
| SPL-07 | moving star field (80 white points) and a gradient line | decorative, 33 ms frame | :29-40, 85-104 |

The splash stays at least 3.0 seconds, then closes when the app is ready (`main.py:56-63`). Closing
it with the window close button destroys only the splash.
Failure to start: the error is printed and the process exits with code 1; no dialog.
`assets/splash.html` exists but no code references it (UNVERIFIED purpose).

---

## Cross-cutting

### Keyboard shortcuts and hotkeys

| ID | Key | Effect | Scope | Source |
|---|---|---|---|---|
| XC-01 | `Ctrl+O` | Toggles the in-game overlay (same as SET-11) | Tk binding on the main window: works only while an EDMRN window has keyboard focus. It is not a global hotkey, so it does not work while Elite Dangerous has focus (the manual claims it does). | app.py:1765-1766 |
| XC-02 | `Ctrl+S`, `Ctrl+L`, `F5` | Documented in the manual (DLG-41) but no binding exists in the code | none | grep of `edmrn/` found no binding |
| XC-03 | `Esc` | Closes the status-update dialog (`route_management.py:117`, result `cancel`) and the visited-systems dialog (`visit_history_dialog.py:171`); documented as closing the manual and most dialogs, which is not implemented for the windows covered here | those two dialogs only | as listed |
| XC-04 | `Enter` | In the theme editor hex entry applies the colour (not reachable); in system autocomplete entries selects a suggestion (`autocomplete_entry.py:39, 131`, other slice) | | as listed |
| XC-05 | Mouse: double-click row, right-click row | Log tab (LOG-07, LOG-08); System Info tables bind the same events (other slice) | | |

No global (system-wide) hotkey registration (`RegisterHotKey`, `pynput`, `keyboard`) exists in
`edmrn/`.

### Sounds

| Sound | File / source | Played when | Conditions | Source |
|---|---|---|---|---|
| Fuel warning | `sounds/fuel-low.wav` through `winsound.PlaySound` (asynchronous). If the file is missing, a `winsound.Beep` with frequency `800 + 4 * volume` Hz and duration `100 + 2 * volume` ms is played instead. | Whenever the fuel percentage is at or below the warning level (SET-51), at most once every 60 seconds, not when current fuel and percentage are both 0 | `Sound Alert` on (SET-53) and volume greater than 0 (SET-54); Windows only (`winsound`) | fuel_tracker.py:144-171 |

No other sound exists. The only audio file in `sounds/` is `fuel-low.wav`. Errors in playback are
logged only.

### Icons, images and fonts

- Window and dialog icons: `assets/explorer_icon.ico` (title bar via `iconbitmap` and
  `WM_SETICON` big and small, Windows) and `assets/explorer_icon.png` (resized to 32x32, via
  `iconphoto`). They are applied at creation and re-applied 100 ms after each `<Map>` and
  `<FocusIn>` of the main window and of the Manual, About, Success, Info, update and optional-columns
  windows, because Windows resets them (`app.py:137-195, 509-572`; `gui.py` per class;
  `updater.py:179-207`). Crew dialog, Theme editor, Log column selector, Log detail, Warning/Error/
  Confirm and Processing dialogs: Processing and Success/Info set icons; Warning/Error/Confirm
  UNVERIFIED (not all inspected); Crew dialog, Log windows and Theme editor set none.
- Header logo: `explorer_icon.png` at 32x32 (`app.py:213-221`). About logo: same at 80x80.
- Splash logo: `assets/edmrn_logo.png`, 100x100. `edmrn_logo.png` also exists in the repo root
  (not referenced by the files read).
- Text icons: emoji used inside labels and buttons (for example `📺 Overlay`, `💾 Auto-save`,
  `📝 Journal`, `🎨 Theme`, `⛽ Fuel Tracker`, `▶ Start`, `⏹ Stop Overlay`, `💾 Save`, `🔍 Test`,
  `✓ Apply`, `🔄`, `🔄 Check Update`, `🐙`, `🎉`, `✅`, `ℹ️`, `⚠️`, `❌`, `❓`, `⋮`, borderless title
  buttons `—`, `⬜`, `❐`, `✕`); log event icons `🔍 🌐 ✨ 📡 💧 🪐 📝`. `edmrn/icons.py` holds eight
  constants (`LOCATION 📍`, `ARROW_RIGHT ➡️`, `COMPASS 🧭`, `TARGET 🎯`, `LIGHTNING ⚡`, `CHART 📊`,
  `ROCKET 🚀`, `CLOSE ✕`) used by the overlay, galaxy plotter, neutron and tracker code.
  The Crew dialog uses no icons.
- Fonts: `Segoe UI` (dialogs, About, Log windows, theme editor, update dialog), `Consolas` (Manual
  text, stats style), `Arial` (splash), CustomTkinter default font elsewhere (13 px, Segoe UI on
  Windows per the theme `CTkFont` block). Sizes in use: 7 to 48 (message-dialog emoji 48).

### Window behaviour

- Main window: `1196x730` initial, minimum `1200x700` (the minimum is larger than the initial
  width), title `ED Multi Route Navigation (EDMRN)`. Closing it runs cleanup (overlay, journal
  monitor, auto-save stopped). `window_geometry` in `settings.json` is saved only on a theme change
  and never restored.
- Borderless chrome code exists (custom title bar with `—`, `⬜`/`❐`, `✕` buttons and resize grips)
  but is unreachable because `_borderless` is `False` (SET-42).
- Global Tk exceptions "invalid command name" and "bad window path name" are ignored silently;
  others are written to `debug_startup.log` and stderr (`main.py:18-25`). A main-loop crash is
  appended to `edmrn_crash.log`.

### Persistence summary for this slice

| Data | Where | Keys / file |
|---|---|---|
| All Settings tab values and Log columns | `<app data>/settings.json` | see Settings table |
| Crew group, webhook, pause flag | `crew_keys/group.json` (next to `edmrn.db`) | `v,url,group_id,leader,member,webhook,paused` |
| Crew identity keys | `crew_keys/cmdr-<hash>.json` | `v,author,private_key,high_water` |
| Theme colours | `edmrn/themes/<name>.json` (read-only at runtime except by the unreachable editor) | |
| Application log | `<app data>/logs/edmrn_YYYYMMDD.log` | level INFO |
| Log tab notes | memory only | |
