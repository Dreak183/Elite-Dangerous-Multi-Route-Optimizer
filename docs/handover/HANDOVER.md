# Handover — EDMRN

## Where things stand

- `main` (8653859) holds the API hardening and neutron save/resume (merged 2026-10-12).
- **`feature/journal-location`** (10 commits on `main`) is finished and reviewed, not merged. It is
  the location feature (spec `../superpowers/specs/2026-10-11-journal-location-design.md`):
  - upstream's journal monitor follows carrier jumps and game restarts and reads only complete lines;
  - an idle monitor stays cheap with thousands of journal files;
  - a class-level listener hook feeds `edmrn/ext/location.py`;
  - the header shows `System · Station` or `System · Body`;
  - `app.location_tracker` is the location source for later features.

  The final whole-branch review executed probes and 28 mutations; its fix wave was re-reviewed as
  ready. 278 tests pass (`.venv/Scripts/python.exe -m pytest`); `tests/test_neutron_resume_gui.py`
  sometimes skips 5 tests with Tk's "can't find usable init.tcl" (environment, intermittent). No
  linter is configured. A smoke start with a fake journal showed `Sol · Abraham Lincoln` in the
  header variable; **no human has looked at the window** yet.
- **Crew features** (the commander's priority): transport is decided in ADR-0005. The crew design
  was proposed in chat on 2026-10-12 and awaits the commander's approval (below); no crew spec
  exists yet.

## Start here

1. Read `../../CLAUDE.md`, ADR-0001/0002/0004/0005 (`../architecture/decisions/`),
   `../architecture/overview.md` and `../knowledge/upstream-baseline.md`.
2. Ask the commander to merge `feature/journal-location` into `main` (there is no `origin`).
3. Ask the commander to look at the GUI once: header location, Neutron "Saved routes", a plot from
   an unknown start.
4. Crew work: get the design approved, then write Spec A (capture), Spec B (sync), Spec C (window),
   each spec → plan → subagent-driven execution.

## Crew requirements the commander gave (2026-10-12) — move into Spec A/B/C, then delete here

- One fixed group, with named expeditions as filters. Own status is shared automatically, with a
  "pause sharing" switch.
- A visited system is tracked but counts as **explored** only when all bodies are found by FSS
  (`FSSAllBodiesFound`). Per explored system: number of planets, landable planets, and per planet
  the FSS-visible signals: biological, geological, xeno (Thargoid), Guardian, human, other.
- Automatic finds: biosignals, stellar phenomena, valuable bodies; manual finds with a note.
  Later: a local inbound API through which other tools (e.g. Observatory scripts) post finds.
- Status per commander: ship and loadout (EDSY link), fuel, hull, current or last known location.
- Window: commander cards in the header; tabs "Explored systems", "Current route", "Reported
  finds"; a live chat; notes on systems.
- Proposed decomposition (awaiting approval): Spec A local capture into the event log
  (journal + `Status.json`, deterministic ids), Spec B sync (identity, invites, Worker, catch-up,
  Discord webhook), Spec C window. Open research for A: `../cowork/BACKLOG.md`.

## Open decisions

- **Merge** of `feature/journal-location`.
- **Crew design approval** (above), then the sub-domain for the Worker.
- **Own GitHub fork as `origin`.** The commander provides it later. Upstream PRs (rows marked "yes"
  in `../architecture/overview.md`) and the updater's target wait for it.

## Preconditions before friend data enters `edmrn.db` (from the neutron review)

The store validates types and numbers; it does not yet validate content. Spec B must add:
- bounds on names, distances and ranges;
- a character whitelist (NUL, ANSI escapes and bidi characters are accepted today);
- rejection of unknown keys (extra waypoint keys are silently dropped today);
- strict `route_id` types;
- a stable record identity. `INTEGER PRIMARY KEY` reuses ids after a delete, so ids must not leave
  the process (ADR-0005's `event_id` is that identity).

## Follow-ups from the location reviews (deferred, none blocking)

- The monitor's "folder was empty at start" flag is also set by a transient failed lookup; the next
  lookup then replays the newest finished journal as live.
- `os.stat(journal_path)` in the rotation gate is unguarded; an error delays rotation by ~4.6 s via
  the retry path.
- `run()`'s error path still sleeps 5 s, so `stop()` can take 5 s after an exception.
- Test gaps: the 30 s rotation safety net; the I1 source-level test checks the keyword, not its
  value (`config.journal_path`).
- Galaxy Plotter and Neutron read the start system from upstream's `get_current_system()` (newest
  journal file only), not from the tracker.
- `app.py:401` does not `.strip()` the configured journal path like the other start sites.
- The tracker follows every commander; upstream filters jumps by the selected commander.
- After a cold start the journal folder is not re-read when the path changes at runtime.
- Listener errors log `str(e)` without traceback or listener name.

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
- `galaxy_handler.py` and `journal_handler.py` are dead copies; the live Galaxy and jump code is in
  `app.py`. Fixes in the dead files have no effect.
- Importing `edmrn.app` replaces `sys.excepthook`; tests that import it restore the hook.
- Journal listeners and tracker observers run on the monitor or cold-start thread; Tk work goes
  through `root.after`.
- Spansh is slow and returns 502s under load (`../../../knowledge/bulk-data.md`). Live checks may
  need several minutes and retries; unit tests must never use the network.
- The Neutron tab is built by `ui_components.create_neutron_tab`; `app._create_neutron_tab` is dead
  code.
- Upstream files are CRLF in the working tree (git autocrlf). Keep them CRLF, and do not "tidy" them
  (ADR-0002).
- `uv venv` without `--python 3.13` picks 3.14.
- `.remember/` is the remember plugin's local store, `.superpowers/` the SDD scratch. Leave both
  untracked.

## Where the knowledge lives

- Rules and map: `../README.md`. Architecture, upstream hook points, decisions: `../architecture/`.
- What upstream does, including its defects: `../knowledge/upstream-baseline.md`.
- Feature specs (authority over plans): `../superpowers/specs/`. Plans: `../superpowers/plans/`.
- Running, testing, probing: `../how-to/run.md`. Upstream merges: `../how-to/upstream-sync.md`.
- Shared facts: `../../../knowledge/README.md` (Spansh API: `bulk-data.md`; location events:
  `reference-data.md` §6; SQLite and folder mtime: `local-ipc-and-storage.md`; crew transport:
  `group-sync-transport.md`; route formats: `ed-data-sharing.md`).
