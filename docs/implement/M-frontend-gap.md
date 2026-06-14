# Phase M — Frontend Gap Remediation & Deferred-List Closure

**Goal.** Close the coverage gaps that Phase J's "all slices finalised" claim concealed and that Phase K explicitly deferred (`K-agent-runtime.md` §K.0 "Out of scope for K"). A 2026-06-14 frontend↔backend coverage sweep confirmed that a large set of v1 backend endpoints have **no UI consumer**: five core features are entirely unreachable from the web app (GraphRAG, MCP, RAG-config creation, in-app notifications, per-agent tool selection), several reachable features are missing their key operations (message edit/delete, export download, attachment download, project member roles), and two fully-built components are orphaned (WakeupConfigEditor, DlqViewer). This phase also absorbs the backend/ops items K parked.

This phase makes the product *usable*: every v1 capability the backend exposes must be reachable, operable, and verifiable through the UI. Until it closes, the staging release gate cannot run — E2E golden path 04 (create agent → attach RAG → ingest → grounded answer) fails at step one because there is no UI to create a RAG config.

**Size.** L
**Depends on.** A–K (all backend endpoints exist; this phase wires the frontend and ops to them).
**Unblocks.** J.∞ (staging release gate). Do **not** run the release checklist before M closes — path 04 and the notification/edit/export paths will fail.
**Refs.** `REQUIREMENTS.md` §13 (R13.16/R13.17/R13.21–R13.24), §18 (R18.01–R18.03), §12 (R12.01–R12.16), §15 (R15.06/R15.16), §22.12. Audit evidence: memory `frontend-coverage-gaps-2026-06-14` and `K-agent-runtime.md` §K.0 deferred list (file:line citations below verified against `main` @ `2c5187b`).

## M.0 Scope summary

By phase close, every endpoint below is reachable through a route + view a user can navigate to, the api-client method is invoked (no dead stubs), and the operation is exercised by an integration test:

- A user can **create, list, and delete RAG configs** from the UI; the agent RAG picker is populated by configs the user actually created.
- A user can **create, configure, build, monitor, and delete GraphRAG configs**, and select one on an agent (the `graphrag_config_id` field stops being hardwired `null`).
- A user can **bind/unbind MCP servers** to an agent, test a binding, and manage the project **egress allowlist**; a user can **select which built-in tools** (`file` / `web_search` / `code_exec`) an agent may use.
- A bell badge shows **unread notification count**; a notifications panel lists and marks-read; new notifications arrive live over `/ws/user/{id}` (R18.03).
- Users can **edit** (own, within 5 min — R13.21) and **delete** (R13.16) messages; moderators per R13.23.
- Chat **export** shows status and a download link when ready (R13.17); **attachments** are downloadable; expired attachments surface `[attachment expired]` (R13.11).
- Project members' roles are manageable; orgs/projects are renamable; the orphaned WakeupConfigEditor and DlqViewer are wired to real routes.
- The backend/ops items K deferred are resolved (M.5).

**Out of scope for M.** MFA (REQUIREMENTS.md:51 — not in v1; see M.6 doc fix). Any new backend business logic — every endpoint M consumes already exists and is tested; M is frontend + wiring + ops only. If a gap turns out to need a backend change, it is escalated, not silently added here.

### Construction order

```
M.1 (knowledge config UI)  ─┐
M.2 (notifications slice)   ─┼─ independent; may run in parallel
M.3 (conversation completion)┤
M.4 (tenancy/orchestration mgmt)┘
M.5 (backend/ops deferred)  — independent; verify each defect still exists on main first
M.6 (doc + checklist + E2E) — last; re-enables path 04, corrects §0.8 and the MFA checklist item
```

---

## M.1 Knowledge configuration UI (RAG / GraphRAG / MCP / tools) — **E2E** — L

Closes Tier-1 gaps #1, #2, #3, #5. All four live in `frontend/src/slices/agents` (knowledge is folded into this slice). Today the slice has three routes — `agents.list`, `agents.detail`, `agents.ragConfig` (document management only) — and the RAG picker can only select pre-existing configs that no UI can create.

