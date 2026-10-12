# Crew capture — design (Spec A)

Status: Draft (2026-10-12), awaiting the commander's review
Date: 2026-10-12
Crew features: A capture (this spec) → B sync → C window. Rules: ADR-0001, ADR-0002, ADR-0004 with
ADR-0006, ADR-0005. Journal facts: `../../../../knowledge/exploration-events.md`,
`../../../../knowledge/reference-data.md` §6. EDSY links: `../../../../knowledge/edsy.md`.

## Purpose

EDMRN records what the commander explores and finds, and keeps the commander's current status, so
that the crew can share it (Spec B) and see it (Spec C). Capture works alone and offline. A
commander without friends still gets a history of their own explored systems and finds.

Every record is an event in ADR-0005's signed, append-only log. Records derived from the journal get
deterministic ids, so replaying a journal never duplicates a record. Two friends who visit the same
system produce two events that land in one system row.

A received event can repeat an id that is already known, for example after a lost database. It is
kept, so that the sender's sequence stays contiguous, but it is projected only once.

## Requirements

### Identity and the log

- C1. **One identity per commander.** EDMRN creates an Ed25519 key pair the first time it sees a
  commander's `FID` (`LoadGame`/`Commander`). The `author` is the public key, base64url without
  padding.
  - Keys live in one file per commander in `crew_keys/` next to `edmrn.db`, never inside the
    database. Deleting an unusable database (ADR-0004) therefore keeps the identity.
  - The key file also holds the author's highest allocated `seq`.
  - The `FID` stays local and is never part of an event.
  - Spec C offers export and import of a key file.
- C2. **Envelope.** Each envelope carries:
  - `v` (1);
  - `event_id`;
  - `author`;
  - `seq`;
  - `kind`;
  - `created_at`, always `YYYY-MM-DDTHH:MM:SSZ` (the journal timestamp, or UTC now for manual
    events);
  - `payload`, a JSON object.

  The signature covers the envelope's canonical bytes: UTF-8 JSON with sorted keys, separators `,`
  and `:`, and no ASCII escaping. The log stores exactly those bytes and the signature. Verifying
  means checking the stored bytes; nothing is re-encoded, so a TypeScript peer verifies the same
  bytes.
  - Integers above 2^53 travel as strings: `SystemAddress` is a decimal string.
  - An envelope is at most 16 KB.
- C3. **Ids.**
  - A journal-derived event has `event_id` = the first 32 hex characters of SHA-256 over the
    canonical JSON array `[author, kind, <natural key parts…>]`.
  - A manual event has a UUIDv7, from our own small function (Python 3.13 has no `uuid.uuid7`).
- C4. **Log and sequence.**
  - The log's key is (`author`, `seq`).
  - For own events, the writer skips an event whose `event_id` the log already holds under any
    `seq`. Otherwise it allocates `seq` = max(own highest `seq` in the log, the key file's
    high-water mark) + 1, signs and inserts the event in the same transaction, and updates the key
    file after the commit. A lost database therefore never reuses a `seq`.
  - Received events enter through `crew_store.insert_received`, which keeps the sender's `seq`:
    1. check the size and envelope structure;
    2. verify the signature;
    3. `INSERT OR IGNORE` on (`author`, `seq`).
  - The projector applies an envelope only if its `event_id` is new to the projections, and only
    if its payload passes C12 for a known `kind` and `v`. Other envelopes are kept in the log,
    unprojected, so a newer client's events never block catch-up.

### What is captured

