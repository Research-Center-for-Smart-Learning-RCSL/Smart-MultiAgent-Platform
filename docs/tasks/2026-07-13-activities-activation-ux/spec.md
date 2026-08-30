---
type: feature
status: implemented
created: 2026-07-13
requirements: [R30.01, R30.09, R30.17, R30.21, R30.22]
depends_on: [2026-07-13-activities-platform-core, 2026-07-13-activities-plugin-sdk]
---

# Activities Activation UX — in-chatroom facilitator broadcast + participant flow

## 1. Summary

Make the fully-built activities platform reachable end-to-end in a real chatroom (fills
**FU-3** of `2026-07-13-activities-plugin-sdk/spec.md:339-342`). Today the backend scoring,
reactive rules, observer context, and the frontend `ActivityHost`/SDK all exist but nothing
mounts the host, opens a session, or decides when an activity is live in a room.

This task adds a **room-level "active activity"** concept so a **facilitator** (the room
creator) can start one activity type for the whole room, broadcast over WebSocket; every
participant then joins from a right-rail **Activity tab**, explicitly starts their own
per-subject session, submits through the existing `ActivityHost`, and finishes. The
facilitator ends the activity, which — enforced server-side — stops further submissions.

Scope is full-stack: a new `ActivityActivation` aggregate + migration + three room-scoped
endpoints + two WS events + a submit-time enforcement check in the `activities` context, and
a frontend Activity rail tab that drives the facilitator and participant flows and mounts the
existing `ActivityHost`.

## 2. Goals and Non-goals

**Goals**
- A room creator can **activate** exactly one `ActivityType` for a room at a time (start),
  and **end** it. Persisted as a new `activity_activations` row; at most one `active` per room.
- **Broadcast**: starting/ending emits `activity.activation.started` / `activity.activation.ended`
  on the room channel; a `GET .../activity-activations/active` lets late-joiners/reconnects
  hydrate the current state.
- **Facilitator gate**: start/end require room-creator capability (`ensure_room_creator`),
  strictly stronger than the `ensure_can_send` floor the shipped session/submit routes use.
- **Participant flow**: any room sender sees the active activity in the Activity rail tab,
  explicitly **starts** (opens their per-subject session), submits via `ActivityHost` (plugin
  or schema-form), and **finishes** (closes their session).
- **Server-side enforcement**: a submission is accepted only while an `active` activation for
  that exact type exists in the room; otherwise rejected. This holds regardless of the client.
- Frontend Activity rail tab (mirroring the Observer tab), an activation store fed by WS +
  seeded by GET, api wrappers, `gen:api` rerun, i18n, boundaries, and the "no bare strings"
  gate satisfied.

**Non-goals** (explicit deferrals)
- **Force-closing participant sessions** when the facilitator ends the activation. Ending
  blocks new submissions (server-enforced) and removes the surface; open per-subject sessions
  are left as-is (closing them is cosmetic once submits are blocked). Recorded as FU.
- **Tightening the pre-existing arbitrary-`subject_user_id` submit gap** (platform-core allows
  a room sender to submit on behalf of any subject). v1 frontend always submits `subject=self`;
  server-side subject-spoofing prevention is a separate concern (FU).
- **Standalone activities route/view** — the still-open UX call in
  `plugin-sdk/spec.md:298-301`; the in-chatroom rail tab is sufficient, no route added.
- **Agent/workflow-driven activation** — the reactive layer only *reacts* to submissions and
  has no path to start an activity (`event_dispatch.py`/`workflow_signals.py`); activation
  stays client/API-driven.
- New validators, scoring, or plugin changes — consumed as-is.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How does an activity become active in a room | Facilitator broadcast, room-wide (new backend) | User chose the classroom/facilitator model over per-user self-serve; needs a room-level active-activity concept + WS broadcast the platform lacks today |
| Q-2 | Where does `ActivityHost` render | Right-rail Activity tab | User chose the rail tab (mirrors the Observer tab); gives a component-manipulation canvas room and keeps the transcript clean |
| Q-3 | Answering lifecycle (attempts/end) | Explicit participant start / finish | Participant explicitly opens (start) and closes (finish) their per-subject session; repeated attempts allowed within an open session (backend has no cap) |
| Q-4 | Reject submissions after the facilitator ends? | Server-side enforcement | User chose data integrity for the research context: submit requires an active activation for that type; UI-only coordination was rejected |
| Q-5 | Who is the "facilitator"? | The room creator (`ensure_room_creator` / `isCreator`) | The exact "privileged room surface" gate already exists (`access.py:139-163`) and the frontend mirror drives the Observer tab; no new role invented |
| Q-6 | Starting a different type while one is active | Reject with 409; same type is idempotent | The one-active-per-room partial-unique makes this natural; the facilitator must explicitly end before switching |

