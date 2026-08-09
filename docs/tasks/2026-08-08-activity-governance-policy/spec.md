---
type: feature
status: implemented
created: 2026-08-08
requirements: [R30.02, R30.09, R30.15, R30.20, R30.21, R30.22, R30.23, R30.25, R30.26]
depends_on: []
---

# Admin governance for structured activities: platform policy and cross-project visibility

## 1. Summary

Today a platform admin can *override* anything about structured activities and can *read*
the audit trail afterwards, but there is no gate before the fact and no admin surface at
all: none of the 10 `app/api/v1/admin*.py` routers and none of the 13 admin frontend views
mentions activities. A Project Owner decides, alone and unobserved, whether participant
text is fed to an LLM provider, whether one participant's answers are shown to the whole
room, and how long research records are retained.

This adds two things, in order. **Part B (first)** is a read-only, admin-only,
cross-project listing of activity types and currently-active activations, so an admin can
see what exists before anyone tries to govern it. **Part A (second)** is a platform-wide
policy that sets defaults and bounds for the three governance-grade fields
(`expose_payload_to_agent`, `echo_includes_content`, `retention_days`), enforced both when
a type is authored or edited and again when a facilitator activates it in a room.

Part A is the institutional answer to FU-8 of
`docs/tasks/2026-08-08-creative-thinking-course-example/spec.md`, which observed that the
first shipped example course opts participant text into agent context with nothing above
the project owner deciding that.

## 2. Goals and Non-goals

**Goals**

- An admin can list every activity type across all orgs/projects, with its project, key,
  name, validator kind, `validator_config`, and the three governance flags.
- An admin can list every currently-active activation, with the room and type it names.
- A platform-wide policy sets, per governance field, a default and whether a Project Owner
  may deviate from it (for `retention_days`, an upper bound rather than a lock).
- Authoring or editing a type that violates the policy is rejected at the API boundary.
- Activating a type that violates the policy is rejected, so tightening the policy takes
  effect on already-existing types without rewriting anybody's data.
- Changing the policy is audited.

**Non-goals**

- **No human approval gate**, on either authoring or activation. A facilitator starting an
  activity is a real-time classroom action; a manual gate would be routed around in
  practice. Explicitly rejected by the user (Q-1 of the prior discussion).
- **Not putting `org_owner` into the authorization chain.** `is_project_owner`
  (`backend/contexts/tenancy/interfaces/facade.py:48-54`) checks the `project_members`
  OWNER role and is shared by RAG upload and other surfaces; widening it is a separate
  evaluation and must not ride along here.
- **No per-org policy.** Platform-wide only (Q-1).
- No admin ability to create, edit, delete, or deactivate activities from the admin
  surface. Part B is strictly read-only.
- No change to activation / session / submission semantics beyond the new policy check.
- No retroactive rewriting of existing `ActivityType` rows (Q-2).
- Does not address FU-7 (undeclared payload keys are still persisted and still reach the
  agent digest) — that is a submission-shape question, not a governance-policy one.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How fine-grained should the policy be? | Platform-wide, one row. | Simplest model, matches the `platform` scope row of the `prompt_studio` precedent. Cost accepted: different institutions with different IRB rules cannot diverge. Revisit only if a second institution actually needs it — a per-org layer is additive (see §5). |
| Q-2 | What happens to existing types when the policy tightens? | Enforce on create/edit **and** again at activation; never rewrite stored rows. | A create/edit-only gate would let a pre-existing type with `expose_payload_to_agent: true` run forever, which defeats the purpose. Retroactive rewriting silently mutates project data and collides with [R30.23], which forbids behavioral edits while an activation is live. Blocking at activation makes a tightened policy bite without touching anyone's data. Accepted cost: a facilitator can discover at class time that an activity will not start, so the error must name the offending field and say who can fix it. |
| Q-3 | Should the admin cross-project list show `validator_config`? | Yes. | An admin already reads it through the project API by bypass ([R30.25] redacts for non-owners only), so withholding it adds no security and costs a screen switch during triage. |
| Q-4 | Which precedent should the policy storage follow? | `prompt_studio`'s `ConfigService`, not `admin_rate_limits`. | `admin_rate_limits.py:57-65,101-105` runs raw `sa.text()` SQL directly in the route handler, bypassing the facade rule in `backend/CLAUDE.md`, has no ORM table, and is essentially untested. `prompt_studio` has a domain model, table module, repository, application service, optimistic concurrency, scope resolution, and unit tests. See §5 and §9. |
| Q-5 | Does this depend on any unfinished dossier? | No — `depends_on: []`. | The only non-implemented dossiers are `2026-07-07-graphrag-two-axis-redesign` (approved, GraphRAG) and `2026-07-19-large-artifacts-silently-dropped` (in-progress, artifacts). Neither touches `contexts/activities`, the admin routers, or the admin slice. `2026-08-08-creative-thinking-course-example` is `implemented`. |

