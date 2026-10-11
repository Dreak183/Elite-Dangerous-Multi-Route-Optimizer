# Handover — EDMRN

## Where things stand

`main` is upstream `1e8cda4` (v3.3.0) plus our commits. 37 tests pass
(`.venv/Scripts/python.exe -m pytest`). No linter is configured. `.venv` is Python 3.13
(`../how-to/run.md`).

Done or ready:
- **Spansh fix for Neutron** (`23903c2`): readable errors, retries, nearest-known start when the
  journal start is unknown. Verified live.
- **API hardening for every live call site:** spec Draft,
  `../superpowers/specs/2026-10-11-api-hardening-design.md`. The design was approved in chat
  (English messages; Tk-thread fixes in the touched flows). The commander has been asked to review
  the written spec. The survey of call sites is in `../knowledge/upstream-baseline.md`
  ("External APIs").
- **Neutron save/resume:** spec Accepted, plan
  `../superpowers/plans/2026-10-11-neutron-route-resume.md`, ADR-0003 (SQLite). Not executed.
- **Location, feature 1:** spec Accepted, plan `../superpowers/plans/2026-10-11-journal-location.md`.
  Not executed.

Order agreed with the commander: API hardening → neutron save/resume → location.

## Start here

1. Read `../../CLAUDE.md`, ADR-0001/0002/0003 (`../architecture/decisions/`) and
   `../knowledge/upstream-baseline.md`.
2. If the API-hardening spec is approved, set its Status to Accepted, run `superpowers:writing-plans`
   for it, and execute subagent-driven. It rebuilds `edmrn/ext/spansh.py` on a new `ext/api.py`.
3. Then execute the neutron plan; its Task 2 tells the implementer to adapt to the rebuilt
   `calculate_route`. Then execute the location plan.

## Open decisions

- **API-hardening spec review** (above).
- **Transport for the friend exchange.** Research first (`../cowork/BACKLOG.md` items 1–3, not
  started), then an ADR. No transport code before that ADR.
- **Own GitHub fork as `origin`.** The commander provides it later. The upstream PRs (rows marked
  "yes" in `../architecture/overview.md`) and the updater's target wait for it.

## Traps

- Every path derives from `Path.home()`, and `edmrn.logger` writes there at import time. Tests rely
  on `tests/conftest.py` (`../architecture/patterns.md`). Probes redirect
  `HOME`/`USERPROFILE`/`APPDATA`/`LOCALAPPDATA` (`../how-to/run.md`).
- `galaxy_handler.py` and `journal_handler.py` are dead copies; the live Galaxy code is in
  `app.py`. Fixes in the dead files have no effect.
- Spansh is slow and returns 502s under load (`../../../knowledge/bulk-data.md`). Live checks may
  need several minutes and retries; unit tests must never use the network.
- The Neutron tab is built by `ui_components.create_neutron_tab`; `app._create_neutron_tab` is dead
  code.
- Upstream files are CRLF. Keep them CRLF, and do not "tidy" them (ADR-0002).
- `uv venv` without `--python 3.13` picks 3.14.
- `.remember/` is the remember plugin's local store. Leave it untracked.

## Where the knowledge lives

- Rules and map: `../README.md`. Architecture, upstream hook points, decisions: `../architecture/`.
- What upstream does, including its defects: `../knowledge/upstream-baseline.md`.
- Feature specs (authority over plans): `../superpowers/specs/`. Plans: `../superpowers/plans/`.
- Running, testing, probing: `../how-to/run.md`. Upstream merges: `../how-to/upstream-sync.md`.
- Shared facts: `../../../knowledge/README.md` (Spansh API: `bulk-data.md`; location events:
  `reference-data.md` §6; SQLite: `local-ipc-and-storage.md` §3.1).
