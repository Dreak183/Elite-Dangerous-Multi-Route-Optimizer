# Self-hosted crew relay in Python — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Cloudflare Worker in `crew-relay/` with a Python asyncio relay in `relay/`. It
speaks the same wire protocol, imports the client's protocol modules, runs on the commander's server
behind Caddy or an existing reverse proxy, and is tested against the real EDMRN client on 127.0.0.1.

**Architecture:** The engine (`engine.py`: `Relay` → one `Group` per group id → one `Session` per
socket) is synchronous and has no sockets. It turns each received frame into a list of actions
(send text to a session, close a session with a code) and persists through `RelayStore`: one SQLite
file in WAL mode, with `group_id` in every table. `server.py` wraps the engine in
`websockets.asyncio.server`. `process_request` answers the HTTP routes. The connection handler feeds
frames to the engine, and each connection's FIFO outbox writes the resulting actions to its socket.
`__main__.py` reads the configuration and handles signals.

**Tech Stack:** Python 3.13, `websockets` (asyncio server), `cryptography` (through
`crew_identity`), `sqlite3`, pytest; Docker Compose with Caddy 2, or systemd behind nginx.

**Spec:** `docs/superpowers/specs/2026-11-08-self-hosted-relay-design.md` (authority) and ADR-0009.
The behaviour authority it amends is `docs/superpowers/specs/2026-10-13-crew-sync-core-design.md`
(Spec B1), S5–S9, S14, S17, S18. The behaviour reference until Task 9 deletes it is
`crew-relay/src/group.ts` with `crew-relay/test/*.ts`. `cd crew-relay && npx vitest run` may be run
for reference, but it is not part of the deliverable.

## Global Constraints

- **Python and dependencies.** Python 3.13 (`setup.py`); the image is `python:3.13-slim`. The relay
  uses the standard library, `websockets` and `cryptography`, and nothing else.
  `relay/requirements.txt` holds exactly the two specifiers from `requirements-edmrn.txt`
  (`cryptography>=42`, `websockets>=15`) (R7).
- **One implementation of shared rules (R2).** The relay imports `edmrn.ext.crew_events` (as `ev`),
  `crew_identity` (`verify`), `crew_roster` and `crew_protocol` (as `proto`). It never
  re-implements a rule those modules hold: envelope structure, canonical form, base64url,
  signatures and small-order keys, roster payloads, plausibility, membership, frame decoding,
  `have` parsing, `evs` batching, status bytes, close codes, size limits.
- **Forbidden imports.** The relay imports no other `edmrn` module and never calls
  `ev.validate_status` or `proto.parse_status`. Reason: `ev._edsy` imports
  `edmrn.ext.crew_status`, which imports `edmrn.logger`. That module creates a log folder under the
  home directory, and it is not in the image.
- **No client change** (spec, "Not in scope"). Task 9 edits two docstrings in `edmrn/`; nothing
  else there changes. If a task finds a client defect, it goes into the task report, not into code.
- **Wire protocol unchanged.** A client built for the Worker works against this relay unchanged.
  - Close codes: 4000 replaced, 4001 bad auth, 4003 forbidden and 4008 policy (taken from `proto`),
    1001 relay shutting down, 1011 internal error.
  - `err` codes: `proto.ERROR_CODES`.
- **Which behaviour wins.** Spec B1 as amended by the relay spec is the authority; the TS tests are
  the parity reference. Where the two disagree, B1 wins, and the task names the ruling.
- **Tests never touch the network or real folders.**
  - `tests/conftest.py` blocks the network; from Task 6, tests marked `loopback` may connect to
    `127.0.0.1` only. It also redirects the home folders.
  - Every database lives under `tmp_path`.
  - On Windows every new asyncio event loop connects a loopback socket pair for its self-pipe
    (verified: `socket.socketpair` is `_fallback_socketpair`). Every test that starts an event loop
    is therefore marked `loopback`.
- **Layout.** The package is `relay/edmrn_relay/`. Tests are `tests/test_relay_*.py`, sharing the
  harness `tests/relay_fakes.py`. `pytest.ini` puts `relay` on `pythonpath` (Task 1).
- **Licence.** AGPL-3.0-only, through the root `LICENSE`. The repo puts no licence header in its
  files. Instead, the docstring of `relay/edmrn_relay/__init__.py` names the licence and ADR-0009,
  the image carries `LICENSE`, and `GET /` names the source URL.
- **Logging.** Logger `edmrn_relay`, stdout, one line per event.
  - Events logged: group created, member admitted, relay-initiated close (code and reason), daily
    total, internal error (with traceback).
  - Authors appear only as their first 12 characters. Envelope bytes, status bytes, payload values,
    signatures, nonces and invite tokens never appear.
- **Conventions.** English everywhere. Stage explicit paths only; never `git add -A`, `.` or `-u`.
  Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016pRinjRCPAEdwuTLmCSLGM
  ```
- **Test command.** `.venv/Scripts/python.exe -m pytest -q` from the repo root; the baseline is
  `1086 passed, 2 skipped`. Every task ends with this suite green: no failures, and the passed count
  grows by the task's new tests.
- **Documentation.** A change and the documents it makes false are updated in the same commit
  (`docs/README.md`). `docs/handover/HANDOVER.md` is rewritten by the controller after the final
  review, not by a task.

## Rulings (binding for every task)

1. **Status rate (B1 S8 over the Worker).** "At most one status per member per 30 s" is kept per
   member author in the group's memory (`Group.status_at`). The Worker kept it per socket, so a
   reconnect reset it; here it does not. Client consequence, recorded and not fixed: after a refusal
   with `rate`, the client does not resend that status until a value changes.
2. **Frame parsing is the client's (R2).**
   - Frames go through `proto.decode`: `t` must be 1–32 printable ASCII characters, and duplicate
     keys are refused.
   - `have` goes through `proto.parse_have`: canonical author keys, integer heads from 0 to
     2^53−1.
   - The Worker accepted more and read a bad head as 0. Here a failure answers `err bad_frame`.
   - `evs` items are judged one by one (`decode_item`), because B1 S7 keeps the items before an
     unattributable one; `proto.parse_evs` would refuse the whole frame.
3. **The relay never validates status content** (B1 S18: it never validates payloads other than
   roster ones). It checks only these things:
   - the bytes parse as a JSON object;
   - `author` equals the session's author, `at` is a string, `status` is an object;
   - the signature verifies.
4. **Unknown groups.** This amends the Worker's "no row before genesis".
   - A group id with no stored group writes nothing.
   - Its `Group` object is dropped from memory when its last session ends.
   - A stored group stays in memory once loaded (R1 S18).
5. **Auth timeout (R1 S7.1).**
   - The server arms a 10 s timer per socket that calls `Relay.expire_auth`.
   - `expire_auth` closes any session still unauthenticated (4001 `auth timeout`) without reading
     the clock, because a loop timer can fire slightly before 10 s of wall-clock time have passed.
   - `receive` also closes a session still in the hello state whose `opened_at` is more than
     `AUTH_TIMEOUT` old (TS case P4).
6. **Ordering.**
   - Each connection has one FIFO outbox, drained by one writer task.
   - The server enqueues the engine's actions without awaiting. Every client therefore receives
     frames in engine order, and a stalled peer delays only itself.
   - Outboxes are unbounded, like the Worker's socket buffers. The `websockets` keepalive (library
     defaults) closes dead peers.
7. **Wire size.** The server's `max_size` is `2 * proto.MAX_FRAME_BYTES`.
   - A text frame between 1 MiB and 2 MiB reaches the engine, which closes it with 4008 `too_big`
     (Worker parity).
   - A larger frame is closed by `websockets` with 1009.
8. **Routes.**
   - Routes accept GET only, as the Worker's `index.ts` did. The query string is ignored.
   - The Durable Object's indifference to the HTTP method (TS test "does not depend on the HTTP
     method of the upgrade request") was a detail of the Workers runtime and is dropped.
9. **`--leaders`.** Entries are separated by commas and/or whitespace. An entry that is not 43
   base64url characters stops the relay at startup with exit code 2 and a message naming the entry.
10. **`evs` size accounting.** The relay imports the private `proto._ITEM_OVERHEAD` for the 512 KiB
    `evs` limit, so it counts exactly as the client batches. Making the name public would be a
    client change.
11. **Backup (R4).** `python:3.13-slim` has no `sqlite3` command-line tool.
    - Option A backs up with `sqlite3.Connection.backup` through `python -c` inside the container.
    - Option B uses `sqlite3 relay.db ".backup …"`.
12. **DNS.** The relay needs a `galroute` A/AAAA record in the `the-hub.cloud` zone with proxy
    status "DNS only". Caddy, or the existing reverse proxy, then ends TLS with its own certificate.

## Review Focus

1. **Imports outside the image.** The image holds only six `edmrn` files. Any path that imports
   `crew_status` or `edmrn.logger` (status validation through `ev._edsy`) would crash the relay or
   write into a home folder on the server. Expected: those modules are never imported. Tests: Task 5
   `test_status_content_is_never_validated`, Task 8
   `test_image_layout_imports_only_the_shared_modules`.
2. **Restart with members connected** (`docker compose up -d --build` or `systemctl restart`).
   Expected: statuses are persisted, clients are closed with 1001 and reconnect, and heads, roster
   and statuses come back from SQLite. Tests: Task 3 `test_a_restarted_relay_serves_from_storage`,
   Task 6 `test_run_stops_on_the_event_and_persists_statuses`.
3. **A stalled member connection** (bad Wi-Fi, a suspended laptop). Expected: frames to the other
   members are not delayed, and each client still gets frames in engine order. Test: Task 6
   `test_a_stalled_peer_does_not_delay_others`.
4. **Scans or typos hitting many `/v1/groups/<uuidv7>/ws` paths.** Expected: no row is written and
   no `Group` stays in memory for any of those ids. Test: Task 2
   `test_unknown_groups_leave_nothing_behind`.
5. **A reverse proxy that forwards a query string.** Expected: routing is unchanged. Tests: Task 6
   `test_routes_ignore_the_query_string` and `test_a_session_over_the_wire` (connects with `?x=1`).

## File map

| Path | Task | Responsibility |
|---|---|---|
| `relay/edmrn_relay/__init__.py` | 1 | package docstring: what it is, AGPL-3.0-only, ADR-0009 |
| `relay/edmrn_relay/store.py` | 1 | `RelayStore`: schema, `user_version` migration, the engine's queries |
| `relay/edmrn_relay/config.py` | 2, 6 | `RelayConfig` (Task 2); `load_config` from argv and environment (Task 6) |
| `relay/edmrn_relay/engine.py` | 2–5 | `Relay`, `Group`, `Session`, actions, item decoding; every protocol decision |
| `relay/edmrn_relay/join_page.py` | 6 | the Worker's invite page byte for byte, its headers, the greeting |
| `relay/edmrn_relay/server.py` | 6 | routes, `process_request`, `Peer` outboxes, `RelayServer` |
| `relay/edmrn_relay/__main__.py` | 6 | `run`, `main`: configuration, logging, signals |
| `relay/requirements.txt`, `relay/Dockerfile`, `relay/Dockerfile.dockerignore`, `relay/compose.yaml`, `relay/Caddyfile`, `relay/edmrn-relay.service` | 8 | deployment |
| `tests/relay_fakes.py` | 2, 5, 7 | harness: keys, envelopes, `FakeConn`, `Harness` over the engine, `ServerThread` (Task 7) |
| `tests/test_relay_store.py` | 1 | store |
| `tests/test_relay_session.py` | 2 | hello, auth, frame checks |
| `tests/test_relay_vectors.py` | 2, 4, 5 | the relay's own paths against `test_crew_vectors.VECTORS` |
| `tests/test_relay_uploads.py` | 3 | catch-up, uploads, refusals, quotas |
| `tests/test_relay_roster.py` | 4 | joins, re-seed, removal |
| `tests/test_relay_status.py` | 5 | statuses, shutdown, logs |
| `tests/test_relay_http.py` | 6 | routes, invite page, configuration (no sockets) |
| `tests/test_relay_server.py` | 6 | the asyncio server (`loopback`) |
| `tests/test_relay_loopback.py` | 7 | two EDMRN clients through the real relay (`loopback`) |
| `tests/test_relay_deploy.py` | 8 | image layout, requirements, ports |

## Where the Worker's tests go

- **Ported:** every `it(...)` in `group.test.ts`, `status.test.ts` and `routes.test.ts`, named in
  the parity tables of Tasks 2–6.
- **Dropped:**
  - `budget.test.ts`: S13 no longer applies.
  - "does not depend on the HTTP method of the upgrade request": ruling 8.
  - "the serial chain keeps a have behind a slow auth": the engine is synchronous, so all of a
    frame's actions exist before the next frame is handled. The test becomes Task 3
    `test_have_right_behind_auth_is_answered_in_order`.
  - `b64.test.ts`: covered by `tests/test_crew_protocol.py::test_b64_round_trip_and_strictness`.
- **Split:** in `vectors.test.ts`, the shared-module cases are already covered by
  `tests/test_crew_vectors.py`; the relay's own decoding, auth, roster-reload and status paths are
  ported to `tests/test_relay_vectors.py`.
- **Adapted:**

  | Worker mechanism | Python equivalent |
  |---|---|
  | object eviction | a relay restarted over the same database |
  | editing attachments | `Session` fields, or advancing the harness clock |
  | the `writes` counter | `store.db.total_changes` |
  | `roster WHERE kind = 'invite'` | roster rows by `seq` (the spec's `roster` table has no `kind` column) |
  | `dailyTotals` | `caplog` |

---

### Task 1: Relay store

**Files:**
- Create: `relay/edmrn_relay/__init__.py`, `relay/edmrn_relay/store.py`
- Modify: `pytest.ini` (`pythonpath = . relay`)
- Modify: `docs/architecture/overview.md`. In "Fork layout", add the row
  `` | `relay/` | EDMRN (Python crew relay, AGPL-3.0; ADR-0009) | `` after the `crew-relay/` row.
- Test: `tests/test_relay_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces (`edmrn_relay.store`):
  ```python
  SCHEMA_VERSION = 1
  class RelayStoreError(Exception): ...
  class LogRow(NamedTuple): author: str; seq: int; e: bytes; s: bytes
  class LoadedGroup(NamedTuple): leader: str; heads: dict[str, int]; roster: list[bytes]
  class RelayStore:
      db: sqlite3.Connection                      # tests read and seed through it
      def __init__(self, path: str | os.PathLike) -> None
      def close(self) -> None
      def load_group(self, group_id: str) -> LoadedGroup | None
      def roster_bytes(self, group_id: str) -> list[bytes]
      def create_group(self, group_id: str, leader: str, created_at: int, genesis: LogRow) -> None
      def add_roster_envelope(self, group_id: str, row: LogRow) -> None
      def apply_frame(self, group_id: str, day: int, log_rows: Sequence[LogRow],
                      roster_rows: Sequence[LogRow], heads: Mapping[str, int],
                      counts: Mapping[str, int]) -> bool
      def log_after(self, group_id: str, author: str, after: int, until: int, limit: int) -> list[LogRow]
      def quota_used(self, group_id: str, day: int, author: str) -> int
      def group_quota_used(self, group_id: str, day: int) -> int
      def take_past_quota(self, group_id: str, day: int) -> list[tuple[int, int]]
      def statuses(self, group_id: str) -> list[tuple[str, bytes, bytes]]
      def save_status(self, group_id: str, author: str, e: bytes, s: bytes) -> None
  ```

