# Location Fill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `📍` button on system and coordinate fields fills them from the commander's current
location; plotter fields check the system at Spansh and fall back to the nearest known system.

**Architecture:** A pure check (`edmrn/ext/start_check.py`) decides what a plotter field should hold;
a widget module (`edmrn/ext/location_fill.py`) reads `app.location_tracker.state`, adds the buttons
inside the existing field widgets and runs the check on a daemon thread. Upstream builders get one
hook call each (ADR-0002).

**Tech Stack:** Python 3.13, customtkinter 6, pytest. Network only through `edmrn.ext.api.call`.

**Spec:** `docs/superpowers/specs/2026-11-17-location-fill-design.md`

## Global Constraints

- Journal data only from `app.location_tracker.state`; no journal file reads (spec L1, ADR-0001).
- Upstream files (`edmrn/ui_components.py`, `edmrn/custom_route.py`, `edmrn/system_info_section.py`)
  get hook lines only, stay CRLF, are never reformatted; edit them with the Edit tool, never `sed -i`
  (ADR-0002, HANDOVER traps). Check with `file <path>` that they still report CRLF.
- Every HTTP request goes through `edmrn.ext.api.call`; Tk is touched only on the Tk thread, results
  arrive through `app.root.after(0, ...)` (`docs/architecture/patterns.md`).
- No test uses the network; tests run with `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`.
- UI texts are English and exactly as the spec writes them (copied into the tasks below).
- Stage explicit paths only; never `git add -A`/`.`/`-u`.

## Review Focus

1. A second click, or typing, while a check is still running: the late result must not overwrite the
   field (Task 2, `test_stale_result_is_dropped`, `test_result_dropped_after_typing`).
2. The field widget destroyed before the result arrives (tab rebuilt on a theme restart): no
   exception reaches Tk (Task 2, `test_result_after_destroy_is_ignored`).
3. A field that already holds text: the fill replaces it, never appends (Task 2
   `test_fill_replaces_text`, Task 3 `test_coordinates_replace_text`).
4. Coordinates that are integers, negative zero or tiny negatives in the journal: written as `0`,
   never `-0` (Task 2 `test_format_coord`).
5. A long hint (long procedural names plus the nearest system) in the half-width Neutron frames: it
   wraps instead of widening the field (Task 2 `test_hint_wraps`).

---

### Task 1: Spansh check for plotter fields

**Files:**
- Modify: `edmrn/ext/api.py` (after `lookup_system`)
- Create: `edmrn/ext/start_check.py`
- Test: `tests/test_start_check.py`

**Interfaces:**
- Produces: `api.spansh_coords(name: str, *, call=call) -> Optional[tuple]` — Spansh system search,
  exact case-insensitive match (reuses `_spansh_coords`), `None` for an empty name or not found;
  service failures raise `ApiError`.
- Produces: `start_check.StartCheck` (frozen dataclass: `name: str` — what the field should hold,
  `hint: str`, `known: bool`) and
  `start_check.check_plot_system(system: str, coords: Optional[tuple], *, call=call) -> StartCheck`.
  Never raises: an `ApiError` or any other exception becomes the failure result.

Exact hints (`<d>` with one decimal; the failure sentence is `str(ApiFailure)`, which ends with a
period):

| Case | `name` | `known` | `hint` |
|---|---|---|---|
| Spansh knows `system` | system | True | `✓ '<system>' is known to Spansh.` |
| unknown, nearest is a different system | nearest | False | `⚠ '<system>' is not known to Spansh - using the nearest known system '<nearest>' (<d> ly away).` |
| unknown, nearest has the same name (casefold) | system | True | the known sentence |
| unknown, `coords is None` | system | False | `⚠ '<system>' is not known to Spansh, and the journal has no coordinates for it.` |
| unknown, `nearest_system` returns `None` | system | False | `⚠ '<system>' is not known to Spansh, and Spansh named no known system near it.` |
| `ApiError` in either request | system | False | `⚠ Could not check '<system>' at Spansh: <failure>` |
| any other exception | system | False | same, with `ApiFailure("Spansh", "unexpected")` (log it with `logger.exception`) |

