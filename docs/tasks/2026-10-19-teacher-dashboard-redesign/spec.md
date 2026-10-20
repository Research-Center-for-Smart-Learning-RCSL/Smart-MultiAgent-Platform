---
type: feature
status: in-progress
created: 2026-10-19
requirements: [R33.01, R33.02, R33.03, R33.04, R33.10, R13.32, R8.08, R8.09, R8.11, R28.18, R24.27, R24.28]
depends_on: []
---

# Teacher dashboard redesign

## 1. Summary

The teacher dashboard (`/workspaces/:workspaceId/dashboard`) is meant to answer one question
for a teacher running a live class: which room or student needs attention right now. Today it
cannot. Its data is wrong in ways a teacher reads as noise: every room without a running round
is permanently red, the traffic light never ages while the page is open, and the watchlist always
lists about half the class while omitting the students who never submitted. Its layout gives no
hierarchy: five equal sections stacked in one column, with an empty alert section at the top.

This dossier rebuilds the dashboard end to end. It absorbs the dashboard findings of the
2026-10-05 audit (`docs/audits/2026-10-05-teacher-dashboard-and-research-export/findings.md`),
whose planned fix dossiers were never created: F-1, F-2, F-3, F-4, F-7, F-8, F-9, F-11, F-16,
F-17, F-18, F-19, F-20, F-21, F-22 and F-23 (F-24 is already fixed, see §4.6). It redefines room
status and the watchlist around the current activity round, shows students by display name in the
live view while the research export keeps pseudonymous codes, adds per-room online and
participation figures, and replaces the page layout with a triage-first design built from new
shared primitives. The analysis behind it came from three independent reviews (functional,
visual, data feasibility) run on 2026-10-19 and the four evidence passes recorded in §4.

## 2. Goals and Non-goals

**Goals**

- G-1. Room status means "is this room's current round producing valid work", ages with the
  clock while the page is open, and is grey (no alert) for rooms without a running round.
- G-2. The watchlist lists the students present in a round who have produced no valid submission
  in it, or none in the last 10 minutes, including students with zero submissions, named by
  display name, with the room they are in.
- G-3. The dashboard shows only rooms the caller can open ([R13.32]), refuses soft-deleted
  projects ([R8.11]), and its live channel admits exactly the callers the REST reads admit.
- G-4. The research export is offered to, and accepted from, exactly the callers [R33.10] names,
  including Org Owners as computed Project Owners ([R8.08]).
- G-5. The page leads with a KPI strip and a needs-attention area, room cards sorted by urgency
  with a non-colour status cue and a link into the room, and a trend chart with a stable,
  token-based, status-neutral palette and a correct time axis.
- G-6. Every panel has distinct loading, error and empty states; no failure renders as good news.
- G-7. Per-room online count, participant count and the student/agent message split for the
  current round are shown on each room card.
- G-8. Three shared primitives exist and absorb their hand-rolled duplicates: `SStatTile`,
  `SSegmentedControl`, and `useChartTheme`, plus a shared number/percent formatter.

**Non-goals**

- The research export's own defects F-5, F-6, F-10, F-12, F-13, F-14 and F-15 (delivery, data
  shape, worker robustness). Only its authorization (F-8) is in scope. See FU-1.
- Restyling `ResearchExportButton.vue`'s hand-rolled modal (§9.1). See FU-1.
- Configurable thresholds per workspace (teacher-dashboard FU-4, workspace-scope FU-3). The
  thresholds stay code constants.
- A custom date-range scope on the summary. Counts are scoped to the current round only (Q-8).
- Naming who has finished a round on the progress endpoint
  (`backend/app/api/v1/activities.py:1059-1061`). The watchlist names students who have *not*
  produced valid work; it does not change the progress endpoint or its gate.
- Detecting that a student caught an AI peer's deliberate error. No stored signal exists
  (`docs/tasks/2026-10-19-peer-deliberate-errors/spec.md:26`, its FU-1). See FU-2.
- The sidebar Dashboard link that contradicts [R33.02] (audit FU-3). Left as is; see FU-3.
- A `ws:workspace` channel or project-level overview (workspace-scope FU-1, FU-2).
- Any change to the chat-bubble agent palette of `2026-10-19-chat-bubble-sides-and-agent-colors`.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How should the work be split into dossiers? | One dossier, built in five stages (§6), each a commit milestone. | The requester's choice over three phase dossiers or the audit's original grouping. The stages keep each commit revertible. |
| Q-2 | Where do the audit's authorization findings (F-7, F-8, F-9, F-17, F-18, F-23) go? | Into this dossier, as Stage B. | The requester's choice. Stage B lands before room cards become links (Stage D), so no link ever points at a room the caller cannot open. |
| Q-3 | How is a room with no running round shown? | Grey "No active round" status, sorted last, never alerted. | The requester's choice. It removes the permanent red state of default and chat-only rooms (F-2). |
| Q-4 | Students' identity in the live view? | Display names in the live view; the research export keeps truncated subject codes. | The requester's choice. A teacher cannot act on `u:1a2b3c4d`. This amends [R33.02] (§13); [R33.03] (no names on the WebSocket) and [R33.10] are unchanged. |
| Q-5 | Who sees the names? | Every caller the dashboard admits (project role holders, filtered to rooms they can open). | The requester's choice over owner-only names. Recorded consequence: a student who holds a project role sees classmates' names on the watchlist for rooms that student can open. §8 and §10 restate this. |
| Q-6 | Without a room-members table, who is "in" a round? | Present in the round: holds an activity session in the round, or sent a message in the room since the round started, or is a guest session seen since the round started, or is online in the room now; minus callers holding the project owner role. | The requester's choice. It catches "present but not submitting". It cannot catch a student who never entered the room, and a student who was online earlier but neither posted nor stayed is missed (§10, R-3). |
| Q-7 | When does a student need attention? | No valid submission in the current round, or no valid submission in the last 10 minutes while the round is active. | The requester's choice over a median-relative rule. The 10 minutes reuses the never-read `_ALERT_MINUTES` (`backend/app/api/v1/dashboard.py:28`), renamed. |
| Q-8 | What time scope do summary counts use? | The room's current round: the active activation, else the most recent ended one, else none. | A teacher reads "today's class", which lifetime counts cannot show (functional review). A date-range picker is a non-goal. Decided by the spec author; open to change at approval. |
| Q-9 | Chart palette vs the approved chat-bubble palette in `main.css`? | Independent `--color-chart-1..8` tokens; no `depends_on`. | The requester's choice. The bubble palette includes amber, orange and rose (`2026-10-19-chat-bubble-sides-and-agent-colors/spec.md:233`), which read as status. The two edit different regions of `main.css`; whoever builds second rebases, including `contrast.test.ts`. |
| Q-10 | Should status age on the server or the client? | The server stays the only place status is computed; the summary and watchlist queries refetch every 30 seconds. | One rule in one place. Recomputing on the client would duplicate the thresholds and their tests. The cost is 2 small queries per 30 seconds per open dashboard (§7). Decided by the spec author. |
| Q-11 | In a group-activity round, who does the watchlist list? | Groups, named by member-group name, not individual students. | Group submissions are attributed to `subject_member_group_id` (`backend/contexts/activities/infrastructure/tables.py:74-104`), so per-student validity does not exist in a group round. Decided by the spec author; open to change at approval. |
| Q-12 | Does this depend on any active dossier? | No. | The scan of all five non-implemented dossiers (§4.8) found one overlap, the chat-bubble dossier, in disjoint regions of `main.css` and `access.py` (Q-9). |

