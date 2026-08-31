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
- **[O4.04]** Index creation on a high-write table uses `CREATE INDEX CONCURRENTLY`, issued inside `op.get_context().autocommit_block()`; there is no per-revision `transactional_ddl` marker in Alembic and none is used in this repository. A migration that opens an autocommit block **must place no statement before it in the same function**: the block unconditionally commits the transaction that precedes it, while the revision stamp is written only after the migration body returns, so any earlier statement can be committed at the previous stamped version and make the migration unretryable. `CONCURRENTLY` is not used where the same migration already takes `ACCESS EXCLUSIVE` on the same relation, since the weaker lock buys nothing there.
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

Two migrations carry a step this order does not describe, because their state does not live
in PostgreSQL alone:

- **0084 (email-domain policy)** stages a change of authority from Redis to PostgreSQL, so
  step 4 is a maintenance command rather than a migration, and a rollback needs a command
  run *before* the downgrade. Neither is optional. See §7a.6.

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
| `admin/email-domain-invalid` | 422 | A policy entry is not a bare domain | Show which entry |
| `admin/email-domain-policy-stale` | 409 | `If-Match` version no longer current | Reload, reapply |
| `admin/email-domain-policy-fenced` | 409 | Writes fenced outside the `active` rollout phase; carries `rollout_state` | §7a.6, not a reload |
| `admin/email-domain-policy-unavailable` | 503 | No policy authority reachable — fails closed, never `off` | Check Postgres |
| `admin/provisioning-rate-limited` | 429 | One Admin over 60 account creations / 10 min; carries `retry_after_seconds` | Wait |
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
Without a working SMTP transport **a self-registering user cannot complete
registration through the UI** — the verification link is never delivered. A
deployment that will never have mail should onboard through §7a.5 instead, which
hands every link over out of band.

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
for that event after any prod deploy. If that is intentional, follow §7a.5.

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

The KV keys the backend reads are exactly these four — no others:

| Key | Values | Notes |
|-----|--------|-------|
| `mode` | `on` \| `off` | Absent ⇒ `off`. `off` forces the public provider to `off`. |
| `provider` | `hcaptcha` \| `turnstile` | Strict allowlist; a typo fails verification closed. |
| `sitekey` | provider site key | Public; sent to the browser. |
| `secret` | provider secret | Private; used only for server-side `siteverify`. |

`sitekey`/`secret` come from the provider dashboard (hCaptcha or Cloudflare
Turnstile) — SMAP cannot generate them. Only **enabling** CAPTCHA needs them;
disabling never does.

**Disable (immediate, no provider keys needed):**

```bash
# Prod/staging Vault runs in a container; exec into it with an admin token.
docker exec -e VAULT_TOKEN=<token> $(docker ps -qf name=vault) \
  vault kv put secret/smap/config/captcha mode=off provider=hcaptcha sitekey="" secret=""
```

**Enable:** write all four keys at once (`vault kv put` overwrites the whole
secret — a partial write that drops `secret` breaks verification):

```bash
docker exec -e VAULT_TOKEN=<token> $(docker ps -qf name=vault) \
  vault kv put secret/smap/config/captcha \
    mode=on provider=hcaptcha sitekey=<site-key> secret=<secret-key>
```

Verify with `vault kv get secret/smap/config/captcha`, then hard-refresh the
register page (the SPA caches `captcha-config` on mount).

> **Bootstrap gotcha:** older `vault-init` seeds wrote `public_key`/`secret_key`
> (wrong names) and left `mode` unset. Because `mode` then defaulted to `on`, a
> fresh stack enforced CAPTCHA against an empty sitekey/secret — an unwinnable
> "please complete the security check" that blocked all registration while login
> kept working. Fixed in `smap/bootstrap/vault_init.py` (correct key names +
> `mode=off` seed) and `shared_kernel/auth/captcha.py` (default `mode=off`).
> `_ensure_kv` never overwrites an existing secret, so a stack bootstrapped
> before the fix must be corrected with the `vault kv put` above.

### 7a.5 Closed deployment: onboarding with no outbound mail

A self-hosted install with no SMTP relay can still register and enrol users. There is
deliberately **no** `registration_mode=invite_only` switch; the two controls below do the
same job with mechanisms that already exist, and every invite and provisioned account
carries a copyable link so nothing depends on delivery.

**1. Close registration to your own domains** (R19a.13). The policy is a versioned,
audited row in PostgreSQL, edited from the Admin console under Users. `allow` admits only
the listed domains, so an empty allow list blocks every signup; `deny` blocks only the
listed domains; `off` applies no restriction and keeps both lists for later. One policy
governs self-registration, email changes and Admin-provisioned accounts alike.

Domains are matched exactly after normalisation, against the part after the last `@`.
Subdomains are not implied: `example.edu` does not admit `dept.example.edu`, which must be
listed separately.

The API behind that screen is `GET`/`PUT /api/admin/email-domain-policy`. `PUT` is a full
replacement and requires an `If-Match` header carrying the version the form was read at, so
two administrators editing at once cannot silently overwrite each other.