## 4. Current State (verified — Agent traces)

- **Facilitator gate already exists.** `ensure_room_creator(access, principal=principal)` /
  `is_room_creator` (`backend/contexts/conversation/application/access.py:139-163`): admin, or
  the actual room creator holding a live project/org role, or (legacy NULL-creator rooms) a
  moderator. Strictly stronger than `ensure_can_send` (`access.py:166-175`). The frontend mirror
  is `useObservations.isCreator` (`frontend/.../composables/useObservations.ts:63-71`), already
  used to gate the Observer rail tab (`ChatroomView.vue:425-427`).
- **No room-level active-activity state exists.** The `activities` context has three tables only
  (`backend/contexts/activities/infrastructure/tables.py`); sessions are per-`(type, room,
  subject)` (`tables.py:41-66`, partial-unique `uq_activity_sessions_open`
  `alembic/versions/0049_activities.py:115-118`). No "active per room" column/table/endpoint, and
  **no WS event for session open/close** — only `activity.created` (`app/api/v1/activities.py:335`)
  and `activity.validated` (`app/workers/tasks/activities.py:86-95`).
- **Facade/service/repo pattern** to mirror: `ActivitiesFacade` composes one service per sub-area
  (`interfaces/facade.py:44-49`); `ActivitySessionService.open_session` validates the type belongs
  to the room's project before touching its repo (`session_service.py:40-42`); repos use
  SQLAlchemy Core with `pg_insert(...).on_conflict_do_nothing().returning(...)` for race-safe
  create (`session_repo.py:84-96`) and guarded `UPDATE ... WHERE status='open'` for idempotent
  close (`:108-121`). Routes instantiate `ActivitiesFacade(db)` inline (no `Depends`;
  `activities.py:240,259,283,313`).
- **Migration conventions**: enums declared `create_type=False` in `tables.py:16-18`, minted in
  the migration via `pg.ENUM(...).create(bind, checkfirst=True)` (`0049:43-49`), partial-unique
  via raw `op.execute` (`0049:115-118`), dropped in reverse in `downgrade()` (`0049:183-200`).
  **Head revision is `0049_activities`** (unreferenced by any `down_revision`). tables.py column
  types MUST match the migration-created PG ENUMs (repo rule, `tables.py:1-7`).
- **WS + audit**: `Publisher(room_channel(chatroom_id)).emit(type, payload)` post-commit,
  best-effort in `try/except` (`activities.py:329-359`); `room_channel` →
  `contexts/conversation/interfaces` (`channels.py:12-13`); `Publisher` →
  `shared_kernel/realtime/pubsub.py:52`. Audit via `audit.emit(db, AuditEvent(...))` **inside the
  request transaction** (`submission_service.py:168-185`; `shared_kernel/audit.py:103-145`).
- **Submit path** (`submission_service.py:79-185`): validates payload against `payload_schema`,
  resolves/lazily-opens the per-subject session (`:292-329`), assigns `attempt_no` under
  `FOR UPDATE` (`:90`), writes a SYSTEM echo + audit atomically. Gated by `ensure_can_send`
  (`activities.py:281-282`); accepts an arbitrary `subject_user_id` (`:288`) — **pre-existing**
  spoofing gap.
- **Frontend hook surfaces** (`frontend/src/slices/conversation/views/ChatroomView.vue`):
  right-rail region `grid-column: 3` uses `STabs` (People + Observer) when `showObserverTab`
  (`:136-170, 425-427`); inline cross-slice card precedent = `ApprovalCard` via `orchStore`
  (`:79-87, 665`) fed by the WS switch (`useChatroomSocket.ts:246-271`). `useActivitiesStore` is
  already imported into the WS switch (`useChatroomSocket.ts:15,30`) and handles
  `activity.created`/`activity.validated` (`:275-295`), resets on unmount (`:350`). `ActivityHost`
  is exported (`slices/activities/index.ts:10`) but **mounted nowhere**. Project id resolves
  async via room→workspace→project (`observerProjectId` computed, `ChatroomView.vue:411-413`).
- **Existing activities api wrappers** already present: `openActivitySession`,
  `closeActivitySession`, `submitActivity`, `listActivityTypes`, `listActivitySubmissions`
  (`slices/activities/api/index.ts`); `useActivityHost` composable takes
  `{chatroomId, activityTypeId, sessionId?, subjectUserId?}`.

