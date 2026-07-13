---
type: bugfix
status: implemented
created: 2026-07-13
requirements: [R8.13]
---

# Admin restore must reject a child whose parent chain is soft-deleted

## 1. Summary

The widened admin restore (`docs/tasks/2026-07-12-admin-restore-widen-resource-types`)
routes six resource types through their owning facades, but the four *child* restores
(project, agent, workflow, chatroom) are pure `deleted_at` clears with no check that the
resource's ancestor chain is still live. An admin can therefore restore a child by id
while its parent org / project / workspace stays soft-deleted, producing a
**live-child-of-dead-parent**: the child reappears in naive `WHERE deleted_at IS NULL`
listings under an ancestor that is gone. The fix adds a parent-chain liveness guard at the
restore route — if any ancestor is soft-deleted, the restore is rejected with 409 and a
"restore the parent first" message, matching the read-time liveness convention the codebase
already relies on. Found in the 0712 admin-restore bug hunt (finding #2, medium / PLAUSIBLE).

## 2. Observed vs Expected

- **Observed** — `AdminService`/context `admin_restore` methods clear only the resource's own
  `deleted_at` and emit an audit event; none consults any ancestor's `deleted_at`:
  `project_service.py:186-219`, `agent_service.py:537-567`, `workflow_service.py:239-271`,
  `conversation/application/chatroom_service.py:243-268`. The route dispatches each type and
  returns success on a truthy result (`admin_projects.py:143-151`) with no parent check.
  Because `ProjectService.soft_delete` does **not** cascade to a project's children
  (`project_service.py:144-163`; confirmed `a2a_service.py:389`), a child keeps
  `deleted_at IS NULL` after its ancestor is soft-deleted, so restoring the child by id
  yields a live row under a dead ancestor.
- **Expected** — restoring a resource must not create a state where a live resource hangs off
  a soft-deleted ancestor. The system's own convention is that a resource under a soft-deleted
  ancestor is unreachable — read paths walk up and treat it as gone (`conversation/application/access.py:69-77`,
  `a2a_service.py:389-391`). Admin restore should honor the same invariant by refusing to
  revive a child whose ancestor chain is not fully live, and directing the admin to restore
  the ancestor first. `[R8.13]` (admin restore) is the intent source; no SRS change is needed
  (see §11). The block-vs-cascade choice was confirmed with the user (Q-1).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Child restored under a still-deleted parent: block, cascade-restore the parents, or allow+warn? | **Block with 409** ("restore the parent first"). | Minimal, safe, reuses the existing restore-conflict-to-409 shape; no silent side effects. Cascade reopens the deferred FU-1 cascade question and can un-delete an org the admin never intended. |
| Q-2 | For a user-owned (personal) project with no org, is the "parent" the owning user? | **Yes** — block if the owning user (`owner_user_id`) is soft-deleted. | A project is owned by an org **or** a user (DB CHECK `projects_owner_xor`, `0002_tenancy.py:78-81`); the owner is the parent in both modes, so both must be liveness-checked for full coverage. |
| Q-3 | Should `AdminOpsView` pre-filter to only-restorable resources? | **No — server-side 409 only** this task; UI pre-filter is FU-1. | Keeps the fix backend-focused and fully testable; the 409 gives a clear, honest error. UI filtering needs a new query surface and is a separate increment. |

## 4. Reproduction

Deterministic, integration-level (admin principal, one org with one project):

1. Create org O with project P (org-owned). Create agent A under P.
2. Soft-delete P (`ProjectService.soft_delete`) — this does **not** touch A, so A keeps
   `deleted_at IS NULL`. Soft-delete A as well (so it is restorable).
3. `POST /api/admin/restore/agent/{A.id}` as an admin.
4. **Observed:** 200, A restored — A is now live while its parent project P is soft-deleted.
5. **Expected (post-fix):** 409, body names the dead ancestor ("restore the parent project
   first"); A stays soft-deleted.

The workflow/chatroom variants reproduce identically through the deeper chain: soft-delete
the parent **workspace** (or the project above it) while leaving the workflow/chatroom row's
own `deleted_at` set, then attempt the child restore.

## 5. Root Cause Analysis

The causal chain from trigger to symptom:

1. Soft-delete is a `deleted_at` stamp, and `ProjectService.soft_delete` deliberately does not
   cascade to a project's children (`project_service.py:144-163`) — the codebase enforces child
   unreachability at **read time** by walking ancestors, not by cascading writes
   (`access.py:69-77`, `a2a_service.py:389-391`). The DB `ondelete="CASCADE"` FKs
   (`agents.project_id`, `workflows.workspace_id`, `chatrooms.workspace_id`,
   `workspaces.project_id`, `projects.owner_org_id`) fire only on hard row DELETE, never on a
   soft `deleted_at` stamp.
2. Each child `admin_restore` clears only its own `deleted_at`
   (`project_service.py:198-207`, `agent_service.py:548-555`, `workflow_service.py:252-259`,
   `chatroom_service.py` restore path) and never reads an ancestor's `deleted_at`.
3. The restore route dispatches to those methods and treats any truthy return as success
   (`admin_projects.py:143-151`) — it has every facade in hand but performs no ancestor check.

**Root cause:** the restore path has no ancestor-liveness precondition. The earliest link
whose correction prevents the symptom is step 3 — a guard at the route (which already holds
all five facades) that walks the ancestor chain before the restore write. Steps 1-2 are the
*design context* (soft-delete + read-time liveness), not defects to change.

## 6. Blast Radius and Sibling Suspects

- **Blast radius.** Any reader that lists/looks up a child by `deleted_at IS NULL` on the
  child table alone, without walking ancestors, will surface the orphan. Access/authorization
  paths are **already protected** by the read-time ancestor walk (`access.py:69-77`,
  `a2a_service.py:389-391`), so the confirmed exposure is narrower than a raw "live orphan"
  sounds — it is the naive-listing and any future non-walking reader that see it. This is why
  the finding is medium / PLAUSIBLE, not high. No persisted data is corrupted; the row's own
  columns are valid — only the cross-row invariant is violated, and only transiently until the
  ancestor is also restored.
- **Sibling suspects.**
  - `restore_user` and `restore_org` (top-level, no parent) — **cleared**: users/orgs have no
    ancestor to check.
  - The user-facing (non-admin) restore paths — `OrgService.restore` cascades
    (`org_service.py`), and there is no user-facing project/agent/workflow/chatroom restore
    that bypasses this guard — **cleared** (the admin route is the only six-type restore
    surface). Confirmed the route is the sole caller of the child `admin_restore` methods.
  - Parent chain depth — **confirmed** a workspace can be live while its project is dead
    (project→workspace is not cascaded), so the guard must walk the **full** chain
    (workspace → project → owner), not just the immediate parent.

## 7. Fix Design

Route-level orchestration in `admin_projects.py` (SoC: the route already depends on every
facade; no context reads another context's tables). Before performing a **child** restore,
resolve the child's ancestor ids through the owning contexts and verify every ancestor is
live; if any is soft-deleted, raise 409 without attempting the restore.

**Ancestor chains** (each link soft-deletable; every hop uses an existing `get_*` reader
whose default `include_deleted=False` returns `None` for a soft-deleted or missing row —
`None` == not live):

| Child | Chain to verify live |
|---|---|
| project | owner: `owner_org_id` → org, **or** `owner_user_id` → user |
| agent | project → (org \| user) |
| workflow | workspace → project → (org \| user) |
| chatroom | workspace → project → (org \| user) |

**Reading the soft-deleted child's own parent id.** The child being restored is itself
soft-deleted, so its parent-id must be read with `include_deleted=True`:
`AgentsFacade.get_agent` (`facade.py:81`) and `TenancyFacade.get_project` (`facade.py:56`)
already expose that flag; `WorkflowFacade.get_workflow` (`facade.py:39`) and
`ConversationFacade.get_chatroom` (`facade.py:78`) do **not** and must gain it (see new
surface below). Ancestors themselves are expected live, so the default (deleted-filtered)
`get_*` is correct for them — a `None` return is the block signal.

**New facade surface (minimal):**
1. `TenancyFacade.get_org(org_id, *, include_deleted=False) -> Org | None` — the only missing
   reader; the sole way to check a project's parent-org liveness (`OrgRepository` is already
   wired at `facade.py:23`; add/route to its `get_by_id`). `Org.deleted_at` exists
   (`tenancy/domain/models.py:54`).
2. `WorkflowFacade.get_workflow` — add an `include_deleted: bool = False` param so the
   soft-deleted workflow's `workspace_id` can be read. Note the current method swallows all
   exceptions and returns `None` (`facade.py:40-44`) — the added path must not mask a genuine
   read error as "missing"; preserve the existing behavior only for the not-found case.
3. `ConversationFacade.get_chatroom` — add `include_deleted: bool = False` so the soft-deleted
   chatroom's `workspace_id` can be read. `get_workspace` (`facade.py:48`) already exists for
   the workspace→project hop and, being deleted-filtered by default, doubles as the workspace
   liveness check.

**Guard placement & ordering** (per child restore):
1. Resolve the child with `include_deleted=True` to obtain its immediate parent id. If the
   child does not exist at all → let the existing restore return falsy → 404 (unchanged). If
   it exists but is already live (not soft-deleted) → the existing restore returns falsy → 404
   (unchanged); no need to special-case.
2. Walk the ancestor chain via the deleted-filtered `get_*` readers. On the first `None`
   (a soft-deleted or missing ancestor), raise 409 naming the offending ancestor type; do not
   call the restore method.
3. Otherwise proceed to the existing restore, which still enforces its own 404 (not
   soft-deleted) and 409 (unique-name collision) semantics.

**409 signalling.** The check lives in the route, so it raises `HTTPException(status_code=409, …)`
directly with a distinct, honest message ("Cannot restore this {type}: its parent {parent_type}
is deleted — restore the parent first."). It is intentionally a *different* 409 message from the
existing unique-name `RestoreConflict` mapping (`admin_projects.py:144-149`); both are 409 but
carry distinct detail text so the admin can tell them apart. No new cross-layer exception type
is required — the walk is pure route orchestration over facade readers. A small private helper
(e.g. `_assert_ancestors_live(...)` in the route module) encapsulates the per-type chain so the
six-type dispatch stays readable.

**Data repair.** None required — no persisted data is corrupt (see §6); the guard is
preventive. Any orphan already created by a prior restore self-heals when its ancestor is
restored, and remains unreachable through the existing read-time walks until then.

## 8. Regression Test Plan

Test-first. `/build` writes these before touching the fix:

- **Route guard (primary), `tests/unit/test_admin_restore.py`** — extend the existing
  `TestRestoreRouteDispatch` style with recording/faked facades: for each of `project`,
  `agent`, `workflow`, `chatroom`, a case where an ancestor `get_*` returns `None`
  (soft-deleted) asserts the route raises `HTTPException` 409 with the parent-deleted detail
  and **never calls** the child's restore method. These fail against current code (no guard →
  restore is called, returns success). Include: agent with a deleted project; workflow/chatroom
  with a deleted workspace **and** a separate case with a live workspace but deleted project
  (proves the full-chain walk, not just the immediate parent); project with a deleted owner-org
  and, separately, a deleted owner-user (Q-2).
- **Happy path preserved** — for each child, all ancestors live → the restore method is called
  and 200 is returned (guards against over-blocking).
- **404/existing-409 unchanged** — a not-soft-deleted child still 404s; a unique-name collision
  still 409s with its original message.
- **New facade readers** — unit coverage that `TenancyFacade.get_org` filters soft-deleted by
  default and returns the row with `include_deleted=True`; that `get_workflow`/`get_chatroom`
  with `include_deleted=True` return a soft-deleted row's parent id.

## 9. Risks and Rollback

- **Over-blocking** — a bug in the chain walk could reject a legitimate restore whose ancestors
  are all live. Mitigated by the happy-path AC and per-type chain cases.
- **Extra reads per restore** — each child restore now performs 1-3 additional facade reads.
  Admin restore is a rare, non-hot path; negligible.
- **`get_workflow` exception-swallowing** — adding `include_deleted` must not let a real read
  error read as "ancestor missing" and mis-emit a 409; the fix must scope the swallow to
  not-found only. Called out in §7 and covered by a reader unit test.
- **Rollback** — `git revert` of the implementation commit; the guard is additive (a helper +
  three facade-reader tweaks) and self-contained. Removing it restores prior behavior exactly.

## 10. Acceptance Criteria

- [x] AC-1: the §8 route-guard tests fail before the fix and pass after — restoring a
      `project`/`agent`/`workflow`/`chatroom` whose ancestor chain contains a soft-deleted org,
      user, project, or workspace returns **409** (distinct parent-deleted detail) and does not
      call the child's restore method. `TestRestoreRouteAncestorGuard::test_dead_ancestor_blocks_with_409`
      (8 parametrized cases; verified red before the fix, green after).
- [x] AC-2: the guard walks the **full** chain — a workflow/chatroom under a *live* workspace
      whose *project* is soft-deleted is still rejected (the `...-project` cases in the AC-1
      parametrization stage a live workspace + `get_project` -> None).
- [x] AC-3: personal (user-owned) projects are covered — restoring a project whose
      `owner_user_id` is soft-deleted is rejected (`...returns6-user`); org-owned projects check
      `owner_org_id` (`...returns3-org`, `...returns7-org`) (Q-2).
- [x] AC-4: happy path preserved — with all ancestors live, each child restore still succeeds
      (200) and reaches its restore method (`test_all_ancestors_live_restores`); `user`/`org`
      top-level restores skip the guard entirely (`test_top_level_restore_skips_guard`).
- [x] AC-5: existing 404 (not soft-deleted / not found) is unchanged — an already-live or
      missing child falls through to 404, not 409 (`test_already_live_or_missing_child_falls_through_to_404`);
      unique-name 409 semantics unchanged (`test_maps_restore_conflict_to_409`); the parent-deleted
      409 carries a distinct detail (`test_parent_deleted_409_detail_is_distinct`).
- [x] AC-6: new facade readers behave — `TestFacadeReaders`: `TenancyFacade.get_org`
      (deleted-filtered by default; `include_deleted=True` passes through), `get_workflow`/
      `get_chatroom` gain `include_deleted`, and `get_workflow` returns `None` on
      `WorkflowNotFound` while a genuine read error propagates (not masked as not-found).
- [x] AC-7: mechanical gates on touched files — `test_admin_restore.py` 41 passed; `ruff check`
      + `ruff format --check` clean on my files; `mypy` on the changed modules introduces no new
      errors (only pre-existing import-followed debt, none in the new code). Full `pytest tests/unit`:
      1527 passed, 4 pre-existing failures unrelated to this task (see FU-4).

## 11. SRS Delta

None — the fix restores the documented `[R8.13]` admin-restore behavior to honor the system's
existing ancestor-liveness invariant; it defines no new behavior.

## 12. Deviation Log

- D-1: **User-hop liveness needs an explicit `deleted_at` check.** §7 assumed every ancestor
  `get_*` reader is deleted-filtered so `None` == not live. Verified during build that
  `IdentityFacade.get_user` -> `UserRepository.get_by_id` does **not** filter soft-deleted
  (`identity/infrastructure/repositories.py:65-67`), unlike the org/project/workspace/chatroom
  getters. The guard therefore uses a uniform liveness predicate
  `_is_live(row) = row is not None and row.deleted_at is None` for the owner checks rather than
  relying on `None` alone. Design shape and ACs are unchanged (AC-3 still holds); this is a
  mechanism refinement. All checked domain models expose `deleted_at`.
- D-2: **`get_org` delegates to `OrgRepository.get`, not `get_by_id`.** §7.1 named `get_by_id`;
  the actual repository reader is `OrgRepository.get(org_id, *, include_deleted=False)`
  (`tenancy/infrastructure/repositories.py:72`), which already supports the flag. No behavior
  difference; the facade method still filters soft-deleted by default.
- D-3: **`get_workflow` broad-swallow narrowed as §7.2 required.** Confirmed `get_workflow` has
  no other callers (grep), so narrowing `except Exception` to `except WorkflowNotFound` has zero
  blast radius; a genuine read error now propagates (AC-6) instead of being masked as `None`.

## 12a. Quality / Security self-audit

- SoC: every ancestor hop resolves through the owning context's facade; no cross-context table
  reads. Admin-gated (`require_admin`). `resource_id` already validated as `uuid.UUID` at the
  path boundary; facade readers use parameterized queries (no string SQL). Fail-closed on a dead
  ancestor, fail-open only to the existing 404 for a missing/already-live child. The 409 detail
  names only the parent *type* (org/user/project/workspace), not identifiers — acceptable for an
  admin who already holds full restore powers.

## 13. Follow-ups

- FU-1: `AdminOpsView` (frontend) could pre-filter or disable resources whose ancestor chain is
  soft-deleted so the admin never attempts a doomed restore (Q-3 deferred this). Needs a
  read-only "restorable?" query surface.
- FU-2: reconcile with the sibling dossier's deferred cascade question
  (`2026-07-12-admin-restore-widen-resource-types` FU-1) — whether admin restore should ever
  offer an opt-in cascade-restore-ancestors action instead of only blocking.
- FU-3: consider extracting the ancestor-liveness walk into a shared helper if a second caller
  (e.g. the FU-1 UI query) needs it, to avoid duplicating the chain definition.
- FU-4: **pre-existing unit failures unrelated to this task** — RESOLVED. Investigated on request:
  root cause was the enum-response refactor (`9915a1d`, 2026-07-10 review pass, *not* the activities
  work as first guessed), which made `MessageOut.sender_type`, `AttachmentOut.status/scan_status`,
  and `KnowmapDocumentOut.status/scan_status` real enums while four unit fixtures still faked enums
  with `SimpleNamespace(value=...)` — rejected by pydantic at response construction. Production paths
  pass real domain enums and were correct; only the fixtures were stale. Fixed test-only in a
  separate `test(backend)` commit (`test_message_attachments_out.py`, `test_knowmap_authz.py`); full
  `pytest tests/unit` now 1531 passed, 0 failed.
- FU-5: **pre-existing `ruff format` drift** at `contexts/workflow/application/workflow_service.py:352`
  (an unrelated committed line, not this change's hunk at line 114). Left untouched to avoid
  sweeping unrelated reformatting into this commit; should be formatted by that hunk's owner.
- FU-6: **behavioral (live) verification deferred.** The new 409 is covered by route-level unit
  tests with faked facades; a full-stack integration run (compose + admin principal + a seeded
  soft-deleted hierarchy) was not executed here. Worth an integration test when the FU-1 UI lands.
