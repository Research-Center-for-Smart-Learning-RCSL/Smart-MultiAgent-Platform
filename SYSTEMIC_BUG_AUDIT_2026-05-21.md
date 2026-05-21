# SMAP — Systemic Bug Audit

**Date:** 2026-05-21
**Scope:** Full codebase — `backend/` (436 Python files) and `frontend/` (304 TS + 59 Vue files)
**Method:** 6 parallel review agents, each assigned a non-overlapping domain, instructed to hunt **systemic** defects (recurring patterns + cross-cutting architectural flaws), trace real call paths, and report with `file:line` evidence. Overlapping findings reported by multiple agents have been merged.

---

## Executive Summary

The codebase is well-structured (DDD bounded contexts, typed transport, envelope encryption, RS256-pinned JWT) and the auth *primitives* are sound. The defects are not in the primitives — they are in **how cross-cutting concerns are wired**. The same root causes recur across contexts because the project relies on per-file *convention* rather than enforced shared abstractions.

The most severe issues, in order of urgency:

1. **Egress proxy SSRF** — screened IPs are never pinned to the outbound socket; cloud metadata / internal services are reachable from inside agent sandboxes via DNS rebinding.
2. **Three background services are defined but never started** — the A2A inbox consumer, the key-revocation cache invalidator, and the GraphRAG reconciler. A2A messaging is entirely dead; **revoked BYO keys keep working** because cached DEKs are never invalidated.
3. **Two whole API routers (workflow, orchestration) have no authorization at all** — cross-tenant IDOR on dozens of read/mutate endpoints.
4. **Workflow transaction ownership is broken** — the run engine self-commits inside request-managed transactions; background jobs are dispatched before the owning transaction resolves.
5. **RAG/GraphRAG delete endpoints orphan data** — soft-deleting a config leaks all child documents, chunks, Qdrant vectors, Neo4j graphs and MinIO blobs; "deleted" knowledge resurfaces in retrieval.

**68 findings (post-verification): 11 Critical · 22 High · 21 Medium · 14 Low.**

> **Verification pass (2026-05-21):** every finding was independently re-checked against the source by a second set of reviewers (one per domain). **57 confirmed as written; 10 partially corrected (severity or mechanism adjusted — see §1a); 1 withdrawn as a false positive (DOM-11).** All counts and severity tags below are post-verification.

| Area | Critical | High | Medium | Low |
|---|---|---|---|---|
| Security & Multi-Tenancy | 1 | 3 | 3 | 2 |
| Persistence & Data Integrity | 1 | 3 | 2 | 3 |
| API Layer & Contracts | 2 | 3 | 5 | 1 |
| Async / Workers / Realtime | 3 | 4 | 3 | 3 |
| Domain Logic (Agents/Conv/Knowledge/Audit) | 3 | 4 | 3 | 2 |
| Frontend | 1 | 5 | 5 | 3 |
| **Total** | **11** | **22** | **21** | **14** |

---

## 1. Cross-Cutting Systemic Themes

The 69 findings collapse into **10 root causes**. Fixing the root cause fixes a cluster of findings at once.

### Theme A — Authorization is enforced ad-hoc per route, with no enforced contract
Every context calls `decide(...)` / `require_membership(...)` by hand in each route handler. Some routers skip it entirely; the shared role resolver *fails open* on an empty scope.
→ SEC-2, SEC-3, SEC-4, SEC-7, API-1, API-2, DOM-8. **The single highest-leverage fix is to make authorization a non-optional, scope-aware dependency.**

### Theme B — Transaction ownership is inconsistent; "best-effort" code poisons live transactions
Some code self-commits, some relies on the request dependency; "swallow the exception" code ignores that a failed statement poisons the whole Postgres transaction.
→ DB-1, DB-2, DB-3, DOM-4, DOM-3 (reconciler).

### Theme C — Background services are defined but nothing launches them
There is no service registry / lifespan wiring contract. Code that *must* run as a long-lived task simply never starts.
→ ASYNC-1, ASYNC-2, ASYNC-3, ASYNC-5 (and the GraphRAG reconciler).

### Theme D — Keyset pagination tie-breaks on random UUID columns
Cursor pagination orders/tie-breaks on `gen_random_uuid()` columns, which have no time correlation → rows skipped/duplicated.
→ DB/DOM merged finding **DATA-PAGINATION** (messages + notifications).

### Theme E — RAG/GraphRAG delete endpoints do not cascade to external stores
The recently added delete endpoints clean some stores but not others; per-project Qdrant/Neo4j collections are not config-scoped.
→ DOM-1, DOM-2, DOM-4, DOM-9.

### Theme F — Copy-pasted code drifts into divergent bugs
The same logic is duplicated across files instead of shared; copies omit a line or use a stale assumption.
→ API-3 (8× `error_mapping.py`), FE-1 (4× keys-slice error extraction), FE-3 (socket composables).

### Theme G — Frontend reactivity / lifecycle conventions applied inconsistently
Route params captured as constants; subscription cleanup omitted; per-entity state held in app-global singletons.
→ FE-2, FE-3, FE-4, FE-5, FE-8.

### Theme H — Resource leaks on hot paths
Pools/connections/tasks created per-call and never closed; sockets with no idle timeout.
→ ASYNC-6, ASYNC-7, FE-3, FE-4.

### Theme I — Unbounded inputs are validated *after* being buffered into memory
Size caps exist but are checked after the full body/field is already in RAM.
→ API-4, API-8, API-11.

### Theme J — Side effects on non-transactional stores escape DB atomicity
Qdrant / MinIO / Neo4j / Redis writes and audit rows are interleaved with DB commits in an order that breaks atomicity.
→ DB-1, DOM-4.

---

## 1a. Verification Corrections

Every finding was independently re-verified against the source. The following were adjusted; all others stand exactly as written.

**Withdrawn — false positive:**
- **DOM-11** (retention purge processes only one chunk) — **WITHDRAWN.** The audit examined `RetentionService.purge_once` in isolation and assumed the cron calls it once. The real cron task `retention_purge` (`backend/app/workers/tasks/conversation.py:76-81`) loops `purge_once` up to 100× per nightly run (≈50,000 rows), stopping when a slice deletes 0. No retention compliance gap exists.

**Partially corrected — core finding stands, detail fixed:**
- **DB-1** — core bug (engine self-commit + endpoint double-commit on the API path) **confirmed Critical**. Two sub-claims dropped: `_dispatch_enqueues` does *not* fire jobs for a non-existent run (the engine commits the run first), and there are *no* missing-commit paths leaving runs stuck — every live caller of `cancel_run`/`resume_at_port`/`retry_node` commits.
- **DATA-PAGINATION** — rescoped to **notifications only**. The messages half is a false positive: `MessageRepository.list` already uses a correct composite keyset (`(created_at, id)` total order) — it cannot skip/duplicate rows. Severity stays High (notifications).
- **DB-5** — split-mechanism observation stands, but the "double-bump on trigger tables" sub-claim is false: the `smap_bump_version` trigger is guarded (`IF NEW.version IS NOT DISTINCT FROM OLD.version`), so a manual bump is idempotent-safe. **Severity Medium → Low.**
- **API-11** — **more severe than reported. Severity Low → High.** `TokenPair` is a `@dataclass(slots=True)`, so it has no `__dict__` — `outcome.tokens.__dict__` raises `AttributeError` on *every* call. `/api/auth/login` and `/api/auth/refresh` return an unhandled 500 on the happy path **right now**, not "if a field is added."
- **ASYNC-11** — **Severity Medium → Low.** `subprocess.run` *does* reap the child on `TimeoutExpired` (`kill()` + `communicate()` internally) — no zombie pile-up. The real residual issue is only the missing TTL cache on `_check()`.
- **DOM-2** — orphan-vector leak **confirmed Critical**. Mechanism nuance: `search_entities` *does* support a `build_id` filter; the defect is that `GraphRagRetrieveService.query` never passes one.
- **FE-1** — **Severity Critical → High.** Only 2 of the 4 cited keys composables are fully broken (`useKeyGroups.ts`, `useSearchKeys.ts`). `useMyKeys.ts`/`useProjectKeys.ts` use an `e instanceof Error` branch that *does* surface the real message via `ApiError.message`.
- **FE-3** — **Severity High → Low.** The claimed leak mechanism is wrong: `onBeforeUnmount` calls `wsManager.close(path)`, which clears all channel handlers and evicts the channel — no stale handler survives a normal unmount. The discarded unsubscribe values are dead code, not an active leak.
- **FE-9** — finding stands (non-`token-expired` 401s skip silent refresh), but the logout is performed by `app/errorHandler.ts`, not `transport/axios.ts`. Severity Medium unchanged.
- **FE-13** — no present defect: all current call sites are component-scoped and `decodePayload` is not exported. Latent style note only; Low.

**Scope/severity nuances noted by verification (no count change):** SEC-3's blast radius is narrower than the capability list implies — only empty-scope endpoints are affected, and SEC-2 is the lone realized case. SEC-4 and SEC-7 are layered-AuthZ / hardening gaps not exploitable today.

---

## 2. Detailed Findings

Severity legend: **Critical** = exploitable now / data loss / security control absent · **High** = serious correctness or security weakness · **Medium** = real bug, bounded blast radius · **Low** = minor / latent.

*All findings below were independently re-verified against source (2026-05-21). Severity tags and bodies reflect the verified result; findings carrying a `> **Verified — …**` note were adjusted (see §1a).*

---

### 2.1 Security & Multi-Tenancy