> **A read is accelerated by a 30-second Redis cache, but the cache is never the
> authority.** A missing, expired, evicted, corrupt or unreadable cache falls back to
> PostgreSQL; a cache value can never extend its own lifetime, so a change is in force
> everywhere within 30 seconds. If PostgreSQL is unreachable as well, registration returns
> `503` rather than admitting every domain. The control fails **closed**, which is the
> difference from the Redis-only version this replaced.

Editing is disabled in two rollout phases, and the Admin screen says which one it is in and
what lifts it. See §7a.6.

**2. Turn the registration CAPTCHA off** if the install has no outbound internet — see
§7a.4. `mode=off` needs no provider keys.

**3. Hand links over instead of mailing them.**

| Situation | What to do |
| --- | --- |
| The person already has an account | Nothing special. `POST /api/orgs/{id}/invites` and `POST /api/projects/{id}/invites` write an in-app notification, and the invite appears in their `/invites` inbox with no mail involved. |
| The person has an account but you want to be sure | The invite-create response carries `accept_url`. Copy it to them over any channel. |
| The person has no account | `POST /api/admin/users` provisions one and returns two links: a **set-password** link (30 min) and a **verify-email** link (24 h). Hand both over, then invite the account as usual. `POST /api/admin/users/{id}/activation-links` re-mints the pair, so click it when you are actually with the person rather than racing the 30-minute clock. |
| They self-registered before you provisioned them | Self-registration still works, but nothing hands them the verification link. With `SMTP_HOST` unset the dev sender writes the whole message body — link included — to the backend log (`event=email_send`, `template=verify_email`), so an operator can recover it from there. Provisioning is the supported route; this is the fallback. |

**Treat every one of these links as a credential.** Possession is authorisation: the accept
link enrols whoever opens it, and the set-password link is equivalent to the account's
password. They are single-use, they are returned only to the person who minted them, and no
read endpoint will show them again — a lost invite link is recovered by revoking the invite
and re-inviting, not by re-reading it.

What provisioning does **not** do: it does not mark the address verified (the holder must
still walk the verification link — an Admin handing it over out of band proves only that an
Admin vouched, not that the address is theirs), it does not bypass the domain list above,
and it does not place anyone in an Org or Project. Membership is still created only by the
invitee accepting an invite.

### 7a.6 Email-domain policy: deploy, activate, roll back

Migration 0084 moved the email-domain policy from three unversioned Redis keys into a
versioned PostgreSQL row. PostgreSQL and Redis cannot commit together, so the change of
authority is staged: a replica running the previous release still reads the three
`config:email_domain:*` keys and knows nothing about the row. The staging exists so that no
window has two releases enforcing two different policies.

The row carries a `rollout_state` with three values.

| State | What governs a new replica | Admin edits | Previous release |
| --- | --- | --- | --- |
| `compatibility` | the legacy Redis keys | refused (409) | may still be running |
| `active` | PostgreSQL, cached for 30 s in Redis | permitted | must already be gone |
| `rollback_frozen` | PostgreSQL, read directly | refused (409) | may start once the marker is verified |

Every command below is idempotent and exits non-zero on failure. Run them from a backend
container: `docker compose exec backend-web python -m smap.maintenance <command>`.

**Deploying this release.** Apply migrations and start the new image as usual. The first
backend process to boot imports one atomic snapshot of the legacy keys into the row as
version 1, in `compatibility`. The deployment is in `compatibility` from that moment, and
nothing about enforcement changes: the legacy keys still govern for old and new replicas
alike. `PUT` on the Admin screen is refused, deliberately, until activation.

If the legacy keys are in a shape the import cannot read, **the boot fails** rather than
coming up with no policy authority. Grep the boot log for
`email_domain_policy_bootstrap_imported` to confirm a successful import; a failure names the
key at fault. The three shapes that block a boot are: `mode` absent while a list holds
members, an unrecognised `mode` value, and a key holding the wrong Redis type. Repair the
keys with `redis-cli` and restart — no row was written, so the retry is clean.

**Activating.** Once every replica of the previous release has drained:

```bash
docker compose exec -e SMAP_ACTIVATE_EMAIL_DOMAIN_POLICY_ARMED=1 backend-web \
  python -m smap.maintenance activate-email-domain-policy
```

The environment variable is the assertion that the old replicas are gone. Nothing in the
platform can verify that, so it is an operator assertion and is deliberately explicit. The
command adopts one final snapshot of the legacy keys as it switches, so a `redis-cli` edit
made during the compatibility window is not reverted. It prints the resulting state and
version; `changed=False` means it was already active.

After activation the Admin screen is editable and the legacy keys are inert. Leave them in
place — they are what a rollback would rewrite.

**Preparing a rollback.** Before starting a previous image, or running the `0084`
downgrade:

```bash
docker compose exec backend-web \
  python -m smap.maintenance prepare-email-domain-policy-rollback
```

This freezes the policy (Admin edits start returning 409), rewrites all three legacy keys
from the frozen policy atomically, reads them back, and records
`legacy_mirrored_version = version` only if the readback matches exactly. Freezing first is
what makes the verified snapshot trustworthy: an Admin edit arriving afterwards is rejected
rather than silently invalidating it.

