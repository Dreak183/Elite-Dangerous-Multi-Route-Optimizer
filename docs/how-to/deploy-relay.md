# Deploying the crew relay

The relay is the Worker in `crew-relay/` (crew sync core spec S17). It runs on the commander's
Cloudflare account at `galroute.the-hub.cloud`. On the Free plan nothing is charged; over a daily
limit operations fail until 00:00 UTC (`../../../knowledge/group-sync-transport.md` §1.1).

## Before the first deploy

- The zone `the-hub.cloud` is listed as Active in the Cloudflare dashboard (Websites).
- Node 20 or newer: `node --version`.
- Dependencies and tests: `cd crew-relay`, `npm ci`, `npm test`.

## First deploy

1. Sign in: `npx wrangler login`. A browser tab opens on Cloudflare; choose Allow. Check with
   `npx wrangler whoami`, which names the account.
2. Leader key: in EDMRN open Settings > Crew... and copy "Your crew key". In
   `crew-relay/wrangler.toml` set `CREW_LEADERS = "<key>"` (several keys: comma-separated). The
   key is public; commit the change.
3. Source URL: set `SOURCE_URL` to the GitHub fork's URL once it exists; leave it empty until then.
4. Deploy: `npx wrangler deploy`. Wrangler creates the Worker, the Durable Object class (migration
   `v1`), and the DNS record and certificate for `galroute.the-hub.cloud`.
5. Check: open `https://galroute.the-hub.cloud/`; it answers with a one-line greeting. A first
   certificate can take a few minutes.
6. In EDMRN: Settings > Crew... > Create group. The relay address is prefilled. The connection
   shows "Connected".

## Operating

- Live logs: `npx wrangler tail`. Each past day appears once as a `crew-daily-total` line.
- Usage: dashboard > Workers & Pages > `edmrn-crew-relay` > Metrics, and the Durable Objects
  usage (rows written) in the account's billing usage view.
- Update: change the code, `npm test`, `npx wrangler deploy`. Groups keep their data.
- Another leader: add the key to `CREW_LEADERS` and deploy.
- Every member holds a full replica, so an emptied relay is re-seeded by the leader's next
  connection (spec S7).
