# Crew features and the data behind them (input for the GUI redesign)

Purpose: tell a designer what a crew UI has to show and offer. Facts only, each with its source. No
layout is proposed. Facts were read on 2026-11-10 from `main` (working tree clean).

Levels used throughout:

- **A** implemented in code, data available today (even where no UI shows it).
- **B** decided in an ADR or spec, not implemented.
- **C** only mentioned as an intention (handover, vision, a spec's "out of scope" list).

Source shorthand: `HO` = `docs/handover/HANDOVER.md`, `OV` = `docs/architecture/overview.md`,
`ADR-n` = `docs/architecture/decisions/000n-*.md`, `SA` = `docs/superpowers/specs/2026-10-12-crew-capture-design.md`
(C1..C13), `SB` = `.../2026-10-13-crew-sync-core-design.md` (S1..S21), `SR` = `.../2026-11-08-self-hosted-relay-design.md`
(R1..R7), code paths are under `edmrn/ext/` unless they start with `relay/`.

An important correction to the brief: a minimal crew dialog **does** exist (Settings > "Crew..."
button, `crew_dialog.py`, SB S20). It is plain customtkinter with no styling intent. What has no UI
is everything beyond it: see section 3 column "In today's dialog".

## 1. Concepts

| Concept | What a designer must understand | Source |
|---|---|---|
| Commander identity / key | Each in-game commander (by Frontier ID) gets its own Ed25519 key pair the first time EDMRN sees it. The public key (43 base64url characters, called `author`) is the commander's identity in the crew; the Frontier ID never leaves the PC. A new key is a new identity; the key file lives in `crew_keys/` next to `edmrn.db`. | SA C1; `crew_identity.py` (`KeyRing`, `Identity`); ADR-0005 |
| Group | One fixed group per installation, one member identity per installation. Identified by a UUIDv7 `group_id` and a name (1-64 printable ASCII). Stored in `crew_keys/group.json` (relay URL, group id, leader key, own member key, webhook, pause flag). | SB S4, S1; `crew_roster.py` `GroupConfig` |
| Leader | The commander who created the group; the trust anchor (taken from the invite or `group.json`, never from received data). Only the leader signs `invite` and `remove`. No leader transfer; a lost leader key freezes the roster. | ADR-0008; SB S2, "Error handling" |
| Member | A commander admitted by redeeming an invite. Has a roster name (ASCII, 1-64, non-ASCII shown as `?`) and a key. A removed member keeps a `last_seq` cap; removal is final for that key. | SB S1, S2; `crew_roster.py` `Member` |
| Invite | A single-use, expiring token: copyable string `edmrn-crew:1:...` plus a link `https://<relay>/join#<string>`. Expiry 1 hour, 24 hours (default) or 7 days; optional label ("for whom"). The first valid `join` wins; a leaked token admits whoever redeems it first. | SB S5, S5a; ADR-0008; `crew_group.py` `create_invite` |
| Roster | The membership list, computed identically by client and relay from the leader's `group`/`invite`/`remove` and members' `join` envelopes. Holds group name, members (leader first, then by name), removed members, and invites with label, expiry and who redeemed. | SB S2; `crew_roster.py` `Roster`, `InviteInfo` |
| Relay connection | A background WebSocket thread to the commander's own relay (`wss://galroute.the-hub.cloud` by default). States: connecting, syncing (catching up), live, waiting (offline, retrying), refused, stopped, plus "no group". Never blocks the UI; retries with backoff 1 s up to 5 min. | ADR-0009; SB S9, S19; `crew_sync.py` `CrewSync`; `crew_dialog.py` `STATE_TEXT` |
| Event log | Append-only, signed log in `edmrn.db` (`crew_log`); every explored system, find and note is an event. Offline capture merges later without duplicates; every member holds a full replica. The UI never reads the raw log, only validated projections. | ADR-0005, ADR-0007; SA C2-C8; `HO` Traps |
| Status | Ephemeral live state per member (location, ship, hull, fuel, paused). Signed, sent when a value changes and at most every 30 s, held in memory only, never logged. The relay keeps the latest per member and also serves it after the member went offline. | SB S8; SA C9; `crew_status.py`; `crew_sync.py` `CrewBoard` |

## 2. Data available per crew member and per event type

Scope limits that apply to every row: an envelope is at most 16 KB (SA C2); names at most 128
characters, note/detail text at most 2,000 (SA C12); strings may contain only Unicode categories
L, M, N, P, S, Zs (SB S10, `crew_events.py` `_ALLOWED_CATEGORIES`); roster names 1-64 printable
ASCII (SB S1). "Example" values are illustrative of the validator's shapes, not taken from a live
group.

### 2.1 Live status, one per member (level A)

Wire document `{author, at, status}`, at most 8 KiB (SB S8; `crew_events.py` `_STATUS`,
`crew_protocol.py` `MAX_STATUS_BYTES`). Every field except `paused` may be null.
Rate: sent on change, at most one per 30 s per member (client `CrewSync.STATUS_INTERVAL`, relay
`engine.py` `STATUS_INTERVAL`, answer `err rate` when exceeded).

| Field | Type / unit | Example | Changes | Source |
|---|---|---|---|---|
| `author` (outside `status`) | 43-char base64url public key | `Xk3...` | never | `crew_events.py` `_author_field` |
| `at` (outside `status`) | UTC `YYYY-MM-DDTHH:MM:SSZ`, the sender's clock when it signed this status (not the time of the last game change) | `2026-11-10T19:42:07Z` | each send | SB S8; `crew_sync.py` `maybe_status` |
| `commander` | in-game name, string up to 128 | `CMDR Jane` | on commander switch | `crew_status.py` |
| `system` | current or last known system name, up to 128 | `Sol` | each jump | `crew_sync.py` `status_from` |
| `address` | system address, decimal string below 2^64 | `10477373803` | each jump | `crew_events.py` `_address` |
| `coords` | `[x, y, z]` light years, finite, magnitude at most 1e7 | `[0.0, 0.0, 0.0]` | each jump | `crew_events.py` `_coords` |
| `ship` | journal ship identifier (not localised) | `mandalay` | on `Loadout` | `crew_status.py` `apply_journal` |
| `ship_name`, `ship_ident` | commander-chosen name and ident string | `Nomad`, `AB-12C` | on `Loadout` | same |
| `edsy_url` | EDSY import link built from the `Loadout`, at most 6,000 characters, must start with `https://edsy.org/#/I=` | link | on `Loadout` | `crew_status.py` `EDSY_IMPORT_URL`; SB S8 |
| `hull` | fraction 0.0 to 1.0 | `0.93` | on `HullDamage`, repair | SA C9; `crew_status.py` |
| `fuel_main`, `fuel_reserve` | tons, 0 to 100,000 | `14.2`, `0.57` | roughly every second while in the main ship (sent at most every 30 s) | SA C9; `crew_status.py` `apply_status` |
| `fuel_capacity_main`, `fuel_capacity_reserve` | tons | `32.0`, `0.63` | on `Loadout` | same |
| `paused` | boolean | `false` | on the pause switch | SB S8 |

Facts a UI must respect:

- A paused member sends exactly one status with `paused: true` and every other field null; afterwards
  nothing (own events included) until resumed (SB S8; `crew_sync.py` `PAUSED_STATUS`).
- A status is sent only for the member's own active commander. If the player is logged in as another
  commander (an alt) nothing is sent and the last status stays on the relay (SB S8;
  `crew_sync.py` `make_status_source`).
- Receivers keep one entry per author and drop a status with an older `at` than the one held; a far-future
  `at` (more than 24 h ahead) is dropped (`CrewBoard.update`; SB S8, S11).
- On connect the relay sends every member's latest status: live ones and the persisted last known
  ones (SB S7.5; SR R1). There is no frame for presence or "online"; a UI can only infer it from `at`
  (frames: `hello, auth, have, evs, synced, st, err` - SB S6).
- No "stale" threshold or text exists anywhere in code or docs.
- The sender's own `ShipStatus` also carries `journal_at` and `timestamp` (time of the last journal
  change), but these are not part of the wire status (`crew_status.py` `ShipStatus` versus SB S8).

