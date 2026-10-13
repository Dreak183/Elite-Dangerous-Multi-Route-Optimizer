# Handover — EDMRN

## Where things stand

- `main` holds API hardening, neutron save/resume and the journal location (merged 2026-10-12)
  and Spec A, crew capture (merged 2026-10-13, fast-forward to `d8ada51`).
  - 598 tests pass (`.venv/Scripts/python.exe -m pytest`). `tests/test_neutron_resume_gui.py`
    sometimes skips 5 tests with Tk's "can't find usable init.tcl" (environment, intermittent;
    they pass when rerun). No linter is configured.
  - All 12 plan tasks were reviewed one by one. The final whole-branch review said "with fixes";
    those fixes are in `b7ddf44` and passed a re-review. An isolated smoke run of the real app
    wrote a signed `visit` to `crew_log` (`../how-to/run.md`).
  - The spec was amended with the implementation rulings (author-bound ids, idempotent
    projection, status adoption, live buffer, locked store, backpressure); ADR-0007 records the
    storage-versus-projection rule.
  - The commander's real `edmrn.db` did not exist yet (checked read-only 2026-10-13), so the first
    run creates schema 2 fresh.
- **No human has looked at the window** since the location and neutron features: header location,
  Neutron "Saved routes", a plot from an unknown start. Crew capture has no UI yet (Spec C).
- Crew features: transport ADR-0005, store layer ADR-0006 and ADR-0007, decomposition
  A capture → B sync → C window (approved).

## Start here

1. Read `../../CLAUDE.md`, ADR-0001/0002/0004–0007 (`../architecture/decisions/`),
   `../architecture/overview.md` and `../knowledge/upstream-baseline.md`.
2. Spec B1, crew sync core (`../superpowers/specs/2026-10-13-crew-sync-core-design.md`, with
   ADR-0008), was accepted by the commander on 2026-10-13. Its plan is
   `../superpowers/plans/2026-10-13-crew-sync-core.md` (15 tasks: Python client 1-10, relay
   11-14, end to end and docs 15). Next: the commander picks the execution method, then
   subagent-driven development on a feature branch. The spec's S10–S12 absorb the received-data
   rulings found during Spec A (stamp plausibility, character whitelist, log volume).
   - The Cloudflare setup (`wrangler login`, `CREW_LEADERS`, `wrangler deploy` to
     `galroute.the-hub.cloud`) happens with the commander once the relay passes its tests; write
     `../how-to/deploy-relay.md` as a step-by-step guide for that session.
3. Then Spec B2 and Spec C (window). Notes for C from Spec A:
   - The manual API runs on the Tk thread only and flushes the whole writer queue (about 1.8 s at
     10,000 drafts).
   - During a lock, an edit right after a report raises `KeyError`.
   - With large journals the startup replay takes seconds before the commander is known, and the
     manual API raises until then.
   - Notes reject newlines (C12, planning ruling 9); decide whether multi-line notes need a `v` 2.

## Crew requirements for Specs B2 and C (commander, 2026-10-12) — move into those specs, then delete

The B1 requirements (group, invites, status, pause) are in the B1 spec.
- Named expeditions as filters inside the one group (B2).
- Window: commander cards in the header; tabs "Explored systems", "Current route", "Reported
  finds"; a live chat; notes on systems; manual finds with a note. Per explored system: planets,
  landable planets, per-planet signals (C).
- Finds are optionally mirrored to the group's Discord through a webhook (ADR-0005) (B2).
- Later: a local inbound API through which other tools (e.g. Observatory scripts) post finds.

## Open decisions

- **Own GitHub fork as `origin`.** The commander provides it later. Upstream PRs (rows marked "yes"
  in `../architecture/overview.md`) and the updater's target wait for it.

## Preconditions before friend data enters `edmrn.db` (from the neutron review)

The neutron store validates types and numbers, not content. For crew data, Spec A's validator
(C12), `insert_received` and ADR-0007 close these; any other friend data must do the same:
- bounds on names, distances and ranges;
- a character whitelist (NUL, ANSI escapes and bidi characters are accepted today);
- rejection of unknown keys (extra waypoint keys are silently dropped today);
- strict `route_id` types;
- a stable record identity. `INTEGER PRIMARY KEY` reuses ids after a delete, so ids must not leave
  the process (ADR-0005's `event_id` is that identity).

## Follow-ups from the crew capture reviews (deferred, none blocking)

- **Tests that cannot fail, or fail weakly:**
  - the stop-wake test catches its mutant only about a quarter of the time;
  - nothing pins the app.py hook as "directly after" the location install;
  - untested: individual commander/hull fill in `adopt`, the 1,000-drop overflow log, "broken key
    logged once", and the Commander event's `Name` key.
- **Status:**
  - The ship group in `adopt` is guarded by `ship` alone (malformed Loadout only).
  - Hull fills individually even when the live ship differs.
  - An unencodable Loadout string drops the whole Loadout.
- **Mapper and replay:**
  - The mapper's coordinate check is looser than the validator's (|v| ≤ 1e7), so such a visit is
    dropped whole instead of losing only its coordinates.
  - An invalid signals draft can leave its biological find alone.
  - The seed hands drafts on outside its lock (affects `seq` order only).
  - A negative `max_files` is not rejected.
- **Key files:**
  - The exists-then-write check has no file lock, so two processes can race.
  - Write failures surface as `OSError` (the writer catches them).
  - The `v` field accepts `true` and `1.0`.
- **Store and log:**
  - `Store._probe` checks only `neutron_routes`.
  - `append_own` does not reject an own journal-kind draft with a non-matching preset id (no
    producer exists).
  - A `sqlite3.Error` during the writer's key-file catch-up is retried per draft (log noise).
- **Plan text:** `../superpowers/plans/2026-10-12-crew-capture.md` predates the review rulings;
  the spec and the code are authoritative.

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
- `sed -i` in Git Bash rewrites CRLF files as LF. Use the Edit tool or a byte-preserving Python
  rewrite for upstream files, and check with `file`.
- `crew_log` holds untrusted bytes from friends (ADR-0007). Show only the validated projections.
- `.remember/` is the remember plugin's local store, `.superpowers/` the SDD scratch. Leave both
  untracked.

## Where the knowledge lives

- Rules and map: `../README.md`. Architecture, upstream hook points, decisions: `../architecture/`.
- What upstream does, including its defects: `../knowledge/upstream-baseline.md`.
- Feature specs (authority over plans): `../superpowers/specs/`. Plans: `../superpowers/plans/`.
- Running, testing, probing: `../how-to/run.md`. Upstream merges: `../how-to/upstream-sync.md`.
- Shared facts: `../../../knowledge/README.md` (Spansh API: `bulk-data.md`; location events:
  `reference-data.md` §6; SQLite and folder mtime: `local-ipc-and-storage.md`; crew transport:
  `group-sync-transport.md`; route formats: `ed-data-sharing.md`; exploration events, hull and fuel:
  `exploration-events.md`).
