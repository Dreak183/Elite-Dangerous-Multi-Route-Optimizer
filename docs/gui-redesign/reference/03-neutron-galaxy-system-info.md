# GUI functional reference 03: Neutron Highway, Galaxy Plotter, System Info

Scope: three tabs of the EDMRN main window (`ctk.CTkTabview`, `edmrn/app.py:601-621`). Functional
description of the code as it is; no visual opinions. "Source" is `file:line` relative to the repo
root. Every UI string is quoted exactly; emoji prefixes are part of the string.

Reading notes for the implementer:

- The live code path for the Galaxy Plotter is the set of `EDMRN_App._calculate_galaxy_route` /
  `_display_galaxy_route_*` / `_galaxy_*` / `_update_galaxy_*` methods in `edmrn/app.py`. The class
  `GalaxyHandler` (`edmrn/galaxy_handler.py`) and `JournalHandler` (`edmrn/journal_handler.py`) are
  instantiated (`app.py:366`, `app.py:368`) but no call to their methods was found; button commands
  are bound to the `app.py` methods. `galaxy_handler.py` differs in text (no emoji, `[Visited]` /
  `[Current]` prefixes) and is documented here only where it matters.
- `EDMRN_App._create_neutron_tab` (`app.py:971`) is a dead upstream copy (it builds a text output
  `neutron_output`). The live Neutron tab is `UIComponents.create_neutron_tab`
  (`edmrn/ui_components.py:226-411`, called at `app.py:657`).
- Tab names (exact): "Neutron Highway", "Galaxy Plotter", "System Info" (`app.py:616-619`). The
  Neutron tab is built at startup; "Galaxy Plotter" and "System Info" are built lazily on first
  visit and also preloaded 1.5 s after startup, one tab every 700 ms (`app.py:1097-1152`).
- Dialog helpers `ErrorDialog`, `WarningDialog`, `InfoDialog`, `ConfirmDialog` come from
  `edmrn/gui.py` (title, message); their look is out of scope.

---

## Neutron Highway tab

- **Purpose**: plot a neutron-star route between two systems for the commander's jump range
  through Spansh's neutron plotter, then walk through it waypoint by waypoint with the next system
  always on the clipboard. The fork adds range auto-fill from the ship, saved routes and a
  supercharged-start correction.

- **Layout today**: two columns (weights 3 : 2) inside one frame (`ui_components.py:229-232`).

```
+-------------------------------------------------+-------------------------+
| LEFT                                            | RIGHT                   |
|  [title]                                        |  Neutron Systems:       |
|  [From System: ______] [To System: ______]      |  +-------------------+  |
|  [Ship Jump Range (LY): ____ ] [FSD Boost: v ]  |  | scrollable list   |  |
|  [range text]                                   |  |  1.⚡ Sol | 1 jump |  |
|  [x] Auto                                       |  |  2.☀️ ...         |  |
|  [ Optimize & Track ]                           |  |  ...              |  |
|  [<] [   current waypoint   ] [>]               |  +-------------------+  |
|  [ Saved routes ]                               |                         |
|  info label (status / messages)                 |                         |
|  Find Nearest System: X [] Y [] Z [] [FIND]     |                         |
|  Neutron Statistics line / Progress line        |                         |
|  Data provided by Spansh.co.uk                  |                         |
+-------------------------------------------------+-------------------------+
```

### Elements

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| NEU-01 | `🌟 Neutron Highway Route Planner` | label | Heading. | always | ui_components.py:238 |
| NEU-02 | `From System:` | label | Caption of NEU-03. | always | ui_components.py:247 |
| NEU-03 | placeholder `Type 3+ chars for suggestions...` | entry with autocomplete (AutocompleteEntry) | Start system. After 3 characters and a 200 ms pause a dropdown (max 10) of system names from Spansh (fallback EDSM) opens; Up/Down select, Return or click picks, footer text `System data by Spansh (primary) / EDSM (fallback)`. Blank at Calculate time is filled from the journal's current system. | always | ui_components.py:250; autocomplete_entry.py; system_autocomplete.py |
| NEU-04 | `To System:` | label | Caption of NEU-05. | always | ui_components.py:260 |
| NEU-05 | placeholder `Type 3+ chars for suggestions...` | entry with autocomplete | Destination system. Blank at Calculate time is filled from the first unvisited system of the Route Tracking route; if none, the error dialog (see Inputs). | always | ui_components.py:263 |
| NEU-06 | `Ship Jump Range (LY):` | label | Caption of NEU-07. | always | ui_components.py:275 |
| NEU-07 | (no label) | entry (`neutron_range_var`, text) | Jump range in LY used for the plot. Initial value = the route tab's range field. Overwritten whenever the route tab's range (`app.jump_range`) is written (`app.py:2534`, trace at `ui_components.py:132`), including by the Auto switch. | always | ui_components.py:277-279 |
| NEU-08 | range text, initial `Waiting for ship data from the journal` | label (shared state with the identical label under the Route Optimization range field) | Shows the ship's range from the journal: see "Data shown". | always | ship_panel.py:31-45, 165-176 |
| NEU-09 | `Auto` | checkbox | On (default each start): the range field follows the laden jump range of the current ship and a Loadout also sets NEU-11. Any key that changes the text of NEU-07 (or of the Route Optimization range field) switches it off; ticking it on again re-applies the range (not the boost). Shared with the Route Optimization tab. Not persisted. | always | ship_panel.py:57-139, 174 |
| NEU-10 | `FSD Boost:` | label | Caption of NEU-11. | always | ui_components.py:284 |
| NEU-11 | `x4 (Normal)` / `x6 (Caspian)` (initial variable value is `x4`, not one of the two values) | dropdown (CTkOptionMenu) | Supercharge multiplier for the plot: text containing `x6` means 6, otherwise 4. Set automatically by a Loadout while Auto is on (`x6 (Caspian)` for the Mk II drive, else `x4 (Normal)`). | always | ui_components.py:287-295; ship_panel.py:98-104 |
| NEU-12 | `Optimize & Track` at start; `Calculating...` while running; `🚀 Calculate Neutron Route` after the first completed run (success or failure) | button | Validates inputs and starts the plot in a background thread (see Flows). | Disabled while a plot runs (state "disabled"). | ui_components.py:298-305; neutron_manager.py:12-112 |
| NEU-13 | `<` | button | Previous waypoint (index minus 1; statuses unchanged). | State is set by navigation updates: enabled iff index > 0. At startup before any route it is enabled but does nothing. | ui_components.py:308; neutron_manager.py:113, 156-163 |
| NEU-14 | `No route calculated` at start; afterwards the current waypoint's system name | button (width 300) | Copies the current waypoint's system name to the clipboard; info label shows `📋 Copied: <system>`. With no route: warning dialog `Warning` / `No neutron route calculated.` | always | ui_components.py:315-322; neutron_manager.py:123-155 |
| NEU-15 | `>` | button | Marks the current waypoint visited, advances one, copies the new current system, shows `📋 Auto-copied: <system>`. | Enabled iff index < last. At startup before any route it is enabled but does nothing. | ui_components.py:323; neutron_manager.py:117-122, 156-163 |
| NEU-16 | `Saved routes` | button (width 200) | Opens the Saved routes dialog (SRD-*). | always | neutron_resume.py:216-225 |
| NEU-17 | initial `ℹ️ Enter systems and click Calculate to plan your neutron highway route` | label (multi-line capable) | Single status/message line for progress, success, note and error texts. | always | ui_components.py:336-340 |
| NEU-18 | `Find Nearest System:` plus `X:`, `Y:`, `Z:` | labels | Captions of NEU-19..21. | always | ui_components.py:346-357 |
| NEU-19 | (no placeholder) | entry (width 70) | X coordinate in LY (decimal number). | always | ui_components.py:350 |
| NEU-20 | (no placeholder) | entry (width 70) | Y coordinate in LY. | always | ui_components.py:354 |
| NEU-21 | (no placeholder) | entry (width 70) | Z coordinate in LY. | always | ui_components.py:358 |
| NEU-22 | `FIND` | button | Asks Spansh `/api/nearest` for the system nearest to X/Y/Z (background thread) and opens a confirm dialog offering to copy its name. Not connected to NEU-03/05. | always | ui_components.py:361; app.py:2400-2471 |
| NEU-23 | initial `📊 Neutron Statistics \| No route calculated` | label (monospace) | Route totals. | always | ui_components.py:381; neutron_manager.py:196-208 |
| NEU-24 | initial `🎯 Progress Status \| Ready to calculate` | label (monospace) | Position in the route. | always | ui_components.py:386; neutron_manager.py:191-195, 207 |
| NEU-25 | `Data provided by Spansh.co.uk` | label | Attribution. | always | ui_components.py:392 |
| NEU-26 | `Neutron Systems:` | label | Heading of the list. | always | ui_components.py:400 |
| NEU-27 | (list) | scrollable frame of one label per waypoint | The route, see "Data shown". Rows are not clickable in effect (cursor `hand2`, no handler). | always | ui_components.py:403-411; neutron_manager.py:274-323 |
| NEU-28 | `No neutron route calculated` | label inside NEU-27 | Empty state of the list. Only appears after `update_neutron_systems_list` ran with no route; the list is empty (blank) before that. | no route and list refreshed | neutron_manager.py:284-291 |