**Semantics:**
- **Schema:** exactly the spec's six tables (spec, "Schema"). `user_version` 0 → create the schema,
  set 1. Above 1 → `RelayStoreError` with a message containing `newer`.
- **Connection:** file databases use `PRAGMA journal_mode=WAL`. One connection, used on one thread.
- **Transactions:** every write method is one transaction; on any error nothing it wrote remains.
- **Log and roster rows:** `create_group`, `add_roster_envelope` and `apply_frame` insert them with
  `INSERT OR IGNORE`.
- **Heads:** only `apply_frame` touches `heads`. It upserts each given author to the given value.
- **Quota:** `apply_frame` upserts `counts` by addition.
- **Return value:** `apply_frame` returns `True` exactly when at least one roster row was newly
  inserted.
- **Loading:** `load_group` returns the leader, the heads table, and the roster bytes ordered by
  (author, seq).
- **Paging:** `log_after` returns at most `limit` rows with `after < seq <= until`, ascending.
- **Past quota:** `take_past_quota` returns `(day, total)` for every day before `day`, ascending,
  and deletes those rows in the same transaction.
- **No parsing:** the store never parses envelopes.

- [ ] **Step 1: Write the failing tests** in `tests/test_relay_store.py`.

  Fixtures: `store = RelayStore(tmp_path / "relay.db")`, `A = "A" * 43`, `B = "B" * 43`,
  `G1 = "01920000-0000-7000-8000-000000000001"`, `G2 = "...-000000000002"`, and
  `row(author, seq) -> LogRow(author, seq, f"e{seq}".encode(), bytes(64))`.

  | Test | Assertions |
  |---|---|
  | `test_new_database_is_version_1_in_wal_mode` | `PRAGMA user_version` is 1; `PRAGMA journal_mode` is `'wal'`; the tables are `{'groups','log','heads','roster','quota','status'}`, and each has a `group_id` column (`PRAGMA table_info`) |
  | `test_reopening_keeps_data` | after `create_group(G1, A, 100, row(A, 1))`, `close()` and reopen: `load_group(G1) == LoadedGroup(A, {}, [b"e1"])` |
  | `test_newer_schema_is_refused` | set `PRAGMA user_version = 2` and close; `pytest.raises(RelayStoreError, match="newer")` on reopen |
  | `test_unknown_group_is_none_and_writes_nothing` | `load_group(G1) is None`; `store.db.total_changes == 0` |
  | `test_create_group_moves_no_head` | `load_group(G1).heads == {}`; `log_after(G1, A, 0, 10, 100) == [row(A, 1)]`; `roster_bytes(G1) == [b"e1"]` |
  | `test_groups_are_isolated` | the same `(A, 1)` row created in G1 and G2 with different bytes; each `log_after` and `roster_bytes` returns only its own |
  | `test_apply_frame_writes_everything_and_reports_new_roster_rows` | the first `apply_frame(G1, 5, [row(A, 2)], [row(A, 2)], {A: 2}, {A: 2})` is `True`; repeating it is `False`; `load_group(G1).heads == {A: 2}`; `quota_used(G1, 5, A) == 4`; `group_quota_used(G1, 5) == 4` |
  | `test_apply_frame_is_one_transaction` | `db.execute("CREATE TRIGGER boom BEFORE INSERT ON quota BEGIN SELECT RAISE(ABORT, 'boom'); END")`; `apply_frame(G1, 5, [row(A, 2)], [row(A, 2)], {A: 2}, {A: 1})` raises `sqlite3.DatabaseError`; afterwards `log_after(G1, A, 1, 9, 9) == []`, the heads are `{}`, and the roster is still `[b"e1"]` |
  | `test_log_after_pages_ascending_up_to_until` | rows `(A, 1..5)` in one frame; `[r.seq for r in log_after(G1, A, 1, 4, 2)] == [2, 3]` |
  | `test_take_past_quota_returns_each_past_day_once` | counts: day 3 (`A` 7, `B` 5), day 4 (`A` 3), day 5 (`A` 4). `take_past_quota(G1, 5) == [(3, 12), (4, 3)]`; a second call returns `[]`; `quota_used(G1, 5, A) == 4` |
  | `test_status_upsert` | `save_status` twice for A; `statuses(G1) == [(A, b"e2", b"s2")]` |

- [ ] **Step 2: Run the tests to verify they fail.**
  Run: `.venv/Scripts/python.exe -m pytest tests/test_relay_store.py -q`
  Expected: errors with `ModuleNotFoundError: No module named 'edmrn_relay'`. Without the
  `pytest.ini` change the error is the same, so make that change in Step 3.

- [ ] **Step 3: Implement** `relay/edmrn_relay/__init__.py` (docstring only), `store.py`, the
  `pytest.ini` line and the overview row.

- [ ] **Step 4: Run the tests to verify they pass.** Same command. Expected: all pass.

- [ ] **Step 5: Run the full suite.** `.venv/Scripts/python.exe -m pytest -q`. Expected: no
  failures.

- [ ] **Step 6: Commit.**
  ```bash
  git add relay/edmrn_relay/__init__.py relay/edmrn_relay/store.py pytest.ini docs/architecture/overview.md tests/test_relay_store.py
  git commit -m "feat(relay): SQLite store for the Python crew relay (ADR-0009)"
  ```
  The message body ends with the two trailer lines (Global Constraints).

---

### Task 2: Engine — sessions, auth and frame checks

**Files:**
- Create: `relay/edmrn_relay/config.py` (`RelayConfig` only), `relay/edmrn_relay/engine.py`
- Create: `tests/relay_fakes.py`
- Test: `tests/test_relay_session.py`, `tests/test_relay_vectors.py`

**Interfaces:**
- Consumes: Task 1's `RelayStore` and `LogRow`.
- Produces (`edmrn_relay.config`):
  ```python
  DEFAULT_PORT = 8765; DEFAULT_MEMBER_QUOTA = 5000; DEFAULT_GROUP_QUOTA = 80000
  @dataclass(frozen=True)
  class RelayConfig:
      host: str = "127.0.0.1"; port: int = DEFAULT_PORT; db: Path = Path("relay.db")
      leaders: frozenset[str] = frozenset(); source_url: str = ""
      member_quota: int = DEFAULT_MEMBER_QUOTA; group_quota: int = DEFAULT_GROUP_QUOTA
  ```
- Produces (`edmrn_relay.engine`); `RosterEnvelope` and `Roster` come from `edmrn.ext.crew_roster`:
  ```python
  AUTH_TIMEOUT = 10.0          # read at call time, so tests may monkeypatch it
  STATUS_INTERVAL = 30.0
  MAX_REFUSALS = 64
  MAX_E_CHARS = -(-ev.MAX_ENVELOPE_BYTES * 4 // 3)          # 21846
  MAX_S_CHARS = -(-proto.SIGNATURE_BYTES * 4 // 3)          # 86
  MAX_STATUS_CHARS = -(-proto.MAX_STATUS_BYTES * 4 // 3)    # 10923
  CLOSE_GOING_AWAY, CLOSE_INTERNAL = 1001, 1011
  def utc_day(now: float) -> int                            # int(now // 86400)

  @dataclass(eq=False)                                      # identity, hashable
  class Session:
      group_id: str; conn: Any; nonce: str; opened_at: float
      state: str = "hello"                                  # "hello" | "member" | "closed"
      author: str | None = None
      status: tuple[bytes, bytes] | None = None             # Task 5
      refused: dict[str, tuple[int, int]] = field(default_factory=dict)  # author -> (first seq, day or 0); Task 3
      closing: bool = False

  @dataclass(eq=False)
  class Group:
      group_id: str; leader: str | None; heads: dict[str, int]
      roster_entries: list[RosterEnvelope]; roster: Roster | None
      sessions: list[Session]; status_at: dict[str, float]  # status_at: Task 5
      cleaned_day: int = -1                                 # Task 3

  @dataclass(frozen=True) class Send:  session: Session; text: str
  @dataclass(frozen=True) class Close: session: Session; code: int; reason: str
  Action = Send | Close
  @dataclass(frozen=True) class Checked: env: dict; e: bytes; s: bytes
  @dataclass(frozen=True) class BadSig: author: str; seq: int
  def decode_item(item: Any) -> Checked | BadSig | Literal["oversize", "undecodable"]
  def roster_entry(checked: Checked, now: float) -> RosterEnvelope | None

  class Relay:
      store: RelayStore; config: RelayConfig; groups: dict[str, Group]
      def __init__(self, store: RelayStore, config: RelayConfig, *,
                   now: Callable[[], float] = time.time,
                   token: Callable[[], bytes] = lambda: secrets.token_bytes(proto.NONCE_BYTES)) -> None
      def group(self, group_id: str) -> Group               # cached, or loaded from the store
      def reload_roster(self, group: Group) -> None
      def connect(self, group_id: str, conn: Any) -> tuple[Session, list[Action]]
      def receive(self, session: Session, message: str | bytes) -> list[Action]
      def expire_auth(self, session: Session) -> list[Action]
      def disconnect(self, session: Session) -> None
  ```
  The engine marks a session `closing` whenever it emits a `Close` for it.

**Harness** (`tests/relay_fakes.py`). Later tasks rely on these names.
```python
from test_crew_vectors import _generator      # same module object as the collected test module
TOKEN = "11" * 32
def key_for(name: str)  # _generator().Key(name): .author, .sign(data) -> bytes; identical to the vectors' keys
def strangers(n: int) -> list  # fresh random Ed25519 keys with .author and .sign
def uuid7() -> str             # f"01920000-0000-7000-8000-{n:012x}", counter from 0x1001
def new_group() -> str         # uuid7()
def stamp_at(epoch: float) -> str                                   # "YYYY-MM-DDTHH:MM:SSZ" (UTC)
def envelope_item(key, seq: int, kind: str, payload: dict, created_at: str, event_id: str | None = None) -> dict
    # {"e", "s"}: ev.canonical_bytes of the envelope (event_id defaults to uuid7()), signed by key
def auth_frame(key, group_id: str, nonce: str, **extra) -> dict    # signs proto.auth_message(group_id, nonce)
def status_frame(key, hull: float, at: str, author: str | None = None, **fields) -> dict
    # st over ev.canonical_bytes({"author", "at", "status"}); status = all 14 ev.STATUS_FIELDS:
    # commander "X", system "Sol", address "1", paused False, given hull, others None; **fields override
def decode(item: dict) -> dict             # ev.parse_envelope(proto.unb64(item["e"], ev.MAX_ENVELOPE_BYTES))
def items_of(frames: list[dict]) -> list[dict]   # every item of every evs frame, in order

class Clock:
    def __init__(self, start: float | None = None) -> None   # start defaults to time.time()
    now: float
    def __call__(self) -> float
    def advance(self, seconds: float) -> None
    def next_utc_day(self) -> None                           # now := start of the next UTC day + 1 s

class FakeConn:
    frames: list[dict]; closed: tuple[int, str] | None
    def next_of(self, t: str) -> dict       # consumes up to and including the first frame of type t; AssertionError if none
    def take(self, t: str | None = None) -> list[dict]   # consumes all; returns those of type t (all if None)

class Harness:
    def __init__(self, db_path: Path, *, leaders: Iterable[str] | None = None,  # default {key_for("leader").author}
                 member_quota: int = 5000, group_quota: int = 80000, clock: Clock | None = None,
                 token: Callable[[], bytes] | None = None,
                 source_url: str = "https://example.invalid/edmrn") -> None
    relay: Relay; store: RelayStore; clock: Clock; db_path: Path
    # applying actions: Send -> json.loads into the target FakeConn.frames;
    # Close -> target.closed = (code, reason), then relay.disconnect(session), as the server does
    def open(self, group_id: str) -> FakeConn                 # the hello stays in frames
    def send(self, conn: FakeConn, frame: dict | str | bytes) -> None   # dict -> proto.encode
    def close(self, conn: FakeConn) -> None                   # the peer closed: relay.disconnect
    def expire(self, conn: FakeConn) -> None                  # relay.expire_auth
    def session(self, conn: FakeConn) -> Session
    def stamp(self, offset: float = 0.0) -> str               # stamp_at(clock.now + offset)
    def envelope(self, key, seq, kind, payload, created_at: str | None = None, event_id=None) -> dict  # created_at default stamp(-60)
    def visit(self, key, seq: int, created_at: str | None = None) -> dict
        # payload {"commander": "A", "system": "Sol", "address": str(seq), "coords": None}
    def genesis(self, key, group_id: str) -> dict  # seq 1 "group" {"group_id", "name": "Crew", "commander": "Leader"}
    def authed(self, group_id: str, key, **extra) -> FakeConn # consumes hello, sends auth_frame (+ extra)
    def create_group(self, group_id: str, key) -> FakeConn    # authed(genesis=self.genesis(key, group_id))
    def head(self, group_id: str, author: str) -> int | None  # from the heads table
    def log_seqs(self, group_id: str, author: str) -> list[int]
    def roster_seqs(self, group_id: str, author: str) -> list[int]
    def table_counts(self, group_id: str) -> dict[str, int]   # rows per table for group_id
```