#### [Critical] SEC-1 — Egress proxy SSRF: screened IPs never pinned to the outbound connection
- **Locations:** `backend/services/egress_proxy/app.py:97-107, 167-237, 259-270`
- **Systemic:** No — single service, but it is the *sole* network exit for every sandboxed agent.
- **Evidence:** The proxy resolves the host with `socket.getaddrinfo`, screens those IPs with `is_blocked_ip`, re-resolves once for a rebinding check, then calls `httpx.AsyncClient(...).send(req, url=forward_url)` passing the **hostname** — `httpx` performs its own independent DNS resolution at TCP-connect time. None of the three prior resolutions is bound to the socket actually used.
- **Impact:** An attacker controlling DNS for an allowlisted hostname (short TTL) returns a public IP for the proxy's checks and `169.254.169.254` / `127.0.0.1` / `10.x` for httpx's connect-time lookup — reaching cloud metadata (IAM credentials), internal services, and the SMAP database from inside a sandbox.
- **Fix:** Resolve once, screen, then connect to the validated **IP literal** (preserve `Host`/SNI), or use a custom `httpx` transport that pins the connection to the pre-screened address set.

#### [High] SEC-2 — Broken access control: any user can create a project owned by an arbitrary other user
- **Locations:** `backend/app/api/v1/projects.py:115-155`, `backend/contexts/tenancy/application/project_service.py:41-63`
- **Evidence:** `create_project` checks `decide(principal, PROJECT_CREATE_UNDER_USER, Scope(org_id=None), resolver)`. With an empty scope the resolver fallback (SEC-3) grants `PROJECT_MEMBER` → ALLOW. `body.owner_id` is then passed straight to `ProjectService.create` as `owner_user_id` with **no check that it equals `principal.user_id`**.
- **Impact:** Any verified user can create projects owned by a victim; the victim's project list surfaces them — data pollution / integrity violation.
- **Fix:** For `owner_type="user"`, require `body.owner_id == principal.user_id` (or admin); pass the principal's id, not the body's.

#### [High] SEC-3 — Role-resolver empty-scope fallback grants a default `PROJECT_MEMBER` role
- **Locations:** `backend/contexts/tenancy/interfaces/role_resolver.py:64-69`, consumed by `backend/shared_kernel/auth/permissions.py:293-328`
- **Systemic:** Yes — the shared role-resolution path behind every `require(...)` / `decide(...)`.
- **Evidence:** `roles_for` ends with `if not roles and scope.org_id is None and scope.project_id is None and scope.chatroom_id is None: roles.add(Role.PROJECT_MEMBER)`. Any path reaching `decide()` with an unpopulated scope treats a non-member as `PROJECT_MEMBER`. `scope_from_path` returns `None` for missing params and `_uuid()` silently returns `None` on malformed input — so a builder misconfiguration degrades silently to "authenticated = PROJECT_MEMBER".
- **Impact:** Latent privilege grant across the whole capability matrix (`KEY_UPLOAD`, `KEY_DELETE_OWN`, `ORG_CREATE`, `PROJECT_CREATE_*`, `CHAT_SEND`, `MESSAGE_DELETE`, …). SEC-2 is one realized instance.
- **Fix:** Make the self-service-org-creation case an explicit opt-in capability; have `decide()` **fail closed** when a scope-requiring capability receives an empty scope.

#### [High] SEC-4 — OC-transfer and quota endpoints lack a membership/scope guard at the HTTP layer
- **Locations:** `backend/app/api/v1/orgs.py:367-386` (`transfer_initiate`), `:399-416` (`transfer_accept`); `backend/contexts/tenancy/application/oc_transfer_service.py:34-131`
- **Evidence:** Unlike every other `/api/orgs/{org_id}/...` route, these declare no `require(...)` / `require_membership(...)`. Authorization depends 100% on in-service checks. `OCTransferService.accept` ignores the `org_id` path param entirely and trusts `transfer.org_id`. Not exploitable today, but the single most privileged tenancy operation (org-ownership transfer) has zero layered AuthZ — one service-side regression silently exposes it.
- **Fix:** Add `require_membership(org_param="org_id")` to both endpoints; assert `transfer.org_id == org_id`.

#### [Medium] SEC-5 — Key-withdraw endpoint is unusable: `OWN_ONLY` cannot resolve without `resource_owner_user_id`
- **Locations:** `backend/app/api/v1/project_keys.py:95-119`, `backend/shared_kernel/auth/permissions.py:306-309`
- **Evidence:** `withdraw_key` gates on `KEY_DELETE_OWN` (`OWN_ONLY`); `decide()` only allows `OWN_ONLY` when `scope.resource_owner_user_id == principal.user_id`, but `scope_from_path(project_param=...)` never sets that field — the check can never pass. `CarryService.withdraw` itself does no ownership check (comment defers it to HTTP). Latent: if the gate is later loosened, anyone passing it can withdraw *any* key.
- **Fix:** Populate `resource_owner_user_id` (resolve the key owner first), or move the own-vs-other decision into `CarryService.withdraw`.

#### [Medium] SEC-6 — Provider `test_error` stored in audit metadata, only shape-redacted
- **Locations:** `backend/contexts/keys/application/key_service.py:118-130, 172-187`, `backend/shared_kernel/audit.py:62-90`
- **Systemic:** Yes — every key/search-key probe writes `test_error` into the DB row and `AuditEvent.metadata`; `audit.redact()` is the single shared scrubber.
- **Evidence:** `redact()` only redacts values matching five fixed secret-shape regexes (`sk-ant-…`, `sk-…{40,}`, PEM, `AKIA…`, `AIza…`). A provider whose key format is none of those (Cohere, Voyage, future providers), or an error embedding a short key fragment, is stored verbatim in `audit_logs.metadata`, readable by any admin via `/api/admin/audit` + CSV export.
- **Fix:** Store only `status` + a coarse category in audit metadata; never the raw `test_error`.

#### [Medium] SEC-7 — JWT verification ignores `typ` and does not bound `iat`; 5s expiry leeway
- **Locations:** `backend/shared_kernel/infra/vault.py:282-311`, `backend/shared_kernel/auth/jwt.py:94-142`
- **Evidence:** `verify_access_token` is the one verification path for HTTP middleware, WS handshake, and in-socket refresh. It pins `alg=RS256` (good) but never checks `typ=="JWT"`, never sanity-checks `iat` (future `iat`, or `iat >= exp`). Any other artifact signed by the same Vault transit key would pass signature verification.
- **Fix:** Assert `header["typ"] == "JWT"`; reject future `iat` and `iat >= exp`; add a `token_use` claim checked per consumer; reduce `_LEEWAY`.

#### [Low] SEC-8 — Email-verification and password-reset tokens transmitted as URL query parameters
- **Locations:** `backend/contexts/identity/application/auth_service.py:577-597`
- **Evidence:** Links built as `{public_origin}/api/auth/verify-email?token={token}` and `/reset-password?token={token}`. High-entropy single-use tokens in a query string land in browser history, `Referer` headers, and intermediary access logs.
- **Fix:** Deliver the token in the URL fragment (`#token=`) and POST it from the SPA, or use a POST landing form.

#### [Low] SEC-9 — `_kill_family_by_hash` runs an unbounded Redis `SCAN` on every unrecognized refresh token
- **Locations:** `backend/shared_kernel/auth/tokens.py:82-94, 190-203`
- **Evidence:** On an unrecognized refresh token, `rotate_session` SCANs the entire `session_family:*` keyspace with a `SISMEMBER` per family. `POST /api/auth/refresh` with random tokens (per-IP rate limit only) repeatedly triggers a full keyspace scan.
- **Fix:** Maintain a `hash → family_id` index (single `GET`); drop the keyspace `SCAN`.

> **No findings:** crypto (Vault Transit envelope, per-row AAD, `hmac.compare_digest`), hardcoded secrets, timing attacks on secret compare, keys-repo tenant isolation — all sound.

---

### 2.2 Persistence & Data Integrity

#### [Critical] DB-1 — Workflow engine self-commits inside request-managed transactions (double-commit on the API path)
*Independently flagged by the persistence, API, and async agents.*
> **Verified — core bug confirmed Critical; two sub-claims dropped.** `_dispatch_enqueues()` does *not* fire jobs for a non-existent run (the engine commits the run first), and no missing-commit path leaves runs stuck — every live caller of `cancel_run`/`resume_at_port`/`retry_node` commits. The remaining, confirmed defect is the engine self-commit + endpoint double-commit.
- **Locations:** `backend/contexts/workflow/application/run_engine.py:173,177,180-181,221,247-248`; `backend/app/api/v1/workflows.py:239,272,288,312,359`; `backend/shared_kernel/db/session.py:92-96`
- **Systemic:** Yes — `RunEngine` is invoked from two call paths with conflicting transaction ownership. Worker tasks use `async_session()` (caller owns commit). API endpoints use `Depends(db_session)`, which already wraps the request in `async with sm() as session, session.begin()`.
- **Evidence:** `RunEngine.start_run`/`resume_step` call `await self._db.commit()`/`rollback()` themselves. The workflow endpoints then call `await db.commit()` again, and the `session.begin()` context manager performs a further commit on `__aexit__` against a transaction that is no longer the active one — fragile/double-commit semantics on every workflow create/patch/delete/trigger/cancel request. Contrast `agents.py`/`chatrooms.py`, which never call `db.commit()` and let the dependency own the single commit.
- **Impact:** Double/inconsistent commit on the API path; depending on autobegin state the trailing context-exit can raise after the write already succeeded, surfacing as a generic 500.
- **Fix:** Establish one rule — engine methods **never** commit/rollback; every entry point (API endpoint or Arq task) owns the transaction and commits exactly once. Audit all public engine methods against the contract.

