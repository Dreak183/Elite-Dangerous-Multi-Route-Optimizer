# Crew sync core — design (Spec B1)

Status: Accepted (2026-10-13), amended the same day with the planning rulings
Date: 2026-10-13
Crew features: A capture → **B1 sync core (this spec)** → B2 chat, route, expeditions, Discord → C
window. Rules: ADR-0005 (transport), ADR-0006 (store layer), ADR-0007 (stored versus projected),
ADR-0008 (membership). Facts: `../../../../knowledge/group-sync-transport.md` (Cloudflare limits,
prices, WebSocket billing, Ed25519 in Web Crypto).

## Purpose

Friends exchange what Spec A captures through a relay on Cloudflare, without paying for it. A
commander creates a group and invites friends by Discord DM. Every member's explored systems,
finds and notes reach every other member. Live status (ship, hull, fuel, location) is shared while
someone plays, and the last known status remains visible after they leave.

The relay is a convenience, not the source of truth: every member holds a full replica, and the
relay can be re-seeded from any of them. On the Free plan, nothing can cost money. When a daily
limit is hit, Cloudflare refuses further operations until 00:00 UTC, and the exchange catches up
the next day.

## Commander's requirements carried into this spec (2026-10-12/13)

- One fixed group per installation. The leader admits members with invites. An invite admits on
  its own; the leader need not be online when it is redeemed.
- An invite exists as copyable text and as a link, to send by hand (for example a Discord DM), or
  is posted through a Discord webhook the leader configures (2026-10-13).
- Own status is shared automatically, with a "pause sharing" switch.
- Status per commander: ship and loadout (EDSY link), fuel, hull, current or last known location.
- The relay lives at `galroute.the-hub.cloud`, a sub-domain of a zone the commander already manages
  in Cloudflare.
- The design must stay inside Cloudflare's Free plan for a group of about five active commanders
  (S12).
- A minimal crew dialog exists before Spec C, so friends can test the exchange.

## Requirements

### Group and roster

- S1. **Roster kinds.** Four new manual kinds (`event_id` is a UUIDv7, ADR-0007), each `v` 1,
  validated like every kind (C12 as amended by S10):

  | Kind | Signed by | Payload |
  |---|---|---|
  | `group` | the leader | `group_id` (UUIDv7), `name`, `commander` |
  | `invite` | the leader | `group_id`, `invite_id` (UUIDv7), `token_hash` (64 lowercase hex), `expires_at` (stamp), `label` (name or null: whom the leader meant it for) |
  | `join` | the new member | `group_id`, `invite_id`, `token` (64 lowercase hex), `commander` |
  | `remove` | the leader | `group_id`, `member` (author), `last_seq` (0 … 2^53−1) |

  - **Roster names** (`name`, `commander`, `label`) are 1–64 characters of printable ASCII
    (U+0020–U+007E). The relay and the client must agree exactly on which roster envelopes are
    valid, and Python's and the Workers runtime's Unicode databases differ in version.
  - A commander name outside that range is sent with each other character replaced by `?`.
  - `expires_at` must be a real calendar time.
  - Roster envelopes by other authors also pass S11's timestamp check, on the relay as on the
    clients.

- S2. **Membership** is a pure function of the roster envelopes for one `group_id` and the leader's
  author, computed identically by the relay and every client (ADR-0008):
  - The leader is the author named in the invite string or in `group.json` (S4), never derived from
    received data. The leader's `group` envelope for the `group_id` opens the group; any other
    author's `group` envelope is ignored.
  - An `invite` counts only if signed by the leader.
  - A `join` admits its author if:
    - an `invite` with its `invite_id` exists;
    - SHA-256 of the 32 token bytes equals `token_hash`;
    - its `created_at` is not after `expires_at`;
    - it is the earliest `join` for that `invite_id` that passes the checks above, compared by
      (`created_at`, `author`, `seq`). The leader's own `join` never counts.
  - A `remove` counts only if signed by the leader, and never for the leader. Its `last_seq` caps
    that member for good: their envelopes with a higher `seq` are refused by the relay and never
    projected. When several `remove` envelopes name one member, the lowest `last_seq` applies.
    Re-admitting a removed key is not supported; the friend joins with a new commander identity.