### 2.2 Roster and group (level A)

| Item | Fields | Example | Changes | Source |
|---|---|---|---|---|
| `group` event | `group_id`, `name` (1-64 ASCII), `commander` (leader's roster name) | `name: "Exploration Crew"` | once per group | SB S1; `crew_events.py` `_GROUP` |
| `invite` event | `group_id`, `invite_id`, `token_hash` (64 hex), `expires_at` (UTC stamp), `label` or null | `label: "Alex"`, expires in 24 h | per invite created by the leader | SB S1; `_INVITE` |
| `join` event | `group_id`, `invite_id`, `token` (64 hex), `commander` | | once per member | SB S1; `_JOIN` |
| `remove` event | `group_id`, `member` (key), `last_seq` | | per removal | SB S1; `_REMOVE` |
| Roster view | `name`, `members` (author, commander, leader flag, `last_seq` if removed), `invites` (invite_id, label, expires_at, `redeemed_by`) | | after any roster event | `crew_roster.py` `Roster`, `InviteInfo`; read via `app.crew.group.roster` |
| Group config | relay URL, own member key, `is_leader`, `paused`, Discord webhook (leader only, secret) | | on user action | `crew_roster.py` `GroupConfig` |
| Connection | `state` (`connecting`, `syncing`, `live`, `waiting`, `refused`, `stopped`, `no group`), `last_error` text, `problem` text | | continuously | `crew_group.py` `state`, `problem`; `crew_sync.py` |
| Own identity | commander name, own public key (copyable), key export text (JSON with private key) | | per commander | `crew.py` `commander_name`, `identity()`; `crew_identity.py` `export_text`, `import_text` |

The dialog today shows invites only as the one just created; the roster's invite list (label,
expiry, who redeemed) exists as data but is not shown (`crew_dialog.py` `_render_invite`).