## 4. Current State

### 4.1 Backend routes

`backend/app/api/v1/dashboard.py` exposes three reads under `/api/v1/workspaces/{id}/dashboard/`:
`summary` (`:128-155`), `timeseries` (`:158-178`) and `watchlist` (`:181-204`). Each resolves
rooms through `_resolve_workspace_rooms` (`:109-125`): it loads the workspace, returns 404 if it
is missing or deleted, calls `assert_project_role` (`:120`), then returns every room id in the
workspace (`list_chatroom_ids_for_workspace`, `:121`) with its name. The traffic light
(`_traffic_light`, `:98-106`) is computed once per request from `last_submission_at` alone:
green at 5 minutes or less, yellow at 15 or less, red otherwise or when there is no submission
(`:26-27`). `_ALERT_MINUTES = 10` (`:28`) is read nowhere. The watchlist hard-codes
`needs_attention=True` (`:200`).

### 4.2 Aggregation

`ActivitiesFacade` exposes `aggregate_for_rooms`, `timeseries_for_rooms` and
`watchlist_for_rooms` (`backend/contexts/activities/interfaces/facade.py:988-1017`). The repository
(`backend/contexts/activities/infrastructure/repositories/submission_repo.py`) computes:

- per-room total, valid (`is_valid IS TRUE`) and `max(created_at)` over all rows whatever their
  validity or status (`:614-645`, the max at `:626`), with no time scope;
- valid-only counts in `floor(epoch / bucket)` buckets, returning no row for empty buckets
  (`:647-684`);
- per-subject submission counts from submissions joined to sessions (`:686-720`), so every row
  has a count of at least one and invalid attempts are counted.

`watchlist_for_rooms` keeps subjects with `count <= median`
(`backend/contexts/activities/application/aggregation_service.py:102-121`, comparison at `:120`).
`tests/unit/test_dashboard_aggregation.py:53-66` mocks a zero-count row the real query cannot
produce, so it passes regardless of behavior; `:19-32` pins the median rule.

Activations live in `activity_activations` (`tables.py:107-143`) with status `active` or
`ended` (`tables.py:19`) and at most one active per room
(`alembic/versions/0050_activity_activations.py:60-61`). Only single-room reads exist:
`get_active_activation` (`facade.py:486-487`, repo `activation_repo.py:54-73`). Sessions carry
`activation_id`, `subject_user_id` (a user id or a guest session id, no FK), `subject_kind`
(`user` or `guest`) and `subject_member_group_id` (`tables.py:74-104`). A session is opened by a
participant's first submission (`backend/contexts/activities/application/submission_service.py:510-521`)
or by an explicit open (`backend/app/api/v1/activities.py:1080`).

### 4.3 Identity and roster sources

There is no room-members table; access is decided by room flags
(`backend/contexts/conversation/application/access.py:225-259`). The pieces a roster can be built
from are all single-room today:

- distinct human message senders: `ConversationFacade.distinct_user_sender_ids` (`facade.py:462`,
  repo `message_repo.py:169`);
- guests: `ConversationFacade.room_guests` (`facade.py:305-314`, returning sessions, registered
  and unaffiliated guests, `application/room_guests.py:25-47`);
- online now: `ConversationFacade.room_presence_snapshot` (`facade.py:521`), which reconciles
  ghosts and reports `available`/`truncated` (`application/room_presence.py:17-25`). Plain `SCARD`
  on `ws:presence:{room}` counts ghosts
  (`backend/contexts/conversation/infrastructure/presence.py:268-318`).

Display names resolve in batch for users through `IdentityFacade.get_display_names`
(`backend/contexts/identity/interfaces/facade.py:71`). Guest session labels resolve per room only
(`guest_session_repo.py:68`); there is no batch lookup by session id. Group names resolve through
`ActivitiesFacade.resolve_member_group_names` (`facade.py:927-942`). Subject codes are `u:` or
`g:` plus eight id characters (`backend/contexts/activities/domain/subject_code.py:30-43`); their
docstring records that no name is ever resolved on that path (`:14-16`).

The privacy comments on adjacent surfaces are load-bearing for §8: `activities/interfaces/facade.py:714-715`
and `app/api/v1/activities.py:249-251,1059-1061` keep "who has finished" a separate decision.

### 4.4 Messages

`messages.sender_type` is `user`, `agent` or `system` (`alembic/versions/0017`, enum at `:27`);
guests post as `user` with a guest session id as `sender_id`. The index
`ix_messages_chatroom_created (chatroom_id, created_at DESC) WHERE deleted_at IS NULL`
(`0017:74-76`) serves a windowed per-room count. No count-by-sender query exists.

### 4.5 Realtime

