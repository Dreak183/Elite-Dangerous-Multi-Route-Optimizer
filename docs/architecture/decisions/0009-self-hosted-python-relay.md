# ADR-0009: The crew relay is a Python service on the commander's own server

Status: Accepted
Date: 2026-11-08
Supersedes: [ADR-0005](0005-crew-exchange-transport.md) on three points only — the primary
transport (a Cloudflare Worker with Durable Objects), the staged peer-to-peer fallback, and the
consequences that follow from them (Cloudflare Free-plan row budget, TypeScript as a second
language). ADR-0005's protocol (one signed append-only event log, deterministic ids, `INSERT OR
IGNORE` merging, version-vector catch-up, single writer per mutable item, ephemeral status), its
rule that no transport carries anything but that protocol, "the server may read content", Discord
as an output only, and its membership model stay in force.

## Context

ADR-0005 chose Cloudflare because the commander had no server that would stay available. On
2026-11-08 the commander reported a dedicated server that is available now and is meant to host,
later, an EDDN listener and a database built from Spansh and EDSM dumps. The Cloudflare relay
(`crew-relay/`, TypeScript) was built and tested in local `workerd` but never deployed.

Most of its complexity paid for the platform, not for the crew features:
- the Free plan's 100,000 SQLite rows written per day shaped the schema (`WITHOUT ROWID` log, no
  secondary indexes, per-frame head and quota rows) and the status handling;
- hibernation forbade timers and in-memory state;
- a second implementation of the envelope, signature and roster rules in TypeScript had to be held
  equal to the Python client through shared test vectors.

The client modules that hold these rules (`crew_events`, `crew_identity`, `crew_roster`,
`crew_protocol`) import only the standard library and `cryptography`, so a Python server can use
them unchanged.

## Decision

- **Relay:** a Python 3.13 asyncio service in the top-level folder `relay/` (AGPL-3.0), built on
  the `websockets` server and the standard library's `sqlite3`. It implements Spec B1's relay
  behaviour (frames, session, heads, quotas, roster checks, status forwarding, routes `/`,
  `/join` and `/v1/groups/<group_id>/ws`) and imports the client's protocol modules instead of
  re-implementing them: one implementation of every rule that client and server must agree on.
- **Hosting:** the commander's dedicated server. TLS ends at a reverse proxy (Caddy by default, or
  an existing one) on port 443 of the relay's host name; the relay listens on a loopback or
  container-internal port. The client's relay address stays `wss://<host>` with no path or port.
- **No peer-to-peer transport.** A server the commander controls removes the reason for it; every
  member keeps a full replica, so any member re-seeds an emptied relay (Spec B1 S7).
- `crew-relay/` is deleted once the Python relay passes the behaviour its tests pinned; the shared
  vectors become fixtures of the Python test suite.

## Consequences

- One language in the repository again. Client/relay agreement is by construction for every rule
  in the shared modules; the relay's own tests cover the session, heads and quotas.
- The relay's limits (per-member and per-group daily quotas, frame and status rate limits) remain
  as abuse protection and are configurable; they are no longer a billing budget.
- The commander operates the server: updates, backups of the relay database, TLS renewal (Caddy
  automates it). Losing the database costs nothing permanent — members re-seed it.
- The same server can later host the EDDN listener and the dump database next to the relay; the
  relay does not depend on them.
- Friends' clients reach the relay only while the server is up; there is no fallback path.