Saved routes dialog (modal-like `CTkToplevel`, 640x420, single instance; reopening lifts the
existing one) `neutron_resume.py:266-335`:

| ID | Label | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| SRD-01 | window title `Saved neutron routes` | dialog | Container with a scrollable body. | opened by NEU-16 | neutron_resume.py:276-288 |
| SRD-02 | `<from> → <to>   <state>   <range> ly <boost>   <YYYY-MM-DD HH:MM>` | label per route | One saved route. `<state>` is `done` (last waypoint visited) or `<visited>/<total>`; `<range>` is `%g` formatted; `<boost>` is `x4` or `x6`; time is local time of last update. Newest first. | at least one route | neutron_resume.py:302-309 |
| SRD-03 | `Resume` | button per row (width 80) | Loads the route into the Neutron tab and continues per the resume decision (see Flows). Closes the dialog on success. | per row | neutron_resume.py:312, 315-323 |
| SRD-04 | `Delete` | button per row (width 70) | Deletes the saved route at once (no confirmation) and refreshes the list. | per row | neutron_resume.py:310, 325-333 |
| SRD-05 | `No saved neutron routes yet. Plot one in this tab.` | label | Empty state. | store empty | neutron_resume.py:300 |
| SRD-06 | `Could not read saved routes: <error>` | label | List read failed. | read error | neutron_resume.py:297 |
| SRD-07 | `Saved routes are unavailable:\n<reason>` (reason `store not initialised` if none) | label | Store could not be opened at startup; dialog shows only this. | store unavailable | neutron_resume.py:283-287 |
| SRD-08 | `This route no longer exists.` / `Resume failed: <error>` / `Delete failed: <error>` | label appended at the bottom of the body | Action errors. Not cleared by later actions except a refresh (Delete refreshes first, then adds its message). | after a failed action | neutron_resume.py:315-333 |

Resume-choice dialog (`CTkToplevel`, modal, title `Resume neutron route`, shown only when the
commander is not on the saved route; `neutron_resume.py:228-254`):

| ID | Label | Type | What it does | Source |
|---|---|---|---|---|
| RES-01 | `You are in <current>, which is not on this route.` + `Back to the last waypoint: '<target>' is copied to the clipboard; set it as your target in game.` + `New route from here: plot from <current> to <destination> (<range> ly, <boost>); it replaces this saved route.` | label (wrap 420) | Explains both options. | :236-240 |
| RES-02 | `Back to last waypoint` | button | Copies the saved current waypoint (info label `📋 Auto-copied: <system>`). | :249 |
| RES-03 | `New route from here` | button | Fills NEU-03 with current system, NEU-05 with the saved destination, NEU-07 with the saved range, NEU-11 with the saved boost and starts a plot; the new route replaces the saved row's waypoints. | :250; resume_route :189-195 |
| RES-04 | `Cancel` | button | Closes; the saved route stays loaded in the tab. | :251 |

### Inputs and validation

- **From** (NEU-03): free text, trimmed. Blank: current system from the journal monitor, else read
  from the newest journal file (`neutron_manager.py:248-272`); else the last visited system of the
  Route Tracking route. There is an unreachable branch for "first unvisited" (see Problems). Still
  blank: dialog `Error` / `Please enter both From and To systems`.
- **To** (NEU-05): free text, trimmed. Blank: first unvisited system of the Route Tracking route;
  else the same error dialog.
- **Same system**: case-insensitive equality of From and To is refused by the router with
  `Source and destination cannot be the same system` (shown as an error, see States).
- **Range** (NEU-07): must parse as a number greater than 10, unit LY. Else dialog `Error` /
  `Please enter a jump range greater than 10 LY`. No upper limit in the tab. Default: the Route
  Optimization range field, which is persisted in `settings.json` as `ship_jump_range`
  (`app.py:2338-2346`); the Neutron field itself is not persisted.
- **FSD Boost** (NEU-11): `x4` (multiplier 4) or `x6` (multiplier 6). Not persisted.
- **Not exposed in the UI**: Spansh `efficiency` is fixed to 60 (`neutron.py:40`).
- **Request sent to Spansh** `POST https://spansh.co.uk/api/route` with `efficiency=60`,
  `range`, `from`, `to`, `supercharge_multiplier` (4 or 6); timeout 30 s per request, up to 2
  retries on 502/503/504, timeout or connection error (waits 2 s, then 5 s); result polling
  every 1 to 3 s, up to 180 s (`neutron.py:38-55`; `ext/spansh.py:57-131`; `ext/api.py:120-165`).
