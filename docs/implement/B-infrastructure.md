# Phase B — Infrastructure Bootstrap & Operations

**Goal.** Make the stack feature-ready: Vault initialised with the right Transit keys (`smap-provider-secret`, `smap-guest-link`, `smap-jwt-sign`) + KV + AppRoles + policies; typed Vault client; Alembic baseline with migration policy; MinIO buckets (**`chat-uploads`, `rag-sources`, `exports`** — exact names); Qdrant + Neo4j init + extensions; bootstrap CLI; healthz/readyz; resource limits; observability baseline.

**Size.** M
**Depends on.** A (compose up works).
**Unblocks.** C (auth, DB), D (Transit + KV), E (Neo4j + Qdrant), F (MinIO), everything.
**Refs.** `REQUIREMENTS.md` §7.6, §21.5, §25; `docs/operations.md` §2–§5; `deploy/vault/README.md`.

## B.0 Scope summary

- `python -m smap.bootstrap all` idempotent against a fresh compose stack.
- Vault has keys `smap-provider-secret` (AES-256-GCM Transit, not exportable, not deletable), `smap-guest-link` (ed25519 Transit), `smap-jwt-sign` (RS256 Transit), KV path `secret/smap/config/*`, and AppRoles `smap-backend` + `smap-rotation` bound to the policies in `deploy/vault/policies/`.
- Alembic baseline + migration-policy CI.
- MinIO buckets `chat-uploads` (3-day lifecycle), `rag-sources` (kept), `exports` (24-hour lifecycle).
- Qdrant / Neo4j reachable; Postgres extensions `pgvector` + `pg_cron` created.
- `/readyz` multi-dep probe returns structured problem+json on failure.

## B.1 Vault init & policies — **OPS** — L

**Deliverables.**

- `deploy/vault/policies/smap-backend.hcl` and `smap-rotation.hcl` already in repo.
- `smap.bootstrap vault-init`:
  - Enables `transit` + `kv-v2`.
  - Creates Transit keys:
    - `smap-provider-secret` — `aes256-gcm96`, `exportable=false`, `deletion_allowed=false` (R7.6 step 1).
    - `smap-guest-link` — `ed25519` for signing guest-link tokens (§F, `deploy/vault/README.md`).
    - `smap-jwt-sign` — `rsa-2048` (or 3072) for RS256 JWT signing; `exportable=false`; rotated quarterly with 7-day verify-overlap (R6.03).
  - Seeds KV:
    - `secret/smap/config/captcha` (provider public/private keys).
    - `secret/smap/config/smtp` (optional SMTP creds).
    - `secret/smap/config/hmac-key` (envelope HMAC material).
    - `secret/smap/config/minio` (MinIO service-account creds).
- `smap.bootstrap vault-approle` creates both AppRoles, binds policies, outputs `role_id` + `secret_id` for operator capture.
- Production: Shamir 3-of-5 per `deploy/vault/README.md`. Dev compose uses `vault -dev` for local DX.

**Key IDs.** §7.6, `deploy/vault/README.md`, §21.1 entity matrix for Vault entries.

**Exit criteria.** `vault token capabilities <backend-token> transit/datakey/plaintext/smap-provider-secret` = `create, update`; rotation token has `rotate + rewrap` only; neither token can `export`.

## B.2 Vault client library — **CODE** — M

**Deliverables.**

- `shared_kernel/infra/vault.py` wrapping `hvac` with methods:
  - `create_dek()`, `unwrap_dek(wrapped)` (against `smap-provider-secret`).
  - `encrypt_envelope(plaintext, aad)`, `decrypt_envelope(record, aad)` using HMAC key from KV.
  - `sign_guest_link(payload)`, `verify_guest_link(token)` (ed25519).
  - `sign_jwt(payload, *, kid)`, `verify_jwt(token)` — RS256 via Transit; public keys cached in-process with `kid` version rotation.
  - `kv_get(path)`, `kv_put(path, data)`.
- Client obtains an AppRole token at startup; re-logins on 403.
- Unit tests + one integration test against dev Vault.

**Key IDs.** §7.6, §6.03.

**Exit criteria.** Round-trip encrypt/decrypt + JWT sign/verify across a forced `kid` rotation.

## B.3 Alembic baseline — **OPS** — S

**Deliverables.**

- `backend/alembic/` wired to `app/config/` DB URL.
- Revision `0000_baseline` empty; bootstrap marks as current.
- `docs/operations.md` §4 (N-1 compatibility) referenced in `backend/alembic/README`.
- CI job `migrations-check`: every new revision up + down cleanly on a scratch DB.

**Key IDs.** `docs/operations.md` §4.

**Exit criteria.** `alembic upgrade head` + `alembic downgrade base` green.

## B.4 Bootstrap CLI — **CODE** — M

**Deliverables.** `python -m smap.bootstrap <subcommand>`:

- `vault-init` (keys + mounts + KV seeds).
- `vault-approle` (both roles + policies).
- `db-init` (`alembic upgrade head`; enables `pgvector` + `pg_cron` extensions in Postgres so §25 stays honoured).
- `minio-init` (see B.7).
- `qdrant-init` (readiness probe only).
- `neo4j-init` (see B.8).
- `create-admin --email --password` (inserts `users` + `admins` rows + guard against creating a second initial admin once one exists).
- `all` runs the subcommands in dependency order.

Every subcommand prints "did" vs "already present".

**Key IDs.** `docs/operations.md` §5.

**Exit criteria.** `all` idempotent on second run.

## B.5 Healthz / Readyz — **CODE** — S

**Deliverables.**

