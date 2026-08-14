# SMAP Deployment — Operator Walk-through

This guide walks through a full bring-up of the SMAP stack on a single host
(16-core / 32 GB target). Budget **< 60 minutes** from zero to smoke-test.

**Prerequisites:** Docker Engine 25+, `docker compose` v2, `vault` CLI,
operator access to unseal keys (or first-time init).

---

## 0. Preflight check

Run the preflight script **before** starting any services. It validates
Docker version, required env vars, TLS certs, host resources, and port
availability in one pass:

```bash
bash deploy/scripts/preflight.sh --staging   # or --prod
```

Fix all FATAL items before proceeding. Warnings are non-blocking but
should be reviewed.

---

## 1. Clone and configure

```bash
git clone <repo-url> && cd smap
cp .env.example .env          # edit: set real SMTP host, CORS origin, etc.
```

Key `.env` variables:

| Variable | Purpose |
|---|---|
| `SMAP_SEC_CORS_ORIGINS` | Allowed CORS origins as a JSON list; prod refuses to start if empty |
| `SMAP_DB_DSN` | Postgres async DSN (default: compose-internal) |
| `SMAP_REDIS_DSN` | Redis DSN (default: compose-internal) |
| `SMAP_VAULT_ADDR` | Vault address (default: `http://vault:8200`) |
| `SMTP_HOST` / `SMTP_*` | SMTP relay for email verification (credentials go in Vault KV, not here) |

---

## 2. TLS certificates

**Dev (self-signed):** The compose stack auto-generates a self-signed cert on
first boot. No action needed.

**Production — nginx (external):** Mount real certificates into the `nginx_certs` volume:

```bash
docker volume create smap_nginx_certs
# Copy your cert/key into the volume:
docker run --rm -v smap_nginx_certs:/certs -v /path/to/certs:/src:ro alpine \
  sh -c "cp /src/smap.crt /src/smap.key /certs/ && chmod 600 /certs/smap.key"
```

See `deploy/compose/nginx/README.md` for details.

**Production — Vault internal TLS:** The prod overlay runs Vault on HTTPS.
Generate the internal CA + cert pair **before** starting the prod stack:

```bash
cd deploy/vault
bash gen-internal-tls.sh          # writes to deploy/vault/certs/
ls certs/                         # vault-internal-ca.pem, vault-internal.crt, vault-internal.key
```

The prod overlay bind-mounts `deploy/vault/certs/` into both Vault and the
backend containers. The CA PEM is set via `SMAP_VAULT_CA_CERT` so the backend
trusts the self-signed Vault cert. These files are gitignored — regenerate
them on each host.

---

## 3. Start infrastructure services

Bring up data stores and Vault first — backend depends on them.

```bash
cd deploy/compose

# Start infra only
docker compose up -d postgres redis qdrant neo4j minio vault

# Wait for health checks
docker compose ps   # all should show "healthy" or "running"
```

---

## 4. Vault bootstrap

### First-time init (production)

Follow `deploy/vault/README.md` §2 in full. Summary:

```bash
# 1. Initialize Shamir 3-of-5
docker compose exec vault vault operator init \
  -key-shares=5 -key-threshold=3 > /safe/path/vault-init.txt

# 2. Unseal (requires 3 key-shares)
docker compose exec vault vault operator unseal <key1>
docker compose exec vault vault operator unseal <key2>
docker compose exec vault vault operator unseal <key3>

# 3. Enable engines + create keys + load policies + create AppRoles
#    (See vault/README.md §2 steps 3–10)

# 4. Revoke root token
docker compose exec vault vault token revoke <root-token>
```

### Dev mode (already active in default compose)

Vault starts in `-dev` mode with root token `root`. No init/unseal needed.

---

## 5. Bootstrap database and services

From the repo root:

```bash
make bootstrap
```

This runs `python -m smap.bootstrap all`, which is idempotent — replaying it
against an already-bootstrapped stack only prints `already-present` entries:

1. `vault-init` — enables the `transit` and `secret` (KV v2) engines, creates
   the three Transit keys (`smap-provider-secret`, `smap-guest-link`,
   `smap-jwt-sign`), writes the `smap-backend` / `smap-rotation` policies, and
   seeds placeholder KV config (`captcha`, `smtp`, `hmac-key`, `minio`)
