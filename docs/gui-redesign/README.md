# GUI redesign: preparation package

Everything a designer (Claude Design) needs to redesign EDMRN's interface so that the result can be
implemented directly. It describes function, not looks. It is a snapshot of the GUI at commit
`17bf938` (2026-11-10) and is deleted once the redesign is implemented.

| File | Holds |
|---|---|
| [`00-brief.md`](00-brief.md) | Product, users, scope, known functional problems, requirements, technical frame, what the design deliverable must contain, vocabulary. **Start here.** |
| [`reference/01-shell-and-route-optimization.md`](reference/01-shell-and-route-optimization.md) | Window, header, tab bar, footer, startup; tabs Route Optimization and Custom Route; shared dialogs |
| [`reference/02-route-tracking-and-overlay.md`](reference/02-route-tracking-and-overlay.md) | Tab Route Tracking, its dialogs, backups, the in-game overlay, fuel and exobiology displays |
| [`reference/03-neutron-galaxy-system-info.md`](reference/03-neutron-galaxy-system-info.md) | Tabs Neutron Highway (with saved routes and range panel), Galaxy Plotter, System Info |
| [`reference/04-log-settings-crew-dialogs-themes.md`](reference/04-log-settings-crew-dialogs-themes.md) | Tabs Log and Settings, the Crew dialog, themes and colour roles, About, Manual, message dialogs, update check, shortcuts, sounds |
| [`reference/05-crew-features-without-ui.md`](reference/05-crew-features-without-ui.md) | Crew data and functions that exist or are decided and have no interface |
| [`screenshots/`](screenshots/README.md) | Captures of every tab and the main dialogs in the empty state, and an index of the older captures with data loaded (`../../screenshots/`) |

## How the reference files are built

Each screen has: purpose, present layout, a table of every element with a stable ID (`NEU-07`,
`OVL-21`, …), exact label, type, behaviour, enabling condition and source line; inputs and
validation; data shown with units and sizes; states with the exact message texts; automatic
behaviour; user flows; observed functional problems. The IDs are the contract between design and
implementation: the design says where each ID went, the implementer finds its code by the source
column.

The files were derived by reading the code, not by operating the application. Lines marked
`UNVERIFIED:` say what could not be determined that way.

## Handing it to the designer

Give the designer this whole folder plus `../../screenshots/`. A prompt that works:

> Redesign the interface of EDMRN, a desktop companion for Elite Dangerous. `00-brief.md` holds the
> requirements, the technical frame and the list of deliverables; `reference/` lists every existing
> function with an element ID; `screenshots/` shows the present state. Keep every function unless
> you list its ID as removed or merged, with the reason. Start with the information architecture
> and the flying view, and show them before detailing the remaining screens.
