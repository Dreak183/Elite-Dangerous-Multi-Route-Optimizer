# Deploying the crew relay

## What it is

The relay is the Python asyncio service in `relay/` (ADR-0009). It listens on loopback, or inside
its container, and speaks plain WebSocket and HTTP; TLS ends at Caddy or at the server's existing
reverse proxy. The client's address is `wss://galroute.the-hub.cloud`: a host with an optional
explicit port, no path beyond `/`, and no credentials, query or fragment; `ws://` only for
localhost (`edmrn/ext/crew_roster.check_relay_url`). The proxy must therefore serve the relay at
the root of its port, pass WebSocket upgrades, and keep idle connections open longer than the
client's 30 s ping interval (`edmrn/ext/crew_sync.py`).

Configuration is by flag or by `EDMRN_RELAY_*` variable (HOST, PORT, DB, LEADERS, SOURCE_URL,
MEMBER_QUOTA, GROUP_QUOTA, MAX_CONNECTIONS); an empty variable counts as unset. Logs go to stdout. SIGTERM persists
the statuses, then closes every socket with 1001 and the store, which takes up to the relay's own
15 s close wait plus the close handshake. The supervisor's stop timeout must exceed that wait, or
SIGKILL lands before the closes are sent and the store is closed; `stop_grace_period` in
`relay/compose.yaml` and `TimeoutStopSec` in `relay/edmrn-relay.service` are therefore 30 s.

## DNS

Create an A record, plus an AAAA record for IPv6, `galroute` pointing at the server's IP in the
Cloudflare DNS of `the-hub.cloud`, with proxy status "DNS only". Check with
`nslookup galroute.the-hub.cloud`.

## Leader key

EDMRN > Settings > Crew... > copy "Your crew key". Several keys are separated by commas. The keys
are public.

## Option A: Docker Compose with Caddy

Needs Docker Engine with the compose plugin and ports 80 and 443 free. The image is built on the
server.

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

`uv` itself has to be installed on the server first: https://docs.astral.sh/uv/getting-started/installation/

```
useradd --system edmrn-relay
git clone <fork URL> /opt/edmrn
export UV_PYTHON_INSTALL_DIR=/opt/edmrn/python
uv venv --python 3.13 /opt/edmrn/.venv-relay
uv pip install --python /opt/edmrn/.venv-relay -r /opt/edmrn/relay/requirements.txt
chmod -R a+rX /opt/edmrn
```

Without a system Python 3.13, uv downloads the interpreter; `UV_PYTHON_INSTALL_DIR` keeps it
under `/opt/edmrn` because the default location in root's home is unreachable for the service
user (`ProtectHome=true` in the unit), which would fail the start with status 203/EXEC.

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

The `docker compose` commands run from `relay/`.

- **Logs:** `docker compose logs -f relay` or `journalctl -u edmrn-relay -f`. Each past day
  appears once as a `crew-daily-total` line.
- **Backup:** option A:
  `docker compose exec relay python -c "import sqlite3; sqlite3.connect('/data/relay.db').backup(sqlite3.connect('/data/backup.db'))"`,
  then `docker compose cp relay:/data/backup.db ./relay-backup-$(date +%F).db`. Option B (needs the `sqlite3` command-line tool):
  `sqlite3 /var/lib/edmrn-relay/relay.db ".backup /var/backups/relay-$(date +%F).db"`.
- **Update:** `git pull`, then `docker compose up -d --build` or `systemctl restart edmrn-relay`.
  Members reconnect by themselves.
- **Adding a leader:** extend `EDMRN_RELAY_LEADERS` and restart.
- **Limits:** `EDMRN_RELAY_MEMBER_QUOTA` and `EDMRN_RELAY_GROUP_QUOTA` (defaults 5,000 and 80,000
  per UTC day) are abuse limits. `EDMRN_RELAY_MAX_CONNECTIONS` (default 256) caps the WebSocket
  connections open at once; over it the relay answers 503 with `Retry-After`. Behind the proxy
  the relay sees only the proxy's address, so it has no per-address limit: limit connection rates
  per client address in the proxy if scanners become a problem.
- **Re-seed:** a lost database costs nothing permanent. Every member holds a full replica, so the
  next connections re-seed it (crew sync core spec S7).