- **Persisted between sessions**: (a) `neutron_route.json` (waypoints, statuses, current index) in
  the current backup folder, written after every calculation and every navigation change when a
  backup folder is set (`neutron.py:172-189`); restored only when a route backup is loaded through
  the Route Optimization tab's "Load Backup" (`file_operations.py:283`). (b) SQLite store
  `edmrn.db` in the app data directory: every calculated or resumed route is auto-saved with its
  progress by a 2 s timer (`neutron_resume.py:62-165`, `ext/store.py:284-286`); this is what
  SRD-* lists. From/To/Boost text is not persisted.

### Data shown

- **Range text (NEU-08)**, from the ship tracker (`ship_panel.py:31-45`); numbers floored to 0.1:
  - no ship data: `Waiting for ship data from the journal`;
  - drive not in the table but the game's max known: `Max 58.7 LY (drive not recognised, laden range unknown)`;
  - normal: `Laden 49.3 LY · now 51.0 · max 58.7 · neutron ×4 197.2`. The `now` part appears only
    when the tank level is known; `neutron ×N` is ×4, or ×6 for the Mk II drive, times the laden
    range. "Laden" = current cargo, full main tank. "now" = current cargo and current fuel.
  - While Auto is on, NEU-07 holds the laden range (e.g. `49.3`), else the max range.
- **Waypoint list (NEU-27)**, one row per waypoint, in route order (realistic route: 5 to 150
  rows, long cross-galaxy routes up to ~1000):
  `<n>.⚡ <system> | <jumps> jump` for a neutron-star waypoint, `<n>.☀️ <system> | <jumps> jump` for
  a normal one. Example: `3.⚡ Eorl Auscs YE-N d6-5 | 1 jump`. The word is always the singular
  "jump" even for 2 or more. Row background: green (`#4CAF50`) for visited, orange for status
  `skipped` (the Neutron code never sets it), otherwise transparent. The current waypoint is not
  marked in the list; it is shown in NEU-14 only. The first row is Spansh's start system.
- **Statistics (NEU-23)**: `📊 Neutron Statistics | Distance: 1234.56 LY | Jumps: 40 | Neutrons: 22 | Waypoints: 23`
  (Distance = sum of per-waypoint distances, 2 decimals; Jumps = sum of per-waypoint jump counts;
  Neutrons = waypoints of type Neutron; Waypoints = row count).
- **Progress (NEU-24)**: `🎯 Progress Status | Current: 4/23 | Remaining: 19 waypoints`.
- **Per-waypoint fields in memory** (not all displayed): `system`, `type` (`Neutron`/`Normal`),
  `distance` (LY), `jumps`, `status` (`visited`/`unvisited`).

### States

- **Empty**: NEU-12 `Optimize & Track`, NEU-14 `No route calculated`, NEU-17 initial text, NEU-23 and
  NEU-24 initial text, list blank.
- **Running**: NEU-12 `Calculating...` and disabled; NEU-17 shows, each prefixed `🔄 `:
  `Connecting to Spansh API...`, then `Waiting for route calculation...`, then
  `Calculating... (<s> s)` every 1 to 3 s, and `Spansh busy, retrying (<n>/2)...` on a retry.
  A second concurrent start is refused: `Route calculation already in progress`.
- **Populated**: NEU-17 `✅ Route calculated: <jumps> jumps (<n> neutron), <distance> LY`, which
  is immediately replaced by `📋 Auto-copied: <system>` (the auto-copy runs after it) unless a note
  exists; with a note NEU-17 is set a second time to the `✅ ...` line plus `\n<note>`. Notes
  (shown only on the info label and in the Log tab):
  - substituted start: `Start system '<name>' is not known to Spansh yet - plotted from nearest known system '<other>' (<d> ly away).` The From field is then set to `<other>`;
  - supercharged start: `Supercharged start (×4): skipped 2 waypoints, first jump 123.4 LY`
    (singular `waypoint` for 1). Both notes can appear, joined by a newline.
  NEU-12 returns to `🚀 Calculate Neutron Route`; NEU-13/15 enabled by position; the overlay is
  started when the setting "auto start overlay" is on; the journal monitor is started if absent.
- **Error**: NEU-17 `❌ Error: <message>` plus dialog `Neutron Route Error` /
  `Failed to calculate route:\n<message>`. Messages (`ext/api.py:34-50`):
  `Spansh rejected the request: <detail>` (e.g. `could not find starting system` when the start is
  unknown and the nearest-system fallback also failed) or `Spansh rejected the request (HTTP <n>).`;
  `Spansh is overloaded (HTTP 503). Try again in a minute.`;
  `Spansh did not answer within 30 s.` / `Spansh did not answer within 180 s; the job may still finish on Spansh's website.`;
  `No connection to Spansh.`; `Unexpected error talking to Spansh.` (with ` (HTTP <n>).` form when
  a status is known).
- **Disabled**: NEU-12 while running; NEU-13/NEU-15 at the ends of the route.
- **Saved routes store unavailable**: SRD-07 (see table).

### Automatic behaviour

- **Journal jump** (`FSDJump`/`Location` through `app._handle_system_jump`, `app.py:1602-1604`):
  only while the tab "Neutron Highway" is the active tab and a route exists, the jumped-to system is
  matched to a waypoint (case, space, `-`, `_` ignored). If the match is at or after the current
  index, every waypoint from the current one to the match is marked visited, the index moves to the
  match, the list, NEU-14, NEU-13/15, NEU-24 are refreshed, the route is saved, and the system
  after the current one is copied to the clipboard (`neutron_manager.py:225-247`). On other tabs
  the jump is ignored by the neutron route. Repeated events for the same system are skipped.
- **Every navigation refresh** copies the waypoint after the current one to the clipboard (log
  line only, no label), refreshes the overlay when it is enabled and the tab is active, and writes
  `neutron_route.json` (`neutron_manager.py:156-195`).
- **Range**: each ship tracker change (Loadout, Status.json fuel/cargo, FSDJump) refreshes NEU-08;
  with Auto on it also rewrites NEU-07 and, when the loadout changed, NEU-11. On startup a
  background replay of the newest journals (up to 5 files) fills the tracker.
- **Autosave** every 2 s to the SQLite store (new route: insert; changed index or statuses:
  update). A replot started from RES-03 replaces the waypoints of the saved row instead of adding
  one.
- **Tab change** to Neutron Highway with an existing route starts the journal monitor if it is not
  running (`app.py:641-644`).
- **Calculation done**: copies the current waypoint to the clipboard, starts the journal monitor
  and (optionally) the overlay.

### User flows