## 5. Design

### 5.1 Backend — `ActivityActivation` aggregate

- **Table `activity_activations`** (`contexts/activities/infrastructure/tables.py`): `id uuid pk`,
  `chatroom_id → chatrooms.id (CASCADE)`, `activity_type_id → activity_types.id (CASCADE)`,
  `started_by_user_id → users.id`, `status activation_status` (server default `'active'`),
  `created_at now()`, `ended_at timestamptz null`. New PG enum
  `activation_status = {active, ended}` declared `create_type=False`.
- **Migration `0050_activity_activations`** (`down_revision = "0049_activities"`): mint the enum,
  create the table, and the **partial-unique** `uq_activity_activations_active ON
  activity_activations (chatroom_id) WHERE status = 'active'` (one active per room). Reverse-drop
  in `downgrade()`. Register the table in `app/db_registry.py`.
- **`ActivationService`** (`application/activation_service.py`) + **`ActivationRepository`**
  (`infrastructure/repositories/activation_repo.py`), mirroring session service/repo:
  - `start(chatroom_id, activity_type_id, started_by, *, actor_ip, request_id)`: verify the type
    belongs to the room's project (mirror `session_service.py:40-42`, else `ActivityTypeNotFound`);
    `pg_insert(...).on_conflict_do_nothing()` on the partial-unique; if inserted → audit
    `activity.activation_started`, return row; on conflict fetch the current active row — if its
    `activity_type_id` matches, return it (idempotent), else raise `ActivityAlreadyActive` (→ 409).
  - `end(chatroom_id, activation_id)`: verify the row belongs to the room; guarded
    `UPDATE ... SET status='ended', ended_at=now() WHERE status='active'` (idempotent double-end);
    audit `activity.activation_ended`.
  - `get_active(chatroom_id) -> ActivityActivation | None`.
- **Facade**: add `self._activation = ActivationService(db)` (`interfaces/facade.py:44-49`) and
  pass-throughs `start_activation` / `end_activation` / `get_active_activation`.

### 5.2 Backend — routes (`app/api/v1/activities.py`, `chatroom_router`)

- `POST /api/chatrooms/{chatroom_id}/activity-activations` — body `ActivityActivationStartIn
  {activity_type_id}`; `resolve_room_access` → **`ensure_room_creator(access, principal=principal)`**;
  `ActivitiesFacade(db).start_activation(...)`; `db.commit()`; post-commit best-effort WS
  `activity.activation.started {activation_id, activity_type_id, started_by}`; return
  `ActivityActivationOut`.
- `PATCH /api/chatrooms/{chatroom_id}/activity-activations/{activation_id}/end` —
  `ensure_room_creator`; `end_activation`; commit; WS `activity.activation.ended {activation_id}`;
  return the ended row.
- `GET /api/chatrooms/{chatroom_id}/activity-activations/active` — `ensure_can_read`; returns
  `ActivityActivationOut | null` for hydration.
- Pydantic `ActivityActivationStartIn` / `ActivityActivationOut` + `_activation_out` mapper
  (enum via `.value`, ISO datetimes), mirroring `_session_out` (`:130-182`).

### 5.3 Backend — submit enforcement (Q-4)

In the submit path (`SubmissionService.submit`, `submission_service.py`), after resolving the
type and before/around session resolution, call `ActivationRepository(db).get_active(chatroom_id)`
(intra-context — no new cross-context dependency) and require a non-null active activation whose
`activity_type_id == submission.activity_type_id`; else raise new domain error `ActivityNotActive`
(mapped to 409 in `interfaces/error_mapping.py`). This makes "submissions only while active"
hold against any client. Route still gates `ensure_can_send` first.

### 5.4 Frontend — Activity rail tab + flows

- **`slices/activities/components/ActivityPanel.vue`** (new, exported): props
  `{ chatroomId, projectId, isCreator }`. Renders:
  - **Facilitator (isCreator)**: when no active activation — a launcher: pick a type from
    `listActivityTypes(projectId)` (gated on the async `projectId`) + "Start for room"
    (`startActivation`). When active — the active type + "End" (`endActivation`).
  - **Participant (all)**: when active — the activity name + "Start" (opens their per-subject
    session via `openActivitySession`, subject=self) → mounts the existing **`ActivityHost`**
    (`:activity-type`, `:chatroom-id`, `:session-id`, `:subject-user-id=self`) → submit → outcome
    via the existing store/badge → "Finish" (`closeActivitySession`).