The backend publishes `dashboard.activation.changed {room_id, status}` and
`dashboard.submission.validated {room_id, type_key, is_valid}` on the project channel
(`backend/contexts/activities/interfaces/broadcast.py:266-300`). Frames are flat
(`backend/shared_kernel/realtime/pubsub.py:52-56`), but the client reads `ev.payload.room_id`
(`frontend/src/slices/dashboard/composables/useDashboardSocket.ts:62-63`), which is always
undefined, so the room filter never applies (F-16). Every event invalidates all three queries
after a 500 ms debounce (`:30-35`). No dashboard query sets `refetchInterval`, so a quiet room
never ages (F-1). The socket's `connected` flag is returned (`:86`) and discarded
(`DashboardView.vue:53`); missed events are never recovered after a reconnect (F-21). The worker
sets `is_valid = result_status == "validated"`, which is true for an invalid verdict
(`backend/app/workers/tasks/activities.py:162,182`; `submission_repo.py:256-292`), and the
watchdog sweep emits no dashboard event (`activities.py:188-239`) (F-22).

### 4.6 Authorization

- REST: `assert_project_role` (`backend/app/api/v1/deps.py:117-133`) uses the role resolver, so a
  bare Org Member is refused. That is F-24's fix, pinned by `tests/unit/test_require_project_role.py:221`.
- F-9: `_resolve_workspace_rooms` returns every room, while the facade already offers the
  [R13.32] batch gate as `ConversationFacade.visible_rooms_in_workspace(*, principal, workspace_id)`
  (`facade.py:128-141`, built on `access.py:272-345`) and `workspaces.py:87-91` uses it.
- F-8: research export calls `assert_project_owner` (`research_export.py:91`), which accepts only
  an explicit OWNER row (`deps.py:65-97`, `tenancy/interfaces/facade.py:134-140`). The UI shows
  the button on `is_moderator`, which includes inherited Org Owners
  (`frontend/src/slices/tenancy/composables/useProjectRole.ts:37-39`).
- F-7: the view gates export on the persisted sidebar project
  (`frontend/src/slices/dashboard/views/DashboardView.vue:57-60`), not the viewed workspace's.
- F-17: the project socket requires an explicit membership row at connect and on re-check
  (`backend/app/api/ws/project.py:37-47`), unlike REST; the client retries forever
  (`frontend/src/shared/transport/ws-manager.ts:203-229`).
- F-18: soft-deleting a project cascades to neither workspaces nor members
  (`tenancy/application/project_service.py:204-242`); the dashboard checks only the workspace and
  the resolver loads deleted projects on purpose (`role_resolver.py:37`).
- F-23: the project socket's room-level re-check runs every second 30-second tick
  (`backend/shared_kernel/realtime/connection.py:75,82,421-446`).

### 4.7 Frontend

`DashboardView.vue` hard-codes `bucketSize` to `5m` (`:29`); stacks Alerts (`:96-101`), room grid
(`:103-114`), chart card (`:116-134`) and watchlist card (`:136-150`); shows only the summary error
(`:74-79`), so a failed watchlist renders "All participants are above median" (F-20). Room cards
show status only through a small badge (`RoomStatusCard.vue:44-50`) and an absolute
`toLocaleString()` (`:34`). Alerts render red rooms as `warning` (`DashboardAlerts.vue:27`). The
chart registers a duplicate Title (`OutputChart.vue:93-97`), uses seven literal `rgb()` colours
picked by insertion index (`:29-37,59`), builds its axis from returned buckets only so a slot
with no data in any room vanishes (`:45-55`, F-19), and labels with `toLocaleTimeString([])`
(`:66-68`), which ignores the app locale and has no date part at 24 hours. The window selector is
three loose buttons without group semantics (`TimeWindowSelector.vue:13-23`). The view test
mocks every composable and asserts two strings (`__tests__/DashboardView.test.ts:77-126`).
`DashboardView` is about 61.7 KB gzip against the 200 KB per-view budget
(`frontend/scripts/check-bundle-size.sh:8`).

### 4.8 Dependency scan

Five dossiers are not implemented: `2026-10-19-chat-bubble-sides-and-agent-colors` (approved),
`2026-10-19-peer-deliberate-errors`, `2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence`,
`2026-07-19-large-artifacts-silently-dropped` (in progress), and
`2026-10-12-graphrag-phase5a2-evidence-refs-contract` (approved, deploy-gated). Only the first
touches this dossier's files: it adds `--color-agent-*` tokens to both theme blocks of
`main.css` with new pairs in `contrast.test.ts` (its spec `:321-322`, `:437-439`), and extracts a
helper in `access.py` (`:263-265`). Neither region is edited here (Q-9, Q-12).

## 5. Design

### Options considered

**Option A: restyle only.** Rebuild the page on the current API. It is the smallest change, but
it renders the wrong data more attractively: a permanently red default room, a watchlist that
lists half the class and omits non-submitters, and a status that never ages.

**Option B: fix the audit findings, then restyle separately.** Matches the audit's original
grouping. It specifies the backend semantics without the page that consumes them, so the
response shapes would be designed twice.

**Option C: one redesign around the current round (chosen).** Define "current round" once in the
backend, derive status, counts and the watchlist from it, add the fields the new page needs, and
build the page against that contract.

### Decision

Option C. The organising concept is the room's **current round**: its active activation, else
its most recent ended activation, else none (Q-8). Everything the teacher reads hangs off it:

- **Status** (`active_round`, else `inactive`): with an active round, minutes since the later of
  the round's start and its last *valid* submission decide green (5 or less), yellow (15 or
  less) or red. Measuring from the round start keeps a just-started round from opening red. A
  room whose round has ended, or which never had one, is `inactive` (grey) and never alerted
  (Q-3). Red is the alert condition, and the alert text says 15 minutes, matching the code.
- **Counts** are the current round's total and valid submissions.
- **Watchlist** rows are the round's present participants (Q-6) who need attention (Q-7),
  returned with all present participants so the table can show the whole room, flagged rows first.

The page is triage-first, following the functional review over the visual review's chart-led
layout. A teacher in class first needs where to go, then whether the class is on track, then the
trend.

