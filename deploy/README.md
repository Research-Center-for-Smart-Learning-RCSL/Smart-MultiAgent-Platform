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
cp .env.example .env          # edit: set real SMTP, domain, etc.
```

Key `.env` variables:

| Variable | Purpose |
|---|---|
| `SMAP_DOMAIN` | Public hostname (used in HSTS, CSP, CORS) |
| `SMAP_DB_DSN` | Postgres async DSN (default: compose-internal) |
| `SMAP_REDIS_DSN` | Redis DSN (default: compose-internal) |
| `VAULT_ADDR` | Vault address (default: `http://vault:8200`) |
| `SMAP_SMTP_*` | SMTP relay for email verification |

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

This runs `python -m smap.bootstrap all`, which is idempotent:

1. Loads Vault policies (`smap-backend`, `smap-rotation`)
2. Creates AppRoles and provisions `secret_id` files
3. Creates Postgres extensions (`pgvector`, `pg_cron`) via superuser init SQL
4. Creates MinIO buckets (`chat-uploads`, `rag-sources`, `exports`) with lifecycle rules
5. Creates Neo4j constraints and indexes
6. Initializes Qdrant collections
7. Runs `alembic upgrade head` (31 migration files, 0000–0030, phases A–M)
8. Creates the first admin account (prints credentials once — save them)

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
| backend-web | frontend_net, backend_net | 2 |
| backend-worker | backend_net | 2 |
| postgres | backend_net | 1 |
| redis | backend_net | 1 |
| qdrant | backend_net | 1 |
| neo4j | backend_net | 1 |
| minio | backend_net | 1 |
| vault | backend_net | 1 |
| egress-proxy | backend_net, egress_net | 1 |
| mcp-sandbox-supervisor | backend_net | 1 |

### Networks

- **smap_frontend_net:** nginx ↔ backend-web ↔ frontend
- **smap_backend_net:** backend ↔ all data stores + Vault
- **smap_egress_net:** MCP sandbox containers ↔ egress-proxy only. Declared
  `internal: true` (SEC-C1) — it has **no** gateway, so a sandbox attached
  here cannot reach the data plane, cloud metadata, or the public internet
  except through the egress-proxy, which alone straddles this network and the
  outbound `backend_net`. Do not give this network a gateway or attach a
  sandbox to a second network; that isolation is the egress chokepoint.

---

## 7. Smoke test

```bash
# 1. Health check
curl -sk https://localhost:10443/healthz   # → {"status": "ok"}
curl -sk https://localhost:10443/readyz    # → {"status": "ok", "dependencies": {...}}

# 2. Admin login (use credentials from step 5)
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
MinIO (all 3 buckets), Neo4j (database dump), Redis (dump.rdb).

**What is NOT backed up:** Vault unseal keys (operator responsibility — store
offline per `deploy/vault/README.md` §2), Qdrant (re-indexed from RAG sources).

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
