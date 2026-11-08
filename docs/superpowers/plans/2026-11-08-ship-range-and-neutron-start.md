# Laden jump range and supercharged neutron start — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** EDMRN plots with the ship's laden jump range computed from the journal and skips the
neutron waypoints a supercharged first jump reaches directly.

**Architecture:** Pure range math in `edmrn/ext/fsd.py`; a `ShipTracker` in `edmrn/ext/ship.py`
fed by upstream's journal and `Status.json` listeners; a Tk binding in `edmrn/ext/ship_panel.py`
that fills the range fields; a pure route post-processor in `edmrn/ext/neutron_start.py` called from
upstream's `NeutronRouter`. Upstream files get only the hook lines listed in the spec.

**Tech Stack:** Python 3.13, customtkinter, pytest (`.venv/Scripts/python.exe -m pytest -q`).

**Spec:** `docs/superpowers/specs/2026-11-08-ship-range-and-neutron-start-design.md` (authority).
Facts: `../knowledge/fsd-jump-range.md` (hub, two levels up from the repo: `X:/Projects/EDProjects/knowledge/`).

## Global Constraints

- Upstream files (`edmrn/*.py` outside `ext/`) get only the hook lines in the spec's table; they are
  CRLF — edit with the Edit tool, never `sed -i`; keep CRLF (`file edmrn/app.py` must still say CRLF).
- Journal and `Status.json` data only through `JournalMonitor.event_listeners` and
  `FuelTracker.status_listeners` (ADR-0001); `edmrn/ext/` never tails journals live.
- Listeners and observers run on the monitor, fuel-tracker or cold-start thread; Tk work goes through
  `app.root.after(0, ...)`.
- No network in tests (an autouse fixture blocks sockets); never touch the real journal folder
  (`tests/conftest.py` redirects home).
- Tests that import `edmrn.app` save and restore `sys.excepthook` (`tests/test_jump_fallback.py`).
- Every change to an upstream file adds or edits its row in `docs/architecture/overview.md`
  ("Upstream hook points") in the same commit.
- UI strings, code, comments and commit messages in English. Commit messages end with the two
  trailer lines the session gives (`Co-Authored-By: …`, `Claude-Session: …`).
- Stage explicit paths only.

## Review Focus

1. `Status.json` while in an SRV, fighter or on foot (flag bit 24 clear) carries the SRV's fuel and
   cargo — the ship values must stay (Task 2 test `test_status_outside_main_ship_is_ignored`).
2. A `Loadout` whose FSD item is unknown, missing, or has zero/absent `MaxJumpRange` must not raise
   and must still show `MaxJumpRange` when present (Task 1 `test_unknown_or_broken_loadouts`, Task 3
   `test_unrecognised_drive_fills_max`).
3. The commander typed a range, then cargo changes — the typed value must survive (Task 3
   `test_typing_turns_auto_off_and_survives_updates`).
4. Route plotted from a system other than the current one while supercharged — the journal boost
   must not apply (Task 2 `test_start_boost_only_for_current_system`, Task 4
   `test_journal_boost_dropped_when_start_substituted`).
5. Spansh result with fewer than three entries, missing coordinates or a non-dict body — returned
   unchanged, no exception (Task 4 `test_malformed_results_pass_through`).

---

### Task 1: Range model (`edmrn/ext/fsd.py`)