#### [High] DB-2 — `record_usage_event` swallows DB errors, poisoning the request transaction
- **Locations:** `backend/contexts/keys/infrastructure/usage_events.py:37-54` (called from `provider_router.py:309,333`)
- **Systemic:** Yes — "best-effort, never re-raise" on the hot path of every provider call, sharing the request `AsyncSession`.
- **Evidence:** `record_usage_event` does `await db.execute(insert(...))` inside `try/except Exception: log`. If the INSERT fails (FK violation from a concurrently hard-deleted `key_id`, statement timeout), Postgres puts the transaction in a failed state. Swallowing the Python exception does **not** clear it — every later `db.execute` raises `InFailedSqlTransaction`/`PendingRollbackError`.
- **Impact:** A failed usage-event insert silently corrupts the caller's transaction; the user-facing operation then fails with an opaque error.
- **Fix:** Write usage events on a separate short-lived session, or wrap the insert in a `SAVEPOINT` (`async with db.begin_nested()`).

#### [High] DB-3 — `EgressAllowlistRepository.upsert` runs a SELECT on an already-poisoned transaction
- **Locations:** `backend/contexts/agents/infrastructure/mcp_repositories.py:54-81`
- **Evidence:** `try: insert(...).returning(...) except IntegrityError: existing = await self._db.execute(select(...))`. After an `IntegrityError` the transaction is in a failed state; the recovery `SELECT` itself raises `InFailedSqlTransaction` (the insert is not in a savepoint).
- **Impact:** The "idempotent upsert on unique collision" never returns the existing row — it raises a confusing secondary error and the whole request transaction is dead.
- **Fix:** Use `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` (as `KeyProjectRepository.upsert_carry` already does), or wrap the insert in `begin_nested()`.

#### [High] DATA-PAGINATION — Notification keyset pagination tie-breaks on a random UUID column
> **Verified — rescoped to notifications only.** The messages half was a false positive: `MessageRepository.list` already implements a correct composite keyset (`(created_at, id)` is a deterministic total order — it cannot skip or duplicate rows; tied messages merely display in id-order). The notification bug is real.
- **Locations:** `backend/contexts/notification/infrastructure/repositories.py:85-101` (`list_for_user`); `notifications.id` is `pg.UUID` defaulted by `gen_random_uuid()`.
- **Systemic:** Partly — the cursor idiom is reused across repos, but only this one mismatches its ordering column. `AuditRepository` and `MessageRepository` page correctly.
- **Evidence:** `list_for_user` orders by `created_at DESC` but pages with `WHERE id < cursor`. Random v4 UUIDs have no time correlation, so `id < cursor` filters an arbitrary subset unrelated to the `created_at` ordering.
- **Impact:** Notification feeds silently drop/duplicate rows and may never reach older entries.
- **Fix:** Page on `(created_at, id)` with a composite cursor, or change the cursor column to a sortable type.

#### [Medium] DB-4 — Audit-log retention `SET ROLE smap_audit_retention` will be denied — no GRANT exists
- **Locations:** `backend/app/workers/tasks/retention.py:103-118`; role created `NOLOGIN NOINHERIT` in `backend/alembic/versions/0004_audit.py:57-67`
- **Evidence:** `_purge_audit_logs` runs `SET ROLE smap_audit_retention` to bypass the append-only trigger. Postgres allows `SET ROLE` only to a role the session user is a member of. Migration 0004 comments "DO NOT grant it to the backend AppRole" and no `GRANT … TO <app_user>` exists anywhere.
- **Impact:** If the retention worker uses the standard app DSN, `_purge_audit_logs` raises `permission denied to set role` every night — audit logs are never purged (`report["audit_logs"] = -1`).
- **Fix:** Grant membership to the worker's DB role as a documented ops step (reference it from the migration), or run the worker under a dedicated DSN.

#### [Medium] NOTIF-DEDUP — `NotificationService.send` dedup is a check-then-act race with no backing constraint
*Merged: persistence agent + async agent.*
- **Locations:** `backend/contexts/notification/application/notification_service.py:34-53`, `backend/contexts/notification/infrastructure/repositories.py:35-83`
- **Systemic:** Yes — `send` is the single entry point for all in-app notifications.
- **Evidence:** Dedup is a non-atomic `SELECT find_recent(...)` (60s window, matched on `user_id + kind + title`) then `INSERT`, with no unique constraint and no idempotency key. Two concurrent `send` calls (same event delivered twice, or two workers) both see nothing and both insert + both emit a WS push. Title-only matching also wrongly suppresses legitimately distinct notifications that share a title.
- **Fix:** Add an explicit idempotency/dedup key (e.g. source event id) with a partial unique index + `INSERT ... ON CONFLICT DO NOTHING`; gate the WS emit on whether a row was actually inserted.

#### [Low] DB-5 — Optimistic-locking strategy is split between a DB trigger and application code
> **Verified — downgraded Medium → Low.** The split-mechanism observation stands, but the "double-bump on trigger tables" sub-claim is false: the `smap_bump_version` trigger is guarded (`IF NEW.version IS NOT DISTINCT FROM OLD.version`), so a manual `version+1` is idempotent-safe.
- **Locations:** Trigger tables: `users/orgs/projects` (`0002_tenancy.py:108-127`), `chatrooms` (`0016`), `messages` (`0017`). App-bumped: `agents` (`agents/infrastructure/repositories.py:176,228`), `workflows` (`workflow/infrastructure/repositories.py:137`).
- **Evidence:** `OrgRepository.rename`/`ProjectRepository.rename` filter on `version` but never increment it — they rely on the `smap_bump_version` trigger. `agents`/`workflows` have **no** such trigger, so they *must* bump in app code (they currently do). Two mechanisms for the same `version` column, undocumented and unenforced — the residual risk is a new method on the trigger-less `agents`/`workflows` tables that forgets to bump, silently breaking `If-Match` concurrency control.
- **Fix:** Pick one mechanism — simplest: add the `smap_bump_version` trigger to `agents`/`workflows` and remove all manual increments.
> **FIXED 2026-05-21.** Migration `0029_version_bump_triggers.py` adds `trg_agents_bump_version` + `trg_workflows_bump_version` (reusing `smap_bump_version()`). The trigger is now the single mechanism for every versioned table; the redundant manual `version+1` increments are removed from `agents` (`patch`, `soft_delete`), `workflows` (`update`), `chatrooms` (`update`) and `messages` (`update_content`). `WorkflowRepository.update` gained an empty-patch guard so dropping the version seed cannot produce an empty `SET`.

#### [Low] DB-6 — `rag_chunks` ORM table omits the `uq_rag_chunk_doc_idx` unique constraint defined in the migration
- **Locations:** `backend/contexts/knowledge/infrastructure/tables.py:86-99` vs `backend/alembic/versions/0012_rag.py:138`
- **Evidence:** Migration 0012 declares `UniqueConstraint("document_id","chunk_idx", name="uq_rag_chunk_doc_idx")`; the ORM table does not. With `compare_type=True` in `env.py`, an autogenerate run would emit a spurious `drop_constraint`.
- **Fix:** Add the matching `UniqueConstraint` to the ORM table.
> **FIXED 2026-05-21.** `sa.UniqueConstraint("document_id", "chunk_idx", name="uq_rag_chunk_doc_idx")` added to the `rag_chunks` ORM table.

#### [Low] DB-7 — Migration 0024 downgrade drops columns (lossy) with no guard or comment
- **Locations:** `backend/alembic/versions/0024_workflow_runs_steps.py:145-149`
- **Evidence:** `downgrade()` drops `workflow_runs.variables/context/...` and drops `workflow_steps`/`workflow_runs_archive` entirely — a downgrade past 0024 destroys all workflow execution history.
- **Fix:** Acceptable for a destructive schema change; add an explicit "downgrade is lossy" comment or refuse downgrade in production tooling.
> **FIXED 2026-05-21.** Explicit `DESTRUCTIVE / LOSSY DOWNGRADE` banner added to `downgrade()` plus a `WARNING` line in the module docstring of `0024_workflow_runs_steps.py`.

> **No findings:** no `DELETE`/`UPDATE` without `WHERE` (the prior "DELETE LIMIT" pattern did not recur — retention deletes use `id IN (SELECT ... LIMIT n)`); no sync sessions in async code; no shared session across async tasks; connection-pool config sound.

---

### 2.3 API Layer & Contracts

#### [Critical] API-1 — Workflow router has no authorization on read or mutate endpoints
- **Locations:** `backend/app/api/v1/workflows.py:166,183,248,281,300,316,337,352,363`
- **Systemic:** Yes — every workflow/run/step endpoint except `create_workflow`. `WorkflowService` contains no `decide`/`principal`/`membership`/`role` logic at all, so the service does not compensate.
- **Evidence:** `patch_workflow`, `delete_workflow`, `trigger_run`, `cancel_run`, `get_run`, `list_steps` only declare `principal = Depends(current_principal)`, take a `workflow_id`/`run_id`, and never resolve it to a project or check the caller's role. `list_workflows` has a comment `# Membership check: ...` followed by no check.
- **Impact:** Any authenticated user can read, edit, soft-delete, trigger and cancel any workflow in any tenant by enumerating UUIDs. `trigger_run` executes arbitrary tenant workflows (cost + side effects).
- **Fix:** Add a `_resolve_workflow`/`_resolve_run` dependency that lifts `project_id` (via `workspace_id`) and runs `decide(...)` — the pattern already used in `agents.py`/`chatrooms.py`.

