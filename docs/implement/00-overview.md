# SMAP Construction Plan — Overview

This directory breaks the full SMAP build (per `REQUIREMENTS.md`) into **ten phases A–J**. Each phase has its own file with numbered sub-steps, requirement-ID traceability, deliverables, and exit criteria.

- **Authoritative source.** `REQUIREMENTS.md` is the SRS; `docs/workflow.schema.json`, `docs/workflow.schema.md`, `docs/operations.md`, and `deploy/vault/README.md` are normative companions. This plan does **not** introduce new requirements — it only schedules and groups them.
- **No MVP cuts.** Full v1 is in scope. A phase may ship incrementally inside itself, but no feature listed in `REQUIREMENTS.md` is deferred past J.
- **Backend-first with contracted frontend.** Phases A–I deliver backend + ops + contracts. Phase J builds the web UI against stable APIs and closes E2E acceptance. Frontend slice skeletons may begin in parallel once a phase's OpenAPI contract is frozen.
- **Single-host target.** 16-core / 32 GB, Docker Compose. No Kubernetes, no multi-region.

## 0.1 Phase map

| Phase | Title | Backend themes | Frontend touch | Blocking for |
|---|---|---|---|---|
| **A** | Foundations & Project Bootstrap | Repo, DDD package tree, Docker Compose skeleton (all §25 services), settings, logging, CI | `slices/` skeleton | B, J |
| **B** | Infrastructure Bootstrap & Operations | Vault (Transit `smap-provider-secret / smap-guest-link / smap-jwt-sign`), Alembic baseline, bootstrap CLI, healthz/readyz, MinIO buckets (`chat-uploads / rag-sources / exports`) | — | C–I |
| **C** | Identity, Tenancy, Access & Web Security | Users + email-verify + sessions + JWT (Transit, `kid` rotate), orgs (with default project + OC transfer), projects, invites, 24×6 matrix, IP bans, rate-limit buckets (R19.02), CSP/HSTS/CORS/CSRF/trust boundary, audit append-only | Auth + tenancy shell | D–I |
| **D** | API Key Management (BYO) | Envelope encryption, ordered Key Groups + rotation config + hourly sliding-window + 80% threshold, `key_projects` carry, search keys (one-active) | `slices/keys` | E |
| **E** | Agents, RAG, Graph RAG, MCP | Agents (versionless, §9.1 exact schema), RAG (Qdrant), Graph RAG (Neo4j+Qdrant 2PC, 1:1 with agent), MCP sandbox (gVisor + FastAPI egress proxy), built-in `file/web_search/code_exec` (Tavily first) | `slices/agents` (folds knowledge) | F, G |
| **F** | Chat & Real-time | Workspaces → chatrooms (§21.1 flag set), messages (`content_md/tsv/metadata`, hard-delete on manual), tus `/api/tus`, WS endpoints per §22.14 with `bearer.<token>` subprotocol, permanent guest links, export + FTS | `slices/conversation` | G |
| **G** | Multi-Agent Orchestration | Per-agent `a2a:agent:{id}` streams, R9.13 envelope, wake-up + self-modify + refresh, agent-only approval (`leader_agent_id`, single/majority/consensus), instruct depth=5 + loop detection, sub-agents (inheritance per R15.22), `key_usage_events.parent_agent_id` | Orchestration affordances | H |
| **H** | Workflow Engine | Versionless `workflows`, SEL v1 exact whitelist, 11 executors, 5 trigger kinds (no webhook), 14 linter rules from `workflow.schema.md`, `workflow_runs` FSM (`running/waiting/succeeded/failed/cancelled`), 90-d archive | `slices/workflow` | I |
| **I** | Admin, Audit, Notifications, Retention | `/api/admin/*` full set incl. `admins` CRUD with last-Admin guard + `ip-bans` + `force-transfer-original-creator` + `graphrag/reset` + `rate-limits PATCH`; retention 16-class worker matrix; in-app-only notifications (R18.02 kinds) | `slices/admin` | J |
| **J** | Frontend Integration, E2E & Release | Seven slices, dependency direction `conversation → agents → keys → tenancy → identity → shared`, 12 CI SoC gates, Playwright against `compose.test.yml`, bundle 250/200 KB, type-cov ≥95% | All slices finalised | — |

## 0.2 Dependency graph (informal)

```
A ──► B ──► C ──► D ──► E ──┬──► F ──► G ──► H ──► I ──► J
                            │
                            └──► (J may begin skeleton from A)
```

