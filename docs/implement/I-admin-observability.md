# Phase I — Admin, Audit, Notifications, Retention

**Goal.** Ship the operator surface per §16, §17, §18, §20: full `/api/admin/*` endpoints (users / ip-bans / admins-CRUD / orgs / projects / audit / restore / metrics / rate-limits / graphrag reset / impersonation); audit query & append-only guarantee; in-app-only notifications with the exact R18.02 kind set; the retention worker matrix (messages 5y, audit 365d, workflow runs 90d → archive, usage events 13mo → daily rollup, soft-deleted rows 60d → hard delete, invites / transfers / approvals / attachments / incomplete tus uploads); dashboards and runbook drills.

**Size.** L
**Depends on.** C–H (feeds audit + retention hooks).
**Unblocks.** J (admin slice needs endpoints).
**Refs.** `REQUIREMENTS.md` §16, §17, §18, §20; `docs/operations.md` §6–§9; §22.13.

## I.0 Scope summary

- Admin console drives every §22.13 endpoint, including promote/demote with last-admin safeguard.
- `admin.view_as_started / _ended` impersonation with per-session row, notification to target, read-only JWT claim.
- Audit events searchable; trigger enforces append-only; retention nightly deletes old rows.
- Notifications in-app only, with the five R18.02 kinds; persisted + pushed via `/ws/user/{id}`.
- Retention workers run at the cadence and cutoffs in §20 / §13 / §17 / §21.
- Grafana dashboards and runbook drills documented.

## I.1 Admin endpoints — **CODE** — L

**Deliverables.** All under `/api/admin/*` with `Admin` role required (§22.13):

| Method | Path | Notes |
|---|---|---|
| GET | `/api/admin/users?q=&status=` | Search/list. |
| GET | `/api/admin/users/{id}` | Full detail: orgs, projects, keys (masked), recent activity. |
| POST | `/api/admin/users/{id}/ban` | `{reason}`; banned user receives the reason on next login once (R18.02 last bullet), audit logged. |
| POST | `/api/admin/users/{id}/unban` | |
| POST | `/api/admin/users/{id}/delete` | Soft-delete. |
| POST | `/api/admin/users/{id}/hard-delete` | Irreversible after 60-day grace. |
| POST | `/api/admin/users/{id}/impersonate` | Start read-only view-as (see I.6). |
| POST | `/api/admin/users/{id}/end-impersonate` | End view-as. |
| GET | `/api/admin/ip-bans` | |
| POST | `/api/admin/ip-bans` | `{cidr, reason}`. |
| DELETE | `/api/admin/ip-bans/{id}` | |
| GET | `/api/admin/admins` | List `admins` rows. |
| POST | `/api/admin/admins` | Promote `{user_id}` — requires confirmation (client enforces double-click); audit. |
| DELETE | `/api/admin/admins/{user_id}` | Demote; **last Admin cannot be demoted** (404 + `{error:"last_admin"}`), enforced in app + by count-trigger on `admins`. |
| GET | `/api/admin/orgs` | List Orgs. |
| POST | `/api/admin/orgs/{id}/force-delete` | Bypass OC requirement (R8.04). |
| POST | `/api/admin/orgs/{id}/force-transfer-original-creator` | `{target_user_id}` — R8.19 path; audit `org.original_creator_force_transferred`. |
| GET | `/api/admin/projects` | List Projects. |
| GET | `/api/admin/audit?filters…` | See I.2. |
| POST | `/api/admin/restore/{type}/{id}` | Restore soft-deleted within 60 d (R8.13). |
| GET | `/api/admin/metrics` | System health + usage aggregate. |
| PATCH | `/api/admin/rate-limits/{key}` | Tune `rate_limit_policies` (R19.04). |
| POST | `/api/admin/graphrag/{id}/reset` | Force `last_build_state='idle'` (R11a.02). |

- Admin never sees plaintext keys (R16.05); endpoints return masked previews.

**Key IDs.** §16, §22.13, `[R16.01]`–`[R16.06]`, `[R8.19]`, `[R11a.02]`.

**Exit criteria.** Each endpoint covered by test; non-Admin receives `forbidden`; last-Admin demotion attempt returns structured error.

## I.2 Audit query & export — **CODE** — M

**Deliverables.**

- `GET /api/admin/audit` filters: `actor_user_id`, `resource_type`, `resource_id`, `action`, `from`, `to`, `ip_prefix`, `session_id`, `request_id`; cursor-paginated.
- Index coverage per §21.1 (`(actor_user_id, created_at desc)`, `(resource_type, resource_id)`, `(created_at)`).
- `POST /api/admin/audit/export` kicks off an Arq job → CSV in MinIO `exports/` → signed URL in response.
- Visibility **Admin only** (R17.02). There is no Org-scoped audit surface.

**Key IDs.** §17.1, §22.13.

**Exit criteria.** Typical filter returns < 300 ms at 10 M rows (bench); export completes in background.