> **Do not start an old image, and do not run `alembic downgrade`, unless the command
> succeeded and `legacy_mirrored_version` equals `version`.** Alembic touches PostgreSQL
> only: it can neither write the legacy Redis keys nor check them. A downgrade run without a
> verified marker leaves the previous release reading whatever happens to be in Redis, which
> after a flush or an eviction is nothing at all. A failed run leaves the policy frozen and
> unmarked, which is safe and safe to retry.

The marker is visible on the Admin screen and in `GET /api/admin/email-domain-policy` as
`legacy_mirrored_version`. Any Admin edit clears it, because the edit moves the policy past
the version the mirror was verified against.

**Cancelling a prepared rollback.** If the rollback is not taken and the old images are gone
again:

```bash
docker compose exec backend-web \
  python -m smap.maintenance cancel-email-domain-policy-rollback
```

This returns the policy to `active` and clears the marker.

**Break-glass diagnosis.** If registration is refusing every address with a 503
(`admin/email-domain-policy-unavailable`), the policy authority is unreachable, not
misconfigured. Check PostgreSQL first; the Redis cache being empty is normal and costs one
database read. If the row itself is missing — for example after a downgrade under a running
backend — restart a backend process so the import runs again, having first confirmed the
legacy keys hold the policy you intend.

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

---

## 12. Alerting

Prometheus alert rules live in `deploy/observability/prometheus/alerts.yml` and
are auto-loaded when the observability stack is running.

### 12.1 Alert severity levels

| Severity | Response time | Channel |
|----------|--------------|---------|
| critical | Immediate (< 5 min) | PagerDuty / phone |
| high | < 15 min | Slack #smap-alerts |
| warning | < 4 hours | Slack #smap-ops |

### 12.2 Configured alerts

**Critical:** BackendDown, ReadyzFailing, VaultSealed, PostgresDown.

**High:** DbPoolExhausted, DbPoolWaiters, RedisDown, HighErrorRate, TlsCertExpiringSoon (< 7d), VaultTlsCertExpiringSoon (< 7d).

**Warning:** TlsCertExpiring30d, HighLatencyP95, DiskSpaceLow, QdrantDown, MinioDown, HighRetentionFailures.

### 12.3 TLS certificate monitoring

Two mechanisms:

1. **Prometheus alerts** — `smap_tls_cert_expiry_seconds` and `smap_vault_tls_cert_expiry_seconds` gauges trigger at 30d (warning) and 7d (high).
2. **Manual check** — `bash deploy/scripts/check-tls-expiry.sh` outputs a human-readable table or `--metrics` for textfile collector integration.

Recommended cron for the textfile collector approach:

```
*/6 * * * * bash /opt/smap/deploy/scripts/check-tls-expiry.sh --metrics \
  > /var/lib/prometheus/node-exporter/smap_tls.prom
```

---

## 13. Upgrades

See `docs/runbook-upgrade.md` for the full procedure. Key points:

- Migrations are N-1 compatible (old code works on new schema).
- Rolling restart: frontend → worker → web (maintains availability).
- Rollback: `git checkout <old-tag>` + `alembic downgrade -1` + rebuild. Rolling back past
  migration 0084 needs `prepare-email-domain-policy-rollback` to succeed **first** — see
  §7a.6 for why a downgrade alone leaves the previous release with no email-domain policy.

---

## 14. Frontend dependency advisories

Transitive npm advisories are resolved with `pnpm.overrides` in the root `package.json`, verified with `pnpm audit`.

### 14.1 Override rules

- **[O14.01]** Every override value MUST carry an upper bound (`">=1.1.16 <2.0.0"`, not `">=1.1.16"`). Without one, pnpm resolves the newest release across all majors and silently swaps a major version into a dependent that cannot use it.
- **[O14.02]** An override MUST keep each dependency line inside its own major. If the only patched release is in a later major, treat the advisory as unfixable here (§14.2) rather than forcing the bump — the parent package must be upgraded instead.
- **[O14.03]** After changing overrides, run `pnpm install` then the full frontend gate: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`. A cross-major swap typically passes `install` and fails only at runtime on an untaken code path, so `pnpm audit` alone is not sufficient evidence.

### 14.2 Accepted residual advisories

`brace-expansion` GHSA (unbounded expansion length → OOM) remains open on the 1.x and 2.x lines, reached via `minimatch@3.1.5` (`@eslint/config-array`) and `minimatch@9.0.9` (`glob`, `editorconfig`, `type-coverage-core`, `js-beautify`). Upstream published no backport; the only patched release is `5.0.8`, and 5.x dropped the default export that both `minimatch` versions call, so forcing it breaks brace globbing at runtime.

Accepted because the reachable surface is dev-only: `minimatch` is invoked by lint, glob, and type-coverage tooling over glob patterns committed to this repo, never over request data. No Node process runs in production (the frontend ships as static assets; the backend is Python). Revisit when the parent packages move to `minimatch@10`.
- Full restore from backup is the last resort for irreversible migrations.
