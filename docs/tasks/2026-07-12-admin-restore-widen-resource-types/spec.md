---
type: feature
status: draft
created: 2026-07-12
requirements: [R8.13, R8.11, R9.03, R16.03]
---

# Widen admin restore to all six soft-deletable resource types

## 1. Summary

The admin "restore soft-deleted resource" tool (Ops view) offers six resource types, but
the backend only implements three. Selecting agent, workflow, or chatroom sends a request
the backend rejects with a 422, so the feature is broken for half its advertised types.
This task implements restore for the three missing types, routing every type through its
owning context's facade (replacing the current cross-context raw-table dispatch), widens
the OpenAPI `resource_type` enum from three to six values, and removes the frontend cast
that papered over the gap. Restore stays a pure `deleted_at = NULL` clear per the current
org/project behavior. Originates as FU-4 of
`docs/tasks/2026-07-12-generated-client-wrap-admin`.

## 2. Goals and Non-goals

**Goals**
- Admin restore works for all six types the UI offers: user, org, project, agent,
  workflow, chatroom (`AdminOpsView.vue:97-104`).
- The generated OpenAPI client declares a six-value `resource_type` enum, and the frontend
  boundary cast (`admin/api/admin.ts:111`) is removed.
- Each type's restore is owned by its context's facade — no context reaches into another
  context's tables (fixing the current SoC violation where identity's `AdminService` raw-
  `UPDATE`s tenancy/other tables).
- The admin restore route calls a facade, not an application service directly (fixing the
  documented app-layer boundary violation at `admin_projects.py:94`).
- Backend test coverage for the restore path (currently zero) across all six types.

**Non-goals**
- No orphan/parent-existence guard (Q-3): restoring a child whose parent is still soft-
  deleted is permitted, matching current org/project behavior. The dangling-record
  possibility is an accepted limitation.
- No cascade on org/project admin-restore. The current admin path clears only the target
  row's `deleted_at`; this is preserved. (The user-facing `OrgService.restore` cascade to
  projects at `org_service.py:193-196` is a separate path and stays untouched — see FU-1
  below.)
- No new soft-delete columns — every target table already has `deleted_at`
  (`agents` `tables.py:66`, `workflows` `tables.py:31`, `chatrooms` `tables.py:49`).
- No change to the user-facing per-resource DELETE/restore endpoints
  (`/api/orgs/{id}/restore`, `/api/projects/{id}/restore`).
