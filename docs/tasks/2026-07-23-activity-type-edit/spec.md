---
type: feature
status: implemented
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
| Q-1 | Which fields are editable? | **Metadata + behavioral, with a version bump (Option A / Q-2a).** `name`, `retention_days` (safe metadata) editable; `payload_schema`, `validator_kind`/`validator_config` (behavioral) editable too, bumping `version` on change. `key` stays immutable (OQ-1). | Owners need to correct real behavioral mistakes, not only typos. Behavioral edits carry the version bump so the change is at least recorded; the mcp validator-scope check (AC-4) is therefore in scope. |
| Q-2 | Does a behavioral edit bump `version` and how do old submissions relate to it? | **Q-2a — in-place mutate + bump `version`.** The type row is updated in place; `version` increments only when a behavioral field changes (metadata-only edits leave it untouched). Old submissions keep referencing the same `activity_type_id` and read under the new definition; the incremented `version` is the only provenance marker. | The `version` column exists but is unused; Q-2a makes it meaningful at the smallest cost. Immutable versioning (Option B) was rejected as too large for v1 (touches the `key` partial-unique and the create flow). |
| Q-3 | Edit while the type is active in rooms? | **Metadata editable anytime; behavioral edits rejected (409) while any `active` `ActivityActivation` references the type.** | `name`/`retention_days` are safe to change live. A `payload_schema`/`validator` change could desync an in-flight activation, so it is blocked until the type is idle. Reuses `ActivationRepository.list_active_for_type` (`activation_repo.py:90`), the same query delete's cascade-end uses. |

## 4. Current State

- Types are created via `POST …/activity-types` → `ActivityTypeService.register`
  (`type_service.py:32-72`); `version` is set by the column `server_default=sa.text("1")`
  (`tables.py:37`) — `create()` never passes it, and no path ever updates it.
- `ActivityTypeRepository` exposes `create` (`type_repo.py:58`), `get` (`:99`),
  `list_for_project` (`:113`), `soft_delete` (`:129`) only — **no update**.
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
**Option A, full field set (Q-1 + Q-2a + Q-3).** A single in-place `PATCH` handles both
metadata and behavioral edits:

- `name`, `retention_days` — editable anytime, `version` untouched.
- `payload_schema`, `validator_kind`, `validator_config` — editable, but only when the type
  has no `active` activation (else 409); re-run the register-time validators; `_assert_mcp_binding_in_project`
  applies when the new config is `mcp`; increment `version` when any behavioral field changes.
- `key` — never editable (OQ-1).

`version` increments once per PATCH that changes at least one behavioral field, computed by
diffing the submitted behavioral fields against the stored row. Option B (immutable
versioning) was rejected for v1 as disproportionate.

## 6. Detailed Changes

**Backend**
- `ActivityTypeRepository.update(type_id, **fields) -> bool` (guarded on `deleted_at IS NULL`),
  mirroring `soft_delete` (`type_repo.py:129`). Increments `version` when the caller passes any
  behavioral field.
- `ActivityTypeService.update(...)` — load the current row (404 if missing/deleted); diff
  behavioral fields; if any changed, re-run `validate_schema_wellformed` + `_validate_validator_config`
  (`type_service.py:46-47,105-128`) and reject with a new domain error (→ 409) when
  `ActivationRepository.list_active_for_type(type_id)` (`activation_repo.py:90`) is non-empty;
  emit an `activity_type.updated` audit event.
- New domain error `ActivityTypeActive` (or similar) mapped to **409** in
  `interfaces/error_mapping.py` (alongside the existing `ActivityTypeKeyConflict→409`).
- `ActivitiesFacade.update_type(...)` passthrough mirroring `register_type` (`facade.py:68-93`);
  tenant-guard by matching `existing.project_id != project_id` inside the facade, the same shape
  `delete_type` uses (`facade.py:160`).
- `PATCH …/activity-types/{type_id}` route in `app/api/v1/activities.py`: `assert_project_owner`
  (mirroring the delete route, `activities.py:283`); apply `_assert_mcp_binding_in_project`
  (`activities.py:245`) when `validator_kind is ValidatorKind.MCP`, same guard as register
  (`activities.py:228`); commit.
- Migration: none (existing columns).

**API contract** — new PATCH; `gen:api` rerun: yes.

**Frontend**
- `activities/api/index.ts`: `updateActivityType(projectId, typeId, body)`, alongside the
  existing `registerActivityType` (`api/index.ts:24`) / `deleteActivityType` (`:34`).
- `ActivityTypeForm.vue` (`components/ActivityTypeForm.vue`): an `edit` mode (pre-filled from
  the row; `key` field disabled). Calls `updateActivityType` instead of `registerActivityType`
  (`ActivityTypeForm.vue:15,99`) when in edit mode; surface 409/422 via `useServerErrors`.
- `ActivityTypesView.vue`: add an `edit` entry to `actionItems` (`:86-88`) and an `onAction`
  branch (`:76-78`); invalidate `activityKeys.types(projectId)` (`queries/index.ts:7`) on
  success, as delete already does (`ActivityTypesView.vue:55,61,81`).
- i18n en + zh-TW for edit labels.

## 7. NFR Checklist
- [ ] i18n — new strings both locales.
- [ ] Audit log — `activity_type.updated`.
- [ ] Tenant isolation — owner-gated + type-project guard (mirror delete).
- [ ] Error handling UX — 409/422 surfaced on the form as in create.
- [ ] Performance — single-row update.

## 8. Security Considerations

Owner-only; tenant-guarded. Validator edits are in scope (Q-1), so the same
`_assert_mcp_binding_in_project` check the register route uses (`activities.py:245`) must run on
PATCH whenever the new `validator_kind` is `mcp` — otherwise the FU-6 hardening is bypassable
via edit (AC-4 guards this).