- **Hard dependencies.** B needs A; C needs B (Vault + DB + JWT Transit); D needs C + Vault; E needs D (LLM / embed / rerank / search keys); F needs C + MinIO; G needs E + F; H needs G; I is cross-cutting but hardened after H; J consumes all APIs.
- **Soft dependencies.** Frontend skeleton (J.1–J.4) can start from A. Retention workers (I.4) and metrics (I.6) can begin during F/G/H.

## 0.3 Conventions

- **Requirement IDs.** Every sub-step lists the `[Rxx.yy]` IDs it implements. If an ID does not yet exist in `REQUIREMENTS.md`, add it there **before** writing code.
- **Problem-URL prefix.** All RFC 7807 `type` URLs use `https://smap.local/problems/…` (§19.06 / `docs/operations.md` §6). No `/errors/*` ad-hoc URLs.
- **Endpoint paths.** All REST paths carry the `/api/` prefix and match §22 exactly.
- **Storage names.** MinIO buckets: `chat-uploads / rag-sources / exports`. Redis streams: `a2a:agent:{agent_id}`. Vault keys: `smap-provider-secret / smap-guest-link / smap-jwt-sign`. Use these verbatim.
- **Traceability.** Commits, PRs, tickets cite sub-step code (e.g. `C6`, `H3`) plus ≥ 1 requirement ID. Tests reference IDs in their docstring.
- **Exit criteria.** Each phase ends with a gate. No phase is "done" until every sub-step's exit criterion is green. A phase-level gate checks integration across sub-steps.
- **Specs stay English.** All files under `docs/`, `deploy/`, and the root SRS remain English. Chat / PR descriptions may use zh-TW.

## 0.4 Cross-cutting concerns tracked in every phase

1. **AuthZ tap.** Every new endpoint goes through the permission matrix (§5.2) or is explicitly marked public (R19.01).
2. **Audit tap.** Every state-changing operation emits an audit event (§17.1 categories).
3. **Rate limit bucket.** Every endpoint belongs to a bucket in §19 (numeric budgets in R19.02).
4. **Observability.** Log fields (`docs/operations.md` §1.2), metrics counters, and traces added when the feature lands.
5. **RFC 7807 errors.** New error types registered in `docs/operations.md` §6 catalog when introduced; always `https://smap.local/problems/…`.
6. **Migration policy.** Any DB change follows Alembic N-1 compatibility (`docs/operations.md` §4).
7. **Secrets.** No secret in env beyond Vault `role_id / secret_id`; everything else via Vault.

## 0.5 Acceptance levels

Each sub-step declares one of:

- **CODE** — implementation + unit tests landed.
- **CONTRACT** — OpenAPI / JSON Schema frozen, stubs callable, no business logic yet.
- **OPS** — deployment artifact (compose file, policy, runbook) reviewed and applied in a scratch environment.
- **E2E** — end-to-end Playwright or integration test green against running stack.

Phase gates require all sub-steps at least at **CODE**; J gates require the affected sub-steps at **E2E**.

## 0.6 Non-goals of this plan

- Not a sprint plan. Estimates are in rough sizes (S / M / L / XL) only.
- Does not allocate people. The user directs engineers; this plan is engineer-agnostic.
- Does not supersede `REQUIREMENTS.md`. If the two disagree, the SRS wins; the plan is updated.

## 0.7 How to use this plan

1. Start at `A-foundations.md`. Close every sub-step and its exit criterion before opening the next phase file.
2. Cut a feature branch per sub-step (`feat/C7-org-lifecycle`). Merge into `main` only when that sub-step's exit criterion is green.
3. When a requirement changes mid-flight, edit `REQUIREMENTS.md` first, then update the affected sub-step entry here.
4. At each phase close, run the phase gate checklist at the bottom of the file and tick it off in `00-overview.md` (this file) under §0.8.

## 0.8 Phase gate status