- C5. **Captured events.** Field shapes are in `exploration-events.md`.

  | Kind | From | Natural key | Payload |
  |---|---|---|---|
  | `visit` | `FSDJump`, `CarrierJump` | address, timestamp | commander, system, address, coords |
  | `honk` | `FSSDiscoveryScan` | address, timestamp | system, address, body count, non-body count |
  | `all_bodies` | `FSSAllBodiesFound` | address | system, address, count |
  | `body` | `Scan` of a star, planet or moon | address, body id, tier | name, body id, star type or planet class, moon-of flag, landable, terraform state, was discovered, was mapped, scan type |
  | `signals` | `FSSBodySignals`, `SAASignalsFound` on a star, planet or moon | address, body id, source event | counts per type, genus names (SAA only) |
  | `find` (auto) | C6 | address, find kind, subject | find kind, system, address, body name, body id, detail |
  | `find` (manual) | Spec C, later the local API | UUIDv7 per find; revisions as for notes | as above plus note, deleted flag |
  | `note` | manual | UUIDv7 per revision | note id, system, address, text, deleted flag |

  - **Tier.** `full` for `AutoScan`, `Detailed` and `NavBeaconDetail`; `basic` for `Basic`, which
    lacks `Landable` and `TerraformState`. A later full scan is therefore not lost behind an
    earlier basic one.
  - **Bodies.**
    - A body is a star (`StarType`) or a planet or moon (`PlanetClass`).
    - Belt clusters, rings and ring signal events (material names) are skipped, not rejected.
    - Body names for finds come from the last `Scan` of that body id in the current system.
  - **Signal types.**
    - `$SAA_SignalType_Biological;` → biological;
    - `…_Geological;` → geological;
    - `…_Human;` → human;
    - `…_Guardian;` → guardian;
    - `…_Thargoid;` and `$SAA_SignalType_PlanetAnomaly;` → thargoid;
    - `…_Other;` → other;
    - `$PlanetaryMiningLocation_Name;` → mining;
    - unknown types are counted as other.
  - **What the FSS shows.** It shows only biological, geological and mining signals.
    Human, Guardian, Thargoid and Other signals appear only after mapping a body with the Detailed
    Surface Scanner (DSS). Auto-scanned bodies carry no signals at all (`exploration-events.md` §3).
    The crew window shows what is known and says when a body is unmapped.
  - **Visits.** A `Location` event is not a visit. A `honk` keeps each honk; the projection uses
    the latest body count.
- C6. **Automatic finds**, one per subject:
  - `biological`: a `signals` event with a biological count ≥ 1. Subject: the body id. Genus names
    are shown from the merged signals, not stored in the find.
  - `stellar_phenomenon`: `FSSSignalDiscovered` with `SignalType` `Codex`. Subject: the system
    (one per system; every observed marker is `$Fixed_Event_Life_Cloud;`).
  - `codex`: a `CodexEntry` whose `NearestDestination` starts with `$Fixed_Event_Life_`, or whose
    `IsNewEntry` is true (absent means false). Subject: `EntryID` and body id. Detail: the entry
    name (`$…;`), category, sub-category, and a new-entry flag.
  - `valuable_body`: a full-tier `body` with planet class `Earthlike body`, `Water world` or
    `Ammonia world`, or terraform state `Terraformable`. Subject: the body id.

  Keys are always the `$…;` names, never localised text.
- C7. **Explored.** An author has explored a system when they have an `all_bodies` event for it, or
  when their distinct star, planet and moon body ids (any tier) reach the body count of their latest
  honk there.
  - The second rule matched the event in 287 of 298 cases (`exploration-events.md` §1), and covers
    revisits, where the game writes no new `FSSAllBodiesFound`.
  - A visit alone, or a honk alone, never counts.
  - For the group, a system is explored when any member explored it.
- C8. **Projections.** These tables are derived from the log, so Spec C does not read JSON. Every
  merge is deterministic, so all replicas agree whatever order events arrive in:
  - systems (name, address, coordinates, latest body count);
  - per-author visits (first and last time, count);
  - per-author explored (C7);
  - per-author body facts, with the merged body chosen by best tier, then earliest
    (`created_at`, `author`, `event_id`). `was discovered` and `was mapped` are shown per author;
  - per-body signals: per type the highest count, plus the union of genera;
  - finds, one row per `event_id`, with the finder;
  - notes and manual finds: per (`author`, note or find id), the revision with the highest `seq` of
    that author wins, a deleted revision hides the item, and revisions by anyone else are ignored.

  `rebuild_projections()` re-derives everything from the log in (`created_at`, `author`, `seq`)
  order and must equal the incremental result.