```
[ Header: title, workspace name        (o Live, updated 12s ago) [Export] ]
[ KPI: Active rounds | Valid this round (78%) | Stalled rooms | Need attention ]
[ Needs attention: one danger alert listing stalled rooms (only if any)    ]
+------------------------- 8 cols ---------------+------- 4 cols ---------+
| Rooms (sorted red, yellow, green, inactive)    | Students needing        |
| cards: status rail + icon, valid/total bar,    | attention (flagged      |
| last valid (relative), online, link to room    | rows, room, reason)     |
+------------------------------------------------+-------------------------+
[ Trend (valid submissions)            [1h | 6h | 24h]                     ]
[ All participants (table, filter by room)                                 ]
```

At `md` the two columns stack; at mobile width everything is one column with a 16 px gutter and
the student tables use `STable`'s card list (`STable.vue`, `responsiveMode` default).

Given up: a client-side clock recomputing status (Q-10), owner-only names (Q-5), a date-range
scope (Q-8), and absorbing the research export's other defects (FU-1).

## 6. Detailed Changes

Five stages, each one commit milestone (or a small series), in this order.

### Stage A: shared primitives and tokens (frontend only, no dashboard behavior change)

- `frontend/src/shared/ui/SStatTile.vue`: props `label`, `value` (string or number), `hint?`,
  `icon?` (a heroicon component), `tone` (`neutral` default, `success`, `warning`, `danger`),
  `compact?`; slot `#value` for a tooltip-wrapped value. Value at `--font-size-2xl` semibold with
  `font-variant-numeric: tabular-nums`; label at `--font-size-xs`, `--color-muted`. Tone colours
  only the value and icon through existing status tokens. Absorbs the tiles in
  `slices/admin/views/AdminHomeView.vue:31-38,68-88`, `AdminMetricsView.vue:31-43,79-105`, and
  `slices/keys/components/UsageDashboard.vue:159-178` (its `danger` flag maps to `tone="danger"`,
  its STooltip moves into `#value`, its `compact` maps to `compact`). The admin value moves from
  a `2rem` literal to `--font-size-2xl`; that visible change is intended (§10, R-5).
- `frontend/src/shared/ui/SSegmentedControl.vue`: `v-model`, `options: {value, label, disabled?, title?, testId?}[]`,
  `ariaLabel`, `size`. Markup follows the one tested instance,
  `slices/activities/components/PayloadSchemaField.vue:102-122`: a `role="group"` container
  with `aria-label`, buttons with `aria-pressed`, outer radii only, the selected button in
  `--color-accent`. Absorbs that instance (its test at `PayloadSchemaField.test.ts:37` must keep
  passing unchanged), `UsageDashboard.vue:108-122`, and the dashboard's `TimeWindowSelector.vue`,
  which is deleted.
- `frontend/src/shared/composables/useChartTheme.ts`: returns reactive tick, grid and legend
  colours read from tokens (generalising `OutputChart.vue:73-111`, theme-reactive via `useTheme`)
  and `seriesColor(key: string): string`, which hashes the key (FNV-1a over UTF-16 code units)
  onto `--color-chart-1..8`. Stable across refetches, windows, reloads and room order.
- `frontend/src/shared/utils/number.ts`: `formatNumber(value, locale)` (`Intl.NumberFormat`,
  compact notation at 10 000 and above) and `formatPercent(numerator, denominator, locale)`
  (whole percent; `null` when the denominator is 0). Per `docs/UI/12-shared-patterns.md:624-629`.
- `frontend/src/shared/styles/main.css`: add `--color-chart-1` to `--color-chart-8` in the light
  `@theme` block and the dark override block, in a region separate from the chat-bubble tokens.
  The set is blue/teal/indigo/violet/cyan/slate-family hues with no pure red, amber or green, so a
  series never reads as status. Validate the set with the `dataviz` skill's palette validator for
  colour-vision-deficiency separation, and add each against `--color-surface` to
  `frontend/src/shared/styles/__tests__/contrast.test.ts` at 3:1 (WCAG 1.4.11, non-text graphics)
  in both themes.
- Export the new components from `frontend/src/shared/ui/index.ts`.
- `docs/UI/01-design-system.md`: add entries for `SStatTile`, `SSegmentedControl` and the chart
  tokens; point `docs/UI/05-keys.md:1452-1500` at them instead of describing a local pattern.

### Stage B: authorization (backend plus one frontend fix)

- `backend/app/api/v1/dashboard.py`: `_resolve_workspace_rooms` keeps the workspace and
  `assert_project_role` checks, adds a 404 when the workspace's project is soft-deleted (F-18),
  and replaces `list_chatroom_ids_for_workspace` plus `get_chatrooms` with
  `ConversationFacade.visible_rooms_in_workspace(principal=..., workspace_id=...)` (F-9). Owners
  still see every room because they can open every room.
- `backend/app/api/v1/research_export.py:91`: replace `assert_project_owner` with a gate that
  admits a principal whose resolved project roles include `PROJECT_OWNER` (which covers a computed
  Org Owner, [R5.03], [R8.08]), admin bypassing, and refuses a soft-deleted project (F-8, F-18).
  Add it to `deps.py` as `assert_computed_project_owner` beside `assert_project_role`, using the
  same resolver. `assert_project_owner` is unchanged for its other callers.
- `backend/app/api/ws/project.py:37-47`: use the same resolver rule as `assert_project_role` at
  connect and re-check, refuse a soft-deleted project, and close a refused or revoked socket with
  a dedicated close code (F-17). Re-check project access on every 30-second tick rather than every
  second tick (F-23); the room-level cadence in `connection.py` for chat sockets is unchanged.
- `frontend/src/slices/dashboard/composables/useDashboardSocket.ts`: register a close-code
  handler that stops reconnecting on the refusal code (F-17), and expose the resolved
  `projectId` (`:18-28`) so the view gates export on the viewed workspace's project (F-7).

### Stage C: round semantics, watchlist and names (backend, API contract)

