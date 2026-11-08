# Laden jump range from the journal and the supercharged neutron start — design

Status: Accepted (2026-11-08; the commander delegated all decisions for this work)
Date: 2026-11-08
Rules: ADR-0001 (journal data only through upstream's journal layer), ADR-0002 (upstream hook points
only). Facts: `../../../../knowledge/fsd-jump-range.md` (formula, constants, multipliers),
`../../../../knowledge/bulk-data.md` ("Neutron route plotter", probe 2026-11-08).

## Purpose

Two defects make plotted neutron routes wrong in daily use.

1. **Range.** Upstream fills "Ship Jump Range" with `Loadout.MaxJumpRange`. That value assumes no
   cargo and just enough fuel for one jump, so a real ship never reaches it. Spansh then plots
   jumps the ship cannot make. The journal writes no current ship range at all (the commander
   assumed it did; `CarrierStats.JumpRangeCurr` is for fleet carriers only), so EDMRN computes the
   range from `Loadout` and `Status.json`.
2. **Neutron start.** Spansh's neutron plotter never supercharges at the start system, even when it
   reports the start as a neutron star; it plots a hop of tens of light years to a neighbouring
   neutron star first. Probe: `Eol Prou WN-T d3-2412` → `Colonia` (283.5 LY) at range 75 comes back
   as 74.5 LY + 215.8 LY, while one supercharged jump (300 LY at ×4, 450 LY at ×6) reaches Colonia.
   The commander's journal shows five such detours where the target was within supercharged range.
   White dwarfs are never used by Spansh.

## Requirements

- J1. For the active ship of the tracked commander, EDMRN knows three ranges:
  - **laden:** current cargo, full main tank — the range used for plotting, because a full tank is
    the heaviest state the ship flies in while it scoops along a route;
  - **current:** current cargo and current main-tank fuel;
  - **max:** `Loadout.MaxJumpRange`, unchanged.
- J2. "Ship Jump Range (LY)" on the route tab, and through upstream's existing sync the Neutron
  tab's range, follow the laden range automatically, rounded down to 0.1 LY.
  - Upstream's fill with `MaxJumpRange` no longer runs while this feature is installed.
  - A keystroke in either range field switches the automatic fill off. A checkbox "Auto" under each
    field shows the state and turns it back on, which fills the laden range at once. The state is
    shared by both fields, held in memory, and on at every start, matching upstream, which also
    overwrote the field on every `Loadout`.
  - The field changes only when the rounded laden range changes.
- J3. Under each range field a one-line label shows, for example,
  `Laden 52.3 LY · now 54.1 · max 58.7 · neutron ×4 209.2`. Without ship data it reads
  `Waiting for ship data from the journal`. When the drive is not in EDMRN's table the label reads
  `Max 58.7 LY (drive not recognised, laden range unknown)` and the field gets `MaxJumpRange`, as
  upstream did.
- J4. While Auto is on, a `Loadout` also sets the Neutron tab's FSD boost: `x6` for the Mk II drive,
  `x4` for every other drive (`knowledge/fsd-jump-range.md`, multipliers).
- J5. **Supercharged start.** After Spansh returns a neutron route, EDMRN removes the waypoints that
  the first jump can skip:
  - The start boost `b` is, in this order: the pending `JetConeBoost.BoostValue` if the commander
    is already supercharged; the drive's neutron multiplier if the arrival star is a neutron star;
    its white-dwarf multiplier if the arrival star is a white dwarf; and, without journal data, the
    request's multiplier (4 or 6) if Spansh marks the start `neutron_star`. Journal data counts only
    when the route's start is the tracked current system and Spansh did not substitute the start.
  - With `b > 1` and plotting range `R`, EDMRN finds the furthest waypoint `k ≥ 2` with straight
    distance from the start `≤ b × R`, using the coordinates Spansh returns. If one exists, the
    waypoints between the start and `k` are dropped; the new first leg is one jump of that
    distance. Everything after `k` stays as Spansh plotted it.
  - The result line names the change, e.g. `Supercharged start (×6): skipped 1 waypoint, first jump
    283.5 LY`.
  - Without coordinates, or with `b ≤ 1`, the route is unchanged.
- J6. Fuel and cargo count only while the commander sits in the main ship (`Status.json` flag
  bit 24); in an SRV, fighter or on foot the last ship values stay.
- J7. A missing, unreadable or unexpected journal or `Status.json` value never raises into Tk and
  never blocks plotting; the field then keeps its last value.
- J8. Tests never touch the real journal folder or the network (CLAUDE.md).

## Design

### Range model: `edmrn/ext/fsd.py` (pure functions)

- A table of every drive in `knowledge/fsd-jump-range.md` keyed by the lower-case journal `Item`:
  `optmass`, `maxfuel`, `fuelmul`, `fuelpower`, and whether it is the Mk II. A second table holds
  the Guardian booster bonus per item. Size-8 standard drives do not exist and are absent.
- `drive_from_loadout(loadout)` returns an immutable drive description or `None` (no FSD, unknown
  item, missing or non-positive `UnladenMass`, `FuelCapacity.Main` or `MaxJumpRange`). Steps:
  1. Engineering modifiers `FSDOptimalMass` and `MaxFuelPerJump` replace the table values.
  2. `mf = min(maxfuel, FuelCapacity.Main)`.
  3. The booster in effect is the one that explains `MaxJumpRange`: the predicted unboosted value
     is compared with `MaxJumpRange` and with `MaxJumpRange` minus the bonus of the boosters fitted
     and switched on; the closer match within 0.05 LY wins. With neither match (the game changed a
     constant), the switched-on bonus is assumed and a warning is logged once per drive.
  4. The mass term is calibrated on the game's own number:
     `K = (MaxJumpRange − boost) × (UnladenMass + mf)`. The table's `optmass` and `fuelmul` serve
     only step 3, so a small table error cannot bias the ranges.