## 9. Quality Notes
- Reuse `ActivityTypeForm`, `useServerErrors`, the delete route's owner+tenant guard shape,
  and `_assert_mcp_binding_in_project`.
- Do not duplicate validation — the service's register-time validators must be shared with
  update.

## 10. Risks and Rollback
- The schema/validator-edit-vs-submissions question (Q-2) is the main risk; deferring
  behavioral edits keeps v1 safe. Additive route; reversible.

## 11. Acceptance Criteria
- [x] AC-1: An owner edits a type's `name`/`retention_days` and the list reflects it without
  reload; a non-owner gets 403. (`test_activity_type_edit.py::TestUpdateService::test_metadata_only_edit_does_not_bump_version`,
  `::TestUpdateRoute::test_owner_update_commits_and_returns_type` + `::test_non_owner_is_refused_before_any_write`;
  frontend `ActivityTypeForm.test.ts` edit pre-fill/PATCH + `ActivityTypesView` `onUpdated` invalidates `activityKeys.types`.)
- [x] AC-2: `PATCH` of an unknown/soft-deleted/foreign-project type returns 404.
  (`::test_unknown_type_raises_and_writes_nothing`, `::test_cross_project_type_is_refused`.)
- [x] AC-3: `activity_type.updated` audit emitted on a successful edit.
  (`::test_metadata_only_edit_does_not_bump_version` asserts the emit.)
- [x] AC-4: a PATCH moving to an `mcp` `validator_config` with a foreign `binding_id` is
  rejected (422), same as register. (`::TestUpdateRoute::test_mcp_foreign_binding_is_rejected_before_write`.)
- [x] AC-5: new strings resolve en + zh-TW; lint passes. (Keys added to both locale files; `pnpm lint` clean.)
- [x] AC-6: a PATCH that changes a behavioral field (`payload_schema`/`validator_kind`/
  `validator_config`) increments `version` by 1; a metadata-only PATCH leaves `version`
  unchanged. (`::test_behavioral_edit_bumps_version` + `::test_metadata_only_edit_does_not_bump_version`
  assert `bump_version` True/False; the repo applies `version = version + 1`.)
- [x] AC-7: a behavioral PATCH while the type has an `active` activation returns 409; a
  metadata-only PATCH on the same active type succeeds.
  (`::test_behavioral_edit_while_active_is_rejected` + `::test_metadata_edit_while_active_succeeds`.)

## 12. Test Plan
- Backend unit: service update happy path + `activity_type.updated` audit (AC-1, AC-3);
  unknown/soft-deleted/foreign-project → 404 (AC-2); non-owner → 403 (AC-1); mcp foreign
  `binding_id` → 422 (AC-4); behavioral change bumps `version`, metadata-only does not (AC-6);
  behavioral edit while active → 409, metadata edit while active succeeds (AC-7).
- Frontend component: edit mode pre-fills from the row and submits a PATCH via
  `updateActivityType`; `key` disabled; 409/422 surfaced through `useServerErrors`.

## 13. SRS Delta

Amend `[R30.23]` — append the following after its existing first sentence (the "author, list,
and delete" sentence becomes "author, list, edit, and delete"):

> A Project Owner may author, list, edit, and delete `ActivityType`s from a project-scoped
> management surface. Editing a type's safe metadata (`name`, `retention_days`) is permitted at
> any time. Editing its behavioral definition (`payload_schema`, `validator_kind`/
> `validator_config`) re-runs the same well-formedness and validator-scope checks as
> registration ([R30.02], [R30.24]), increments the type's `version`, and is rejected while any
> `active` `ActivityActivation` references the type. Editing never changes a type's `key`.
> Editing emits an `activity_type.updated` audit event and requires Project Owner capability.

(The existing deletion sentences of [R30.23] are unchanged.)

## 14. Open Questions
- OQ-1: Is `key` ever editable (with the reference-breakage risk), or permanently immutable?

## 15. Deviation Log

- D-1: The tenant/project guard was placed in `ActivityTypeService.update` (single row
  load, reject `existing.project_id != project_id` → 404) rather than in the facade as §6
  suggested. Reason: the service must load the row anyway to diff behavioral fields; a
  facade-level guard would force a second fetch. Observable behavior is identical
  (unknown/soft-deleted/foreign → 404).
- D-2: The PATCH body (`ActivityTypeUpdateIn`) requires the full editable field set (§6 left
  the body shape implicit). Reason: the edit form always resubmits the whole object, so a
  full-replace avoids the PATCH null-vs-absent ambiguity (notably `retention_days: null`
  meaning "clear" vs "unchanged") and lets the route reuse register's
  `if validator_kind is MCP` guard verbatim. `version` is still bumped only when a
  behavioral field actually differs from the stored row (Q-2a preserved).

## 16. Follow-ups

- FU-1: `ActivityTypeOut` does not expose `version`, so the UI cannot display it and AC-6 is
  verified via the service test rather than the API response. If the UI should surface the
  version or a bumped-since indicator, expose `version` in the response model (and rerun
  `gen:api`) in a follow-up.
- FU-2 (security hardening, no current attack path): `_assert_mcp_binding_in_project` runs at
  the route only, so a future internal (non-route) caller of `update_type`/`register_type`
  would bypass the cross-project binding check. This mirrors register exactly. If internal
  callers are ever added, move the check into the service.
- FU-3: `SchemaBuilder`'s edit rehydration coerces a property whose `type` is outside the
  flat builder's set to `'string'`. Harmless today (no path produces such a schema), but once
  raw-JSON schema editing lands (the authoring dossier's FU-5) editing such a type through the
  guided builder would silently rewrite it. The guided builder should refuse to rehydrate a
  schema it cannot faithfully represent then.
