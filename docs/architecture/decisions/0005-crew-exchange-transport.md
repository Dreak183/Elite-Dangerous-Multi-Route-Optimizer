# ADR-0005: Crew data travels as a signed event log through a Cloudflare relay, with peer-to-peer as a staged fallback

Status: Accepted
Date: 2026-10-12

## Context

The crew features share live status, explored systems, finds, notes, chat and the leader's route
among a small fixed group (2–10 commanders) on Windows home connections, some behind CGNAT. Members
are often offline while others play, and the commander wants data gathered without a connection to
merge later without duplicates (two members in the same system must not create two systems). The
commander has a Cloudflare account with own domains, an occasional VPS that may not stay available,
and a group Discord server. Measured limits, prices, NAT traversal rates and library maturity are in
`../../../../knowledge/group-sync-transport.md`; route formats and why EDDN cannot identify friends
are in `../../../../knowledge/ed-data-sharing.md`.

## Decision

- **One protocol, several transports.** Crew data is an append-only event log in `edmrn.db`. Each
  event carries a global `event_id`, its `author` (member key), a gap-free per-author `seq` and an
  Ed25519 signature. Journal-derived events get deterministic ids (hash of author, kind and natural
  key), manual ones UUIDv7. Merging is: verify signature, validate schema, `INSERT OR IGNORE`.
  Catch-up exchanges version vectors (highest contiguous `seq` per author). Every mutable item
  (status, a note, the route) has exactly one writer; the highest `seq` of that writer wins. Live
  status is ephemeral: only the latest value per commander is kept, never logged.
- **Primary transport:** a Cloudflare Worker with one SQLite-backed Durable Object per group,
  WebSocket with hibernation, reached at a sub-domain of the commander's domain. Its source is
  TypeScript in its own top-level folder of this repository, outside upstream's tree, licensed
  AGPL-3.0; its greeting names the source URL.
- **The server may read content.** Transport is TLS; authenticity comes from the signatures. The
  server may evaluate data and post finds to a Discord webhook the leader configures per group.
  Discord is an output only, never a sync channel.
- **Peer-to-peer is staged:** first the same protocol over a direct WebSocket between peers (LAN,
  Tailscale, ZeroTier); then a spike with iroh (in-app, no install) and, if the spike passes on
  Windows, behind CGNAT and in a PyInstaller bundle, an iroh transport. aiortc and libp2p are not
  used.
- **Membership:** each member has an Ed25519 key pair; the leader admits and removes members with
  signed roster events; a new member joins with a one-time invite string. Expeditions are named
  filters inside the group.

## Consequences

- No transport may carry anything but this protocol; a second sync path would break the "no
  duplicates, any order" guarantee that the deterministic ids and `INSERT OR IGNORE` give.
- Friends install nothing beyond EDMRN. The group depends on Cloudflare for live exchange while
  online together without a peer path; every member holds a full replica, so the server can be
  re-seeded from any member.
- The Free plan's 100,000 row writes per day is the binding limit: the Durable Object throttles
  status upserts and enforces per-member quotas. Beyond that, Workers Paid costs USD 5 per month.
- A second language (TypeScript) enters the repository, with its own tests (Miniflare/Vitest).
- Data from friends is untrusted: bounds, a character whitelist, rejection of unknown keys and size
  caps apply on client and server before insert (`../../handover/HANDOVER.md` lists the store's
  current gaps).
- A lost key or lost database means a new member identity unless the client first fetches its own
  events and continues at its highest `seq`.