1. Plot: type or pick From (or leave blank for the current system), type or pick To, check or
   edit the range and the boost (Auto does this from the ship), click NEU-12, watch NEU-17, read
   the list, the statistics and the note.
2. Travel by hand: paste the clipboard into the in-game galaxy map; after the jump click `>` (marks
   the waypoint visited, copies the next target). `<` steps back; the name button re-copies the
   current system.
3. Travel automatically: stay on this tab; each journal jump advances the route and the clipboard
   (see Automatic behaviour).
4. Resume: click `Saved routes`, click `Resume` on a row. Decision (`neutron_resume.py:29-39`):
   current system unknown or on the route one step behind the saved index: continue silently;
   current system on the route at or ahead of the saved index: waypoints up to it are marked
   visited and the route continues there; otherwise RES-* appears. Back to last waypoint copies
   the saved target; New route from here replots and replaces the saved row.
5. Delete a saved route: `Saved routes`, `Delete` (immediate).
6. Find a system by coordinates: enter X, Y, Z, click `FIND`. Dialogs: `Input Error` /
   `Please enter X, Y, and Z coordinates` or `Coordinates must be numbers`; then a confirm dialog
   `Nearest System` with `Nearest System Found:\n\n🌟 <name>\n📍 Coordinates: X=<x>, Y=<y>, Z=<z>\n📏 Distance: <d> LY\n\nCopy system name to use in route planner?`;
   yes copies the name to the clipboard (Log tab entry `✓ '<name>' copied to clipboard (Distance: <d> LY)`).
   No result: `No Results` / `No systems found near those coordinates`; failure: `API Error` with
   the Spansh message, or `Error` / `Unexpected error talking to Spansh.`.

### Observed functional problems

- `calculate_neutron_route` builds a multi-line `route_text` and never displays it
  (`neutron_manager.py:63-75`); the from-fallback "first unvisited system" sits in an `else` of
  `if route_data:` and can never find anything (`neutron_manager.py:21-32`).
- The calculate button is created with the text `Optimize & Track` but is reset to
  `🚀 Calculate Neutron Route` after the first run; the info label's initial text says "click
  Calculate".
- Prev/Next buttons are created enabled and do nothing until a route exists
  (`ui_components.py:308-329`, no initial `state="disabled"`; the Galaxy tab does disable them).
- Clipboard semantics differ by path: after `>` or Calculate the clipboard holds the current
  waypoint; after `<` and after an automatic jump it holds the waypoint after the current one
  (`neutron_manager.py:156-188`), without any label feedback.
- Automatic advance works only while the Neutron tab is the active tab (`app.py:1602-1607`).
- The list does not mark the current waypoint; only visited rows are coloured.
- The route's start system is the first row and first "waypoint"; the progress counts it.
- NEU-07 is overwritten by every write to the route tab's range field (one-way sync); a value typed
  into NEU-07 is lost on the next tracker update while Auto is on, although typing there turns
  Auto off for both panels.
- The row text always says `jump`, never `jumps`.
- Dialog SRD-08 messages accumulate at the bottom of the body until the next refresh.
- The worker thread has no guard around the final callback; if it raised, NEU-12 would stay
  `Calculating...` (the router itself catches all exceptions).
- Spansh ignores nothing here: all sent parameters are used by the neutron endpoint, but the plot
  does not know the ship, so jumps may exceed the ship's real range (see knowledge note).

---

## Galaxy Plotter tab

- **Purpose**: plot a long-distance route from a source to a destination and walk through it. Fact
  (`docs/knowledge/upstream-baseline.md`, "Neutron Highway and Spansh"): the tab calls Spansh's
  neutron endpoint `POST https://spansh.co.uk/api/route` (`galaxy_plotter.py:18`), which ignores
  `ship_build`, `cargo`, `reserve_fuel`, `already_supercharged`, `use_injections`,
  `exclude_secondary_stars` and `refuel_every_scoopable`. The result is a plain neutron route; only
  `from`, `to`, `range`, `efficiency` and `supercharge_multiplier` have an effect. The form still
  shows all inputs.

- **Layout today**: heading row with Help; below it one scrollable area with two columns.

```
[🌌 Galaxy Plotter                                                    [Help]]
+------------------------------------------+---------------------------------+
| LEFT                                     | RIGHT: Galaxy Systems:          |
| [Source System:]   [Destination System:  |  +---------------------------+  |
|  [entry]           [⇄ Reverse]  [entry]] |  | scrollable waypoint list  |  |
| Ship Build URL (EDSY or Coriolis):       |  +---------------------------+  |
|  [saved-builds combo][Save][Delete]      |  [<] [ current waypoint ] [>]   |
|  [multi-line build textbox]              |  Galaxy Statistics line          |
|  hint text                               |  Progress line                   |
| [Ship Jump Range][FSD Boost]             |  Data provided by Spansh.co.uk   |
| [Cargo (tons)] [Reserve Fuel (tons)]     |  Console                         |
| [checks left]   [checks right]           |  [console text]  [Show full log] |
| [Routing Algorithm v]                    |                                  |
| [ Optimize & Track ] [💾 Export to CSV]  |                                  |
+------------------------------------------+---------------------------------+
```