- S3. **Projection gate.** The projector applies an envelope only if its author is an own author
  (a key file exists in `crew_keys/`), or a member whose `last_seq`, if any, is not below the
  envelope's `seq`. Roster envelopes are always evaluated by S2. Everything else stays in the log,
  unprojected (ADR-0007).
  - When a member is admitted, that author's stored envelopes are projected; the merges are
    order-independent (Spec A C8), so the result equals projecting them on arrival.
  - When a member is removed, `rebuild_projections()` runs once, because projected rows must be
    withdrawn.
- S4. **Group configuration.** `crew_keys/group.json` holds:
  - `v` 1;
  - the relay URL;
  - `group_id`;
  - the leader's author;
  - the member author this installation uses;
  - the Discord webhook URL or null (S5a);
  - the pause flag (S8), so a restart stays paused.

  The file lives next to the key files and outside `edmrn.db`. Deleting an unusable database
  (ADR-0004) therefore keeps the membership, and the next connection re-downloads the log, own
  events included. One installation belongs to at most one
  group, with one member author.
- S5. **Invite string.** `edmrn-crew:1:` followed by base64url (no padding) of the canonical JSON
  object `{"g": group_id, "i": invite_id, "k": token, "l": leader, "u": url}`. The token is 32
  random bytes from `secrets`. The URL must be `wss://` with a host name and no user info, query or
  fragment. `ws://` is accepted only for `localhost` and `127.0.0.1`, for the end-to-end test. A
  string that fails any check is rejected whole, with one sentence saying why.
  - **Expiry.** The leader chooses 1 hour, 24 hours (the default) or 7 days.
  - **Link form.** `https://<relay host>/join#<invite string>`. The relay serves a static page at
    `/join`: a script reads the fragment, shows it as text (never as markup) with a copy button and
    the steps "EDMRN → Settings → Crew… → Join with invite". The fragment never reaches the relay,
    so the token stays out of its logs.
  - **Join box.** "Join with invite" accepts the bare string or the link and takes everything after
    the first `#`.
- S5a. **Invite delivery.** "Create invite" always shows the string and the link with copy buttons.
  If the leader has configured a Discord webhook, a "Post to Discord" button also posts the invite.
  - **The message** names the group, the label, the expiry and the link. It is sent with
    `allowed_mentions` `{"parse": []}`, so a group name or label never pings anyone.
  - **The webhook URL** is a secret: whoever has it can post to the channel. It is stored only in
    the leader's `crew_keys/group.json`, never sent to the relay, never logged, and never shown in
    an error text.
    - It must be `https://discord.com/api/webhooks/<digits>/<token>`; `discordapp.com`, `ptb.` and
      `canary.` hosts are accepted.
    - Spec B2's find mirror may reuse it, with the commander's consent then.
  - **Sending.** The post goes through `edmrn/ext/api.py` like every external request, with a
    redacted URL in any log line. It sends `?wait=true`, honours a 429's `retry_after` once, and
    reports success or the reason in the dialog.
  - **Warning.** A webhook posts into a channel, not a DM. Whoever reads the channel first can
    redeem the invite. The dialog says this next to the button and recommends a private channel and
    a short expiry.

### Protocol

- S6. **Frames.** WebSocket text frames holding one JSON object each, at most 1 MiB. Envelopes and
  signatures travel as base64url of the exact signed bytes (Spec A C2), never re-encoded.

  | Frame | Direction | Fields |
  |---|---|---|
  | `hello` | relay → client | `v` 1, `source` (AGPL source URL), `nonce` (32 random bytes) |
  | `auth` | client → relay | `group`, `author`, `sig` over `edmrn-crew-auth\n<group>\n<nonce>`; optional `genesis` or `join` (`e`, `s`) |
  | `have` | both | `heads`: author → highest stored `seq` |
  | `evs` | both | `items`: up to 100 `{e, s}`, at most 512 KiB of items |
  | `synced` | relay → client | none: the relay has sent everything the client lacked |
  | `st` | both | `e`, `s`: a signed status (S8) |
  | `err` | relay → client | `code`, optionally `author` and `seq` |

  Unknown frame types are ignored by both sides, so a later version can add frames.