- [ ] **Step 1: Write the failing tests** in `tests/test_start_check.py`. Fake `call` like
  `tests/test_api.py` does (a function returning objects with `status_code` and `json()`, routing by
  URL: `SPANSH_SEARCH_URL` → `{"results": [...]}`, `SPANSH_NEAREST_URL` →
  `{"system": {"name", "x", "y", "z", "distance"}}`). Tests:
  - `test_spansh_coords_exact_match` — results `[{"name": "Sol Sector", ...}, {"name": "SOL", "x": 0, "y": 0, "z": 0}]`, `spansh_coords("sol", call=fake) == (0.0, 0.0, 0.0)`; not in results → `None`; `spansh_coords("  ", call=fake) is None` without a request.
  - `test_spansh_coords_failure_raises` — 503 twice → `ApiError` with kind `overloaded` (pass a `call` wrapper with `sleep=lambda s: None` or fake raising `ApiError` directly).
  - `test_known_system` — `check_plot_system("Sol", (0,0,0), call=fake)` → `StartCheck("Sol", "✓ 'Sol' is known to Spansh.", True)`, and the nearest URL was never requested.
  - `test_unknown_uses_nearest` — search empty, nearest `Blae Phoe SS-B d751` at distance `10.6249` → name `Blae Phoe SS-B d751`, `known` False, hint `⚠ 'Blae Phoe XT-A e1459' is not known to Spansh - using the nearest known system 'Blae Phoe SS-B d751' (10.6 ly away).`; the nearest request carried `params={"x": 768.375, "y": -565.90625, "z": 14325.34375}`.
  - `test_nearest_is_same_system_counts_as_known` — nearest name `blae phoe xt-a E1459` → name is the original, `known` True, known sentence.
  - `test_unknown_without_coords` — `coords=None` → no nearest request, the no-coordinates sentence.
  - `test_nearest_answers_nothing` — nearest body `{}` → the "named no known system" sentence.
  - `test_search_failure` / `test_nearest_failure` — fake raises `ApiError(ApiFailure("Spansh", "overloaded", 502))` at the respective URL → hint `⚠ Could not check 'X' at Spansh: Spansh is overloaded (HTTP 502). Try again in a minute.`, name `X`.
  - `test_unexpected_exception` — fake raises `ValueError` → hint ends with `Unexpected error talking to Spansh.`

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_start_check.py` — Expected: FAIL (import errors).

- [ ] **Step 3: Implement** `spansh_coords` in `edmrn/ext/api.py` and `edmrn/ext/start_check.py`
  (module docstring names the spec, section L4). Import `call`, `nearest_system`, `spansh_coords`,
  `ApiError`, `ApiFailure` from `edmrn.ext.api` and pass `call=call` through.

- [ ] **Step 4: Run** the same command — Expected: PASS. Then `tests/test_api.py tests/test_spansh.py` — PASS.

- [ ] **Step 5: Commit**

```bash
git add edmrn/ext/api.py edmrn/ext/start_check.py tests/test_start_check.py
git commit -m "feat(location): Spansh check with nearest-known fallback for plotter fields"
```

### Task 2: Location button for system fields

**Files:**
- Create: `edmrn/ext/location_fill.py`
- Test: `tests/test_location_fill.py`

**Interfaces:**
- Consumes: `check_plot_system`, `StartCheck` (Task 1); `edmrn.ext.location.LocationState`;
  `edmrn.autocomplete_entry.AutocompleteEntry` (`.entry` CTkEntry, `.entry_var` StringVar, `.get()`,
  `.set()`; the entry is packed inside the widget's own frame).
- Produces (used by Tasks 3 and 4):
  - Constants: `UNKNOWN_LOCATION = "Current system unknown - no location in the journal yet."`,
    `NO_COORDINATES = "Current coordinates unknown - the journal has no position for '{system}' yet."`,
    `CHECKING = "Checking '{system}' at Spansh..."`,
    `SYSTEM_TOOLTIP = "Fill in your current system from the journal"`,
    `COORDS_TOOLTIP = "Fill in your current coordinates from the journal"`, `BUTTON_TEXT = "📍"`.
  - `current_location(app) -> Optional[LocationState]` — the tracker's state when it has a `system`,
    else `None`; never raises (missing tracker included).
  - `format_coord(value: float) -> str` — at most five decimals, trailing zeros and a trailing `.`
    stripped, `-0` becomes `0`.
  - `thread_runner(fn: Callable[[], None]) -> None` — runs `fn` on a daemon thread named
    `edmrn-location-check`.
  - `class Tooltip(widget, text)` — shows `text` in a borderless `tk.Toplevel` 500 ms after
    `<Enter>`, hides on `<Leave>`/`<ButtonPress>`; binds with `add="+"` (customtkinter buttons bind
    Enter/Leave themselves).
  - `class SystemFieldFill(app, field, *, check_spansh: bool, after_fill: Optional[Callable[[str], None]] = None, check=check_plot_system, runner=thread_runner)`
    with attributes `button` (CTkButton, text `BUTTON_TEXT`, width 32, `Tooltip(SYSTEM_TOOLTIP)`)
    and `hint` (CTkLabel, `wraplength=300`, `justify="left"`, `anchor="w"`), and method `fill()`
    (the button's command).

Layout inside the field (spec L5, L11): `field.entry.pack_forget()`; `field.columnconfigure(0, weight=1)`;
entry `grid(row=0, column=0, sticky="ew", padx=2, pady=2)`; button `grid(row=0, column=1, padx=(2, 2), pady=2)`;
hint `grid(row=1, column=0, columnspan=2, sticky="w", padx=4)` then `grid_remove()` (hidden while empty).
Hint colours: checking `gray60`, known `#4CAF50`, warning `#FFA500`.