#### [Critical] API-2 — Orchestration read endpoints expose other tenants' data with no scope check
- **Locations:** `backend/app/api/v1/orchestration.py:96,113,132,148,167`
- **Systemic:** Yes — all five read endpoints; only `get_agent_dlq` (`:186`) checks `is_admin`.
- **Evidence:** Each handler takes a UUID path param and calls the service with only `current_principal` — no project resolution, no `decide(...)`, no membership check.
- **Impact:** Any authenticated user can read arbitrary approval gates (votes/rationale), instruction chains/payloads, and live sub-agent instances across all tenants by enumerating IDs.
- **Fix:** Resolve each entity to `workflow_run → workflow → project` and run a membership check, or gate the whole router on `is_admin` if it is intended as admin-only backstage.

#### [High] API-3 — 8 duplicated `error_mapping` handlers: exact-type dispatch + unmapped errors downgraded to HTTP 400
- **Locations:** `backend/contexts/{identity,keys,conversation,knowledge,workflow,tenancy,agents,orchestration}/interfaces/error_mapping.py`
- **Systemic:** Yes — eight near-identical copies.
- **Evidence:** Every handler does `_MAP.get(type(exc), <default>)`. `type(exc)` matches the **exact** class only — any subclass of a mapped error, or any new error class, falls through to a default that uses status `400` with a misleading `type` slug. Conditions that should be 404/409/500 surface as generic 400; new error subclasses silently lose their intended status until all eight tables are updated.
- **Fix:** Replace the eight copies with one shared handler that walks `type(exc).__mro__` to find the nearest mapped ancestor; make the fallback a 500 (`type=internal`).

#### [High] API-4 — `upload_document` and `tus_patch` buffer the entire body before enforcing size caps
- **Locations:** `backend/app/api/v1/rag.py:328` (`data = await file.read()`), `backend/app/api/v1/tus.py:142` (`body = await request.body()`)
- **Systemic:** Yes — `attachments.py:77` does it correctly (`await file.read(MAX+1)` then reject), so the right idiom exists but two upload paths ignore it.
- **Evidence:** `rag.upload_document` reads the whole file with no bound; the size check happens inside `IngestService` *after*. `tus.tus_patch` reads `await request.body()` fully, then checks `len(body) > TUS_MAX_CHUNK`.
- **Impact:** A client can POST an arbitrarily large file/chunk and force the worker to allocate it entirely in RAM before rejection — memory-exhaustion DoS, multiplied by concurrency.
- **Fix:** Read at most `cap + 1` bytes and reject on overflow; for TUS reject early using `Content-Length`.

#### [Medium] API-5 — Orchestration error mapping raises exceptions that produce HTTP 200 problem+json
- **Locations:** `backend/contexts/orchestration/interfaces/error_mapping.py:29` (`WakeupClampApplied`→200), `:59` (`ApprovalTimeoutLeader`→200)
- **Evidence:** Both are `OrchestrationError` subclasses mapped to status `200`; the handler builds `JSONResponse(status_code=200, ..., media_type="application/problem+json")`. A request raising one returns HTTP 200 with an RFC-7807 *problem* body.
- **Impact:** Clients branching on status treat it as success but receive a problem document instead of the resource schema — deserialization/contract break.
- **Fix:** Don't raise advisory outcomes as exceptions out of a handler; map them to a real 2xx success body.

#### [Medium] API-6 — `list_runs` returns a union type / bypasses its response schema when `include_archive=true`
- **Locations:** `backend/app/api/v1/workflows.py:316-334`
- **Evidence:** Return annotation `list[RunOut | dict]`; with `include_archive=True` the handler `return runs` (raw service objects) with `# type: ignore`, otherwise validated `RunOut`. Response shape changes by query flag; archive rows skip schema validation.
- **Fix:** Define an explicit `ArchivedRunOut` schema, or split into a separate endpoint.

#### [Medium] API-7 — Several mutating schemas accept unbounded strings / lists
- **Locations:** `messages.py:48,54` (`content_md` — no `max_length`), `notifications.py:30` (`MarkReadIn.ids` — no length cap), `agents.py:51,104` (`system_prompt`, `reference`, `allowed_tools` — no bounds), `admin.py:116` (`RateLimitPatchIn.window_sec`/`max_count` — no `ge`)
- **Systemic:** Yes — recurs across routers; contrast `OrgCreateIn`/`KeyUploadIn` which do set `max_length`.
- **Impact:** Oversized message bodies and giant `ids` lists drive memory/DB load; a `0`/negative `max_count` can disable rate limiting platform-wide.
- **Fix:** Add `max_length` to free-text and list fields; add `ge=1` bounds to `RateLimitPatchIn` numerics.

#### [Medium] API-8 — CORS configured with `allow_credentials=True` and `allow_headers=["*"]`
- **Locations:** `backend/app/main.py:188-203`
- **Evidence:** When `cors_origins` is set, `CORSMiddleware` mounts with `allow_credentials=True` + `allow_headers=["*"]`. Origins are an explicit list (good), but wildcard headers + credentialed requests is broader than necessary, and auth uses cookies.
- **Fix:** Replace `["*"]` with the explicit header set the SPA sends (`Authorization`, `Content-Type`, `If-Match`, `Tus-Resumable`, `Upload-*`, `X-Request-ID`).

#### [Medium] API-9 — Account-recovery endpoints share one coarse per-IP auth rate-limit bucket
- **Locations:** `backend/app/api/middleware/rate_limit.py:24-34`, applied to `backend/app/api/v1/auth.py`
- **Evidence:** `_bucket_for` puts every `/api/auth/*` path into one `AUTH` bucket (10/min/IP). Login, registration, password-reset request, token verification and refresh share that counter; there is no per-account limiter on `request-password-reset` / `verify-email`.
- **Impact:** Reset-email flooding against a chosen victim is throttled only by a shared IP counter; users behind shared NAT get blocked.
- **Fix:** Add a per-target-account limiter for recovery flows; separate buckets for login vs recovery.



2026-05-01 fixed above




#### [Low] API-10 — `csp_report` reads an unbounded anonymous request body
- **Locations:** `backend/app/api/v1/csp_report.py:17-22`
- **Evidence:** `await request.json()` / `await request.body()` with no size limit; the endpoint is unauthenticated and rate-limit-exempt. The body is then `repr()`-logged.
- **Fix:** Cap the read to a few KB, reject oversized reports, truncate before logging.

#### [High] API-11 — `auth/login` and `auth/refresh` are broken: `__dict__` splat of a `slots=True` dataclass
> **Verified — escalated Low → High.** This is not a latent risk — it is broken now. `TokenPair` is a `@dataclass(frozen=True, slots=True)`; a `slots=True` instance has **no `__dict__`**, so `outcome.tokens.__dict__` raises `AttributeError` on *every* call. No integration test covers the login/refresh happy path, so the breakage is currently undetected.
- **Locations:** `backend/app/api/v1/auth.py:209,230`; `backend/contexts/identity/application/auth_service.py:61-66` (`TokenPair` dataclass)
- **Evidence:** `TokenPairOut(**outcome.tokens.__dict__)` / `TokenPairOut(**pair.__dict__)` — `TokenPair` is declared `slots=True`, which removes `__dict__`. Accessing `.__dict__` raises `AttributeError` → unhandled 500 on the auth happy path right now.
- **Impact:** `POST /api/auth/login` and `POST /api/auth/refresh` return 500 on success. If this is not observed in a running deployment, those endpoints are not being exercised — confirm against a live build.
- **Fix:** Map fields explicitly (`TokenPairOut(access_token=..., refresh_token=..., ...)`) or use `dataclasses.asdict()`.

> **No findings:** mass-assignment (schemas uniformly server-set `id`/`tenant_id`/`version`/timestamps); OpenAPI drift (`openapi.json` includes workflow/orchestration routes consistently).

---

### 2.4 Async, Workers, Events & Realtime

#### [Critical] ASYNC-1 — A2A inbox consumer loop is never started — all A2A messaging is dead
- **Locations:** `backend/contexts/orchestration/application/a2a_consumer.py:87` (`run_consumer_loop`), `:41` (`consume_once`)
- **Systemic:** Yes — the entire A2A subsystem depends on a consumer draining `a2a:agent:{id}` streams. `run_consumer_loop`/`consume_once` are referenced only inside `a2a_consumer.py`; not in `WorkerSettings.functions`, not in `app/main.py` `_lifespan`, not in any entrypoint.
- **Evidence:** `a2a_service.call()` does `xadd_envelope` then `wait_for_reply(...)`; `agent_invocation.py:41` calls `a2a_call(timeout=120s)`. Nothing reads the stream.
- **Impact:** Every A2A message is appended to a Redis stream nothing reads. `call` (and every workflow `agent_invocation` node) blocks the full timeout then raises `A2ATimeout`. `notify`/`send` accumulate until `_STREAM_MAXLEN=10_000` trims them. DLQ/retry never fire.
- **Fix:** Launch a supervised consumer (per live agent or multiplexed) as a long-lived task in worker `on_startup` / app lifespan, wired to `make_dlq_audit_callback`.

#### [Critical] ASYNC-2 — Key-revocation cache invalidator is never started — revoked DEKs stay cached
- **Locations:** `backend/contexts/keys/infrastructure/revocation_listener.py:26` (`run`); cache `provider_router.py` `DEK_CACHE`
- **Systemic:** Yes — the only mechanism propagating `key.revoked` / `key.carry_revoked` pub/sub events into each worker's in-process `DEK_CACHE`. Its docstring says "Runs as a long-lived background task in `app.workers`" but no `asyncio.create_task(revocation_listener.run())` exists anywhere.
- **Impact:** After a key is soft-deleted (D.4) or a carry withdrawn (D.5/R7.04), any process that already cached the unwrapped DEK keeps using it for the cache entry's lifetime — **a revoked/withdrawn BYO key continues to work.** Violates §7.4.
- **Fix:** Start `revocation_listener.run()` as a tracked background task in the app `_lifespan` and worker startup, cancelled cleanly on shutdown.

