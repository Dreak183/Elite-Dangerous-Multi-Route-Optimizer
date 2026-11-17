# Fill system and coordinate fields from the current location; first test version — design

Status: Accepted (2026-11-17; the commander asked for autonomous execution and delegated the
decisions marked "ruling")
Date: 2026-11-17
Rules: ADR-0001 (journal data only through upstream's journal layer), ADR-0002 (upstream hook points
only). Facts: `../../../../knowledge/bulk-data.md` ("Spansh API": name search, `/api/nearest`,
unknown systems), `../../../../knowledge/reference-data.md` §6 (location events).

## Purpose

The commander plots neutron routes from wherever the ship is, often deep in unexplored space. Today
the start system has to be typed or copied, and when Spansh does not know it, the plot fails first
and is then repeated from the nearest known system, which the commander only learns from the result
note. Coordinate fields ("Find Nearest System", Custom Route "Add by Coords") must be typed by hand,
although the journal holds the exact position.

The commander also wants a first test version: EDMRN as it stands, without the crew relay, usable
in-game, started from the repository's Python environment (`../../how-to/run.md`).

## Requirements

### Location source

- L1. Every fill reads `app.location_tracker.state` (system, `StarPos` coordinates, station), the
  in-memory state the journal monitor and the cold start keep current (location spec). Nothing reads
  a journal file for this feature, so a click never blocks the window. Without a tracker, or while
  the tracker knows no system (the cold start is still reading, or no journal exists), the fill
  reports `Current system unknown - no location in the journal yet.` and changes no field.
- L2. No field in EDMRN asks for a station, so no fill writes one. (Ruling: the request allowed for
  station fields; there are none.)

### System fields

- L3. A location button (text `📍`, tooltip `Fill in your current system from the journal`) sits at
  the right end of these system fields:

  | Tab | Fields | After the fill |
  |---|---|---|
  | Neutron Highway | From System, To System | Spansh check (L4) |
  | Galaxy Plotter | Source System, Destination System | Spansh check (L4) |
  | Custom Route | Starting System, Add System | nothing more; Set or Add stays the commander's click |
  | System Info | the system name field | fetches the system's data, as the 🔍 button does |
  | Route Optimization | Starting System (Optional) | when a CSV is loaded and does not list the system, a console line says so (L3a) |

  Custom Route's "Ending System" gets no button: its top row has room for two at the default window
  size, and a route rarely ends where the commander is. (Ruling.) Custom Route's two fields give up
  the button's width from their entry, so the row keeps upstream's width and "Add by Coords" stays
  whole (L11).
- L3a. Route Optimization's optimizer accepts only a start that the loaded CSV lists and otherwise
  picks one itself; "🎯 Find Nearest" picks the CSV system nearest to the current position. The
  location button writes the current system as it is (asked for by the commander 2026-11-17). When
  a CSV is loaded and its "System Name" column does not list the system (case-insensitive), the
  Console gets `'<system>' is not in the loaded CSV - the optimizer will choose the start; 🎯 Find
  Nearest picks the CSV system nearest to you.`
- L4. **Spansh check** (plotter fields). The button writes the current system name into the field at
  once, shows `Checking '<system>' at Spansh...` under it, and asks Spansh in the background:
  1. Spansh's system search knows the name (exact, case-insensitive match, the same check as
     `lookup_system`'s Spansh step): the field keeps the name; the hint reads
     `✓ '<system>' is known to Spansh.`
  2. Spansh does not know it and the tracker has coordinates: EDMRN asks `/api/nearest` with them.
     The field then holds the nearest known system and the hint reads
     `⚠ '<system>' is not known to Spansh - using the nearest known system '<nearest>' (<d> ly away).`
     with `<d>` to one decimal. If Spansh's nearest system is the current one (same name, ignoring
     case), the result counts as known (step 1's sentence).
  3. Spansh does not know it and there are no coordinates (a `Docked` in another system without a
     jump): the field keeps the name; `⚠ '<system>' is not known to Spansh, and the journal has no
     coordinates for it.`
  4. `/api/nearest` answers no usable system: the field keeps the name;
     `⚠ '<system>' is not known to Spansh, and Spansh named no known system near it.`
  5. A request fails (overloaded, timeout, offline, unexpected): the field keeps the name;
     `⚠ Could not check '<system>' at Spansh: <ApiFailure sentence>`. The plot keeps its own
     fallback to the nearest known system (API hardening spec A5), so a failed check costs nothing.

  Ruling: the field shows the substituted system instead of keeping the current one and leaving the
  substitution to the plot. The commander sees before plotting what the route starts from, and the
  plot does not first fail with 400. Typed names keep the plot-time fallback unchanged.
- L5. The hint is a label directly under the field that wraps to the field's width, hidden while
  empty. It disappears as soon
  as the field's text is no longer the name the hint speaks about (typing, a suggestion, Reverse, a
  resumed route, the substitution note after a plot). A check result arrives only if no later click
  on the same button started a newer one and the field still holds the name that was checked;
  otherwise it is dropped. Network requests run on a daemon thread through `edmrn.ext.api.call`;
  the result reaches Tk through `root.after` (patterns.md).