- S7. **Session.**
  1. The relay sends `hello`. The client answers with `auth`. The relay closes a socket that is
     still unauthenticated after 10 s when it next handles any message; a timer would prevent
     hibernation.
  2. Creating a group: the relay's group is empty, the `auth` carries a `genesis` envelope of kind
     `group` whose `group_id` equals the URL's, both signed by `author`, and `author` is listed in
     the Worker variable `CREW_LEADERS`. Otherwise an empty group refuses every `auth`.
  3. Joining: the `auth` carries a `join` envelope by `author` that admits it under S2. A client
     that holds an own `join` for the group always sends its latest one; the relay ignores it once
     the author is a member.
  4. The relay refuses an `auth` from a non-member or a removed member (close code 4003) and a bad
     signature (4001). A second connection of the same author replaces the first (4000).
  5. After `auth`, each side sends `have`. Each side then sends the other, per author in ascending
     `seq`, every envelope above the other's head, and the relay ends with `synced`. The relay's
     `have` lists every member, with 0 for a member it holds nothing of; a client uploads only for
     the authors listed there. The relay also sends the latest status of every member: live ones
     and the persisted last known ones.
  6. Afterwards, new envelopes travel as `evs` the moment they are stored or received. The client
     gathers its own new envelopes for at most 5 s or 100 envelopes per frame.

  **Heads.** The catch-up point must never pass an envelope the receiver lacks.
  - The relay's head for an author is the highest `seq` it accepted through `evs`, kept in the
    `heads` table.
    - Per author, senders send in ascending `seq`, and a WebSocket delivers in order.
    - An envelope at or below the head is a duplicate and is ignored. One above it is accepted,
      even across a gap, because a lost database can leave a permanent gap in an author's `seq`
      (Spec A C4).
    - If the relay refuses an envelope (quota, size), it refuses the rest of that author's
      envelopes in the frame, so the head never passes a hole.
  - A `genesis` or `join` presented with `auth` is stored in the log and recorded as a roster
    change, but does not move the head. The new member's earlier envelopes, with lower `seq`, still
    upload. When the upload reaches the stored envelope, the insert is a no-op and the head moves
    past it.
  - The relay serves an author's envelopes, in catch-up and when forwarding, only up to that
    author's head. A client's log therefore holds a prefix of each friend's sequence, and its
    highest stored `seq` is a sound head.
  - The relay accepts envelopes of any member from any member, so a member can re-seed an emptied
    relay. A leader's `auth` always carries its `genesis`; the relay ignores it once the group
    exists.
  - The relay applies S2 to every roster envelope it stores, whether from `auth` or `evs`, and
    re-evaluates membership from all roster envelopes it holds.
    - In `evs` it accepts a non-member's `join` that admits its author.
    - After every admission it sends each connected member a fresh `have`.
    - A client offers, once per connection, the `join` of every author in its log that the relay's
      `have` does not list. An emptied relay therefore learns every member from any replica:
      first the leader's own envelopes, then the offered joins, then, after the fresh `have`, the
      admitted members' envelopes.
    - A client merges a fresh `have` with what it already sent, keeping the higher head per author.
  - The relay also refuses a `join` at `auth` when its own clock is past the invite's
    `expires_at`. The relay may be stricter than the clients, because a `join` it refuses reaches
    nobody.
- S8. **Status.** `st` carries canonical bytes of `{"author", "at", "status"}`, signed by the
  author, at most 8 KiB.
  - `status` holds commander, system, address, coordinates, ship type, name and ident, EDSY link,
    hull, fuel main and reservoir, fuel capacities and `paused`. Each field may be null; the
    validator follows C12 and S10, and the EDSY link may be at most 6,000 characters and must start
    with Spec A's EDSY prefix.
  - The client sends a status when a value changed, at most every 30 s, and only for the member
    author's commander. A status for a different active commander is never sent.
  - "Pause sharing" sends one status with `paused` true and nothing else. Then it stops sending
    statuses and own envelopes, and keeps receiving. Resuming catches up through `have`, so a
    pause hides live position, not history.
  - The relay accepts at most one status per member per 30 s, keeps the latest in the sender's
    socket attachment, and forwards it. The attachment survives hibernation and is not SQLite
    storage.
    - Cloudflare does not say whether an attachment write is billed as a row
      (`group-sync-transport.md` §6). Even if it is, the cost is at most 120 rows per playing
      member per hour.
    - The relay writes a member's latest status to its `status` table once, when that member's
      socket closes.
  - Received statuses live in memory only (`CrewBoard`), keyed by author; a status with an older
    `at` than the one held is dropped.
- S9. **Errors.** `err` codes: `quota`, `group_quota`, `not_member`, `removed`, `bad_sig`,
  `too_big`, `bad_frame`, `rate`. The client logs each code once per connection.
  - It reconnects with backoff from 1 s doubling to 5 min, with ±20 % jitter.
  - It stops reconnecting after a close with 4001 or 4003 until the commander acts in the dialog.
  - After a close with 4000 (another connection of the same author took over, for example a
    second PC), it waits 60 s before reconnecting, so two installations do not take turns every
    second.