**Deliverables.**

- **RAG config lifecycle.** New `RagConfigListView` (per project) + create form (chunk strategy/params, embed provider+model+key, rerank toggle+key, top_k — mirror `ragConfigCreateSchema`, already defined at `slices/agents/types/schemas.ts:24`). Wire the dead `agentsApi.createRagConfig` / `deleteRagConfig` (`api/index.ts:82,85`). Add routes; link from the agent form's RAG picker ("+ New config") and from a project knowledge index. Backend: `POST/DELETE /api/projects/{}/rag-configs`, `/rag-configs/{id}` (`rag.py`).
- **GraphRAG lifecycle (entirely new UI).** New api-client methods + `GraphragConfigListView` + create/detail/build views + a status panel consuming `GET /api/graphrag/{id}/status`. Add a `graphrag_config_id` picker to the agent form (AgentListView + AgentDetailView) — it currently writes `null` unconditionally. Backend: full CRUD + build + status in `graphrag.py:113-447` (1:1 with agent per R15.16). Admin reset (`POST /api/admin/graphrag/{id}/reset`) goes in the admin slice (M.4) or a config-detail danger zone.
- **MCP bindings + egress allowlist (entirely new UI).** Agent MCP section (list/add/patch/delete bindings — `agents.py:369-496`; test — `mcp.py:137`) and a project-level egress-allowlist editor (`mcp.py:188-260`). New api-client methods + views.
- **Per-agent built-in tool selection.** UI to choose which of `file` / `web_search` / `code_exec` an agent may invoke (today the backend builds them unconditionally — confirm whether the agent schema carries an `allowed_tools`/`agent_tools` field to persist this; if the field exists in `agent_tools` use it, otherwise escalate — do not invent a backend field).

**Key IDs.** `[R12.01]`–`[R12.16]`, `[R15.16]`, §10.2 (RAG), §11 (GraphRAG). Confirm exact IDs against §10–§12 before coding (per §0.3).

**Exit criteria.** Playwright against `compose.test.yml`: create a RAG config → it appears in the agent RAG picker → attach to an agent. Create a GraphRAG config → build → status reaches a terminal state. Bind an MCP server to an agent → test returns. Add/remove an egress-allowlist host. Toggle a built-in tool and confirm it persists on reload. No dead api-client method remains in `slices/agents`.

## M.2 Notifications slice — **E2E** — M

Closes Tier-1 gap #4. R18 mandates in-app notifications (R18.01 in-app only), persisted + pushed over `/ws/user/{id}` with a bell badge reading unread count (R18.03). The backend is complete (`notifications.py`: `GET /api/notifications`, `POST /api/notifications/read`, `GET /api/notifications/unread-count`); the generated client exists but is never invoked, and there is **no notifications slice**.

**Deliverables.**