- `contexts/activities`:
  - `ActivationRepository.current_for_rooms(chatroom_ids) -> dict[UUID, Activation]`: the active
    activation per room, else the most recent ended one (one query with `DISTINCT ON
    (chatroom_id)` ordered by active first, then `started_at DESC`). Facade wrapper
    `current_activations_for_rooms`.
  - `SubmissionRepository.round_aggregates(activation_ids)`: per activation, total, valid and
    `max(created_at) FILTER (WHERE is_valid IS TRUE)`. Replaces `aggregate_for_rooms` for the
    dashboard; `aggregate_for_rooms` is deleted if it has no other caller.
  - `SubmissionRepository.round_subject_counts(activation_ids)`: per (activation, subject), valid
    count and last valid time, grouped by `subject_user_id` and `subject_kind`, or by
    `subject_member_group_id` for group rounds (Q-11). Replaces `participant_submission_counts`
    for the dashboard.
  - `AggregationService.watchlist_for_rounds(...)`: merges the roster (below) with
    `round_subject_counts`, flags `no_valid_in_round` or `idle` (no valid submission in the last
    `WATCHLIST_IDLE_MINUTES = 10`), and returns every present participant, flagged first. The
    median rule and its tests are removed.
  - `backend/app/workers/tasks/activities.py:162,182`: emit `is_valid` from the verdict, not from
    `result_status`; the watchdog sweep (`:188-239`) emits `dashboard.submission.validated` with
    `is_valid=false` for each swept submission (F-22).
- `contexts/conversation`, all behind new `ConversationFacade` methods:
  - `round_roster(rounds: Sequence[(chatroom_id, since)]) -> dict[chatroom_id, set[(principal_id, kind)]]`:
    distinct human message senders since `since` (new `IN`/`GROUP BY` query on the
    `ix_messages_chatroom_created` index), guest sessions with `last_seen_at >= since` (index
    `ix_guest_sessions_chatroom_last_seen`, `alembic/versions/0085:79`), and current presence
    (pipelined batch read reusing `_reconcile_members`, `presence.py:318`, so ghosts are excluded).
  - `guest_labels(session_ids) -> dict[UUID, str]`: batch lookup in `guest_session_repo.py`.
  - The API route merges the roster with activity-session subjects and excludes principals whose
    resolved roles in the project include `PROJECT_OWNER` (Q-6).
- `backend/app/api/v1/dashboard.py` contract (`gen:api` rerun required):
  - `RoomSummaryOut`: `room_id`, `room_name`, `round` (`{activation_id, state: active|ended,
    started_at, ended_at}` or `null`), `total_submissions`, `valid_count` (both for the current
    round, 0 without one), `last_valid_submission_at`, `status: green|yellow|red|inactive`.
  - `DashboardSummaryOut`: `rooms`, `thresholds` (`{green_minutes: 5, yellow_minutes: 15,
    idle_minutes: 10}`) so labels and alert text read the real numbers, `generated_at`.
  - `WatchlistEntryOut`: `room_id`, `kind: user|guest|group`, `display_name` (nullable when an
    account has none; the client then shows the subject code), `subject_code` (kept for
    cross-reference with the research export), `valid_count`, `last_valid_submission_at`,
    `needs_attention`, `reason: no_valid_in_round|idle|null`. Capped at 500 rows, flagged first.
  - `_ALERT_MINUTES` becomes `_WATCHLIST_IDLE_MINUTES` in the activities context, its only reader.
  - Timeseries is unchanged.
- Locales: the alert text reads its minutes from `thresholds.yellow_minutes`.

### Stage D: dashboard page (frontend)

- `DashboardView.vue`: rebuilt to the §5 layout. `SPageHeader` gets the workspace name as its
  subtitle and a live indicator (connected dot plus `SRelativeTime` of the last successful
  summary fetch) beside Export.
- KPI strip (four `SStatTile`): rooms with an active round over all rooms; valid submissions this
  round with `formatPercent` validity as hint; stalled rooms (`tone="danger"` when above 0);
  participants needing attention (`tone="warning"` when above 0).
- Needs-attention area: one `SAlert variant="danger"` listing stalled rooms with a link to each;
  when nothing needs attention, one muted success line instead of a heading and empty state.
  `DashboardAlerts.vue` is deleted.
- `RoomStatusCard.vue`: a 3 px left rail in the status colour (`inactive` uses
  `--color-border-strong`), a heroicon per status (`CheckCircleIcon` green, `PauseCircleIcon`
  yellow, `ExclamationTriangleIcon` red, `MinusCircleIcon` inactive) with the status label, so
  status never depends on colour alone; `valid / total` with an `SProgressBar` of the ratio;
  `SRelativeTime` for the last valid submission; the whole card a `RouterLink` to
  `{ name: 'conversation.chatroom', params: { chatroomId: room_id } }`
  (`frontend/src/slices/conversation/routes.ts:23-27`, by name, no slice import). Sorted red,
  yellow, green, inactive, then by name.
- `StudentWatchlist.vue`: `STable` with columns name (display name, else subject code in mono),
  room, valid count, last valid (`SRelativeTime`), reason (`SBadge` warning with the reason text;
  no badge for unflagged rows). The always-true attention column is removed. A room filter
  (`SSelect`) applies to both tables. The right column shows flagged rows only; the full table
  below shows every present participant.
- `OutputChart.vue`: Title plugin removed and unregistered; legend at the bottom with point
  style; `pointRadius: 0`, hover radius 3; series colour from `seriesColor(room_id)`; one series
  per visible room, including rooms with no data in the window (a flat zero line); x axis built
  from the full window at the chosen bucket and zero-filled (F-19); labels through
  `Intl.DateTimeFormat` with the vue-i18n locale, adding the date when the window is 24h.
- Window selection uses `SSegmentedControl`; bucket follows the window: 1h to 5m, 6h to 15m,
  24h to 1h.
- Queries: summary and watchlist set `refetchInterval: 30_000` (Q-10). `useDashboardSocket`
  reads the flat `room_id` (F-16); an event for a room not in the current set refetches the
  summary (a room created after page load), and a known room's event invalidates summary,
  watchlist and the current timeseries key. On reconnect (`onDegraded` to healthy,
  `ws-manager.ts:122`) it invalidates all dashboard queries (F-21).
- Error states: each panel reads its own query's `error` and renders `SQueryError` with retry;
  the summary error no longer also renders the "no rooms" empty state (F-20).
- Skeletons mirror the final layout (KPI row, two columns, chart) per
  `docs/UI/12-shared-patterns.md:335-350`.
- Locales `en.json` and `zh-TW.json`: new keys for statuses, reasons, KPI labels, live
  indicator; unused keys (`summary`, `submissions`, `validSubmissions`, `bucketSize.*`) removed.

### Stage E: per-room presence, participation and message split