- `GET /healthz` = process liveness.
- `GET /readyz` green only if Postgres `SELECT 1`, Redis `PING`, Qdrant `/readyz`, Neo4j `CALL db.ping`, MinIO `HeadBucket chat-uploads`, Vault `sys/health?standbyok=true` all succeed within 2 s total.
- Failure → problem+json `type=https://smap.local/problems/dependency-unavailable`, `status=503`, `detail` naming the failed deps.
- Backend-worker runs a tiny HTTP sidecar for its own `/healthz` per `docs/operations.md` §2.

**Key IDs.** `[R3.03]`, `docs/operations.md` §2.

**Exit criteria.** Killing any dep flips `/readyz` to 503; recovery flips back within 5 s.

## B.6 Resource limits — **OPS** — S

**Deliverables.**

- `deploy/compose/docker-compose.prod.yml` adjoint overlay (used only for prod staging — single authoritative `docker-compose.yml` remains per §25) with per-service `deploy.resources.limits.memory / cpus` aligned to `docs/operations.md` §3 and R20.03:
  - Postgres 4 GB; Redis 2 GB; Neo4j 4 GB; Qdrant 4 GB; MinIO 4 GB; Vault 1 GB; backend 8 GB; remainder headroom.
- Postgres `shared_buffers / effective_cache_size / work_mem` tuned for its 4-GB allocation.
- Redis `maxmemory` + `maxmemory-policy allkeys-lru`.

**Key IDs.** `[R20.03]`, `docs/operations.md` §3.

**Exit criteria.** `docker stats` under `make smoke-load` shows no limit breach.

## B.7 MinIO buckets — **OPS** — S

**Deliverables.** Exactly the three buckets from §21.5 with correct lifecycles:

- `chat-uploads` — 3-day expiration (R13.10).
- `rag-sources` — kept as long as `rag_documents` row lives.
- `exports` — 24-hour expiration.
- Dedicated MinIO service account (creds in Vault KV `secret/smap/config/minio`) with policy restricted to `PutObject / GetObject / DeleteObject / ListBucket` on the three buckets only.

**Key IDs.** §21.5, `[R13.10]`.

**Exit criteria.** `mc ls` shows all three; `chat-uploads` lifecycle verified via fast-forward rule test.

## B.8 Neo4j schema prep — **OPS** — S

**Deliverables.**

- Create database `smap` (Community edition supports a single user DB + system DB).
- Constraints aligned with §11 / §21.3: per-project subgraph labelled `:P_{project_id}` with `(:Entity {id})` uniqueness and indexes on `:Entity(canonical_name)` and `:REL(type)`.
- Executed by `neo4j-init`.

**Key IDs.** §11, §21.3.

**Exit criteria.** `SHOW CONSTRAINTS` + `SHOW INDEXES` confirm.

## B.9 Qdrant init probe — **OPS** — S

**Deliverables.**

- `qdrant-init` only runs readiness probe and prints version. Collections `rag_{project_id}` / `graphrag_{project_id}` are created on demand in Phase E.

**Key IDs.** §21.4.

**Exit criteria.** Exits 0 against healthy Qdrant.

## B.10 Observability baseline — **CODE** — S

**Deliverables.**

- Per §4: OpenTelemetry SDK → OTLP collector → Grafana Tempo / Loki / Prometheus stack declared as optional/structural. Backend instrumented with OTEL (FastAPI + SQLAlchemy + httpx) with `OTEL_EXPORTER_OTLP_ENDPOINT` settable.
- `GET /metrics` Prometheus-compatible (`prometheus-client`), opt-in via `metrics.enabled=true`.
- Seed counters: `http_requests_total{method,route,status}`, `db_pool_in_use`, `redis_command_errors_total`.
- Access restricted to localhost + Nginx upstream only.

**Key IDs.** §4, `docs/operations.md` §1.

**Exit criteria.** `/metrics` returns exposition format with at least one sample per seeded counter.

## B.∞ Phase gate

- [ ] `python -m smap.bootstrap all` idempotent on fresh + replayed compose.
- [ ] Vault tokens have exact capability sets per `deploy/vault/README.md`; `smap-jwt-sign` Transit key present.
- [ ] Alembic up / down green; CI migrations-check green.
- [ ] Postgres `pgvector` + `pg_cron` extensions created.
- [ ] MinIO buckets `chat-uploads / rag-sources / exports` present with lifecycles.
- [ ] Neo4j constraints + indexes confirmed.
- [ ] `/readyz` flips correctly.
- [ ] `create-admin` inserts `admins` + `users` row visible via `psql`.
- [ ] `00-overview.md` §0.8: B = done.

## Cross-cutting checklist

1. **AuthZ tap.** Bootstrap CLI is operator-only (host process, not HTTP-exposed).
2. **Audit tap.** Each bootstrap subcommand logs an audit-style entry (`actor=bootstrap-cli`).
3. **Rate limit bucket.** `/healthz` and `/readyz` excluded in C.12.
4. **Observability.** `/metrics` live.
5. **RFC 7807.** `https://smap.local/problems/dependency-unavailable` registered.
6. **Migration policy.** Baseline + N-1 check gate.
7. **Secrets.** Only `role_id / secret_id` in env; everything else from Vault.

## Risks

- **Shamir key loss.** `deploy/vault/README.md` warns; no magical recovery.
- **Alembic autogenerate drift.** Wrap in `make migration` that resets a scratch DB first.
- **Compose dev vs prod mix.** CI runs `dev` and `prod` overlays in separate matrices; never mixed.
- **JWT key rotation window.** B.1 pins the 7-day overlap explicitly; CI includes a verify-both-`kid` test.
