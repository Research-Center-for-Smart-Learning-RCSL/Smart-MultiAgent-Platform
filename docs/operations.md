# SMAP Operations Manual

Companion to `REQUIREMENTS.md`. This document covers what operators and SREs need to run SMAP day-to-day — log handling, health checks, resource sizing, migrations, bootstrap, and the error-code catalog — topics that the SRS references but deliberately does not prescribe.

Unless otherwise stated, every path is relative to the repo root and every config file is mounted read-only into the relevant container.

---

## 1. Operational logging

(Separate from the product **audit log**, which is a user-visible feature defined in `REQUIREMENTS.md` §17. Operational logs are for the SRE; they MUST NOT contain end-user content or secrets.)

### 1.1 Format

- **[O1.01]** All services (backend-web, backend-worker, egress-proxy, mcp-sandbox-supervisor) emit **structured JSON** to stdout, one record per line.
- **[O1.02]** Required fields per record:
  ```
  {
    "ts": "RFC 3339 UTC",
    "level": "debug|info|warn|error|fatal",
    "service": "backend-web|...",
    "request_id": "uuid",           // propagated via X-Request-ID
    "session_id": "uuid | null",
    "user_id": "uuid | null",
    "route": "/api/...",            // when applicable
    "latency_ms": int,              // when applicable
    "event": "short machine-readable identifier",
    "msg": "human-readable message",
    "error": {...} | null           // Python exception info
  }
  ```
- **[O1.03]** Default level is `info`. The `SMAP_LOG_LEVEL` env var overrides at startup. Dynamic level change via signal is not in v1.
- **[O1.04]** Backend uses `loguru` with a JSON sink. Workers inherit the same configuration.

### 1.2 Redaction

- **[O1.05]** A logging filter redacts the same shapes as audit (REQUIREMENTS §17 R17.03): JSON keys matching `^(authorization|api[_-]?key|secret|password|token|bearer|private[_-]?key|cookie|session)$`, plus known secret-shape strings (`sk-ant-…`, `sk-…` ≥ 40 chars, PEM headers). The filter runs before any sink.
- **[O1.06]** Operators MUST NOT disable the redaction filter in production.

### 1.3 Collection and rotation

- **[O1.07]** Since all services log to stdout, rotation is Docker's job. The compose file sets on every service:
  ```yaml
  logging:
    driver: json-file
    options:
      max-size: "50m"
      max-file: "5"
  ```
  This caps per-container on-disk log at ~250 MB.
- **[O1.08]** Operators MAY replace the driver with `journald`, `syslog`, or `fluentd` by setting `SMAP_LOG_DRIVER=...` in `.env`. The compose file reads this via `logging.driver: ${SMAP_LOG_DRIVER:-json-file}`.
- **[O1.09]** Centralized shipping is optional and out of scope for v1; the recommended path is **Promtail → Loki → Grafana** for hosts that want it. A sample `promtail.yaml` is provided in `deploy/observability/` for operators who opt in.

---

## 2. Health checks and readiness

Every long-running service MUST expose both a **liveness** (`/healthz`) and a **readiness** (`/readyz`) HTTP endpoint. Liveness answers "am I alive at all"; readiness answers "am I ready to receive traffic".

### 2.1 Per-service contract

| Service | `/healthz` | `/readyz` |
|---|---|---|
| `backend-web` | Process responds, event loop not stuck | Postgres reachable, Redis reachable, Vault sealed=false+token valid, Qdrant ping OK, Neo4j bolt ping OK, MinIO HeadBucket OK |
| `backend-worker` | Arq heartbeat in last 10 s | Same dependencies as backend-web |
| `egress-proxy` | Process responds | Nothing (stateless) |
| `mcp-sandbox-supervisor` | Process responds | Docker socket reachable, gVisor runtime discoverable. Note: this service is a runtime *probe*, not a lifecycle supervisor — it only verifies that `runsc` is registered. Sandbox container lifecycle is owned by the backend MCP context. |
| `postgres` | Docker `pg_isready -U smap` | Same |
| `redis` | Docker `redis-cli ping` | Same |
| `qdrant` | Docker TCP probe `exec 3<>/dev/tcp/127.0.0.1/6333` | Same |
| `neo4j` | Docker `cypher-shell 'RETURN 1'` (bolt connection) | Same |
| `minio` | Docker HTTP `GET /minio/health/live` | `/minio/health/cluster` |
| `vault` | Docker HTTP `GET /v1/sys/health?standbyok=true` | Same (requires unsealed) |
| `nginx` | Docker `wget -qO- http://127.0.0.1/healthz` | Same |