- **`ChatroomView.vue`**: add an Activity tab to the right-rail `STabs` (and the mobile drawer),
  rendering `<ActivityPanel>`. Tab visibility: shown when `isCreator` OR an active activation
  exists (participants need it only while live). Reuse `observations.isCreator` and the
  `observerProjectId` computed.
- **Store**: extend `useActivitiesStore` with `activations: Record<roomId, ActivationView | null>`
  + `setActivation` / `clearActivation` / `getActivation`, reset in `resetRoom`. Seed from
  `getActiveActivation` on panel mount; keep in sync via WS.
- **WS switch** (`useChatroomSocket.ts`): add `activity.activation.started` → `setActivation`;
  `activity.activation.ended` → `clearActivation`.
- **api wrappers** (`slices/activities/api/index.ts`): `startActivation`, `endActivation`,
  `getActiveActivation`; `activityKeys.activeActivation(chatroomId)`; slice types
  `ActivityActivation`.
- **`gen:api`** rerun to surface the three new endpoints/models.

## 6. Detailed Changes

- **Backend**: `tables.py` (+enum, +table); migration `0050_activity_activations`;
  `db_registry.py` (+table import); `domain/models.py` (`ActivationStatus`, `ActivityActivation`,
  `ActivityAlreadyActive`, `ActivityNotActive`); `application/activation_service.py`;
  `infrastructure/repositories/activation_repo.py`; `interfaces/facade.py` (+service +3 methods);
  `interfaces/error_mapping.py` (map the two new errors); `submission_service.py` (enforcement
  check); `app/api/v1/activities.py` (3 routes, 2 Pydantic models, `_activation_out`, 2 WS emits,
  2 audit actions).
- **Frontend**: `slices/activities/` — `components/ActivityPanel.vue`, `api/index.ts` (+3
  wrappers), `queries/index.ts` (+key), `stores/activities.ts` (+activation state),
  `types/index.ts` (+`ActivityActivation`), `index.ts` (export `ActivityPanel`), `locales/{en,zh-TW}.json`;
  `slices/conversation/views/ChatroomView.vue` (+Activity tab +drawer), `composables/useChatroomSocket.ts`
  (+2 WS cases). `pnpm run gen:api`.

## 7. NFR Checklist

- [x] i18n — all panel/tab strings via `$t()`; new keys in both locale bundles; passes
  `vue/no-bare-strings-in-template`.
- [x] Audit — `activity.activation_started` / `activity.activation_ended` written in-transaction
  (mirror `submission_service.py:168-185`).
- [x] Tenant isolation — activation type must belong to the room's project (service check); all
  routes go through `resolve_room_access`; GET active gated `ensure_can_read`.
- [x] Error handling — 409 on `ActivityAlreadyActive` / `ActivityNotActive`; frontend surfaces a
  typed `ApiError`; participant surface reflects `activity.activation.ended`.
- [x] Performance — activation seeded once via GET then WS-driven; the store keys by room (no
  refetch per event); one indexed active row per room.

## 8. Security Considerations

Touches WebSocket, room authZ, user-input submission, and a new privileged room action — full lens:
- **Facilitator authorization.** Start/end gated by `ensure_room_creator` (`access.py:161`) — the
  established privileged-room-surface gate; strictly stronger than the `ensure_can_send` floor.
  Non-creators get 403. Admin bypass matches every other room surface.
- **Server-side submission enforcement (Q-4).** A submission requires an `active` activation for
  its exact type in the room (§5.3); a tampered client cannot submit before/after the window. This
  is a server guarantee independent of the UI.
- **Tenant isolation.** The activation's type must belong to the room's project (service check,
  mirrors `session_service.py:40-42`); the partial-unique is room-scoped; the room-access chain
  derives the project server-side.
- **WS is read-only display.** `activity.activation.*` events update local state only; no event
  payload drives an authorization decision.
- **Pre-existing subject-spoofing gap (flagged, not fixed here).** `submit` still accepts an
  arbitrary `subject_user_id` (`activities.py:288`); v1 frontend always sends `subject=self`, but
  a crafted client could submit for another subject. Out of this task's scope — recorded as FU-3;
  it predates this change and the enforcement above narrows *when*, not *for-whom*.
- **No secrets, no injection** — payload validated against `payload_schema` server-side (unchanged);
  no raw SQL (Core expressions); activation ids are UUIDs.

## 9. Quality Notes