`fill()` behaviour:
1. `current_location(app)` is `None` → show `UNKNOWN_LOCATION` (warning colour) about the field's
   current text; nothing else changes.
2. Else `field.set(state.system)`. Without `check_spansh`: hide the hint and call
   `after_fill(state.system)` if given; done.
3. With `check_spansh`: increment a generation counter, show `CHECKING` (checking colour) about
   `state.system`, and `runner(job)`; `job` calls `check(state.system, state.coords)` and hands the
   result to Tk with `app.root.after(0, ...)`.
4. On Tk: drop the result if a newer generation exists or `field.get().strip() != state.system`.
   Otherwise, when `result.name != state.system`, make `result.name` the hint's subject *before*
   `field.set(result.name)`; show `result.hint` (known colour if `result.known`, else warning). A
   `tk.TclError` (destroyed widget) is ignored.

The hint hides on every write to `field.entry_var` that leaves a text other than its subject
(`trace_add("write", ...)`).

- [ ] **Step 1: Write the failing tests** in `tests/test_location_fill.py` with a module-scoped
  `ctk.CTk()` root fixture as in `tests/test_ship_panel.py` (skip when Tk is unavailable), a fresh
  `AutocompleteEntry(root)` per test, an app `SimpleNamespace(root=ImmediateRoot(), location_tracker=SimpleNamespace(state=LocationState(system="Blae Phoe XT-A e1459", coords=(768.375, -565.90625, 14325.34375))))`
  and `runner=lambda fn: jobs.append(fn)` so each test runs the job when it chooses. Hint text is
  `fill.hint.cget("text")`, visibility `fill.hint.winfo_manager() == "grid"`.
  - `test_format_coord` — `768.375 → "768.375"`, `-565.90625 → "-565.90625"`, `0 → "0"`, `-0.0 → "0"`, `-0.000001 → "0"`, `19808.125 → "19808.125"`, `12.0 → "12"`.
  - `test_current_location` — state without system → `None`; app without `location_tracker` → `None`; with system → the state.
  - `test_layout` — after construction the entry is gridded at (0, 0), the button at (0, 1) with text `📍`, the hint hidden.
  - `test_unknown_location` — tracker state `LocationState()` → field unchanged, hint `UNKNOWN_LOCATION` visible, no job queued.
  - `test_fill_replaces_text` — field holds `Sol`; `check_spansh=False`; `fill()` → field `Blae Phoe XT-A e1459`, hint hidden, `after_fill` called once with that name.
  - `test_check_known` — `check` returns `StartCheck(name, "✓ ...", True)`; after `fill()` hint reads `Checking 'Blae Phoe XT-A e1459' at Spansh...`; after running the job the hint is the result's, colour `#4CAF50`, field unchanged.
  - `test_check_substitutes` — `check` returns `StartCheck("Blae Phoe SS-B d751", "⚠ ... ", False)` → field `Blae Phoe SS-B d751`, hint still visible (the substitution does not clear it), colour `#FFA500`; `check` was called with `("Blae Phoe XT-A e1459", (768.375, -565.90625, 14325.34375))`.
  - `test_stale_result_is_dropped` — two `fill()` calls queue two jobs; running the first job after the second leaves the second's result only (first returns a substitution, second a known result → field keeps the current system).
  - `test_result_dropped_after_typing` — `fill()`, then `field.set("Sol")`, then run the job → field `Sol`, hint hidden.
  - `test_hint_clears_when_text_changes` — after a shown result, `field.set("Colonia")` hides the hint.
  - `test_result_after_destroy_is_ignored` — `fill()`, `field.destroy()`, run the job → no exception.
  - `test_hint_wraps` — `fill.hint.cget("wraplength") == 300`.

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_location_fill.py` — Expected: FAIL (module missing).

- [ ] **Step 3: Implement** the module (docstring names the spec, L1–L6). Log with
  `get_logger('LocationFill')`.

- [ ] **Step 4: Run** the same command — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add edmrn/ext/location_fill.py tests/test_location_fill.py
git commit -m "feat(location): location button for system fields with Spansh check hint"
```