### 2.2 docker-compose healthcheck snippet (backend-web)

```yaml
healthcheck:
  test: ["CMD", "python", "-c",
    "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5).status==200 else 1)"]
  interval: 10s
  timeout: 5s
  retries: 12
  start_period: 30s
```

The prod image has no `curl` — use Python `urllib` instead. Every service in the compose file has its own healthcheck block sized for its startup cost. Neo4j gets 15 s interval × 10 retries; Vault gets 5 s interval × 10 retries + 10 s start_period; Postgres gets 5 s interval × 10 retries; others 10–30 s start_period.

### 2.3 Shallow vs deep checks

- **[O2.01]** `/healthz` MUST be cheap (no dependency calls) — it's used as the liveness probe and is called every few seconds.
- **[O2.02]** `/readyz` performs dependency checks and is the one Nginx and load-balancers consult. Its response caches for 2 s per backend process so stampedes don't DoS dependencies.

---

## 3. Per-service resource limits (16-core / 64 GB baseline)

The REQUIREMENTS NFR §20.03 gives rough memory budgets; `docker-compose.prod.yml` encodes them as hard caps:

| Service | CPU (limit) | Memory (limit) | Memory (reservation) | Replicas | Notes |
|---|---|---|---|---|---|
| `nginx`              | 1.0  | 512 MB  | 256 MB  | 1 | TLS termination |
| `backend-web`        | 4.0  | 4 GB    | 2 GB    | 3 (rolling) | 4 uvicorn workers each |
| `backend-worker`     | 3.0  | 2 GB    | 1 GB    | 3 | Arq task workers |
| `frontend`           | 0.5  | 256 MB  | 128 MB  | 1 | Nginx serving SPA |
| `postgres`           | 6.0  | 8 GB    | 4 GB    | 1 | shm_size=2g, max_conn=512 |
| `redis`              | 1.0  | 4 GB    | 1 GB    | 1 | maxmemory=3500mb, allkeys-lru |
| `qdrant`             | 4.0  | 8 GB    | 4 GB    | 1 | mmap-based; more RAM → page cache |
| `neo4j`              | 4.0  | 8 GB    | 4 GB    | 1 | shm_size=2g, heap=5G, pagecache=2G |
| `minio`              | 2.0  | 5 GB    | 2 GB    | 1 | |
| `vault`              | 1.0  | 2 GB    | 512 MB  | 1 | file storage; TLS internal |
| `egress-proxy`       | 1.0  | 512 MB  | 256 MB  | 1 | |
| `mcp-sandbox-supervisor` | 0.25  | 128 MB  | 64 MB  | 1 | Health-check probe only |
| `docker-socket-proxy` | 0.25  | 128 MB  | 64 MB  | 1 | Prod only; SEC-C1 isolation |

Tuning target: ≥16-core / 64 GB single host. Total hard-limit sum ≈ 56 GB; remaining ~8 GB reserved for OS + Docker daemon + transient sandbox containers.

Operators running on smaller hosts (32 GB) should halve every memory value and drop replicas to 1, but below 8-core / 16 GB the R20.01 p95 target is not guaranteed.

---

## 4. Database migrations (Alembic)

### 4.1 Repository layout

```
backend/
  migrations/
    env.py
    script.py.mako
    versions/
      0001_identity.py
      0002_tenancy.py
      …
```

### 4.2 Policy

- **[O4.01]** One migration = one logical change set. No squash-and-rewrite across releases.
- **[O4.02]** Every migration MUST have `upgrade()` and `downgrade()`. Downgrades that are intentionally lossy raise `RuntimeError("irreversible migration")` with a comment explaining why.
- **[O4.03]** Zero-downtime policy — all migrations MUST be compatible with at least **N-1 application versions** running concurrently during rolling deploys:
  - Add columns as NULL-able first; backfill; then add NOT NULL and defaults in a second migration.
  - Drop columns only after the application stops reading them (two-release cycle).
  - Never rename a column in a single migration — add new, copy, cut-over, drop old.