- **Patterns to follow:** service+repo split (`session_service.py`/`session_repo.py`); facade
  pass-through (`interfaces/facade.py`); migration enum + partial-unique idiom (`0049_activities.py`);
  post-commit best-effort WS (`activities.py:329-359`); in-transaction audit
  (`submission_service.py:168-185`); rail-tab + `isCreator` gate (Observer tab, `ChatroomView.vue`);
  cross-slice store fed by WS switch (`orchStore`/`ApprovalCard`); `observerProjectId` async-project
  pattern.
- **Reuse inventory:** `ensure_room_creator` / `is_room_creator` (no new role); `Publisher` +
  `room_channel`; `audit.emit`/`AuditEvent`; the existing `ActivityHost`, `useActivityHost`,
  `ActivityOutcomeBadge`, `openActivitySession`/`closeActivitySession`/`submitActivity`/
  `listActivityTypes` wrappers, `useActivitiesStore`; `STabs`, `SButton`, `SSelect`, `SDrawer`,
  `SEmptyState` atoms; `useObservations.isCreator` (or replicate the computed).
- **Debt to avoid:** do not add a distinct "facilitator" role (reuse the creator gate); do not
  emit WS before commit; do not let the activation table diverge from its migration enum
  (`tables.py` rule); do not mount `ActivityHost` outside the panel.

## 10. Risks and Rollback

- **Migration + new PG enum** — follow the `0049` idiom exactly; sanity-check `downgrade()`
  (drop partial index → table → enum). Head is `0049_activities`.
- **Submit-path coupling (§5.3)** — the enforcement check adds an intra-context query on the hot
  submit path; keep it a single indexed lookup and ordered before the `FOR UPDATE` attempt.
- **Dependency direction** — `ActivityPanel` lives in `slices/activities` and is rendered by
  `conversation` (already depends on activities, one-way); activities must not import conversation.