**Dispatch in `Relay.receive`** (D-steps):
- D0. `session.state == "closed"` or `session.closing` → return `[]`.
- D1. Hello state and `now - opened_at > AUTH_TIMEOUT` → `Close 4001 "auth timeout"`.
- D2. `bytes` → `Close 4008 "binary"`.
- D3. `len(message.encode("utf-8", "surrogatepass")) > proto.MAX_FRAME_BYTES` →
  `Close 4008 "too_big"`. No `err` is sent.
- D4. `proto.decode(message)` raises `FrameError` → `err bad_frame`.
- D5. Hello state: `t == "auth"` → auth; any other `t` → `Close 4001 "auth first"`.
- D6. Member state: `have` (Task 3), `evs` (Task 3) and `st` (Task 5) are handled; any other `t`
  is ignored.

**Auth** (U-steps; each `Close` ends the step list):
- U1. `frame.get("group") != session.group_id` or `not ev.is_author(frame.get("author"))` →
  `Close 4001 "bad auth"`.
- U2. Signature: if `proto.unb64(frame.get("sig"), proto.SIGNATURE_BYTES)` raises `FrameError`, or
  the result is not 64 bytes, or `verify(author, sig, proto.auth_message(group_id,
  session.nonce))` fails → `Close 4001 "bad signature"`.
- U3. When `group.leader is None`:
  - If `author in config.leaders` and `check_auth_envelope(frame.get("genesis"), author, "group")`
    passes:
    - `store.create_group(group_id, author, int(now), LogRow(...))`;
    - set `group.leader`; `reload_roster(group)`; log "group created".
  - Otherwise → `Close 4003 "no group"`. Nothing is written.
- U4. Join at auth (Task 4).
- U5. Look up `group.roster.members.get(author)`:
  - missing → `Close 4003 "not_member"`;
  - `last_seq` is not `None` → `Close 4003 "removed"`.
- U6. Every other member session of the same author → `Close 4000 "replaced"`.
- U7. Then, in order:
  - set `state = "member"` and `author`;
  - (Task 4) on admission, a `have` to every other member session;
  - `proto.have_frame(listed heads)` to this session, where listed heads are
    `{a: group.heads.get(a, 0) for a in roster.members}`, uncapped;
  - (Task 5) statuses.

`check_auth_envelope(raw, author, kind)` returns `(Checked, RosterEnvelope)` or `None`. All of these
must hold:
- `decode_item(raw)` is `Checked`;
- `env["author"] == author` and `env["kind"] == kind`;
- `roster_entry(...)` is not `None`, and its `payload["group_id"]` equals the session's group;
- `verify(author, c.s, c.e)` passes.

**Item decoding:**
- `decode_item`:
  - not a dict, or `e`/`s` not strings → `"undecodable"`;
  - `len(e) > MAX_E_CHARS` or `len(s) > MAX_S_CHARS` → `"oversize"`;
  - `proto.unb64(e, ev.MAX_ENVELOPE_BYTES)` followed by `ev.parse_envelope` fails → `"undecodable"`;
  - `proto.unb64(s, 64)` fails or is not 64 bytes → `BadSig(author, seq)`;
  - otherwise → `Checked`. Extra keys in the item are ignored (Worker parity).
- `roster_entry` is `crew_roster.parse_roster_envelope(checked.e, frozenset(),
  datetime.fromtimestamp(now, timezone.utc))`. Plausibility applies to every author, the leader
  included, as on the Worker.

**Groups and sessions:**
- `group()` loads with `store.load_group`. Roster entries are parsed with `roster_entry` semantics
  and `None` results are dropped. `roster = compute_roster(entries, group_id, leader)`, or `None`
  without a leader.
- `connect` validates `ev.is_uuid7(group_id)` (`ValueError` otherwise). It returns
  `Send(hello)`, where hello is `{"t": "hello", "v": proto.PROTOCOL_VERSION, "source":
  config.source_url, "nonce": proto.b64(token())}`.
- `expire_auth` → `Close 4001 "auth timeout"` when the session is still in the hello state and not
  closing; otherwise `[]`.
- `disconnect` is idempotent. It sets state `closed`, removes the session from its group, and drops
  the group from `groups` when it has no sessions and `leader is None` (ruling 4). Task 5 adds
  status persistence.
- Frames go out through `proto.encode`. `err` frames are `{"t": "err", "code": code}`, plus
  `author` and `seq` when known.

- [ ] **Step 1: Write the failing tests.**

  Each module defines `h = Harness(tmp_path / "relay.db")`, `g = new_group()` and
  `leader = key_for("leader")`.

  `tests/test_relay_session.py`:

  | Test | Ports | Steps → assertions |
  |---|---|---|
  | `test_hello_then_bad_frame_before_auth` | group.test.ts "hello arrives and a frame round-trips through the Durable Object" | `c = h.open(g)`; `hello = c.next_of("hello")`. Then `hello["v"] == 1`, `hello["source"] == "https://example.invalid/edmrn"`, `len(proto.unb64(hello["nonce"], 32)) == 32`. Send `"not json"` → `c.next_of("err") == {"t": "err", "code": "bad_frame"}`, `c.closed is None` |
  | `test_empty_group_refuses_auth_without_genesis` | "an empty group refuses auth without a genesis" | `h.authed(g, leader).closed == (4003, "no group")` |
  | `test_genesis_by_an_author_not_listed_is_refused` | "a genesis by an author not in CREW_LEADERS is refused" | alice's `group` envelope for `g` → `(4003, "no group")` |
  | `test_genesis_for_another_group_is_refused` | "a genesis for another group id is refused" | leader's genesis naming `new_group()` → `(4003, "no group")` |
  | `test_nothing_is_stored_before_a_valid_genesis` | "writes no row, not even the tables, before a valid genesis" (amended: one database, ruling 4) | the three refusals above, plus an auth with `sig="A" * 86` → `h.table_counts(g)` is 0 in all six tables and `g not in h.relay.groups`. Then `h.create_group(g, leader)` → `h.roster_seqs(g, leader.author) == [1]`, the group row's leader is `leader.author`, `h.head(g, leader.author) is None` |
  | `test_unknown_groups_leave_nothing_behind` | Review Focus 4 | 100 × (`c = h.open(new_group())`, then `h.close(c)`) → `h.relay.groups == {}` and `SELECT COUNT(*) FROM groups` is 0. A created group stays in `h.relay.groups` after its last session closes |
  | `test_bad_signature_closes_4001` | "a bad signature closes with 4001" | auth with `sig="A" * 86` → `(4001, "bad signature")`; auth without `sig` → `(4001, "bad signature")` |
  | `test_signature_over_another_nonce_or_group_closes_4001` | "a signature over another nonce or group closes with 4001" | sig over `proto.auth_message(g, proto.b64(bytes(32)))` → `(4001, "bad signature")`; frame `group` ≠ the socket's group → `(4001, "bad auth")`; `author="x"` → `(4001, "bad auth")` |
  | `test_first_frame_other_than_auth_closes_4001` | "a first frame other than auth closes with 4001" | `{"t": "have", "heads": {}}` first → `(4001, "auth first")` |
  | `test_genesis_creates_the_group_without_moving_the_head` | B1 S7 heads | `c = h.create_group(g, leader)` → `c.next_of("have") == {"t": "have", "heads": {leader.author: 0}}`, `h.log_seqs(g, leader.author) == [1]`, `h.head(g, leader.author) is None` |
  | `test_non_member_without_join_is_refused` | first half of "a join admits; a non-member without a join is refused" | after `create_group`: `h.authed(g, key_for("bob")).closed == (4003, "not_member")` |
  | `test_second_connection_replaces_the_first` | "a second connection of the same author replaces the first" | `first = h.create_group(g, leader)`; `second = h.authed(g, leader)` → `second.next_of("have")`, `first.closed == (4000, "replaced")`, `second.closed is None` |
  | `test_auth_timer_closes_an_unauthenticated_socket` | "an unauthenticated socket older than 10 s is closed on the next activity" (R1 S7.1: now a timer) | `c = h.open(g)`; `h.expire(c)` → `c.closed == (4001, "auth timeout")`. On an authed connection `h.expire(conn)` leaves `closed is None` |
  | `test_a_stale_socket_that_sends_auth_is_closed_and_replaces_nothing` | "P4: a stale unauthenticated socket that sends auth is closed and does not replace the live connection" | `live = h.create_group(g, leader)`; `stale = h.open(g)`; `h.clock.advance(11)`; valid auth on `stale` → `stale.closed == (4001, "auth timeout")`, `live.closed is None` |
  | `test_oversize_frames_close_4008` | "bad frames answer bad_frame; oversize frames close with 4008" (the `evs` part is in Task 3) | on the creator's connection: `"not json"` → err `bad_frame`; `"x" * ((1 << 20) + 1)` → `closed == (4008, "too_big")` |
  | `test_frame_size_counts_utf8_bytes` | "the frame size counts UTF-8 bytes, not characters" | `"é" * 600_000` → `(4008, "too_big")` |
  | `test_binary_frames_close_with_reason_binary` | "binary frames close with the reason binary" | `h.send(c, bytes([1, 2, 3]))` → `(4008, "binary")` |
  | `test_unknown_frame_type_is_ignored` | "an unknown frame type is ignored" (the have/synced half is in Task 3) | `{"t": "from-the-future"}` → `c.take() == []` and `c.closed is None`; `{"t": "x" * 33}` → err `bad_frame` (ruling 2) |
  | `test_frames_after_a_close_are_not_processed` | precondition of C1 | after a `(4001, "bad auth")` close: a valid auth on that connection → no frame and `h.table_counts(g)` unchanged |

  `tests/test_relay_vectors.py` starts with
  `from test_crew_vectors import VECTORS, raw, now as vectors_now` and
  `v = json.loads(VECTORS.read_text(encoding="utf-8"))`. Its harness is
  `Harness(tmp_path / "r.db", token=lambda: bytes(range(32)), clock=Clock(vectors_now(v).timestamp()))`,
  so the session nonce is `v["auth"]["nonce"]`.

  | Test | Ports | Assertions |
  |---|---|---|
  | `test_auth_vectors` | vectors.test.ts "auth signature", "auth negatives" | the positive `v["auth"]` on group `v["group_id"]` → `(4003, "no group")`: the signature passed. Each `v["auth_negative"]` case → `(4001, "bad signature")` |
  | `test_structure_cases_through_decode_item` | "structure cases, judged per layer" | for each case, `r = decode_item({"e": c["e"], "s": "A" * 86})`. Non-roster layer: `isinstance(r, Checked) is c.get("python_valid", c["valid"])`. Roster layer: `(isinstance(r, Checked) and roster_entry(r, h.clock.now) is not None) is c.get("python_valid", c["valid"])` |
  | `test_decode_item_classes` | engine contract | `decode_item(None) == "undecodable"`; `{"e": "A", "s": "A"}` → `"undecodable"`; `{"e": "A" * 21847, "s": "A" * 86}` → `"oversize"`; a good `e` with `"s": "A" * 87` → `"oversize"`; a good `e` with `"s": "A"` → `BadSig(author, seq)` of that envelope; a good item plus `"x": 1` → `Checked` |

- [ ] **Step 2: Run the tests to verify they fail.**
  `.venv/Scripts/python.exe -m pytest tests/test_relay_session.py tests/test_relay_vectors.py -q`
  Expected: `ModuleNotFoundError: No module named 'edmrn_relay.engine'`.

- [ ] **Step 3: Implement** `config.py` (`RelayConfig` and the defaults), `engine.py` (D0–D6, U1–U3,
  U5–U7 without the Task 4 and Task 5 parts, decoding, `group`, `reload_roster`, `connect`,
  `expire_auth`, `disconnect`) and `tests/relay_fakes.py`.

- [ ] **Step 4: Run the tests to verify they pass.** Same command. Expected: all pass.

- [ ] **Step 5: Run the full suite.** Expected: no failures.

- [ ] **Step 6: Commit.**
  ```bash
  git add relay/edmrn_relay/config.py relay/edmrn_relay/engine.py tests/relay_fakes.py tests/test_relay_session.py tests/test_relay_vectors.py
  git commit -m "feat(relay): engine sessions, auth and frame checks (B1 S6 S7 S9)"
  ```

---

### Task 3: Engine — catch-up, uploads, refusals and quotas