- No DB migration — `resource_type` is a request-path `Literal`, not a PG ENUM (§4).
- No 60-day-window enforcement change (R8.13 already governs the window; restore does not
  re-check age today and won't start to).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | UI offers 6 restore types, backend supports 3 (agent/workflow/chatroom 422). Widen backend or narrow UI? | **Widen backend to 6.** | Matches the UI + i18n already built for six types (`en.json:170-175`), and is what FU-4 asked for. Restoring these resources is a legitimate admin recovery capability already implied by R8.13's generic `{type}`. |
| Q-2 | How to wire the 3 new types, given the service currently raw-`UPDATE`s other contexts' tables from `contexts/identity`? | **Facade dispatch** — each owning context exposes an admin-restore on its facade; the route dispatches by type. | Respects the SoC hard rule (no cross-context raw table access) and follows the app→facade pattern the rest of `app/api/v1` already uses (`admin_ip_bans.py:53`, `admin_audit.py:70`). The current raw `table_map` (`admin_service.py:373-377`) is the anomaly. |
| Q-3 | Guard restore against orphaning (refuse if parent still soft-deleted)? | **Plain clear, no guard.** | Pure `deleted_at = NULL`, matching org/project today (`admin_service.py:381-390`). An admin who restores a child can also restore the parent. Orphan possibility recorded as an accepted limitation. |
| Q-4 | Scope: migrate the existing user/org/project to facade dispatch too, or only add the 3 new via facade and leave the old 3 on `AdminService.restore_resource`? | **Migrate all six to facade dispatch** (Design Option A); remove `AdminService.restore_resource`. | A split dispatcher (3 raw + 3 facade) is worse than either pure option; one uniform registry is cleaner and fixes the SoC violation for all six. Existing behavior is preserved exactly (pure clear + `admin.restore_resource` audit + the user status/ban reset). Decided at approval — narrow to only-new-3 if reduced blast radius is preferred. |

## 4. Current State

- **Route:** `POST /api/admin/restore/{resource_type}/{resource_id}` at
  `app/api/v1/admin_projects.py:86-104`. `resource_type: Literal["user","org","project"]`
  (`admin_projects.py:88`); `require_admin` guard (`admin_projects.py:90`); returns
  `RestoreOut{restored: bool}` (`admin_projects.py:36-37`), 404 "Resource not found or not
  soft-deleted" when the service returns `False` (`admin_projects.py:102-103`). The route
  directly instantiates the application service `AdminService(db)` (`admin_projects.py:94`)
  — a violation of the documented app→facade rule (`backend/CLAUDE.md`); the rest of the
  layer goes through facades (e.g. `admin_ip_bans.py:53` `IdentityFacade(db)`).
- **Service:** `AdminService.restore_resource` at
  `contexts/identity/application/admin_service.py:364-417`. Holds a raw `table_map`
  (`admin_service.py:373-377`) of `sa.table(...)` for user/org/project and issues
  `UPDATE ... SET deleted_at = NULL WHERE id = :id AND deleted_at IS NOT NULL`
  (`admin_service.py:381-390`); `rowcount == 0 → False → 404` (`admin_service.py:391-392`).
  The `"user"` branch additionally resets `status` (ACTIVE if `email_verified` else PENDING)
  and clears `banned_reason`/`banned_at` (`admin_service.py:393-405`). Emits
  `admin.restore_resource` audit (`admin_service.py:406-416`). It bypasses even
  tenancy's own `OrgService.restore`/`ProjectService.restore`.
- **Type is NOT a PG ENUM.** The only place `resource_type` is persisted is the audit log,
  a free-form `sa.Text` column (`shared_kernel/audit.py:49`), independent of the restore
  Literal. No `CREATE TYPE ... AS ENUM` for resource types exists in `alembic/versions/`.
  Widening the Literal needs no migration.
- **FastAPI surfaces the Literal as an OpenAPI enum:** `backend/openapi.json` path
  `/api/admin/restore/{resource_type}/{resource_id}` parameter `resource_type` →
  `"enum": ["user","org","project"]`. Widening the Literal to six values widens this enum.
- **Frontend:** the Ops selector offers six types — user/org/project/agent/workflow/chatroom
  (`AdminOpsView.vue:97-104`), passed unchanged to `restoreResource`
  (`AdminOpsView.vue:131`); `AdminOrgsView.vue:61` hardcodes `type: 'org'`.
  `admin/api/admin.ts:109-113` casts `resourceType: type as 'user'|'org'|'project'`
  (`admin.ts:111`) to satisfy the three-value generated enum
  (`api-client/services/AdminService.ts:395`), with a comment naming this a backend defect
  tracked as FU-4 (`admin.ts:106-108`). The characterization test deliberately passes
  `'agent'` "beyond the OpenAPI enum" (`admin/api/__tests__/admin.spec.ts:229-233`).
- **Target tables** all have `deleted_at` and no other lifecycle state to reset (only
  `users` carries extra state):
  - `agents` — `deleted_at` (`agents/infrastructure/tables.py:66`), trigger-bumped
    `version` (`:65`); no status. Soft-delete: `AgentService.soft_delete`
    (`agent_service.py:510`) → `AgentRepository.soft_delete` (`repositories.py:264`).
  - `workflows` (the workflow *definition*; runs are a separate, non-soft-deletable table)
    — `deleted_at` (`workflow/infrastructure/tables.py:31`); no status. Soft-delete:
    `WorkflowService.soft_delete` (`workflow_service.py:217`) → repo (`repositories.py:191`).
    A partial-unique index `uq_workflows_workspace_id_name WHERE deleted_at IS NULL`
    (`tables.py:33-39`) means restoring a workflow whose name was reused while deleted can
    raise an IntegrityError (§10).
  - `chatrooms` — `deleted_at` (`conversation/infrastructure/tables.py:49`); no status.
    Soft-delete: `ChatroomService.soft_delete` (`chatroom_service.py:191`), which also
    enforces R13.02 (auto-creates a "general" room if the workspace would be left empty,
    `chatroom_service.py:217-234`).
- **Tenancy precedent:** `OrgService.restore` (`org_service.py:185-207`, cascades +
  `org.restored` audit) and `ProjectService.restore` (`project_service.py:163-182`); repo
  inverses `OrgRepository.restore` (`repositories.py:119-120`), `ProjectRepository.restore`
  (`repositories.py:380-382`) are pure `deleted_at=None`. Restore lives on the service, not
  the tenancy facade (`tenancy/interfaces/facade.py` has none).
- **Tests:** none exist for `AdminService.restore_resource` or any agent/workflow/chatroom
  restore. Tenancy restore is covered at `tests/unit/test_tenancy_services.py:233,485` —
  the template to mirror.

## 5. Design

### Options considered

**Option A — uniform facade dispatch (chosen).** Add an admin-restore method to each of the
five owning facades (identity, tenancy, agents, workflow, conversation). Each clears
`deleted_at` for its own resource via its own repository and emits `admin.restore_resource`
audit; identity additionally applies the user status/ban reset. The route holds a
`{resource_type → restorer}` registry and dispatches, returning 404 when the restorer
reports no row un-deleted. `AdminService.restore_resource` is removed.
Trade-offs: touches five facades + the route + removes a service method (larger blast
radius), but yields one clean dispatcher, fixes both SoC violations (cross-context table
access AND app→service instantiation), and preserves every current behavior.

**Option B — split dispatch.** Keep `AdminService.restore_resource` for user/org/project;
add agent/workflow/chatroom through their facades; the route branches. Trade-offs: smaller
change and zero risk to the working three, but leaves a split-brain dispatcher and keeps
the two SoC violations for the existing three.

### Decision

**Option A.** The uniform registry is the only design that leaves the codebase cleaner
rather than merely bigger — it retires the raw cross-context `table_map` and the direct
service instantiation in one pass, and every type is restored by the context that owns its
data. Behavior is held constant: the new tenancy admin-restore methods do a *pure* clear
(not `OrgService.restore`'s cascade), so admin org/project restore behaves exactly as
today; the user status/ban reset moves verbatim into `IdentityFacade.restore_user`; audit
stays `admin.restore_resource` with the per-type `resource_type`. What is given up is
minimal blast radius — mitigated by the new regression tests (§12) that pin the preserved
behavior before the migration.

## 6. Detailed Changes

- **Backend**
  - **New repository restore inverses** (mirror each `soft_delete`, pure
    `UPDATE ... SET deleted_at = NULL WHERE id = :id AND deleted_at IS NOT NULL`, return
    whether a row changed): `AgentRepository.restore` (`agents/.../repositories.py`, beside
    `:264`), `WorkflowRepository.restore` (`workflow/.../repositories.py`, beside `:191`),
    `ChatroomRepository.restore` (`conversation/.../repositories/chatroom_repo.py`, beside
    `:200`). Tenancy (`OrgRepository.restore` `:119`, `ProjectRepository.restore` `:380`)
    and users already exist / are handled in the facade.
  - **Facade admin-restore methods** — `(resource_id, admin_user_id, actor_ip, request_id)
    -> bool`, each emitting `audit.emit(db, AuditEvent(action="admin.restore_resource",
    actor_user_id=admin_user_id, actor_ip=..., resource_type="<type>", resource_id=...,
    request_id=...))` (shape per `admin_service.py:406-416`, signature `audit.py:103-115`):
    - `IdentityFacade.restore_user` (`identity/interfaces/facade.py`, after `:130`) — pure
      clear on `users` + the status/ban reset moved verbatim from `admin_service.py:393-405`
      (`UserStatus` already imported at `facade.py:18`; add `from shared_kernel import
      audit` and the identity tables import).
    - `TenancyFacade.restore_org` / `restore_project` (`tenancy/interfaces/facade.py`) —
      call the existing pure-clear `OrgRepository.restore` / `ProjectRepository.restore`
      (NOT `OrgService.restore`, to avoid introducing the project cascade).
    - `AgentsFacade.restore_agent` / `WorkflowFacade.restore_workflow` /
      `ConversationFacade.restore_chatroom` — call the new repo inverses.
  - **Route** `admin_projects.py:86-104` — widen the Literal to
    `Literal["user","org","project","agent","workflow","chatroom"]` (`:88`); replace the
    `AdminService(db)` call with a dispatch over the six facade methods; keep the 404-on-
    False and `RestoreOut` shape. Remove the now-unused `AdminService` import.
  - **Remove** `AdminService.restore_resource` (`admin_service.py:364-417`) and any import
    left dangling.
- **API contract** — `resource_type` OpenAPI enum widens 3→6. Regenerate:
  `make openapi-types` from repo root (runs `python -m scripts.export_openapi > openapi.json`
  then `pnpm run gen:api`; `Makefile:93-96`). On Windows without `make`, run the two
  commands manually (`backend`: `python -m scripts.export_openapi > openapi.json.tmp; mv`;
  `frontend`: `pnpm run gen:api`). Commit `backend/openapi.json` +
  `frontend/src/shared/api-client/`. `gen:api` rerun required: **yes**.
- **Frontend**
  - `admin/api/admin.ts:109-113` — drop the `as 'user'|'org'|'project'` cast (`:111`) and
    the FU-4 comment (`:106-108`); `resourceType: type` now type-checks against the widened
    six-value enum. Signature stays `(type: string, id)` — or narrow to the generated union;
    keep `string` to avoid churn in callers.
  - `admin/api/__tests__/admin.spec.ts:228-233` — the `'agent'` case is no longer "beyond
    the enum"; update the comment/test name. The assertion (path + result) stays valid.
  - `admin/composables/useAdminActions.ts:104-113` — `onSuccess` invalidates caches only for
    user/org/project; agent/workflow/chatroom get none. Left as-is (the Ops view doesn't
    render the restored resource); noted as FU-2.
- **Deploy/config** — none.

## 7. NFR Checklist

- [x] i18n — the six type labels already exist in both locales (`en.json:170-175`,
  `zh-TW.json:170-175`); no new strings. Error toast keys unchanged.
- [x] Audit log — every restore emits `admin.restore_resource` (documented at REQUIREMENTS
  line 842) with the per-type `resource_type`; behavior preserved for existing three.
- [x] Tenant isolation — restore is a global admin capability (`require_admin`), not tenant-
  scoped; unchanged. No org/project membership check applies (admin-only by design, R16.03).
- [x] Error handling UX — 404 preserved for not-found/not-soft-deleted; the workflow
  name-uniqueness edge maps to a 409 (§10). Frontend already renders success/danger alerts
  (`AdminOpsView.vue:65-68`).
- [x] Performance — single indexed `UPDATE ... WHERE id` per call; no N+1, no volume concern
  (admin point operation).

## 8. Security Considerations

Admin-only privilege surface (`require_admin`, `admin_deps.py:15-20`), so scoped to the
`check-security` privilege/tenant + input-validation dimensions:
- **Privilege** — every restorer is reachable only behind `require_admin`; no authorization
  moves into the facades (they assume the caller is already admin-gated, matching the
  existing `AdminService` contract). The route remains the single gate.
- **Input validation** — `resource_type` stays a boundary `Literal` (now six values); an
  out-of-set value is a 422 before any facade runs. `resource_id` stays a validated `UUID`.
- **Data exposure** — restore re-exposes soft-deleted data; this is the intended admin
  recovery capability (R8.13) and does not widen who can see it (still admin-gated, and the
  resource returns to its normal ACL once un-deleted).
- **Audit non-repudiation** — the `admin.restore_resource` audit entry (actor, type, id, ip,
  request id) is preserved for all six types, so every restore stays attributable.
- **No secrets** — no key material, token, or PII is read or logged; the audit fields are
  ids only.

## 9. Quality Notes

- **Existing debt** in touched files (do not imitate; recorded, not silently fixed):
  - The app→facade violation at `admin_projects.py:94` and the cross-context raw `table_map`
    at `admin_service.py:373-377` — this task *fixes* both as part of the chosen design.
  - `admin_projects.py` is named for "projects" but hosts the generic restore route — naming
    debt, not touched (FU-3).
  - `ChatroomRepository.soft_delete` (`chatroom_repo.py:200`) lacks the `deleted_at IS NULL`
    guard the agents/workflow variants have; the new `restore` should include the
    `deleted_at IS NOT NULL` guard for correct rowcount semantics regardless.
- **Patterns to follow**:
  - Facade construction + repo usage: `IdentityFacade` (`identity/interfaces/facade.py:38-43`).
  - Restore service/repo shape + audit: tenancy (`org_service.py:185-207`,
    `repositories.py:119-120`) and its tests (`test_tenancy_services.py:233,485`).
  - App→facade route: `admin_ip_bans.py:53`, `admin_audit.py:70`.
  - Audit emit convention: `admin_service.py:406-416`, signature `audit.py:103-115`.
- **Reuse inventory**:
  - `audit.emit` / `audit.AuditEvent` (`shared_kernel/audit.py`) — do not hand-roll audit.
  - Existing `OrgRepository.restore` / `ProjectRepository.restore` — reuse; do not add new
    tenancy restore code.
  - `UserStatus` (`contexts/identity/domain/models`) — reuse for the user status reset.
  - The `soft_delete` methods are the exact templates for the new `restore` inverses.

## 10. Risks and Rollback

- **Workflow name-uniqueness (medium).** `uq_workflows_workspace_id_name WHERE deleted_at IS
  NULL` (`workflow/.../tables.py:33-39`) can make `restore_workflow` raise an IntegrityError
  if a workflow with the same name was created in the same workspace after this one was
  soft-deleted. Mitigation: `restore_workflow` catches `IntegrityError` on the unique
  constraint and maps to a 409 "name already in use" (mirroring the recent
  `create_group` IntegrityError-discrimination precedent, commit `bc9d735`). Recorded as
  AC-7; not a data-loss risk.
- **Chatroom R13.02 residue (low).** Soft-deleting the last room auto-creates a "general"
  room (`chatroom_service.py:217-234`); restoring the original then leaves two rooms. That
  satisfies R13.02 (≥1 room) and needs no special handling — noted so the implementer
  doesn't add spurious cleanup.
- **Behavior drift on the existing three (low).** Mitigated by regression tests that pin
  the current pure-clear + user status/ban reset + `admin.restore_resource` audit before the
  migration (§12, AC-6).
- **No migration** — nothing to roll back at the DB layer. Code rollback is `git revert`
  plus re-running `make openapi-types` on the reverted Literal.

## 11. Acceptance Criteria

- [ ] AC-1: `POST /api/admin/restore/{type}/{id}` succeeds (un-deletes the row, returns
      `{restored: true}`) for each of the six types when the target is soft-deleted; returns
      404 when the target does not exist or is not soft-deleted.
- [ ] AC-2: restore for every type is dispatched through the owning context's facade; no
      context reads or writes another context's tables for restore; the route calls a facade
      (not `AdminService`); `AdminService.restore_resource` no longer exists.
- [ ] AC-3: the `user` restore still resets `status` (ACTIVE if `email_verified` else
      PENDING) and clears `banned_reason`/`banned_at`, and every restore emits an
      `admin.restore_resource` audit entry with the correct `resource_type`.
- [ ] AC-4: `backend/openapi.json` and the generated client declare a six-value
      `resource_type` enum; `admin/api/admin.ts` no longer casts (`type` passes through
      type-checked); `pnpm run gen:api` produces no further drift.
- [ ] AC-5: backend tests cover restore success + not-found/not-soft-deleted for all six
      types and the user status/ban reset (none existed before).
- [ ] AC-6: no behavior change for user/org/project restore — a regression test asserts the
      pure `deleted_at` clear (no project cascade on org), the user status/ban reset, and the
      `admin.restore_resource` audit shape.
- [ ] AC-7: restoring a workflow whose name was reused in its workspace returns a 409 (not a
      500/unhandled IntegrityError).
- [ ] AC-8: all mechanical gates green — backend `pytest -q`, `ruff check . && ruff format
      --check .`, `mypy .`; frontend `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build`.

## 12. Test Plan

- **Backend unit** (mirror `tests/unit/test_tenancy_services.py:216-241,474-491`): per
  facade restore method — success un-deletes + returns True + emits `admin.restore_resource`;
  not-found/not-soft-deleted returns False. Identity: assert the status/ban reset for a
  verified vs unverified user (AC-3, AC-6). Workflow: a name-collision case asserting 409
  (AC-7). These live beside each context's existing service tests.
- **Backend route/integration**: a test hitting `POST /api/admin/restore/{type}/{id}` for
  each of the six types (dispatch correctness + 404 path + `require_admin` 403 for a non-
  admin). Location: the admin API test module (create if absent; none exists today).
- **Frontend component/characterization**: update `admin/api/__tests__/admin.spec.ts:228-233`
  (drop the "beyond the enum" framing; assertion unchanged); the existing `AdminOpsView`
  tests continue to pass unmodified.
- **Manual (`verify`)**: N/A beyond the automated route test — no new user-visible flow, the
  UI already renders the six options and success/error alerts.

## 13. SRS Delta

Amend **[R8.13]** to enumerate the supported types (currently generic `{type}`):

> - **[R8.13]** Within the 60-day window, Admin may restore a soft-deleted resource via
>   `POST /api/admin/restore/{type}/{id}`, where `{type}` is one of `user`, `org`,
>   `project`, `agent`, `workflow`, `chatroom`. Restore clears `deleted_at`; it does not
>   cascade to child resources and does not re-check the 60-day age. Each restore emits an
>   `admin.restore_resource` audit event.

Update the endpoint table row (REQUIREMENTS line 1569) note to: "Restore soft-deleted
(user/org/project/agent/workflow/chatroom) within 60 d."

## 14. Open Questions

None blocking. (Q-4's scope choice is confirmable at the approval gate.)

## 15. Deviation Log

Appended by /build.

## 16. Follow-ups

- FU-1: admin org/project restore does a pure clear with no cascade to child projects, unlike
  the user-facing `OrgService.restore` (`org_service.py:193-196`). Whether admin restore
  *should* cascade is a product question, deliberately out of scope here (behavior preserved).
- FU-2: `useAdminActions.ts:104-113` invalidates query caches only for user/org/project;
  agent/workflow/chatroom restore invalidates nothing. Harmless for the Ops view; revisit if
  admin restore is ever surfaced next to those resources' lists.
- FU-3: `admin_projects.py` hosts the generic restore route despite its "projects" name —
  rename/relocate for clarity.