## 4. Current State

### 4.1 Who can do what today

| Action | Gate | Citation |
|---|---|---|
| Create / edit / delete an activity type | strict Project Owner, admin bypasses | `backend/app/api/v1/activities.py:304,364,393` → `assert_project_owner` (`backend/app/api/v1/deps.py:82-98`), bypass at `:77-78` |
| List types in a project | project membership; `validator_config` redacted for non-owners | `activities.py:418-419`, [R30.25] |
| Activate / end in a room | room creator, admin bypasses | `activities.py:474-475,501-502`, [R30.21] |
| Read the audit trail | platform admin only | `backend/app/api/v1/admin_audit.py` → `require_admin` (`backend/app/api/v1/admin_deps.py:15-20`) |

`org_owner` satisfies none of these: `is_project_owner` resolves the `project_members`
OWNER role only (`backend/contexts/tenancy/interfaces/facade.py:48-54`). Roles are
enumerated at `backend/shared_kernel/auth/permissions.py:34-40`.

Eight activity audit actions already exist: `activity_type.created` / `.updated` /
`.deleted`, `activity.activation_started` / `.activation_ended`, `activity.submitted`,
`activity.validated`, `activity.session_closed`.

### 4.2 There is no admin surface for activities

No file under `app/api/v1/admin*.py` and no view under `frontend/src/slices/admin/views/`
references activities. The admin router aggregate is `backend/app/api/v1/admin.py:15-33`
(imports at `:15-23`, `include_router` at `:27-33`, prefix `/api/admin` at `:25`); a new
`admin_activities.py` must be added at both points.

### 4.3 Nothing in the activities context can read across projects

Every repository query is scoped. `ActivityTypeRepository`
(`backend/contexts/activities/infrastructure/repositories/type_repo.py`) offers
`create` `:62`, `get` `:107`, `list_for_project` `:121` (the only list, hard-filtered on
`project_id`), `update` `:137`, `soft_delete` `:177`. `ActivationRepository`
(`.../activation_repo.py`) offers `get` `:44`, `get_active` `:52`,
`get_active_for_update` `:55`, `create_active` `:73`, `list_active_for_type` `:90` (one
type id), `end` `:106`. **There is no unscoped list on either.** The facade
(`backend/contexts/activities/interfaces/facade.py`) likewise exposes nothing cross-project
— `list_types(project_id)` `:138-139` is the closest.

So Part B needs new repository methods, new port entries
(`backend/contexts/activities/application/ports.py:13-33`), and new facade methods.

### 4.4 How other admin listings do cross-tenant reads

`admin_projects.list_projects` (`backend/app/api/v1/admin_projects.py:54-87`) and
`admin_orgs.list_orgs` (`backend/app/api/v1/admin_orgs.py:43-75`) query their tables with
raw `sa.text()` in the route handler and paginate with a hand-rolled keyset cursor
(`cursor: uuid.UUID | None` + `limit: int = Query(50, ge=1, le=200)`,
`admin_projects.py:56-57`). `admin_users.list_users` (`backend/app/api/v1/admin_users.py:76-97`)
instead delegates to an application service (`:85-86`). Two pagination idioms coexist:
the shared `PaginationParams` (limit/offset, `backend/app/api/v1/deps.py:25-30`) is used by
`admin_rate_limits.py:51` and `skills.py:1431`, while the big listings use cursors.

