# ADR-0007: A received envelope is stored once signed and well-formed; payload validation and author binding gate only its projection

Status: Accepted
Date: 2026-10-13
Supersedes: [ADR-0005](0005-crew-exchange-transport.md) and
[ADR-0006](0006-store-layer-and-crew-modules.md) on one point each. ADR-0005 said merging is
"verify signature, validate schema, `INSERT OR IGNORE`". ADR-0006 said `crew_store.insert_received`
"validates every field before any SQL". Everything else in both ADRs stays in force.

## Context

The crew capture spec (C4) keeps every signed envelope, so that an author's `seq` stays gap-free
and a newer client's kinds or versions never block catch-up. Reviews of the implementation found
two more ways a friend's envelope can harm the group without breaking its signature:
- it can carry another member's deterministic `event_id`, which would suppress that member's own
  event;
- the same author can repeat an `event_id` with a different payload after a lost database, which
  made the projections depend on arrival order.

## Decision

- `insert_received` checks size, envelope structure and the signature. Then it inserts with
  `INSERT OR IGNORE` on (`author`, `seq`). Payload content is not a reason to refuse storage.
- The projector applies an envelope only if three conditions hold:
  - its kind and `v` are known and its payload passes the validator;
  - a journal kind's `event_id` equals the deterministic id of (`author`, `kind`, natural key from
    the payload);
  - a manual kind's `event_id` is a UUIDv7.

  Anything else stays in the log, unprojected, and a later client or `rebuild_projections()` can
  re-derive it.
- Every envelope that passes is projected, and every merge is idempotent. The first sight of each
  (`author`, `event_id`) gates counting only.
- The own-event writer skips an `event_id` only if the same author already logged it.

## Consequences

- Untrusted content reaches the `crew_log` table as signed bytes. It must never be read from
  there for display; only the validated projections are.
- A hostile but signed envelope costs one log row and a warning per rebuild. Spec B's quotas bound
  the volume.
- Reverting the author binding lets any member erase another member's events from every replica.
  Reverting idempotent projection makes replicas disagree after a member loses their database.
