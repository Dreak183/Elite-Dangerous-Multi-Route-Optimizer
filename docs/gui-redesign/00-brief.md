# Design brief: EDMRN GUI redesign

Read this first. It tells a designer what EDMRN is, who uses it and how, what the redesign has to
cover, and what the result must contain so that it can be implemented without further questions.
The detail of every screen is in `reference/`; pictures of the present state are in `screenshots/`.

## 1. The product

EDMRN is a desktop companion tool for the space game *Elite Dangerous*. The game is played in a
galaxy of 400 billion star systems; travelling means "jumping" from system to system, each jump
limited by the ship's jump range in light years (LY). EDMRN plans such journeys and follows the
commander along them:

| Job | What the commander wants | Where it lives today |
|---|---|---|
| Visit many systems in the shortest order | Load a list of systems (CSV), get an optimised order, tick them off while flying | tabs Route Optimization, Route Tracking |
| Cross long distances fast | Plot a route along neutron stars, which multiply the jump range | tab Neutron Highway |
| Plot with the exact ship | Route computed from the ship's loadout | tab Galaxy Plotter |
| Build a route by hand | Type systems one by one, reorder, optimise | tab Custom Route |
| Look up a system | Bodies, stations, history of a system from community databases | tab System Info |
| See the next stop without leaving the game | A small always-on-top window over the game | Overlay |
| Travel with friends | Who is where in which ship, what was found, the leader's route, chat | Settings > Crew... (setup only) |

The tool reads the game's journal files, so it knows the current system, ship, fuel and every jump
without input. Route data comes from the public services Spansh, EDSM and EDAstro.

## 2. Users and situation of use

The points on monitors and attention are the working assumption of this brief, not a recorded
statement of the commander; correct them before the design starts if they do not fit.

- One commander and a handful of friends. Experienced players; game terms need no explanation.
- **The game runs at the same time.** EDMRN sits on a second monitor or behind the game window and
  is looked at for seconds between jumps. A jump cycle is 30–60 s; the recurring question is "what
  is the next system" and "is the name in my clipboard".
- The overlay is used when there is one monitor only: it floats over the game and must cover as
  little of the view as possible.
- Play sessions are long and dark-room; the game's own interface is orange on black.
- Planning (entering start and destination, loading a file, changing settings) happens before
  departure, with full attention, keyboard and mouse.
- Windows is the main platform; the code also runs on Linux.

Two modes follow from that, and the present GUI does not separate them: **planning** (many inputs,
rare) and **flying** (almost no input, glanceable, constant).

## 3. Scope of the redesign

In scope: the main window with all eight tabs, every dialog and secondary window, the overlay, and
a place for the crew features that have data but no interface (`reference/05-…`).

The redesign may regroup, merge, rename and reorder everything. It must not drop a function listed
in `reference/` without saying so: the deliverable lists every element ID it removes or merges, with
the reason.

Out of scope: new route algorithms, changes to data formats, the relay server, the game itself.

## 4. What is known to be wrong today

Facts, not taste; details and sources in the reference files.

- **Features are invisible.** The whole crew function is reached through one button on the last
  card of the last tab (`screenshots/08-settings.png`, `09-crew-dialog.png`). "Saved routes" is a
  button without any hint of how many routes are saved.
- **Content is clipped at the default window size** (1196×730 logical px): the "Find Nearest
  System" row in Neutron Highway is cut off at the bottom (`screenshots/03-neutron-highway.png`).
- **Eight top-level tabs mix three kinds of things**: four ways to make a route, one way to follow
  it, one lookup, and two utility pages (Log, Settings).
- **The same function exists several times** with separate controls and separate state: range
  input, previous/next/copy navigation and "optimize" exist per route type.
- **Identity is shown inconsistently**: the header and the crew dialog can show different commander
  names at the same moment (`08-settings.png` vs `09-crew-dialog.png`).
- **The footer is permanent advertising and links** (donations, GitHub, Discord, About, Manual) and
  takes a full row of every screen.
- **The Galaxy Plotter does not do what its inputs promise**: the service it calls ignores the ship
  data (`reference/03-…`). Design it as "route from A to B with a range"; an exact, ship-aware
  plotter may follow as a separate function.
- **Following a route depends on which tab is open.** A neutron or plotted route advances on a
  jump only while its own tab is the active one; the overlay's previous/next/copy buttons do
  nothing after a route is loaded until a tab is clicked (`reference/02-…`, `03-…`).
- **Feedback appears where the user is not looking.** Route Tracking has no message area; copy
  confirmations and detected jumps are written to a console on the Route Optimization tab.
- **Controls without effect**: "Borderless Mode", fuel "Critical Level", fuel "Volume", the fuel
  "Status:" line, the "Optional Columns" dialog, the auto-save card (nothing is written). The theme
  editor exists but cannot be opened. The user manual describes shortcuts that do not exist; the
  only one is Ctrl+O (overlay), and only while EDMRN has focus (`reference/01-…`, `04-…`).
- **Labels that mislead**: the overlay's copy button shows the current system and copies the next
  one; status texts name buttons that do not exist ("Calculate Exact Route", "Fetch Data"); the tab
  "Log" is a table of scanned bodies, not a log.