### Received data

- S10. **Characters (amends Spec A C12).** Every string in every payload, own or received, may
  contain only characters of the Unicode general categories L, M, N, P, S and Zs. That excludes
  control and format characters (bidi, U+200E/F, U+061C, U+FEFF), line and paragraph separators,
  surrogates, private use and unassigned code points. The rule lives in the validator module.
- S11. **Received timestamps (amends Spec A C8).** An envelope by another author is projected only
  if its `created_at` is a real calendar time, not before 2014-12-16T00:00:00Z and not more than
  24 hours after the local clock at projection time.
  - System name and coordinates prefer reports by own authors. Among own reports, and then among
    everyone else's, the earliest wins, as before.
  - The first run after this change calls `rebuild_projections()` once, by a migration.
- S12. **Log volume.**
  - `PayloadError` messages are cut to 200 characters.
  - `rebuild_projections()` logs one summary line with the count of unprojected envelopes per
    reason, instead of one warning per envelope.
  - An envelope that arrives at a known (`author`, `seq`) with different bytes (equivocation) is
    dropped as before, and logs one warning per author per run.

### Budget (Cloudflare Free plan)

- S13. **Cost per operation.** The binding limit is 100,000 SQLite rows written per day per
  account. The relay therefore writes:
  - one row per stored envelope. The log table is `WITHOUT ROWID` with (`author`, `seq`) as its
    only key, because every secondary index adds a written row per insert;
  - one `heads` row and one `quota` row per accepted `evs` frame and author, not per envelope;
  - one `status` row per closed member socket;
  - a few rows per roster change.

  The relay keeps no in-memory state that a wake from hibernation would have to rebuild by scanning
  the log: `heads` answers `have`, and `quota` answers the limits. Both are per-author rows.
- S14. **Limits.**
  - Each member may store at most 5,000 envelopes per UTC day (`err quota`).
  - The group may store at most 80,000 per UTC day (`err group_quota`). The rest of the account's
    100,000 stays as headroom for status rows and other Workers.
  - Roster envelopes are exempt from both limits.
  - Refused envelopes are offered again at the next `have`, so nothing is lost; it arrives a day
    later.
- S15. **Requests.** Envelopes travel in frames of up to 100. Cloudflare bills incoming WebSocket
  messages at 20:1 and outgoing ones not at all, so 2,000 uploaded envelopes cost about one
  billed request. A connection is one request; reconnect backoff bounds reconnects. Keepalive uses
  WebSocket protocol pings, which are not billed.
- S16. **Expected load.**
  - **A commander's evening (4 h):**
    - on the neutron highway, about 60 jumps per hour at about 3 envelopes, so about 700 rows;
    - full FSS exploration, about 30 systems per hour at about 25 envelopes, so about 3,000 rows.
  - **Five commanders exploring fully all evening:** about 15,000 rows, 15 % of the limit, plus at
    most 2,400 if status attachments are billed.
  - **Joining:** a new member uploads the history EDMRN holds once, a few thousand rows.

  These estimates are not measurements. The relay logs its daily totals, so the first weeks of use
  correct them.

### Relay

- S17. **Worker.**
  - **Source:** TypeScript in the top-level folder `crew-relay/`, AGPL-3.0, deployed with
    Wrangler.
  - **Durable Object:** one SQLite-backed Durable Object class, one instance per group, addressed
    by `idFromName(group_id)`.
  - **Routes:**
    - `GET /` answers a plain-text greeting naming the source URL (Worker variable `SOURCE_URL`).
    - `GET /join` answers the static invite page (S5), with a strict Content-Security-Policy and no
      external resources.
    - `GET /v1/groups/<group_id>/ws` with a WebSocket upgrade goes to the group's object.
    - Anything else answers 404.

    A malformed `group_id` (not a UUID) is refused before the object is touched.
  - **Domain:** `galroute.the-hub.cloud` as a custom domain in `wrangler.toml`. Cloudflare creates
    the DNS record and certificate on deploy.
