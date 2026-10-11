# Handover — EDMRN

## Where things stand

`main` = upstream `1e8cda4` (v3.3.0) plus the scaffold, the upstream baseline and the location spec.
There is no own code and there are no tests yet. `.venv` (Python 3.13, git-ignored) is set up per
`../how-to/run.md`. The app starts cleanly in an isolated home (verified 2026-10-11). Brainstorming
for feature 1 is done. Its spec is
`../superpowers/specs/2026-10-11-journal-location-design.md` (Status: Draft), and the commander has
been asked to review it.

## Start here

1. Read `../../CLAUDE.md`, ADR-0001/0002 (`../architecture/decisions/`),
   `../knowledge/upstream-baseline.md` and the location spec.
2. If the commander has approved the spec, set its Status to Accepted and run
   `superpowers:writing-plans`. The plan goes to `../superpowers/plans/`. If the spec has not been
   approved, ask for the review first.

## Open decisions

- **Settled in brainstorming (in the spec).**
  - Feature order: location → bookmarks/route library → exchange.
  - Approach A: fix the journal-tail defects in `journal.py` and offer them upstream as PRs; add a
    class-level listener hook; keep the location state machine in `edmrn/ext/location.py`.
- **Transport for the friend exchange.** P2P is preferred; an own server is acceptable. Research
  comes first (`../cowork/BACKLOG.md` items 1–3, not started), then an ADR. No transport code before
  that ADR.
- **Local storage for routes and bookmarks.** SQLite or JSON files (the `slef_store` pattern)?
  Decide in the feature-2 spec, together with upstream's autosave defect.
- **Own GitHub fork as `origin`.** Not set up. It is needed for the upstream PRs from feature 1 and
  for builds that friends can use. The commander provides the URL.

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
- Feature specs (authority over plans): `../superpowers/specs/`.
- Running and probing: `../how-to/run.md`. Upstream merges: `../how-to/upstream-sync.md`.
- Shared facts: `../../../knowledge/README.md`. Location events: `reference-data.md` §6.