- **Nothing survives a restart**: the active route is not restored, the overlay position is not
  saved, notes in the "Log" table are lost on the next scan.
- **Errors are silent or blocking**: optimizer errors reach only the console; "Cancel" in the
  progress dialog does not stop the work; the manual update check and "Find Nearest" freeze the
  window for up to 10 s.

The redesign is the occasion to decide for each of these whether the function is repaired, merged
or dropped; the design should not carry a dead control forward.

## 5. Requirements the design must meet

1. **The flying state is the default view.** Current system, next system, remaining jumps and
   distance, and "copy next system" are reachable without a tab change, for whichever route is
   active — list route, neutron route or plotted route.
2. **One active route at a time**, with a visible type and source; switching routes is explicit.
3. **Location, ship and range are global facts**, shown once: commander, system, ship, fuel, laden
   jump range, supercharge state.
4. **Crew is a first-class area**, not a settings dialog: member list with system and ship, the
   connection state, and room for chat, the leader's route and finds (`reference/05-…` marks what
   has data today).
5. **Every state is designed**, not only the populated one: empty (nothing loaded), running (a
   Spansh request takes 5–60 s and can fail), error (texts are quoted in the reference), offline,
   game not running, journal folder not found.
6. **Long content**: system names up to about 30 characters, e.g. `Blae Phoe XT-A e1459`,
   `Eol Prou BO-S c4-184`; routes from 2 to several thousand rows; crew of 2–10 members.
7. **Keyboard**: every primary action has a visible default action or shortcut.
8. **Themes stay.** Users pick a colour theme; a theme is a small set of colour roles
   (`reference/04-…`, theme section). The design defines its colours as such roles — background,
   surface, border, primary, text, muted, success, warning, danger — and nothing hard-coded.
9. **Text from friends is untrusted**: shown as plain text, length-bounded, never as formatted
   markup, links or images.
10. **English interface text.**

## 6. Technical frame for implementation

The application is Python with **customtkinter** (Tk). The table widget is tksheet, the 3D route
map is a matplotlib canvas. A design is directly implementable when it stays within what these can
draw:

| Available | Not available |
|---|---|
| Frames with solid fill, 1-colour border, rounded corners | Shadows, blur, glass, gradients (only as pre-rendered images) |
| Buttons, entries, multi-line text, dropdowns, checkboxes, switches, sliders, progress bars, segmented buttons, tab views, scrollable frames | Per-element transparency; only a whole window can be translucent |
| Tables with fixed columns, per-cell colours, sorting, selection | Smooth animation and transitions; state changes are instant |
| Any installed font; icons as PNG or font glyphs | Hover effects beyond a colour change; rich tooltips need extra work |
| Extra top-level windows, always-on-top, borderless, movable | Docking, split panes with free drag (possible, costly) |
| Canvas drawing for a custom chart or map | HTML/CSS layout rules (no flex-wrap, no media queries) |

Layout is a grid of rows and columns with weights; the window is resizable from 1200×700 logical
px upward and is often used at exactly that size. Design for 1200×700 first, then say how each
region grows.

## 7. What the design deliverable must contain

1. **Information architecture**: the areas of the application and what moved where, as a table from
   every element ID in `reference/` to its new place (or "removed, because …").
2. **Screens**: each area in the states empty, running, populated and error, at 1200×700 and at
   1920×1080.
3. **The flying view** and the **overlay** in its three sizes (Small, Medium, Large; system names
   are cut to 18, 25 and 30 characters today), each with a list route, a neutron route and no
   route.
4. **Crew area**: not in a group, group with members online and offline, connection lost.
5. **Component sheet**: every component with its states (normal, hover, pressed, focused, disabled,
   error), sizes in px, spacing scale, type scale.
6. **Colour roles** with values for at least the default dark theme, and the rule for deriving a
   theme from a handful of user-chosen colours.
7. **Interaction notes** per screen: what is the default action, what is disabled when, what
   happens on Enter, Escape, double-click, right-click.
8. **Exact interface text** for every label, button, placeholder, empty state and error.

## 8. Vocabulary

| Term | Meaning |
|---|---|
| Commander (CMDR) | The player's character; one account can have several |
| System | A star system; the unit of travel. Has a name and x/y/z coordinates in LY |
| Jump | One hyperspace hop between two systems |
| Jump range | Longest jump the ship can make, in LY. *Laden*: with current cargo and fuel |
| Neutron star, supercharge | Flying through a neutron star's jet multiplies the next jump ×4 (white dwarf ×1.5) |
| FSD | Frame shift drive, the jump engine. *FSD injection* is a consumable boost ×1.25/×1.5/×2 |
| Fuel scoop, scoopable star | Refuelling at stars of class K G B F O A M |
| Body | Star, planet or moon inside a system |
| Exobiology | Scanning plant life on planets; each species needs three samples |
| Journal | Log files the game writes; EDMRN's source of live data |
| Spansh, EDSM, EDAstro | Community web services with routes and system data |
| Waypoint | One stop of a route |
| Crew, group, leader, invite, relay | `reference/05-crew-features-without-ui.md` |