- S18. **Durable Object.**
  - **Tables:**
    - `meta` (group, leader);
    - `log`, `heads`, `quota`, `status`;
    - `roster`, the parsed roster envelopes that passed the checks, a few rows per group.

    Membership is computed from `roster` in memory and recomputed after each roster change and
    after a wake from hibernation. A few rows read cost nothing that matters; a `members` table
    would cost writes.
  - **WebSockets** use the Hibernation API. Per-socket state (author, status, the last status
    time) lives in the attachment.
  - **Checks:** the relay checks frame size, envelope structure (Spec A C2) and the Ed25519
    signature with Web Crypto (`Ed25519`) on every envelope. It refuses small-order public keys,
    as the client does.
    - It parses only roster payloads, with the rules of S1 and S2.
    - A roster envelope must also be in canonical form: re-encoding the parsed object must give the
      received bytes. This refuses duplicate keys and number spellings that Python reads
      differently.
    - A member's signed envelope whose roster payload fails these checks is still stored and
      served, as on the clients (ADR-0007), but never changes membership.
    - It never validates or projects other payloads, and never evaluates content in B1.
  - **Cleanup:** when the first frame of a new UTC day is accepted, the `quota` rows of past days
    are logged as one daily-total line per day, then deleted. Each past day is logged once.

### Client

- S19. **Connection.**
  - A daemon thread `edmrn-crew-sync` runs the `websockets` synchronous client (BSD-3, added to
    `requirements-edmrn.txt`).
  - It never writes `edmrn.db`. Received items go on a bounded queue, and a Tk-loop applier stores
    them through `insert_received` in batches. It uses the writer's pattern: 200 ms busy timeout,
    requeue on a lock.
  - For uploads, the thread reads the log through `Store.read()` in pages of 100, so the Tk thread
    never waits long for the lock.
  - Without `websockets`, or with crew capture disabled, sync is disabled and logged once; the
    rest of EDMRN runs.
- S20. **Dialog.** A minimal Tk dialog, opened by one button in upstream's Settings tab (a hook
  point, ADR-0002). It shows:
  - the active commander and its author key, with a copy button (the key goes into
    `CREW_LEADERS`);
  - with no group: "Create group" (name, and the relay address prefilled with
    `wss://galroute.the-hub.cloud`) and "Join with invite" (paste box). The connection URL is the
    address plus `/v1/groups/<group_id>/ws`;
  - in a group: the connection state, the member list, "Pause sharing", and "Leave", which deletes
    `group.json` and nothing else;
  - for the leader:
    - "Create invite", with a label and an expiry, which signs the `invite` and shows S5a;
    - a field for the Discord webhook URL;
    - "Remove" per member.

  The dialog's text is English, like all UI text.
- S21. **No real folders and no network in tests** (Spec A C13).

## Design

### Upstream hook points (ADR-0002)

- `edmrn/settings_manager.py` (or the Settings tab builder the plan finds): two lines that add a
  "Crew…" button calling `edmrn.ext.crew_dialog.open_dialog(app)`.

### Our modules

- `crew_events.py`: the roster kinds' validators (S1), the category whitelist (S10), the
  timestamp plausibility (S11) and the status validator (S8). Pure.
- `crew_roster.py`: membership from roster envelopes (S2), the invite string and link (S5), the
  webhook URL check (S5a) and `group.json` (S4). Pure except for the file.
- `crew_store.py`:
  - the projection gate (S3), with membership computed from the log's roster envelopes and cached
    in memory;
  - the own-first system merge (S11) and the log-volume rules (S12);
  - `heads()`;
  - a batch insert for received envelopes, one transaction per batch.
- `crew_group.py`: the controller behind the dialog: create, join, invite, remove, leave, pause,
  webhook. Testable without Tk.
- `crew_discord.py`: the invite message and its post (S5a).
- `crew_protocol.py`: frame build and parse, the auth message, status bytes. Pure, no I/O.
- `crew_sync.py`: the connection thread, the Tk-loop applier, the status sender, pause, backoff,
  `CrewBoard`, and the connection state for the dialog.
- `crew_dialog.py`: S20.
- `crew.py`: `install` also starts sync when a `group.json` exists. `app.crew` gains `sync`.

### Relay modules (`crew-relay/`)

- `src/index.ts`: routing (S17).
- `src/group.ts`: the Durable Object (S7, S8, S13, S14, S18).
- `src/envelope.ts`: canonical structure checks, base64url, Ed25519 verification.
- `src/roster.ts`: S1, S2 and S10 for roster payloads.
- `test/`: Vitest with `@cloudflare/vitest-pool-workers`.
- `wrangler.toml`: the Durable Object binding with a SQLite migration, the custom domain, the
  variables `CREW_LEADERS` and `SOURCE_URL`.