- `ConversationFacade.room_online_counts(chatroom_ids) -> dict[UUID, int | None]`: the pipelined
  presence read from Stage C, `None` for a room when Redis is unavailable (never reported as 0).
- `ConversationFacade.message_counts_by_sender(rounds) -> dict[UUID, {human, agent}]`: one
  `GROUP BY chatroom_id, sender_type` query since each round's start, `deleted_at IS NULL`;
  `system` is excluded.
- `RoomSummaryOut` gains `online_count: int | null`, `participant_count: int` (the size of the
  Stage C roster for the current round), and `message_counts: {human, agent}` for the current
  round.
- `RoomStatusCard.vue` shows online count (`UserGroupIcon` and "n online", or a dash when
  `null`), participant count, and a two-segment bar of human versus agent messages
  with its numbers as text.

**Migration required: no.** Every new query is served by an existing index named above.

## 7. NFR Checklist

- [ ] i18n: every new string through `$t()` in `en` and `zh-TW`; status and reason labels are
  keys, not server text. The gate `vue/no-bare-strings-in-template` (`eslint.config.js:280-296`)
  covers `aria-label` and `title`.
- [ ] Audit log: N/A. All changes are reads. The research export's existing
  `research_data.exported` audit row is unchanged.
- [ ] Tenant isolation: every dashboard read gates on project role and [R13.32] room visibility;
  the WebSocket uses the same rule; the export gates on computed project ownership. Pinned by
  AC-7 to AC-11.
- [ ] Error handling UX: per-panel loading, error (with retry) and empty states (AC-20); Redis
  unavailability is a dash, not 0 (AC-25).
- [ ] Performance: a classroom workspace is about 2 to 10 rooms and 20 to 30 participants per
  room. Summary is about 6 queries plus one Redis pipeline, independent of room count; the
  watchlist about 5 queries. At a 30-second refetch per open dashboard that is well under one
  query per second per teacher. The roster read is bounded by the round window, the watchlist
  response by 500 rows. No N+1: every per-room read is a batch.

## 8. Security Considerations

This touches tenant boundaries, a WebSocket gate and personal data.

- **Names in the live view (Q-4, Q-5).** Names leave the server only in the watchlist REST
  response, only for rooms the caller can open (Stage B's [R13.32] filter runs before any name is
  resolved), and only to holders of a project role. They never travel on `ws:project`
  ([R33.03], unchanged; AC-13), never enter logs, and never enter the research export (AC-14).
  Email is never returned (`IdentityFacade.get_display_names` returns names only,
  `identity/infrastructure/repositories.py:122`). The recorded consequence of Q-5, that a student
  holding a project role sees classmates' names and attention flags for rooms they can open, is
  an accepted product decision, restated in [R33.02] (§13) so it is not mistaken for a leak later.
- **Room enumeration (F-9).** The visibility filter is applied once, in `_resolve_workspace_rooms`,
  so all three reads inherit it; route tests pin a hidden room's absence from each (AC-7).
- **Soft-deleted projects (F-18).** Dashboard, export and socket all refuse; tests pin each (AC-9).
- **WebSocket (F-17, F-23).** Connect and re-check use the REST rule; a revoked caller is closed
  within one 30-second tick (AC-10). The refusal close code stops the reconnect loop, so a refused
  client stops hammering the ticket endpoint.
- **Export gate (F-8).** Widening to computed owners is the intent of [R33.10] and [R8.08]; it
  admits no role the chat export does not already admit (audit F-8 evidence).
- **Input.** New query parameters are none; the time window stays an enum.

## 9. Quality Notes

### 9.1 Existing debt

- `ResearchExportButton.vue` uses a raw `<button>` (`:100-113`), a hand-rolled Teleport modal
  with a `div role=button` backdrop (`:115-244`), raw date inputs and a hand-rolled spinner.
  Do not imitate it; do not restyle it here (FU-1 owns it with F-5 and F-6).
- `GET /chatrooms/{id}/members` imports `application.access` from the route layer
  (`backend/app/api/v1/chatrooms.py:18-23`). Do not copy that: the roster composition here goes
  through facade methods only. Recorded as FU-4.
- `OutputChart.vue`'s `rgb()` literals pass `literal-colours.test.ts` only because that test
  checks hex (`:35`). Stage A removes the literals; widening the test is FU-5.
- `tests/unit/test_dashboard_aggregation.py:53-66` tests an impossible input. It is deleted with
  the median rule, and the replacement behavior gets a `db`-tier test.
- `DashboardView.test.ts` mocks a stale `dashboardKeys` (`:12-15`). Rewritten in Stage D.

### 9.2 Patterns to follow

- Routes call facades only (`backend/CLAUDE.md`); `dashboard.py` already does and must keep doing.
- Any PostgreSQL-specific SQL (`DISTINCT ON`, `FILTER`, `floor(extract(epoch ...))`) gets a
  `pytest.mark.db` test (`backend/CLAUDE.md`, the `MessageRepository.search` lesson).
- Inline gates in the `deps.py` style: `assert_project_role` (`:117-133`) is the exemplar for the
  new `assert_computed_project_owner`.
- Shared components consume tokens only ([R24.28]); exemplars `SBadge.vue:82-101`,
  `SCard.vue:89-117`.
- Cross-slice: the dashboard slice may import only `activities` and `tenancy`
  (`eslint.config.js:36`); link to rooms by route name.
- Server state through TanStack Query composables in `slices/dashboard/composables/`; no raw
  transport.

### 9.3 Reuse inventory

Backend: `ConversationFacade.visible_rooms_in_workspace` (`facade.py:128-141`),
`room_presence_snapshot` and `_reconcile_members` (`presence.py:318`),
`IdentityFacade.get_display_names` (`identity/interfaces/facade.py:71`),
`ActivitiesFacade.resolve_member_group_names` (`facade.py:927-942`), `subject_code` and
`group_subject_code` (`subject_code.py:30-43`), `holds_project_role` and the role resolver
(`shared_kernel/auth/permissions.py:399-404`, `deps.py:100-114`), `project_channel` and the
existing emitters (`broadcast.py:252-300`).

