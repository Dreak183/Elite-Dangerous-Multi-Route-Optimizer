# Deploying the crew relay

## What it is

The relay is the Python asyncio service in `relay/` (ADR-0009). It listens on loopback, or inside
its container, and speaks plain WebSocket and HTTP; TLS ends at Caddy or at the server's existing
reverse proxy. The client's address is `wss://galroute.the-hub.cloud`: host only, port 443, root
path (`edmrn/ext/crew_roster.check_relay_url`). The proxy must therefore serve the relay at the
root of port 443, pass WebSocket upgrades, and keep idle connections open longer than the client's
30 s ping interval (`edmrn/ext/crew_sync.py`).

Configuration is by flag or by `EDMRN_RELAY_*` variable (HOST, PORT, DB, LEADERS, SOURCE_URL,
MEMBER_QUOTA, GROUP_QUOTA); an empty variable counts as unset. Logs go to stdout. SIGTERM persists
the statuses and closes every socket with 1001, which takes up to 15 s plus the close handshake;
the stop timeouts below are 30 s for that reason.

## DNS

Create an A record, plus an AAAA record for IPv6, `galroute` pointing at the server's IP in the
Cloudflare DNS of `the-hub.cloud`, with proxy status "DNS only". Check with
`nslookup galroute.the-hub.cloud`.

## Leader key

EDMRN > Settings > Crew... > copy "Your crew key". Several keys are separated by commas. The keys
are public.

## Option A: Docker Compose with Caddy

Needs Docker Engine with the compose plugin and ports 80 and 443 free. The image is built on the
server; it cannot be built on the Windows development machine, where Docker is not installed.

`relay/.env` (git-ignored):

```
RELAY_HOST=galroute.the-hub.cloud
EDMRN_RELAY_LEADERS=<key>
EDMRN_RELAY_SOURCE_URL=<fork URL or empty>
```

Start and check:

```
cd relay && docker compose up -d --build
curl https://galroute.the-hub.cloud/
```

The `curl` shows the greeting. Caddy obtains the certificate on the first request, which can take
a minute. The compose file builds from the repository root; the Dockerfile's `COPY` lines are the
list of `edmrn` files in the image, and `Dockerfile.dockerignore` admits exactly those.

## Option B: systemd behind an existing reverse proxy

```
useradd --system edmrn-relay
git clone <fork URL> /opt/edmrn
uv venv --python 3.13 /opt/edmrn/.venv-relay
uv pip install --python /opt/edmrn/.venv-relay -r /opt/edmrn/relay/requirements.txt
```

Write `/etc/edmrn-relay.env` with the `EDMRN_RELAY_*` variables (at least `EDMRN_RELAY_LEADERS`),
copy `relay/edmrn-relay.service` to `/etc/systemd/system/`, then:

```
systemctl daemon-reload
systemctl enable --now edmrn-relay
```

The nginx `server` block for `galroute.the-hub.cloud` (certificate from certbot):

```
location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;
}
```

120 s is above the client's 30 s ping interval; nginx's default of 60 s would also do, but the
timeout must never be lower than a few ping intervals.

## Operating

- **Logs:** `docker compose logs -f relay` or `journalctl -u edmrn-relay -f`. Each past day
  appears once as a `crew-daily-total` line.
- **Backup:** option A:
  `docker compose exec relay python -c "import sqlite3; sqlite3.connect('/data/relay.db').backup(sqlite3.connect('/data/backup.db'))"`,
  then `docker compose cp relay:/data/backup.db ./relay-backup-$(date +%F).db`. Option B:
  `sqlite3 /var/lib/edmrn-relay/relay.db ".backup /var/backups/relay-$(date +%F).db"`.
- **Update:** `git pull`, then `docker compose up -d --build` or `systemctl restart edmrn-relay`.
  Members reconnect by themselves.
- **Adding a leader:** extend `EDMRN_RELAY_LEADERS` and restart.
- **Limits:** `EDMRN_RELAY_MEMBER_QUOTA` and `EDMRN_RELAY_GROUP_QUOTA` (defaults 5,000 and 80,000
  per UTC day) are abuse limits.
- **Re-seed:** a lost database costs nothing permanent. Every member holds a full replica, so the
  next connections re-seed it (crew sync core spec S7).
