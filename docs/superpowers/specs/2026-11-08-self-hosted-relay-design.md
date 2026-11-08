# Self-hosted crew relay in Python — design

Status: Accepted (2026-11-08; the commander delegated all decisions for this work)
Date: 2026-11-08
Rules: ADR-0009 (supersedes ADR-0005's transport), ADR-0007, ADR-0008. Behaviour authority:
`2026-10-13-crew-sync-core-design.md` (Spec B1), sections S5–S9, S14, S17, S18, as amended here.

## Purpose

The commander now has a dedicated server (2026-11-08). The crew relay moves there from Cloudflare,
rewritten in Python so it shares the client's protocol modules. The wire protocol does not change:
an EDMRN client built for the Cloudflare relay talks to this relay unchanged.

## Requirements

- R1. **Behaviour parity.** The relay implements Spec B1 S5 (invite page), S6 (frames), S7 (session,
  heads, catch-up, roster handling), S8 (status), S9 (error codes and close codes), S14 (quotas),
  S17 (routes) and S18 (checks, cleanup), with these amendments:
  - S7.1: an unauthenticated socket is closed by a 10 s timer (no hibernation constraint).
  - S7.2: the leader keys come from the configuration (`--leaders`, environment
    `EDMRN_RELAY_LEADERS`), not from a Worker variable.
  - S8: a connection's latest status lives in memory on that connection; it is written to the
    `status` table when the socket closes or the relay shuts down.
  - S13, S15, S16 (Cloudflare budget) no longer apply. S14's limits stay as abuse protection, with
    the same defaults (5,000 per member and 80,000 per group per UTC day), configurable.
  - S17: `GET /` names `--source-url`; the greeting, `/join` page and its Content-Security-Policy
    are byte-for-byte the Worker's.
  - S18: one SQLite database holds all groups; every table carries `group_id`. Membership and heads
    are kept in memory per group once loaded and written through to SQLite.
- R2. **One implementation of shared rules.** The relay imports `edmrn.ext.crew_events`,
  `crew_identity`, `crew_roster` and `crew_protocol`; it never copies their rules. Server-only
  frame parsing (the `auth` frame) and the session engine live in the relay package.
- R3. **Operation.** `python -m edmrn_relay` with `--host` (default `127.0.0.1`), `--port`
  (`8765`), `--db` (`./relay.db`), `--leaders`, `--source-url`, `--member-quota`,
  `--group-quota`; each also from an `EDMRN_RELAY_*` environment variable. Logs go to stdout, one
  line per event, never envelope contents. SIGTERM/SIGINT close every socket with 1001 after
  persisting statuses. The SQLite file uses WAL; one connection on the event-loop thread.
- R4. **Deployment.** `relay/Dockerfile` (python:3.13-slim, non-root user, `/data` volume),
  `relay/compose.yaml` (relay + Caddy with automatic TLS for the host name in `RELAY_HOST`),
  `relay/Caddyfile`, and `relay/edmrn-relay.service` (systemd, without Docker).
  `docs/how-to/deploy-relay.md` covers: DNS record for `galroute.the-hub.cloud` → the server;
  option A Compose with Caddy; option B behind an existing reverse proxy (nginx snippet with the
  WebSocket upgrade headers and a read timeout above the client's ping interval); backup with
  `sqlite3 relay.db ".backup ..."`; update; logs; adding a leader.
- R5. **Tests.**
  - The session engine is tested without sockets through a fake connection (send, close), covering
    every behaviour `crew-relay/test/group.test.ts`, `status.test.ts`, `routes.test.ts` and
    `vectors.test.ts` pin. `budget.test.ts` is dropped (S13).
  - One loopback test runs the real server on `127.0.0.1` with an ephemeral port and the client's
    `CrewSync` against it: create a group, join a second member, exchange envelopes and a status.
    It replaces the opt-in `tests/test_relay_e2e.py`. The test-suite network guard
    (`tests/conftest.py`) lets tests marked `loopback` connect to `127.0.0.1` only.
- R6. **Removal.** `crew-relay/` is deleted in the last task. `crew-relay/test/vectors.json` moves
  to `tests/vectors/crew_vectors.json`; the generator and `tests/test_crew_vectors.py` follow it.
- R7. **Dependencies.** `relay/requirements.txt` pins `websockets` and `cryptography` to the
  versions in `requirements-edmrn.txt`; nothing else.

## Design

### Package `relay/edmrn_relay/`

| Module | Holds |
|---|---|
| `config.py` | `RelayConfig` (frozen) from argv and environment |
| `store.py` | `RelayStore`: schema, migrations by `user_version`, the queries the engine needs |
| `engine.py` | `Group` (one per `group_id`: roster, heads, connections, quotas) and `Session` (one per socket: state `hello` → `authed`, author, last status time) |
| `server.py` | the `websockets.asyncio.server` app: `process_request` serves `/`, `/join`, 404 and refuses a malformed `group_id` before any group is touched; the WebSocket handler runs a `Session` |
| `join_page.py` | the static invite page text and its headers |
| `__main__.py` | argument parsing, logging, signal handling |

The engine is synchronous: frame in → list of actions out (send to this socket, send to others,
close with code). The server turns actions into socket writes. The engine is therefore tested
without an event loop.

### Schema (`user_version` 1)

```sql
CREATE TABLE groups (group_id TEXT PRIMARY KEY, leader TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE log (group_id TEXT NOT NULL, author TEXT NOT NULL, seq INTEGER NOT NULL,
                  e BLOB NOT NULL, s BLOB NOT NULL, PRIMARY KEY (group_id, author, seq));
CREATE TABLE heads (group_id TEXT NOT NULL, author TEXT NOT NULL, seq INTEGER NOT NULL,
                    PRIMARY KEY (group_id, author));
CREATE TABLE roster (group_id TEXT NOT NULL, author TEXT NOT NULL, seq INTEGER NOT NULL,
                     e BLOB NOT NULL, PRIMARY KEY (group_id, author, seq));
CREATE TABLE quota (group_id TEXT NOT NULL, day INTEGER NOT NULL, author TEXT NOT NULL,
                    count INTEGER NOT NULL, PRIMARY KEY (group_id, day, author));
CREATE TABLE status (group_id TEXT NOT NULL, author TEXT NOT NULL, e BLOB NOT NULL,
                     s BLOB NOT NULL, PRIMARY KEY (group_id, author));
```

`roster` stores the raw bytes of roster envelopes that passed S1/S2's checks; membership is
`crew_roster.compute_roster` over them, recomputed after each roster change.

### Reference implementation

`crew-relay/src/group.ts` and its tests are the behaviour reference while the port runs; where
they and Spec B1 disagree, Spec B1 (as amended above) wins and the task's ruling says so.

## Not in scope

- Reading EDDN or dump data on the server.
- Any client change. If the port finds a client defect, it is recorded, not fixed here.
- A web dashboard for the relay.

## Rulings made without the commander

1. Python and the client's modules instead of self-hosting the TypeScript Worker in `workerd`:
   one implementation of the shared rules, no Cloudflare runtime to operate.
2. Caddy as the default TLS front: automatic certificates; an nginx variant for servers that
   already run a web server on 443.
3. Quotas stay with their old defaults, now as abuse limits.