Frontend: `SCard` (`#header` slot, `padding`), `SBadge`, `SAlert`, `STable` (card list on
mobile, `row-click`), `SProgressBar`, `SRelativeTime` with `useNow`, `SEmptyState`, `SSkeleton`,
`SQueryError`, `SSelect`, `SPageHeader`, `STooltip`, `useTheme`, `useBreakpoint`,
`formatRelativeTime` (`shared/utils/datetime.ts:32-52`), `dashboardKeys`
(`slices/dashboard/queries/index.ts:1-7`), `ws-manager` `closeCodeHandlers` and `onDegraded`
(`ws-manager.ts:122,227`).

## 10. Risks and Rollback

- **R-1. Names to students (Q-5).** Accepted by the requester. If it is reversed later, the
  change is one branch in the watchlist route returning `display_name: null` for non-owners; the
  client already falls back to the code.
- **R-2. Response shape change.** `RoomSummaryOut` and `WatchlistEntryOut` change incompatibly.
  The only consumer is this slice, regenerated in the same series; Stage C and Stage D must
  deploy together (backend first is safe only if Stage D follows in the same release).
- **R-3. Roster misses.** A student who never enters the room, or who was online earlier but
  neither posted nor stayed, is not on the watchlist (Q-6). The KPI and table label the
  population as "present in this round" so the teacher does not read it as the class list.
- **R-4. Refetch load.** 30-second polling multiplies with open dashboards. Mitigated by the
  query counts in §7; if it shows in metrics, the interval is one constant.
- **R-5. Admin and keys visuals.** Absorbing their tiles changes admin tile value size from a
  `2rem` literal to `--font-size-2xl`. Checked in the visual pass (AC-26).
- **R-6. Export gate widening.** Org Owners gain a working export button they already saw; no
  other role changes.
- **Rollback.** No migration. Each stage is a separate commit and reverts on its own, except
  Stage C and Stage D, which revert together because of R-2.

## 11. Acceptance Criteria

Stage A

- [x] AC-1: `SStatTile`, `SSegmentedControl`, `useChartTheme` and `formatNumber`/`formatPercent`
  exist with component or unit tests; `seriesColor` returns the same token for the same key
  across calls and different tokens for at least 7 of 8 distinct sample room ids.
- [x] AC-2: `AdminHomeView`, `AdminMetricsView` and `UsageDashboard` render their tiles through
  `SStatTile`; `UsageDashboard` and `PayloadSchemaField` render their window and option pickers
  through `SSegmentedControl`; `PayloadSchemaField.test.ts` passes unmodified.
- [x] AC-3: `--color-chart-1..8` exist in both theme blocks; `contrast.test.ts` asserts each at
  3:1 against `--color-surface` in both themes; the palette validator's CVD check passes and its
  output is recorded in §15 or the commit message.

Stage B

- [ ] AC-4: A project member who cannot open room B (owners-only) receives no trace of B from
  the summary; an owner receives B (route test against the real gate, not a mock).
- [ ] AC-5: An Org Owner without an explicit project row can request a research export (200) and
  sees the button; a Project Member gets 403 and no button.
- [x] AC-6: With the sidebar store on project P1 and the page on a P2 workspace, the export
  button follows the caller's role in P2 (component test).
- [ ] AC-7: The same caller as AC-4 receives no bucket and no watchlist row for room B from the
  timeseries and watchlist reads.
- [x] AC-8: The project socket admits an Org Owner without an explicit row and refuses a bare
  Org Member; a refused client stops reconnecting.
- [x] AC-9: After soft-deleting the project, dashboard reads return 404, the export returns 404
  and the project socket refuses.
- [x] AC-10: A member removed from the project is disconnected from `ws:project` within 35 seconds.

Stage C

- [ ] AC-11: Status: no activation, or only ended ones, gives `inactive`; an active round started
  2 minutes ago with no submission gives `green`; last valid 10 minutes ago gives `yellow`; last
  valid 20 minutes ago gives `red`; invalid submissions do not refresh the status (unit tests on
  the status function and a route test).
- [ ] AC-12: Summary counts cover only the current round: a submission in an earlier ended round
  is not counted once a new round is active (db test).
- [ ] AC-13: No `ws:project` frame contains a display name (broadcast test asserting frame keys).
- [ ] AC-14: The research export output is byte-identical in its subject columns before and after
  this change for the same fixture (builder test).
- [ ] AC-15: Watchlist: in a round with five present students where one has zero submissions,
  one only invalid ones, one a valid submission 12 minutes ago and two a valid submission in the
  last minute, the first three are flagged with reasons `no_valid_in_round`,
  `no_valid_in_round` and `idle`, the last two are listed unflagged, and all carry display names
  (db test exercising the real roster queries).
- [ ] AC-16: A project owner who posts in the room is not on the watchlist; a guest session seen
  since the round start appears with its guest label.
- [ ] AC-17: In a group round, watchlist rows are groups with member-group names.
- [ ] AC-18: The worker emits `is_valid=false` for an invalid verdict, and a watchdog sweep emits
  a dashboard event per swept submission.

Stage D

- [ ] AC-19: With no websocket event, a room crosses from green to yellow on screen within 30
  seconds of crossing the threshold (component test with fake timers and a mocked query).
- [ ] AC-20: A failed watchlist query shows an error with retry, not the empty state; a failed
  summary shows an error and not the "no rooms" state; a failed timeseries shows an error in the
  chart card.
- [ ] AC-21: Room cards are ordered red, yellow, green, inactive; each shows its status icon and
  label; each links to its chatroom.
- [ ] AC-22: The chart has no Title plugin, uses a bucket of 5m, 15m or 1h for 1h, 6h or 24h,
  shows a continuous zero-filled axis across the whole window, shows one series per visible room,
  and gives a room the same colour after a window change and a reload.
- [ ] AC-23: An event for a room in the current set invalidates the dashboard queries; an event
  for an unknown room refetches the summary; a reconnect invalidates all dashboard queries.
- [ ] AC-24: With no stalled room, no alert element is rendered and a single success line is.

Stage E

- [ ] AC-25: Room cards show online count, participant count and the human/agent message split
  for the current round; with Redis unavailable, the online count renders a dash, not 0.

Whole change