### Task 3: Location button for coordinate fields

**Files:**
- Modify: `edmrn/ext/location_fill.py`
- Test: `tests/test_location_fill.py`

**Interfaces:**
- Consumes: `current_location`, `format_coord`, `Tooltip`, `UNKNOWN_LOCATION`, `NO_COORDINATES`,
  `COORDS_TOOLTIP`, `BUTTON_TEXT` (Task 2).
- Produces:
  - `fill_coordinates(app, entries: tuple, name_entry=None, *, warn: Optional[Callable[[str], None]] = None) -> bool`
    — writes X, Y, Z (and the system name into `name_entry`) with `delete(0, "end")` then
    `insert(0, text)`; returns `True` on success. Without a location it calls
    `warn(UNKNOWN_LOCATION)`, without coordinates `warn(NO_COORDINATES.format(system=...))`, and
    changes no entry. Default `warn` opens `edmrn.gui.WarningDialog(app, "Current Location", message)`.
  - `add_coords_button(app, master, entries: tuple, *, name_entry=None, warn=None, **grid) -> CTkButton`
    — a `BUTTON_TEXT` button (width 32, `Tooltip(COORDS_TOOLTIP)`) in `master`, placed with
    `button.grid(**grid)`, command `fill_coordinates(...)`. Works with CTkEntry and `tk.Entry`.

- [ ] **Step 1: Write the failing tests** (same file, same fixtures; entries are real `ctk.CTkEntry`
  and `tk.Entry` widgets):
  - `test_coordinates_fill` — three CTkEntry → `"768.375"`, `"-565.90625"`, `"14325.34375"`; returns `True`.
  - `test_coordinates_replace_text` — entries pre-filled with `"1"`, `tk.Entry` widgets, plus a `tk.Entry` name field → exact values, name `Blae Phoe XT-A e1459`.
  - `test_coordinates_without_location` — tracker without system → `warn` called once with `UNKNOWN_LOCATION`, entries unchanged, returns `False`.
  - `test_coordinates_without_coords` — state with system `Docked Only` and `coords=None` → `warn("Current coordinates unknown - the journal has no position for 'Docked Only' yet.")`.
  - `test_add_coords_button` — `add_coords_button(app, frame, entries, warn=..., row=0, column=8)` → `grid_info()` row 0 column 8, text `📍`; `button.invoke()` fills the entries.

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_location_fill.py` — Expected: the new tests FAIL.

- [ ] **Step 3: Implement** both functions.

- [ ] **Step 4: Run** the same command — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add edmrn/ext/location_fill.py tests/test_location_fill.py
git commit -m "feat(location): location button for coordinate fields"
```