- New `frontend/src/slices/notifications` (api/, views/, components/, queries/, routes.ts) respecting the slice-isolation gates (§24.15 #1/#2/#7). Bell-badge component lives in the app shell / shared layout (decide placement against the gate rules — a shared shell component that reads via the slice's composable, not a cross-slice api import).
- Unread-count badge polling **and** live updates: subscribe to the existing `/ws/user/{id}` channel for notification events (the channel already carries `ban-kick`; add the notification event handler alongside `useBanKickGuard`). On event → invalidate the unread-count + list queries.
- Notifications panel: paginated list (R18.02 kinds: key-threshold R7.11, invite received, OC-transfer request, ban reason, key test failed, ingestion status, etc. — enumerate from R18.02), mark-one/mark-all read calling `POST /api/notifications/read {ids:[…]}`.
- i18n (en + zh-TW) for every kind's display string; accessibility on the bell + panel (gate #11).

**Key IDs.** `[R18.01]`–`[R18.03]`, `[R7.11]`. Notification kinds per R18.02.

**Exit criteria.** Integration (Vitest + MSW): badge renders unread count; mark-read decrements; new-notification WS event bumps the badge without reload. Slice passes all 12 SoC gates. A view-level integration test exists (gate #8).

## M.3 Conversation completion (edit/delete, export, attachments) — **E2E** — M

Closes Tier-2 gaps #6, #7, #8. `slices/conversation` reaches all rooms but several backend capabilities are dark: `editMessage`/`deleteMessage`/`getAttachment`/`getExport` are dead (`api/index.ts:149/162/183/215`).

**Deliverables.**

- **Message edit/delete UI** in `ChatroomView`: per-message action affordance respecting R13.21 (author edits own within 5 min; immutable after, except Admin/Project Owner per R13.23) and R13.16 (manual delete → immediate DB + index removal). Edit uses `PATCH /api/messages/{id}` with `If-Match: <version>`; render `edited_at`/`deleted_at` states (the type already carries them). Wire the `message.updated` WS event (R13.23) into `useChatroomSocket`.
- **Export status + download** (R13.17): after `createExport`, poll `getExport` (or subscribe if a WS event exists) until `ready`, then surface the presigned download link; show `failed` state. Replace the current fire-and-forget toast.
- **Attachment download** + expiry: wire `getAttachment` to a download affordance; when an attachment is past its 3-day lifecycle (R13.10), render `[attachment expired]` (R13.11) instead of a broken link.

**Key IDs.** `[R13.10]`, `[R13.11]`, `[R13.16]`, `[R13.17]`, `[R13.21]`–`[R13.24]`.

**Exit criteria.** Playwright: author edits a fresh message → `message.updated` observed on a second client; author delete removes it from both clients and from search; >5-min edit blocked for author, allowed for owner. Export → status transitions to ready → download link present. Attachment uploads → downloads; simulated-expired attachment shows the expired text.

## M.4 Tenancy & orchestration management — **CODE** — M

Closes Tier-2 gaps #9, #10, #11 and Tier-3 #12–#16.

**Deliverables.**

- **Project member role management** (`PATCH /api/projects/{id}/members/{uid}`): add to `ProjectMembersView` the setRole capability `OrgMembersView` already has (`OrgMembersView.vue:35` is the template).
- **Org/project rename**: wire the dead `orgsApi.rename` and add a `projectsApi.rename` + UI in the respective detail views (`PATCH /orgs/{id}`, `PATCH /projects/{id}`).
- **Org quotas display** (`GET /orgs/{id}/quotas`): add api method + a read-only quota panel.
- **Wire orphaned orchestration components**: route/host `WakeupConfigEditor.vue` (wire `patchAgentWakeupConfig` → `PATCH /api/agents/{id}` `wakeup_config`; R15.06) from the agent detail or workflow backstage; surface `DlqViewer.vue` (`GET /api/orchestration/agents/{id}/dlq`) from an admin/ops panel.
- **Key group rename** (`keyGroupsApi.rename`, dead): inline-edit in `KeyGroupDetailView`. **Project key usage** (`GET /projects/{pid}/keys/{kid}/usage`): usage panel in the key detail view (windowed 1h/24h/7d/30d).
- **User-level restore** of soft-deleted orgs/projects (`POST /orgs/{id}/restore`, `POST /projects/{id}/restore`): a "recently deleted" affordance for owners, distinct from admin force-restore.
- Remove any genuinely dead api methods that M decides not to surface (e.g. `getApproval`/`getInstruction` single-item fetches — delete rather than leave as stubs) — record the decision in `docs/frontend-exceptions.md`.

**Key IDs.** `[R15.06]`, §5.2 (member roles), §8 (org/project lifecycle), §7 (key usage/quotas).

**Exit criteria.** Integration tests per touched view (gate #8). Project owner changes a member role and it persists; org/project rename round-trips; quota panel renders; WakeupConfigEditor saves and reloads; DLQ viewer lists entries; key group rename works; usage panel shows windowed data; owner restores a soft-deleted project.

## M.5 Backend / ops deferred items (from K.0) — **CODE/OPS** — DONE (2026-06-14)

The non-frontend items K parked (`K-agent-runtime.md` §K.0 line 22), each re-verified on `main` first (6 parallel investigations). Also closed the two escalations from M.1 (#5) and M.3 (#8).

**Fixed:**

- **`rotate-transit` checkpoint** — real, and the *inverse* of the deferred description: single-rotation crash-resume was already safe; the bug was that the **second rotation skipped every row** (the prior completed rotation's `last_id` cursor was preserved on the new target, so `id > last_id` matched nothing — DEKs silently left at the old version, undecryptable once `min_decryption_version` is raised). Fix: a pure `_resume_cursor` decision (insert/reset/resume) that resets the cursor on a new target. No migration. Unit-tested (`test_rotate_transit_resume.py`).
- **Per-agent built-in tool gate** (closes M.1 escalation #5) — `build_builtin_tools` added file/web_search/code_exec unconditionally (violating R12.01/R12.10/§12.1) and misrouted `source='builtin'` bindings to the MCP sandbox. Fix (Option A, no migration): honor a builtin-source binding's enabled set (back-compat: no builtin binding → all three), skip builtin bindings from the sandbox loop. The M.1 MCP UI already creates these bindings.
- **Admin rate-limit Redis mirror** — worse than described: the `config:ratelimit:*` mirror was never written and the table shipped empty (GET `[]` / PATCH 404). Fix (no migration): a startup `prime_policies()` seeds the 5 bucket rows from compile-time defaults + primes the mirror, and the admin PATCH now `HSET`s the mirror so overrides take effect live. (Default limits already worked via compile-time fallback.)
- **`graphrag_reconciler` deployment** — the `ReconciliationLoop` + entrypoint existed but were never registered. Fix: a `graphrag_reconcile` arq task (calls `reconcile_once()`) registered as a once-per-minute cron (cron lock keeps it singleton across replicas). No migration.
- **`MessageOut` attachments** (closes M.3 escalation #8) — read API exposed no attachments, so the UI had no ids to download / show `[attachment expired]`. Fix (no migration): `MessageOut.attachments` populated via a batched `list_for_messages` (no N+1; includes expired/quarantined per R13.11) + frontend download / placeholder in `ChatroomView`.

**Struck (false alarm):**

- **Guest-route auth bug** — does NOT reproduce on `main`: the guest flow is sound (constant-time token compare, room-scoped `is_guest`, the WS path reuses the same `resolve_room_access`/`ensure_can_read` ACL, send is HTTP-only through `ensure_can_send`). No exploitable escalation.

**Deferred (product/doc decisions, not bugs):**

- `ensure_can_send` ordering: a room with BOTH `allow_project_owners_only` AND `allow_guest_links` fail-closed-denies an enrolled guest's send (unusual config). Needs a §13.2 product call before changing.
- §22.14 doc drift: documents `Sec-WebSocket-Protocol: bearer.<token>`; the impl uses a single-use `ticket.<id>` (more secure — avoids logging the JWT). Correct the spec, not the code.

**Exit criteria.** Met: each fix has a regression test; backend verified via the local unit tier (396 passing) + pinned ruff 0.7 (check + format); CI's pinned mypy 1.13 / integration / wiring tiers are the gate.

## M.6 Documentation, checklist & E2E re-enable — **OPS** — S

**Deliverables.**

- `docs/implement/00-overview.md` §0.8: correct the **J** row (it is not "all slices finalised" — note the M-tracked UI gaps) and add an **L** row (account self-delete R6.07/R8.14/R8.18 + RAG tus ingestion E.6 + RAG document UI, closed 2026-06-14, commit `2c5187b`) and an **M** row (this phase). Update the §0.1 phase map / prose that still says "ten phases A–J".
- `docs/release-checklist.md`: **remove the MFA item** (line 28 "MFA enrollment prompt shown on first admin login") — it contradicts `REQUIREMENTS.md:51` (no MFA in v1, email+password only). Add checklist items for the M-delivered paths (RAG/GraphRAG/MCP config, notifications, message edit/delete, export download).
- E2E golden path 04 (RAG) and any other path blocked by M is re-enabled and required in CI's `frontend-e2e` tier.

**Exit criteria.** §0.8 reflects reality (no false "complete"); checklist has no MFA item and no requirement-contradicting lines; `frontend-e2e` path 04 green on push.

---

## M.∞ Phase gate

- [ ] RAG config create→attach→ingest→grounded-answer reachable end-to-end in the UI (E2E path 04 green).
- [ ] GraphRAG create/build/status/select reachable; agent `graphrag_config_id` settable from UI.
- [ ] MCP bind/unbind/test + egress allowlist + built-in tool selection reachable.
- [ ] Notifications: bell badge unread count + list + mark-read + live WS update; new slice passes all 12 SoC gates.
- [ ] Message edit (5-min author rule) + delete + moderator edit; export status+download; attachment download + expired-state.
- [ ] Project member roles, org/project rename, quotas, key usage, WakeupConfigEditor, DlqViewer, user restore all reachable.
- [ ] M.5 backend/ops items resolved or struck-with-reason; each fix has a failing→passing regression test.
- [ ] No dead api-client method remains across all slices (or each surviving one is justified in `frontend-exceptions.md`).
- [ ] §0.8 J row corrected; L + M rows added; MFA checklist item removed.

## Cross-cutting checklist

1. **AuthZ tap.** Every newly-surfaced mutation already has a backend permission check; the frontend must hide/disable affordances the caller lacks permission for (RAG/GraphRAG/MCP config = `RESOURCE_CREATE_EDIT`; member roles/rename = owner scope; admin reset/DLQ = admin).
2. **Audit tap.** No new backend events expected (M is frontend); confirm existing config/message/member events still fire on the paths M surfaces.
3. **Rate-limit buckets.** New endpoints already belong to buckets; verify the frontend handles 429 surfacing (problem+json) on these paths.
4. **Observability.** No new metrics required; confirm WS notification subscription does not regress `ws_connections_active`.
5. **RFC 7807.** Surface existing problem types (e.g. RAG/GraphRAG validation, version conflict on message edit `If-Match`) in the new forms.
6. **Migration policy.** None expected — M adds no tables. If M.5 needs one, follow N-1; `0031+` numbering.
7. **Secrets.** No secrets touched; embed/rerank keys are referenced by id, never plaintext, in the RAG/GraphRAG forms.
8. **SoC gates.** Every new view ships ≥1 integration test (gate #8), no raw transport (gate #3), no bare strings (gate #12), slice isolation (gate #2/#7). The notifications bell living in the shell must not become a cross-slice api import.

## Risks

- **Doc-trust erosion (again).** This phase exists for the same reason K did: §0.8 asserted completeness that file-level reading disproved. Rule for M: a sub-step closes **only** with its exit test green in CI — Playwright for user-facing paths, no mock-only closes for E2E-tagged steps.
- **Scope creep into backend.** M is frontend + wiring + ops. The moment a "gap" needs new backend business logic, stop and escalate — it means the prior phase's CODE-complete claim was also false and needs its own remediation, not a quiet M patch.
- **GraphRAG/MCP UI surface is large.** M.1 is the heaviest sub-step (four feature surfaces). Ship RAG-create first (unblocks E2E path 04 and is the smallest), then GraphRAG, then MCP, then tool selection — each independently mergeable.
- **Notifications bell placement vs SoC gates.** A shell-level bell that imports a slice's api violates gate #2/#7. Resolve by exposing a shared composable boundary or an app-shell injection point before building the badge.
- **M.5 phantoms.** The K-deferred backend list is unverified against current `main`. Verify-before-fix is mandatory; some items may already be resolved by intervening commits.