### Elements

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| GAL-01 | `🌌 Galaxy Plotter` | label | Heading. | always | ui_components.py:425 |
| GAL-02 | `Help` | button | Opens `GALAXY_PLOTTER_GUIDE.md` (repo root) through the OS default handler (`webbrowser.open` of the file path). | always | ui_components.py:432-441; app.py:1744 |
| GAL-03 | `Source System:` | label | Caption of GAL-04. | always | ui_components.py:461 |
| GAL-04 | placeholder `Type 3+ chars for suggestions...` | entry with autocomplete | Source. Blank at plot time: current system from the running journal monitor, if any. | always | ui_components.py:464 |
| GAL-05 | `Destination System:` | label | Caption of GAL-07. | always | ui_components.py:478 |
| GAL-06 | `⇄ Reverse` | button | Swaps the contents of GAL-04 and GAL-07; log line `Galaxy Plotter: Route reversed`. | always | ui_components.py:481-488; app.py:2658 |
| GAL-07 | placeholder `Type 3+ chars for suggestions...` | entry with autocomplete | Destination. Blank at plot time: `last_known_system` (the last system the journal reported) if the journal monitor runs. | always | ui_components.py:490 |
| GAL-08 | `Ship Build URL (EDSY or Coriolis):` and hint `📋 Paste the share link` | labels | Caption of the build area. | always | ui_components.py:504-509 |
| GAL-09 | (empty, readonly) | dropdown (CTkComboBox, width 180) | Names of saved loadout strings (SLEF store, file `~/.edmrn_slef_store.json`). Picking a name replaces the text of GAL-12 with its code. | always | ui_components.py:511-530; slef_store.py |
| GAL-10 | `Save` | button | If GAL-12 is non-empty, asks `Save SLEF` / `Enter a name for this SLEF code:` (simple input dialog), stores name and code, selects the new name. Empty text or empty/cancelled name: nothing happens. | always | ui_components.py:531-540, 608 |
| GAL-11 | `Delete` | button | With a name selected, opens a confirm window `Delete SLEF` / `Are you sure you want to delete the SLEF entry '<name>'?` with buttons `Delete` and `Cancel`; Delete removes the entry, clears the selection and clears GAL-12. No selection: nothing. | always | ui_components.py:541-613 |
| GAL-12 | (multi-line text, height 60, wrap word) | textbox | Ship build: pasted SLEF text or an EDSY/Coriolis URL. Sent to Spansh as `ship_build` (see fact above); strings longer than 2000 characters are reduced to essential modules first (`galaxy_plotter.py:42-43, 76-128`). | always | ui_components.py:614 |
| GAL-13 | `💡 Paste your SLEF (Spansh Loadout Exchange Format) string here.` / `Direct SLEF is recommended over EDSY/Coriolis URL.` | label | Help text. | always | ui_components.py:616-624 |
| GAL-14 | `Ship Jump Range (LY):` | label | Caption of GAL-15. | always | ui_components.py:630 |
| GAL-15 | (empty initially) | entry (`galaxy_jump_range_var`) | Jump range in LY. Not linked to the Auto switch and has no range text. Blank at plot time: copied from the Route Optimization range field and written back here. | always | ui_components.py:632-633 |
| GAL-16 | `FSD Boost:` | label | Caption of GAL-17. | always | ui_components.py:635 |
| GAL-17 | `x4 (Normal)` / `x6 (Caspian)` (default `x4 (Normal)`) | dropdown | Text containing `x6` gives supercharge multiplier 6, else 4. | always | ui_components.py:637-645 |
| GAL-18 | `Cargo (tons):` | label | Caption of GAL-19. | always | ui_components.py:658 |
| GAL-19 | default `0` | entry | Cargo tons, whole number. Sent to Spansh; no effect there. | always | ui_components.py:660 |
| GAL-20 | `Reserve Fuel (tons):` | label | Caption of GAL-21. | always | ui_components.py:663 |
| GAL-21 | default `0` | entry | Reserve fuel tons (decimal). Sent; no effect there. | always | ui_components.py:665 |
| GAL-22 | `Use Neutron Supercharge` | checkbox (default on) | Variable `galaxy_use_supercharge` is never read; no effect. | always | ui_components.py:675-677 |
| GAL-23 | `Use FSD Injections` | checkbox (default off) | `use_injections` sent; no effect at Spansh. | always | ui_components.py:679-681 |
| GAL-24 | `Refuel Every Scoopable` | checkbox (default on) | `refuel_every_scoopable` sent; no effect at Spansh. | always | ui_components.py:683-685 |
| GAL-25 | `Already Supercharged` | checkbox (default off) | `already_supercharged` sent; no effect at Spansh. | always | ui_components.py:687-689 |
| GAL-26 | `Exclude Secondary Stars` | checkbox (default off) | `exclude_secondary_stars` sent; no effect at Spansh. | always | ui_components.py:691-693 |
| GAL-27 | `Routing Algorithm:` | label | Caption of GAL-28. | always | ui_components.py:698 |
| GAL-28 | `optimistic` / `efficient` (default `optimistic`) | dropdown | Sets Spansh `efficiency`: 60 for `optimistic`, 80 for `efficient`. This is the only ship-independent option that reaches the plotter. | always | ui_components.py:700-707; galaxy_plotter.py:156 |
| GAL-29 | `Optimize & Track` (reset to `🚀 Calculate Exact Route` only after an exception path) | button (width 250) | Validates and starts the plot (see Flows). | Disabled while a plot runs; re-enabled when the result or error arrives. | ui_components.py:712-719; app.py:2667-2740 |
| GAL-30 | `💾 Export to CSV` | button | Save-as dialog (`Export Galaxy Route`, default `.csv`, filter `CSV files`/`All files`) then writes the last route. | Disabled until the first successful plot; stays enabled afterwards. | ui_components.py:721-729; app.py:2792-2811 |
| GAL-31 | `Galaxy Systems:` | label | Heading of the list. | always | ui_components.py:736 |
| GAL-32 | (list) | scrollable frame, one label per waypoint | The route, see "Data shown". Rows have cursor `hand2` but no click handler. | always | ui_components.py:740-748; app.py:2958-3014 |
| GAL-33 | `<` | button | Previous waypoint (index minus 1, no status change). | Disabled at start and when index is 0. | ui_components.py:754; app.py:2813 |
| GAL-34 | `No route calculated` at start; afterwards the current waypoint's system | button | Copies the current waypoint's name to the clipboard (Log tab line only). No route: warning dialog `Warning` / `No galaxy route calculated.` | always | ui_components.py:762; app.py:2845 |
| GAL-35 | `>` | button | Marks the current waypoint visited, advances, copies the new current system. | Disabled at start and on the last waypoint. | ui_components.py:771; app.py:2826 |
| GAL-36 | initial `📊 Galaxy Statistics \| No route calculated` | label | Route totals. | always | ui_components.py:787; app.py:2932 |
| GAL-37 | initial `🎯 Progress Status \| Ready to calculate` | label | Position. | always | ui_components.py:793 |
| GAL-38 | `Data provided by Spansh.co.uk` | label | Attribution. | always | ui_components.py:799 |
| GAL-39 | `Console` | label | Heading of the console. | always | ui_components.py:810 |
| GAL-40 | initial text (see States) | read-only textbox (height 140, wrap word) | Instructions, progress lines, route summary, notes and errors. | always | ui_components.py:813-816 |
| GAL-41 | `Show full log` / `Hide log` | button | Toggles the console height between 140 and 320 px. | always | ui_components.py:818-839 |

### Inputs and validation

- **Source / Destination**: free text, trimmed; fallbacks described in the table. Either still
  blank: dialog `Missing Input` / `Please enter both source and destination systems.` No check that
  they differ or exist; Spansh answers (see States).
- **Jump range**: blank means the Route Optimization range; text that is not a number silently
  becomes `50.0`. No lower/upper limit. Not persisted.
- **Cargo**: `int(...)`, blank means 0; a decimal such as `12.5` fails. **Reserve fuel**:
  `float(...)`, blank means 0. Either failing: dialog `Invalid Input` /
  `Cargo and Reserve Fuel must be numeric values.`
- **Ship build**: any text, trimmed; blank is allowed.
- **Request**: `POST /api/route` with `from`, `to`, `range`, `efficiency`,
  `supercharge_multiplier`, and the non-empty/non-false of `ship_build`, `cargo` (non-zero),
  `reserve_fuel` (non-zero), `already_supercharged`, `use_injections`, `exclude_secondary_stars`,
  `refuel_every_scoopable` (`galaxy_plotter.py:45-59`). Timeout 60 s per request, up to 2 retries;
  start-system fallback as for the Neutron tab (nearest known system, uses the journal coordinates
  when the source is the current system); polling up to 300 s.