- **Rollback**: additive. Removing the rail tab + WS cases + the three endpoints disables the UX;
  the migration downgrade drops the table/enum. Existing sessions/submissions are unaffected
  (enforcement is the only submit-path behavior change — gate it so a missing activation table
  can't 500 the submit path during a partial rollback).

## 11. Acceptance Criteria

- [x] AC-1: A room creator can start an activity for the room from the project's type catalog; a
  non-creator receives 403. At most one active per room — starting a *different* type while one is
  active returns 409; starting the *same* type is idempotent.
- [x] AC-2: Starting emits `activity.activation.started`; a connected participant's Activity tab
  shows the active activity with no refetch; a late-joiner/reconnect hydrates via
  `GET .../activity-activations/active`.
- [x] AC-3: A participant explicitly starts (opens their per-subject session), `ActivityHost`
  mounts (plugin or schema-form path) and submits, and explicitly finishes (closes their session);
  repeated attempts are allowed within the open session.
- [x] AC-4: The facilitator ends the activity → `activity.activation.ended` broadcast → participant
  surfaces clear; a submission attempted after the end is rejected server-side (`ActivityNotActive`,
  409) even if the client bypasses the UI.
- [x] AC-5: A submission is accepted only when an `active` activation for that exact type exists in
  the room; submitting with no active activation, or for a type that is not the active one, is
  rejected server-side.
- [x] AC-6: AuthZ verified end-to-end — start/end = `ensure_room_creator`; GET active =
  `ensure_can_read`; submit = `ensure_can_send` + active-activation check; the activation type must
  belong to the room's project (tenant isolation).
- [x] AC-7: `alembic upgrade head` applies and `downgrade` reverses cleanly; the `activation_status`
  enum and the partial-unique one-active-per-room index exist; `tables.py` column types match the
  migration.
- [x] AC-8: Gates green — backend `pytest`/`ruff`/`mypy`; frontend `pnpm lint`/`typecheck`/`build`;
  `gen:api` rerun; new i18n keys present in `en` and `zh-TW`.

## 12. Test Plan

- **Backend unit**: `ActivationService.start` get-or-create + same-type idempotency +
  different-type `ActivityAlreadyActive`; `end` idempotency; `get_active`; tenant-isolation
  rejection; submit enforcement (`ActivityNotActive` when none/wrong type active).
- **Backend routes**: creator-only start/end (403 for non-creator), 409 conflict, GET active
  read-gate, submit rejected when inactive; WS emits fired post-commit; audit rows written.
- **Migration**: upgrade/downgrade round-trip; enum + partial-unique present.
- **Frontend**: store activation reducer (set/clear/get, resetRoom); `ActivityPanel` facilitator
  (launcher/start/end) vs participant (start→host→submit→finish) paths (mock api-client); rail-tab
  visibility (creator vs active-vs-inactive); WS `activity.activation.*` update the store.
- **Gates** in CI (AC-8).

## 13. SRS Delta

Append to chapter **§30**, continuing the numbering after `[R30.20]`:

```
- **[R30.21]** A room facilitator (the room creator; admins bypass) may activate exactly one `ActivityType` for a room at a time, and end it. Activation is persisted (at most one active per room) and broadcast on the room channel (`activity.activation.started` / `activity.activation.ended`); a room-scoped read endpoint exposes the current active activity so late-joining or reconnecting participants hydrate the same state. Starting is gated by room-creator capability — strictly stronger than the send-message floor; starting a different type while one is active is rejected until the current one is ended.
- **[R30.22]** A submission is accepted only while an active activation for that exact activity type exists in the room; otherwise the platform rejects it. This is enforced server-side and holds regardless of the client, so a facilitator ending an activity stops further submissions and out-of-window data cannot enter the authoritative record. Participants join an active activity, open their own per-subject session (explicit start), submit, and finish (close); ending the room activation does not force-close open participant sessions but blocks their further submissions.
```

## 14. Open Questions

- None blocking. (Whether ending an activation should also auto-close open participant sessions is
  deferred as FU-2 — cosmetic once submissions are blocked.)

## 15. Deviation Log

- **D-1 (strengthening, §5.3/§10): submit enforcement uses a locking read, not a plain lookup.**
  Spec called for "a single indexed lookup ... ordered before the `FOR UPDATE` attempt". The build
  reads the active row with `SELECT ... FOR UPDATE` via `ActivationRepository.get_active_for_update`
  (`activation_repo.py:53-69`), called in `SubmissionService.submit` before session resolution
  (`submission_service.py:85-87`). This serializes the facilitator's guarded end-`UPDATE` against an
  in-flight submit, closing the check-then-insert race the plain lookup left open. Still one indexed
  access on the partial-unique; ordering unchanged.
- **D-2 (addition): explicit `end` transition result to gate the WS broadcast.**
  `ActivationService.end` returns `ActivityActivationEndResult{activation, transitioned}`
  (`models.py:86-91`, `activation_service.py:75-106`); the route emits `activity.activation.ended`
  only when `transitioned` is true (`activities.py:300-301`), so an idempotent double-end does not
  replay the event. Not specified either way; chosen to match the "post-commit best-effort WS" idiom
  without spurious broadcasts.
- **D-3 (structure): a `ports.py` Protocol seam between service and repo.**
  `ActivationService` / `SubmissionService` depend on `ActivityActivationRepository` /
  `ActivityTypeReader` Protocols (`application/ports.py`), with the concrete `ActivationRepository`
  injected by the facade (`facade.py:54-61`). Lets submit-path enforcement reuse the same repo
  instance and keeps the application layer free of an infrastructure import. Spec named the repo
  directly; behavior identical.
- **D-4 (no-op vs spec): `db_registry.py` needed no edit.**
  §5.1 said to "register the table in `app/db_registry.py`". The registry imports each context's
  `tables` module by side effect (`db_registry.py:15-17`), so adding `activity_activations` to
  `tables.py` registers it automatically. No registry change made.
- **D-5 (review fixes): three gate failures the initial build shipped, fixed during review.**
  `ActivityPanel.vue` prop typed `projectId?: string | undefined` to satisfy
  `exactOptionalPropertyTypes` when bound to the `string | undefined` `observerProjectId`
  (typecheck); the active-activity `<p>` split to multiple lines
  (`vue/singleline-html-element-content-newline`); and `useChatroomSocket.test.ts` switched from an
  inline `import()` type to a top-level `import type * as ActivitiesSlice`
  (`@typescript-eslint/consistent-type-imports`). All four frontend gates
  (typecheck/lint/test/build) green after these.

## 16. Follow-ups

- FU-1: Standalone activities route/view beyond the in-chatroom rail tab (the still-open UX call
  from `plugin-sdk/spec.md:298-301`).
- FU-2: Auto-close open per-subject sessions when the facilitator ends the activation (currently
  left open; submissions are already blocked by [R30.22]).
- FU-3: Close the pre-existing arbitrary-`subject_user_id` submit gap (a room sender can submit on
  behalf of any subject; predates this task) — enforce `subject == caller` unless a facilitator
  capability explicitly allows proxying.