**Files:**
- Modify: `relay/edmrn_relay/engine.py`
- Modify: `tests/relay_fakes.py` (add the helpers below)
- Test: `tests/test_relay_uploads.py`

**Interfaces:**
- Consumes: Task 2's `Relay`, `Session`, `Group`, `decode_item`, `roster_entry`, `utc_day`,
  `Harness`, `FakeConn`, `key_for`, `strangers`, `items_of`, `decode`. Task 1's `apply_frame`,
  `log_after`, `quota_used`, `group_quota_used`, `take_past_quota`.
- Produces:
  - `Relay.receive` handles `have` and `evs`.
  - `receive` returns `[Close(session, 1011, "internal error")]` on any exception. The exception is
    logged with its traceback, and group memory is unchanged.
  - Harness additions:
    ```python
    def create_group_and_upload(self, group_id, key) -> FakeConn
        # create_group; next_of("have"); send have {}; next_of("synced"); send evs [genesis]
    def invite(self, conn, group_id, key, seq, token=TOKEN, expires_in=3600.0) -> str
        # evs [invite {group_id, invite_id: uuid7(), token_hash: crew_roster.token_hash(token),
        #              expires_at: stamp(expires_in), label: None}]; returns invite_id
    def set_quota(self, group_id, author: str, count: int, day: int | None = None) -> None  # upsert; day defaults to today
    ```

**Catch-up (`have`):**
1. `heads = proto.parse_have(frame)`; `FrameError` → `err bad_frame`.
2. For each author in `sorted(group.heads)`:
   - `until = min(head, last_seq)` when the author is a member with a `last_seq`; otherwise
     `until = head`.
   - `after = heads.get(author, 0)`.
   - While `after < until`: `rows = store.log_after(g, author, after, until, proto.MAX_EVS_ITEMS)`.
     No rows → stop. Otherwise send `proto.evs_frames((r.e, r.s) for r in rows)` and set
     `after = rows[-1].seq`.
3. Send `{"t": "synced"}`.

**Uploads (`evs`)** (E-steps; steps marked Task 4 are added there):
- E1. `items` not a list, or more than `proto.MAX_EVS_ITEMS` → `err bad_frame` and nothing else.
- E2. Aggregate size: `total = Σ len(e) + len(s) + proto._ITEM_OVERHEAD` over the items that are
  dicts with string `e` and `s`. `total > proto.MAX_EVS_BYTES` → `err too_big`, then
  `Close 4008 "too_big"`. Nothing is stored.
- E3. Setup:
  - `now`, `day = utc_day(now)`.
  - Daily cleanup: when `group.cleaned_day != day`, set `cleaned_day = day`. For each
    `(d, n)` in `store.take_past_quota(g, day)`, log
    `json.dumps({"event": "crew-daily-total", "group": g, "day": <d as YYYY-MM-DD>,
    "envelopes": n}, separators=(",", ":"))`.
  - `group_left = config.group_quota - store.group_quota_used(g, day)`.
  - `refused` is a copy of `session.refused` without the entries whose day is neither 0 nor `day`.
  - `members_before = set(group.roster.members)`; the working `roster = group.roster`.
- E4. For each item, in order:
  - a. `decode_item`:
    - `"oversize"` → `err too_big`, `close_after = "too_big"`, stop the loop;
    - `"undecodable"` → `err bad_frame`, `close_after = "bad item"`, stop the loop.
  - b. `BadSig(author, seq)` → `refuse(author, seq, "bad_sig")`; next item.
  - c. Refused already: `refused` has an entry for the author whose day is 0 or `day` and
    `seq >= first` → next item, without an `err`.
  - d. Non-member `join` (Task 4).
  - e. Not a member of the working roster → `refuse not_member`.
  - f. `not roster.admits(author, seq)` → `refuse removed`.
  - g. `head = advanced.get(author, group.heads.get(author, 0))`; `seq <= head` → next item
    (a duplicate; no `err`).
  - h. `not verify(author, c.s, c.e)` → `refuse bad_sig`.
  - i. `entry = roster_entry(c, now) if env["kind"] in ev.ROSTER_KINDS else None`;
    `exempt = entry is not None and author == group.leader`. This is B1 S14 and the Worker's
    rule: every roster envelope of the leader that passes the checks is exempt.
  - j. When not exempt: read `used = store.quota_used(g, day, author)` once per author per frame.
    - `used + counts[author] >= config.member_quota` → `refuse quota` (daily);
    - else `group_left <= 0` → `refuse group_quota` (daily);
    - else `counts[author] += 1` and `group_left -= 1`.
  - k. `advanced[author] = seq`. Add a log row and a forwarded item. Add a roster row only when
    `exempt and entry.kind != "join"`; a member's own `join` is already in the table.

  `refuse(author, seq, code, daily=False)` appends `{"t": "err", "code": code, "author": author,
  "seq": seq}`. It then sets `refused[author] = (seq, day if daily else 0)` unless the author
  already has an entry whose day is 0 or `day` with a first seq `<= seq`.
- E5. `roster_changed = store.apply_frame(g, day, log_rows, roster_rows, advanced, counts)`.
- E6. Only after the commit:
  - `group.heads.update(advanced)`;
  - `session.refused = refused`;
  - if `roster_changed`: `reload_roster(group)`.
- E7. `len(refused) > MAX_REFUSALS` → `close_after = close_after or "too many refusals"`.
- E8. The forwarded items, in order, go to every other member session (state member, not closing)
  as `proto.evs_frames(...)`.
- E9. Roster consequences (Task 4).
- E10. `close_after` → `Close 4008 close_after`.

Actions come out in this order: errs, forwards, E9, the close.

- [ ] **Step 1: Write the failing tests** in `tests/test_relay_uploads.py`.

  Setup: `c = h.create_group_and_upload(g, leader)` unless stated otherwise. `L = leader.author`.
  `forged3` is `h.visit(leader, 3)` with `s` replaced by the `s` of `h.visit(leader, 2)`. `big` is
  `{"e": "A" * 21846, "s": "A" * 86}` and `oversize` is `{"e": "A" * 21847, "s": "A" * 86}`.

  | Test | Ports | Steps → assertions |
  |---|---|---|
  | `test_have_right_behind_auth_is_answered_in_order` | group.test.ts "a have sent right behind auth is not overtaken by the signature check" and "the serial chain keeps a have behind a slow auth" | an auth with genesis, then `{"t": "have", "heads": {}}` → after the hello, `[f["t"] for f in c.take()] == ["have", "synced"]`; the have's heads are `{L: 0}` |
  | `test_unknown_type_then_have_gets_synced` | "an unknown frame type is ignored" | `{"t": "from-the-future"}`, then have `{}` → `c.next_of("synced") == {"t": "synced"}`; no `err` |
  | `test_gap_accepted_duplicate_ignored` | "a gap in seq is accepted and duplicates are ignored" | `h.visit(leader, 5)` sent in two frames → `h.head(g, L) == 5`, `h.log_seqs(g, L) == [1, 5]`, no `err` |
  | `test_catch_up_sends_exactly_what_is_lacking` | "catch-up sends exactly what the client lacks, in ascending seq" | visits 2..150 in frames of 100 and 49; `o = h.authed(g, leader)`; `o.next_of("have")`; have `{L: 120}` → `[decode(i)["seq"] for i in items_of(o.take())] == list(range(121, 151))` |
  | `test_a_restarted_relay_serves_from_storage` | "a socket survives the eviction of its object and is served from storage" (adapted: a process restart) | visit 2; `h2 = Harness(h.db_path, clock=h.clock)`; `c2 = h2.authed(g, leader)` → `c2.next_of("have")["heads"] == {L: 2}`. Have `{L: 1}` → served seqs `[2]`, then synced. Visit 3 on `c2` → `h2.head(g, L) == 3` |
  | `test_bad_evs_and_have_frames_answer_bad_frame` | "bad frames answer bad_frame…" (the `evs` part) | each of these → err `bad_frame`, connection open: `{"t": "evs", "items": [{"e": "A", "s": "A"}] * 101}`; `{"t": "evs", "items": "x"}`; `{"t": "have", "heads": []}`; `{"t": "have", "heads": {"x": 1}}` (ruling 2) |
  | `test_member_quota_refuses_the_rest_of_the_author` | "the member quota refuses the rest of that author in the frame" | `h.set_quota(g, L, 4999)`; visits 2, 3, 4 in one frame → `c.take("err") == [{"t": "err", "code": "quota", "author": L, "seq": 3}]`, `h.head(g, L) == 2` |
  | `test_quotas_come_from_the_configuration` | R1 S14 configurable | with `member_quota=2`: visits 2, 3, 4 → err quota at seq 4, head 3 (the genesis is exempt). With `group_quota=1` in a new harness: visits 2, 3 → err `group_quota` at seq 3, head 2 |
  | `test_group_quota_refuses_and_roster_envelopes_are_exempt` | "the group quota refuses, the head stays before the hole; roster envelopes are exempt" | `h.set_quota(g, "X" * 43, 80000)`; visit 2 → `[{"t": "err", "code": "group_quota", "author": L, "seq": 2}]`, head 1. Then `s2 = h.authed(g, leader)`; `h.invite(s2, g, leader, 2)` → no `err`, `h.roster_seqs(g, L) == [1, 2]`, head 2 |
  | `test_past_quota_days_are_logged_once_then_deleted` | "quota rows of past days are logged once as daily totals, then deleted" | `D = utc_day(h.clock.now)`; `h.set_quota(g, L, 7, D - 1)`; `h.set_quota(g, "Y" * 43, 5, D - 1)`; `h.clock.next_utc_day()`; visit 2, then visit 3 → the caplog messages containing `crew-daily-total` are exactly `[json.dumps({"event": "crew-daily-total", "group": g, "day": <D-1 as YYYY-MM-DD>, "envelopes": 12}, separators=(",", ":"))]`; `SELECT COUNT(*) FROM quota WHERE group_id = ? AND day < ?` (`g`, `D + 1`) is 0 |
  | `test_non_member_envelope_is_refused_naming_author_and_seq` | "a non-member envelope is refused with not_member naming author and seq" | bob's visit seq 4 → `[{"t": "err", "code": "not_member", "author": bob.author, "seq": 4}]` |
  | `test_bad_signature_stops_the_author_in_the_frame` | "a bad signature is refused and stops that author in the frame" | `[forged3, visit 4]` → `[{"t": "err", "code": "bad_sig", "author": L, "seq": 3}]`, head 1 |
  | `test_oversize_item_keeps_what_precedes_it` | "an oversize item keeps what precedes it, answers too_big and ends the session with 4008" | `[visit 2, oversize, visit 3]` → errs `[{"t": "err", "code": "too_big"}]`, `closed == (4008, "too_big")`, head 2 |
  | `test_undecodable_item_ends_the_session_after_the_items_before_it` | "an undecodable item ends the session with 4008 after the items before it are kept" | `[visit 2, {"e": "A", "s": "A"}]` → err `bad_frame`, `(4008, "bad item")`, head 2 |
  | `test_broken_signature_field_answers_bad_sig_and_refuses_onward` | "an item with a good envelope and a broken signature field answers bad_sig, naming author and seq and refuses the author onward" | `[visit 2, {**visit3, "s": "A"}]` → `[bad_sig L 3]`; visit 4 in a later frame → no frame; head 2 |
  | `test_evs_items_over_512_kib_close_4008` | "evs items totalling more than 512 KiB answer too_big and end the session with 4008" | `[big] * 25` → err `too_big`, `(4008, "too_big")`, head 1 |
  | `test_quota_refusal_also_refuses_a_later_roster_envelope` | "P1: a quota refusal in one frame also refuses the leader's roster envelope in the next" | quota 4999; `[v2, v3, v4]`; then `[invite seq 5, v6]` → `h.log_seqs(g, L) == [1, 2]`, head 2 |
  | `test_bad_signature_refuses_later_frames` | "P5: a bad signature refuses the author's later frames as well" | `[v2, forged3]`; then `[invite5, v6]` → log `[1, 2]` |
  | `test_daily_refusal_ends_with_the_utc_day` | "a daily refusal ends with the UTC day" (adapted: the clock moves to the next UTC day) | quota 4999; `[v2, v3]` → err quota; `h.clock.next_utc_day()`; `[v3]` → log `[1, 2, 3]` |
  | `test_a_frame_behind_a_4008_close_is_not_processed` | "C1: a frame queued behind a 4008 close is not processed" | `[v2, oversize]`, then `[v4]` → 4008, log `[1, 2]`, head 2 |
  | `test_same_after_the_aggregate_size_close` | "C1: the same for the aggregate-size close" | `[big] * 25`, then `[v2]` → log `[1]` |
  | `test_beyond_the_refusal_cap_the_session_ends` | "C2: beyond the refusal cap the map is still saved and a queued frame is judged with it" | `h.set_quota(g, L, 5000)`; `[v2] + [visit of each strangers(65) key, seq 1]` → `closed == (4008, "too many refusals")`; then `[invite seq 3]` → log `[1]` |
  | `test_more_than_eight_refused_authors_do_not_end_the_session` | same name | the visits of 12 strangers → 12 `not_member` errs, still open; `[v2]` → log `[1, 2]` |
  | `test_expired_daily_refusals_are_pruned` | "expired daily refusals are pruned and do not count toward the cap" | `h.session(c).refused = {f"{i:02d}" + "Z" * 41: (5, utc_day(h.clock.now) - 1) for i in range(70)}`; a stranger's visit → err `not_member`, still open, `list(h.session(c).refused) == [stranger.author]` |
  | `test_writes_of_a_frame_are_one_transaction` | "the writes of a frame are one transaction: a failure midway leaves nothing" | trigger `boom` before insert on `quota` (as in Task 1); `[v2, v3]` → `closed == (1011, "internal error")`, log `[1]`, head 1, `h.relay.groups[g].heads == {L: 1}`; caplog has an ERROR record with `exc_info` |