- **Persisted between sessions**: the saved SLEF builds (`~/.edmrn_slef_store.json`, outside the app
  data folder); the route and index in `galaxy_route.json` of the current backup folder after each
  plot/navigation (restored via "Load Backup" on the Route Optimization tab,
  `file_operations.py:288-300`). Nothing else on this tab is persisted. Galaxy routes are not in
  the Saved routes store.

### Data shown

- **Waypoint list (GAL-32)**: row text is `<n>. ⚡ <system>` (neutron-star waypoint),
  `<n>. ⛽ <system>` (refuel waypoint), else `<n>. <system> (<k> jumps)` when k > 1, else
  `<n>. <system>`. Example: `7. ⚡ Phaa Hypa LL-D d12-33`. Visited rows get a green (`#4CAF50`)
  background, status `skipped` orange (never set by the Galaxy code); the current waypoint is not
  marked. Realistic size 5 to 150 rows, up to ~1000. The Spansh neutron plot normally contains no
  `refuel` flag, so `⛽` rows are not expected from this endpoint (UNVERIFIED against live data).
- **Console summary** (after a plot): `=== ROUTE SUMMARY ===` then
  `🚀 <n> jumps | ⚡ <n> neutron boosts | ⛽ <n> refuels | 📊 <distance> LY` (distance 2 decimals),
  a blank line, then the note (start substitution) if any.
- **Statistics (GAL-36)**: `📊 Galaxy Statistics | Distance: 1234.56 LY | Jumps: 40 | Neutrons: 22 | Refuels: 0`
  (Jumps here is the number of rows, not the sum of jump counts as on the Neutron tab).
- **Progress (GAL-37)**: `🎯 Progress Status | Current: 4/23 | Remaining: 19 waypoints`.
- **CSV columns (GAL-30)**: `System`, `Distance` (2 decimals), `Jumps`, `Neutron` (`Yes`/`No`),
  `Refuel` (`Yes`/`No`), `Coordinates` (`x, y, z` with 2 decimals).

### States

- **Empty**: GAL-40 contains `No route calculated yet.` + blank line + `Steps:` and six numbered
  steps: `1. Enter source and destination systems`, `2. Paste your ship build from Coriolis.io or EDSY.org`,
  `3. Configure options (cargo, fuel, neutron boost, etc.)`, `4. Click 'Calculate Exact Route' button`,
  `5. Wait for route calculation (may take 1-2 minutes)`, `6. Export route to CSV if needed`.
  Step 4 names a button label that does not exist (the button reads `Optimize & Track`). GAL-33/35
  disabled, GAL-30 disabled, GAL-34 `No route calculated`.
- **Running**: GAL-29 disabled (text unchanged); GAL-40 shows `Submitting route to Spansh API...`,
  `⏳ Calculating route...`, then appended lines: `Submitting route calculation to Spansh API...`,
  `Spansh busy, retrying (<n>/2)...`, `Waiting for route calculation...\nJob ID: <id>`, repeated
  `Calculating route...\nElapsed: <s>s`. Dialog-free.
- **Populated**: GAL-40 shows the summary (see Data shown); the list, GAL-33/35, GAL-34, GAL-36/37
  are filled; GAL-30 enabled; first waypoint copied to the clipboard; route saved; journal monitor
  and (optionally) overlay started.
- **Error / empty result**: GAL-29 re-enabled; GAL-40 shows `❌ <message>` (messages as in the
  Neutron "Error" state with the service `Spansh`, timeout text
  `Spansh did not answer within 300 s; the job may still finish on Spansh's website.`) or
  `No route data returned.`; GAL-34 `No route calculated`, GAL-33/35 disabled. The previous route
  data and list are not cleared (see Problems). An exception in the worker thread appends
  `\n❌ Error: <exception text>` and sets the button text to `🚀 Calculate Exact Route`.
- **Dialogs**: `Missing Input`, `Invalid Input` (above); `No Route` / `Please calculate a route first before exporting.`; `Export Successful` /
  `Route exported to:\n<path>`; `Export Failed` / `Failed to export route to CSV.`; `Error` /
  `Failed to copy to clipboard:\n<error>` only when both clipboard methods fail on a manual copy.

### Automatic behaviour

- **Journal jump** while the Galaxy Plotter tab is active and a route exists
  (`app.py:1605-1607`, `2488-2531`): the matching waypoint (normalized name) is marked visited, the
  index is set to it (earlier waypoints are not marked), list/navigation/statistics refresh, the
  route is saved, and the next not-yet-visited waypoint is copied to the clipboard.
- **Every navigation refresh** copies the next waypoint (idx + 1) to the clipboard (Log tab line
  only), refreshes the overlay when enabled and the tab is active, and updates the progress line
  `🎯 Progress Status | Current: ...`.
- **Lazy build**: the tab is built on first visit or by the preload timer.
- **After a plot**: auto-copy of the first waypoint, journal monitor and overlay start as above.

### User flows

1. Plot: enter source and destination (or leave blank for current system / last known system when
   the journal monitor runs), optionally paste or pick a build, set range and algorithm, click
   GAL-29, read the console and the list.
2. Reverse a route: GAL-06.
3. Save/reuse a build: paste in GAL-12, `Save`, name it; later pick it in GAL-09.
4. Travel: copy with GAL-34 or step with `<` / `>`, or rely on the journal auto-advance (tab must
   be active).
5. Export: GAL-30, choose a file name.
6. Read the guide: `Help`.

### Observed functional problems

- All ship-dependent inputs (GAL-12, GAL-19, GAL-21, GAL-23 to GAL-26) have no effect on the
  result because of the endpoint; GAL-22 is not read at all. GAL-17 and GAL-28 do take effect.
- The tab duplicates the plotting and navigation code in `edmrn/galaxy_handler.py` (unwired) and
  `edmrn/app.py` (live).
- The empty-console instructions name a button `Calculate Exact Route` and the button reads
  `Optimize & Track`.
- On an error result the old `galaxy_route_waypoints` list and statistics stay in memory and in
  the list while GAL-34 says `No route calculated`; GAL-30 can still export the previous route.
- Waypoint indices after an error reply: navigation buttons are disabled but journal auto-advance
  still works on the stale route.
- Invalid jump range text silently becomes 50.0.
- Selecting the already-selected name in GAL-09 does not reload the code (variable trace fires only
  on change); the Delete confirmation is a plain `tk` window.
- GAL-29 text is not reset to its original after a successful plot in the live path (it is never
  changed in the normal path).
- The plot runs in a background thread; the progress callback writes to the textbox from that
  thread through `root.after`, fine; no cancel control exists.

