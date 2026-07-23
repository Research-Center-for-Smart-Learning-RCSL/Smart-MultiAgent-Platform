---
type: feature
status: approved
created: 2026-07-23
requirements: [R30.02, R30.05, R30.07, R30.11, R30.17, R30.19, R30.20, R30.21, R30.22]
depends_on: []
---

# Activity-type authoring UI

## 1. Summary

Project Owners can currently create an `ActivityType` (the template a facilitator
activates in a chatroom) only out-of-band — a direct `POST /api/projects/{id}/activity-types`
call, a seed, or admin tooling. The backend to register/list types is complete and even
wrapped in the generated client, but no hand-written frontend surface calls it, and there
is no HTTP route to delete a type at all. This feature adds an owner-scoped
"Activity types" management page (list, create, delete) under project settings, wires the
missing `registerActivityType` and a new `DELETE` route through the activities slice, and
supports authoring the two validator kinds that work end-to-end today (`webhook`, `mcp`).
Background: `docs/activities-type-authoring-gap.md`.

## 2. Goals and Non-goals

**Goals**
- A project-scoped, owner-only page listing the project's live activity types with a
  create action and a per-row delete action.
- A create form that authors `key`, `name`, optional `retention_days`, a `payload_schema`
  built through a guided field builder, and a `validator_kind` + `validator_config` for
  `webhook` or `mcp`.
- A `DELETE /api/projects/{project_id}/activity-types/{type_id}` route (owner-only) that
  soft-deletes the type and cascade-ends every active activation referencing it, notifying
  each affected room, so no room is left with a dangling activation.
- All new backend behavior covered by the existing audit trail and RFC-7807 error surface;
  all new UI strings in en + zh-TW.

**Non-goals**
- **`in_process` validator authoring.** The platform ships zero registered in-process
  validators (`registry.py` has no listing accessor and no `app/plugins/` registration
  site exists), so a type authored with `in_process` cannot even be registered (422). Out
  of scope until first-party validators are registered; recorded as FU-1.
- **Editing an existing type.** Types are immutable post-creation today (no update route,
  no `version`-bump path); this feature does not add one. FU-2.
- **MCP tool enumeration.** The `mcp` sub-form takes `tool_name` as a text input; listing
  the tools a binding exposes is out of scope. FU-3.
- **Closing in-flight `ActivitySession`s on delete.** Cascade-ending the activation already
  stops new submissions (R30.22); force-closing open sessions is not done. FU-4.
- **A raw JSON-Schema editor.** The builder is the only authoring path in v1; a raw/advanced
  JSON mode is FU-5. A read-only JSON preview of the built schema is in scope.
- Changing how activities are activated, joined, submitted, or scored in a chatroom.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which validator kinds does the v1 create form support? | `webhook` + `mcp` | `webhook` (needs only a `url`) and `mcp` both dispatch and score end-to-end today; `in_process` has zero registered validators and no way to enumerate them, so authoring it is a dead path. User chose the fuller of the two working kinds over webhook-only. |
| Q-2 | How deep is the `payload_schema` editor? | Guided field builder | The audience is facilitators, not schema authors; a builder (add field: name/type/required) that emits JSON Schema is friendlier than a raw textarea. Larger build; accepted deliberately. |
| Q-3 | Delete behavior when a type is active in a room? | Cascade-end the activation(s) | On soft-delete, every active activation referencing the type is ended and its room notified. Chosen over blocking (409, "end the activity first") for smoother UX, accepting the cross-context write and the added service logic. |
| Q-4 | Does this depend on the in-progress `2026-07-22-activity-session-authz-and-validation` dossier? | No | That dossier touches session AuthZ, the activity watchdog, and optional enum-array assembly — none of the files or lines this task edits (`activities.py` type routes, `type_service`, `ProjectDetailView.vue`, a new activities settings view). No logical or overlap prerequisite. `depends_on: []`. |

## 4. Current State

**Backend — type registration complete, deletion has no route.**
- `POST /api/projects/{project_id}/activity-types` → `register_activity_type`, owner-gated
  via `assert_project_owner` (`backend/app/api/v1/activities.py:218-240`). Request model
  `ActivityTypeIn` (`activities.py:59-65`). Commits, returns `ActivityTypeOut`.
- `GET /api/projects/{project_id}/activity-types` → membership-gated list
  (`activities.py:243-251`).
