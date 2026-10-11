# Handover — EDMRN

## Where things stand

`main` = upstream `1e8cda4` (v3.3.0) + the hub-template scaffold + the upstream baseline commit.
There is no own code, no spec and no tests yet. `.venv` (Python 3.13, git-ignored) is set up per
`../how-to/run.md`. The app starts cleanly in an isolated home (verified 2026-10-11). The upstream
survey is done; its results are in `../knowledge/upstream-baseline.md`.

## Start here

1. Read `../../CLAUDE.md`, ADR-0001/0002 (`../architecture/decisions/`), and
   `../knowledge/upstream-baseline.md`.
2. Run `superpowers:brainstorming` for the first part. The spec goes to `../superpowers/specs/`.

## Open decisions

- **Feature order.** The four features are journal location, a saved-route library with UI, system
  bookmarks, and friend exchange. Suggested order: location → bookmarks and route library (local,
  sharing one storage design) → exchange. To be settled in brainstorming.
- **Upstream defects under our features** (`upstream-baseline.md`):
  - the idle busy-spin in the journal tail;
  - no journal file rotation;
  - live `Location`/`CarrierJump` are ignored;
  - autosave writes nothing.

  Location builds directly on the first three. Fix them in `journal.py` as small diffs offered
  upstream as PRs, or work around them from `edmrn/ext/`? ADR-0001 rules out a second journal reader
  unless a superseding ADR allows it. Decide in the spec.
- **Transport for the friend exchange.** P2P is preferred; an own server is acceptable. Research
  comes first (`../cowork/BACKLOG.md` items 1–3, not started), then an ADR. No transport code before
  that ADR.
- **Local storage for routes and bookmarks.** SQLite, or JSON files like upstream's (the
  `slef_store` pattern)? Decide in the spec.
- **Own GitHub fork as `origin`.** Not set up. It is needed for upstream PRs and for builds that
  friends can use; the updater checks upstream's releases. The commander provides the URL.

## Traps

- Every path derives from `Path.home()`: redirect `HOME`/`USERPROFILE`/`APPDATA`/`LOCALAPPDATA`
  (recipe in `../how-to/run.md`).
- A running `JournalMonitor` burns a full CPU core when idle and keeps the process alive. In probes
  and tests, stop it, or end with `os._exit` and run Python with `-u`.
- `uv venv` without `--python 3.13` picks 3.14.
- Do not "tidy" upstream files. Every diff there is merge-conflict surface (ADR-0002).
- `.remember/` is the remember plugin's local store. Leave it untracked.

## Where the knowledge lives

- Rules and map: `../README.md`. Architecture and decisions: `../architecture/`.
- What upstream already does: `../knowledge/upstream-baseline.md`.
- Running and probing: `../how-to/run.md`. Upstream merges: `../how-to/upstream-sync.md`.
- Shared facts: `../../../knowledge/README.md`. Location events: `reference-data.md` §6.