---

## System Info tab

- **Purpose**: look up one system and see its basic data, bodies, stations and Galactic
  Mapping Project (GMP) history from EDSM and EDAstro; it follows the commander's current system
  and shows exobiology progress for it.

- **Layout today**: search row, status line, inner tab strip with four inner tabs.

```
[ system entry (autocomplete) ][🔍]
status line
[Overview][Bodies][Stations][System History]
Overview:  +-------------------+ +---------------+ +-------------------+
           | <System name>     | | Statistics    | | Exobiology Scans  |
           | Allegiance: ...   | | Planet Count  | | - / species rows  |
           | ... 6 fields      | | ... 6 fields  | |                   |
           | [View on EDSM]    | +---------------+ +-------------------+
           | trivia text       |
           +-------------------+
Bodies:    [Filter bodies...]   table (6 columns)
Stations:  [Filter stations...] table (5 columns)
History:   read-only text or "No System History data."
```

### Elements

| ID | Label (exact UI text) | Type | What it does / shows | Enabled/visible when | Source |
|---|---|---|---|---|---|
| SYS-01 | placeholder `Enter system name (e.g., Sol, Achenar)` | entry with autocomplete | System to look up. Typing (any key) sets `_auto_system_info_enabled = False`. Picking a suggestion fetches at once. Return with no dropdown does nothing. | always | system_info_section.py:264-287; app.py:1165-1168 |
| SYS-02 | `🔍` | button (width 40) | Fetch for the text in SYS-01; blank: last known system; still blank: status `Please enter a system name`. | always | system_info_section.py:289; app.py:1170-1193 |
| SYS-03 | initial `Enter a system name and click Fetch Data` | label | Status/messages line (colour: green `#4CAF50` ok, red `#FF6B6B` problem). No control is labelled "Fetch Data". | always | system_info_section.py:293 |
| SYS-04 | inner tab `Overview` | tab | Three cards: SYS-05..SYS-19. | always | :297 |
| SYS-05 | system name, initial `-` | label (title) | Name returned by EDSM. | Overview | :313 |
| SYS-06 | `Allegiance:` | label + value | See Data shown. | Overview | :318-331 |
| SYS-07 | `Population:` | label + value | | Overview | |
| SYS-08 | `Security:` | label + value | | Overview | |
| SYS-09 | `Permit:` | label + value | | Overview | |
| SYS-10 | `Controlling Faction:` | label + value | | Overview | |
| SYS-11 | `Star Type:` | label + value | | Overview | |
| SYS-12 | `View on EDSM` | button | Opens the system's EDSM page in the browser. Does nothing before the first successful fetch. | Overview | :333-337 |
| SYS-13 | (italic text, initially empty) | label | Trivia lines, see Data shown. | Overview | :338-342; :855-884 |
| SYS-14 | `Statistics` | heading | Card heading. | Overview | :346 |
| SYS-15 | `Planet Count:` | label + value | | Overview | :351-364 |
| SYS-16 | `Moon Count:` | label + value | | Overview | |
| SYS-17 | `Landable Bodies:` | label + value | | Overview | |
| SYS-18 | `Atmosphere Bodies:` | label + value | | Overview | |
| SYS-19 | `Stations:` and `Permit Required:` | label + value (2 rows) | | Overview | |
| SYS-20 | `Exobiology Scans`, value initially `-` | card | Species progress of the current system, see Data shown. | Overview | :366-371; exobiology.py:199-455 |
| SYS-21 | inner tab `Bodies` | tab | Filter and table. | always | :298 |
| SYS-22 | placeholder `Filter bodies...` | entry | Live case-insensitive substring filter over all six columns. | Bodies | :378 |
| SYS-23 | columns `Name`, `Type`, `Gravity`, `Landable`, `Atmosphere`, `Subtype` | table (tksheet) | Bodies. Double-click opens `Body Details`; right-click offers `Copy selected`. | Bodies | :380-464 |
| SYS-24 | `Body Details` window | popup (modal) | Six `Key:` / value rows (`Name`, `Type`, `Gravity`, `Landable`, `Atmosphere`, `Subtype`). | double-click a row | :414-444 |
| SYS-25 | `Copy selected` | context-menu item | Copies selected rows as tab-separated text to the clipboard. | right-click in a table | :457-464 |
| SYS-26 | inner tab `Stations` | tab | Filter and table. | always | :299 |
| SYS-27 | placeholder `Filter stations...` | entry | Live substring filter over the five columns. | Stations | :471 |
| SYS-28 | columns `Name`, `Type`, `Planet`, `Distance (ls)`, `Services` | table | Stations. Double-click opens `Station Details`; right-click `Copy selected`. | Stations | :473-569 |
| SYS-29 | `Station Details` window | popup (modal) | `Name:`, `Type:`, `Planet:`, `Distance (ls):`, `Services:` (one per line). | double-click a row | :511-548 |
| SYS-30 | inner tab `System History` | tab | GMP text, see Data shown. Rebuilt on each fetch. | always | :300, 774-853 |
| SYS-31 | `No System History data.` | label | Empty state of SYS-30. | no GMP text | :794, 809 |
| SYS-32 | (read-only text, Consolas 14, wrap word) | textbox | GMP entry text, see Data shown. | GMP text present | :798-806 |

The tksheet tables also enable cell editing, cut/paste/delete/undo, row and column drag and
column resize (`edmrn_sheet.py:41-60`); edits are cosmetic and lost on refresh.

The separate "Log" tab (`log_viewer.LogViewer`, parent `app.tab_log`) is created by this section
and updated with every fetch, jump and `Scan`/`SAASignalsFound`/`CodexEntry` event; it is out of
scope here.

### Inputs and validation

- **System name**: free text, trimmed; sent as `systemName` to EDSM. No local validation; EDSM
  decides (unknown: `System '<name>' is not known to EDSM.`).
- **Filters**: free text; empty shows all rows.
- **Persisted**: nothing from this tab. The exobiology card reads `visited_systems_history.json`
  in the app data folder (`system_info_section.py:221-223`; exobiology.py).
- **Data sources** (all `GET`, sequential in one background thread per fetch,
  `ext/system_info.py:52-89`):
  1. `https://www.edsm.net/api-v1/system` with `showInformation`, `showCoordinates`,
     `showPrimaryStar`, `showTraffic`, `showPermit`, `showId` (10 s, up to 2 retries). Failure or
     empty answer aborts the whole fetch.
  2. `https://www.edsm.net/api-system-v1/bodies`, `.../stations`, `.../factions` (10 s, no retry
     each). A failure keeps the rest and adds a note.
  3. If the EDSM answer has an integer `id64`:
     `https://edastro.com/gec/json/id64/<id64>` (15 s, no retry). HTTP 404 is silent (most systems
     have no entry); other failures add a note.
  Traffic data and the factions list are fetched but not shown, except that the factions list
  can supply the controlling faction.