- [ ] **Step 2: Run the tests to verify they fail.**
  `.venv/Scripts/python.exe -m pytest tests/test_relay_uploads.py -q`
  Expected: failures. `have` and `evs` are ignored, and the harness helpers are missing.

- [ ] **Step 3: Implement** catch-up, E1–E8, E10 and the `receive` exception guard in `engine.py`,
  plus the harness additions.

- [ ] **Step 4: Run the tests to verify they pass.** Run Task 2's tests too. Expected: all pass.

- [ ] **Step 5: Run the full suite.** Expected: no failures.

- [ ] **Step 6: Commit.**
  ```bash
  git add relay/edmrn_relay/engine.py tests/relay_fakes.py tests/test_relay_uploads.py
  git commit -m "feat(relay): catch-up, uploads, refusals and daily quotas (B1 S7 S14 S18)"
  ```

---

### Task 4: Engine — joins, re-seed and removal

**Files:**
- Modify: `relay/edmrn_relay/engine.py`
- Modify: `tests/relay_fakes.py`
- Test: `tests/test_relay_roster.py`
- Modify: `tests/test_relay_vectors.py`

**Interfaces:**
- Consumes: Tasks 2–3 (E-steps, U-steps, `reload_roster`, `Relay.group`, the harness).
  `crew_roster.compute_roster` and `Roster.invites[invite_id].expires_at`.
- Produces: steps U4, E4d and E9. Harness additions:
  ```python
  def join_envelope(self, key, group_id, invite_id, seq, token=TOKEN, created_at=None) -> dict
      # "join" {group_id, invite_id, token, commander: "Alice"}
  def group_with_invite(self, group_id) -> tuple   # (leader, alice, leader_conn, invite_id)
      # create_group_and_upload, invite seq 2 (TOKEN, 1 h), leader_conn drained
  def two_members(self) -> tuple                   # (group_id, leader, alice, lc, ac)
      # group_with_invite on new_group(); alice authed with join seq 1; both connections drained
  ```

**New steps:**
- **U4.** Runs when `frame.get("join") is not None`, `author != group.leader` and `author` is not
  in `group.roster.members`.
  - `checked = check_auth_envelope(join, author, "join")`.
  - Refuse quietly if any of these holds:
    - the check failed;
    - `group.roster.invites.get(entry.payload["invite_id"])` is missing;
    - `ev.parse_stamp(invite.expires_at)` is `None`;
    - the relay clock is past it (B1 S7: the relay may be stricter).
  - Then `compute_roster(group.roster_entries + [entry], g, leader)` must admit the author.
  - On admission: `store.add_roster_envelope(g, LogRow(...))` (no head), `reload_roster(group)`,
    log "member admitted", `joined = True`.
  - A refused join is not an error by itself: U5 then closes with `4003 "not_member"`.
  - When `joined`, U7 sends a `have` with the listed heads to every other member session.
- **E4d.** Runs when the author is not a member and `env["kind"] == "join"`.
  - `not verify(...)` → `refuse bad_sig`.
  - Otherwise compute `entry = roster_entry(c, now)` and
    `candidate = compute_roster(group.roster_entries + pending + [entry], g, leader)`.
  - When the author is in `candidate.members`:
    - the working roster becomes `candidate`, and `pending` gets `entry`;
    - add a log row and a roster row, both headless: the head is not advanced, the item is not
      counted and not forwarded;
    - next item.
  - Otherwise fall through to E4e (`not_member`).
  - B1 S7: the invite's expiry on the relay clock is an auth-time rule only.
