# Gap report: Activity-type authoring has no UI

Date: 2026-07-23
Scope: `contexts/activities` (backend) and `slices/activities` (frontend)
Status: analysis only. No code changed.

## Question

In a workspace chatroom (執行環境) the right rail shows an "活動" (Activity) tab, but
there is no visible place to *configure* an activity. Is this by design, or a missing
feature?

## Answer in one line

It is a genuine missing frontend feature, not an "activities are auto-generated" design.
The backend to author activity types is fully built and even wrapped in the generated
API client; the hand-written frontend slice deliberately stops at *consuming* existing
types. A project owner today can only create an activity type out-of-band (direct API
call, seed, or admin tooling).

## Model recap (four nouns)

`backend/contexts/activities/domain/models.py`

| Noun | What | Created by | Scope |
|------|------|-----------|-------|
| `ActivityType` | Template: `key`, `name`, `payload_schema` (JSON Schema), `validator_kind`, `validator_config`, `retention_days` | Project **owner** | Project |
| `ActivityActivation` | Turns one type "on" in a room (partial-unique: <=1 active per room) | Room creator (facilitator) | Chatroom |
| `ActivitySession` | One subject-user's run (OPEN/CLOSED) | Participant | Chatroom |
| `ActivitySubmission` | One graded submission (payload + verdict + sub_scores) | Participant | Chatroom |

A chatroom does not own an activity by column; the link is the `ActivityActivation` row.
An activity is not spawned by running an agent group. It is a pre-registered project
template that a facilitator explicitly activates.

## What exists vs what is missing

### Backend: complete

`backend/app/api/v1/activities.py`

- `POST /api/projects/{project_id}/activity-types` -> `register_activity_type`, owner-only
  (`assert_project_owner`). Request model `ActivityTypeIn` (key/name/payload_schema/
  validator_kind/validator_config/retention_days). Validates JSON Schema well-formedness
  and validator config per kind (`type_service._validate_validator_config`), emits
  `activity_type.created` audit.
- `GET /api/projects/{project_id}/activity-types` -> `list_activity_types`, membership.
- Facade has `soft_delete_type` (emits `activity_type.deleted` audit) but **no HTTP route
  exposes it** — types can be created but not deleted via the API.
- No update/PATCH route for a type (edit = out of scope; types are immutable once created).

Validator kinds and their required config (`type_service.py:105`):
- `in_process`: `validator_id` must be a registered validator.
- `webhook`: requires `url`.
- `mcp`: requires `tool_name`, plus `agent_id` and `binding_id` (both must be valid UUIDs).

### Generated client: present but unused

`frontend/src/shared/api-client/services/ActivitiesService.ts` already contains
`registerActivityTypeApiProjectsProjectIdActivityTypesPost` with the `ActivityTypeIn`
model. Nothing under `frontend/src` (outside the generated client) calls it.

### Hand-written frontend slice: consumer only

`frontend/src/slices/activities/api/index.ts` wraps: `listActivityTypes`,
`startActivation`, `endActivation`, `getActiveActivation`, `openActivitySession`,
`closeActivitySession`, `submitActivity`, `listActivitySubmissions`. There is **no
`registerActivityType` wrapper and no `softDeleteType` wrapper**.

`components/ActivityPanel.vue` (the Activity tab body):
- Facilitator: `SSelect` over existing types (`listActivityTypes`) + Start / End.
- Participant: Join / Finish / submit via `ActivityHost`.
- The dropdown can only pick types that already exist. There is no form to author
  `payload_schema` / `validator_kind` / `validator_config`.

Locale surface (`slices/activities/locales/{en,zh-TW}.json`) confirms the only verbs are
start/join/finish/end/submit — no "create activity type" copy anywhere.

### Task history

`docs/tasks/` has five activities specs (platform-core, observer-context, reactive-rules,
plugin-sdk, activation-ux). None covers type authoring UI. The gap was never scoped, not
descoped by accident.

## Why this shape (assessment)

The activation/session/submission runtime was shipped first so the end-to-end loop works
with types created out-of-band. Type authoring is the heaviest UX in the feature: it is a
JSON-Schema editor plus a three-branch validator form (in_process / webhook / mcp, the
last binding an agent + binding UUID). Deferring it while proving the runtime is a
reasonable ordering, not an oversight — but it leaves owners with no supported way to
create a type.

## Missing pieces, if this gap is to be closed

Backend (small):
1. Optional: expose `DELETE /api/projects/{id}/activity-types/{type_id}` over the existing
   `soft_delete_type` facade method (owner-only). Without it, types are create-only.
2. Optional: an update route if types should become editable (currently immutable by
   design — decide before building an edit UI).

Frontend (the bulk):
3. Wrap `registerActivityType` (and `softDeleteType` if route added) in
   `slices/activities/api/index.ts`.
4. A type-authoring surface. Placement decision needed: project settings (owner-level,
   matches the owner-only AuthZ and project scope) vs. a modal launched from the Activity
   panel. Project settings is the better fit — the panel is per-room and participant-
   facing, while a type is project-wide and owner-only.
5. The form itself:
   - key + name + retention_days (straightforward).
   - `payload_schema`: a JSON-Schema input. Options range from a raw JSON textarea with
     client-side validity check, to a guided field builder. The raw textarea is the
     minimum viable; a builder is a larger investment.
   - `validator_kind` selector driving a conditional sub-form:
     - in_process -> pick from registered `validator_id`s (needs a backend endpoint to
       list them; none exists yet — currently the frontend cannot enumerate valid ids).
     - webhook -> `url`.
     - mcp -> `tool_name` + agent picker + binding picker (agent_id / binding_id).
   - Backend already validates all of this and returns problem+json on error, so the form
     can lean on server-side validation for correctness and treat client checks as UX.
6. List + delete management view for existing types (owner-level).
7. i18n keys (en + zh-TW), tests (every view needs >=1 per gate 8), and
   `pnpm run gen:api` is not needed (client already has the method).

Note the hidden dependency in item 5: the in_process branch has no way to discover valid
`validator_id`s from the frontend. Either add a "list registered validators" endpoint or
restrict the first cut to webhook/mcp (or free-text with server-side rejection).

## Recommendation

If closing the gap: scope a task for a project-settings "Activity types" management page
(list + create + delete), MVP form using a raw JSON-Schema textarea and the three-branch
validator sub-form, plus the missing `DELETE` route and (for the in_process branch) a
validator-listing endpoint. This is primarily a frontend task with two small backend
additions. Hand it to `/spec` to produce the dossier.