### Status, replay, validation

- C9. **Status (ephemeral).** A thread-safe `CrewStatus` per commander holds the latest status. It
  notifies observers only when a value changes, and it is never written to the log.
  - Commander and location come from `app.location_tracker`.
  - The ship comes from the last `Loadout`: ship type, name and ident, and an EDSY link built from
    the `Loadout` by EDMC's method (`edsy.md`).
  - **Hull.**
    - Start from `Loadout.HullHealth`.
    - Then `HullDamage.Health` when `PlayerPilot` is true and `Fighter` is not true. Per the manual,
      `PlayerPilot` false with `Fighter` false is the player's ship flown by NPC crew; this is
      shown too.
    - `RepairAll`, or `Repair` whose `Item` or `Items` names the hull or `all` (case-insensitive),
      sets it to 1.0 (inferred).
  - **Fuel.**
    - `FuelMain` and `FuelReservoir` come from `Status.json` while the in-ship flag (bit 24) is
      set; otherwise the last values are kept.
    - Capacity comes from `Loadout.FuelCapacity.Main`/`.Reserve`.
  - The timestamp of the last change.
- C10. **Startup replay.**
  - At app start, the newest 5 journal files (by modification time) are replayed in a daemon
    thread, oldest first, through their own mapper instance.
  - The replay seeds `CrewStatus` with the same rule as the location tracker's `adopt`: only if
    newer. The live monitor reads the newest file from its end and would miss the startup
    `Loadout`.
  - Ids are deterministic, so the replay only adds what EDMRN missed. Older history is not
    imported.
- C11. **Mapping is stateful.** A mapper tracks the current commander (by `FID`), the current system
  and the body names of the current system. A journal without `FID` (pre-2018) uses a hash of the commander name instead. Events before the first commander is known are dropped.
  Each event is attributed to the commander active when it was written.
- C12. **Validation.** One validator per kind and `v` checks a payload before it is projected. Own
  events are also checked before signing.
  - Unknown keys are rejected.
  - Types are exact; `bool` is not an int.
  - Numbers are finite and within bounds.
  - Names are at most 128 characters; notes and details at most 2,000.
  - Strings may contain no C0/C1 control characters, no lone surrogates, and no bidi controls
    (U+202A–U+202E, U+2066–U+2069).

  Spec B reuses the validator for friends' data, so it lives in its own module.
- C13. **No real folders in tests** (as in the location spec).

## Design

### Upstream hook points (ADR-0002)

- `edmrn/app.py`: two lines after the location install, `edmrn.ext.crew.install(self)`.
- `edmrn/fuel_tracker.py`: a class attribute `status_listeners`. `_tracking_loop` hands each
  `Status.json` dict it read to every listener, each call in its own try/except. This is the
  journal listener pattern applied to upstream's `Status.json` reader (ADR-0001: extend upstream's
  layer, do not read `Status.json` a second time).

### Our modules (`edmrn/ext/`, store layer per ADR-0006)

- `store.py` gains `Store.transaction()` and the crew migrations in its single list.
- `crew_identity.py`: per-commander key files, `author_id`, `sign`, `verify`, the `seq` high-water
  mark, and key export and import. New dependency `cryptography` (Apache-2.0 or BSD-3; Windows
  wheels; PyInstaller hooks), added to `requirements-edmrn.txt`.
- `crew_events.py`: canonical bytes, ids (deterministic, UUIDv7), envelope build and parse, and the
  validators (C12). Pure: no I/O, no Tk.
- `crew_store.py`: the own-event writer (C4), `insert_received`, the projector,
  `rebuild_projections()`, and the read queries Spec C needs. It writes only inside
  `Store.transaction()`, on the Tk thread.
- `crew_capture.py`: the stateful mapper (C5, C6, C11), the startup replay (C10), and the manual
  API `report_find`, `edit_find`, `delete_find`, `set_note` and `delete_note`.