#### [Critical] ASYNC-3 — Workflow `subagent_spawn` passes a workflow run_id as a parent agent-instance id
- **Locations:** `backend/contexts/workflow/application/executors/subagent_spawn.py:61-65,74`, `backend/contexts/orchestration/application/subagent_service.py:65-67`
- **Evidence:** `subagent_spawn.execute` calls `facade.spawn_subagent(parent_instance_id=ctx.run_id, ...)` — `ctx.run_id` is a `workflow_runs.id`. `SubagentService.spawn` does `get(parent_instance_id)` against `agent_instances` and raises `ValueError("parent instance ... not found")`. Separately, the executor registers `wf:subagent_callback:{instance.id}` but **no completion hook exists** — grep for `subagent_callback`/`resume_at_port` in `contexts/orchestration` finds nothing.
- **Impact:** The node fails instantly with `ValueError` (failing the run); a `wait_for_all=True` run would park `WAITING` with no completion path until the 1-hour timeout forces `FAILED`.
- **Fix:** Pass a real `agent_instances` parent id (or change `SubagentService.spawn` to accept a workflow-run parent); implement the subagent-completion hook that reads `wf:subagent_callback:{instance_id}` and calls `engine.resume_at_port`.

#### [High] ASYNC-4 — Overlapping retention crons double-process the same rows
- **Locations:** `backend/app/workers/main.py:147-166`; `conversation.py:68` (`retention_purge`) vs `retention.py:70` (`_purge_messages`); `workflow.py:149` (`archive_workflow_runs`) vs `retention.py:121` (`_archive_workflow_runs`)
- **Systemic:** Yes — two parallel retention subsystems cover the same tables with different cutoffs.
- **Evidence:** `retention_purge` (03:10) deletes messages older than `5*365+1` days; `_purge_messages` (03:30) older than `5*365` days. `archive_workflow_runs` (04:00) and `_archive_workflow_runs` (03:30) both archive 90-day runs with different guards — `archive_workflow_runs.insert()` has **no `ON CONFLICT`**, so an overlap raises a PK violation on `workflow_runs_archive` and fails the batch.
- **Fix:** Pick one retention path per table; remove the duplicate cron, or make `archive_workflow_runs` idempotent (`ON CONFLICT DO NOTHING`) and align horizons.

#### [High] ASYNC-5 — `evaluate_silence` cron is a permanent no-op — silence-trigger wake-ups never fire
- **Locations:** `backend/app/workers/tasks/orchestration.py:62-75`; cron `main.py:153`
- **Evidence:** The body is `logger.debug("silence trigger sweep: no-op until room-agent bindings exist"); return "ok"`. It never iterates rooms and never calls `WakeupService.evaluate_silence_trigger` (which is fully implemented but unreachable from any worker).
- **Impact:** `silence_minutes` wake-up triggers (G.3/R15.02) never fire in production despite the cron running every 30s.
- **Fix:** Implement the room/agent-binding query and dispatch `wakeup_agent` jobs; until then track as an incomplete feature, not a passing cron.

#### [High] ASYNC-6 — `RunEngine._dispatch_enqueues` creates a new Arq Redis pool on every call and never closes it
- **Locations:** `backend/contexts/workflow/application/run_engine.py:380-396`
- **Systemic:** Yes — called after every `start_run`, `resume_step`, `retry_node`, and `workflow_event_timeout`.
- **Evidence:** `pool = await create_pool(...)` then `await pool.enqueue_job(...)` — no `await pool.close()` and no `try/finally`. A fresh pool is created and abandoned per dispatch.
- **Impact:** Each parked/resumed/retried step leaks a Redis connection pool; the worker eventually exhausts Redis connections / file descriptors.
- **Fix:** Create the pool once (cache on worker `ctx` / module singleton) or `try/finally: await pool.close()`.

#### [High] ASYNC-7 — WebSocket connection has no heartbeat / idle timeout — half-open sockets leak slots
- **Locations:** `backend/shared_kernel/realtime/connection.py:146-204`; `presence.py`
- **Systemic:** Yes — `connection_loop` is the single shared driver for every `/ws/*` endpoint.
- **Evidence:** `_reader` does `await ws.receive_text()` in an unbounded loop with no `wait_for`/deadline; `ping` is purely client-initiated; the server never pings and never closes idle sockets. `presence.py` claims a retention-worker scrub of expired heartbeats, but no such scrub exists in `retention.py` `_POLICIES`.
- **Impact:** A client that vanishes without TCP FIN leaves the server task blocked in `receive_text` forever, counting against `WS_CONNECTIONS_ACTIVE` and the per-user Redis cap (`ws:conns:{user_id}`) — eventually locking the user out of new connections. Presence sets accumulate stale members.
- **Fix:** Add a server-side idle deadline (`wait_for` around `receive_text`, or periodic server ping with a missed-pong cutoff); implement the presence-heartbeat-expiry scrub.

#### [Medium] ASYNC-8 — A2A retry has no backoff delay — failed messages re-delivered in a tight loop
- **Locations:** `backend/contexts/orchestration/application/a2a_consumer.py:57-70,142-169`
- **Evidence:** On handler failure with `attempt < _MAX_RETRIES`, the entry is left un-ACKed and `consume_once` drains pending first thing every iteration with no delay. `_BACKOFF_BASE_SECONDS` is defined but unused on the message-retry path.
- **Impact:** A deterministically failing message is retried as fast as the loop spins until 3 attempts exhaust, hammering the failing dependency with no jitter. (Currently masked only because the consumer is never started — ASYNC-1.)
- **Fix:** Defer pending re-reads by exponential backoff keyed on `times_delivered`, with jitter.

#### [Medium] ASYNC-9 — `join` executor: arrival counter survives retries / re-runs — fan-in resolves early or stalls
- **Locations:** `backend/contexts/workflow/application/executors/join.py:46-72`
- **Evidence:** `arrivals = await redis.incr("wf:join:{run_id}:{node_id}")`, deleted only on the final arrival. A retried/re-delivered non-final branch step `INCR`s again, inflating `arrivals`; there is no per-branch dedup.
- **Impact:** A `mode=all` join can fire after fewer real branches than required (advancing with incomplete results), or never reach `required` and stall. Leftover counters corrupt the next loop pass.
- **Fix:** Track arrivals as a Redis SET of branch/edge ids (`SADD` + `SCARD`); scope the key to a run-attempt epoch.

#### [Medium] ASYNC-10 — `wait_for_event` timeout has a TOCTOU gap with event dispatch — possible double-resume
- **Locations:** `backend/app/workers/tasks/workflow.py:67-118`, `backend/contexts/workflow/application/executors/wait_for_event.py:34-80`
- **Evidence:** `workflow_event_timeout` checks `redis.exists(wait_key)` then calls `resume_at_port(..., "timeout")`; the event dispatcher independently calls `resume_at_port(..., "default")`. No atomic claim — both can see `wait_key` present and both resume. `resume_at_port`'s `state == WAITING` guard narrows but does not close the window across processes.
- **Impact:** A run can be resumed twice (`timeout` and `default`), producing duplicate downstream steps.
- **Fix:** Make the claim atomic — `GETDEL wait_key` or a Lua compare-and-delete; the winner owns the resume.

#### [Low] ASYNC-11 — MCP supervisor health check spawns an uncached `docker` process per probe
> **Verified — downgraded Medium → Low.** The "zombie cleanup" framing was inaccurate: `subprocess.run` already reaps the child on `TimeoutExpired` (`kill()` then `communicate()` internally). The real residual issue is only the missing result cache.
- **Locations:** `backend/services/mcp_supervisor/main.py:34-46`
- **Evidence:** `subprocess.run([...], timeout=5)` is invoked on every `/healthz` GET with no result caching — a fresh `docker` process per probe.
- **Impact:** Under a wedged Docker daemon, frequent probes repeatedly invoke `docker`; processes are reaped but the load is wasteful.
- **Fix:** Cache the `_check()` result with a short TTL so a hung daemon is probed at most once per interval.

#### [Low] ASYNC-12 — `archive_workflow_runs` holds long locks; redundant with the retention sweep
- **Locations:** `backend/app/workers/tasks/workflow.py:200-254`
- **Evidence:** Selects 500 rows, then per row issues 2 `COUNT`s + 1 `INSERT` + 2 `DELETE`s, all in one transaction with no intermediate commit — long-held locks on `workflow_runs`/`workflow_steps`. `_archive_workflow_runs` already does this set-based and idempotently.
- **Fix:** Prefer the set-based `_archive_workflow_runs`; retire this one, or batch-commit.

#### [Low] ASYNC-13 — `_pubsub_fanin` closes the socket on slow consumer while `_writer` may be mid-send
- **Locations:** `backend/shared_kernel/realtime/connection.py:175-204`
- **Evidence:** On a full enqueue, `_pubsub_fanin` calls `ws.close(...)` directly while `_writer` may be awaiting `ws.send_text` — surfacing benign-but-noisy exceptions.
- **Fix:** Signal the writer to stop via an event; perform the single close in the finally-block.

> **No findings:** lock ordering / deadlocks (no multi-lock acquisition); infinite-retry loops (Arq `job_timeout`/`_MAX_RETRIES` bound everything); `agent_fs_gc` subprocess handling; `smap/rotation` rewrap (correctly checkpointed/idempotent).

---

### 2.5 Domain Logic — Agents, Conversation, Knowledge, Audit

