# SMAP Release Checklist

Execute on staging before tagging each release. Every item must pass.
Tag format: `vYYYY.MM.DD` (date-based) or `v1.0.0` (semver at launch).

---

## 1. Vault — 7-point verification

Source: `deploy/vault/README.md` §7.

- [ ] `vault status` reports `sealed=false`.
- [ ] `vault policy read smap-backend` matches `deploy/vault/policies/smap-backend.hcl`.
- [ ] `vault policy read smap-rotation` matches `deploy/vault/policies/smap-rotation.hcl`.
- [ ] `vault read transit/keys/smap-provider-secret` shows `deletion_allowed=false, exportable=false`.
- [ ] Backend log contains `vault: authenticated as smap-backend, token ttl=…` on startup.
- [ ] Synthetic upload + outbound call on a throwaway project succeeds end-to-end.
- [ ] `vault token lookup` for backend token shows `renewable=true` and decreasing TTL.

---

## 2. Backend

- [ ] `/readyz` returns 200 on staging with all 6 dependencies green (Postgres, Redis, Qdrant, Neo4j, MinIO, Vault).
- [ ] `/healthz` returns 200.
- [ ] `python -m smap.bootstrap all` is idempotent — second run produces no errors or side effects.
- [ ] First admin account created and can log in.
- [ ] All Alembic migrations applied cleanly (`alembic upgrade head` — 30 migrations, A through M).
- [ ] `alembic downgrade base && alembic upgrade head` round-trip succeeds.
- [ ] **SMTP smoke (K.6)**: with `SMTP_*` + Vault `secret/smap/config/smtp` configured, register a throwaway address on staging → verification mail **received** → link verifies → login succeeds. Run one password-reset round-trip and one invite to an unregistered address (sign-up → auto-enroll).
- [ ] **SMTP fail-open guard**: a `env=prod` boot with no `SMTP_HOST` logs `event=smtp_unconfigured` exactly once and does not crash.
- [ ] **CAPTCHA**: `GET /api/auth/captcha-config` returns the configured provider+sitekey (no secret); with `mode=off` no widget renders and registration still succeeds.

---

## 3. Frontend — CI SoC gates

All 12 gates from §24.15 must be green on the tagged release commit:

- [ ] Gate #1: Layer direction (`eslint-plugin-boundaries`)
- [ ] Gate #2: Slice isolation (`no-restricted-imports`)
- [ ] Gate #3: Transport isolation (no raw `fetch`/`WebSocket`/`EventSource`)
- [ ] Gate #4: `v-html` allowlist (only `ChatroomView.vue` via `renderMarkdown.ts`)
- [ ] Gate #5: No `alert`/`confirm`/`prompt`
- [ ] Gate #6: No global CSS outside `shared/styles/` (`scripts/check-global-css.sh`)
- [ ] Gate #7: Store isolation (no cross-slice api imports)
- [ ] Gate #8: Every view has ≥ 1 integration test (`scripts/check-view-tests.sh`)
- [ ] Gate #9: Bundle budget — initial ≤ 250 KB gzip, per-view lazy ≤ 200 KB gzip
- [ ] Gate #10: Type coverage ≥ 95% (`scripts/check-type-coverage.sh`)
- [ ] Gate #11: Accessibility (`eslint-plugin-vuejs-accessibility`)
- [ ] Gate #12: i18n — no bare string literals in templates

---

## 4. Frontend — quality

- [ ] `pnpm run typecheck` passes (zero errors).
- [ ] `pnpm run test` passes — all unit + integration tests green.
- [ ] Coverage thresholds met: lines ≥ 80%, functions ≥ 80%, branches ≥ 75%, statements ≥ 80%.
- [ ] Bundle analysis: record initial chunk size and largest lazy chunk size.
  - Initial: ______ KB gzip (must be ≤ 250 KB)
  - Largest lazy: ______ KB gzip (must be ≤ 200 KB)
- [ ] Lighthouse audit on landing page:
  - Performance: ≥ 90
  - Accessibility: ≥ 90
  - Best Practices: ≥ 90
  - SEO: ≥ 90
