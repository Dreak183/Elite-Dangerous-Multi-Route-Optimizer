# Handover — EDMRN

## Where things stand

- `main` holds API hardening, neutron save/resume, journal location, Spec A crew capture, Spec B1
  crew sync core, and the laden jump range with the supercharged neutron start (spec
  `../superpowers/specs/2026-11-08-ship-range-and-neutron-start-design.md`, merged 2026-11-08).
  - `.venv/Scripts/python.exe -m pytest -q`: `1086 passed, 2 skipped` (2026-11-08). One skip is the
    opt-in end-to-end test `tests/test_relay_e2e.py` (`EDMRN_RELAY_E2E=1`, against `wrangler dev`);
    Tk tests sometimes skip with "can't find usable init.tcl" (environment, intermittent). No
    Python linter is configured.
  - The supercharged start was checked live against Spansh: `Eol Prou WN-T d3-2412` → `Colonia` at
    75 LY/×4 now plots 1 jump instead of 2.
- **Crew relay: decision changed (2026-11-08).** The commander has a dedicated server; ADR-0009 (on
  branch `feature/self-hosted-relay`, see Start here) replaces the never-deployed Cloudflare Worker
  in `crew-relay/` (76 vitest tests, TypeScript) with a Python relay on that server and drops the
  peer-to-peer plan. The client protocol is unchanged.
- **No human has looked at the window** since the location, neutron, B1 crew dialog and range
  features: header location, Neutron "Saved routes", a plot from an unknown start, Settings >
  Crew..., and the range label with its Auto switch under both range fields (the label is one line
  in the half-width Neutron range frame; clipping is unchecked).
- Crew features: ADR-0005 (protocol; transport superseded by ADR-0009), ADR-0006, ADR-0007,
  ADR-0008; decomposition A capture → B1 sync core → relay port (ADR-0009) → B2 chat/route/
  expeditions/Discord → C window.

## Start here

1. Read `../../CLAUDE.md`, the ADRs (`../architecture/decisions/`), `../architecture/overview.md`
   and `../knowledge/upstream-baseline.md`.
2. **Self-hosted relay (Phase 2):** branch `feature/self-hosted-relay` holds ADR-0009 and the spec
   `../superpowers/specs/2026-11-08-self-hosted-relay-design.md`. Next: plan
   (`superpowers:writing-plans`) → subagent-driven development → final review → merge. The port's
   behaviour reference is `crew-relay/src/group.ts` with its tests, deleted in the plan's last task.
3. **Deploy session with the commander** (needs the commander): server OS, Docker or not, an
   existing reverse proxy on 443, the DNS record for `galroute.the-hub.cloud`, and the leader key
   from Settings > Crew....
4. **Then Specs B2 and C (crew window).** Notes for B2 and C from Specs A and B1:
   - The manual API and group actions run on the Tk thread only and flush the writer queue (about
     1.8 s at 10,000 drafts).
   - During a lock, an edit right after a report raises `KeyError`.
   - With large journals the startup replay takes seconds before the commander is known, and the
     manual API and group actions raise until then.
   - Notes reject newlines (C12, planning ruling 9); decide whether multi-line notes need a `v` 2.
   - An expired invite is recorded locally as a `join`, then refused by the relay with `not_member`
     (4003); the invite string carries no `expires_at`, so the client cannot explain why (C22,
     deferred to B2).

## Crew requirements for Specs B2 and C (commander, 2026-10-12) — move into those specs, then delete

The B1 requirements (group, invites, status, pause) are in the B1 spec.
- Named expeditions as filters inside the one group (B2).
- Window: commander cards in the header; tabs "Explored systems", "Current route", "Reported
  finds"; a live chat; notes on systems; manual finds with a note. Per explored system: planets,
  landable planets, per-planet signals (C).
- Finds are optionally mirrored to the group's Discord through a webhook (ADR-0005) (B2).
- Later: a local inbound API through which other tools (e.g. Observatory scripts) post finds.

## Open decisions

- **Own GitHub fork as `origin`.** The remote named `upstream` points at the commander's fork
  `Dreak183/Elite-Dangerous-Multi-Route-Optimizer` (it carries `feature/crew-sync-core`), while
  ADR-0002 expects `upstream` = NinurtaKalhu's original and the fork as `origin`. Recommendation:
  `git remote rename upstream origin` and `git remote add upstream
  https://github.com/NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer.git`. Upstream PRs (rows
  marked "yes" in `../architecture/overview.md`) and the updater's target wait for it.
- **Galaxy Plotter endpoint (open question).** Upstream posts its exact-plotter parameters to
  `/api/route`, the neutron plotter's endpoint (`../knowledge/upstream-baseline.md`); whether
  Spansh honours them there is unchecked. Check before building on the Galaxy Plotter.

## Preconditions before friend data enters `edmrn.db` (from the neutron review)