- `jump_range(drive, cargo, fuel)`:
  `K × (min(fuel, mf) / mf) ^ (1 / fuelpower) / (UnladenMass + cargo + fuel) + boost`, with `fuel`
  clamped to `[0, FuelCapacity.Main]` and `cargo` to `≥ 0`. The reservoir is ignored.
- `supercharge_multiplier(drive, star_class)`: `N` → 6 (Mk II) or 4; a class starting with `D` →
  3 (Mk II) or 1.5; anything else 1. `drive=None` counts as a non-Mk II drive.

### Ship state: `edmrn/ext/ship.py`

- Immutable `ShipState`: `commander`, `system`, `drive`, `cargo`, `fuel_main`, `star_class`,
  `supercharge` (the pending `BoostValue` or `None`), `timestamp`.
- `ShipTracker` mirrors `LocationTracker` (thread-safe holder, observers on change):
  - `feed(event)` from `JournalMonitor.event_listeners`:
    - `LoadGame` / `Commander` with another commander → fresh state.
    - `Loadout` → `drive`.
    - `StartJump` with `JumpType` `Hyperspace` → remembers `StarClass` for the coming jump.
    - `FSDJump` → `system`, `star_class` from that `StartJump` when its `StarSystem` matches,
      otherwise `None`; `supercharge` cleared.
    - `CarrierJump` → `system`; `star_class` and `supercharge` cleared.
    - `Location` → `system`; if the system changed, `star_class` and `supercharge` cleared.
    - `Scan` of the arrival star (`DistanceFromArrivalLS` 0, `StarType` present, `StarSystem` equal
      to `system` or absent) → `star_class`.
    - `JetConeBoost` → `supercharge = BoostValue` (a positive number, else ignored).
    - `Cargo` with `Vessel` `Ship` → `cargo = Count`, as a fallback until `Status.json` reports it.
  - `feed_status(status)` from `FuelTracker.status_listeners`: with flag bit 24 set, `Fuel.FuelMain`
    → `fuel_main` and `Cargo` → `cargo`. Values outside `0 … 10,000` t are ignored.
- `cold_start` replays the newest journal files (at most 5) in a daemon thread until one yields a
  drive, and hands the result to `adopt`, which never overwrites a newer live state — the location
  tracker's pattern. It is needed because upstream's monitor reads the newest journal from its end
  and never sees that session's `Loadout`.
- `install(app)` creates `app.ship_tracker`, registers both listeners and starts the cold start.
  Observers reach Tk through `root.after`.
- `start_boost(app, from_system, used_from)`: the journal part of J5's order, or `None`.

### UI: range panel

- `add_range_panel(app, parent)` adds the label (J3) and the "Auto" checkbox (J2) to `parent`. Both
  panels share one auto flag on `app.ship_tracker`'s binding. A `<KeyRelease>` binding on
  `app.range_entry` and `app.neutron_range_entry` switches Auto off; programmatic writes by
  upstream (resume, sync) do not.
- When the laden range or Auto changes, the binding writes `app.jump_range`; upstream's trace then
  saves it and syncs the Neutron field.

### Neutron start: `edmrn/ext/neutron_start.py`

- `optimize_start(data, range_ly, start_boost, multiplier)` takes Spansh's result body and returns
  `(data, note)`; `data` is a copy whose `result.system_jumps` follows J5, `note` is the J5 line or
  `None`. It never raises: malformed input returns the input unchanged.
- `multiplier` (the request's 4 or 6) applies when `start_boost` is `None` and the first entry is
  `neutron_star`.

### Upstream hook points

| File | Change |
|---|---|
| `edmrn/app.py` | Two lines after `install_edmrn_crew(self)`: `edmrn.ext.ship.install(self)` |
| `edmrn/app.py` (`_journal_callback`) | The `Loadout` branch skips its `jump_range` fill when `app.ship_tracker` exists |
| `edmrn/app.py` (route tab) | One line after `self.range_entry.pack(...)`: `add_range_panel(self, jump_input_frame)` |
| `edmrn/ui_components.py` | One line after the Neutron range entry: `add_range_panel(self.app, range_frame)` |
| `edmrn/neutron.py` | `calculate_route` and `calculate_route_async` accept `start_boost=None`; after a successful poll, `optimize_start` runs before `_process_route_data` and its note joins the result's `note` |
| `edmrn/neutron_manager.py` | Passes `start_boost=edmrn.ext.ship.start_boost(...)` and shows the joined note |

`start_boost` from the journal is computed on the Tk thread before the worker starts; whether
Spansh substituted the start is known only in the worker, so `calculate_route` drops the journal
boost when `used_from` differs from `from_system`.

## Not in scope

- The Galaxy Plotter. Upstream posts its exact-plotter parameters (ship build, cargo, "already
  supercharged") to the neutron endpoint `/api/route`; whether Spansh honours them there is open
  and recorded in the handover.
- Routing through white dwarfs or secondary neutron stars beyond the start.
- Per-leg fuel simulation; the laden range already assumes a full tank.

## Rulings made without the commander

1. Plot with the laden range (full tank), not the current one: after scooping, a route planned with
   a half-empty tank would contain jumps the ship cannot make.
2. Auto is not persisted; it starts on, as upstream's behaviour did.
3. Skip only to waypoints a single supercharged jump reaches; partial-reach legs are left to
   Spansh, whose estimate counts real stars.
4. The mass term is calibrated on `MaxJumpRange` instead of trusting the table alone.