- L6. A substituted start is not the tracked current system, so the journal's supercharge does not
  apply to it (ship range spec J5: journal data counts only for the current system); the route's own
  start handling is unchanged.

### Coordinate fields

- L7. A location button (text `📍`, tooltip `Fill in your current coordinates from the journal`)
  fills coordinate fields from `StarPos`:
  - Neutron Highway, "Find Nearest System": X, Y and Z; the button sits right of FIND.
  - Custom Route, "Add System by Coordinates" dialog: System Name, X, Y and Z; the button sits right
    of the name field.
- L8. Each value is written with at most five decimals and without trailing zeros (`StarPos` has a
  precision of 1/32 ly, so five decimals are exact): `-910.28125`, `19808.125`, `0`. Every field is
  cleared before it is written, so a fill never appends.
- L9. Without a known system the button reports L1's sentence; with a system but no coordinates it
  reports `Current coordinates unknown - the journal has no position for '<system>' yet.` Both appear
  in a warning dialog (these rows have no hint line), and no field changes.

### Layout

- L10. The Neutron Highway tab's left column scrolls. At the default window size upstream's column
  cut off the "Find Nearest System" row and hid the statistics below it entirely; the hint lines add
  height. (Ruling: a scrolling column over a larger default window, which would not fit small
  screens.) The column keeps upstream's width; its status and statistics lines wrap to the column
  instead of being cut off at its edges.
- L11. Everything else keeps upstream's layout; the buttons sit inside the existing field widgets.
  A hint never changes the width of its field or of the field beside it: Neutron's From/To and
  Galaxy's Source/Destination columns stay equal.

### First test version

- T1. Smoke check: the app starts isolated (redirected home, `../../how-to/run.md`) on a fake
  journal that places the commander in a system Spansh does not know; Neutron Highway, Galaxy
  Plotter, Custom Route and System Info are inspected in screenshots at the default window size;
  the app's log holds no `Traceback`.
- T2. Live check: the Spansh check runs once against the real service for a known system (`Sol`)
  and for an invented name at real coordinates (`../../how-to/run.md`), and the sentences match L4.
- T3. No executable is built (ruled by the commander 2026-11-17: the pytest suite is the
  verification, `../../../CLAUDE.md`). The crew relay is out of scope: crew capture runs locally,
  sync stays off without a group.

## Non-goals

- Following a route regardless of the active tab, and every other defect in
  `../../gui-redesign/00-brief.md` section 4 beyond L10: the GUI redesign decides them.
- Replacing upstream's start-system defaults when Calculate is clicked with an empty From field.
- Changing the updater's target (ruled 2026-11-10, handover).

## Error handling

All failures are sentences in the hint (L4) or a warning dialog (L9); none raises into Tk. A
destroyed widget at the time a result arrives is ignored.

## Testing

- The check (L4) is a pure function of name, coordinates and an injected `call`; each branch,
  including both failure points and the nearest-is-current case, has a test with faked Spansh
  answers. No test uses the network.
- The coordinate format (L8) and the location read (L1) are tested directly.
- The button behaviour (fill, hint, stale results, hint clearing, coordinate fill, failure dialogs)
  runs against real customtkinter widgets with an injected, synchronous runner instead of a thread.
- A source-level test pins each hook call in its upstream builder.