- **E9.** Runs when `roster_changed` (after E6's reload):
  - Every member session, this one included, whose author is no longer in `group.roster.members`,
    or has a `last_seq` → `Close 4003 "removed"`.
  - If some member is not in `members_before` → the listed heads go to every member session not
    closing, this one included.

- [ ] **Step 1: Write the failing tests** in `tests/test_relay_roster.py`.

  `L`/`A` are the leader's and alice's authors. "alice joined" means
  `leader, alice, lc, inv = h.group_with_invite(g)` followed by
  `ac = h.authed(g, alice, join=h.join_envelope(alice, g, inv, 1))`. `remove(seq, last)` is the
  leader's `remove` envelope `{"group_id": g, "member": A, "last_seq": last}`.

  | Test | Ports | Steps → assertions |
  |---|---|---|
  | `test_a_join_admits` | second half of "a join admits; a non-member without a join is refused" | alice joined → `ac.next_of("have")["heads"][A] == 0`, `h.log_seqs(g, A) == [1]`, `h.head(g, A) is None`; caplog has an "admitted" INFO line |
  | `test_admission_sends_a_fresh_have_to_connected_members` | B1 S7 "After every admission it sends each connected member a fresh have" | alice joined → `lc.next_of("have")["heads"] == {L: 2, A: 0}` |
  | `test_invite_expired_on_the_relay_clock_does_not_admit` | "an invite past its expiry on the relay clock does not admit" | invite with `expires_in=-1`; join with `created_at=h.stamp(-120)` → `(4003, "not_member")`, `h.log_seqs(g, A) == []` |
  | `test_join_of_a_member_at_auth_is_ignored` | B1 S7.3 | alice joined; `h.close(ac)`; re-auth with `join_envelope(..., seq=9)` → a have arrives, `h.log_seqs(g, A) == [1]` |
  | `test_join_at_auth_moves_no_head_and_nothing_above_the_head_is_served` | "a join at auth does not move the head; earlier envelopes still upload; nothing above the head is served" | join seq 3 at auth → have `A: 0`. `w = h.authed(g, leader)`; have `{}` → no item by A. Alice sends `[visit(alice, 1), visit(alice, 2), join]` → `[decode(i)["seq"] for i in items_of(w.take())] == [1, 2, 3]` |
  | `test_forwards_to_other_members_not_back` | "forwards stored envelopes to the other members but not back to the sender" | `g, leader, alice, lc, ac = h.two_members()`; alice's visit 2 → the seqs in `lc`'s items are `[2]`, `items_of(ac.take()) == []` |
  | `test_reseed_admits_members_from_their_stored_joins` | "a re-seed by the leader admits the members again from their stored joins, without moving their heads" | `lc = h.create_group(g, leader)`; `lc.next_of("have")`; `[genesis, invite]` (invite_id `"01920000-0000-7000-8000-00000000beef"`, expires +3600); `[alice's join seq 1]` → `lc.next_of("have")["heads"][A] == 0`, `h.head(g, A) is None`; `h.authed(g, alice).next_of("have")["heads"][A] == 0` |
  | `test_reseed_admits_a_join_whose_invite_expired_on_the_relay_clock` | "a re-seed admits a join whose invite has expired on the relay clock (S7: expiry is an auth-time rule)" | the same with `expires_at=h.stamp(-1)` and the join's `created_at=h.stamp(-120)` → the same assertions |
  | `test_removal_closes_the_member_and_caps_what_is_served` | "removal closes the member and caps what is accepted and served" | alice joined; alice sends `[join, visit 2, visit 3]`; the leader sends `remove(3, 2)` → `ac.closed == (4003, "removed")`. `w = h.authed(g, leader)`; have `{}` → alice served `[1, 2]`; `h.authed(g, alice).closed == (4003, "removed")` |
  | `test_removed_member_cannot_upload_above_the_cap` | "a removed member cannot upload above the cap and is told so" | `remove(3, 1)` → ac closed; `w` sends `[visit(alice, 2)]` → `[{"t": "err", "code": "removed", "author": A, "seq": 2}]` |
  | `test_have_lists_a_removed_members_head_uncapped` | "the relay's have lists a removed member's stored head uncapped; catch-up serves only up to last_seq" | as in the removal test → `w.next_of("have")["heads"][A] == 3`, served `[1, 2]` |
  | `test_members_invite_kind_envelopes_count_and_write_no_roster_row` | "P6: a member's self-signed invite-kind envelopes count toward the quota and write no roster row" | `two_members`; `h.set_quota(g, A, 5000)`; `before = h.store.db.total_changes`; alice sends 99 valid `invite` envelopes, seq 2..100 → first err `{"t": "err", "code": "quota", "author": A, "seq": 2}`, `h.store.db.total_changes == before`, `h.roster_seqs(g, A) == [1]` |
  | `test_a_members_own_join_is_not_stored_in_the_roster_again` | "a member's own join is not stored in the roster table again" | alice sends a join with seq 9 → `h.log_seqs(g, A) == [1, 9]`, `h.roster_seqs(g, A) == [1]` |

  Append to `tests/test_relay_vectors.py`:

  | Test | Ports | Assertions |
  |---|---|---|
  | `test_roster_cases_through_the_relays_store` | vectors.test.ts "roster cases" | for each case `i`: `store = RelayStore(tmp_path / f"c{i}.db")`; `INSERT INTO groups VALUES (G, case["leader"], 0)`; `add_roster_envelope(G, LogRow(env["author"], env["seq"], e, bytes(64)))` for each named envelope (`e = raw(...)`, `env = ev.parse_envelope(e)`). `grp = Relay(store, RelayConfig(), now=lambda: vectors_now(v).timestamp()).group(G)` → `{a: m.last_seq for a, m in grp.roster.members.items()} == case["members"]`, `grp.roster.name == case["name_of_group"]`, and the commander/leader details equal `case["member_details"]` |

- [ ] **Step 2: Run the tests to verify they fail.**
  `.venv/Scripts/python.exe -m pytest tests/test_relay_roster.py tests/test_relay_vectors.py -q`
  Expected: the join, re-seed and removal tests fail; the roster-cases test already passes.

- [ ] **Step 3: Implement** U4, E4d, E9 and the harness additions.

- [ ] **Step 4: Run the tests to verify they pass.** Run Tasks 2–3's tests too. Expected: all pass.

- [ ] **Step 5: Run the full suite.** Expected: no failures.

- [ ] **Step 6: Commit.**
  ```bash
  git add relay/edmrn_relay/engine.py tests/relay_fakes.py tests/test_relay_roster.py tests/test_relay_vectors.py
  git commit -m "feat(relay): joins at auth, re-seed admissions and removal (B1 S2 S7)"
  ```

---

### Task 5: Engine — statuses and shutdown

**Files:**
- Modify: `relay/edmrn_relay/engine.py`
- Modify: `tests/relay_fakes.py`
- Test: `tests/test_relay_status.py`
- Modify: `tests/test_relay_vectors.py`

**Interfaces:**
- Consumes: Tasks 2–4. Task 1's `statuses` and `save_status`.
- Produces:
  - `Relay.shutdown() -> list[Action]`: persists every status, then emits
    `Close(session, 1001, "relay shutting down")` for every session.
  - `Relay.receive` handles `st`; `disconnect` persists the session's status.
  - Harness additions: `def status(self, key, hull, author=None, **fields) -> dict`, which calls
    `status_frame` with `at=self.stamp()`, and `def shutdown(self) -> None`, which applies
    `relay.shutdown()`.

**Status steps** (B1 S8 as amended; ruling 1 for T1; the Worker's order of checks):
- T1. `now - group.status_at.get(author, -inf) < STATUS_INTERVAL` → `err rate`.
- T2. `e` or `s` not a string → `err bad_frame`.
- T3. `len(e) > MAX_STATUS_CHARS` → `err too_big`.
- T4. `data = proto.unb64(e, proto.MAX_STATUS_BYTES)` and `sig = proto.unb64(s, 64)`. A failure,
  or a signature that is not 64 bytes → `err bad_frame`.
- T5. `doc = ev.load_json(data, proto.MAX_STATUS_BYTES, "status")`. `EnvelopeError`, or a `doc`
  that is not a dict with `doc["author"] == session.author`, a string `at` and a dict `status`
  → `err bad_frame`.
- T6. `not verify(session.author, sig, data)` → `err bad_sig`.
- T7. `session.status = (data, sig)`; `group.status_at[author] = now`;
  `proto.status_frame(data, sig)` goes to every other member session.

**Statuses at auth** (U7's last part, B1 S7.5):
- First, for each other member session that is not closing, has a different author, and whose
  author is active (a member without `last_seq`): mark the author live, and send its status if it
  has one.
- Then, for each `(author, e, s)` in `store.statuses(g)` whose author is not this author, not live,
  and active: send `proto.status_frame(e, s)`.

**Persistence:**
- `disconnect` (and `shutdown`): for a member session with a status →
  `store.save_status(g, author, *status)`, then `session.status = None`.

- [ ] **Step 1: Write the failing tests** in `tests/test_relay_status.py`.

  Setup: `g, leader, alice, lc, ac = h.two_members()`; `L`/`A` are their authors. `doc(frame)` is
  `json.loads(proto.unb64(frame["e"], 8192))`.

  | Test | Ports | Steps → assertions |
  |---|---|---|
  | `test_status_is_forwarded_rate_limited_and_checked` | status.test.ts "is forwarded, rate-limited, and checked for author and signature" | `ac` sends `h.status(alice, 0.5)` → `doc(lc.next_of("st"))["status"]["hull"] == 0.5`. `ac` sends `h.status(alice, 0.4)` → `ac.next_of("err")["code"] == "rate"`. `lc` sends `h.status(leader, 1, author=A)` → `lc.next_of("err")["code"] == "bad_frame"` |
  | `test_oversize_status_and_bad_signature` | "an oversize status answers too_big and a bad signature answers bad_sig (C10, S8)" | `ac` sends `{"t": "st", "e": proto.b64(bytes(8193)), "s": "A" * 86}` → `too_big`. `lc` sends `h.status(leader, 0.8)` with `s` replaced by the `s` of `h.status(alice, 0.8, author=L)` → `bad_sig` |
  | `test_last_status_of_a_member_who_left_reaches_a_new_connection` | "the last status of a member who left reaches a new connection" | alice's status 0.3; `lc.next_of("st")`; `h.close(ac)` → `[a for a, _, _ in h.store.statuses(g)] == [A]`; `again = h.authed(g, leader)` → `doc(again.next_of("st"))["author"] == A` |
  | `test_stored_status_of_a_removed_member_is_not_sent` | "the stored status of a removed member is not sent to a new connection (S12)" | alice's status 0.3; `lc.next_of("st")`; `h.close(ac)`; `lc` sends the leader's `remove` seq 3 `{"group_id": g, "member": A, "last_seq": 1}`; `again = h.authed(g, leader)` → `again.take("st") == []` |
  | `test_live_status_reaches_a_new_connection` | B1 S7.5 (live statuses) | alice's status; `again = h.authed(g, leader)` → `again.next_of("st")` is alice's; `h.store.statuses(g) == []` |
  | `test_status_rate_is_per_member_across_reconnects` | ruling 1 (B1 S8 over the Worker) | alice's status; `ac2 = h.authed(g, alice)` (ac closed with 4000); a status on `ac2` → err `rate`; `h.clock.advance(30)`; a status on `ac2` → arrives at `lc` |
  | `test_a_replaced_connections_status_is_persisted` | B1 S8 (written on close) | alice's status; `h.authed(g, alice)` → `[a for a, _, _ in h.store.statuses(g)] == [A]` |
  | `test_status_content_is_never_validated` | ruling 3, Review Focus 1 | `monkeypatch.setattr(edmrn.ext.crew_events, "validate_status", boom)` and `monkeypatch.setattr(edmrn.ext.crew_protocol, "parse_status", boom)`, where `boom` raises `AssertionError`; `ac` sends `h.status(alice, 5.0, edsy_url="https://edsy.org/#/I=AAAA")` → `doc(lc.next_of("st"))["status"]["hull"] == 5.0` |
  | `test_shutdown_persists_live_statuses_and_closes_1001` | R3 | alice's status; `h.shutdown()` → `ac.closed == lc.closed == (1001, "relay shutting down")` and `[a for a, _, _ in h.store.statuses(g)] == [A]` |
  | `test_logs_never_carry_envelope_contents` | R3 | `caplog.set_level(logging.DEBUG, "edmrn_relay")`. Exchange: alice's visit with `system` `"Secret Sol"`; alice's status with `system="Secret Sol"`; a removal; `h.shutdown()`. No `record.getMessage()` contains `"Secret Sol"`, any sent item's `e`, any hello nonce, or `TOKEN` |

  Append to `tests/test_relay_vectors.py`:

  | Test | Ports | Assertions |
  |---|---|---|
  | `test_status_vectors` | vectors.test.ts "status signature and negatives" | `two_members()`; the vectors' alice is `key_for("alice")`. `ac` sends `v["status_negative"][0]`'s `e`/`s` as `st` → err `bad_sig`, sent first so the rate window stays open. Then `v["status"]` → `lc.next_of("st") == {"t": "st", "e": v["status"]["e"], "s": v["status"]["s"]}` |

- [ ] **Step 2: Run the tests to verify they fail.**
  `.venv/Scripts/python.exe -m pytest tests/test_relay_status.py tests/test_relay_vectors.py -q`
  Expected: failures (`st` is ignored, `shutdown` is missing).

- [ ] **Step 3: Implement** T1–T7, statuses at auth, persistence, `shutdown`, and the harness
  additions.

- [ ] **Step 4: Run the tests to verify they pass.** Run Tasks 2–4's tests too. Expected: all pass.

- [ ] **Step 5: Run the full suite.** Expected: no failures.

- [ ] **Step 6: Commit.**
  ```bash
  git add relay/edmrn_relay/engine.py tests/relay_fakes.py tests/test_relay_status.py tests/test_relay_vectors.py
  git commit -m "feat(relay): statuses in memory, persisted on close and shutdown (B1 S8 as amended)"
  ```

---

### Task 6: Server, routes, invite page and command line

**Files:**
- Create: `relay/edmrn_relay/join_page.py`, `relay/edmrn_relay/server.py`,
  `relay/edmrn_relay/__main__.py`
- Modify: `relay/edmrn_relay/config.py` (add `load_config`)
- Modify: `tests/conftest.py` (the `loopback` guard), `pytest.ini` (register the marker)
- Modify: `docs/architecture/patterns.md`: the autouse-fixture sentence in "All external HTTP goes
  through `edmrn/ext/api.py`" gains "; tests marked `loopback` may connect to 127.0.0.1 only".
- Modify: `docs/how-to/run.md`: "Run the tests" says tests marked `loopback` reach 127.0.0.1
  only. Keep the `relay_e2e` clause; Task 7 removes it.
- Test: `tests/test_relay_http.py` (no sockets), `tests/test_relay_server.py`
  (`pytestmark = pytest.mark.loopback`), `tests/test_isolation.py`

**Interfaces:**
- Consumes: Tasks 1–5 (`Relay`, `Send`, `Close`, `RelayStore`, `RelayConfig`,
  `engine.AUTH_TIMEOUT`). From the harness: `key_for`, `new_group`, `envelope_item`,
  `auth_frame`, `status_frame`, `stamp_at`.
- Produces:
  ```python
  # join_page.py
  JOIN_SCRIPT: str; JOIN_STYLE: str; JOIN_HTML: str; JOIN_CSP: str
  JOIN_HEADERS: tuple[tuple[str, str], ...]   # Content-Type text/html; charset=utf-8, Content-Security-Policy,
                                              # Referrer-Policy no-referrer, Cache-Control no-store
  def greeting(source_url: str) -> str        # "EDMRN crew relay. Licence: AGPL-3.0. Source: <url or 'not yet published'>\n"
  # config.py
  class ConfigError(ValueError): ...
  def load_config(argv: Sequence[str] | None, environ: Mapping[str, str]) -> RelayConfig
  # server.py
  MAX_WIRE_BYTES = 2 * proto.MAX_FRAME_BYTES
  @dataclass(frozen=True) class HttpReply: status: int; headers: tuple[tuple[str, str], ...]; body: bytes
  def group_of(path: str) -> str | None
  def route(method: str, path: str, upgrade: bool, source_url: str) -> HttpReply | None   # None: go on with the WebSocket handshake
  class Peer:
      def __init__(self, ws: Any) -> None       # needs a running loop; starts its writer task
      def enqueue(self, item: str | tuple[int, str]) -> None
      async def finish(self, closing: bool) -> None   # closing: await the writer; otherwise cancel it
  class RelayServer:
      def __init__(self, config: RelayConfig, *, now: Callable[[], float] = time.time,
                   token: Callable[[], bytes] | None = None) -> None   # None: Relay's default token
      store: RelayStore; relay: Relay; port: int        # set by start()
      def dispatch(self, actions: list[Action]) -> None # never awaits
      async def start(self) -> int
      async def stop(self) -> None
  # __main__.py
  async def run(config: RelayConfig, stop: asyncio.Event,
                started: Callable[[int], None] | None = None) -> None   # started(port) once listening
  def main(argv: list[str] | None = None) -> int
  ```

**Join page and greeting** (exact copy is the spec's ruling, R1 S17):
- `JOIN_SCRIPT`, `JOIN_STYLE` and the HTML template are copied verbatim from
  `crew-relay/src/join.ts`.
- `JOIN_CSP` is `default-src 'none'; script-src '<h(JOIN_SCRIPT)>'; style-src '<h(JOIN_STYLE)>';
  base-uri 'none'; form-action 'none'; frame-ancestors 'none'`, where
  `h(t) = "sha256-" + base64(sha256(t.encode()))`.

**Routes:**
- The path is cut at the first `?`.
- `GET /` → 200, `(("Content-Type", "text/plain; charset=utf-8"),)`, `greeting(source_url)`.
- `GET /join` → 200, `JOIN_HEADERS`, `JOIN_HTML`.
- `GET` with `group_of(path)` not `None` → `None` when `upgrade`, else
  426 `b"expected a WebSocket upgrade\n"` as text/plain.
- Anything else, every non-GET method included → 404 `b"not found\n"` as text/plain.
- `group_of` matches `/v1/groups/<id>/ws` exactly, with `ev.is_uuid7(id)` (R2: no copied regex).

**Server pieces:**
- **`process_request`.** It calls `route(getattr(request, "method", "GET"), request.path,
  request.headers.get("Upgrade", "").lower() == "websocket", config.source_url)`. Older
  `websockets` releases parse only GET and have no `Request.method`. A reply becomes
  `websockets.http11.Response(status, HTTPStatus(status).phrase, Headers([*headers,
  ("Content-Length", str(len(body))), ("Connection", "close")]), body)`.
- **Handler** (ruling 6):
  1. `peer = Peer(ws)`; `session, actions = relay.connect(group_of(ws.request.path), peer)`;
     `dispatch(actions)`.
  2. Arm `loop.call_later(engine.AUTH_TIMEOUT, lambda: dispatch(relay.expire_auth(session)))`.
  3. `async for message in ws`: `dispatch(relay.receive(session, message))`; break when
     `session.closing`. `ConnectionClosed` ends the loop.
  4. `finally`: cancel the timer, `relay.disconnect(session)`,
     `await peer.finish(session.closing)`.
- **`dispatch`.** A `Send` enqueues its text on `session.conn`; a `Close` enqueues
  `(code, reason)`.
- **`Peer` writer.** A `str` → `await ws.send(item)`; a tuple → `await ws.close(*item)`, then stop.
  `ConnectionClosed` stops the writer.
- **`start()`:**
  - opens `RelayStore(config.db)` on the loop thread and builds `Relay`;
  - calls `websockets.asyncio.server.serve(handler, config.host, config.port,
    process_request=..., max_size=MAX_WIRE_BYTES)`;
  - sets `port` from `server.sockets[0].getsockname()[1]` and logs one "listening" line (host,
    port, database path, number of leaders).
- **`stop()`.** `dispatch(relay.shutdown())`, then `server.close()`, `await server.wait_closed()`,
  `store.close()`.

**Configuration and entry point:**
- **`load_config`.** argparse reads every option as a string, with defaults taken from
  `environ["EDMRN_RELAY_<NAME>"]`. The flags and variables are:

  | Flag | Environment variable |
  |---|---|
  | `--host` | `EDMRN_RELAY_HOST` |
  | `--port` | `EDMRN_RELAY_PORT` |
  | `--db` | `EDMRN_RELAY_DB` |
  | `--leaders` | `EDMRN_RELAY_LEADERS` |
  | `--source-url` | `EDMRN_RELAY_SOURCE_URL` |
  | `--member-quota` | `EDMRN_RELAY_MEMBER_QUOTA` |
  | `--group-quota` | `EDMRN_RELAY_GROUP_QUOTA` |

  The function then converts and validates (ruling 9), raising `ConfigError`: the port must be
  0–65535 (0 means ephemeral) and the quotas integers ≥ 1.
- **`main`:**
  - `load_config(argv, os.environ)`; on `ConfigError`, print the message to stderr and return 2;
  - `logging.basicConfig(stream=sys.stdout, level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s")`, and set the `websockets` logger to
    WARNING;
  - `asyncio.run` with `loop.add_signal_handler` for SIGTERM and SIGINT setting `stop`. On
    `NotImplementedError` (Windows), use `signal.signal(SIGINT, ...)` with
    `loop.call_soon_threadsafe(stop.set)`;
  - return 0.
- **Module end:** `__main__.py` ends with `if __name__ == "__main__": sys.exit(main())`.

**conftest guard:**
- Without the marker: unchanged (refuse everything).
- With `loopback`: `socket.socket.connect` and `connect_ex` are allowed only when `address[0] ==
  "127.0.0.1"`, `socket.getaddrinfo` only when `host == "127.0.0.1"`, and
  `socket.create_connection` only when `address[0] == "127.0.0.1"`. Allowed calls go to the
  original functions; anything else raises the existing `RuntimeError`.
- The `relay_e2e` early return stays until Task 7.
- `pytest.ini` markers gain:
  `loopback: may connect to 127.0.0.1 only (tests/conftest.py); asyncio needs it on Windows`.

- [ ] **Step 1: Write the failing tests.**

  `tests/test_relay_http.py`. `V7 = "01920000-0000-7000-8000-0000000000a2"`;
  `SRC = "https://example.invalid/edmrn"`.

  | Test | Ports | Assertions |
  |---|---|---|
  | `test_greeting_names_the_source_url` | routes.test.ts "greets with the source URL" | `route("GET", "/", False, SRC) == HttpReply(200, (("Content-Type", "text/plain; charset=utf-8"),), b"EDMRN crew relay. Licence: AGPL-3.0. Source: https://example.invalid/edmrn\n")`; `greeting("")` ends with `"Source: not yet published\n"` |
  | `test_join_page_is_the_workers_byte_for_byte` | "serves the invite page with a hash-only CSP" (R1 S17) | `hashlib.sha256(JOIN_HTML.encode()).hexdigest() == "44fd9af6fd516f2ba9cb41496354dd066d7253ac1fc5e4ef205fb941e6d6732c"` and `len(JOIN_HTML) == 1146` (computed from the Worker's `join.ts` with node, 2026-11-08). `JOIN_CSP == "default-src 'none'; script-src 'sha256-Ys1QBGBHzkw97W1xVOxlbYsbsaM7a1646hnDPwqtrs0='; style-src 'sha256-ccuruHwjqmAReQp4XtXI4ITovYe/9LRSYO7FmD+hsvU='; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"`. `route("GET", "/join", False, SRC) == HttpReply(200, JOIN_HEADERS, JOIN_HTML.encode())`. `f"<script>{JOIN_SCRIPT}</script>" in JOIN_HTML`. `"textContent"`, `"{1,1987}"` and `"Copy failed"` are in `JOIN_SCRIPT`; `"innerHTML"` is not |
  | `test_unknown_paths_bad_ids_methods_and_plain_gets` | "refuses unknown paths, bad group ids and plain GETs on the socket path"; "forwards a valid socket upgrade … and refuses malformed ids" | 404 `b"not found\n"` for: `/nope`; `/v1/groups/not-a-uuid/ws` with upgrade; `V7.upper()` with upgrade; `01920000-0000-4000-8000-0000000000a2` with upgrade; `POST /`; `POST` on the socket path with upgrade. `GET /v1/groups/{V7}/ws` without upgrade → 426 `b"expected a WebSocket upgrade\n"`; with upgrade → `None` |
  | `test_routes_ignore_the_query_string` | Review Focus 5 | `/?x=1` → the greeting; `/join?a` → the page; `/v1/groups/{V7}/ws?x=1` with upgrade → `None`; `group_of(f"/v1/groups/{V7}/ws?x=1") == V7`; `group_of(f"/v1/groups/{V7}/ws/") is None` |
  | `test_config_defaults_environment_and_flags` | R3 | `load_config([], {}) == RelayConfig()` and `RelayConfig()` has the defaults from Task 2. All seven `EDMRN_RELAY_*` variables set → the fields follow them, with `db` as a `Path`. `["--port", "9000"]` overrides `EDMRN_RELAY_PORT=8000` |
  | `test_config_leaders_are_split_and_checked` | ruling 9 | `--leaders "<a>, <b>\n<c>"` → a frozenset of the 3 authors; `--leaders nope` → `ConfigError` whose text contains `nope` |
  | `test_config_numbers_are_checked` | R3 | each of `--member-quota 0`, `--group-quota x`, `--port 70000` → `ConfigError` |
  | `test_main_refuses_bad_configuration` | ruling 9 | `main(["--leaders", "nope"]) == 2` and `"nope" in capsys.readouterr().err` |

  `tests/test_relay_server.py` (`loopback`). Every test runs `asyncio.run(scenario())` and starts
  `RelayServer(RelayConfig(port=0, db=tmp_path / "relay.db", leaders=frozenset({key_for("leader").author}), source_url=SRC))`.
  The helper `http(port, request_line, headers=()) -> (status, headers_lowercase, body)` writes a
  raw request through `asyncio.open_connection("127.0.0.1", port)` and reads until EOF. Clients
  use `websockets.asyncio.client.connect`.

  | Test | Ports / reason | Assertions |
  |---|---|---|
  | `test_routes_over_the_wire` | routes.test.ts, through `process_request` | `GET /` → 200, the body is the greeting. `GET /join` → 200, `content-security-policy == JOIN_CSP`, `content-length == str(len(body))`, the body is `JOIN_HTML`. `GET /nope` → 404. `GET /v1/groups/{V7}/ws` → 426. `POST /` → 404. A bad id with upgrade headers → 404, and `server.relay.groups == {}` |
  | `test_a_session_over_the_wire` | integration | connect to `/v1/groups/{g}/ws?x=1` → hello; `auth_frame(leader, g, nonce, genesis=envelope_item(...))` → a have; have `{}` → synced. A second client for the same author → the first gets `ConnectionClosed` with `rcvd.code == 4000` |
  | `test_auth_timer_closes_an_idle_socket` | ruling 5 | `monkeypatch.setattr(engine, "AUTH_TIMEOUT", 0.2)`; after the hello, the next `recv` raises `ConnectionClosed` with code 4001 within 5 s |
  | `test_a_1_2_mb_text_frame_closes_4008_not_1009` | ruling 7 | after auth, send `"é" * 600_000` → the close code is 4008 |
  | `test_a_stalled_peer_does_not_delay_others` | Review Focus 3, ruling 6 | the fake websocket B's `send` awaits a never-set `asyncio.Event`; the fake C records sends. `pb, pc = Peer(B), Peer(C)`; `server.dispatch([Send(SimpleNamespace(conn=pb), "x"), Send(SimpleNamespace(conn=pc), "y"), Send(SimpleNamespace(conn=pc), "z")])` → within 1 s `C.sent == ["y", "z"]` and `B` has sent nothing |
  | `test_run_stops_on_the_event_and_persists_statuses` | Review Focus 2, R3 | `task = asyncio.create_task(run(config, stop, started=port_future.set_result))`; `port = await port_future`. The leader creates the group and sends `status_frame(leader, 0.7, at=stamp_at(time.time()))`. `stop.set()` → the client's close code is 1001 and `await task` returns. `[a for a, _, _ in RelayStore(db).statuses(g)] == [leader.author]` |

  `tests/test_isolation.py` additions:

  | Test | Assertions |
  |---|---|
  | `test_loopback_marker_allows_127_0_0_1_only` (`@pytest.mark.loopback`) | each raises `RuntimeError`: `socket.create_connection(("192.0.2.1", 9), timeout=0.1)`; `socket.getaddrinfo("localhost", 80)`; `socket.socket().connect(("10.255.255.1", 9))`. A listener bound to `("127.0.0.1", 0)` accepts `socket.create_connection(("127.0.0.1", port))` |
  | `test_unmarked_tests_cannot_reach_loopback` | `socket.create_connection(("127.0.0.1", port))` to such a listener raises `RuntimeError` |

- [ ] **Step 2: Run the tests to verify they fail.**
  `.venv/Scripts/python.exe -m pytest tests/test_relay_http.py tests/test_relay_server.py tests/test_isolation.py -q`
  Expected: `ModuleNotFoundError` for the new modules, and failures in the guard tests.

- [ ] **Step 3: Implement** `join_page.py`, `server.py`, `__main__.py`, `load_config`, the
  conftest guard, the `pytest.ini` marker, and the `patterns.md` and `run.md` sentences.

- [ ] **Step 4: Run the tests to verify they pass.** Same command. Expected: all pass. Then run
  `$env:PYTHONPATH='.;relay'; .venv/Scripts/python.exe -m edmrn_relay --help` (PowerShell).
  Expected: a usage line listing the seven options; no socket is opened.

- [ ] **Step 5: Run the full suite.** Expected: no failures.

- [ ] **Step 6: Commit.**
  ```bash
  git add relay/edmrn_relay/join_page.py relay/edmrn_relay/server.py relay/edmrn_relay/__main__.py relay/edmrn_relay/config.py tests/conftest.py pytest.ini tests/test_relay_http.py tests/test_relay_server.py tests/test_isolation.py docs/architecture/patterns.md docs/how-to/run.md
  git commit -m "feat(relay): asyncio server, routes, invite page and command line (B1 S5 S17, R3)"
  ```

---

### Task 7: Loopback test with the real client

**Files:**
- Modify: `tests/relay_fakes.py` (add `ServerThread`)
- Create: `tests/test_relay_loopback.py` (`pytestmark = pytest.mark.loopback`)
- Delete: `tests/test_relay_e2e.py`
- Modify: `pytest.ini` (remove the `relay_e2e` marker), `tests/conftest.py` (remove the
  `relay_e2e` early return and its comment)
- Modify: `docs/how-to/run.md`. Remove the opt-in end-to-end section and the `relay_e2e` clause.
  Add one sentence: `tests/test_relay_loopback.py` runs two EDMRN crew clients against the real
  relay on 127.0.0.1 in the default run.

**Interfaces:**
- Consumes: Task 6's `RelayServer` and `RelayConfig`. From the client: `CrewGroup`, `CrewStore`,
  `Store`, `KeyRing`, `Writer`, `CrewSync`, `crew_helpers.visit`. Everything else is as in
  `tests/test_relay_e2e.py`, whose `Root`, `Ship`, `wait_for` and `world` helpers move into the new
  test file unchanged.
- Produces:
  ```python
  class ServerThread:
      def __init__(self, config: RelayConfig) -> None  # thread "edmrn-relay-test" with its own loop;
                                                       # returns once RelayServer.start() returned, re-raising its error; 10 s cap
      port: int
      def stop(self) -> None   # RelayServer.stop() on that loop, stop the loop, join (10 s)
  ```

- [ ] **Step 1: Write the failing test.**

  The fixture `relay_url(tmp_path, monkeypatch)` works as follows:
  - `monkeypatch.setattr(CrewSync, "FLUSH_INTERVAL", 0.5)`;
  - `leader = KeyRing(tmp_path / "leader_keys").for_commander("Leader").author`;
  - `server = ServerThread(RelayConfig(port=0, db=tmp_path / "relay.db", leaders=frozenset({leader}), source_url="https://example.invalid/edmrn"))`;
  - yield `f"ws://127.0.0.1:{server.port}"`; `server.stop()` in `finally`.

  `test_two_clients_exchange_envelopes_and_a_status(relay_url, tmp_path)` replaces the e2e test
  (R5). It keeps that test's `world(...)` setup and its `friend_is_live` reconnect on `refused`,
  then checks:
  - `leader.group.create_group("Loopback", relay_url)` → `wait_for(lambda: leader.group.state == "live")`.
  - `invite = leader.group.create_invite("Friend", 1)`, then
    `wait_for(lambda: not leader.group.sync._flush_requested)`, then
    `friend.group.join(invite.link)` → the friend becomes live.
  - The friend writes a visit with `address="42"` and `system="Loopback A"`. After
    `leader.group.inbox.drain()`, `leader.crew.system("42").name == "Loopback A"`.
  - The leader writes a visit with `address="43"` and `system="Loopback B"`. After
    `friend.group.inbox.drain()`, `friend.crew.system("43").name == "Loopback B"`.
  - `leader.group.board.snapshot()[friend_author].status["hull"] == 0.75`, and
    `friend_author in leader.group.roster.members`.
  - In `finally`: stop both groups and close both stores.

- [ ] **Step 2: Run the test to verify it fails.**
  `.venv/Scripts/python.exe -m pytest tests/test_relay_loopback.py -q`
  Expected: `ImportError` for `ServerThread`.

- [ ] **Step 3: Implement** `ServerThread`. Then delete `tests/test_relay_e2e.py`
  (`git rm tests/test_relay_e2e.py`), and edit `pytest.ini`, `tests/conftest.py` and
  `docs/how-to/run.md`.

- [ ] **Step 4: Run the test to verify it passes.** Same command. Expected: 1 passed, in under
  30 s.

- [ ] **Step 5: Run the full suite.** Expected: no failures. The skip count drops by one, because
  the e2e skip is gone.

- [ ] **Step 6: Commit.**
  ```bash
  git add tests/relay_fakes.py tests/test_relay_loopback.py pytest.ini tests/conftest.py docs/how-to/run.md
  git commit -m "test(relay): two EDMRN clients through the real relay on 127.0.0.1 (R5)"
  ```
  `git rm` in Step 3 already staged the deletion of `tests/test_relay_e2e.py`.

---

### Task 8: Deployment artifacts and documentation

**Files:**
- Create: `relay/requirements.txt`, `relay/Dockerfile`, `relay/Dockerfile.dockerignore`,
  `relay/compose.yaml`, `relay/Caddyfile`, `relay/edmrn-relay.service`
- Create: `tests/test_relay_deploy.py`
- Modify: `docs/how-to/deploy-relay.md` (rewrite), `docs/how-to/README.md` (row)
- Modify: `docs/architecture/overview.md` (crew sync data flow items 4 and 5, first invariant)
- Modify: `docs/how-to/run.md` ("Crew relay" section)
- Modify: `.gitignore` (add `relay.db*`)

**Interfaces:**
- Consumes: `edmrn_relay.__main__` and `edmrn_relay.config.DEFAULT_PORT`.
- Produces: the files below. The Dockerfile's `COPY` lines are the single list of `edmrn` files
  in the image.

**File contents:**

`relay/requirements.txt`: one comment line, then `cryptography>=42` and `websockets>=15`.

`relay/Dockerfile` (build context = repository root):
```dockerfile
FROM python:3.13-slim
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin relay \
 && mkdir /data && chown relay:relay /data
WORKDIR /app
COPY relay/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY LICENSE /app/LICENSE
COPY edmrn/__init__.py /app/edmrn/__init__.py
COPY edmrn/ext/__init__.py /app/edmrn/ext/__init__.py
COPY edmrn/ext/crew_events.py edmrn/ext/crew_identity.py edmrn/ext/crew_roster.py edmrn/ext/crew_protocol.py /app/edmrn/ext/
COPY relay/edmrn_relay /app/edmrn_relay
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    EDMRN_RELAY_HOST=0.0.0.0 EDMRN_RELAY_DB=/data/relay.db
VOLUME /data
USER relay
EXPOSE 8765
CMD ["python", "-m", "edmrn_relay"]
```

`relay/Dockerfile.dockerignore`, applied by BuildKit to `-f relay/Dockerfile`: the line `*`, then
`!<source>` for every `COPY` source above (`!LICENSE`, `!relay/requirements.txt`, the six `edmrn`
files, `!relay/edmrn_relay`), then `**/__pycache__`.

`relay/compose.yaml` (secrets and keys in `relay/.env`; `.env` is already git-ignored):
```yaml
services:
  relay:
    build: {context: .., dockerfile: relay/Dockerfile}
    restart: unless-stopped
    environment:
      EDMRN_RELAY_LEADERS: ${EDMRN_RELAY_LEADERS:?set EDMRN_RELAY_LEADERS in relay/.env}
      EDMRN_RELAY_SOURCE_URL: ${EDMRN_RELAY_SOURCE_URL:-}
    volumes: [relay-data:/data]
    stop_grace_period: 15s
  caddy:
    image: caddy:2
    restart: unless-stopped
    depends_on: [relay]
    ports: ["80:80", "443:443", "443:443/udp"]
    environment:
      RELAY_HOST: ${RELAY_HOST:?set RELAY_HOST in relay/.env}
    volumes: [./Caddyfile:/etc/caddy/Caddyfile:ro, caddy-data:/data, caddy-config:/config]
volumes: {relay-data: {}, caddy-data: {}, caddy-config: {}}
```

`relay/Caddyfile` (Caddy proxies WebSocket upgrades and sets no read timeout by default):
```
{$RELAY_HOST} {
	reverse_proxy relay:8765
}
```

`relay/edmrn-relay.service`:
```ini
[Unit]
Description=EDMRN crew relay
After=network-online.target
Wants=network-online.target

[Service]
User=edmrn-relay
Group=edmrn-relay
WorkingDirectory=/opt/edmrn
Environment=PYTHONPATH=/opt/edmrn:/opt/edmrn/relay
EnvironmentFile=/etc/edmrn-relay.env
ExecStart=/opt/edmrn/.venv-relay/bin/python -m edmrn_relay --db /var/lib/edmrn-relay/relay.db
StateDirectory=edmrn-relay
Restart=on-failure
KillSignal=SIGTERM
TimeoutStopSec=15
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

**`docs/how-to/deploy-relay.md`** (R4; timeless prose, exact commands). Sections:
1. **What it is.** The `relay/` service (ADR-0009), listening on loopback or inside the container.
   TLS ends at Caddy or the existing reverse proxy. The client's address is
   `wss://galroute.the-hub.cloud`.
2. **DNS** (ruling 12). An A record, plus AAAA for IPv6, `galroute` → the server's IP, in the
   Cloudflare DNS of `the-hub.cloud`, with proxy status "DNS only". Check with
   `nslookup galroute.the-hub.cloud`.
3. **Leader key.** EDMRN → Settings → Crew… → copy "Your crew key". Several keys are separated by
   commas. The keys are public.
4. **Option A: Docker Compose with Caddy.**
   - Needs Docker Engine with the compose plugin, and ports 80 and 443 free.
   - `relay/.env` holds `RELAY_HOST=galroute.the-hub.cloud`, `EDMRN_RELAY_LEADERS=<key>` and
     `EDMRN_RELAY_SOURCE_URL=<fork URL or empty>`.
   - Start: `cd relay && docker compose up -d --build`.
   - Check: `curl https://galroute.the-hub.cloud/` shows the greeting.
5. **Option B: systemd behind an existing reverse proxy.**
   - Steps: `useradd --system edmrn-relay`; `git clone` to `/opt/edmrn`;
     `uv venv --python 3.13 /opt/edmrn/.venv-relay`;
     `uv pip install --python /opt/edmrn/.venv-relay -r /opt/edmrn/relay/requirements.txt`;
     `/etc/edmrn-relay.env` with the `EDMRN_RELAY_*` variables; copy the unit to
     `/etc/systemd/system/`; `systemctl enable --now edmrn-relay`.
   - The nginx `server` block for `galroute.the-hub.cloud`:
     - `location / { proxy_pass http://127.0.0.1:8765; proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";
       proxy_set_header Host $host; proxy_read_timeout 120s; proxy_send_timeout 120s; }`;
     - the certificate from certbot;
     - note that 120 s is above the client's 30 s ping interval.
6. **Operating:**
   - **Logs:** `docker compose logs -f relay` / `journalctl -u edmrn-relay -f`. Each past day
     appears once as a `crew-daily-total` line.
   - **Backup** (ruling 11), option A:
     `docker compose exec relay python -c "import sqlite3; sqlite3.connect('/data/relay.db').backup(sqlite3.connect('/data/backup.db'))"`,
     then `docker compose cp relay:/data/backup.db ./relay-backup-$(date +%F).db`. Option B:
     `sqlite3 /var/lib/edmrn-relay/relay.db ".backup /var/backups/relay-$(date +%F).db"`.
   - **Update:** `git pull`, then `docker compose up -d --build` / `systemctl restart
     edmrn-relay`. Members reconnect by themselves.
   - **Adding a leader:** extend `EDMRN_RELAY_LEADERS` and restart.
   - **Limits:** `EDMRN_RELAY_MEMBER_QUOTA` and `EDMRN_RELAY_GROUP_QUOTA` (defaults 5,000 and
     80,000 per UTC day) are abuse limits.
   - **Re-seed:** a lost database costs nothing permanent. Every member holds a full replica, so
     the next connections re-seed it (B1 S7).

**Other documentation:**
- **`docs/how-to/README.md`:** the deploy row's task becomes "Deploy and operate the crew relay on
  the commander's server".
- **`docs/architecture/overview.md`, crew sync data flow:**
  - Item 4: replace "the relay's socket attachment" with "the relay (in memory on the sender's
    connection; written to SQLite when that connection closes or the relay stops)".
  - Item 5 becomes: "The relay (`relay/`, ADR-0009) is a Python asyncio service on the commander's
    server behind a TLS reverse proxy. One SQLite database holds every group. Per group it keeps
    membership and heads in memory, writes them through, stores signed envelopes of members, counts
    daily quotas, and serves only up to each author's head (and `last_seq` cap). It imports
    `crew_events`, `crew_identity`, `crew_roster` and `crew_protocol`, so client and relay judge
    envelopes, signatures, roster and frames with the same code."
  - The first invariant becomes: "Membership is computed from roster envelopes by the same code
    (`crew_roster.compute_roster`) in client and relay. Vectors in `crew-relay/test/vectors.json`,
    generated by `tests/vectors/make_crew_vectors.py`, pin the shared rules. Roster envelopes must
    be in canonical form."
- **`docs/how-to/run.md`, "Crew relay":**
  - Add "Run the relay locally": in PowerShell, `$env:PYTHONPATH='.;relay';
    .venv/Scripts/python.exe -m edmrn_relay --port 8765 --db "$env:TEMP\relay.db" --leaders
    <your-author-key>`, then point "Create group" at `ws://127.0.0.1:8765`.
  - Add: the relay's tests are `tests/test_relay_*.py`, in the default run.
  - "Deploying and operating the relay on Cloudflare" becomes "Deploying and operating the relay".
  - Leave the Node/`crew-relay` block for Task 9.
- **`.gitignore`:** add the line `relay.db*`. The default `--db` is `./relay.db`.

- [ ] **Step 1: Write the failing tests** in `tests/test_relay_deploy.py`. `ROOT` is the repo root.

  | Test | Assertions |
  |---|---|
  | `test_relay_requirements_match_the_clients` | the non-comment lines of `relay/requirements.txt`, sorted, equal the lines of `requirements-edmrn.txt` that start with `cryptography` or `websockets`, sorted; there are exactly 2 |
  | `test_image_layout_imports_only_the_shared_modules` (Review Focus 1) | parse the `COPY` lines of `relay/Dockerfile`; the sources under `edmrn/` are exactly the six expected files. Copy them to `tmp_path/<source>` and `relay/edmrn_relay` to `tmp_path/edmrn_relay`. Run `subprocess.run([sys.executable, "-I", "-c", SCRIPT, str(tmp_path)], cwd=tmp_path, capture_output=True, text=True, timeout=60)`. `SCRIPT` inserts `argv[1]` at `sys.path[0]`, imports `edmrn_relay.__main__`, and prints `sorted(m for m in sys.modules if m == "edmrn" or m.startswith("edmrn."))`. Expected: return code 0 and output `['edmrn', 'edmrn.ext', 'edmrn.ext.crew_events', 'edmrn.ext.crew_identity', 'edmrn.ext.crew_protocol', 'edmrn.ext.crew_roster']` |
  | `test_dockerignore_admits_every_copy_source` | every `COPY` source of `relay/Dockerfile` has a line `!<source>` in `relay/Dockerfile.dockerignore`, and its first line is `*` |
  | `test_ports_and_entry_points_agree` | `RelayConfig().port == 8765`; `"EXPOSE 8765"` in the Dockerfile; `"reverse_proxy relay:8765"` in the Caddyfile; `"dockerfile: relay/Dockerfile"` in `compose.yaml`; `"-m edmrn_relay"` in the service file and `'"-m", "edmrn_relay"'` in the Dockerfile |

- [ ] **Step 2: Run the tests to verify they fail.**
  `.venv/Scripts/python.exe -m pytest tests/test_relay_deploy.py -q`
  Expected: `FileNotFoundError` for `relay/requirements.txt` and the Dockerfile.

- [ ] **Step 3: Create the artifacts and write the documentation** above.

- [ ] **Step 4: Run the tests to verify they pass.** Same command. Expected: all pass. If
  `docker --version` works, also run `docker build -f relay/Dockerfile -t edmrn-relay:test .` and
  expect success. Docker is not installed on the development machine as of 2026-11-08, so the task
  report says whether this step ran.

- [ ] **Step 5: Run the full suite.** Expected: no failures.

- [ ] **Step 6: Commit.**
  ```bash
  git add relay/requirements.txt relay/Dockerfile relay/Dockerfile.dockerignore relay/compose.yaml relay/Caddyfile relay/edmrn-relay.service tests/test_relay_deploy.py docs/how-to/deploy-relay.md docs/how-to/README.md docs/architecture/overview.md docs/how-to/run.md .gitignore
  git commit -m "feat(relay): Docker Compose with Caddy, systemd unit, deploy how-to (R4, R7)"
  ```

---

### Task 9: Remove the Worker

**Files:**
- Move: `crew-relay/test/vectors.json` → `tests/vectors/crew_vectors.json`
- Delete: `crew-relay/` (tracked files with `git rm -r`; then remove the untracked
  `node_modules/` and `.wrangler/` from disk)
- Modify: `tests/vectors/make_crew_vectors.py`. `OUT = Path(__file__).resolve().parent /
  "crew_vectors.json"`; the docstring's first line names `tests/vectors/crew_vectors.json`.
- Modify: `tests/test_crew_vectors.py`. `VECTORS = ROOT / "tests" / "vectors" /
  "crew_vectors.json"`; `tests/test_relay_vectors.py` follows it through the import.
- Modify: `.gitattributes` (`tests/vectors/crew_vectors.json text eol=lf`), `.gitignore` (remove
  the two `crew-relay/` lines)
- Modify: `edmrn/ext/crew_protocol.py`, docstring only: "The relay's side of the same protocol is
  `relay/edmrn_relay/engine.py`, which imports this module."
- Modify: `edmrn/ext/crew_roster.py`, docstring only: "The relay imports this module
  (`relay/edmrn_relay/engine.py`); `tests/vectors/crew_vectors.json` pins the shared rules." These
  two docstrings are the only `edmrn/` edits (Global Constraints).
- Modify: `docs/architecture/overview.md`. Remove the `crew-relay/` row; the first crew-sync
  invariant names `tests/vectors/crew_vectors.json`.
- Modify: `docs/how-to/run.md`. Remove the Node block (`cd crew-relay`, `npm ci`, `npm test`,
  `npm run typecheck`) and the `wrangler dev` block; the vectors line names
  `tests/vectors/crew_vectors.json`.
- Modify: `CLAUDE.md`, the Stack sentence. Drop "; the TypeScript Worker in `crew-relay/` is its
  behaviour reference until the port removes it", ending the sentence after "(ADR-0009)".

**Interfaces:**
- Consumes: everything above. Produces: nothing new.

- [ ] **Step 1: Make the regeneration check fail** by moving the file first:
  `git mv crew-relay/test/vectors.json tests/vectors/crew_vectors.json`, then
  `.venv/Scripts/python.exe -m pytest tests/test_crew_vectors.py tests/test_relay_vectors.py -q`.
  Expected: `FileNotFoundError` for `crew-relay/test/vectors.json`.

- [ ] **Step 2: Repoint** the generator and `tests/test_crew_vectors.py`. Run the same command.
  Expected: all pass, including `test_committed_vectors_match_a_fresh_generation`.

- [ ] **Step 3: Delete** `crew-relay/` (`git rm -r crew-relay`, then remove the leftover
  directory from disk). Apply the remaining file edits listed above.

- [ ] **Step 4: Sweep.** Run
  `git grep -n -i -I -e crew-relay -e wrangler -e "durable object" -e cloudflare -e workerd -e CREW_LEADERS -- . ":!docs/superpowers/specs" ":!docs/superpowers/plans" ":!docs/architecture/decisions" ":!docs/handover/HANDOVER.md"`.
  - Expected remaining hits: only `docs/how-to/deploy-relay.md` lines that name the Cloudflare DNS
    of the `the-hub.cloud` zone (justified: the zone's DNS is managed there).
  - Fix every other hit. List each remaining hit with its justification in the task report.
  - The excluded paths are justified because they are historical: specs, plans and ADRs are
    amended by ADR-0009 and the relay spec, never edited, and the handover is rewritten by the
    controller.

- [ ] **Step 5: Run the full suite.** Expected: no failures; the test count is unchanged from Task 8.

- [ ] **Step 6: Commit.**
  ```bash
  git add tests/vectors/make_crew_vectors.py tests/test_crew_vectors.py .gitattributes .gitignore edmrn/ext/crew_protocol.py edmrn/ext/crew_roster.py docs/architecture/overview.md docs/how-to/run.md CLAUDE.md
  git commit -m "chore(relay): remove the Cloudflare Worker; vectors become Python fixtures (R6)"
  ```
  `git mv` (Step 1) and `git rm -r` (Step 3) already staged the move and the deletions. Check
  `git status` before committing: nothing outside this task's files may be staged.
