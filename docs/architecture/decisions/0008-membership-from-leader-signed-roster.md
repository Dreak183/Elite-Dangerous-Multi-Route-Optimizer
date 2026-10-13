# ADR-0008: Group membership is computed from leader-signed roster envelopes and gates projection

Status: Proposed
Date: 2026-10-13

## Context

ADR-0005 has the leader admit and remove members with signed roster events and an invite string,
but it leaves open who decides membership and what it controls. If only the relay enforced
membership, a relay bug, a re-seed or a later peer-to-peer path could hand a client envelopes from
anyone holding a key, and those envelopes would be projected like friends' data. If membership
were derived from received data alone, a forged `group` envelope could name a new leader.

## Decision

- Membership is a pure function of the roster envelopes (`group`, `invite`, `join`, `remove`) of one
  group and the leader's author (crew sync core spec S2). The relay and every client compute it with
  the same rules and the same test vectors.
- The leader's author is a trust anchor taken from the invite string or from the local
  `group.json`, never from received envelopes.
- The projector applies an envelope only if its author is an own author or a member, and, for a
  removed member, only up to that member's `last_seq`. Everything else stays in the log
  unprojected (ADR-0007).
- An invite admits on its own: the first valid `join` with the matching token wins. Removal is final
  for that key.

## Consequences

- A client never trusts the relay's view of the roster, so a re-seeded or faulty relay cannot add
  members to a client's projections.
- A leaked invite token admits whoever redeems it first, until it expires (7 days). The leader can
  remove that key afterwards.
- A lost leader key freezes the roster: nobody can be admitted or removed. Recovery is a new group.
- Removing a member forces one `rebuild_projections()`, because projected rows must be withdrawn.
- Reverting the gate lets anyone who reaches a client, through any transport, write into its
  projections.