- Facade `soft_delete_type` exists (`backend/contexts/activities/interfaces/facade.py:140-150`)
  → `ActivityTypeService.soft_delete` (`application/type_service.py:80-103`, pre-checks
  existence, raises `ActivityTypeNotFound`, emits `activity_type.deleted` audit) →
  `ActivityTypeRepository.soft_delete` (`infrastructure/repositories/type_repo.py:129-142`,
  idempotent `UPDATE ... SET deleted_at=now() WHERE id AND deleted_at IS NULL`). **No HTTP
  route exposes it.**
- `get`/`list_for_project` already exclude soft-deleted rows
  (`type_repo.py:99-127`); `key` is unique per project among live rows via partial index
  `uq_activity_types_project_key_active`, so re-registering a deleted key is allowed
  (`type_repo.py:70-96`).
- Validator config validation at registration (`type_service.py:105-128`): `in_process`
  requires a registered `validator_id` (`is_registered`); `webhook` requires `url`; `mcp`
  requires `tool_name` + UUID `agent_id` + UUID `binding_id`.
- Domain errors → HTTP: `ActivityTypeNotFound` 404, `ActivityTypeKeyConflict` 409,
  `PayloadSchemaInvalid` 422, `ValidatorConfigInvalid` 422
  (`domain/errors.py`, `interfaces/error_mapping.py:13-64`).
- **Referential exposure:** `type_service.soft_delete` has no guard against active
  activations. Deleting a type that is active in a room silently breaks new submissions
  (`submission_service.py:79-83` re-resolves the type → `ActivityTypeNotFound`) and leaves
  `get_active_activation` still returning the dangling row (`facade.py:137`). This is what
  Q-3's cascade closes.

**Frontend — consumer only, no authoring.**
- `frontend/src/slices/activities/api/index.ts` wraps list/activation/session/submission
  but has **no `registerActivityType` and no delete wrapper**. The generated client has
  `ActivitiesService.registerActivityType…Post` and `ActivityTypeIn`, unused.
- `ActivityPanel.vue` selects only existing types via `listActivityTypes`
  (`components/ActivityPanel.vue:49-56`); no create surface.
- **No project-settings sub-route exists.** Project owner surfaces are flat sibling routes
  linked from the `ProjectDetailView` header — precedent: the Skills page, a separate slice
  at `/projects/:projectId/skills` (`frontend/src/slices/skills/routes.ts:6-11`), linked
  from `ProjectDetailView.vue:163-173`.
- Owner gating composable: `useProjectRole(projectId)` → `{ isOwner, isAuthorized, decided }`
  (`frontend/src/slices/tenancy/composables/useProjectRole.ts`, exported from the slice
  index). Gate on `decided && isAuthorized` to avoid flash-hiding.
- **No reusable agent/binding picker.** Agent dropdowns are ad-hoc `computed` maps over an
  agents list fed to `SSelect` (e.g. `AgentInvocationConfigForm.vue:32-34`). No
  `listMcpBindings` API method exists — only the query-key stub
  `agentKeys.mcpBindings(agentId)` (`agents/queries/index.ts:30-31`).

**Requirements.** §30 defines registration/list/validators/retention/activation
(`REQUIREMENTS.md` R30.01–R30.22) but has **no requirement covering type deletion**; the
authoring surface is only implied by R30.17/R30.19 (plugin host + generic form). Deletion
and the owner authoring page are the SRS delta (§13).

## 5. Design

### Options considered

**Placement — where the authoring UI lives.**
- **Option A — project-settings sibling route in the `activities` slice.** New route
  `/projects/:projectId/activity-types` + a view, following the Skills precedent, linked
  from an owner-gated button in `ProjectDetailView`'s header. Matches the owner-only,
  project-scoped AuthZ of the register endpoint.
- **Option B — a modal launched from the chatroom `ActivityPanel`.** Rejected: the panel is
  per-room and participant-facing, while a type is project-wide and owner-only; authoring
  there conflates scopes and hides an owner action inside a participant surface.

**Delete integrity — Q-3.** Chosen: cascade-end. On soft-delete, the service finds every
`active` activation whose `activity_type_id` matches (across all rooms in the project),
ends each, and the route emits `activity.activation.ended` per affected room (reusing the
existing dispatch, `activities.py:472-478`). Rejected alternatives: block-on-active (409;
safer but worse UX per Q-3) and unconditional delete (leaves dangling activations).