2. `vault-approle` — creates the `smap-backend` / `smap-rotation` AppRoles and
   prints their `role_id` / `secret_id` once (capture and add to `.env`)
3. `db-init` — creates Postgres extensions (`pgcrypto`, `uuid-ossp`, `vector`,
   `pg_cron`) and runs `alembic upgrade head` (idempotent; see
   `backend/alembic/versions/` for the current migration count)
4. `minio-init` — creates the 7 buckets (`chat-uploads`, `rag-sources`,
   `knowmap-sources`, `exports`, `agent-workspace`, `prompt-assistant-files`,
   `skill-bundles`) with lifecycle rules on `chat-uploads`/`exports`, plus a
   scoped MinIO service account whose credentials land in Vault KV
5. `qdrant-init` — initializes Qdrant collections
6. `neo4j-init` — creates Neo4j constraints and indexes

`bootstrap all` does **not** create an admin account unless you pass
`--admin-email`. Create the first admin explicitly once the stack is healthy:

```bash
cd backend && python -m smap.bootstrap create-admin --email <admin-email>
# Prints the generated password exactly once — save it before it scrolls away.
```

---

## 5a. Build the gVisor sandbox images (K.5)

The MCP / `code_exec` / `file` tools run inside two gVisor-isolated images that
are **not** pulled from a registry — you build them locally:

```bash
cd deploy/compose
docker compose --profile sandbox-build build
# → smap/mcp-runtime:pinned   (stdio + URL MCP servers + the in-image driver)
# → smap/code-exec:pinned      (curated scientific Python)
```

Requirements and notes:

- **gVisor (`runsc`) must be installed and registered as a Docker runtime** on
  the host — `docker_runsc.py` asserts the container actually landed on `runsc`
  and refuses to run untrusted workloads on `runc`. Install:
  https://gvisor.dev/docs/user_guide/install/
- **Baked-in MCP servers.** The sandbox network is gateway-less, so a stdio MCP
  server cannot install itself from npm/PyPI at run time — add the servers your
  agents bind to `deploy/sandbox/mcp-runtime/Dockerfile` (the image ships
  `@modelcontextprotocol/server-everything` for the smoke test). URL-source MCP
  servers are reached through the egress proxy and need no baking.
- **Pin by digest in production.** After building (or after the CI job records
  them), set the digests so a rebuilt-but-unreviewed image can't slip in:

  ```bash
  SANDBOX_MCP_IMAGE=smap/mcp-runtime@sha256:<digest>
  SANDBOX_CODE_EXEC_IMAGE=smap/code-exec@sha256:<digest>
  ```

  The backend reads these (defaulting to the `:pinned` tags for dev).
- **Egress.** The proxy is a custom HMAC forwarder, not a transparent
  `HTTP_PROXY`. The host pre-signs the per-project HMAC and passes it into the
  sandbox, so the shared secret never enters the container; `code_exec` has no
  raw outbound route (safe default), and allowlisted egress is exposed only via
  `web_search` / URL MCP.

---

## 6. Start the full stack