## I.3 Notifications — **CODE** — S

**Deliverables.**

- Tables already in §21.1 (`notifications`). No `notification_prefs` (R18.01 — in-app only, no channel selection).
- Endpoints (§22.12):
  - `GET /api/notifications` (paginated).
  - `POST /api/notifications/read` `{ids:[…]}`.
- **Kinds** (R18.02 — exact):
  - `key.usage_threshold` — key usage ≥ 80% of any hourly limit (to users with usage-view permission).
  - `key.test_failed` — upload or retest failed.
  - `invite.received` — Org or Project invite arrived.
  - `approval.human_requested` — reserved; v1 approvals are agent-only per §15.4 (not fired in v1).
  - `admin.ban_reason` — delivered on next login for the banned user, surfaced once on render.
- Delivery: persist to `notifications` + push on WS channel `/ws/user/{user_id}` (R18.03). Bell badge reads unread count.
- **No email, webhook, or Slack** in v1 (R18.01).

**Key IDs.** `[R18.01]`–`[R18.03]`, §22.12.

**Exit criteria.** Each kind triggers correct row + WS push; unread count accurate.

## I.4 Retention workers — **CODE** — L

**Deliverables.** One Arq cron worker per policy; each idempotent, Redis-locked, and audited with `rows_affected`:

| Class | Policy | Source |
|---|---|---|
| `messages` | hard-delete older than 5 y | R13.15 / R13.25 (F.8) |
| `message_attachments` | MinIO lifecycle 3 d unless pinned to a live message | R13.10 |
| `exports` bucket | 24 h lifecycle | §21.5 |
| `audit_logs` | hard-delete older than 365 d (trigger allowlists this job) | R17.01 |
| `workflow_runs` → `workflow_runs_archive` | move after 90 d (steps dropped) | §21.1 comment + H.6 |
| `key_usage_events` | > 13 mo aggregated to `key_usage_daily`, raw rows deleted | R7.13 |
| soft-deleted tenancy / agents / workflows / chatrooms | hard-delete after 60 d | R8.12 / R9.03 |
| `invites` | `state='pending' AND expires_at < now()` → `state='expired'` | §21.1 |
| `original_creator_transfers` | `resolved_at IS NULL AND expires_at < now()` → `state='expired'` | R8.16 step 4 |
| `approvals` | `state='pending' AND started_at + timeout_seconds < now()` → `timeout_leader` + leader resolution | R15.13 |
| `email_verify_tokens / password_reset_tokens` | delete expired | R6.02 / R6.05 |
| `sessions` | prune idle > 30 d | §21.1 |
| `instructions` chain sweep | remove chains with terminal state older than audit retention window | §21.1 |
| `agent_instances` | delete 30 d after `destroyed_at` | R15.21 |
| incomplete tus uploads | 24 h abandoned cleanup | R22.15.04 |
| `admin_impersonation_sessions` | auto-close sessions idle > 30 min | §I.6 |

- Each worker emits an audit summary (e.g. `retention.messages.swept {count: N}`).
- Cutoffs overridable via runtime config (`rate_limit_policies` style key/value table §21.1).

**Key IDs.** §20, §13.15, §13.25, §17.01, §7.13, §8.12, R9.03, R15.21, R22.15.04.

**Exit criteria.** Fast-forward clock tests for each class pass; no worker deletes non-eligible rows; nightly `audit_logs` retention job authorised through trigger allowlist.

## I.5 Impersonation (view-as) — **CODE** — S

**Deliverables.**

- `POST /api/admin/users/{id}/impersonate` creates `admin_impersonation_sessions` row, issues a scoped JWT with `impersonated_by=<admin_user_id>` claim, and writes audit `admin.view_as_started`.
- Session is **read-only**: middleware rejects non-GET requests when claim present (R16.04).
- Target user gets an in-app `notification` (kind `admin.impersonation_started_on_self` — internal; this is outside the user-visible R18.02 set because R18.02 explicitly scopes admin notifications to ban reason only; the target instead sees a persistent banner enforced by the frontend via the `impersonated_by` claim rendered against their own session).
- Auto-expire 30 min without activity; `end-impersonate` closes session + audit `admin.view_as_ended`.

**Key IDs.** `[R16.04]`, §21.1 `admin_impersonation_sessions`.

**Exit criteria.** Write via impersonated JWT → 403; auto-close fires.

## I.6 Observability & dashboards — **OPS** — M

**Deliverables.**

- Ops stack per §4 / `docs/operations.md` §1: OpenTelemetry SDK → OTLP collector → Grafana Tempo / Loki / Prometheus (optional, structural).
- `deploy/observability/grafana/dashboards/smap.json` panels:
  - Requests, latency, error rate per route group.
  - DB / Redis / Qdrant / Neo4j / MinIO / Vault health.
  - Rate-limit hits per bucket.
  - Provider calls + key-group exhaustion + usage threshold events.
  - GraphRAG build state over time.
  - Workflow runs by state; SEL budget overflows; linter issues per rule.
  - A2A throughput + DLQ size; wake-up fires per kind.
  - Admin impersonation sessions active.
