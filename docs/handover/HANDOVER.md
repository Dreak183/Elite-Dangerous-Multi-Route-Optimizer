# Handover — EDMRN

## Where things stand

Branch `feature/api-hardening` (not yet merged into `main`) holds the API hardening, 12 commits on
top of `1018269`. 144 tests pass (`.venv/Scripts/python.exe -m pytest`); the tests block network
access. No linter is configured.

Done:
- **API hardening** (spec `../superpowers/specs/2026-10-11-api-hardening-design.md`, plan
  `../superpowers/plans/2026-10-11-api-hardening.md`):
  - Every live external call goes through `edmrn/ext/api.py` (binding rule in
    `../architecture/patterns.md`).
  - Neutron and Galaxy plot from the nearest known system when the start is unknown to Spansh.
  - Readable failures everywhere.
  - Verified live on 2026-10-12 with the procedure in `../how-to/run.md`; the GUI was not looked
    at.
- **Neutron save/resume:** spec Accepted, plan `../superpowers/plans/2026-10-11-neutron-route-resume.md`,
  ADR-0003. Not executed. Its Task 2 test is already adapted to the rebuilt `calculate_route`.
- **Location, feature 1:** spec Accepted, plan `../superpowers/plans/2026-10-11-journal-location.md`.
  Not executed.

## Start here

1. Read `../../CLAUDE.md`, ADR-0001/0002/0003 (`../architecture/decisions/`),
   `../architecture/overview.md` (the upstream hook points) and `../knowledge/upstream-baseline.md`.
2. Ask the commander whether to merge `feature/api-hardening` into `main`. There is no `origin`,
   so a PR is not possible.
3. Execute the neutron plan, then the location plan, subagent-driven, on a branch.

## Open decisions

- **Merge of `feature/api-hardening`** (above).
- **Transport for the friend exchange.** Research first (`../cowork/BACKLOG.md` items 1–3, not
  started), then an ADR. No transport code before that ADR.
- **Own GitHub fork as `origin`.** The commander provides it later. Upstream PRs (rows marked "yes"
  in `../architecture/overview.md`) and the updater's target wait for it.

## Follow-ups from the API-hardening reviews (deferred, none blocking)

- Autocomplete still sends upstream's ineffective `sort` (`system_autocomplete.py`). Spansh then
  ranks long procedural prefixes badly (`../../../knowledge/bulk-data.md`).
- `api.call`:
  - a tuple or `None` timeout raises a raw `TypeError`;
  - huge JSON ints raise `OverflowError` in `_is_number`.
- An EDAstro 4xx is hidden like a 404. It should note everything except 404.
- System Info runs its sub-requests with retries one after another, about 3 min worst case.
  Consider `retries=0` for the sub-parts.
- Raw exception text remains on hard-to-reach upstream paths:
  - the Galaxy worker exception (also relabels the button "🚀 Calculate Exact Route");
  - Nearest's outer `Failed to search` and `Failed to display results`;
  - `update_ui` with non-dict list elements.
- The updater:
  - an unparseable `tag_name` still says "up to date";
  - the auto and manual checks share state across threads.
- The text "Unexpected error talking to …" is hard-coded in `app.py`, `neutron.py` and `updater.py`.
  Use `str(ApiFailure(service, "unexpected"))`.
- `neutron_manager` has its own journal-coords check; it should use `spansh.journal_start_coords`.
- The live-check expectations assume that Spansh does not know `Blae Phoe XT-A e1459`.

## Traps

- Every path derives from `Path.home()`, and `edmrn.logger` writes there at import time. Tests rely
  on `tests/conftest.py` (`../architecture/patterns.md`). Probes redirect
  `HOME`/`USERPROFILE`/`APPDATA`/`LOCALAPPDATA` (`../how-to/run.md`).
- `galaxy_handler.py` and `journal_handler.py` are dead copies; the live Galaxy code is in
  `app.py`. Fixes in the dead files have no effect.
- Importing `edmrn.app` replaces `sys.excepthook`; tests that import it restore the hook.
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