```bash
# Dev (with hot reload, exposed ports):
docker compose up -d

# Staging (single replica, 16 GB host, real Vault TLS):
docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d

# Production (with resource limits, 3 replicas, tuned Postgres):
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### Service topology (§25)

| Service | Network | Replicas (prod) |
|---|---|---|
| nginx | frontend_net, backend_net | 1 |
| frontend | frontend_net | 1 |
| backend-web | frontend_net, backend_net, data_net | 3 |
| backend-worker | backend_net, data_net | 3 |
| knowledge-scan-worker | backend_net, data_net | 1 |
| knowledge-ingest-worker | backend_net, data_net | 1 |
| postgres | data_net | 1 |
| redis | data_net | 1 |
| qdrant | data_net | 1 |
| neo4j | data_net | 1 |
| minio | data_net | 1 |
| bge-reranker | data_net | 1 |
| vault | backend_net | 1 |
| egress-proxy | backend_net, egress_net, data_net | 1 |
| mcp-sandbox-supervisor | backend_net | 1 |
| docker-socket-proxy | backend_net | 1 (prod/staging overlay only) |
| clamav | backend_net | 1 (optional — `--profile scanning`) |

### Networks

- **smap_frontend_net:** nginx ↔ backend-web ↔ frontend
- **smap_backend_net:** backend-web/backend-worker ↔ vault, egress-proxy,
  mcp-sandbox-supervisor, docker-socket-proxy (prod/staging), clamav (optional)
- **smap_data_net:** backend-web/backend-worker/knowledge-\*-worker/egress-proxy
  ↔ postgres, redis, qdrant, neo4j, minio, bge-reranker. Declared
  `internal: true` — it has no gateway, so a compromised data-plane service has
  no direct route to the internet even without the egress-proxy chokepoint.
  The data stores are **not** on `smap_backend_net`; only the backend
  processes bridge both networks.
- **smap_egress_net:** MCP sandbox containers ↔ egress-proxy only. Declared
  `internal: true` (SEC-C1) — it has **no** gateway, so a sandbox attached
  here cannot reach the data plane, cloud metadata, or the public internet
  except through the egress-proxy, which alone straddles this network and the
  outbound `backend_net`. Do not give this network a gateway or attach a
  sandbox to a second network; that isolation is the egress chokepoint.

---

## 6a. Updating a running stack

Do **not** update with a bare `docker compose up -d --build`. Compose restarts a
service's dependents when it recreates one, so that command takes the edge nginx
down for the length of the rebuild. A front proxy that caches static assets can
latch onto the 502s served during that window and keep serving them long after
the stack is healthy again — see the last two Troubleshooting rows.

Build first, then swap only the services you changed, leaving the edge alone:

```bash
COMPOSE="docker compose --env-file .env \
  -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.staging.yml"      # or docker-compose.prod.yml

APP="frontend backend-web backend-worker knowledge-scan-worker \
     knowledge-ingest-worker egress-proxy mcp-sandbox-supervisor"

git pull

# 1. Build while the stack keeps serving — nothing is recreated yet.
$COMPOSE build $APP

# 2. Apply migrations before the new backend code starts.
#    Staging only: the `migrate` one-shot service exists in that overlay.
#    On prod, run `alembic upgrade head` per §5 instead.
$COMPOSE run --rm migrate

# 3. Swap the containers. --no-deps is what keeps nginx untouched.
$COMPOSE up -d --no-deps $APP