### 2.3 Journal-derived events (level A; captured and shared automatically)

Every row is a signed event; the projected tables are what the UI reads (`crew_store.py` queries).
Each is stored per author, so "who found/explored this" is always available.

| Kind / projection | Fields (units) | Example | How often | Source |
|---|---|---|---|---|
| `visit` (from `FSDJump`, `CarrierJump`) | commander, system, address, coords (LY) or null. Projection per author: first visit, last visit, visit count; a system row (name, coords, body count) | `Sol`, `10477373803` | one per jump: about 60 jumps/h while neutron-routing | SA C5, C8; SB S16; `crew_store.py` `VisitRow`, `SystemRow` |
| `honk` (from `FSSDiscoveryScan`) | system, address, `body_count` 0-10,000, `non_body_count` 0-100,000 | `body_count: 14` | one per discovery scan | SA C5; `_HONK` |
| `all_bodies` (from `FSSAllBodiesFound`) | system, address, `count` | | once per fully scanned system | SA C5; `_ALL_BODIES` |
| Explored (projection) | per author and for the group: set of system addresses. A system is explored when the author has `all_bodies` for it, or has scanned as many distinct bodies as the latest honk's body count; a visit or honk alone never counts | | derived | SA C7; `crew_store.py` `explored(author=None)` |
| `body` (from `Scan` of star, planet, moon) | system, address, `body_id` 0-65,535, name, `tier` (`full`/`basic`), `star_type` or `planet_class` (exactly one), `moon`, `landable` (true/false/null), `terraform` (e.g. `Terraformable`, or empty/null), `was_discovered`, `was_mapped` (per author), `scan_type` (`AutoScan`, `Detailed`, `NavBeaconDetail`, `Basic`) | `planet_class: "Earthlike body"` | about 25 envelopes per fully explored system, mostly bodies | SA C5, C8; `_BODY_FIELDS`; `crew_store.py` `BodyRow`, `BodyReport` |
| `signals` (from `FSSBodySignals`, `SAASignalsFound`) | body id and name, `source` (`fss`/`saa`), `counts` per type (`biological`, `geological`, `human`, `guardian`, `thargoid`, `other`, `mining`; 0-1000 each), `genera` (names, at most 64, SAA only) | `counts: {biological: 3}` | per scanned body with signals | SA C5; `_SIGNALS_FIELDS`; `crew_store.py` `BodySignals` |
| Signals knowledge limit | FSS shows only biological, geological and mining; human, guardian, thargoid and other appear only after a mapping scan (DSS); auto-scanned bodies carry none. SA says the crew window "shows what is known and says when a body is unmapped" | | | SA C5 "What the FSS shows" |
| `find` auto: `biological` | system, address, body name/id, `detail.count` 1-1000 | | one per body with at least one biological signal | SA C6; `_FIND_DETAILS` |
| `find` auto: `stellar_phenomenon` | system, address, `detail.signal` (`$Fixed_Event_Life_Cloud;` is the only observed marker) | | one per system | SA C6 |
| `find` auto: `codex` | body, `detail`: `entry_id`, `name`, `category`, `subcategory`, `new_entry` | | per qualifying codex entry | SA C6 |
| `find` auto: `valuable_body` | body, `detail`: `planet_class`, `terraform` (Earthlike, water world, ammonia world, or terraformable; full scan only) | | per qualifying body | SA C6 |
| `find` manual | `find_id` (UUIDv7), `find_kind` (the four above or `other`), system or null, address, body name/id or null, `note` (up to 2,000, may be empty), `deleted` | | on user action; revisions: highest `seq` of the same author wins, a deleted revision hides it | SA C5, C8; `_MANUAL_FIND`; `crew.py` `report_find`, `edit_find`, `delete_find` |
| `note` | `note_id` (UUIDv7), system or null, address, `text` (up to 2,000, newline-free), `deleted` | | on user action; only the author's own revisions count | SA C5, C8; `_NOTE`; `crew.py` `set_note`, `delete_note`; `HO` (notes reject newlines) |
| Replay at start | newest 5 journal files are replayed, oldest first; older history is never imported | | once per app start | SA C10 |