- `crew_status.py`: `CrewStatus` (C9).
- `crew.py`: `install(app)`.
  - Registers the journal and `Status.json` listeners.
  - Starts the Tk-loop writer.
  - Starts the replay.
  - Sets `app.crew`, which exposes the queries, the manual API, the status and the identities.
  - It is idempotent.

### Threading and the writer

- Listeners and the replay never write. They put unsigned drafts on a thread-safe queue. A draft
  holds kind, natural key or UUIDv7, commander, `created_at` and payload.
- The Tk-loop writer runs about once per second and takes at most 500 drafts per tick. For each
  draft it does, in one transaction: compute the id, skip if known, allocate the `seq`, sign,
  insert, project.
- If the batch fails, the writer retries its drafts one by one, drops the failing one and logs it.
- Signing costs tens of microseconds per event.
- `CrewStatus` observers run on the thread that caused the change, so UI observers go through
  `root.after`.

### Error handling

- **Store unavailable** (ADR-0004): crew capture is disabled. `app.crew` is `None`, the reason is
  logged, and the rest of EDMRN runs.
- **Key file unreadable:** the same, for that commander. It is never silently replaced by a new
  key, because a new key is a new member identity.
- **Invalid mapped payload:** the draft is dropped and logged once per kind. The monitor and other
  listeners continue.
- **Queue overflow** (store locked for a long time): the oldest drafts are dropped beyond 10,000,
  with a log line. Deterministic ids let the next replay restore journal-derived ones.

## Testing

pytest. HOME and related variables are redirected (`tests/conftest.py`); journals, key files and
databases live in `tmp_path`; there is no network.

- **Events:**
  - canonical bytes are stable;
  - deterministic ids are stable, with no collision across kinds that share key parts;
  - UUIDv7 ordering;
  - a signature verifies, and a tampered byte fails it;
  - an int above 2^53 is a string;
  - every validator rule, with hostile values: NUL, bidi controls, a lone surrogate, a
    10,000-character name, NaN, unknown keys, `bool` as int, a 17 KB envelope.
- **Log:**
  - replaying a journal twice adds no envelope;
  - two authors in one system give two events and one system row;
  - a received envelope that repeats a known id under a new `seq` is stored but projected once;
  - a newer-`v` or unknown-kind envelope is stored but not projected;
  - after deleting `edmrn.db`, the next own `seq` continues above the key file's high-water mark;
  - a poison draft is dropped without losing the rest of its batch.
- **Projections:**
  - a full scan after a basic one wins;
  - the merge is order-independent: shuffled arrival gives the same result;
  - signals take the highest count and the union of genera;
  - a note revision by another author is ignored;
  - `rebuild_projections()` equals the incremental result.
- **Capture rules:**
  - each row of C5 and C6;
  - ring and belt events are skipped;
  - Biological from FSS then SAA gives one find;
  - several NSP markers in one system give one find;
  - a codex find carries the body name from the earlier `Scan`;
  - explored from `FSSAllBodiesFound` and from scanned bodies reaching the honk count, never from a
    visit or a honk alone;
  - two commanders in one journal sequence give two authors.
- **Status:**
  - from `Loadout`, `HullDamage` (pilot, NPC crew, fighter), `Repair` (both shapes), `RepairAll`,
    and the `Status.json` listener (in ship vs logged out);
  - the replay seeds status;
  - the EDSY link round-trips to the `Loadout`.
- **Wiring:**
  - listeners only enqueue;
  - the writer drains on a fake root;
  - `install` is idempotent;
  - an unusable store disables crew without raising;
  - the `fuel_tracker` hook delivers to listeners, and a raising listener stops nothing.

## Not in scope

- Transport, groups, expeditions, roster, invitations, chat, the leader's route, sending status
  (Spec B).
- The crew window, the manual-entry UI and the key export/import UI (Spec C).
- Importing history older than the newest 5 journal files.
- The local inbound API for other tools (a later spec).
- Surface coordinates of finds.
- On-foot status.