#### [Critical] DOM-1 — RAG config soft-delete orphans every child document, chunk, Qdrant point, and MinIO blob
- **Locations:** `backend/contexts/knowledge/application/config_service.py:138-158` (`RagConfigService.soft_delete`), `backend/app/api/v1/rag.py:239-265` (`delete_rag_config`)
- **Systemic:** Yes — the soft-delete convention (`deleted_at = now()` on the config row only) is shared by `RagConfigRepository` and `GraphRagConfigRepository`; neither touches child stores. Contrast `delete_rag_document`, which *does* clean Qdrant + MinIO.
- **Evidence:** `soft_delete` only flips `rag_configs.deleted_at`. `rag_documents` are FK'd to `rag_configs` but not soft-deleted and not cascade-hard-deleted (the config is only soft-deleted). `rag_chunks` (ON DELETE CASCADE from documents) survive too. The per-project Qdrant collection `rag_{project_id}` keeps every vector; MinIO `rag-sources` blobs are never removed. The delete endpoint does no infra cleanup.
- **Impact:** Deleting a RAG config permanently leaks all documents/chunks/vectors/blobs. Because Qdrant `search` filters by `agent_ids` only (not `rag_config_id`), points from a deleted config can still be returned to other configs/agents in the same project — **stale "deleted" knowledge resurfaces in retrieval.**
- **Fix:** Make config deletion cascade — enumerate child documents and run the document-delete cleanup (Qdrant + MinIO + chunks), or drop the `rag_{project_id}` collection if the config is the project's last.

#### [Critical] DOM-2 — GraphRAG config delete cascades Neo4j but never deletes the Qdrant entity vectors
> **Verified — confirmed Critical; one mechanism nuance.** `search_entities` *does* support a `build_id` filter — the defect is that `GraphRagRetrieveService.query` never passes one (so retrieval is unscoped in practice). The orphan-vector leak and missing `config_id` on the payload are confirmed.
- **Locations:** `backend/app/api/v1/graphrag.py:265-302` (`delete_config`), `backend/contexts/knowledge/infrastructure/graphrag_vector_store.py:141-163`
- **Systemic:** Yes — same partial-cleanup pattern; the helpers (`delete_by_build`, `delete_collection`) exist but are wired into no delete path.
- **Evidence:** `delete_config` calls `soft_delete(...)` then `Neo4jAsyncDriver.delete_all(config_id=...)` — never constructs `GraphRagVectorStore`, never deletes entity vectors. Worse, GraphRAG entity points carry `{entity, description, build_id}` — **no `config_id`** — and multiple configs share `graphrag_{project_id}`. `search_entities` accepts a `build_id` filter but `GraphRagRetrieveService.query` never passes one, so retrieval is unscoped.
- **Impact:** Deleted config's entity vectors live forever in the shared collection; any surviving config in the project vector-matches those orphan entities, seeds Neo4j traversal with dead names, and silently retrieves wrong/no context.
- **Fix:** Tag entity points with `config_id` in `upsert_entities`, filter `search_entities` by it, and have `delete_config` call `delete_by_config`. Until tagging exists, deletion cannot safely target the right points — this needs a payload schema change.

#### [Critical] DOM-3 — GraphRAG reconciler commits all-or-nothing across many configs
- **Locations:** `backend/contexts/knowledge/application/graphrag_reconciler.py:66-87` (`run_once`/`run_forever`)
- **Systemic:** Yes — affects every `failed_compensating` config on every 60s cycle (single shared loop). *(Also note: per the async audit, the reconciler is among the background services never started — ASYNC theme C.)*
- **Evidence:** `run_once` opens one session, loops over all stuck configs calling `_reconcile_one`, then does a single `await db.commit()` at the end; `finally: await db.close()` rolls back if commit never ran. If `_reconcile_one` for config *k* raises, the exception propagates; `run_forever` logs it — but the `finally` already closed the session **uncommitted**, discarding the successful transitions and `graphrag.reconciled` audit rows for configs 1..k-1.
- **Impact:** A single unhealthy config poisons the whole batch every cycle; healed configs never finalize; Phase-2 work is repeated; audit rows are lost — the 2PC state machine livelocks.
- **Fix:** Commit per config (or a savepoint per `_reconcile_one`) and catch per-config exceptions so one failure does not roll back peers.

#### [High] DOM-4 — Audit rows can describe operations that did not durably happen — infra side effects escape the DB transaction
- **Locations:** `backend/app/api/v1/rag.py:435-492` (`delete_rag_document`), `backend/app/api/v1/graphrag.py:285-302`, `backend/contexts/knowledge/application/ingest_service.py:142-234`
- **Systemic:** Yes — the project-wide convention is "audit row joins the caller's transaction" (`shared_kernel/audit.py:110-131`), which silently breaks wherever a handler performs non-transactional infra work (Qdrant/MinIO/Neo4j) then writes the audit/DB row in the same handler.
- **Evidence:** In `delete_rag_document`, Qdrant points + MinIO blob are deleted **first**, then `docs_repo.delete(...)` + `audit.emit(...)`. If the transaction rolls back after the infra deletes, the document row + chunks survive but vectors/blob are gone — and no audit row records the destructive action. `graphrag.py delete_config` runs Neo4j `delete_all` after `soft_delete`+`audit.emit`; `ingest_service.ingest` has the same shape.
- **Impact:** The append-only audit trail (R17) can disagree with reality in both directions — claiming rolled-back deletes, or missing irreversible infra deletions. Forensic value undermined.
- **Fix:** Order operations so the DB commit is the point of no return: write DB row + audit, commit, *then* do best-effort infra cleanup in a follow-up step/worker (transactional outbox). Never delete infra before the audit row is durable.

#### [High] DOM-5 — Ingest writes N chunk rows but tolerates a shorter embedding list (`strict=False` zip)
- **Locations:** `backend/contexts/knowledge/application/ingest_service.py:168-204`; same pattern in `graphrag_builder.py:342-350` (`_embed_entities`)
- **Systemic:** Yes — recurring `strict=False` zip-over-embeddings.
- **Evidence:** `insert_many` inserts one `rag_chunks` row per piece; `upsert_chunks` zips `point_ids` with `vectors` using `strict=False`. If `embed_batch` returns fewer vectors than pieces (provider drops blank inputs, partial response), the zip stops short: DB has N chunk rows, Qdrant has M<N points.
- **Impact:** Extra chunks are permanently unretrievable (no vector), yet `rag.document_indexed` audit reports `chunks=N`. Silent partial indexing.
- **Fix:** Use `strict=True` (or assert `len(vectors) == len(pieces)`) and fail the ingest as `IngestFailed` on mismatch.

#### [High] DOM-6 — `chunk_semantic` silently downgrades to a fallback, contradicting its own contract
- **Locations:** `backend/contexts/knowledge/infrastructure/chunkers.py:69-113`
- **Systemic:** Yes — affects every `semantic`-strategy RAG config.
- **Evidence:** The docstring says a missing `semantic-text-splitter` should raise `ChunkParamsInvalid`; the code instead does `except ImportError: return _sentence_aware_fallback(...)`. `similarity_threshold` is validated but never used by either the real splitter or the fallback.
- **Impact:** Environments missing the optional wheel silently produce different chunk boundaries → different embeddings/retrieval quality, with no signal; behavior diverges from the documented contract and from tests that pin the wheel.
- **Fix:** Make code + docstring agree; if fallback is intended, log a warning and surface the strategy used. Honor or remove `similarity_threshold`.

#### [High] DOM-7 — `delete_rag_document` returns 204 before authorization is checked (enumeration oracle)
- **Locations:** `backend/app/api/v1/rag.py:390-433`
- **Evidence:** The handler does `doc = await docs_repo.require(document_id)`; on `RagDocumentNotFound` it returns 204 **before** the `RESOURCE_CREATE_EDIT` `decide(...)` check. For a missing id any authenticated caller gets 204 regardless of project membership.
- **Impact:** 204 vs 403 distinguishes "id does not exist" from "exists but forbidden" — a cross-tenant document-UUID enumeration oracle; the audit trail records no attempt for the missing-id path.
- **Fix:** Resolve authorization context before branching on existence; return 404/403 uniformly.

#### [Medium] DOM-8 — GraphRAG hybrid retrieval traverses Neo4j with entity names from other builds/configs
- **Locations:** `backend/contexts/knowledge/application/graphrag_retrieve.py:67-125`, `graphrag_vector_store.py:92-115`
- **Evidence:** `query` vector-searches `graphrag_{project_id}` with no `build_id`/`config_id` filter (see DOM-2), feeds `h.entity` strings as `seed_entities` to `Neo4j.traverse(config_id=cfg.id, ...)`. Neo4j is config-scoped (so no wrong-tenant data), but stale entities from old builds of the same config (never deleted on rebuild) and from deleted configs still seed the traversal.
- **Impact:** Retrieval seeds polluted by superseded/foreign builds; top-k slots consumed by dead entities → reduced recall.
- **Fix:** Filter `search_entities` by the active `build_id` (and `config_id` once tagged); delete prior-build entity points when a build finalizes.

#### [Medium] DOM-9 — Agent patch with an empty draft still emits an `agent.edited` audit row
- **Locations:** `backend/contexts/agents/application/agent_service.py:142-216`, `agents/infrastructure/repositories.py:160-202`
- **Systemic:** Yes — "build `values`, call repo, unconditionally `audit.emit`" recurs; `ChatroomService.patch` handles the empty case, agent patch does not.
- **Evidence:** If no recognized fields are set, `values == {}`; `repo.patch` returns the existing row without bumping `version` or issuing an UPDATE, but the service still fires `audit.emit("agent.edited", metadata={"fields": []})`.
- **Impact:** Audit noise — an `agent.edited` entry with empty `fields` misrepresents history in a security-relevant log.
- **Fix:** Mirror `ChatroomService.patch` — return early without an audit event when `values` is empty.

