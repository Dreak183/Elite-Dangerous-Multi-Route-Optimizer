# Handover — EDMRN

## Where things stand

`main` is upstream `1e8cda4` (v3.3.0) plus our commits. 37 tests pass
(`.venv/Scripts/python.exe -m pytest`). No linter is configured. `.venv` is Python 3.13
(`../how-to/run.md`).

Done:
- **Spansh fix** (`23903c2`): the neutron plotter shows Spansh's error reason, retries 502/503/504
  and timeouts, and falls back to the nearest known start system when the journal start is unknown
  to Spansh. It was verified live on 2026-10-11.
- **Location feature, feature 1:** spec Accepted and plan written. Not executed.
- **Neutron save/resume:** spec Draft, `../superpowers/specs/2026-10-11-neutron-route-resume-design.md`,
  together with ADR-0003 (SQLite). The commander has been asked to review the spec.

The order agreed with the commander: Spansh fix → neutron save/resume → location.

## Start here

1. Read `../../CLAUDE.md`, ADR-0001/0002/0003 (`../architecture/decisions/`) and
   `../knowledge/upstream-baseline.md`.
2. If the neutron spec is approved, set its Status to Accepted and run `superpowers:writing-plans`.
   The plan goes to `../superpowers/plans/`. Then execute it subagent-driven.
3. Then execute `../superpowers/plans/2026-10-11-journal-location.md` subagent-driven. Its Task 6
   adds the location install call next to the neutron install call in `app.py`.

## Open decisions

- **Neutron spec review** (above).
- **Galaxy Plotter** has the same unknown-start problem and no fallback. The commander has not
  decided whether it gets the fallback too.
- **Transport for the friend exchange.** Research first (`../cowork/BACKLOG.md` items 1–3, not
  started), then an ADR. No transport code before that ADR.
- **Own GitHub fork as `origin`.** The commander provides it later. The upstream PRs (rows marked
  "yes" in `../architecture/overview.md`) wait for it.

## Traps

- Every path derives from `Path.home()`, and `edmrn.logger` writes there at import time. Tests rely
  on `tests/conftest.py` (`../architecture/patterns.md`). Probes redirect
  `HOME`/`USERPROFILE`/`APPDATA`/`LOCALAPPDATA` (`../how-to/run.md`).
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