### Data shown

- **Overview values** (`-` when missing): Allegiance (text, e.g. `Federation`); Population
  (integer with thousands commas, e.g. `22,780,919,531`; maximum ~40 billion); Security (text, e.g.
  `High`); Permit (`Yes`, `No`, a permit name, or `-`); Controlling Faction (name); Star Type (EDSM
  primary star type, e.g. `G (White-Yellow) Star`). Title: system name.
- **Statistics values**: Planet Count, Moon Count, Landable Bodies, Atmosphere Bodies are counted
  from the bodies list (planets by `type`/`isPlanet`, landable by `isLandable`, atmosphere by a
  non-empty `atmosphereType`); Moon Count counts only bodies whose `type` is `moon`
  (UNVERIFIED whether EDSM ever supplies that type; it may always show 0). Stations = number of
  stations; Permit Required = `Yes`/`No`/`-`. Computed on a short-lived background thread and
  applied on the Tk thread.
- **Trivia (SYS-13)**: for `Sol` three fixed lines (`Sol is the only system with Earth in-game.`,
  `Permit is obtained via Federation rank.`, `Sol hosts iconic stations in ED history.`); for
  `Cubeo` two fixed lines; otherwise any of `Permit required: <name or Yes>.`,
  `Population: <n>`, `Primary star type: <type>`, `<n> planets in this system.`,
  `Controlled by: <faction>`; if none: `<name> is a notable system in Elite Dangerous.`
- **Bodies table**: sorted stars first, then planets by orbital order, then others. Columns:
  Name (e.g. `Sol A 3`), Type (e.g. `Planet`, `Star`), Gravity (g, 2 decimals, `-` if unknown),
  Landable (`Yes`/`No`), Atmosphere (text, e.g. `Thin Argon`, `-`), Subtype (e.g.
  `High metal content world`). Realistic size 1 to 100 rows, up to ~250.
- **Stations table**: Name, Type (e.g. `Orbis Starport`), Planet (parent body or `-`), Distance
  (ls) (whole number with commas, e.g. `1,234`), Services (comma-joined `otherServices`, `-` if
  none). Realistic size 0 to 30.
- **System History**: EDAstro GEC text (name, `Type:`, `Region:`, summary, markdown description,
  `Link:` line) re-flowed to about 70 characters, quote blocks with a bar, image lines removed;
  realistic length 500 to 5000 characters. Dict/list form (not produced by the current fetch) would
  render cards with name, type, description (400 characters max), discoverer, coords, link, EDSM ID.
- **Exobiology Scans card**: empty `-`. Otherwise one row per species: status dot (green
  `Complete` at 3 samples, yellow `2/3 samples`, orange `1/3 samples`), species name, for up to 5
  species three sample circles (tooltips `Sample <n> scanned`/`not scanned`), for more than 5
  species the text `<species> [<n>/3]` instead; a `🔗` that opens the species' fandom wiki page;
  plus lines `[COMPLETED] <system> / <body>: <genus>` from the history file.

### States

- **Empty**: SYS-03 initial text; Overview values `-`; tables empty; SYS-31 shown.
- **Loading**: SYS-03 `Fetching data for <system>...` (green).
- **Populated**: SYS-03 `Data fetched for <system>` plus one extra line per note (colour green):
  `Bodies could not be loaded: <reason>`, `Stations could not be loaded: <reason>`,
  `Factions could not be loaded: <reason>`, `Galactic POI data could not be loaded: <reason>`
  (reason uses the service messages: `EDSM is overloaded (HTTP 503). Try again in a minute.`,
  `EDSM did not answer within 10 s.`, `No connection to EDSM.`, `EDAstro ...`).
- **Error** (red): `Please enter a system name`; `System '<name>' is not known to EDSM.`; the
  service failure text, e.g. `EDSM rejected the request: <detail>`, `No connection to EDSM.`,
  `Unexpected error talking to EDSM.`; `Error: Unexpected data format from EDSM (type: <type>)`;
  `Error updating UI: <error>`; `Error fetching data: <error>`. On error the previous content stays.
- **Disabled**: nothing is disabled.

### Automatic behaviour

- **Follow the current system**: every time the journal reports a system not handled before
  (`_handle_system_jump`, `app.py:1548-1630`), on whichever tab is active: SYS-01 is overwritten
  with the system name, `cmdr_location` is updated, the exobiology card is cleared for the new
  system, a fetch for that system starts, and the Log tab updates. This ignores the "user has typed"
  flag, so a name being typed can be overwritten.
- **At startup**: after the latest journal's location is read, if the user has not typed in SYS-01
  yet, SYS-01 is filled with that location and fetched (`app.py:1722-1731`).
- **Tab not built yet**: a jump while the tab does not exist stores a pending fetch and builds the
  tab (`app.py:1106-1110, 1172-1179`).
- **No refresh timer**: data is fetched once per trigger and never refreshed on its own.
- **Journal events** `Scan`, `SAASignalsFound`, `CodexEntry`, and on-foot/ship organic scan events
  update the Log tab and the exobiology card (`app.py:1555-1590`, `1634-1635`).
- **Concurrency**: each fetch runs in its own thread without cancelling an earlier one; the
  last to finish wins.

### User flows

1. Look up a system: type in SYS-01 (suggestions after 3 characters), pick one or click `🔍`; read
   Overview, then Bodies / Stations / System History; double-click a row for details; filter with
   the filter entries; right-click to copy rows.
2. Open EDSM: `View on EDSM`.
3. Follow the commander: do nothing; each jump replaces the content.
4. Check exobiology: Overview, `Exobiology Scans` card; click `🔗` to open the wiki.

### Observed functional problems

- The status text says "click Fetch Data" but no control has that label (the button is `🔍`).
- Return in SYS-01 without an open dropdown does not fetch.
- Auto-follow overwrites a name the user is typing; the `_auto_system_info_enabled` flag only
  guards the one startup fill.
- The detail popups read row values at indices 1..N of a row that has N values (0..N-1), so
  `Body Details` appears to show every field shifted by one (Name shows Type, Subtype shows `-`) and
  `Station Details` shows Services as `-` (`system_info_section.py:419-424`, `516-522`);
  UNVERIFIED at runtime (depends on tksheet's `get_row_data` indexing).
- `update_planetary_access` rebuilds the whole table and auto-resizes columns once per body row
  (`system_info_section.py:598-611`), on the Tk thread: blocking for large systems.
- `update_system_info` first writes `system_data['trivia']` (never present) and `update_trivia`
  then overwrites it.
- Traffic and factions are requested but not displayed; Moon Count may always be 0.
- Overlapping fetches can finish out of order and show an older system under a newer name.
- A failed bodies request leaves an empty Bodies table and relies only on the note in SYS-03.
- Table cells are editable in the UI although nothing reads them back.