- Optional `deploy/compose/docker-compose.obs.yml` overlay for local metrics.

**Key IDs.** §4, `docs/operations.md` §1.

**Exit criteria.** Dashboards render with live data; key panels move under a scripted load.

## I.7 Runbook drills — **OPS** — M

**Deliverables.** Drill each `docs/operations.md` §7 runbook on a scratch environment and append observed timings:

- Vault sealed → unseal → backend reconnect.
- Key Group exhausted → admin rotate / revoke → recovery.
- GraphRAG stuck in transitional state → `/api/admin/graphrag/{id}/reset` or reconciliation observation.
- Disk fill → top-table lookup → lifecycle lever → recovery.
- Postgres restore from snapshot (operator level; DR out-of-scope per R20.09 but restore procedure remains).

**Key IDs.** `docs/operations.md` §7.

**Exit criteria.** All five drills executed; notes committed.

## I.8 Per-tenant advisory snapshots — **CODE** — S

**Deliverables.**

- Daily snapshot worker computes per-Org counts (users, chatrooms, agents, workflows, storage MB) by reading existing rows — **no new tables** (honours "no billing, no quotas" constraint).
- `GET /orgs/{id}/quotas` — returns current counts vs an operator-configurable soft advisory target (from the same `rate_limit_policies` runtime table's "advisory_*" keys).
- Never blocks an action; purely informational.

**Key IDs.** §20 (informational), R20.xx.

**Exit criteria.** Endpoint returns accurate snapshots; panel visible in admin dashboard.

## I.9 Frontend admin slice — **CODE** — L

**Objective.** `slices/admin/` (per §24.2).

**Deliverables.**

- Views: `AdminHomeView`, `AdminUsersView`, `AdminUserDetailView`, `AdminIpBansView`, `AdminAdminsView` (with last-Admin guard UI), `AdminOrgsView`, `AdminProjectsView`, `AdminAuditView` (rich filters + CSV export), `AdminImpersonateLauncher`, `AdminOpsView` (force rotate / reindex / rebuild / cancel run / graphrag reset), `AdminRateLimitsView`, `AdminMetricsView`.
- **Impersonation banner** — global component shown whenever session JWT contains `impersonated_by`; blocks all non-GET UI actions with toast + ESLint-tagged helper (R16.04).
- Audit export downloads signed URL.

**Key IDs.** §24.2, §22.13, §16.

**Exit criteria.** Playwright: disable user → impersonate → rotation → audit query → export.

## I.∞ Phase gate

- [ ] `/api/admin/admins` CRUD with last-Admin guard.
- [ ] `/api/admin/ip-bans` CRUD; middleware 403 enforced.
- [ ] `/api/admin/orgs/{id}/force-transfer-original-creator` live + audited.
- [ ] `/api/admin/graphrag/{id}/reset` live.
- [ ] `/api/admin/rate-limits/{key}` updates runtime.
- [ ] Retention workers exist for all 16 classes in I.4 with idempotency.
- [ ] Audit append-only trigger blocks impostor UPDATE/DELETE.
- [ ] Notifications: exactly the R18.02 kinds; no email/webhook/Slack surface.
- [ ] Grafana dashboards + 5 runbook drills green.
- [ ] Impersonation JWT read-only enforced.
- [ ] `00-overview.md` §0.8: I = done.

## Cross-cutting checklist

1. **AuthZ tap.** Every admin endpoint requires Admin (§5.2 rows 21–24); promote/demote requires confirmation; impersonated JWT limited to GET.
2. **Audit tap.** Full §17.1 Admin category: `admin.ban_user/unban_user/delete_user/restore_resource/view_as_started/view_as_ended`; plus `org.original_creator_force_transferred`, `admin.graphrag_reset`, `admin.rate_limit_patched`.
3. **Rate limit bucket.** `admin-read`, `admin-write`, `notifications-list`.
4. **Observability.** Dashboards per I.6.
5. **RFC 7807.** `https://smap.local/problems/{admin/last-admin, admin/impersonation-active-elsewhere, admin/impersonation-read-only}`.
6. **Migration policy.** Revisions `0017_rate_limit_policies`, `0018_notifications_index`, each N-1 compatible.
7. **Secrets.** No new secret storage.

## Risks

- **Retention lag.** Each worker publishes `last_run_at`; alert fires if > 25 h stale.
- **Audit query hot path.** EXPLAIN-gated CI test ensures indexes used; partition pruning keyed on `created_at`.
- **Notification spam.** `key.usage_threshold` is per-hour rate-limited to one per key per hour.
- **Impersonation abuse.** Every session audit-logged; admin demotion immediately closes active impersonation sessions.