Per-member reading queries available: `visits(author)`, `explored(author)`, `bodies(address)`,
`body_reports(address)` (per-author discovered/mapped), `signals(address)`, `finds()` (all authors,
newest first, deleted hidden), `notes(address=None)` (newest first, with author) (`crew_store.py`
lines 738-833). A system's name and coordinates prefer the group member's own report, then the
earliest (SB S11).

### 2.4 Volumes and retention

| Quantity | Value | Source |
|---|---|---|
| Group size | "small fixed group (2-10 commanders)"; "a group of about five active commanders". No limit is enforced in code or relay (no cap found). | ADR-0005 Context; SB Purpose, S16; searched `crew*.py`, `relay/` |
| Groups per installation | exactly one; one member identity | SB S4; SB "Not in scope" |
| Own events per hour | neutron highway: about 60 jumps/h at about 3 envelopes (about 700 per 4 h evening); full FSS exploration: about 30 systems/h at about 25 envelopes (about 3,000 per 4 h) | SB S16 (estimates, "not measurements") |
| Five commanders exploring fully all evening | about 15,000 envelopes | SB S16 |
| Join of a new member | uploads the history EDMRN holds: a few thousand envelopes once | SB S16 |
| Relay daily limits (abuse protection, configurable) | 5,000 envelopes per member per UTC day; 80,000 per group per UTC day; roster envelopes of the leader are exempt | SB S14; SR R1; `relay/edmrn_relay/config.py` |
| Relay frame and batch sizes | frame at most 1 MiB; `evs` at most 100 items / 512 KiB; client batches own uploads for about 5 s | SB S6, S7; `crew_protocol.py` |
| Client writer queue | 500 drafts per tick, cap 10,000; past it the oldest drafts are dropped (manual notes and finds are not restorable) | SA "Threading and the writer", "Error handling"; `crew.py` `Writer` |
| Received-envelope inbox | 250 ms tick, 200 per batch, cap 5,000 | `crew_sync.py` `Inbox` |
| Retention | None stated. The log is never pruned (no delete path found); the relay deletes only past-day quota rows; the relay stores about 1 KB per envelope | SB "Error handling" (Relay storage); `relay/edmrn_relay/engine.py` |
| Replica | every member holds the full log; the relay can be re-seeded from any member | ADR-0005, ADR-0009; SB S7 |

### 2.5 Items with no data layer (levels B and C)

| Item | Level | What exists | Source |
|---|---|---|---|
| Named expeditions as filters inside the one group | B (one ADR line; no spec, no event kind, no schema) | "Expeditions are named filters inside the group." | ADR-0005 Decision "Membership"; `HO` "Crew requirements" ("B2") |
| Mirror finds to the group's Discord through the leader's webhook | B (ADR line; no spec; the webhook field and the invite post exist) | ADR-0005: the server may post finds to a webhook; SB S5a: the webhook may be reused "with the commander's consent then" | ADR-0005; SB S5a; `HO`. **Contradiction, see section 6.** |
| Chat ("a live chat") | C (no event kind, no frame, no table; absent from all `crew*.py` and `relay/`) | named as a feature in ADR-0005 Context, in SB "Not in scope" (B2) and in the commander's requirements | ADR-0005 Context; SB "Not in scope"; `HO` |
| The leader's route ("Current route" tab) | C (same; ADR-0005 states the single-writer rule for "the route", which implies a mutable item with one writer) | | ADR-0005 Decision; SB "Not in scope"; `HO` |
| Per explored system: planets, landable planets, per-planet signals | Data A (`bodies`, `signals`, `landable`), window C | The data exists; the commander's wish is for a window | `HO` "Crew requirements"; `crew_store.py` |
| Local inbound API for other tools (e.g. Observatory scripts) to post finds | C ("Later") | The Tk-thread manual API (`report_find` etc.) exists, but no HTTP/IPC API | `HO`; SA "Not in scope" |
| Surface coordinates of finds, on-foot status | C (explicitly out of scope of Spec A) | | SA "Not in scope" |

## 3. Functions a crew UI needs (user tasks)

"In today's dialog" describes the existing Settings > Crew... window (`crew_dialog.py`): identity
block (commander, copyable public key); outside a group "Create a group" and "Join with invite";
inside a group connection label, "Pause sharing" switch, member rows (name, "(leader)", one text
line "system - ship name"), for the leader "Remove" (two-click confirm), invite section, webhook
field, and "Leave group" (two-click confirm). One message line at the bottom.