### Task 4: Hooks in the upstream tabs and the scrolling Neutron column

**Files:**
- Modify: `edmrn/ext/location_fill.py` (hook functions)
- Modify: `edmrn/ui_components.py` (`create_neutron_tab`, `create_galaxy_plotter_tab`)
- Modify: `edmrn/custom_route.py` (`CustomRouteTab._build_ui`, `CustomRouteTab._add_by_coordinates`)
- Modify: `edmrn/system_info_section.py` (`SystemInfoSection._build_ui`, after `fetch_btn.grid(...)`)
- Modify: `docs/architecture/overview.md` (upstream hook table)
- Test: `tests/test_location_fill_hooks.py`

**Interfaces:**
- Consumes: `SystemFieldFill`, `add_coords_button` (Tasks 2, 3).
- Produces (each logs with `logger.exception` and returns instead of raising, so a failing button
  never stops a tab from building):
  - `add_neutron_tab_buttons(app) -> None` — `SystemFieldFill(..., check_spansh=True)` on
    `app.neutron_from_autocomplete` and `app.neutron_to_autocomplete`; `add_coords_button(app,
    app.nearest_find_btn.master, (app.nearest_x_entry, app.nearest_y_entry, app.nearest_z_entry),
    row=0, column=8, padx=(0, 8), pady=6)`.
  - `add_galaxy_tab_buttons(app) -> None` — `check_spansh=True` on `app.galaxy_source_autocomplete`
    and `app.galaxy_dest_autocomplete`.
  - `add_custom_route_buttons(tab) -> None` — `check_spansh=False` on `tab.starting_entry`,
    `tab.ending_entry`, `tab.system_entry` (`app` is `tab.app`).
  - `add_coords_dialog_button(app, frame, name_entry, entries: tuple) -> None` —
    `add_coords_button(..., name_entry=name_entry, row=0, column=2, padx=(4, 0), pady=4)`.
  - `add_system_info_button(app, section) -> None` — `check_spansh=False`,
    `after_fill=lambda _name: section.fetch_callback()` on `section.system_info_entry`; does nothing
    when `app` is `None`.

Hook lines (import inside the function, as `add_range_panel` is imported in `ui_components.py`):
- `create_neutron_tab`: `left_frame = ctk.CTkFrame(main_frame, corner_radius=10, fg_color=colors['frame'])`
  becomes `ctk.CTkScrollableFrame(...)` with the same arguments (spec L10); at the end of the method
  `from edmrn.ext.location_fill import add_neutron_tab_buttons` and `add_neutron_tab_buttons(self.app)`.
- `create_galaxy_plotter_tab`: same two lines with `add_galaxy_tab_buttons(self.app)` at the end.
- `CustomRouteTab._build_ui`: `add_custom_route_buttons(self)` at the end.
- `CustomRouteTab._add_by_coordinates`: after `frame.columnconfigure(1, weight=1)`,
  `add_coords_dialog_button(self.app, frame, name_entry, (x_entry, y_entry, z_entry))`.
- `SystemInfoSection._build_ui`: after `fetch_btn.grid(...)`, `add_system_info_button(app, self)`
  (`app` is the local already defined there).