| Phase | Status | Date closed | Notes |
|---|---|---|---|
| A | ☐ not started | — | |
| B | ☐ not started | — | |
| C | ☑ CODE complete | 2026-04-21 | Migrations 0001–0004, identity/tenancy/audit contexts, middleware stack, `/api/auth` `/api/orgs` `/api/projects` `/api/invites` `/api/csp-report` wired. Nginx TLS terminator + self-signed cert bootstrap (C.11 OPS) added. Integration tests land in `backend/tests/integration/` (144-case matrix, trust boundary, §19a.2 headers + CSP toggle, password policy, rate-limit bucket mapping, IP-ban cache). Frontend C.15 scaffolded: 8 identity + 8 tenancy views, shared transport with silent-refresh, route guards. E2E Playwright + live-stack integration (real DB/Redis) deferred to Phase J gate. |
| D | ☑ CODE complete | 2026-04-21 | Migrations 0005–0010 (api_keys, key_projects, key_groups + key_group_members with DEFERRABLE priority unique, key_usage_events partitioned by `at`, search_keys partial-unique-active, rewrap_progress). `shared_kernel/security/envelope.py` + version-tracked `EnvelopeRecord` + `rewrap_dek`. 5 provider probes (claude/openai/gemini/voyage/cohere) with secret-scrubbed error summaries; 4 search probes (brave/serper/tavily/google_cse). 23 endpoints across `/api/keys`, `/api/projects/{}/keys`, `/api/key-groups`, `/api/projects/{}/search-keys`. Provider router with per-member retry budget (R7.07 defaults baked in), quota-queue 60s vs error-exhaust split, in-process DEK cache + Redis pub/sub revocation fanout (`key.revoked` / `key.carry_revoked`). `shared_kernel/infra/redis_buckets.py` Lua aggregator for 1-min × 60 sliding-window; 30s threshold worker emitting `key.usage_threshold_hit`. `smap.rotation rotate-transit` operator CLI with resumable chunked rewrap (plaintext DEK never leaves Vault). Frontend `slices/keys` with 6 views, 4 composables, vee-validate+Zod upload form, native drag-reorder with optimistic + server-rollback. Unit tests: envelope security, R7.01 golden (BE + FE mirror), 5 probe adapters, router policy (classify_http + backoff jitter band). Follow-ups: search `activate` IntegrityError→`SearchActivationConflict` translation; Phase I.3 notification sink for threshold worker; Phase J Playwright E2E (upload→carry→group→reorder→retest→withdraw→delete). |
| E | ☐ not started | — | |
| F | ☑ CODE complete | 2026-04-23 | Migrations 0015–0018 (workspaces, chatrooms + chatroom_agents/guests + version trigger, messages + message_edits + message_attachments + content_tsv trigger, attachments chatroom FK). Contexts: workspace/chatroom/message/tus/attachment/guest/export/retention services. WS: five separate endpoints (`/ws/user`, `/ws/chatroom`, `/ws/workflow-runs`, `/ws/rag-configs`, `/ws/admin/tail`) with subprotocol bearer auth + in-socket refresh. Hard-delete on manual (R13.16); 5-year nightly retention purge. tus `/api/tus` with 16 MB chunk cap, 24h abandoned TTL. Guest links permanent, no revoke (R6.12/R13.07). FTS via `content_tsv` GIN + `ts_rank_cd`. Export worker → MinIO `exports` bucket (24h lifecycle). Frontend `slices/conversation/`: WorkspaceListView, ChatroomListView, ChatroomView, ChatroomSettingsView, GuestLandingView; `useChatroomSocket` sole WS subscriber; tus-client upload with pause/resume; `renderMarkdown.ts` single `v-html` site (markdown-it → DOMPurify → KaTeX/Mermaid/hljs). Bugs fixed: WS close-before-accept for per-user cap (close code 1013 now sent after accept); Mermaid SVG sanitized with DOMPurify before innerHTML insert; five F-scope Prometheus metrics added (`ws_connections_active`, `ws_per_user_rejections_total`, `tus_upload_bytes_total`, `message_sanitize_rejections_total`, `export_jobs_total`). |
| G | ☐ not started | — | |
| H | ☐ not started | — | |
| I | ☑ CODE complete | 2026-04-23 | All `/api/admin/*` endpoints (users, ip-bans, admins CRUD with last-admin guard, orgs, projects, audit, restore, metrics, rate-limits, graphrag reset, impersonation). Audit append-only trigger (0004) + retention-role bypass. 16 retention workers: messages 5y, attachments 24h, exports 24h, audit 365d, workflow_runs→archive 90d, key_usage_events 13mo rollup, soft-deleted tenancy/agents/workflows/chatrooms 60d, invites/transfers/approvals expiry, token cleanup, sessions 30d, instructions chain sweep, agent_instances 30d, tus parts 24h, impersonation sessions 30min. Notifications wired: ban_reason (ban_user), invite.received (invite create), key.test_failed (upload/retest), key.usage_threshold (threshold worker, Redis dedup). Impersonation read-only JWT in middleware. Grafana dashboard JSON + OTel stack. 5 runbook drills with timings. Frontend admin slice: 12 views + impersonation banner. Bugs fixed: PostgreSQL DELETE…LIMIT syntax (subquery form); 3 missing retention workers added (exports_bucket, instructions_chains, tus_parts); notification sends were not wired (now called from 4 service methods). |
| J | ☐ not started | — | |

Update this table at every gate close.