- **[O4.04]** Index creation uses `CREATE INDEX CONCURRENTLY` wherever possible; Alembic offers `op.create_index(..., postgresql_concurrently=True)` in non-transactional mode. Migrations that use this MUST be marked `transactional_ddl = False`.
- **[O4.05]** Autogeneration (`alembic revision --autogenerate`) is a convenience; every generated migration is reviewed by hand before commit.

### 4.3 Operational flow

```bash
# Generate (dev only)
python -m alembic revision --autogenerate -m "add search_keys"

# Review the diff; edit if needed

# Apply
python -m alembic upgrade head

# Rollback (during an incident)
python -m alembic downgrade -1

# Inside Docker
docker compose run --rm backend-web python -m alembic upgrade head
```

### 4.4 Production rollout order

1. Deploy application code release **N** with migrations **forward-only-compatible** with DB schema **S**.
2. Run `alembic upgrade head` → DB now at schema **S+1**.
3. Roll application from N to N+1 (which can also read S or S+1).
4. Only *after* all replicas are on N+1, run the follow-up migration to schema **S+2** (drop-column-style changes).

---

## 5. Bootstrap procedure (first-time deployment)

A helper CLI `python -m smap.bootstrap` orchestrates all one-time setup. Each step is idempotent.

```bash
# 0. Bring up infrastructure only (no backend yet)
docker compose up -d postgres redis qdrant neo4j minio vault

# 1. Initialize Vault (interactive; writes unseal keys to stdout)
python -m smap.bootstrap vault-init

# 2. Apply Vault policies (from deploy/vault/policies/*.hcl)
python -m smap.bootstrap vault-policies --root-token "<root>"

# 3. Create AppRole role_ids / secret_ids
python -m smap.bootstrap vault-approle --root-token "<root>"
#   → writes secret_id for smap-backend to /run/secrets/smap-backend-secret-id
#   → revokes root token at end

# 4. Create MinIO buckets with lifecycle
python -m smap.bootstrap minio-init

# 5. Create Qdrant collections (rag_{dummy}, graphrag_{dummy} templates)
python -m smap.bootstrap qdrant-init

# 6. Create Postgres extensions, run migrations to head
python -m smap.bootstrap db-init
python -m alembic upgrade head

# 7. Seed the first platform Admin
python -m smap.bootstrap create-admin --email admin@example.com

# 8. Start the application
docker compose up -d backend-web backend-worker nginx frontend
```

### 5.1 The `create-admin` command

- **[O5.01]** Creates a `users` row with `status = 'active'`, `email_verified = true`, a random 24-char password (printed once to stdout), and an `admins` marker row.
- **[O5.02]** The operator MUST log in, change this password, and enable MFA (when v2 adds it) before any other user signs up.
- **[O5.03]** Refuses to run if any `admins` row already exists unless `--force` is provided.

### 5.2 The last-admin safeguard

- **[O5.04]** Demoting the last remaining platform Admin via the REST API is rejected (see REQUIREMENTS §22.13). The bootstrap CLI has a `create-admin --rescue` flag that creates an emergency Admin if and only if zero active Admins exist.

---

## 6. RFC 7807 error catalog

All errors from the API use `application/problem+json` with these common fields: `type` (URI), `title`, `status`, `detail`, `instance` (request id). Operators and client authors should refer to this catalog.

### 6.1 Error types (namespace `https://smap.local/problems/`)