- [ ] AC-26: Visual pass on a running stack (the `frontend:verify` skill) at 1280 px and 390 px,
  light and dark, `en` and `zh-TW`, with at least one red, one yellow, one green and one inactive
  room; screenshots attached to the PR or recorded in §15. Admin home, admin metrics and the keys
  usage dashboard are included for R-5.
- [ ] AC-27: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`, `check:bundle-size`
  (DashboardView stays under 200 KB gzip), `check:openapi-drift`, `ruff check`, `ruff format
  --check`, `mypy` and `pytest -q` including the `db` tier pass on CI.

## 12. Test Plan

- Unit (backend, `tests/unit/`): status function table (AC-11); watchlist flagging from given
  roster and counts (AC-15 logic); worker `is_valid` mapping and watchdog emission (AC-18);
  broadcast frame keys (AC-13); `assert_computed_project_owner` (AC-5).
- `db` tier (backend, `tests/integration/`, `pytest.mark.db`): `current_for_rooms`,
  `round_aggregates`, `round_subject_counts`, `round_roster`'s message and guest parts,
  `message_counts_by_sender`, `guest_labels` (AC-12, AC-15, AC-16, AC-17, AC-25).
- Route tests with the real gates (`tests/integration/`): room visibility for all three reads
  (AC-4, AC-7), export gate (AC-5), soft-deleted project (AC-9).
- WebSocket tests (`tests/integration/`): project socket admit, refuse and revoke (AC-8, AC-10).
- Builder test: export subject columns unchanged (AC-14).
- Frontend component tests (`slices/dashboard/__tests__/`, `shared/ui/__tests__/`): every new
  component; `DashboardView` panel states and ordering (AC-19 to AC-24); export gating (AC-6);
  socket composable flat payload, unknown room and reconnect (AC-23).
- Manual: AC-26 through the `frontend:verify` skill; AC-3's validator run.
- CI: AC-27. Per the project convention, full suites are judged on remote CI, not the local host.

## 13. SRS Delta

Amend [R33.02] (replace the entry):

- **[R33.02]** A dashboard view presents project members with the status of each room in a
  workspace that they can open ([R13.32]), the current round's counts, time-series output charts,
  a watchlist of participants needing attention, and threshold alerts. The watchlist names
  participants by display name (never by login email) to every caller the dashboard admits,
  including participants who hold a project role; the research export ([R33.10]) and the project
  WebSocket ([R33.03]) never carry names. The view is lazy-loaded and does not affect the initial
  bundle budget. The entry point is the workspace card in the workspace list, not a global
  sidebar link.

Add:

- **[R33.05]** A room's current round is its active activation, else its most recent ended
  activation, else none. Dashboard counts cover the current round only. With an active round, a
  room's status is green when the later of the round start and its last valid submission is at
  most 5 minutes old, yellow at most 15, and red beyond; red raises a stall alert. A room without
  an active round has an inactive status and raises no alert. Invalid, pending and errored
  submissions do not affect status.
- **[R33.06]** The watchlist population is the current round's present participants: subjects
  holding a session in the round, human message senders in the room since the round started,
  guest sessions seen since the round started, and principals online in the room, excluding
  Project Owners. A participant needs attention when they have no valid submission in the round,
  or none in the last 10 minutes while the round is active. In a group round the watchlist lists
  member groups. Participants with zero submissions are included.
- **[R33.07]** Dashboard reads, the project WebSocket and the research export refuse a
  soft-deleted project ([R8.11]). The project WebSocket admits exactly the callers the dashboard
  REST reads admit and re-checks on every auth watchdog tick.

Amend [R33.10]'s first sentence to: "Project Owners, including Org Owners as computed Project
Owners ([R8.08]), can export de-identified research data for a workspace." The rest of [R33.10]
is unchanged.

Run `python scripts/traceability.py` after applying, so `docs/traceability.csv` gains the
[R33.05] to [R33.07] rows.

## 14. Open Questions

- OQ-1. Should `participant_count` (Stage E) and the KPI "need attention" count be phrased as a
  ratio ("8 of 12 present")? The SRS forbids participation rates only for observer blocks
  ([R28.18]); this dossier shows counts side by side and leaves a ratio to the visual pass.
- OQ-2. The exclusion (Q-6) removes Project Owners only. A teaching assistant who is a Project
  Member and posts in the room appears on the watchlist. Acceptable for the pilot; revisit if
  teaching assistants are routinely listed.

## 15. Deviation Log

- Stage A palette check: chart tokens measure at least 3:1 against `--color-surface` in both
  themes. Machado full-severity simulations report minimum pairwise CIE76 distances of 21.2,
  21.2, and 21.3 for protanopia, deuteranopia, and tritanopia in light mode, and 16.7, 16.5,
  and 15.9 in dark mode.
- Stage A used a repeatable Machado/CIE76 palette check in `contrast.test.ts`; the listed
  `dataviz` skill was unavailable in this environment. The component exposes `accessibleLabel`
  (`accessible-label` in templates) and renders it as `aria-label`, since Vue's type checker did
  not resolve the spec's `ariaLabel` prop through the `aria-label` attribute spelling.
- Stage B's database-backed route tests were added but cannot run on this host: the configured
  DSN uses `postgres:5432`, which resolves inside the Compose test network but not on the local
  Windows host. The unit suites, lint and frontend typecheck pass; the `db` tests remain for CI.
- Stage B also stops dashboard socket retries for 4404 (deleted project) as well as 4403
  (authorization refusal), since neither condition is repaired by reconnecting.

## 16. Follow-ups

- FU-1. Spec the research export's remaining audit findings (F-5, F-6, F-10, F-12, F-13, F-14,
  F-15) as the planned `research-export-delivery` and `research-export-data-shape` dossiers,
  including `ResearchExportButton.vue`'s hand-rolled modal. `2026-10-11-browser-file-delivery`
  already changed the delivery path; re-verify F-5 and F-6 against it first.
- FU-2. If a hidden marker for deliberate peer errors lands (`peer-deliberate-errors` FU-1), a
  "caught errors" figure per room can join the KPI strip.
- FU-3. Decide the sidebar Dashboard link against [R33.02] (audit FU-3).
- FU-4. Move `GET /chatrooms/{id}/members`'s direct `application.access` import behind the facade.
- FU-5. Extend `literal-colours.test.ts` to catch `rgb()`/`hsl()` literals.