**Schema authoring — Q-2.** Chosen: guided field builder. A repeating row editor (field
name, type from a fixed set: string/number/integer/boolean, required checkbox) that
assembles a JSON Schema `{type:"object", properties, required}` client-side, plus a
read-only JSON preview. The assembled schema is validated server-side (`PayloadSchemaInvalid`
422 is surfaced to the form) so the builder need not re-implement JSON-Schema validity.

### Decision

Build the authoring surface as an owner-only project-settings page in the existing
`activities` slice (Option A), add the missing `DELETE` route with cascade-end semantics,
support `webhook` and `mcp` validators (Q-1), and author `payload_schema` via a guided
field builder (Q-2). `in_process`, type editing, MCP tool enumeration, and a raw JSON
editor are consciously deferred (§2 non-goals, FU-1..5) so v1 ships a coherent
create/list/delete loop for the validators that actually score today. The `mcp` sub-form
reuses the established "computed SSelect over a useQuery list" idiom rather than a new
picker component, and requires one new backend read (`listMcpBindings`) plus its frontend
wrapper.

## 6. Detailed Changes

**Backend**
- `contexts/activities`: add an active-activation lookup + cascade-end used by delete.
  - `ActivationRepository`: add `list_active_for_type(project_id, activity_type_id) ->
    Sequence[ActivityActivation]` (or reuse an existing query if one fits).
  - `ActivityTypeService.soft_delete` (or a new `delete_type` orchestration on the facade):
    within the caller's transaction, end each active activation for the type before/after
    stamping `deleted_at`, returning the list of `(chatroom_id, activation_id)` ended so the
    route can emit the WS notifications post-commit. Keep the `activity_type.deleted` audit;
    each ended activation keeps its own end path/audit.
  - The context still imports only the conversation facade + shared_kernel (SoC preserved;
    activation-end is intra-context).
- No migration required (uses existing `deleted_at` column and activation status).

**API contract**
- New `DELETE /api/projects/{project_id}/activity-types/{type_id}` in `activities.py`:
  `assert_project_owner`, call the facade cascade-delete, `await db.commit()`, emit
  `activity.activation.ended` for each returned room via the existing
  `_dispatch_activation_ended`, return `204 No Content` (`response_model=None`). Mirrors
  `agent_groups.py:201` / `projects.py:214` delete shape.
- New `GET …/agents/{agent_id}/mcp-bindings` (or confirm an existing endpoint) returning the
  agent's MCP bindings `{id, name/label}` for the `mcp` validator picker. If an equivalent
  read already exists, wrap that instead of adding one.
- `gen:api` rerun required: **yes** (new DELETE + bindings read).

**Frontend** (`slices/activities`, plus one line in `slices/tenancy`)
- `api/index.ts`: add `registerActivityType(projectId, body)` and
  `deleteActivityType(projectId, typeId)` wrappers over the generated client; add
  `listMcpBindings(agentId)` (or place in the agents slice and re-export) for the picker.
- `routes.ts` (new in the slice) + register in `app/router.ts`: route
  `activityTypes.project` at `/projects/:projectId/activity-types`.
- `queries/index.ts` (new): `activityTypesKeys` factory (`list(projectId)`).
- New views/components:
  - `ActivityTypesView.vue` — list (STable/rows) + create modal + per-row delete
    (`useConfirmDialog` + `useToast`, invalidate `activityTypesKeys.list` on success).
    Modeled on `RagConfigListView.vue`.
  - `ActivityTypeForm.vue` — vee-validate + Zod (`types/schemas.ts`): key, name,
    retention_days, `SchemaBuilder`, validator-kind `SSelect` driving a conditional
    sub-form (`webhook`: url; `mcp`: agent `SSelect` + binding `SSelect` + tool_name input).
    Map server 409/422 back to fields via `useServerErrors`.
  - `SchemaBuilder.vue` — repeating field-row editor emitting a JSON Schema + read-only
    preview.
- `ProjectDetailView.vue`: add an owner-gated header button linking to the new route
  (`v-if="decided && isAuthorized"`), following the Skills button precedent.
- i18n: new keys in `slices/activities/locales/{en,zh-TW}.json`.

