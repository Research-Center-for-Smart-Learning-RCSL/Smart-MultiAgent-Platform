---
type: feature
status: draft
created: 2026-07-23
requirements: [R30.02, R30.23]
depends_on: []
---

# Edit an existing activity type

## 1. Summary

Activity types are immutable after creation: there is no update route, and the
`ActivityType.version` column (`type_repo.py` `_TYPE_COLS`) is set to 1 at create and never
bumped. Owners who make a typo in a name, want to adjust `retention_days`, or need to tweak a
validator config must delete and recreate the type — which cascade-ends activations and
orphans historical submissions' provenance. This feature adds an owner-only edit path.
Follows up FU-2 of `docs/tasks/2026-07-23-activities-type-authoring-ui/`.

## 2. Goals and Non-goals

**Goals**
- An owner can edit an existing activity type from the authoring UI (an edit action on each
  list row, reusing the create form pre-filled).
- A `PATCH /api/projects/{project_id}/activity-types/{type_id}` route (owner-only) applying
  the allowed field changes and emitting an `activity_type.updated` audit event.

**Non-goals**
- Changing `key` (the per-project identity; renaming it would break any external reference
  and the `uq_activity_types_project_key_active` semantics). Out of scope; recorded as an OQ.
- Retroactively re-validating or migrating existing submissions when the schema/validator
  changes (see Q-2).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which fields are editable? | **To decide.** Candidates: `name`, `retention_days` (safe metadata) always; `payload_schema`, `validator_kind`/`validator_config` (behavioral) only via a version bump, or not at all. | Editing the schema/validator of a type with existing submissions changes how past data is interpreted. Safe-metadata-only is the low-risk baseline; behavioral edits need the version story (Q-2). |
| Q-2 | Does a behavioral edit bump `version` and how do old submissions relate to it? | **To decide.** Options: (a) in-place mutate + bump `version`, old submissions keep the old `version` semantics implicitly; (b) immutable — a behavioral change creates a new type row that supersedes the old. | The `version` column exists but is unused; this decides whether it becomes meaningful. |
| Q-3 | Edit while the type is active in rooms? | **To decide.** Metadata edits are safe live; schema/validator edits could desync an in-flight activation. Options: allow, or block behavioral edits while `active`. | Mirrors the delete-cascade concern from the authoring dossier's Q-3. |

## 4. Current State

- Types are created via `POST …/activity-types` → `ActivityTypeService.register`
  (`type_service.py:32-72`); `version` defaults to 1 and is never updated.
- `ActivityTypeRepository` exposes `create`, `get`, `list_for_project`, `soft_delete` only —
  **no update** (`type_repo.py`).
- `ActivityTypeService` validates `payload_schema` well-formedness and validator config at
  register (`type_service.py:46-47,105-128`); an edit path would reuse the same validation.
- Domain errors map: `ActivityTypeNotFound` 404, `ActivityTypeKeyConflict` 409,
  `PayloadSchemaInvalid` 422, `ValidatorConfigInvalid` 422 (`interfaces/error_mapping.py`).
- The mcp validator config now also gets a route-layer project-scope check
  (`activities.py` `_assert_mcp_binding_in_project`) — an edit route must apply the same.
- Frontend: `ActivityTypeForm.vue` is create-only (`registerActivityType`); the list
  (`ActivityTypesView.vue`) has only a delete row action.

## 5. Design

### Options considered
- **Option A — in-place PATCH** of the allowed fields (per Q-1), bumping `version` on a
  behavioral change. Smallest change; reuses the create form and validation. Old submissions
  keep referencing the same `activity_type_id` with the new definition (Q-2a).
- **Option B — immutable versioning**: a behavioral edit inserts a new type row (new
  `version`, same `key` after tombstoning the old), old submissions stay bound to the old
  row. Cleaner provenance; larger (touches the `key` partial-unique and the create flow).

### Decision
To be finalized at approval with Q-1..Q-3. Recommended starting point: Option A limited to
`name` + `retention_days` (safe metadata) with the schema/validator edit deferred to a later
phase if Q-2 favors versioning — so v1 ships a low-risk edit without committing the version
semantics prematurely.

## 6. Detailed Changes

**Backend**
- `ActivityTypeRepository.update(type_id, **fields) -> bool` (guarded on `deleted_at IS NULL`).
- `ActivityTypeService.update(...)` — re-run the relevant validation for changed fields; emit
  `activity_type.updated` audit.
- `ActivitiesFacade.update_type(...)` passthrough.
- `PATCH …/activity-types/{type_id}` route: `assert_project_owner`, tenant-guard the type's
  project, apply the mcp scope-check if validator changes, commit.
- Migration: none (existing columns).

**API contract** — new PATCH; `gen:api` rerun: yes.

**Frontend**
- `activities/api/index.ts`: `updateActivityType(projectId, typeId, body)`.
- `ActivityTypeForm.vue`: an `edit` mode (pre-filled from the row; disable `key`; hide/lock
  the fields Q-1 excludes). Row "Edit" action in `ActivityTypesView.vue`; invalidate
  `activityKeys.types` on success.
- i18n en + zh-TW for edit labels.

## 7. NFR Checklist
- [ ] i18n — new strings both locales.
- [ ] Audit log — `activity_type.updated`.
- [ ] Tenant isolation — owner-gated + type-project guard (mirror delete).
- [ ] Error handling UX — 409/422 surfaced on the form as in create.
- [ ] Performance — single-row update.

## 8. Security Considerations

Owner-only; tenant-guarded. If validator edits are in scope, the same
`_assert_mcp_binding_in_project` check the register route uses must run on PATCH — otherwise
the FU-6 hardening is bypassable via edit.

## 9. Quality Notes
- Reuse `ActivityTypeForm`, `useServerErrors`, the delete route's owner+tenant guard shape,
  and `_assert_mcp_binding_in_project`.
- Do not duplicate validation — the service's register-time validators must be shared with
  update.

## 10. Risks and Rollback
- The schema/validator-edit-vs-submissions question (Q-2) is the main risk; deferring
  behavioral edits keeps v1 safe. Additive route; reversible.

## 11. Acceptance Criteria
- [ ] AC-1: An owner edits a type's `name`/`retention_days` and the list reflects it without
  reload; a non-owner gets 403.
- [ ] AC-2: `PATCH` of an unknown/soft-deleted/foreign-project type returns 404.
- [ ] AC-3: `activity_type.updated` audit emitted on a successful edit.
- [ ] AC-4 (if validator edits in scope): a PATCH moving to an `mcp` config with a foreign
  `binding_id` is rejected (422), same as register.
- [ ] AC-5: new strings resolve en + zh-TW; lint passes.

## 12. Test Plan
- Backend unit: service update happy path + audit; unknown/foreign → 404; validator-scope
  reject (if in scope).
- Frontend component: edit mode pre-fills and submits a PATCH; 409/422 surfaced.

## 13. SRS Delta

Amend `[R30.23]` to state an owner may also **edit** an `ActivityType` (fields per Q-1),
emitting `activity_type.updated`. Draft the exact wording at approval once Q-1/Q-2 are fixed.

## 14. Open Questions
- OQ-1: Is `key` ever editable (with the reference-breakage risk), or permanently immutable?

## 15. Deviation Log

Appended by /build.

## 16. Follow-ups

To be discovered during build.
