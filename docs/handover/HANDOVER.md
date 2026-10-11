# Handover — EDMRN

## Where things stand

Project created 2026-10-11 as a clone of upstream (161 commits, `main` = upstream `1e8cda4`),
remote `upstream` = NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer, plus the hub template as
one commit on top. No own code, no spec, no tests yet; the environment has not been set up or run.

## Start here

1. Read `../../CLAUDE.md`, then ADR-0001 and ADR-0002 in `../architecture/decisions/`.
2. Get to know upstream before designing: set up the environment
   (`uv venv`, `uv pip install -r requirements.txt`), start the app, read `edmrn/journal*.py`,
   `route_management.py`, `autosave.py`, `visit_history.py`. Several requested features may
   already exist in part; record findings in `../knowledge/` (what upstream already does).
3. Run `superpowers:brainstorming` for the first part; the spec goes to `../superpowers/specs/`.

## Open decisions

- **Feature order.** Four features requested: journal location, saved-route library with UI,
  system bookmarks, friend exchange. Suggested: location → bookmarks/route library (local, share one
  storage design) → exchange. To settle in brainstorming.
- **Transport for the friend exchange:** P2P preferred, own server acceptable. Needs research
  first (`../cowork/BACKLOG.md` items 1–3), then an ADR. No transport code before that ADR.
- **Local storage for routes and bookmarks:** upstream has no database; SQLite vs. upstream's
  existing file-based storage. Decide in the spec.
- **Own GitHub fork as `origin`:** not set up; needed before PRs to upstream or distributing
  builds to friends. The commander provides the URL.

## Traps

- Upstream derives the journal folder from `~` — tests must redirect `HOME`/`USERPROFILE`.
- `setup.py` requires Python ≥ 3.13 while upstream's README says 3.12+; use 3.13.
- Do not "tidy" upstream files — every diff there is merge-conflict surface (ADR-0002).

## Where the knowledge lives

- Rules and map: `../README.md`; architecture and decisions: `../architecture/`
- Upstream merge procedure: `../how-to/upstream-sync.md`
- Project notes on external facts: `../knowledge/`; shared facts: `../../../knowledge/README.md`