**Deploy/config** — none.

## 7. NFR Checklist

- [x] i18n — all new strings via `$t()`; en + zh-TW keys added under the activities slice.
- [x] Audit log — `activity_type.deleted` already emitted by `soft_delete`; each cascade-ended
  activation emits its own end audit through the existing activation-end path. Registration
  audit (`activity_type.created`) already exists.
- [x] Tenant isolation — register and delete both call `assert_project_owner`; the list read
  keeps membership gating. The `mcp-bindings` read must verify project/agent membership.
- [x] Error handling UX — list has loading/empty/error states; form surfaces 409
  (key conflict), 422 (schema/validator config) to fields, and 404 on delete of an
  already-deleted type via toast + list refetch.
- [x] Performance — type counts per project are small; a single list query, no pagination
  needed initially. Cascade-end iterates active activations for one type (bounded by rooms
  in the project); no N+1 beyond that bounded set.

## 8. Security Considerations

Touches tenant boundaries and (via `webhook`/`mcp` validator config) an egress-capable
surface, so a security lens applies:
- **AuthZ:** create and delete are owner-only (`assert_project_owner`); the new
  `mcp-bindings` read and the delete route must both re-verify project membership/ownership
  server-side — never trust the frontend's owner gate.
- **Webhook SSRF:** authoring a `webhook` validator lets an owner set an arbitrary `url`.
  R30.07 requires webhook validators egress only through the proxy; this feature only stores
  the config and must not add any direct-fetch path. Confirm the existing dispatch still
  routes through the egress proxy — no change that bypasses it.
- **mcp references:** `agent_id`/`binding_id` are validated as UUIDs at registration
  (`type_service.py:121-128`); the form should constrain the pickers to the current
  project's agents/bindings so an owner cannot bind a type to another project's agent.
- **No secrets** are entered or displayed by this UI. Audit events already exclude payload
  bodies.

Recommend a `check-security` pass at build time on the new routes and the webhook path
(gate is conditional in `/build`).

## 9. Quality Notes

**Existing debt (do not imitate, do not silently fix — see FU):**
- `type_service.soft_delete` ignores the repo `soft_delete` bool return
  (`type_service.py:91`), swallowing a lost tombstone race. Harmless for a DELETE route;
  leave as-is, do not "fix" opportunistically.
- `agentKeys.mcpBindings` query key exists with no backing API method
  (`agents/queries/index.ts:30-31`) — a stub; this task either fills it or leaves it, but
  must not add a second parallel key.

**Patterns to follow:**
- List+create+delete view: `slices/agents/views/RagConfigListView.vue` (useQuery list,
  SModal create, `useMutation` + `useConfirmDialog` delete, invalidate-on-success).
- vee-validate + Zod form: `slices/keys/components/KeyUploadForm.vue`; schemas in
  `types/schemas.ts`; server-error mapping via `useServerErrors`.
- Owner-gated header action + sibling route: the Skills button in
  `ProjectDetailView.vue:163-173` and `slices/skills/routes.ts`.
- Backend 204 delete: `agent_groups.py:201` / `projects.py:214`.

**Reuse inventory:**
- `useProjectRole` (owner gating), `useConfirmDialog` + `useToast` + `SConfirmDialog`
  (delete), `useServerErrors` (form errors), `@shared/ui` (`SFormField`, `SInput`,
  `SSelect`, `SModal`, `STable`, `SDropdown`, `SPageHeader`, `SButton`), the per-slice
  `queries/index.ts` key-factory and `installActivitiesSlice()` locale registration.
- Backend: `assert_project_owner` (already imported at `activities.py:20`),
  `_dispatch_activation_ended` (`activities.py:472`), the existing activation-end service
  path (reuse for cascade, do not reimplement ending logic).

## 10. Risks and Rollback

- **Cascade-end correctness** is the main risk: ending activations across rooms on delete is
  a broader write than register. Mitigation — reuse the existing single-activation end
  service inside a loop over `list_active_for_type`, keep it in one transaction, emit WS
  notifications only post-commit (best-effort, mirroring existing dispatch). Tests must
  cover "delete a type active in N rooms ends all N and notifies each".
- **Schema builder scope creep** — cap the field types to the fixed set (string/number/
  integer/boolean); anything richer is FU-5.
