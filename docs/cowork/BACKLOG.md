# Cowork brief — EDMRN

Role, rules and research order: `../../../cowork/INSTRUCTIONS.md`. Read that first.

## The project

EDMRN is a fork of *ED Multi Route Navigation* (NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer,
AGPL-3.0), a Python/customtkinter desktop route planner for Elite Dangerous. The fork adds journal
location detection, saved routes, system bookmarks, and an exchange channel through which a wing
leader sends routes to a small group of friends.

## Research backlog

1. **Transport for the friend exchange.** Compare peer-to-peer and server-based options for a
   group of roughly 2–10 commanders on Windows home connections, from Python: NAT traversal
   (UDP hole punching, STUN/TURN, relay fallback), libraries (e.g. libp2p/py-libp2p, aiortc
   WebRTC data channels, Tailscale/headscale or ZeroTier as an overlay, Matrix, a small
   WebSocket relay, a hosted pub/sub such as a free-tier service). For each: does it work behind
   typical consumer NAT and CGNAT, what must each friend install or configure, offline delivery
   (leader sends while a friend is not online), identity and authentication of group members,
   encryption, hosting cost and maintenance, Python maturity, licence compatibility with AGPL-3.0.
2. **AGPL-3.0 and a network component.** What the AGPL network-use clause (§13) requires if we
   run a modified server that friends connect to, and what it requires for a pure P2P design.
3. **How other ED tools share routes.** Existing route or bookmark exchange formats and channels
   in the ED community (Spansh route export, EDDiscovery expeditions, EDSM, in-game squadron
   tools, Discord bots) — formats we could import or export.