**Files:**
- Create: `edmrn/ext/fsd.py`
- Test: `tests/test_fsd.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Drive: item: str; unladen_mass: float; fuel_capacity: float; max_fuel: float; fuel_power: float; mass_term: float; boost: float; max_jump_range: float; mk2: bool`
    (`fuel_capacity` = `FuelCapacity.Main`; `max_fuel` = min(table/engineered maxfuel, fuel_capacity);
    `mass_term` = spec's `K`; `boost` = Guardian bonus in effect).
  - `drive_from_loadout(loadout: Any) -> Optional[Drive]`
  - `jump_range(drive: Drive, cargo: float, fuel: float) -> float`
  - `supercharge_multiplier(drive: Optional[Drive], star_class: Optional[str]) -> float`
  - `FSD_TABLE: dict[str, tuple[float, float, float, float]]` (optmass, maxfuel, fuelmul, fuelpower)
    and `BOOSTERS: dict[str, float]`; `MK2_ITEM = "int_hyperdrive_overcharge_size8_class5_overchargebooster_mkii"`.

Test fixtures — copy these real `Loadout` excerpts (from the commander's journal, trimmed) into
`tests/test_fsd.py` as dict constants:

```python
ASP = {"event": "Loadout", "Ship": "asp", "UnladenMass": 404.0, "FuelCapacity": {"Main": 32.0, "Reserve": 0.63}, "MaxJumpRange": 14.104039, "Modules": [{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_size5_class1", "On": True}]}
CONDA_T9M = {"event": "Loadout", "Ship": "type9_military", "UnladenMass": 1474.0, "FuelCapacity": {"Main": 64.0, "Reserve": 0.77}, "MaxJumpRange": 6.983821, "Modules": [{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_size6_class1", "On": True}]}
KRAIT_LIGHT = {"event": "Loadout", "Ship": "mediumtransport01", "UnladenMass": 528.900024, "FuelCapacity": {"Main": 32.0, "Reserve": 0.75}, "MaxJumpRange": 40.911518, "Modules": [{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_overcharge_size5_class5", "On": True, "Engineering": {"Modifiers": [{"Label": "FSDOptimalMass", "Value": 1894.099976, "OriginalValue": 1175.0, "LessIsGood": 0}]}}, {"Slot": "Slot02_Size5", "Item": "int_guardianfsdbooster_size5", "On": True}]}
CASPIAN = {"event": "Loadout", "Ship": "explorer_nx", "UnladenMass": 1256.649902, "FuelCapacity": {"Main": 128.0, "Reserve": 1.14}, "MaxJumpRange": 88.20607, "Modules": [{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_overcharge_size8_class5_overchargebooster_mkii", "On": True, "Engineering": {"Modifiers": [{"Label": "FSDOptimalMass", "Value": 7528.039551, "OriginalValue": 4670.0, "LessIsGood": 0}]}}, {"Slot": "Slot04_Size5", "Item": "int_guardianfsdbooster_size5", "On": True}]}
MANDALAY_BOOSTED = {"event": "Loadout", "Ship": "mandalay", "UnladenMass": 242.143997, "FuelCapacity": {"Main": 32.0, "Reserve": 0.5}, "MaxJumpRange": 95.444145, "Modules": [{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_overcharge_size5_class5", "On": True, "Engineering": {"Modifiers": [{"Label": "FSDOptimalMass", "Value": 1821.25, "OriginalValue": 1175.0, "LessIsGood": 0}]}}, {"Slot": "Slot02_Size5", "Item": "int_guardianfsdbooster_size5", "On": True}]}
MANDALAY_BOOSTER_OFF = {**MANDALAY_BOOSTED, "MaxJumpRange": 84.944153, "Modules": [MANDALAY_BOOSTED["Modules"][0], {"Slot": "Slot02_Size5", "Item": "int_guardianfsdbooster_size5", "On": False}]}
```

- [ ] **Step 1: Write the failing tests** in `tests/test_fsd.py`:
  - `test_max_jump_range_is_reproduced` — for every fixture, `jump_range(d, 0, d.max_fuel) == pytest.approx(loadout["MaxJumpRange"], abs=1e-6)`.
  - `test_booster_in_effect_is_read_from_max_jump_range` — `drive_from_loadout(MANDALAY_BOOSTED).boost == 10.5`; `MANDALAY_BOOSTER_OFF` → `0.0`; `KRAIT_LIGHT` (booster on, not counted by the game) → `0.0`; `CASPIAN` → `10.5`; `ASP` → `0.0`.
  - `test_laden_and_current_ranges` — with `d = drive_from_loadout(MANDALAY_BOOSTED)`:
    `jump_range(d, 0, 32.0) == approx(87.14010365, abs=1e-6)`; `jump_range(d, 24, 32.0) == approx(80.97072743, abs=1e-6)`;
    `jump_range(d, 10, 20.0) == approx(87.70333565, abs=1e-6)`; low fuel `jump_range(d, 0, 2.6) == approx(75.19241882, abs=1e-6)`.
    Mk II: `jump_range(drive_from_loadout(CASPIAN), 0, 128.0) == approx(81.40436823, abs=1e-6)`.
  - `test_inputs_are_clamped` — `jump_range(d, -5, 40.0) == jump_range(d, 0, 32.0)`; `jump_range(d, 0, -1.0) == d.boost` (no fuel → only the flat bonus).
  - `test_mk2_flag_and_multipliers` — `drive_from_loadout(CASPIAN).mk2 is True`, others `False`;
    `supercharge_multiplier(caspian, "N") == 6.0`, `(caspian, "DA") == 3.0`, `(asp, "N") == 4.0`,
    `(asp, "DC") == 1.5`, `(asp, "D") == 1.5`, `(asp, "K") == 1.0`, `(None, "N") == 4.0`, `(asp, None) == 1.0`.
  - `test_engineered_max_fuel_replaces_the_table` — `ASP` with a `MaxFuelPerJump` modifier `Value 3.63` on the FSD
    (and `MaxJumpRange` recomputed by the test as `(3.63/0.011)**(1/2.45)*560/(404+3.63)`) gives `d.max_fuel == 3.63` and reproduces that `MaxJumpRange`.
  - `test_unexplained_max_jump_range_assumes_switched_on_boosters` — `MANDALAY_BOOSTED` with `MaxJumpRange` 120.0 → `boost == 10.5`, a warning logged once (use `caplog`; a second call with the same item logs nothing new).
  - `test_unknown_or_broken_loadouts` — each returns `None` and none raises: unknown item `int_hyperdrive_size9_class9`;
    no FSD module; `UnladenMass` missing, `0`, `True`, `"404"`; `FuelCapacity` missing or `{"Main": 0}`; `MaxJumpRange` `0`, `None`, `float("nan")`;
    `Modules` not a list; the loadout not a dict; a modifier `Value` that is a string (ignored, table value used, still a `Drive`).

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/test_fsd.py -q` — expect FAIL (module missing).

- [ ] **Step 3: Implement `edmrn/ext/fsd.py`.** Table values exactly as `knowledge/fsd-jump-range.md`
  (standard drives size 2–7, SCO size 2–8, the Mk II, five boosters); item keys lower case; booster
  items that are `"On": False` do not count. Matching tolerance 0.05 LY; when both candidates match,
  prefer the closer. Numbers must be finite and positive (`math.isfinite`, reject `bool`). Module
  docstring points at the spec and the knowledge file.

- [ ] **Step 4: Run** the same command — expect PASS; then the full suite
  `.venv/Scripts/python.exe -m pytest -q` — expect no new failures.

- [ ] **Step 5: Commit** `git add edmrn/ext/fsd.py tests/test_fsd.py` — `feat(range): FSD range model calibrated on MaxJumpRange`.

---

### Task 2: Ship state from the journal and `Status.json` (`edmrn/ext/ship.py`) and the install hooks

**Files:**
- Create: `edmrn/ext/ship.py`
- Modify: `edmrn/app.py` — after `install_edmrn_crew(self)` (line ~407) add
  `from edmrn.ext.ship import install as install_edmrn_ship` / `install_edmrn_ship(self)`; in
  `_journal_callback`'s `Loadout` branch change `if max_jump_range:` to
  `if max_jump_range and getattr(self, 'ship_tracker', None) is None:`
- Modify: `docs/architecture/overview.md` — two hook rows; new section "Ship range data flow (ship range spec)"
- Test: `tests/test_ship.py`

**Interfaces:**
- Consumes: Task 1 (`Drive`, `drive_from_loadout`, `jump_range`, `supercharge_multiplier`).
- Produces:
  - `@dataclass(frozen=True) class ShipState: commander: Optional[str]=None; system: Optional[str]=None; drive: Optional[Drive]=None; max_jump_range: Optional[float]=None; cargo: Optional[float]=None; fuel_main: Optional[float]=None; star_class: Optional[str]=None; supercharge: Optional[float]=None; pending_star: Optional[tuple[str, str]]=None; journal_at: Optional[str]=None`
    with properties `laden -> Optional[float]` (`jump_range(drive, cargo or 0, drive.fuel_capacity)`) and
    `current -> Optional[float]` (`None` unless `drive` and `fuel_main` are known).
  - `apply_event(state: ShipState, event: Any) -> ShipState` and `apply_status(state: ShipState, status: Any) -> ShipState` (same object when nothing changed).
  - `class ShipTracker: state; subscribe(observer: Callable[[ShipState], None]); feed(event); feed_status(status); adopt(state: ShipState) -> bool`
  - `cold_start(journal_dir: Optional[str], tracker: ShipTracker, max_files: int = 5) -> Optional[ShipState]`
  - `install(app) -> ShipTracker` — sets `app.ship_tracker`, appends `tracker.feed` to `JournalMonitor.event_listeners` and `tracker.feed_status` to `FuelTracker.status_listeners`, subscribes `edmrn.ext.ship_panel.binding_for(app).on_state` through `app.root.after(0, ...)` (Task 3 creates that module; until then guard the import with `try/except ImportError` — Task 3 removes the guard), starts `cold_start` in thread `edmrn-ship-cold-start` with `edmrn.ext.location.journal_dir_for(app)`.
  - `start_boost(app, from_system: str) -> Optional[float]` — never raises.

- [ ] **Step 1: Write the failing tests** in `tests/test_ship.py` (use `journal_helpers` for files, `FakeVar`/`ImmediateRoot` patterns from `tests/test_location_install.py`):
  - `test_loadout_sets_drive_and_max` — after `Loadout` (Task 1's `MANDALAY_BOOSTED`, import it from `test_fsd`), `state.drive.boost == 10.5`, `state.max_jump_range == 95.444145`, `state.laden == approx(87.1401, abs=1e-4)`, `state.current is None`.
  - `test_unrecognised_drive_keeps_max` — unknown FSD item: `drive is None`, `max_jump_range` set, `laden is None`.
  - `test_status_updates_fuel_and_cargo` — status `{"Flags": 1 << 24, "Fuel": {"FuelMain": 20.0, "FuelReservoir": 0.4}, "Cargo": 10.0}` → `fuel_main == 20.0`, `cargo == 10.0`, `current == approx(87.7033, abs=1e-4)`, `laden == approx(jump_range(drive, 10, 32), abs=1e-9)`.
  - `test_status_outside_main_ship_is_ignored` — `Flags` `0` or missing, or `Fuel` not a dict, or `Cargo` `-1`/`20000`/`"5"`/`True`: state unchanged (same object).
  - `test_cargo_event_is_a_fallback` — `{"event": "Cargo", "Vessel": "Ship", "Count": 7}` → `cargo == 7`; `"Vessel": "SRV"` ignored.
  - `test_star_class_from_startjump_and_scan` — `StartJump` (`JumpType` `Hyperspace`, `StarSystem` `B`, `StarClass` `N`) then `FSDJump` to `B` → `star_class == "N"`; `FSDJump` to `C` after a `StartJump` naming `B` → `None`; then `Scan` `{"StarSystem": "C", "DistanceFromArrivalLS": 0.0, "StarType": "DA"}` → `"DA"`; a `Scan` with `DistanceFromArrivalLS` 1200 or of another system changes nothing.
  - `test_supercharge_lifecycle` — `JetConeBoost` `BoostValue` 6.0 → `supercharge == 6.0`; `FSDJump` clears it; `CarrierJump` clears it and `star_class`; `Location` in the same system keeps both, in another system clears both; `BoostValue` `0`, `-1`, `"6"` ignored.
  - `test_commander_switch_resets` — `LoadGame` `Commander` `A`, `Loadout`, then `LoadGame` `B` → state has only `commander == "B"`.
  - `test_adopt_keeps_live_status_and_newer_journal` — a live tracker with `fuel_main` 20 (from status, no `journal_at`) adopting a replayed state with a drive keeps `fuel_main == 20` and takes the drive; a tracker whose `journal_at` is newer than the replay's refuses (`adopt` returns `False`).
  - `test_cold_start_finds_the_loadout` — two journal files in `tmp_path`; the newest has `LoadGame`, `Loadout`, `FSDJump`; `cold_start(str(tmp_path), tracker)` returns a state with the drive and `system`; an empty or missing folder returns `None`.
  - `test_start_boost_only_for_current_system` — tracker in system `Sol` with `supercharge` 6.0: `start_boost(app, "sol ") == 6.0`, `start_boost(app, "Achenar") is None`; with no supercharge and `star_class` `N` and a Mk II drive → `6.0`; `star_class` `DA` and a non-Mk II drive → `1.5`; `star_class` `K` → `None`; `app` without `ship_tracker` → `None`.
  - `test_install_registers_listeners_and_skips_upstream_fill` — `install(app)` with a `SimpleNamespace` app (root `ImmediateRoot`, `config.journal_path=""`, `journal_monitor=None`) appends to both listener lists (monkeypatch both class lists to fresh lists) and sets `app.ship_tracker`; then calling `edmrn.app.EDMRN_App._journal_callback(fake_self, None, {"event": "Loadout", "MaxJumpRange": 50.0, "FuelCapacity": 0})` with `fake_self` having `ship_tracker` and a `jump_range` var whose `set` records calls → no call; without `ship_tracker` → `"50.0"` is set (root `ImmediateRoot`, main thread).

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/test_ship.py -q` — expect FAIL.

- [ ] **Step 3: Implement `edmrn/ext/ship.py`** following `edmrn/ext/location.py`'s structure
  (`apply_event` on a lock, observers called outside the lock with the current state, exceptions in
  observers logged). Event rules exactly as the spec's "Ship state" list; `journal_at` follows the
  event `timestamp` for journal events only. `adopt(replayed)`: refuse when the live `journal_at` is
  not older; otherwise take the replay but keep the live `fuel_main` and `cargo` when not `None`.

- [ ] **Step 4: Add the two `app.py` hooks** (Edit tool; CRLF) and the overview rows:
  - `edmrn/app.py` | Two lines after `install_edmrn_crew(self)`: `edmrn.ext.ship.install(self)` | Wires the ship range tracker (ship range spec) | no
  - `edmrn/app.py` (`_journal_callback`) | The `Loadout` branch fills `jump_range` with `MaxJumpRange` only when `ship_tracker` is absent | `MaxJumpRange` assumes no cargo and one jump of fuel; the laden range replaces it (ship range spec J2) | yes
  Overview section "Ship range data flow": journal → `ShipTracker.feed`, `Status.json` → `feed_status`, cold start → `adopt`, observers → `root.after` → range binding; invariant: only the binding writes `app.jump_range` from ship data.

- [ ] **Step 5: Run** `tests/test_ship.py`, then the full suite — expect PASS / no new failures; `file edmrn/app.py` still reports CRLF.

- [ ] **Step 6: Commit** `git add edmrn/ext/ship.py tests/test_ship.py edmrn/app.py docs/architecture/overview.md` — `feat(range): ship tracker from journal and Status.json; upstream MaxJumpRange fill yields to it`.

---

### Task 3: Range fields follow the laden range (`edmrn/ext/ship_panel.py`)

**Files:**
- Create: `edmrn/ext/ship_panel.py`
- Modify: `edmrn/ext/ship.py` (remove Task 2's import guard)
- Modify: `edmrn/app.py` — one line after `self.range_entry.pack(anchor="w")` (route tab, line ~907):
  `from edmrn.ext.ship_panel import add_range_panel; add_range_panel(self, jump_input_frame)` (two lines, no `;`)
- Modify: `edmrn/ui_components.py` — after `self.app.neutron_range_entry.pack(...)` (line ~277):
  two lines importing and calling `add_range_panel(self.app, range_frame)`
- Modify: `docs/architecture/overview.md` — two hook rows
- Test: `tests/test_ship_panel.py`

**Interfaces:**
- Consumes: Task 2 (`ShipState`, its `laden`/`current`), Task 1 (`supercharge_multiplier`), `edmrn.ext.neutron_resume.BOOST_LABELS`.
- Produces:
  - `range_text(state: ShipState) -> str` (pure).
  - `class RangeBinding: auto: bool; on_state(state: ShipState) -> None; set_auto(value: bool) -> None; register_panel(label, checkbox_var) -> None`
  - `binding_for(app) -> RangeBinding` — get-or-create `app.ship_range`.
  - `add_range_panel(app, parent) -> None` — never raises (logs).

- [ ] **Step 1: Write the failing tests** in `tests/test_ship_panel.py`:
  - `test_range_text` — every number floored to 0.1:
    a state with `MANDALAY_BOOSTED`'s drive, `cargo` 0, `fuel_main` 20 → exactly `"Laden 87.1 LY · now 90.6 · max 95.4 · neutron ×4 348.5"`
    (format `Laden {l} LY · now {c} · max {m} · neutron ×{x:g} {n}`, `n` = floor of `x × laden`);
    without `fuel_main` the `· now …` part is absent; Mk II drive → `neutron ×6`;
    no drive, `max_jump_range` 58.7 → `"Max 58.7 LY (drive not recognised, laden range unknown)"`;
    empty state → `"Waiting for ship data from the journal"`.
  - `test_auto_writes_floored_laden_range_once` — fake app with `FakeVar` `jump_range` (from `tests/test_location_install.py`, copy into the test) counting `set` calls: two `on_state` calls with the same laden range → one write of `"87.1"`; a state whose laden floor changes → second write.
  - `test_unrecognised_drive_fills_max` — no drive, `max_jump_range` 58.73 → field `"58.7"`.
  - `test_typing_turns_auto_off_and_survives_updates` — after a write, the field is set to `"60"` by the test and the binding's key handler runs (call the handler `binding.on_key()` directly) → `auto is False`; a later `on_state` with another laden value leaves `"60"`; `set_auto(True)` writes the current laden value at once.
  - `test_key_without_change_keeps_auto` — `on_key()` when the field still holds the last written value → `auto` stays `True`.
  - `test_new_drive_sets_boost_label_while_auto` — app with `neutron_boost_var`: a Mk II drive → `"x6 (Caspian)"`; then a non-Mk II drive → `"x4 (Normal)"`; with Auto off nothing changes; an app without `neutron_boost_var` does not raise.
  - `test_add_range_panel_builds_widgets` (Tk; skip without a display exactly like `tests/test_neutron_resume_gui.py`'s `_tk_root` fixture) — `add_range_panel` on a frame creates one `CTkLabel` and one `CTkCheckBox` with text `"Auto"`; unticking the checkbox sets `binding.auto False`; a parent that raises on `pack` is logged, not raised.

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/test_ship_panel.py -q` — expect FAIL.

- [ ] **Step 3: Implement `edmrn/ext/ship_panel.py`.** `on_state` runs on the Tk thread (Task 2
  delivers it through `root.after`); it updates every registered label, and, while `auto`, writes
  `app.jump_range` only when the formatted value differs from the field's current text, and sets
  the boost label when `state.drive` differs from the last seen drive. `add_range_panel` binds
  `<KeyRelease>` (with `add="+"`) on `app.range_entry` if `parent` is the route tab's frame and on
  `app.neutron_range_entry` for the Neutron frame — simplest: bind on whichever of the two entries
  is a descendant of `parent`; the handler compares the entry's variable to the binding's last
  written text. The checkbox variable is shared: `set_auto` updates every registered checkbox.

- [ ] **Step 4: Add the hooks** (Edit tool; CRLF) and overview rows:
  - `edmrn/app.py` (route tab) | Two lines after `self.range_entry.pack(anchor="w")`: `add_range_panel(self, jump_input_frame)` | Shows laden/now/max range and the Auto switch (ship range spec J2, J3) | no
  - `edmrn/ui_components.py` (range) | Two lines after the Neutron range entry: `add_range_panel(self.app, range_frame)` | Same, on the Neutron tab | no

- [ ] **Step 5: Run** the test file, the full suite, and an isolated start
  (`docs/how-to/run.md`, "Start it isolated"; write a journal into `$H` with a `LoadGame`, the
  `MANDALAY_BOOSTED` `Loadout` and an `FSDJump`) — the log has no traceback and the run reaches
  the window (exit 124 from `timeout`).

- [ ] **Step 6: Commit** `git add edmrn/ext/ship_panel.py edmrn/ext/ship.py tests/test_ship_panel.py edmrn/app.py edmrn/ui_components.py docs/architecture/overview.md` — `feat(range): range fields follow the laden range with an Auto switch`.

---

### Task 4: Supercharged start (`edmrn/ext/neutron_start.py`) and the Neutron hooks

**Files:**
- Create: `edmrn/ext/neutron_start.py`
- Modify: `edmrn/neutron.py` — import; `calculate_route(..., start_coords=None, start_boost=None)`;
  `calculate_route_async(..., start_coords=None, start_boost=None)` passes it on; after a successful
  `_wait_for_result`, `optimize_start` runs on `result_data["data"]` before `_process_route_data`;
  the result's `note` is the substitution note and the start note joined by `"\n"`, or `None` when
  both are absent
- Modify: `edmrn/neutron_manager.py` — import `start_boost` from `edmrn.ext.ship`; compute it from
  `from_system` before the worker starts (in the existing `try` that computes `start_coords`) and
  pass `start_boost=` to `calculate_route_async`
- Modify: `docs/architecture/overview.md` — extend the `edmrn/neutron.py` and `edmrn/neutron_manager.py` rows
- Test: `tests/test_neutron_start.py`; extend `tests/test_neutron.py` and `tests/test_neutron_manager.py`

**Interfaces:**
- Consumes: Task 2 `start_boost(app, from_system)`.
- Produces: `optimize_start(data: Any, range_ly: float, start_boost: Optional[float], multiplier: float) -> tuple[Any, Optional[str]]`.

Probe data for the tests (Spansh, 2026-11-08, range 75):

```python
EOL_PROU = {"result": {"system_jumps": [
    {"system": "Eol Prou WN-T d3-2412", "x": -9269.75, "y": -995.8125, "z": 19737.09375, "distance_jumped": 0, "jumps": 0, "neutron_star": True},
    {"system": "Eol Prou WN-T d3-304", "x": -9344.21875, "y": -994.09375, "z": 19738.40625, "distance_jumped": 74.5001441902967, "jumps": 1, "neutron_star": True},
    {"system": "Colonia", "x": -9530.5, "y": -910.28125, "z": 19808.125, "distance_jumped": 215.83777092848, "jumps": 1, "neutron_star": False}]}}
GRIA = {"result": {"system_jumps": [
    {"system": "Gria Eaec IO-X d2-406", "x": -2567.03125, "y": -100.90625, "z": 15857.40625, "distance_jumped": 0, "jumps": 0, "neutron_star": True},
    {"system": "Gria Eaec IO-X d2-3574", "x": -2618.03125, "y": -100.0625, "z": 15875.625, "distance_jumped": 54.1630387406855, "jumps": 1, "neutron_star": True},
    {"system": "Boeths FH-S d5-3517", "x": -2988.46875, "y": -84.75, "z": 16084.6875, "distance_jumped": 425.635457837749, "jumps": 1, "neutron_star": True},
    {"system": "Boeths KO-O d7-4279", "x": -3381.75, "y": -166.9375, "z": 16238.34375, "distance_jumped": 430.157145612943, "jumps": 1, "neutron_star": True}]}}
```

- [ ] **Step 1: Write the failing tests** in `tests/test_neutron_start.py`:
  - `test_spansh_neutron_flag_skips_the_hop` — `optimize_start(EOL_PROU, 75.0, None, 4)` → entries `["Eol Prou WN-T d3-2412", "Colonia"]`, the Colonia entry `jumps == 1`, `distance_jumped == approx(283.4636, abs=1e-3)`; note `"Supercharged start (×4): skipped 1 waypoint, first jump 283.5 LY"`; the input dict is not modified.
  - `test_no_skip_beyond_reach` — `optimize_start(GRIA, 75.0, None, 6)` → unchanged entries, note `None`; `optimize_start(GRIA, 80.0, None, 6)` → `["Gria Eaec IO-X d2-406", "Boeths FH-S d5-3517", "Boeths KO-O d7-4279"]` (479.1 ≤ 480).
  - `test_journal_boost_wins_and_white_dwarf_counts` — `EOL_PROU` with the first entry's `neutron_star` set `False`: `start_boost=None` → unchanged; `start_boost=1.5` (reach 112.5) → unchanged; `start_boost=6.0` → skip, note says `(×6)`.
  - `test_furthest_reachable_waypoint_wins` — a synthetic five-entry route on the x axis (0, 30, 150, 290, 600) with `start_boost=4.0`, range 75 → entries `[0, 290, 600]`, note `skipped 2 waypoints`.
  - `test_malformed_results_pass_through` — each returns `(input, None)` without raising: `None`, `{}`, `{"result": {}}`, fewer than three entries, an entry without `x`, a coordinate `"1"` or `True`, `range_ly` `0` or `nan`.
  - In `tests/test_neutron.py`: `test_start_boost_applies_after_poll` — fake submit (`used_from="Eol Prou WN-T d3-2412"`) and poll returning `EOL_PROU` → `calculate_route("Eol Prou WN-T d3-2412", "Colonia", 75.0, start_boost=6.0)` has two waypoints and `note` containing `"Supercharged start (×6)"`; `test_journal_boost_dropped_when_start_substituted` — `used_from="Near Sys"` and the first entry `neutron_star` `False` → three waypoints, note equals the substitution note only.
  - In `tests/test_neutron_manager.py`: `test_start_boost_is_passed_to_the_router` — the fake router records the `start_boost` kwarg; with `edmrn.neutron_manager.start_boost` monkeypatched to return `6.0`, the router receives `6.0`; when it raises, `None` is passed and plotting continues.

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/test_neutron_start.py tests/test_neutron.py tests/test_neutron_manager.py -q` — expect FAIL.

- [ ] **Step 3: Implement `optimize_start`.** Distances with `math.dist` on `(x, y, z)` of entry 0 and
  entry `k`; search `k` from the last entry down to 2; reach `b × range_ly` inclusive. The boost:
  `start_boost` if it is a finite number `> 1`, else `multiplier` when entry 0 has
  `neutron_star is True`, else none. Work on `copy.deepcopy(data)`. Note text: `Supercharged start
  (×{b:g}): skipped {n} waypoint{'s' if n != 1 else ''}, first jump {d:.1f} LY`.

- [ ] **Step 4: Add the hooks** in `edmrn/neutron.py` and `edmrn/neutron_manager.py` (Edit tool;
  CRLF). In `calculate_route` the journal boost is dropped when `used_from != from_system`; the
  multiplier passed is the request's `supercharge_multiplier`. Update both overview rows (append:
  "`start_boost` from the journal; `optimize_start` skips waypoints a supercharged first jump
  reaches, its note joins `note` (ship range spec J5)").

- [ ] **Step 5: Run** the three test files and the full suite — PASS / no new failures; both upstream files still CRLF.

- [ ] **Step 6: Commit** `git add edmrn/ext/neutron_start.py edmrn/neutron.py edmrn/neutron_manager.py tests/test_neutron_start.py tests/test_neutron.py tests/test_neutron_manager.py docs/architecture/overview.md` — `fix(neutron): supercharge at the start system; skip waypoints the first jump reaches`.