- **No migration** → no schema rollback path needed. The DELETE route is additive and
  reversible by removing it; the frontend page is behind an owner gate and route.

## 11. Acceptance Criteria

- [ ] AC-1: A project owner sees an "Activity types" entry point in the project detail
  header; a non-owner member does not. Gated on `decided && isAuthorized`.
- [ ] AC-2: The page lists the project's live activity types (name, key, validator kind);
  soft-deleted types do not appear.
- [ ] AC-3: The create form registers a `webhook` type (key, name, optional retention_days,
  builder-authored schema, `url`) and it appears in the list without a page reload.
- [ ] AC-4: The create form registers an `mcp` type with agent + binding selected from the
  current project and a `tool_name`, and it appears in the list.
- [ ] AC-5: The schema builder emits a valid JSON Schema `object`; a server
  `PayloadSchemaInvalid` (422) or `ActivityTypeKeyConflict` (409) is surfaced on the form,
  not as an unhandled error.
- [ ] AC-6: `DELETE /api/projects/{id}/activity-types/{type_id}` returns 204 for an owner,
  403 for a non-owner, 404 for an already-deleted/unknown type.
- [ ] AC-7: Deleting a type that is `active` in one or more rooms ends every such activation
  and each affected room receives `activity.activation.ended`; afterwards a new submission
  to those rooms is rejected and no room shows a dangling active activation for the type.
- [ ] AC-8: `in_process` is not offered as a selectable validator kind in the form.
- [ ] AC-9: All new user-facing strings resolve in both en and zh-TW; `pnpm lint`
  (i18n gate) passes.

## 12. Test Plan

- **Backend unit** (`backend/tests/unit/`): the cascade-delete service — deletes a type,
  asserts `deleted_at` stamped, all active activations for it ended across rooms, audit
  emitted (AC-6, AC-7). Delete of unknown/already-deleted → `ActivityTypeNotFound` (AC-6).
- **Backend integration/wiring**: DELETE route AuthZ matrix (owner 204 / member 403 /
  unknown 404) and the WS `activity.activation.ended` emit per affected room (AC-6, AC-7).
- **Frontend component** (Vitest): `ActivityTypesView` list/empty/error states and the
  owner-gate (AC-1, AC-2); `ActivityTypeForm` builds a schema, switches validator sub-forms,
  and maps a 409/422 to fields (AC-3, AC-4, AC-5, AC-8); every new view has ≥1 test (gate 8).
- **Manual via `run` / `frontend:verify`**: create webhook + mcp types, delete a type active
  in a room, confirm the room's Activity panel reflects the ended activation (AC-7).

## 13. SRS Delta

Add to `REQUIREMENTS.md` §30, verbatim on approval:

- **[R30.23]** A Project Owner may author, list, and delete `ActivityType`s from a
  project-scoped management surface. Deletion is a soft-delete (`deleted_at`) that also ends
  every `active` `ActivityActivation` referencing the type, and each affected room is
  notified (`activity.activation.ended`), so no room is left with an activation for a
  deleted type. Deletion emits an `activity_type.deleted` audit event and requires Project
  Owner capability. A soft-deleted type's `(project_id, key)` is freed for reuse
  (consistent with [R30.02]).

- **[R30.24]** The authoring surface supports the `webhook` and `mcp` validator kinds. It
  does not offer `in_process` while the platform registers no in-process validators
  ([R30.05]); a `webhook` validator's URL is stored for proxy-only egress ([R30.07]) and an
  `mcp` validator's `agent_id`/`binding_id` must reference agents/bindings within the same
  project.

## 14. Open Questions

- OQ-1: Should the guided builder support nested object/array field types eventually, or is
  a flat `object` of scalar fields the permanent shape? Flat is the v1 assumption (FU-5
  covers raw JSON as the escape hatch for anything richer).

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- FU-1: Register first-party `in_process` validators (a `backend/app/plugins/` startup site
  + a listing accessor/endpoint) and add the `in_process` branch to the form.
- FU-2: Edit an existing activity type (update route + `version` bump + edit UI).
- FU-3: Enumerate the tools an MCP binding exposes so `tool_name` becomes a picker.
- FU-4: Force-close in-flight `ActivitySession`s when their type is deleted.
- FU-5: A raw/advanced JSON-Schema editor (and nested field types) alongside the builder.