- [ ] No Axe critical or serious violations on core views (login, dashboard, chatroom, workflow editor).

---

## 5. E2E — Playwright golden paths

Run against `compose.test.yml` (full stack with seeded fixtures):

- [ ] 01 — Identity flow: register → verify email → login
- [ ] 02 — Org/project flow: create org → invite → accept → transfer OC
- [ ] 03 — LLM key flow: upload → validate → carry into project → build group
- [ ] 04 — Agent + RAG flow: create agent → attach RAG → ingest → grounded answer
- [ ] 05 — Chatroom live: two-browser send/receive sync
- [ ] 06 — File upload: tus 600 MB with mid-transfer blip
- [ ] 07 — Workflow flow: editor → validate → run → live steps via WS
- [ ] 08 — Admin impersonate: start → audit visible → last-admin block

Phase M UI gap-remediation flows (M.1–M.4):

- [ ] 09 — RAG config (M.1): create a RAG config → it appears in the agent RAG picker → attach to an agent. (Note: path 04 depends on this.)
- [ ] 10 — GraphRAG (M.1): create a GraphRAG config → bind on the agent → Build → status reaches a terminal `BuildState`; the list shows Bound/Not-bound correctly.
- [ ] 11 — MCP (M.1): bind a url/package MCP server to an agent → Test returns tool names; add/remove a project egress-allowlist host.
- [ ] 12 — Notifications (M.2): an event (e.g. invite received) bumps the bell unread count live over `/ws/user`; the panel lists it; mark-read clears it.
- [ ] 13 — Message edit/delete (M.3): author edits own message < 5 min (R13.21) and after > 5 min is blocked (Admin/Owner can); delete removes it for everyone + from search (R13.16).
- [ ] 14 — Export + attachments (M.3/M.5): export → status polls to ready → download link works; a message's attachment downloads via presigned URL; an expired attachment shows `[attachment expired]` (R13.11).
- [ ] 15 — Tenancy/keys mgmt (M.4): change a project member's role; rename an org/project; key-group rename; project key-usage panel renders windowed counts.

Retry budget: ≤ 1 per path.

---

## 5a. Phase M backend (M.5) acceptance

Verify on staging (these were the K-deferred / escalated backend items):

- [ ] Per-agent built-in tools (M.5): an agent with no `source='builtin'` MCP binding still has file/web_search/code_exec; once a builtin binding lists a subset, only those are exposed (R12.01/R12.10).
- [ ] Admin rate-limit override (M.5): `GET /api/admin/rate-limits` returns the 5 seeded bucket rows; `PATCH` one and confirm the new budget takes effect **without a restart** (the Redis mirror is updated live).
- [ ] GraphRAG reconciler (M.5): the `graphrag_reconcile` arq cron appears in the worker schedule and runs ~once/minute; a config left in `FAILED_COMPENSATING` is healed.
- [ ] rotate-transit (M.5): run `smap.rotation rotate-transit` twice in succession — the **second** rotation rewraps all DEKs (does not skip them); `rewrap_progress` resets per rotation.
- [ ] Message attachments API (M.5): `GET /api/chatrooms/{id}/messages` returns each message's `attachments` (incl. expired/quarantined), enabling the path-14 UI.

---

## 6. Data — retention workers

Verify each retention worker's last-run metric is within the expected window:

| Worker | Expected cadence | Metric |
|---|---|---|
| messages_purge | nightly | `retention_messages_last_run` |
| attachments_cleanup | hourly | `retention_attachments_last_run` |
| exports_cleanup | hourly | `retention_exports_last_run` |
| audit_purge | nightly | `retention_audit_last_run` |
| workflow_runs_archive | nightly | `retention_workflow_runs_last_run` |
| key_usage_rollup | hourly | `retention_key_usage_last_run` |
| key_usage_partitions | nightly | `retention_key_usage_partitions_last_run` |
| soft_deleted_tenancy | nightly | `retention_soft_deleted_last_run` |
| invites_expiry | hourly | `retention_invites_last_run` |
| oc_transfers_expiry | nightly | `retention_oc_transfers_last_run` |
| approvals_timeout | nightly | `retention_approvals_last_run` |
| token_cleanup | hourly | `retention_tokens_last_run` |
| sessions_cleanup | nightly | `retention_sessions_last_run` |
| instructions_sweep | nightly | `retention_instructions_last_run` |
| agent_instances_cleanup | nightly | `retention_agent_instances_last_run` |
| tus_parts_cleanup | hourly | `retention_tus_parts_last_run` |
| impersonation_sessions | every 5 min | `retention_impersonation_last_run` |