$COMPOSE ps
```

`--no-deps` is safe because nginx resolves `frontend` / `backend-web` through
Docker DNS per request instead of caching the IPs from startup — a recreated
container is picked up within the resolver's 10 s TTL and the edge never
restarts. That is the whole reason `deploy/compose/nginx/conf.d/smap.conf` uses
variable `proxy_pass` targets rather than `upstream {}` blocks; reverting that
also reverts the safety of `--no-deps`.

Restart nginx only when you changed `deploy/compose/nginx/**`, and prefer a
graceful reload — it re-reads the config without dropping in-flight requests:

```bash
$COMPOSE exec nginx nginx -t && $COMPOSE exec nginx nginx -s reload
```

---

## 7. Smoke test

```bash
# 1. Health check
curl -sk https://localhost:10443/healthz   # → {"status": "ok"}
curl -sk https://localhost:10443/readyz    # → {"status": "ok", "dependencies": {...}}

# 2. Admin login (use the credentials printed by `create-admin` in step 5)
curl -sk https://localhost:10443/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "<admin-email>", "password": "<admin-password>"}'
# → {"access_token": "...", ...}

# 3. Open browser
#    https://localhost:10443 (accept self-signed cert warning in dev)
```

---

## 8. Vault verification (run after every deployment)

Execute the 7-point checklist from `deploy/vault/README.md` §7:

```bash
VAULT="docker compose exec vault vault"

# 1. Vault is unsealed
$VAULT status | grep -E "Sealed.*false"

# 2. Backend policy matches
$VAULT policy read smap-backend
diff <($VAULT policy read smap-backend) ../vault/policies/smap-backend.hcl

# 3. Rotation policy matches
diff <($VAULT policy read smap-rotation) ../vault/policies/smap-rotation.hcl

# 4. Transit keys are non-exportable, non-deletable
$VAULT read transit/keys/smap-provider-secret | grep -E "deletion_allowed.*false"
$VAULT read transit/keys/smap-provider-secret | grep -E "exportable.*false"

# 5. Backend log shows successful Vault auth
docker compose logs backend-web | grep "vault: authenticated"

# 6. Synthetic encrypt/decrypt round-trip
#    (Upload a test key on a throwaway project, verify outbound call succeeds)

# 7. Backend token is renewable with decreasing TTL
$VAULT token lookup | grep -E "renewable.*true"
```

---

## 9. Observability (optional)

```bash
docker compose -f docker-compose.yml \
  -f ../observability/docker-compose.obs.yml up -d
```

Provides: Prometheus (`:9090`), Grafana (`:3000`), Loki, Tempo, OTel Collector.
See `deploy/observability/` for configuration.

---

## 10. E2E test stack

For running Playwright E2E tests against a full stack:

```bash
# From deploy/compose/:
docker compose -f docker-compose.yml -f compose.test.yml up -d

# Wait for all services to be healthy, then from frontend/:
pnpm run test:e2e
```

The test compose uses Vault dev mode, a separate `smap_test` database, and
seeds fixture data on startup. See `compose.test.yml` for details.

---

## 11. Backup and restore

Scripts under `deploy/scripts/`:

```bash
# Create a timestamped backup of all stateful services:
bash deploy/scripts/backup.sh                    # → ./backups/2026-06-20_143000/
bash deploy/scripts/backup.sh /mnt/backups/smap  # custom path

# Restore from a backup (DESTRUCTIVE — replaces all data):
bash deploy/scripts/restore.sh ./backups/2026-06-20_143000/
```

**What is backed up:** Postgres (pg_dump), Vault (raft snapshot or file copy),
3 of MinIO's 7 buckets — `chat-uploads`, `rag-sources`, `exports` (mirrored by
`backup.sh`) — Neo4j (database dump), Redis (dump.rdb).

**What is NOT backed up:** the other 4 MinIO buckets —
`knowmap-sources`, `agent-workspace`, `prompt-assistant-files`,
`skill-bundles` — are not yet mirrored by `backup.sh`; back them up manually
(`mc mirror`) until the script covers them. Also not backed up: Vault unseal
keys (operator responsibility — store offline per `deploy/vault/README.md`
§2), Qdrant (re-indexed from RAG sources).

Schedule backups via cron on the host — daily at minimum, hourly for
production. Test restores quarterly.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/readyz` returns 503 | A dependency is down | Check `docker compose ps` for unhealthy services |
| Vault sealed after restart | Expected — Vault seals on process restart | Re-unseal with 3-of-5 keys (see §8 runbook in ops.md) |
| Backend 401 on all requests | Vault token expired / Vault sealed | Check Vault status; restart backend after unseal |
| Frontend shows blank page | Build failed or nginx misconfigured | Check `docker compose logs frontend nginx` |
| WebSocket 502 | Nginx upgrade header missing | Verify `/ws/` location in `nginx/conf.d/smap.conf` |
| `/assets/*.js` and `*.css` return 502 but `/` and `/healthz` return 200 | A front proxy is serving cached 502s for the asset URLs. Confirm by re-requesting with a query string (`?cb=1`) — a 200 there proves the origin is fine and the cache key is poisoned | Purge the front proxy's cache, then stop it caching assets at all. On Nginx Proxy Manager: `docker exec <npm> sh -c 'rm -rf /var/lib/nginx/cache/public/*' && docker exec <npm> nginx -s reload`, then untick **Cache Assets** on the proxy host. Its `assets.conf` combines `proxy_cache_valid any 30m` (stores 502s) with `proxy_cache_use_stale ... http_502` (keeps serving them), and `access_log off` hides the requests. The upstream already sends `immutable, max-age=31536000`, so that cache layer buys nothing |
| Everything 502s right after a deploy and stays that way | The edge was restarted while a dependent was recreated, or an `upstream {}` block is holding a dead container IP | Deploy per §6a (`build` + `up -d --no-deps`). If the edge config was reverted to `upstream {}` blocks, nginx resolves hostnames only at startup and needs `docker compose restart nginx` after every recreation |
