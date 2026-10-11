# Handover — EDMRN

## Where things stand

Two finished, reviewed branches are stacked on `main`. Neither is merged.

- **`feature/api-hardening`** (12 commits on `1018269`) hardens the API layer:
  - every live external call goes through `edmrn/ext/api.py`;
  - plotting falls back to the nearest known system when the start is unknown to Spansh;
  - failures are readable.

  Spec `../superpowers/specs/2026-10-11-api-hardening-design.md`. Verified live 2026-10-12
  (`../how-to/run.md`).
- **`feature/neutron-resume`** (11 commits on `feature/api-hardening`) saves neutron routes and
  resumes them:
  - every plotted neutron route is saved to `edmrn.db` with its progress;
  - the Neutron tab has a "Saved routes" list with Resume and Delete;
  - resuming follows where the commander is.

  Spec `../superpowers/specs/2026-10-11-neutron-route-resume-design.md`, ADR-0004. Verified end to end
  on real Tk with faked Spansh. No human has looked at the window.

214 tests pass (`.venv/Scripts/python.exe -m pytest`, network blocked in tests). No linter is
configured. The location feature (feature 1) has an accepted spec and plan,
`../superpowers/plans/2026-10-11-journal-location.md`, and is not executed.

## Start here

1. Read `../../CLAUDE.md`, ADR-0001/0002/0004 (`../architecture/decisions/`),
   `../architecture/overview.md` (the upstream hook points and data flows) and
   `../knowledge/upstream-baseline.md`.
2. Ask the commander whether both branches should be merged into `main`. Merge
   `feature/neutron-resume`; it contains the API branch. There is no `origin`.
3. Look at the GUI once: the Neutron tab's "Saved routes", and a plot from an unknown start.
4. Execute the location plan subagent-driven, on a branch. Its Task 6 places the location install
   lines directly after `install_edmrn_neutron_resume(self)` in `app.py`.

## Open decisions

- **Merge** of the two branches (above).
- **Transport for the friend exchange.** Research first (`../cowork/BACKLOG.md` items 1–3, not
  started), then an ADR. No transport code before that ADR.
- **Own GitHub fork as `origin`.** The commander provides it later. Upstream PRs (rows marked "yes"
  in `../architecture/overview.md`) and the updater's target wait for it.

## Preconditions before friend data enters `edmrn.db` (from the neutron review)

The store validates types and numbers; it does not yet validate content. The exchange feature must
first add the following:
- bounds on names, distances and ranges;
- a character whitelist (NUL, ANSI escapes and bidi characters are accepted today);
- rejection of unknown keys (extra waypoint keys are silently dropped today);
- strict `route_id` types;
- a stable record identity. `INTEGER PRIMARY KEY` reuses ids after a delete, so ids must not leave
  the process.

## Follow-ups from the neutron-resume review (deferred, none blocking)

- Test gap: no test fails if `_snapshot = snapshot` in the progress branch of `NeutronAutosave.check`
  is removed. Pin it by changing a status, ticking once, then counting `update_neutron_progress`
  calls over further unchanged ticks.
- Reloading the same upstream CSV backup inserts a duplicate saved route each time.
- Resuming while a Spansh calculation runs: the later result overwrites the resumed route. A
  second replot bypasses the disabled button.
- On continue, advance or back, the From/To fields are not set.
- Corruption that appears mid-session logs a warning every 2 s and never unmaps.
- The 5 s SQLite busy timeout blocks the Tk thread while another process holds a lock.
- Neutron `_key` does not strip spaces, `-` and `_` like upstream's `normalize`. The cost is "ask"
  instead of "advance".

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