- [ ] All workers have run within their expected cadence on staging.
- [ ] No workers in error state.

---

## 7. Deployment — bring-up verification

- [ ] Staging bring-up completed in < 60 min following `deploy/README.md` alone.
- [ ] All 12 services healthy (`docker compose ps` — no unhealthy/restarting).
- [ ] Nginx returns correct security headers (HSTS, CSP, X-Content-Type-Options, X-Frame-Options).
- [ ] WebSocket upgrade succeeds on `/ws/` path.
- [ ] No `server` header leaked by nginx (banner suppression).

---

## 8. Documentation

- [ ] `docs/implement/00-overview.md` §0.8: all phases A–M marked ☑ with dates.
- [ ] `REQUIREMENTS.md` has no unresolved TODOs or TBD markers for v1 scope.
- [ ] `docs/operations.md` runbooks reviewed and current.
- [ ] `deploy/vault/README.md` matches deployed Vault state.
- [ ] `docs/frontend-exceptions.md` lists all active gate exceptions with rationale.

---

## 9. Release tag

- [ ] All CI jobs green on the release commit.
- [ ] Tag created: `git tag -a v1.0.0 -m "SMAP v1.0.0 release"` (or `vYYYY.MM.DD`).
- [ ] Tag pushed: `git push origin v1.0.0`.
- [ ] Release notes drafted with:
  - Summary of capabilities
  - Known limitations
  - Minimum host requirements (16-core / 32 GB)
  - Link to `deploy/README.md` for operator instructions

---

## 10. Backup & restore

- [ ] Postgres `pg_dump` tested — dump completes in < 10 min for staging data set.
- [ ] Postgres restore round-trip verified: `pg_dump | pg_restore` into a clean DB, then `alembic upgrade head` succeeds (no-op).
- [ ] MinIO bucket data (`chat-uploads`, `rag-sources`, `exports`) backed up via `mc mirror` or equivalent.
- [ ] Vault disaster recovery tested per `deploy/vault/README.md` §6 — unseal keys + recovery snapshot.
- [ ] Neo4j `neo4j-admin dump` completes; restore into fresh container succeeds.
- [ ] Redis AOF integrity checked: `redis-check-aof --fix` on a copy of `appendonly.aof`.
- [ ] Backup schedule documented: frequency, retention, offsite copy if applicable.

---

## 11. TLS & certificate verification

- [ ] Nginx serves valid TLS cert on :443 (not the dev self-signed cert in staging/prod).
- [ ] Vault internal TLS enabled — `vault status` shows `https` listener; backend connects via `SMAP_VAULT_ADDR=https://vault:8200`.
- [ ] Certificate expiry ≥ 90 days from release date.
- [ ] Certificate rotation procedure documented and tested (nginx reload, Vault restart).
- [ ] No services communicate secrets over plaintext HTTP outside the Docker internal network.

---

## 12. Monitoring & alerting baseline

- [ ] Prometheus scrapes `backend-web:8000/metrics` successfully (check Targets page).
- [ ] Grafana SMAP dashboard loads without errors; all panels have data.
- [ ] Key metrics have non-zero values: `http_requests_total`, `db_pool_available`, `workflow_runs_total`.
- [ ] Loki receives logs from all compose services (query `{service=~".+"}` returns recent entries).
- [ ] Tempo receives traces (if OTEL is enabled): query a recent trace ID in Grafana.
- [ ] Promtail positions file persists across container restart (verify volume mount).

---

## Sign-off

| Role | Name | Date | Signature |
|---|---|---|---|
| Operator | | | |
| Developer | | | |