- [ ] **Step 1: Write the failing tests** in `tests/test_location_fill_hooks.py`:
  - `test_hook_lines_in_upstream_builders` — `inspect.getsource` of `UIComponents.create_neutron_tab`
    contains `add_neutron_tab_buttons(self.app)` and `ctk.CTkScrollableFrame(main_frame`; of
    `create_galaxy_plotter_tab` contains `add_galaxy_tab_buttons(self.app)`; of
    `CustomRouteTab._build_ui` `add_custom_route_buttons(self)`; of `_add_by_coordinates`
    `add_coords_dialog_button(self.app, frame, name_entry, (x_entry, y_entry, z_entry))`; of
    `SystemInfoSection._build_ui` `add_system_info_button(app, self)`. (Find the class names with
    `grep -n "^class" edmrn/ui_components.py`.)
  - `test_neutron_hook_adds_buttons` — real widgets on the module root: two `AutocompleteEntry`, a
    `CTkFrame` holding three `CTkEntry` and a FIND `CTkButton`, assembled on a `SimpleNamespace` app
    with those attribute names → both fields have a `📍` button gridded at column 1, the frame has a
    `📍` button at row 0 column 8.
  - `test_system_info_hook_fetches` — `SimpleNamespace(system_info_entry=AutocompleteEntry(root), fetch_callback=counter)`;
    after the hook, invoking the field's button with a known location fills the field and calls
    `fetch_callback` once.
  - `test_hook_failure_does_not_raise` — `add_neutron_tab_buttons(SimpleNamespace())` returns `None`.

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_location_fill_hooks.py` — Expected: FAIL.

- [ ] **Step 3: Implement** the hook functions and the hook lines. After editing, `file
  edmrn/ui_components.py edmrn/custom_route.py edmrn/system_info_section.py` must still report CRLF,
  and `git diff --stat` must show only the hook lines in those files.

- [ ] **Step 4: Add the rows** to the upstream hook table in `docs/architecture/overview.md`, one per
  upstream file: what changed and why (spec L3, L7, L10), in the table's style.

- [ ] **Step 5: Run** the whole suite: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests` — Expected: all pass (baseline before this plan: 1353).

- [ ] **Step 6: Commit**

```bash
git add edmrn/ext/location_fill.py edmrn/ui_components.py edmrn/custom_route.py edmrn/system_info_section.py docs/architecture/overview.md tests/test_location_fill_hooks.py
git commit -m "feat(location): location buttons in Neutron, Galaxy, Custom Route and System Info; scrolling Neutron column"
```

### Task 5: First test version (orchestrator; needs screenshots and judgement)

**Files:**
- Create: `docs/how-to/build.md`
- Modify: `docs/how-to/run.md` (live check: add the Spansh check), `docs/how-to/README.md` if it lists the how-tos
- Modify: only what the smoke check proves broken (layout of the new buttons)

- [ ] **Step 1: Smoke run (spec T1).** Isolated home with a fake journal (`LoadGame`, then an
  `FSDJump` to `Blae Phoe XT-A e1459` at `[768.375, -565.90625, 14325.34375]`), start `main.py`,
  drive each tab through a probe script (select tab, invoke the `📍` buttons), screenshot the window
  at the default size. Check: buttons visible and not clipped, hints readable, the Neutron column
  scrolls to the statistics, Custom Route's top row still shows "Add by Coords". Fix layout defects
  in `location_fill.py` (test-first where a test can express it).
- [ ] **Step 2: Live check (spec T2)** of `check_plot_system` for `Sol` and `Blae Phoe XT-A e1459`;
  extend the live-check script in `docs/how-to/run.md` with these two lines and their expected
  output.
- [ ] **Step 3: Windows build (spec T3).** `.venv/Scripts/python.exe -m PyInstaller edmrn.spec --noconfirm`;
  start the executable isolated; the log holds no `Traceback`. Write `docs/how-to/build.md` (command,
  output location, isolated start, what a tester checks in-game). Never stage the executable.
- [ ] **Step 4: Commit** the docs and fixes with explicit paths.

### Task 6: Milestone sweep and handover (orchestrator)

- [ ] Re-read `CLAUDE.md`, `docs/architecture/`, `docs/how-to/`, `docs/knowledge/`; correct what this
  work falsified (`docs/knowledge/upstream-baseline.md` on the plot-time fallback stays true).
- [ ] Whole-branch review (superpowers:requesting-code-review), fixes, full suite green.
- [ ] Rewrite `docs/handover/HANDOVER.md` (state, test count, how to try the test version, open
  decisions); commit. Merging into `main` waits for the commander.