#### [Medium] DOM-10 — Audit query `ip_prefix` filter casts INET to text and does a substring match
- **Locations:** `backend/contexts/audit/infrastructure/repositories.py:56-57`
- **Evidence:** `q.where(sa.cast(audit_logs.c.actor_ip, sa.Text).startswith(filters.ip_prefix))` — a string prefix `10.1` matches `10.1.0.0` but also `10.10.0.0`, `10.100.0.0`. No octet-boundary anchoring.
- **Impact:** Forensic IP-prefix filters return over-broad/misleading result sets.
- **Fix:** Use PostgreSQL `inet` containment (`actor_ip <<= :cidr`), or anchor to an octet boundary.

#### [WITHDRAWN] DOM-11 — ~~Retention purge processes only one 500-row chunk per invocation~~
> **Verified — FALSE POSITIVE, withdrawn.** The audit examined `RetentionService.purge_once` in isolation and assumed the cron invokes it once. The actual cron task `retention_purge` (`backend/app/workers/tasks/conversation.py:76-81`) loops `purge_once` up to 100× per nightly run (≈50,000 rows), stopping when a slice deletes 0. `purge_once` is correctly chunked *and* correctly drained — no retention compliance gap exists. This finding is withdrawn and excluded from all counts.

#### [Low] DOM-12 — `web_search` cache hits still consume the project rate-limit quota
- **Locations:** `backend/contexts/agents/application/tools/web_search.py:121-137`
- **Evidence:** `try_acquire` runs before the cache lookup (deliberate "fair quota"), so cached responses consume quota despite no provider call.
- **Fix:** Decide policy explicitly — check cache first, or refund the token on a hit.

#### [Low] DOM-13 — `_strip_css_payloads` regex is redundant and gives false confidence
- **Locations:** `backend/shared_kernel/markdown/sanitize.py:88-108`
- **Evidence:** The `style=` regex does not handle unquoted values; bleach's allowlist (which drops `style` entirely) is the real control. No exploitable gap today.
- **Fix:** Drop the redundant regex or document that bleach's allowlist is the sole control.

> **No findings:** agent execution loop (no in-process multi-turn run loop in scope; `docker_runsc.py` is one-shot per call with explicit timeouts); conversation lifecycle state machine (edit/delete transitions are version-locked and re-checked atomically in SQL).

---

### 2.6 Frontend

#### [High] FE-1 — `keys` slice error extraction reads `e.response.data`, already stripped by the transport interceptor
> **Verified — downgraded Critical → High; scope corrected.** Only 2 of the 4 cited composables are fully broken: `useKeyGroups.ts` and `useSearchKeys.ts` always show `'request failed'`. `useMyKeys.ts` and `useProjectKeys.ts` use an `e instanceof Error` branch that *does* surface the real message via `ApiError.message` — they lose only 422 field-error granularity.
- **Locations:** `frontend/src/slices/keys/composables/useKeyGroups.ts:132-135`, `useSearchKeys.ts:77-80` (fully broken); `useMyKeys.ts:70-76`, `useProjectKeys.ts:60-66` (degraded — lose field-level detail only)
- **Systemic:** Yes — the keys-slice error helpers; the broken `e.response.data` pattern was copied between two of them.
- **Evidence:** `transport/axios.ts:114-122` converts every problem+json response into an `ApiError`/`ValidationError` and throws *that* — never the raw `AxiosError`. `useKeyGroups`/`useSearchKeys` `detail()` reads `e.response.data.detail` — `ApiError` has no `.response` — and always falls through to the literal `'request failed'`.
- **Impact:** Key-group / search-key API failures (quota exceeded, invalid key, permission denied) show "request failed" instead of the real server message.
- **Fix:** Use `err instanceof ApiError ? err.detail ?? err.title : ...` from `@shared/errors`, matching the typed-error convention used elsewhere.

#### [Critical] FE-2 — Route params captured as non-reactive constants — every dynamic-segment view breaks on in-place navigation
- **Locations:** 12+ files — `slices/conversation/views/ChatroomView.vue:151-152`, `ChatroomSettingsView.vue:122`, `ChatroomListView.vue:54`, `WorkspaceListView.vue:58`, `slices/workflow/views/WorkflowRunView.vue:156`, `WorkflowEditorView.vue:219`, `WorkflowListView.vue:120`, `WorkflowRunsListView.vue:117`, `WorkflowBackstageView.vue:147`, `slices/agents/views/AgentDetailView.vue:20`, `AgentListView.vue:20`, `slices/admin/views/AdminUserDetailView.vue:67`
- **Systemic:** Yes — the dominant convention `const x = route.params.X as string` taken once in `setup`. A minority of views correctly use `computed(() => route.params.X)` (e.g. `KeyDetailView.vue:8`), proving the right pattern exists but is applied inconsistently.
- **Evidence:** Vue Router reuses the same component instance when navigating between two routes resolving to the same component (`/chatrooms/A → /chatrooms/B`). `setup()` does not re-run, so `chatroomId`/`agentId`/`runId` keep the old value. `useQuery` keys and `useChatroomSocket(chatroomId)` are built from the stale constant; `<router-view>` in `App.vue` has no `:key`.
- **Impact:** Navigating list→detail→sibling-detail shows the previous entity's data, subscribes to the wrong WebSocket channel, and mutates the wrong record on save/delete.
- **Fix:** Add `:key="$route.fullPath"` to `<router-view>` in `App.vue`, or convert every `route.params.X` capture to a `computed` and make query keys / socket composables reactive.

#### [Low] FE-3 — Discarded WebSocket unsubscribe callbacks in two socket composables (dead code / latent footgun)
> **Verified — downgraded High → Low; mechanism corrected.** The claimed leak does **not** occur on a normal unmount: `onBeforeUnmount` calls `wsManager.close(path)`, which runs `Channel.close()` → `handlers.clear()` + `statusHandlers.clear()` and evicts the channel from the cache. No stale handler survives.
- **Locations:** `frontend/src/slices/workflow/composables/useWorkflowRunSocket.ts:64-65,83-86`, `frontend/src/slices/agents/composables/useRagConfigSocket.ts:73-77,91-93`
- **Systemic:** Partly — `useChatroomSocket.ts` stores and calls the unsubscribe functions; these two discard them.
- **Evidence:** `channel.subscribe('*', handleEvent)` / `channel.onStatus(...)` return unsubscribe functions that these two composables ignore; `onBeforeUnmount` relies entirely on `wsManager.close(path)` to clear handlers — which it does correctly.
- **Impact:** None in the normal mount→unmount cycle. Latent risk only if the same channel path is shared by overlapping component instances.
- **Fix:** Store both unsubscribe functions and call them in `onBeforeUnmount` before `wsManager.close()`, as `useChatroomSocket` does — defensive hygiene.

#### [High] FE-4 — `useBanKickGuard` never closes the WebSocket channel on user change
- **Locations:** `frontend/src/shared/composables/useBanKickGuard.ts:14-41`
- **Evidence:** The `watch` on `session.me?.id` calls `teardown()`, which only invokes `unsubStatus()`/`unsubEvent()` — the `Channel` for `/user/<oldId>` is never `wsManager.close()`-d, so its socket and reconnect/refresh timers keep running. Logout's `session.clear()` does `wsManager.closeAll()`, but a user switch without full logout leaves the path entry.
- **Impact:** Orphaned per-user WebSocket connections with live reconnect timers; token-scoped channels for logged-out users persist.
- **Fix:** In `teardown()` also call `wsManager.close(\`/user/${previousId}\`)`, capturing the previous id in the watch callback.

#### [High] FE-5 — `WorkflowEditorView` has no unsaved-changes guard and the editor store leaks across workflows
- **Locations:** `frontend/src/slices/workflow/views/WorkflowEditorView.vue:216,274-300`, `frontend/src/slices/workflow/stores/workflow.ts:10-19,88-102`
- **Systemic:** Yes — `useWorkflowStore` is a Pinia singleton holding per-workflow editor state (`dirty`, `undoStack`, `redoStack`, `lintErrors`, `selectedNodeId`) keyed by nothing.
- **Evidence:** `loadWorkflow()` calls `store.markSaved(...)` but never `store.clearAll()`; there is no `onBeforeRouteLeave`. Opening workflow B after editing A: B inherits A's `undoStack` (so "undo" applies A's snapshot onto B), A's `lintErrors`, A's `dirty` flag. Combined with FE-2, editor→editor navigation never re-runs `setup`, so `loadWorkflow` never re-fires.
- **Impact:** Undo corrupts the second workflow with the first's nodes; dirty/lint indicators are wrong; navigating away from a dirty editor silently discards unsaved changes.
- **Fix:** Call `store.clearAll()` at the start of `loadWorkflow()`; add an `onBeforeRouteLeave` confirm when `store.dirty`; key editor state by `workflowId`.

#### [High] FE-6 — Out-of-order `replayDelta` / reconnect-sync responses can overwrite newer realtime data
- **Locations:** `frontend/src/slices/conversation/composables/useChatroomSocket.ts:29-47,103-106`, `frontend/src/slices/workflow/composables/useWorkflowRunSocket.ts:24-41,65-68`
- **Systemic:** Yes — the reconnect-recovery pattern is shared across socket composables.
- **Evidence:** `onStatus` fires `void replayDelta()` / `void syncOnReconnect()` on every reconnect with no cancellation and no in-flight guard. If the socket flaps, two `listMessages(roomId, { since })` calls run concurrently; the slower resolves last and re-applies an older delta. `lastSeenMessageId` can move backwards.
- **Impact:** On flaky networks the message/step list briefly shows stale or out-of-order content; the next replay re-fetches a wider window.
- **Fix:** Add a generation counter / in-flight guard; abort the previous replay when a new reconnect fires; ignore stale-generation deltas.