The neutron store validates types and numbers, not content. For crew data, Spec A's validator
(C12), `insert_received` and ADR-0007 close these; any other friend data must do the same:
- bounds on names, distances and ranges;
- a character whitelist (NUL, ANSI escapes and bidi characters are accepted today);
- rejection of unknown keys (extra waypoint keys are silently dropped today);
- strict `route_id` types;
- a stable record identity. `INTEGER PRIMARY KEY` reuses ids after a delete, so ids must not leave
  the process (ADR-0005's `event_id` is that identity).

## Follow-ups from the ship range reviews (deferred, none blocking)

- `tests/test_ship_panel.py` defines `test_each_range_entry_is_bound_to_its_own_variable` twice;
  the first (no assertions) is shadowed. Delete it.
- `edmrn/ext/fsd.py`: the "not explained by the table" warning says "assuming switched-on
  boosters" even with none fitted, and `_warned` is keyed by item only; the outer catch-all logs at
  DEBUG; the docstring's `knowledge/fsd-jump-range.md` means the hub's `../knowledge/`.
- `edmrn/ext/ship_panel.py`: the typed-edit baseline is read once at construction; an outside write
  to `jump_range` before the first tracker write makes the next keystroke count as an edit.
- The cold start accepts the newest journal file that holds a `Loadout`; after a 500,000-line split
  (`Continued`) the newer part has none, so star class and supercharge come from the older part
  until the next jump (the boost is gated on the current system).
- Unverified game behaviour the design assumes: a pending supercharge survives a relog in the same
  system; the supercharge multiplies the range including the Guardian bonus.
- The ship tracker follows whichever commander's events arrive, not upstream's
  `selected_commander` (same as the location tracker).
- `tests/test_neutron.py` imports fixtures from `tests/test_neutron_start.py`.

## Follow-ups from the crew sync core reviews (deferred, none blocking)

- **Store and gate (`edmrn/ext/crew_store.py`, `crew_group.py`):**
  - Redundant second forced rebuild when `_retry_rebuild` succeeds inside a `set_gate` call that
    also changes own authors who already have rows.
  - Live/rebuild loop edge when the `admit` override is used with an empty `own` set (tests only).
- **Controller and dialog (`edmrn/ext/crew_group.py`, `crew_dialog.py`):**
  - When `CrewGroup._record` hits a database lock, the draft stays queued in `Writer` and is
    written on a later tick even though the action reported failure, leaving an orphan `group`,
    `join` or `invite` in the log (C21).
  - An expired invite is recorded locally as a `join`, then refused by the relay with `not_member`
    (4003); the invite string carries no `expires_at`, so the client cannot tell the commander why
    (C22, deferred to B2).
  - App shutdown never calls `app.crew.group.stop()` (the sync thread is a daemon thread that never
    writes `edmrn.db`; the relay sees an abnormal close instead of 1000).
  - `_me()` and `_roster_name()` read the commander key and `commander_name` separately (negligible
    race).
  - `_who_key` in `CrewDialog` excludes the `Identity` object: if a key file is created after
    `identity()` returned `None`, the "You" section stays stale until the next state or board
    render.
- **Sync, status and Discord (`edmrn/ext/crew_sync.py`, `crew_discord.py`):**
  - `CrewSync` state observers are called outside `_state_lock` and can arrive out of order (Tk
    handlers in `CrewDialog` re-read `sync.state`).
  - Calling `CrewSync.stop()` from the sync thread itself raises `RuntimeError` on `Thread.join`
    (caught/logged; guard `threading.current_thread()`).
  - `websockets` 17.2 emits a `DeprecationWarning` when `websockets.sync.client.connect()` is
    called without a `with` block or `legacy=True` (seen in `test_relay_e2e.py`).
  - A raising status source logs both `logger.exception` and a `log_once` warning on the first
    failure; minor import order in `crew_sync.py:12`.
  - `retries=1` in `api.call` can double-post a Discord webhook message if the first attempt
    succeeded on Discord's side before a timeout or 502 (relevant for Spec B2's find mirror).
- **Events, roster and vectors (`edmrn/ext/crew_events.py`, `crew_roster.py`, `tests/`):**
  - `plausible_received` raises `TypeError` if passed a naive `now` datetime.
  - `ascii_name` slices to 64 before `.strip()` (`' ' * 70 + 'z'` becomes `'CMDR'`).
  - `load_json` reports `"0 KB"` if called with `limit < 1024`; `_edsy` regex is rebuilt per call
    (function-local import to avoid a cycle); no direct round-trip test from `crew_status.edsy_url`
    to `validate_status`.
  - `_DECORATION` strips only ASCII whitespace (`<>`/`\``); NBSP, ZWSP, Discord spoiler/bold
    markers or fenced language tags are refused; an over-long string starting with `edmrn-crew:1:`
    says `"not an EDMRN crew invite"` rather than `"damaged"`.
  - Non-canonical `"B" * 43` authors remain in `tests/test_crew_events.py` and
    `tests/test_crew_roster.py` fixtures; minor spacing in `tests/test_crew_events.py` and
    `tests/test_crew_vectors.py:96`; `make_crew_vectors.py` sanity guard compares raw cases only
    against `group` bytes.
- **Plan text:** `../superpowers/plans/2026-10-13-crew-sync-core.md` predates the review rulings
  (for example line 6780 still says Node 20 while Wrangler 4 requires Node 22); the spec,
  `../how-to/deploy-relay.md` and the code are authoritative.

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
- `app._create_optimizer_tab` and `app._create_neutron_tab` are dead; the live tabs are built by
  `ui_components.create_optimizer_tab` and `create_neutron_tab`.
- Importing `edmrn.app` replaces `sys.excepthook`; tests that import it restore the hook.
- Journal listeners and tracker observers run on the monitor or cold-start thread; Tk work goes
  through `root.after`.
- Spansh is slow and returns 502s under load (`../../../knowledge/bulk-data.md`). Live checks may
  need several minutes and retries; unit tests must never use the network.
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