### Data flow

1. The writer stores an own envelope (Spec A). `crew_sync` sees the new log rows by polling its
   own author's highest `seq` after each writer tick and batches them into `evs` (S7 step 6).
2. The relay verifies, stores, counts and forwards the frame to the other members' sockets.
3. A receiving client's thread queues the items; the Tk-loop applier stores them with
   `insert_received`, and the projector applies the gate (S3) and Spec A's validators.
4. Statuses take the same path without the log: `CrewStatus` observer → sender → relay attachment
   → other clients' `CrewBoard`.

## Error handling

- **Relay unreachable:** backoff (S9); capture continues; everything is sent at the next
  connection.
- **Daily limit (ours or Cloudflare's):** the relay refuses with `quota` or `group_quota`, or
  Cloudflare answers with an error. The client logs once and retries at the next connection.
  Nothing is lost.
- **Invalid `group.json`:** sync is disabled and logged. The dialog offers "Leave", which deletes
  the file.
- **Lost leader key:** the group cannot admit or remove members any more. A new group needs a new
  leader key in `CREW_LEADERS`. Leadership transfer is out of scope.
- **Lost database:** the next connection's `have` is empty for every author, so the relay sends the
  whole log, own envelopes included, and the writer continues above the key file's high-water mark
  (Spec A C4).
- **Relay storage:** about 1 KB per envelope. At the expected load a group stays far below the
  Free plan's per-object storage limit for years.

## Testing

pytest for the client, Vitest for the relay; neither touches real folders or the network.

- **Shared vectors:** `crew-relay/test/vectors.json`, generated by a Python script and committed.
  It holds envelopes, signatures, an invite string, roster sequences with their expected
  memberships, and strings the whitelist must refuse. Both test suites must pass them, so the
  relay and the client cannot disagree.
- **Roster (both sides):**
  - genesis by a non-leader is ignored;
  - a second `join` on one invite does not admit;
  - an expired invite and a wrong token do not admit;
  - `remove` caps at `last_seq`, takes the lowest of several, and never applies to the leader;
  - the relay refuses an empty group's genesis from an author not in `CREW_LEADERS`.
- **Heads (relay):**
  - a `join` at `auth` does not move the head, and the member's earlier envelopes still upload;
  - nothing above the head is served;
  - a gap in an author's `seq` is accepted;
  - a re-seed by the leader admits the other members again from their stored `join`s.
- **Protocol (client, against an in-process fake relay that plays scripted frames):**
  - `auth` signs the nonce;
  - catch-up sends exactly what the other side lacks, in ascending `seq`;
  - a refused envelope stops that author's frame;
  - pause sends one paused status, then nothing own;
  - backoff grows and stops after 4001 or 4003;
  - the applier requeues on a locked store.
- **Budget (relay):**
  - one stored envelope writes exactly one `log` row (read through the Durable Object's SQL
    cursor counters);
  - one accepted frame writes one `heads` and one `quota` row per author;
  - a status writes no SQLite row until the socket closes;
  - the per-member and group limits refuse with the right code and leave the head before the
    hole.
- **Hardening (client):**
  - every excluded category is refused;
  - received stamps before 2014-12-16, after now + 24 h, or impossible (`2026-02-30`) are stored
    but not projected;
  - an own report of a system name wins over an earlier friend's;
  - equivocation warns once per author;
  - a rebuild logs one summary.
- **Invite delivery (client):**
  - the link and the bare string both parse;
  - a hostile fragment (markup, a 10,000-character string, a wrong prefix) is refused;
  - the webhook URL validator accepts the four hosts and refuses others, `http:`, and extra path
    parts;
  - the post sends `allowed_mentions` with an empty `parse` and `wait=true` through a fake
    transport;
  - no log line and no error text contains the webhook token.
- **Invite page (relay):** it renders the fragment as text, and its CSP allows only its own
  script, by hash.
- **End to end (opt-in, marked, not in the default run):** two clients against `wrangler dev` on
  localhost exchange a visit, a status and an invite. This needs Node but no Cloudflare account.

## Not in scope

- Chat, the leader's route, expeditions, mirroring finds to Discord (Spec B2).
- Revoking an unredeemed invite before it expires.
- Peer-to-peer transports (ADR-0005's staged fallback).
- Leadership transfer, re-admitting a removed key, several groups per installation.
- The crew window, commander cards, key export and import UI (Spec C).
- Encryption of content against the relay (ADR-0005: the relay may read content).