#### [High] FE-7 — WebSocket access token passed as a subprotocol — logged into proxy/server logs, not refreshed before expiry
- **Locations:** `frontend/src/shared/transport/ws-manager.ts:66-76,160-178`
- **Systemic:** Yes — every realtime feature authenticates this way.
- **Evidence:** `new WebSocket(url, [\`bearer.${token}\`])` puts the JWT in `Sec-WebSocket-Protocol`, which proxies/access logs routinely record (unlike `Authorization`, which infra usually redacts). `scheduleTokenRefresh` uses a fixed 60s margin and just resends `getAccessToken()` — it never triggers an HTTP refresh, so a long-backgrounded tab sends an expired token.
- **Impact:** Bearer tokens leak into infrastructure logs; long-lived WS sessions silently de-authenticate.
- **Fix:** Authenticate the WS via a short-lived ticket fetched over HTTPS (or a cookie); have `scheduleTokenRefresh` obtain a genuinely fresh token via the HTTP refresh path before resending.

#### [Medium] FE-8 — `useAdminStore.impersonatedBy` is a `computed` over non-reactive `getAccessToken()`
- **Locations:** `frontend/src/slices/admin/stores/admin.ts:19-28`; same in `composables/useImpersonation.ts:17-28`
- **Evidence:** `getAccessToken()` returns a non-reactive module-level `let` in `transport/axios.ts`. The `computed` records no dependency, so its result is cached on first eval and never recomputes when `setAccessToken` runs.
- **Impact:** The `ImpersonationBanner` may fail to appear when impersonation starts or disappear when it ends; read-only-mode gating can be stale.
- **Fix:** Make the token reactive — expose a `ref` from the transport layer, or store decoded claims in the session store.

#### [Medium] FE-9 — Silent-refresh interceptor skips refresh on any 401 that lacks the exact `token-expired` problem type
> **Verified — finding stands; attribution corrected.** Non-`token-expired` 401s do skip silent refresh. The resulting logout/redirect is performed by `app/errorHandler.ts` (on an unhandled `AuthError`), not by `transport/axios.ts` itself.
- **Locations:** `frontend/src/shared/transport/axios.ts:95-111,126-143`; logout in `frontend/src/app/errorHandler.ts:7-9`
- **Evidence:** The refresh path requires `problem.type.endsWith('/auth/token-expired')` (line 97). A 401 without that exact type string (or with a missing/garbled problem body) skips refresh entirely and is thrown as `AuthError` → user bounced to login even though a refresh would have succeeded.
- **Fix:** Treat any 401 (except explicit `token-revoked`) as refresh-eligible once per request; rely on the `_retry` flag to prevent loops.

#### [Medium] FE-10 — `ChatroomSettingsView` save/delete have no error handling; `room` never resets on chatroom change
- **Locations:** `frontend/src/slices/conversation/views/ChatroomSettingsView.vue:151-205`
- **Evidence:** `onSave()` calls `patchChatroom(...)` with no `try/catch`; an `If-Match` 409 rejects, surfaces only as a generic global toast, and the form keeps a stale `version`. `room` is populated only by scanning *cached* lists — a deep link straight to settings leaves `room` `null` forever and the `v-if="room"` section never renders.
- **Fix:** Wrap `onSave`/`onDelete` in `try/catch` with inline error + version refresh on 409; fetch the chatroom directly when uncached; add loading/empty states.

#### [Medium] FE-11 — No global error boundary — a render-time throw blanks the entire app
- **Locations:** `frontend/src/app/App.vue:1-12`, `frontend/src/app/errorHandler.ts:30-48`
- **Systemic:** Yes — architectural; affects every route.
- **Evidence:** `App.vue` is just `<ImpersonationBanner/><router-view/>` with no `onErrorCaptured`. `app.config.errorHandler` shows a toast but Vue still tears down the failed subtree. Views access nullable data during loading races (`rendered[m.id]` v-html, `new Date(run.started_at)`).
- **Impact:** A single null-access bug in any view degrades to a blank screen.
- **Fix:** Add an `onErrorCaptured`-based error-boundary wrapper around `<router-view>` with a retry fallback.

#### [Medium] FE-12 — `enhanceRenderedMarkdown` runs on every `onUpdated` with no throttle or in-flight guard
- **Locations:** `frontend/src/slices/conversation/views/ChatroomView.vue:295-305`, `lib/renderMarkdown.ts:121-148`
- **Evidence:** `onUpdated(runEnhance)` fires on every reactive update (each message, presence change, typing indicator). `runEnhance` calls async `mermaidInDom`, which `await`s `mermaid.render` and does `node.parentElement?.replaceWith` — overlapping invocations race over the same DOM nodes.
- **Impact:** Redundant KaTeX/Mermaid re-rendering in busy rooms; possible duplicated/detached diagram nodes.
- **Fix:** Debounce `runEnhance`, guard against concurrent runs, process only newly added message nodes.

#### [Low] FE-13 — `useImpersonation` calls `useMutation` — latent scope hazard (no present defect)
> **Verified — no present defect.** All current call sites are component `<script setup>` scopes; `decodePayload` is a local function and is **not** exported, so it cannot be reused out of scope. This is a style note, not a bug.
- **Locations:** `frontend/src/slices/admin/composables/useImpersonation.ts:30-39`
- **Evidence:** `useMutation` must run within a Vue effect scope. `useImpersonation` currently satisfies that at every call site; the hazard would only materialise if it were later called outside a component.
- **Fix:** Keep `useMutation` calls in component-scoped composables.

#### [Low] FE-14 — `SessionsView.load()` rejection becomes an unhandled rejection with no inline error
- **Locations:** `frontend/src/slices/identity/views/SessionsView.vue:11-19,30`
- **Evidence:** `load()` has a `finally` (no permanent spinner) but no `catch`; passed straight to `onMounted`, a failed `listSessions()` rejects unhandled → global toast only, and the page shows an empty list as if the user had no sessions.
- **Fix:** Add a `catch` setting an inline `loadError` + retry, matching `OrgListView`.

> **No findings:** token XSS exposure (access token in a module variable only, refresh token in an httpOnly cookie — correct); missing `v-for` keys; double-submit guards (adequate everywhere checked).

---

## 3. Recommended Remediation Order

### P0 — Before any production / multi-tenant use
| ID | Finding |
|---|---|
| SEC-1 | Egress proxy SSRF — pin connections to screened IPs |
| ASYNC-2 | Start the key-revocation listener — revoked BYO keys currently keep working |
| API-1, API-2 | Add authorization to the workflow and orchestration routers |
| SEC-3 | Make the role resolver fail closed on empty scope |
| DOM-1, DOM-2 | Make RAG/GraphRAG config deletion cascade to all stores |
| DB-1 | Fix workflow transaction ownership |
| ASYNC-1, ASYNC-3 | Start the A2A consumer; fix `subagent_spawn` |
| API-11 | `/api/auth/login` & `/api/auth/refresh` appear to return 500 (`__dict__` on a `slots=True` dataclass) — confirm against a live build, fix immediately if reproduced |

### P1 — Next sprint (High severity)
SEC-2, SEC-4, DB-2, DB-3, DATA-PAGINATION, API-3, API-4, ASYNC-4–7, DOM-3–7, FE-1–7.

### P2 — Backlog (Medium)
All Medium findings — group by theme: error-handling consolidation (API-3 root, FE-1, FE-9, FE-11), input bounds (API-7, API-10, ASYNC inputs), retention correctness (DB-4, DOM-11, ASYNC-4).

### P3 — Cleanup (Low)
All Low findings — schedule opportunistically alongside related work.

### Highest-leverage structural fixes (each closes a cluster)
1. **A scope-aware, mandatory authorization dependency** → closes Theme A (7 findings). Consider a router-level default dependency so a new endpoint is *deny-by-default*.
2. **A single transaction-ownership rule** ("handlers/tasks own the transaction; services and engines never commit") → closes Theme B (5 findings).
3. **A background-service registry wired into one lifespan/worker-startup contract** → closes Theme C (4 findings).
4. **One shared `error_mapping` handler** walking the exception MRO → closes API-3 and prevents future drift.
5. **A monotonic sort key on `messages` and `notifications`** → closes Theme D.
6. **A reusable `delete_*_config` cascade** (+ `config_id` tagging on Qdrant entity points) → closes Theme E.

---

## 4. Methodology & Caveats

- **Coverage:** 6 agents, each tracing real call paths (open files, follow imports) within an assigned non-overlapping domain; backend and frontend both fully in scope.
- **Static analysis only.** No code was executed and no tests were run as part of this audit. Findings marked "latent" describe correct-today code with a fragile invariant; findings marked exploitable were traced end-to-end.
- **Confidence:** `file:line` references were valid at commit `ff19610` (branch `main`, 2026-05-21). Verify line numbers before editing — they drift.
- **Not exhaustive:** the audit targeted *systemic* defects. One-off logic bugs outside the named categories, performance profiling, and dependency-CVE scanning were out of scope.
- **Verification pass (2026-05-21):** all 69 original findings were independently re-checked against source by a second set of reviewers (one per domain). Result: 57 confirmed as written, 10 partially corrected (see §1a), 1 withdrawn as a false positive (DOM-11). Three findings (DB-1, NOTIF-DEDUP, DATA-PAGINATION) were independently reported by multiple first-pass agents — cross-agent agreement raises confidence.
- **One finding needs live confirmation:** API-11 (`/api/auth/login` returning 500) is a strong static inference from the `slots=True` dataclass, but no test exercises that path — confirm against a running build before and after the fix.