| Code (URI suffix) | HTTP | When | Client action |
|---|---|---|---|
| `auth/no-client-ip` | 400 | Request has no peer IP (TrustedProxyMiddleware can't resolve `actor_ip`) | Fix the reverse-proxy chain |
| `auth/invalid-credentials` | 401 | Login failed | Show form error |
| `auth/account-locked` | 423 | 5 bad attempts in 15 min | Wait 15 min |
| `auth/not-verified` | 403 | Email not verified | Resend verification |
| `auth/banned` | 403 | Account or IP banned | Surface reason |
| `auth/token-expired` | 401 | Access token expired | Call refresh |
| `auth/token-revoked` | 401 | jti on denylist | Re-login |
| `auth/captcha-failed` | 400 | Register w/o valid CAPTCHA | Retry |
| `ip-banned` | 403 | Caller IP matches an entry in `ip_bans` (earliest middleware short-circuit) | Surface reason; appeal via operator |
| `permission/forbidden` | 403 | Authz denied | Hide control |
| `permission/last-admin` | 409 | Demoting last admin (org-scope) | Refuse |
| `admin/last-admin` | 409 | Demoting last platform admin (admin-API path) | Refuse |
| `validation/schema` | 422 | Request body schema error | Show field errors |
| `validation/version-mismatch` | 409 | Optimistic lock failed | Refresh, retry |
| `validation/org-has-other-members` | 409 | OC self-delete blocked | Transfer first |
| `resource/not-found` | 404 | Unknown resource | — |
| `resource/conflict` | 409 | Duplicate name / pending transfer | Choose different name |
| `resource/gone` | 410 | Hard-deleted, past 60 d | — |
| `key/test-failed` | 422 | Live provider test call failed at upload | Show provider error |
| `key/group-exhausted` | 503 | All keys in group exhausted | Wait or add keys |
| `key/capability-mismatch` | 422 | Using a non-llm key in a Key Group, etc. | Choose compatible key |
| `search/tool-unavailable` | 503 | No active search key | Configure one |
| `search/rate-limited` | 429 | Per-project cap | Slow down |
| `mcp/egress-blocked` | 502 | Host not in allowlist | Add to allowlist |
| `mcp/tool-unavailable` | 503 | Tool not attached or not permitted | Check config |
| `workflow/invalid-definition` | 422 | Schema or linter failure | Show lint results |
| `workflow/loop-detected` | 409 | Instruct cycle | — |
| `workflow/chain-too-deep` | 409 | Instruct depth exceeded | — |
| `workflow/run-cancelled` | 409 | Canceling a finished run | — |
| `chat/message-immutable` | 409 | Edit beyond 5 min | Only Owner/Admin |
| `chat/attachment-too-large` | 413 | > 32 MB single-shot | Use tus |
| `chat/attachment-expired` | 410 | Past 3 days | — |
| `rag/unsupported-format` | 415 | File not pdf/docx/md/txt | — |
| `rag/ingest-failed` | 500 | Parser error | See detail |
| `rate-limited` | 429 | Global / endpoint rate cap | Retry-After |
| `internal/error` | 500 | Unhandled | Retry later |
| `dependency-unavailable` | 503 | Postgres/Redis/Qdrant/Neo4j/MinIO/Vault down (see `/readyz`) | Retry |

### 6.2 Common response shape

```json
{
  "type":   "https://smap.local/problems/key/test-failed",
  "title":  "Provider key rejected the test call",
  "status": 422,
  "detail": "Anthropic returned 401: invalid x-api-key",
  "instance": "smap:req:01HFE8Z...",
  "provider": "claude",
  "masked_preview": "sk-ant-...xE9a"
}
```

Any extra fields beyond the core RFC 7807 set are documented per-type in this catalog.

---

## 7. CORS, rate-limit headers, and CSP

- **CORS**: see REQUIREMENTS §19a.3. Same-origin deploy → nothing special. Operators splitting origins must set `SMAP_SEC_CORS_ORIGINS` in `.env` (comma-separated list of allowed origins, e.g. `https://app.example.com,https://admin.example.com`) and understand the CSRF implications. The default when unset is same-origin only.
- **Rate-limit response headers**: see REQUIREMENTS §19 R19.06 and §19a — `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` on all 200/429 responses from rate-limited endpoints.
- **CSP**: see REQUIREMENTS §19a.2. Tune by editing `deploy/nginx/nginx.conf.d/csp.conf`.

---

## 7a. Email / SMTP (transactional mail)

Transactional mail covers email verification (R6.02), password reset (R6.05),
email-change re-verification (R6.06), and org/project invites (R6.09–R6.11).
Without a working SMTP transport **users cannot complete registration through the
UI** — the verification link is never delivered.

### 7a.1 Configuration

Non-secret connection parameters live in `.env` (read by `EmailSection`):

| Var | Default | Notes |
| --- | --- | --- |
| `SMTP_HOST` | *(empty)* | Empty ⇒ dev "log to stdout" sender; no mail is sent. |
| `SMTP_PORT` | `587` | `465` for `implicit` TLS. |
| `SMTP_FROM` | `SMAP <no-reply@localhost>` | Envelope/header From. |
| `SMTP_TLS_MODE` | `starttls` | `starttls` (587) \| `implicit` (465) \| `none` (in-cluster relay only). |
| `SMTP_TIMEOUT_S` | `15` | Per-send timeout. |

SMTP **credentials are never put in `.env`**. Store them in Vault KV at
`secret/smap/config/smtp`:

```bash
vault kv put secret/smap/config/smtp username="apikey" password="<smtp-password>"
```

If `SMTP_HOST` is set but the Vault secret is unreadable, the sender proceeds
**without authentication** (valid for an unauthenticated in-cluster relay / MailHog)
and logs `event=smtp_creds_missing`.

### 7a.2 Fail-open behaviour

The factory selects `SmtpEmailSender` only when `SMTP_HOST` is set; otherwise it
keeps the dev `LoggingEmailSender`. This is deliberate — a self-hosted operator
may run mail-less in a closed lab. **In `env=prod` with no `SMTP_HOST` the backend
still boots** but logs exactly one warning at startup (`event=smtp_unconfigured`):
registration, reset, and invites are silently undeliverable. Grep the boot logs
for that event after any prod deploy.

### 7a.3 Smoke test (staging)

1. `vault kv put secret/smap/config/smtp …` and set `SMTP_*` for the staging relay.
2. Register a throwaway address; confirm a "Verify your email" message arrives.
3. Click the link → account becomes `active` → log in succeeds.
4. Run a password-reset round-trip and one org-invite to an **unregistered** address
   (the invite link should route through sign-up, then auto-enroll).

CI exercises the same path headlessly against MailHog (`compose.test.yml` +
the `wiring` job's email round-trip).

### 7a.4 CAPTCHA config

The registration CAPTCHA (R19a.12) provider/secret/sitekey/mode live in Vault KV
`secret/smap/config/captcha`. The frontend reads only the **public** subset via
`GET /api/auth/captcha-config` (provider + sitekey + mode); the verify secret never
leaves the backend. `mode=off` (or an unreachable Vault) ⇒ no widget renders and the
backend bypasses verification. Login takes no CAPTCHA — it is register-only.

---

## 8. Runbooks (selected high-impact scenarios)

### 8.1 "Vault is sealed after host restart"

Symptom: `backend-web /readyz` returns 503 with `dependency = vault`.

Action:
1. `docker compose exec vault vault status` → `Sealed: true`.
2. Run `vault operator unseal` three times with three of the five unseal keys (quorum).
3. `/readyz` recovers within a minute (backend AppRole re-login happens automatically on next check).

### 8.2 "All keys in a Key Group are exhausted"

Symptom: Agents in a specific project cannot respond; users see `key/group-exhausted` toasts.

Action:
1. UI → Project → Keys → Usage dashboard: identify which keys are at quota.
2. Either wait for next hour bucket, raise the per-key limits, or add another key to the group.
3. No platform action required; the scheduler auto-resumes on refresh.

### 8.3 "GraphRAG build stuck in failed_compensating"

Symptom: Admin audit tail shows repeated Qdrant upsert failures for a specific `graphrag_config_id`.

Action:
1. Check Qdrant health: `docker compose exec qdrant curl -s localhost:6333/healthz`.
2. If Qdrant is healthy, inspect the config: `curl /api/admin/graphrag/{id}/status`.
3. If safe, reset: `POST /api/admin/graphrag/{id}/reset`. The next scheduled build runs from scratch; the previous batch's Neo4j rows remain but will be reconciled on next build (dedup by `build_id`).

### 8.4 "Disk filling up"

Common culprits and their cleanup targets:

| Culprit | Path | Action |
|---|---|---|
| Chat attachments | MinIO `chat-uploads` | Lifecycle rule should purge at 3 d; verify bucket lifecycle policy. |
| RAG sources | MinIO `rag-sources` | Only deleted when RAG document row is deleted; inspect `rag_documents` count. |
| Container logs | Host `/var/lib/docker/containers` | Should be bounded by O1.07; verify compose `logging` block was applied. |
| Postgres WAL | PG data volume | Check `archive_mode` off in dev; tune `wal_keep_size`. |
| Workflow runs | `workflow_runs` table | 90-day retention; verify nightly job is running. |
| Audit logs | `audit_logs` table | 365-day retention (REQUIREMENTS §17 R17.01); verify nightly job. |

---

## 8a. Backup and restore

Scripts in `deploy/scripts/` automate backup and restore for all five datastores.

### 8a.1 Backup

```bash
# Full backup (Postgres + Vault + MinIO + Neo4j + Redis)
bash deploy/scripts/backup.sh /path/to/backup/dir
```

Creates timestamped files: `pg_dump.sql.gz`, `vault-snapshot.snap`, MinIO mirror, `neo4j-dump.dump`, `redis-dump.rdb`.

Schedule via cron on the Docker host:

```
0 3 * * * /opt/smap/deploy/scripts/backup.sh /backups/smap >> /var/log/smap-backup.log 2>&1
```

### 8a.2 Restore

```bash
bash deploy/scripts/restore.sh /path/to/backup/dir
```

The restore script:
1. Stops application services (backend-web, backend-worker, nginx)
2. Restores each datastore (pg_restore, Vault snapshot restore, mc mirror, Neo4j load, Redis BGSAVE copy)
3. Restarts application services
4. Logs pg_restore stderr to a file for review

An abort window of 5 seconds is provided before destructive operations begin.

---

## 9. Observability (optional, structural only)

Operators who want full OTel telemetry:

- Backend and workers emit traces via OpenTelemetry SDK to `OTEL_EXPORTER_OTLP_ENDPOINT` if set.
- A sample OTel Collector + Tempo + Loki + Prometheus + Grafana bundle is available in `deploy/observability/`.
- The product's own audit log (REQUIREMENTS §17) is the source of truth for security/compliance questions; OTel telemetry is for performance.

---

## 10. File scanning (ClamAV)

SMAP supports optional AV scanning of uploaded files (R22.15.07). When
disabled (default), all attachments and RAG documents are auto-approved as
`clean`. Enable it by deploying ClamAV and setting three env vars:

```bash
# 1. Start ClamAV alongside the stack (uses Docker Compose profile)
docker compose --profile scanning up -d

# 2. Set env vars (backend-web + backend-worker)
SMAP_SEC_FILE_SCAN_ENABLED=true
SMAP_SEC_CLAMAV_HOST=clamav
SMAP_SEC_CLAMAV_PORT=3310       # default, can be omitted
```

**How it works:**

- Every file upload (chat attachment or RAG document) enqueues a scan task.
- The worker fetches the blob from MinIO, sends it to ClamAV via the clamd
  INSTREAM protocol (TCP 3310), and records the result.
- If a threat is detected: `scan_status` is set to `quarantined`, the parent
  resource status is set to `quarantined`, and an audit event is emitted
  (`attachment.quarantined` / `rag.document.quarantined`).
- Quarantined attachments return HTTP 403 on download. Quarantined RAG
  documents are excluded from retrieval.

**ClamAV resource usage:** The `clamav/clamav:1.4` image bundles both `clamd`
and `freshclam`. Signature updates run automatically. Expect ~1 GB RAM for
the signature database. The `clamav_db` volume persists signatures across
restarts so cold starts don't re-download the full database.

## 11. Version matrix (at v1.0 release)

| Component | Pinned version |
|---|---|
| Python | 3.12.x |
| FastAPI | ≥ 0.110 |
| SQLAlchemy | 2.x async |
| Alembic | ≥ 1.13 |
| Arq | ≥ 0.25 |
| Pydantic | 2.x |
| Loguru | ≥ 0.7 |
| Postgres | 16 |
| Redis | 7.2 |
| Qdrant | 1.12 |
| Neo4j | 5 Community |
| MinIO | RELEASE.2024-06 or later |
| Vault | 1.18 |
| Node | 20 LTS |
| Vue | 3.4 |
| Vite | 5 |
| Nginx | 1.27 |
| Docker Engine | 25+ |
| gVisor | latest release channel |

The exact digests are pinned in `docker-compose.yml` and `requirements.lock`.