**No admin listing currently hydrates names across contexts** — `admin_projects` returns
raw owner UUIDs and the frontend prints them (`frontend/src/slices/admin/views/AdminProjectsView.vue:22-28`).
The sanctioned batch-resolve pattern lives in non-admin routes; the worked example is
`backend/app/api/v1/keys.py:160-182` ("Batch both per-project lookups into one query each
(no N+1)" at `:161-163`).

Available batch helpers: `TenancyFacade.get_projects(ids) -> dict` `:66-68` and
`member_project_ids` `:70-74`; `IdentityFacade.get_display_names(ids)` `:63-70`.
**Missing**: there is no `TenancyFacade.get_orgs(ids)` and no
`ConversationFacade.get_chatrooms(ids)` batch helper — `get_chatroom` `:78-84` is
single-row.

### 4.5 The two policy precedents

`rate_limit_policies` — migration `backend/alembic/versions/0004_audit.py:114-126`, keyed
rows, **no SQLAlchemy table module anywhere**, all access via raw `sa.text()`, Redis
write-through mirror with a 24 h TTL (`backend/shared_kernel/auth/ratelimit.py:100-110,149-166`),
audit action `admin.rate_limit_patched` (`admin_rate_limits.py:113`), boot-seeded by
`prime_policies` (`ratelimit.py:113-146`) from `INSERT ... ON CONFLICT DO NOTHING`.
Only test coverage is `backend/tests/integration/test_ratelimit_bucket_mapping.py`; **no
test exercises the admin routes at all**.

`prompt_assistant_configs` — migration `backend/alembic/versions/0042_prompt_studio.py:49-88`,
three scopes (`platform`/`org`/`user`) in one table with partial unique indexes enforcing a
singleton per scope holder (platform singleton at `:77-80`), proper table module
(`backend/contexts/prompt_studio/infrastructure/tables.py:36`), repository, application
service `ConfigService` (`backend/contexts/prompt_studio/application/config_service.py:32`),
optimistic concurrency via a `version` column + `If-Match` (`config_service.py:89-92`),
resolution chain user→org→platform (`config_service.py:117+`), audit actions
`prompt_studio.config_created` / `.config_updated` (`config_service.py:87,92`), no caching
(read per request), unit-tested in `backend/tests/unit/test_prompt_studio_services.py`.
Admin routes at `backend/app/api/v1/prompt_studio.py:637-675`.

There is **no generic settings mechanism**: a grep for `feature_flag|system_settings|
platform_settings|app_settings|global_settings|site_settings` across the backend returns
zero matches.

### 4.6 Frontend admin slice wiring

Query keys only in `frontend/src/slices/admin/queries/index.ts:3-22`; views call `useQuery`
directly with `adminApi.*` (`AdminProjectsView.vue:70-73`, `AdminRateLimitsView.vue:112-115`).
API wrappers over the generated client in `frontend/src/slices/admin/api/admin.ts:53-138`.
Routes as children of the `AdminLayout` parent in `frontend/src/slices/admin/routes.ts:5-10`
(`meta.requiredRoles: ['admin']` at `:9`). The nav list is the `navItems` array in
`frontend/src/slices/admin/components/AdminNav.vue:27-40`. Tables use `STable` with the
`type XRow = X & Record<string, unknown>` cast idiom (`AdminProjectsView.vue:75-80`).
i18n under a top-level `admin.` namespace, `frontend/src/slices/admin/locales/{en,zh-TW}.json`,
registered at `frontend/src/slices/admin/index.ts:8-13`.

`SPagination` exists (`frontend/src/shared/ui/index.ts:25`) but **no admin view uses it**;
the only paginated admin view is `AdminAuditView`, using `useInfiniteQuery` + a Load More
button (`AdminAuditView.vue:196-208`).

**Deep-linking into the audit view with a pre-filled filter does not exist.**
`AdminAuditView.vue` never imports `useRoute`; filters are local state seeded empty
(`:160-169`). Making "click a type → see its audit trail" work requires modifying that view
to hydrate from `route.query`.

## 5. Design

### Options considered

**Storage — Option A: copy `rate_limit_policies`.** Keyed rows, raw SQL in the route, Redis
mirror. Fast reads. But it violates `backend/CLAUDE.md`'s rule that route handlers call
facades, has no ORM table, and its cache has a documented 24 h expiry hole
(`ratelimit.py:110`) where a long-running process silently reverts to compile-time
defaults.

**Storage — Option B: follow `prompt_studio`'s `ConfigService`.** Domain model + table
module + repository + application service + facade, singleton row enforced by a partial
unique index, optimistic concurrency, audit in the service. Reads hit Postgres per request.

**Enforcement point — Option C: create/edit only.** Cheapest; leaves non-compliant existing
types running indefinitely.

**Enforcement point — Option D: create/edit + activation.** A tightened policy bites
without mutating stored rows.

**Enforcement point — Option E: retroactive clamp on policy save.** Most thorough, but
silently mutates project-owned data and collides with [R30.23]'s ban on behavioral edits
while an activation is live.

### Decision

**Option B** for storage. This policy is read at type-authoring time and at activation
time — both already do database work in the same transaction — not on a per-request hot
path, so the caching complexity that justifies the rate-limit shape buys nothing here.
Following `prompt_studio` also means the new code obeys the project's own layering rules
instead of copying a router that breaks them. Concretely: a single `platform`-scoped row,
so the table carries a `scope` column with a partial unique index even though only
`platform` is used in v1 — that is what makes Q-1's rejected per-org layer additive later
rather than a migration rewrite.

**Option D** for enforcement (Q-2). The activation check is what makes the policy real for
the installed base; the create/edit check is what makes violations rare enough that the
activation check seldom fires. The error raised at activation must name the offending
field and state that a Project Owner must edit the type — a facilitator hitting this at
class time can otherwise only see "forbidden".

**Policy shape.** For each of the two booleans: a `default` plus a `locked` flag (when
locked, a type must match the default; when unlocked, the default only pre-fills the
authoring form). For `retention_days`: a `default` plus `max_days` (an upper bound, since a
lock on a numeric would be meaninglessly rigid) — `null` for `max_days` means unbounded.
Rejected alternative: a general allowed-value-set model per field, which is more expressive
than three fields justify and would need its own validation grammar.

**Part B read path.** New unscoped, cursor-paginated repository methods on both activity
repositories, surfaced through new facade methods, called from a new
`app/api/v1/admin_activities.py`. Hydration of project and chatroom names follows the
`keys.py:160-182` batch pattern, **not** a SQL join — [R30.09] forbids cross-context joins
and the activities context must not read another context's tables. This needs one new
batch helper, `ConversationFacade.get_chatrooms(ids) -> dict`, since only a single-row
`get_chatroom` exists (§4.4). Org names are deliberately **not** shown: that would need a
second new batch helper (`TenancyFacade.get_orgs`) for a column the "which classroom is
running what" question does not need; project name is enough.

**Pagination**: cursor, matching `admin_projects`/`admin_orgs`/`admin_users`, because both
listings grow with platform usage. Not `PaginationParams` — deep offsets over a
cross-tenant table are the case keyset pagination exists for. This is a deliberate pick
between the two competing idioms in §4.4, not an accident.

## 6. Detailed Changes

Two independently shippable parts. **B ships first**: it is read-only, has no enforcement
risk, and gives the admin something to look at before the policy can strand anyone.

### Part B — cross-project visibility (read-only)

- **Backend**
  - `contexts/activities/infrastructure/repositories/type_repo.py`: add
    `list_all(*, cursor, limit) -> Sequence[ActivityType]`, unscoped, ordered
    `created_at DESC, id DESC`, excluding soft-deleted.
  - `.../activation_repo.py`: add `list_all_active(*, cursor, limit) -> Sequence[ActivityActivation]`.
  - `contexts/activities/application/ports.py`: extend the two port protocols (`:13-33`).
  - `contexts/activities/interfaces/facade.py`: add `list_all_types` and
    `list_all_active_activations`.
  - `contexts/conversation/interfaces/facade.py`: add `get_chatrooms(ids) -> dict[UUID, Chatroom]`
    batch helper (+ repository method), mirroring `TenancyFacade.get_projects` `:66-68`.
  - New `app/api/v1/admin_activities.py`: `GET /api/admin/activity-types` and
    `GET /api/admin/activity-activations`, both `Depends(require_admin)`, cursor+limit,
    hydrating project name (and chatroom name for activations) via batch facades.
    Register in `app/api/v1/admin.py` at both the import block and `include_router`.
- **API contract** — two new endpoints. `gen:api` rerun required: **yes**.
- **Frontend**
  - `slices/admin/api/admin.ts`: wrappers for both endpoints.
  - `slices/admin/queries/index.ts`: two new query keys.
  - `slices/admin/types/index.ts`: row types.
  - New `slices/admin/views/AdminActivitiesView.vue` — two `STable`s (types, active
    activations) with loading / error / empty states, following `AdminProjectsView.vue`.
  - `slices/admin/routes.ts`: a child route; `components/AdminNav.vue:27-40`: a nav item.
  - `slices/admin/locales/{en,zh-TW}.json`: an `admin.activities` block plus the nav label.
  - `AdminAuditView.vue`: hydrate `filters` from `route.query` on mount so the activities
    view can link to a pre-filtered audit trail (this behavior does not exist today, §4.6).

### Part A — platform policy

- **Backend**
  - New Alembic migration creating `activity_policies`: `id`, `scope` (`platform`),
    `expose_payload_to_agent_default` bool, `expose_payload_to_agent_locked` bool,
    `echo_includes_content_default` bool, `echo_includes_content_locked` bool,
    `retention_days_default` int null, `retention_days_max` int null, `version` int,
    `updated_at`, `updated_by_user_id`. Partial unique index on `scope` where
    `scope = 'platform'`, mirroring `0042_prompt_studio.py:77-80`.
  - `contexts/activities/domain/models.py`: `ActivityPolicy` frozen dataclass.
  - `contexts/activities/infrastructure/tables.py` + a `policy_repo.py`.
  - `contexts/activities/application/policy_service.py`: `get_effective()` (returns
    shipped defaults when no row exists) and `update(...)` with optimistic concurrency on
    `version`, emitting `activity_policy.updated`.
  - `contexts/activities/application/type_service.py`: call the policy check inside
    `register` (after `:51-52`) and inside `update`'s behavioral branch (`:111-120`).
  - `contexts/activities/application/activation_service.py`: check the policy against the
    resolved type before creating the activation; raise a new domain error
    `ActivityTypeViolatesPolicy` carrying the offending field name.
  - `contexts/activities/interfaces/facade.py` + `interfaces/error_mapping.py`: expose the
    policy read/update and map the new error to 409 with a problem+json code.
  - `app/api/v1/admin_activities.py`: `GET`/`PUT /api/admin/activity-policy`,
    `require_admin`, `If-Match` on the version.
- **API contract** — two more endpoints; the activation endpoint gains a new 409 code.
  `gen:api` rerun required: **yes**.
- **Frontend**
  - `slices/admin/views/AdminActivitiesView.vue`: a policy form section (admin-editable).
  - `slices/activities/components/ActivityTypeForm.vue`: pre-fill the three fields from the
    policy and disable a locked one with an explanatory hint; clamp the `retention_days`
    input to `retention_days_max`. The server remains authoritative.
  - New i18n keys in both admin and activities locale files, `en` + `zh-TW`.
- **Deploy/config** — none.

## 7. NFR Checklist

- [x] **i18n** — every new string via `$t()` in both locale files; the activation-refusal
  message must be a translated key, not a raw backend string, since a facilitator sees it.
- [x] **Audit log** — `activity_policy.updated` on every policy change, with before/after
  values in metadata. Part B is read-only and emits nothing.
- [x] **Tenant isolation** — every new endpoint is `require_admin`. Part B's listings are
  deliberately cross-tenant, which is the feature; no non-admin surface gains cross-tenant
  reach. Existing project- and room-scoped endpoints are unchanged.
- [x] **Error handling UX** — the activation refusal names the field and the role that can
  fix it; the admin views carry loading / error / empty states; the policy form surfaces
  409 on a stale `If-Match`.
- [x] **Performance** — both listings are cursor-paginated and hydrate names by batch
  facade call (two queries per page, not N+1). The policy is a single-row read per
  authoring/activation call, not per request; no cache, hence no invalidation bug.

## 8. Security Considerations

- **Cross-tenant read is the feature, so the gate is the only control.** Both Part B
  endpoints return data from every org. They must use `require_admin`
  (`admin_deps.py:15-20`) and must not accept any caller-supplied project/org filter that
  could be reached by a non-admin route. Note `admin_ip_bans.py:40-45` re-implements its
  own `_require_admin` — do not copy that; import the shared one.
- **`validator_config` exposure widens by design** (Q-3). It may hold answer keys
  (`exact_match`'s `expected`). This is admin-only and admins already have it via bypass,
  but the field must never leak into Part B's *frontend* types in a way that a future
  non-admin view could reuse — keep the row type inside the admin slice.
- **Policy is a privilege boundary.** `PUT /api/admin/activity-policy` can force
  `expose_payload_to_agent` on platform-wide, which would push every project's participant
  text into agent prompts. Locking it *on* is a legitimate but dangerous setting; the audit
  entry must record the previous value, and the admin UI should confirm before enabling a
  lock that widens exposure.
- **The activation check must be server-side and unconditional.** It is a gate on
  [R30.22]'s "submission only while active" chain; a client-side-only check would be
  bypassable by calling the activation endpoint directly.
- No auth, provider-key, WebSocket, or upload surface is touched. No new user-input
  processing beyond admin-supplied integers/booleans, all Pydantic-validated with bounds.

## 9. Quality Notes

**Existing debt in touched files** (do not imitate, do not silently fix)

- `app/api/v1/admin_rate_limits.py:57-65,101-105` runs raw `sa.text()` in the route,
  bypassing the facade rule. `admin_projects.py:61-75` and `admin_orgs.py:50-64` do the
  same. The new `admin_activities.py` must **not** follow them — go through the activities
  facade.
- `admin_ip_bans.py:40-45` duplicates `require_admin` locally. Import the shared one.
- The admin surface has two competing pagination idioms (§4.4). This task picks cursor and
  says so; it does not unify the existing ones.
- `AdminAuditView.vue` has no route-query hydration (§4.6). Part B adds it for the audit
  deep-link; that is in scope because Part B's own requirement needs it, not opportunistic.
- No test anywhere covers the admin rate-limit routes. Do not treat that as the standard —
  every new endpoint here needs tests.

**Patterns to follow**

- Policy persistence and service shape: `contexts/prompt_studio/application/config_service.py:32`
  (optimistic concurrency `:89-92`, audit `:87,92`) and its table
  `contexts/prompt_studio/infrastructure/tables.py:36`, migration
  `alembic/versions/0042_prompt_studio.py:49-88`.
- Cross-context hydration without joins: `app/api/v1/keys.py:160-182`.
- Admin route shape: `admin_users.py:76-97` (delegates to a service — the good one).
- Admin view shape: `AdminProjectsView.vue`; nav registration `AdminNav.vue:27-40`.
- SoC: `contexts/activities` stays free of *pedagogy* domain; a platform governance policy
  over its own aggregate's fields is a platform concept and belongs in the context, exactly
  as `prompt_studio` owns its config.

**Reuse inventory**

- `require_admin` — `app/api/v1/admin_deps.py:15-20`.
- `TenancyFacade.get_projects(ids)` — `contexts/tenancy/interfaces/facade.py:66-68`.
- `IdentityFacade.get_display_names(ids)` — `contexts/identity/interfaces/facade.py:63-70`
  (never `get_chat_labels` — `:77-79` forbids it in user-facing responses).
- `shared_kernel.audit.emit` / `AuditEvent` — `shared_kernel/audit.py:105,116`.
- `STable`, `SPageHeader`, `SQueryError`, `SEmptyState`, `SFormField`, `SInput`,
  `SCheckbox`, `SButton` from `@shared/ui`; `formatDate`/`formatDateTime` from
  `@shared/utils/datetime`.
- `useServerErrors` / `useToast` from `@shared/composables`, as `ActivityTypeForm.vue:9`
  already does.

## 10. Risks and Rollback

- **Migration** adds one table; reversible by dropping it. No column is added to
  `activity_types`, so a downgrade cannot lose project data.
- **Biggest behavioral risk: the activation check strands a class.** Mitigations: ship Part
  B first so an admin can see what would break before setting a policy; the shipped default
  policy must be permissive (everything unlocked, `retention_days_max` null) so installing
  the feature changes nothing until an admin deliberately tightens it; and the error must
  be actionable. A tightening admin should be shown how many existing types violate the new
  policy before saving — worth building, and cheap once Part B's listing exists.
- **`gen:api` drift** — four new endpoints; `pnpm run check:openapi-drift` must pass.
- Rollback is `git revert` per part; Part A reverts independently of Part B.

## 11. Acceptance Criteria

**Part B**

- [x] AC-1: `GET /api/admin/activity-types` returns types from more than one project in a
  single response, is rejected with 403 for a non-admin, and paginates by cursor.
- [x] AC-2: Each row carries project id **and** project name, resolved by batch lookup — a
  page of N rows issues a bounded number of queries, not N+1.
- [x] AC-3: `GET /api/admin/activity-activations` lists only `active` activations across
  projects, each with its chatroom name and activity type name.
- [x] AC-4: No cross-context SQL join is introduced ([R30.09]) — verified by reading the
  new repository methods; every cross-context field comes from a facade batch call.
- [x] AC-5: `AdminActivitiesView` renders both tables with loading, error, and empty
  states, is reachable from the admin nav, and is admin-gated by the route meta.
- [x] AC-6: A row links to `AdminAuditView` pre-filtered to that resource, and the audit
  view hydrates its filters from `route.query`.

**Part A**

- [x] AC-7: With no policy row present, `get_effective()` returns permissive shipped
  defaults and no existing behavior changes.
- [x] AC-8: With `expose_payload_to_agent` locked to `false`, registering or editing a type
  with it `true` is rejected at the API boundary with a problem+json code naming the field.
- [x] AC-9: With the same policy, activating an **already-existing** type that has it
  `true` is rejected, and the stored row is not modified.
- [x] AC-10: With `retention_days_max = 365`, a type declaring `730` is rejected; one
  declaring `365` or `null` is accepted.
- [x] AC-11: An unlocked field is only a default: a Project Owner may still deviate, and
  the authoring form pre-fills from the policy.
- [x] AC-12: Updating the policy emits `activity_policy.updated` with previous and new
  values; a stale `If-Match` version is rejected with 409.
- [x] AC-13: `PUT /api/admin/activity-policy` is 403 for a non-admin.
- [x] AC-14: The activation refusal reaches the facilitator as a translated message naming
  the field and stating a Project Owner must edit the type.
- [x] AC-15: Gates green — backend `ruff check`, `ruff format --check`, `mypy` (916 files),
  and the targeted suites (406 tests); frontend `pnpm test` (175 files / 1001 tests),
  `pnpm lint`, `pnpm run typecheck`, `pnpm build`, `check:bundle-size`. **Migrations 0074
  and 0075 are not applied or downgrade-tested** — no Postgres on this host; the revision
  chain is verified offline (0075 is the single head). CI or a live stack owns that.

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-1, AC-3, AC-13 | unit | New `backend/tests/unit/test_admin_activities_routes.py`, calling the route functions directly with a `SimpleNamespace` principal, as `test_activity_type_edit.py:329-355` does. |
| AC-2, AC-4 | unit | Assert the facade batch helpers are awaited once regardless of row count (mock facades, count awaits); read-through review for the join ban. |
| AC-5, AC-6 | component | New `frontend/src/slices/admin/__tests__/AdminActivitiesView.test.ts` (gate #8), plus an `AdminAuditView.test.ts` case for route-query hydration. |
| AC-7, AC-10, AC-11 | unit | New `backend/tests/unit/test_activity_policy_service.py` — effective-policy resolution with and without a row, bounds. |
| AC-8 | unit | Extend `backend/tests/unit/test_activities_services.py` register/update cases. |
| AC-9 | unit | New activation-service case: existing non-compliant type + tightened policy → refusal, and `type_repo.update` not awaited. |
| AC-12 | unit | Policy-service test asserting the audit action and the `version` conflict path. |
| AC-14 | component | `ActivityPanel` test asserting the translated key renders on the refusal code. |
| AC-15 | CI | Commands in the root `CLAUDE.md` table. |

## 13. SRS Delta

Add **[R30.29]**:

> - **[R30.29]** A platform-wide activity governance policy constrains the three privacy- and retention-grade fields of an `ActivityType` (`expose_payload_to_agent`, `echo_includes_content`, `retention_days`). For each boolean the policy carries a default and a lock; for retention it carries a default and an upper bound. The policy is read and written only by platform admins, is versioned with optimistic concurrency, and every change emits an `activity_policy.updated` audit event recording previous and new values. When no policy row exists the platform behaves permissively, so installing the capability changes no existing behavior.

Add **[R30.30]**:

> - **[R30.30]** The governance policy is enforced server-side at two points: when an `ActivityType` is registered or its behavioral definition edited ([R30.02], [R30.23]), and again when a facilitator activates a type in a room ([R30.21]). The activation check is what makes a tightened policy apply to types that already exist; the platform never retroactively rewrites a stored `ActivityType` to match a policy change. A refusal identifies the offending field so the facilitator knows a Project Owner must edit the type.

Add **[R30.31]**:

> - **[R30.31]** Platform admins have a read-only, cross-project view of every registered `ActivityType` and every currently-active `ActivityActivation`, including each type's governance-field settings. Cross-context attributes (project and chatroom names) are resolved through batch facade reads, never SQL joins, preserving [R30.09]. The view is admin-only; it grants no create, edit, or deactivate capability.

## 14. Open Questions

- Whether a tightening admin should be *blocked* from saving a policy that strands existing
  types, or merely warned with a count. §10 proposes warn-with-count; blocking would make
  the policy unusable on a platform with legacy data. Does not block approval.
- Whether `org_owner` should eventually read (not write) this admin view for their own org.
  Deliberately out of scope here (Non-goal), recorded so it is not lost.

## 15. Deviation Log

### Part A

- **D-7** — §6 did not mention it, but Part A adds a **fourth endpoint**,
  `GET /api/activity-policy`, authenticated rather than admin-only. The authoring form is
  used by Project Owners and needs the policy to pre-fill defaults and disable a locked
  switch (AC-11), which the admin-only endpoint cannot serve. Scoped like the sibling
  `GET /api/activity-validators`: the policy is platform configuration, not a secret, and
  an owner would learn the same facts from a 409 on their first save. Note it therefore
  also reaches guest-link participants — flagged by the security gate as Hardening, not a
  leak (six configuration values, no tenant data).
- **D-8** — A fifth endpoint, `POST /api/admin/activity-policy/impact`, was added. §10
  proposed warning a tightening admin with a count of stranded types; that needs a
  server-side count, and there was no way to obtain one. Read-only, bounded to 500 types in
  a single query, and reports `approximate` when the bound is hit rather than silently
  under-counting.
- **D-9** — §6 said the enforcement point in `update` would sit in the behavioral branch
  alongside the other re-validation. It sits **outside** it. The three governance fields
  are safe metadata under [R30.23], so an edit that touches only them never sets
  `behavioral_changed` — the specced placement would have left the policy bypassable by
  editing nothing else. Pinned by `test_edit_of_only_the_governance_field_is_still_gated`.
- **D-10** — Two defects the security gate found in this work, both fixed here rather than
  deferred. (a) A policy whose `retention_days_default` exceeded its own
  `retention_days_max` was accepted, because Pydantic and the table CHECKs validate the
  fields independently; the form would then pre-fill an illegal value. Now a 422 via a new
  `ActivityPolicyInconsistent`. (b) Two admins saving the *first* policy concurrently both
  take the create path (no version exists yet) and the loser hit the partial unique index
  as an unhandled `IntegrityError` → 500; it is the same conflict the update path already
  reports, so it now returns the same 409.
- **D-11** — AC-14 needed a mechanism §6 did not anticipate. The refusal's field reached
  the client only inside the problem `detail`, as untranslated English prose
  (`context_handler.py:70` sets `detail = str(exc)`), which cannot satisfy "a translated
  message naming the field". It now travels as a structured problem member through the
  context handler's existing `extras` hook (the `identity` context's `_extras` is the
  precedent), so `ActivityPanel` renders a translated message with the field interpolated.

### Part B

- **D-1** — §6 said Part B would extend the two port protocols in
  `contexts/activities/application/ports.py:13-33`. It does **not**. Those protocols exist
  so *services* can receive an injected repository; the new reads are called by the facade
  on its concrete repositories, so adding the methods to the protocols would only force
  every test double to grow a method nothing consumes. Ports stay untouched.
- **D-2** — §6 listed `list_all_types` and `list_all_active_activations`. A third facade
  method, `get_types_by_ids` (with `ActivityTypeRepository.get_many`), was needed: the
  activations listing must name its type, and doing that without a join or an N+1 requires
  a batch read inside the activities context.
- **D-3** — The keyset predicate is written as decomposed `OR`/`AND` rather than a
  row-value tuple comparison. The tuple form failed mypy against `sa.tuple_` and, more
  importantly, is exactly the class of construct `backend/CLAUDE.md` warns cannot be
  verified by the unit tier (it compiles with `literal_binds`, so a parameter-type error
  would surface only against a real database). The decomposed form is what
  `admin_projects.list_projects:63-70` already runs in production.
- **D-4** — The view requests the server maximum page (200) and renders an explicit notice
  when the page comes back full, instead of the single default page §6 implied. Found in
  self-audit: a governance view that showed the newest 50 of 300 with no indication would
  actively mislead. True paging is FU-6.
- **D-5** — Two audit-view tests were added beyond §12's plan, covering that a crafted link
  cannot inject a non-filter param or drive `limit`/`cursor`. The route-query hydration is
  new attack surface, so it needed a negative test, not only a positive one.
- **D-6** — §10 said Part B involves no migration. It now ships one:
  `0074_activity_admin_listing_indexes`. Raised by code review — both `list_all` queries
  order by `(created_at DESC, id DESC)` with no index behind them, so the keyset-over-offset
  justification in their own docstrings did not hold, and the view's 200-row page meant a
  full scan and sort of every activity type on the platform per mount. Two partial indexes,
  built `CONCURRENTLY` following `0071_retention_sweep_indexes`.
  **The migration has not been applied or downgrade-tested** — there is no Postgres on this
  development host. The revision chain is verified offline (0074 is the single head, chained
  to 0073). Applying it and exercising the downgrade is CI's, or a live stack's, job.

## 16. Follow-ups

- **FU-1** — Absorbs FU-8 of `2026-08-08-creative-thinking-course-example`: that dossier
  noted participant text reaching observer-agent context with only a Project Owner
  deciding. Part A is the institutional control; this entry records the linkage so the
  original FU can be closed when Part A ships.
- **FU-2** — `TenancyFacade.get_orgs(ids)` batch helper is still absent; org names are not
  shown in the admin view (§5). Add it if an org column is ever wanted.
- **FU-3** — The two admin pagination idioms (§4.4) remain unreconciled platform-wide.
- **FU-4** — FU-7 of the course-example dossier (undeclared payload keys persisted and fed
  to the agent digest) is untouched here; it is a submission-shape fix, not governance.
- **FU-5** — **Neither admin read is audited.** An admin can page through every tenant's
  activity configuration, including `validator_config` answer keys, leaving no record of
  who read what. Not a vulnerability (the admin is authorized and gains nothing they lack),
  but inconsistent with a platform that audits a rate-limit write and impersonation. For a
  governance feature, "who inspected which tenant" is arguably part of the record. Raised
  by the security gate as Hardening; needs a product decision, not just an implementation.
- **FU-6** — `AdminActivitiesView` fetches one page of 200 and says so when full (D-4).
  Real paging (Load More via `useInfiniteQuery`, as `AdminAuditView.vue:196-208` does)
  is the proper fix.
- **FU-7** — `ActivityTypeRepository.list_all` and `ActivationRepository.list_all_active`
  are the only unscoped queries in a context where everything else is project- or
  room-scoped, and they sit next to `list_for_project`. Containment today is a docstring
  plus review (verified: the sole production callers are the two admin handlers). Consider
  renaming them `*_unscoped`, or an import-linter contract restricting them to
  `app.api.v1.admin_activities`, so a future call site reads as a decision.
- **FU-9** — An unresolvable `cursor` (a UUID naming no row) makes the correlated subquery
  return NULL, so the page comes back empty — indistinguishable from "end of list" rather
  than an error. Safe (no unbounded scan, `LIMIT` always applies) and it matches
  `admin_projects.list_projects`, the cited precedent, so fixing it here alone would make
  the admin surface inconsistent. A 422 on an unresolvable cursor is the right answer for
  both, together.
- **FU-10** — Migrations `0074` and `0075` are **not applied or downgrade-tested**: there is
  no Postgres on this development host. The revision chain is verified offline (`0075` is
  the single head, chained through `0074` to `0073`). `alembic upgrade head` and the
  downgrade path belong to CI or a live stack before release.
- **FU-11** — `GET /api/activity-policy` is readable by any authenticated principal,
  including guest-link participants. Harmless today (six configuration values), but if the
  policy ever gains a field that is not safe to publish, narrow it then.
- **FU-12** — The policy gates activation, not submission, so an activity already running
  when an admin tightens keeps accepting submissions until the facilitator ends it. That is
  the designed semantics ([R30.30]) and avoids killing a class mid-session; worth saying in
  the admin UI copy if the expectation ever matters.
- **FU-8** — Minor quality items recorded and not fixed: the two governance-flag cells in
  `AdminActivitiesView.vue` duplicate a six-line `SBadge` block; the audit-link column uses
  an empty `label`, leaving that `<th>` without an accessible name; and
  `test_activity_repos.py`'s `_run` helper carries a `# type: ignore[operator]` that a
  `Callable` annotation would remove.