| # | User task | Level | Data it relies on | In today's dialog | Source |
|---|---|---|---|---|---|
| T1 | See who is in the group (leader, members, who was removed) | A | Roster `members`, `last_seq` | Active members listed; removed members not shown | SB S2, S20; `crew_roster.py` `Roster.active` |
| T2 | See where each member is (system, coords) and what they fly | A | Board status: `system`, `address`, `coords`, `ship`, `ship_name` | One line "system - ship" | SB S8; `crew_dialog.py` `_status_text` |
| T3 | See a member's ship detail: hull, fuel, capacities, EDSY link | A | Board status `hull`, `fuel_*`, `edsy_url` | Not shown | SB S8 |
| T4 | See that a member is paused, or how old their last status is | A (data), no staleness rule | `paused`, `at` | Not shown | SB S8 |
| T5 | See own current status as friends will see it | A | `app.crew.status()` (location, `ShipStatus`) | Not shown | `crew.py` `status` |
| T6 | See systems explored by the group, by one member, by me | A | `explored(author)`, `systems`, `visits(author)` | Not shown | SA C7, C8 |
| T7 | Open a system: name, coords, body count, bodies, per-body signals and genera, who discovered/mapped | A | `system`, `bodies`, `body_reports`, `signals` | Not shown | SA C5, C8; `crew_store.py` |
| T8 | See finds of the group (auto and manual), with finder and time | A | `finds()` | Not shown | SA C6, C8 |
| T9 | Report, edit, delete a manual find (with note) | A (Tk-thread API only) | `report_find`, `edit_find`, `delete_find` | No UI | SA "Not in scope" (Spec C); `crew.py` |
| T10 | Write, edit, delete a note on a system; read friends' notes | A (API only; notes single-line) | `set_note`, `delete_note`, `notes(address)` | No UI | SA C5, C8; `HO` (newlines) |
| T11 | Create a group (name, relay address prefilled `wss://galroute.the-hub.cloud`) | A | `create_group` | Yes | SB S20; `crew_group.py` |
| T12 | Join with an invite (paste string or link; link or string in Discord decoration is accepted) | A | `join`, `decode_invite` | Yes | SB S5, S20 |
| T13 | Leader: create invite (label, expiry 1 h / 24 h / 7 d), copy string and link, see expiry | A | `create_invite` | Yes (only the latest invite) | SB S5, S5a, S20 |
| T14 | Leader: see issued invites and whether redeemed | A (data) | `Roster.invites` | Not shown | `crew_roster.py` `InviteInfo` |
| T15 | Leader: post invite to Discord; save/clear the webhook | A | `post_invite`, `set_webhook`, `GroupConfig.webhook` | Yes (webhook field masked) | SB S5a |
| T16 | Leader: remove a member | A | `remove` (caps at the member's highest known `seq`) | Yes (two-click) | SB S2, S20 |
| T17 | Leave the group (withdraws friends' data from view, keeps own log) | A | `leave` | Yes (two-click) | SB S3, S20 |
| T18 | Pause / resume sharing | A | `set_paused` | Yes | SB S8 |
| T19 | See connection state and why it is not live; retry after a refusal | A | `state`, `last_error`, `problem`, `reconnect` | Label, Reconnect button when refused | SB S9; `crew_dialog.py` |
| T20 | See and copy own crew key (needed by the server operator for `EDMRN_RELAY_LEADERS`) | A | `identity().author` | Yes | SB S20; `HO` start-here step 3 |
| T21 | Export / import a key file (move identity to another PC) | A (code only) | `KeyRing.export_text`, `import_text` | No UI; SA says Spec C offers it | SA C1; `crew_identity.py` |
| T22 | Use crew data alone (no group): own explored history and finds | A | capture works offline and without friends | No UI | SA Purpose |
| T23 | Filter by named expedition | B | not defined | | ADR-0005 |
| T24 | Mirror finds to Discord | B | webhook exists | | ADR-0005; SB S5a |
| T25 | Chat live with the group | C | not defined | | `HO`; SB "Not in scope" |
| T26 | See and follow the leader's route | C | not defined | | `HO`; SB "Not in scope" |
| T27 | Commander cards in the header; tabs "Explored systems", "Current route", "Reported finds"; planets/landable/signals per explored system | C (UI wishes) | A data for cards, explored, finds, planets; none for route | | `HO` "Crew requirements" |
| T28 | Other tools post finds through a local API | C | | | `HO` |

## 4. States and failure cases the UI must be able to show

Existing message texts are quoted verbatim. "Log only" means today the commander is not told.

### 4.1 Availability

| Case | Existing text or behaviour | Source |
|---|---|---|
| Crew capture unavailable (store unusable, `cryptography` missing) | `app.crew` is `None`; the dialog says "Crew features are unavailable; the log file says why."; the Settings card says "Crew is unavailable." when it cannot even be built | `crew_dialog.py` `render`, `add_settings_card`; SA "Error handling"; `crew.py` `install` |
| Sync unavailable but capture works (`app.crew.group` is `None`) | Same dialog text as above (the dialog gets `group=None`) | `crew_dialog.py` `open_dialog`; `crew.py` `_install_group` |
| `websockets` package missing | problem: "Crew sync needs the websockets package (pip install websockets)." | `crew_group.py` `_start_sync` |
| Key for this membership missing in `crew_keys` | problem: "The key of this group membership is missing from crew_keys." | `crew_group.py` |
| Sync could not be created | problem: "Crew sync could not start; the log file says why." | `crew_group.py` |
| `group.json` unusable | problem: "The group file cannot be used (<reason>); leave the group to start over." Only "Leave group" is offered | `crew_group.py` `start`; `crew_dialog.py` `_render_outside` |
| Key file unusable for a commander | capture disabled for that commander, never replaced; log only | SA "Error handling"; `crew.py` `Writer._identity` |
| No commander known yet (startup replay takes seconds; actions raise until known) | "No commander is known yet; start Elite Dangerous or wait until EDMRN has read the journal."; identity block shows "Commander: not known yet" | `crew_group.py` `_me`; `crew_dialog.py`; `HO` Start-here 4 |
| Not in a group | connection text "Not in a group"; create/join forms | `crew_dialog.py` `STATE_TEXT` |

### 4.2 Connection (shown as "Connection: <text>" plus " (<last_error>)" unless live)

| State | Text | Source |
|---|---|---|
| `connecting` | "Connecting..." | `crew_dialog.py` `STATE_TEXT` |
| `syncing` | "Catching up..." | same |
| `live` | "Connected" | same |
| `waiting` | "Offline, retrying" (backoff 1 s doubling to 5 min, plus or minus 20 %) | same; SB S9 |
| `refused` | "Refused by the relay"; last_error "The relay refused this member (<reason>)"; reconnect stops until the commander presses Reconnect. Reasons are the relay's raw close texts: `bad signature`, `bad auth`, `auth first`, `auth timeout` (code 4001), `not_member`, `removed`, `no group` (code 4003) | `crew_sync.py` `_attempt`; `relay/edmrn_relay/engine.py` |
| `stopped` | "Not connected" | `crew_dialog.py` |

Other `last_error` strings (lower-case sentences, shown in parentheses): "cannot reach the relay: <exception>",
"the connection closed (<code>)", "the connection failed: <reason>", "unexpected <Type> in the connection",
"another EDMRN with this commander took over the connection" (code 4000; waits 60 s, then retries, so two
installations of one commander do not take turns) (`crew_sync.py` `_note`, `_attempt`; SB S9).

A removed member and an expired or wrong invite are indistinguishable in the UI: all appear as
"refused" (4003). Known gap C22: an expired invite is recorded as a `join` and then refused; the
invite string carries no `expires_at`, so the client cannot explain it (`HO`; deferred to B2).

### 4.3 Relay refusals that are log-only today

`err` codes: `quota`, `group_quota`, `not_member`, `removed`, `bad_sig`, `too_big`, `bad_frame`,
`rate`; the client logs each once per connection and does not surface it (`crew_sync.py`
`handle`; SB S9). Consequences a UI may need to explain: a daily quota refusal lifts at 00:00 UTC and
the refused envelopes are offered again (SB S14); after `err rate` the client does not resend its status,
so a refused status shows only at the next value change (`HO` Start-here 4).

### 4.4 Group actions: sentences a refused action shows (one line under the dialog)

- "This EDMRN is already in a group; leave it first."
- "A group name is 1 to 64 plain ASCII characters."
- "The relay address must look like wss://galroute.the-hub.cloud."
- "This is not an EDMRN crew invite." / "The invite is damaged; ask the leader for a new one."
- "This is your own invite; send it to a friend."
- "Only the group leader can do this; switch to the commander who created the group."
- "An invite lasts 1 hour, 24 hours or 7 days." / "A label is 1 to 64 plain ASCII characters."
- "Only a current member other than the leader can be removed."
- "This EDMRN is not in a group."
- "Set a Discord webhook first." / "This is not a Discord webhook URL (https://discord.com/api/webhooks/...)."
- "EDMRN could not <what>; the log file says why." (disk or database error)
- "EDMRN could not record this; the log file says why."
- "The post to Discord failed; the log file says why." (other Discord failures use the shared API failure sentence)

Success texts: "Group created.", "Joined.", "Left the group.", "Sharing paused.", "Sharing resumed.",
"Member removed.", "Webhook saved.", "Invite created; it works once.", "Posting to Discord...",
"Invite posted to Discord." (`crew_group.py`, `crew_dialog.py`).

### 4.5 Data-state cases with no existing text

| Case | Fact | Source |
|---|---|---|
| Member has never sent a status | roster member without a board entry; dialog shows an empty status label | `crew_dialog.py` `_render_inside` |
| Member is paused | status with `paused: true` and all other fields null | `crew_sync.py` `PAUSED_STATUS` |
| Member's status is old / member offline | no presence frame; only `at` | SB S6, S8 |
| Member's alt commander is playing | no status sent; previous status persists on the relay | SB S8 |
| Untrusted or invalid friend data refused | An envelope that is signed but fails validation, has a wrong author-bound id, an implausible `created_at` (before 2014-12-16 or more than 24 h ahead), comes from a non-member or beyond a removed member's `last_seq`, is stored but never projected and never shown; only warnings in the log ("Crew sync refused N received envelopes ...", per-rebuild summary) | ADR-0007, ADR-0008; SB S3, S11, S12; `crew_sync.py` `Inbox.drain` |
| Friend text with characters outside the whitelist | the whole envelope is unprojected; a status field that fails is nulled alone before sending (own side only) | SB S10; `crew_sync.py` `signable` |
| Catch-up of a large history | state `syncing`; a joiner uploads a few thousand envelopes, projections fill in meanwhile | SB S16 |
| Rebuild after a member is removed or after leaving | friends' rows are withdrawn and re-derived; a rebuild marker survives a crash | ADR-0008; OV "Crew capture data flow" invariants |
| Locked database | drafts stay queued and retry; log line only | `crew.py` `Writer.drain` |
| Queue overflow | oldest drafts dropped past 10,000; log only | SA "Error handling" |
| Relay down for good | "Friends' clients reach the relay only while the server is up; there is no fallback path." Local capture continues and syncs later | ADR-0009 Consequences; SB "Error handling" |
| Lost database / lost key | database lost: next connection re-downloads the log, `seq` continues above the key file; key lost: a new identity (new member) unless the key file is restored | SB "Error handling"; SA C1, C4; ADR-0005 |
| Lost leader key | roster frozen; recovery is a new group | ADR-0008 Consequences |

## 5. Constraints from the ADRs and specs that restrict UI design

1. **Friends' data is untrusted and shown only as plain widget text.** Never as markup; never read
   from `crew_log`, only from validated projections (ADR-0007 Consequences; `HO` Traps; `CLAUDE.md`
   hard rules; `crew_dialog.py` docstring). The existing dialog bounds each friend string to 80
   characters with an ellipsis (`_plain`, `STATUS_MAX`) and collapses whitespace. The invite page on the
   relay renders the fragment as text with a strict CSP (SB S5, S17).
2. **Names have two forms.** Roster names (group name, member commander name, label) are printable
   ASCII only, non-ASCII replaced by `?`, at most 64 (SB S1; `ev.ascii_name`). Status and event names
   may be Unicode letters/marks/numbers/punctuation/symbols/spaces, up to 128 (SB S10). A member's
   display name therefore differs by source.
3. **Leader-only actions:** create invite, remove member, set/clear the Discord webhook, post an
   invite to Discord. Members can: pause, leave, create/join (when not in a group), reconnect, report
   finds and notes. Leader cannot be removed; removal is final for that key (re-join needs a new
   commander identity); no leader transfer; no revoking an unredeemed invite (SB S2, "Not in scope";
   `crew_group.py` `_leader`).
4. **Destructive actions are confirmed by a second click within 4 s, not a modal** (`crew_dialog.py`
   `_confirm_button`, `CONFIRM_MS`): used for Remove and Leave.
5. **The Discord webhook URL is a secret.** Stored only in the leader's `group.json`, never sent to
   the relay, never logged or shown in an error; the input is masked. A webhook posts to a channel,
   not a DM: the dialog must warn "A webhook posts into a channel, not a DM: whoever reads the channel
   first can use the invite. Use a private channel and a short expiry." Posts never ping
   (`allowed_mentions` empty) (SB S5a; `crew_dialog.py` `WEBHOOK_WARNING`; OV last invariant).
6. **Privacy: what is shared.** Once in a group, every journal-derived event (all visited and explored
   systems with coordinates and times, scanned bodies, signals, finds) and every note and find is
   uploaded to the relay and to every member's replica automatically; live status adds current
   system, ship, fuel and hull. The relay may read all content; there is no end-to-end encryption
   (ADR-0005 "The server may read content", ADR-0009; SB "Not in scope"). Controls that exist: the
   single "Pause sharing" switch (stops live status and all own envelopes; resuming catches up, so it
   hides live position, not history) and "Leave" (SB S8, S20). The Frontier ID is never part of an
   event (SA C1). Notes are crew-visible events (SA C5).
7. **One group, one member identity per installation**; the active commander must be the member
   author for status to be sent. UI cannot assume several groups (SB S4).
8. **Threading:** all group and manual-API actions run on the Tk thread; observers fire on other
   threads and reach Tk only through `root.after`; observers can arrive out of order, so a UI must
   re-read state when notified (`crew_group.py`; `HO` follow-ups). The manual API flushes the writer
   queue (about 1.8 s at 10,000 drafts) (`HO`).
9. **Roster has no change notification;** the dialog polls once per second (`crew_dialog.py`
   docstring, `REFRESH_MS`). Board and sync state do notify.
10. **Notes reject newlines** (SA planning ruling 9, `HO`); notes at most 2,000 characters.
11. **Text language:** UI text is English (SB S20).
12. **Own events win:** a system's name and coordinates prefer the reader's own reports; merges are
    order-independent, so displayed values may change when a friend's older report arrives (SB S11; OV invariants).
13. **Invite text formats** (the UI will show or accept them): string `edmrn-crew:1:<base64url>` (at
    most 2,000 characters) and link `https://<relay host>/join#<string>`; the join box accepts either and
    strips Discord decoration (`< > `` ` `` and whitespace) (SB S5; `crew_roster.py` `extract_invite`).
14. **Relay address form:** `wss://<host>` only (no path, query, user info, port allowed); `ws://` only
    for `localhost` and `127.0.0.1` (`crew_roster.py` `check_relay_url`).

## 6. Open points the documents leave undecided

Contradictions first (precedence: spec > ADR > plan > code comment):

1. **Who posts finds to Discord.** ADR-0005 says the *server* may post finds to a webhook the leader
   configures per group; SB S5a and OV say the webhook is stored only in the leader's `group.json`
   and never sent to the relay (and SB S5a defers the find mirror to Spec B2 "with the commander's
   consent"). No spec for the mirror exists; a mirror that runs on the leader's client would make
   finds mirror only while the leader is online. Undecided.
2. **Where the status lives on the relay.** SB S8 says "in the sender's socket attachment"
   (Cloudflare); SR R1 and OV say in memory on the connection, written to the `status` table on close.
   SR is later and wins: the persisted last-known status is what offline members show.
3. **Spec A says Spec C offers key export and import; SB S20 puts only a copyable public key in the
   dialog.** Export/import exist in `crew_identity.py` only.
4. **Group size:** ADR-0005 "2-10", SB "about five active"; no cap is enforced anywhere.

Decisions the documents leave open:

- Staleness: what age of `at` counts as "old" or "offline"; no presence concept exists.
- Whether the status's `at` or the last game change should be shown ("last seen").
- Per-kind privacy: no way to share status but not history, or to keep a note or find private; only
  pause (all own output) and leave.
- Expeditions: schema, whether they filter systems, finds, notes or statuses, who creates them
  (ADR-0005 gives one sentence).
- Chat: event kind or frame, retention, history for late joiners, whether it counts against the 5,000
  per-day member quota, whether it is shown while paused. Nothing is specified.
- The leader's route: payload format (route formats are in `../knowledge/ed-data-sharing.md`),
  who may publish it (the leader only is implied, not stated), how members follow progress.
- Discord find mirror: see contradiction 1; which find kinds, per-find opt-in ("with the commander's
  consent").
- Expired-invite explanation (C22) and "refused" wording that distinguishes removed, expired and bad
  signature (`HO`).
- Invite lifecycle: listing invites, showing redeemed state, revoking before expiry (revoking is out of
  scope in SB).
- Alt commanders: only the member's commander shares status; how the UI says "you are playing as a
  commander who is not in the group" is undefined.
- Manual find/note UI: which `find_kind` values a user may pick (`biological`, `stellar_phenomenon`,
  `codex`, `valuable_body`, `other`), multi-line notes (`HO`: decide whether a `v` 2 is needed).
- Commander cards: which fields (SA C9 and SB S8 define the available ones); the commander's wish list is
  in `HO` ("commander cards in the header").
- How a body's unknown signals are labelled ("says when a body is unmapped", SA C5).
- Whether the existing dialog stays as a settings sub-dialog once a crew window exists (SB S20 calls it
  "minimal ... before Spec C").
- Retention and storage growth of the local log and the relay (none defined).
- Not yet seen by a human: "No human has looked at the window since ... B1 crew dialog" (`HO`).
- Planned but outside this document: a deployment session fixes the relay host; the server is not yet
  deployed, so no real group exists (`HO` "Never executed").
