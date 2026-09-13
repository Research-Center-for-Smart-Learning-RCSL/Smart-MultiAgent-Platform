---
type: feature
status: implemented
created: 2026-09-13
requirements: [R30.10]
depends_on: []
---

# Teacher Dashboard

## 1. Summary

A cross-room activity monitoring dashboard for facilitators (teachers), showing
real-time status of all chatrooms in a project that have active or recent
structured activities. Includes time-series output charts (Chart.js), a student
watchlist flagging low participation, threshold-based alerts, and live updates
via a new `ws:project:{id}` WebSocket channel. Serves the NSTC undergraduate
proposal's requirement for a teacher control panel with status indicators,
output trends, and a student attention list. Lives in a new `dashboard`
frontend slice; backend extends the existing activities aggregation service
(`aggregation_service.py:3-4` explicitly names "dashboards" as a future
consumer) with project-scoped queries.

## 2. Goals and Non-goals

**Goals**
- Cross-room view: a single page showing activity status across all chatrooms
  in a project where the current user is room creator (facilitator).
- Per-room status cards with traffic-light indicators (active/idle/stalled)
  derived from submission recency and rate.
- Time-series chart of valid submissions per time window (1h/6h/24h) using
  Chart.js + vue-chartjs, grouped by chatroom.
- Student watchlist: participants with zero or below-median submissions across
  an activation, surfaced as "needs attention."
- Threshold alerts: configurable alert when a room's output rate drops below a
  threshold (e.g., no valid submission in T minutes).
- Real-time updates via a new `ws:project:{project_id}` WebSocket channel,
  pushing submission events and status transitions.

**Non-goals**
- Creativity four-dimension scoring logic (separate task, domain-specific).
- Design Agent parameter-to-prompt generation UI.
- Chinese-character creativity task content (ActivityType definitions, plugins,
  validators).
- Observer/AA report display within the dashboard (teachers use
  `ObserverPanel.vue` in each chatroom for that).
- Mobile-optimized dashboard layout (desktop-first; responsive down to tablet
  but not phone-primary).
- Cross-project aggregation (dashboard is always scoped to one project).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | MVP scope: minimal (stat cards only), medium (+ sparklines + observer summary), or full (+ time-series charts + alerts + real-time WS)? | Full scope | User selected complete range with charts, threshold alerts, and WebSocket real-time updates. The NSTC proposal describes status indicators, output trend charts, and a student watchlist as core dashboard elements. |
| Q-2 | Frontend placement: inside `conversation` slice (no SLICE_DEPS change, widest existing deps) or new `dashboard` slice (cleanest separation, higher ceremony)? | New `dashboard` slice | User chose the cleaner separation despite the ceremony of adding a SLICE_DEPS entry. The dashboard is a distinct concern from conversation. |
| Q-3 | Charting library: none (progress bars only), Chart.js + vue-chartjs (~60KB gzip), or hand-drawn SVG? | Chart.js + vue-chartjs | User chose Chart.js for time-series line charts and bar charts. Lightweight, well-maintained, covers the needed chart types. |
| Q-4 | Real-time strategy: reuse existing `ws:user:{id}` channel, polling, or new `ws:project:{id}` channel? | New `ws:project:{id}` channel | User chose the most complete option. A project-level channel lets any project member with dashboard access subscribe to cross-room activity events without per-room subscriptions. |
| Q-5 | File overlap with active dossiers? `2026-07-19-large-artifacts-silently-dropped` (in-progress) touches `turn_engine.py`; `2026-07-07-graphrag-two-axis-redesign` (approved) is a blueprint for GraphRAG. | No overlap | This dossier touches `aggregation_service.py`, `facade.py` (activities), `pubsub.py` (shared_kernel), `eslint.config.js`, `router.ts`, and creates new files. None of these overlap with either active dossier's touched files. |

## 4. Current State

### 4.1 Backend -- Aggregation service

`aggregation_service.py:1-5` docstring explicitly names "dashboards" as a future
consumer. Three public methods (`list_submissions:28`, `aggregate:45`,
`list_recent_activity:56`), all room-scoped -- every method requires
`chatroom_id` as a mandatory parameter. No project-scoped aggregation exists.

The facade (`facade.py:879-964`) exposes six aggregation methods, all room-scoped:
`list_submissions:879`, `aggregate:896`, `list_recent_activity:924`,
`field_coverage:941`, `mandala_grid:948`, `attempt_summary:955`.

### 4.2 Backend -- Submission table schema

`activity_submissions` (`tables.py:143-189`) has `chatroom_id` as a direct
NOT NULL FK with a compound index `ix_activity_submissions_room_created` on
`[chatroom_id, created_at]` (`alembic/versions/0049_activities.py:166-168`).
Cross-room queries are structurally simple: `WHERE chatroom_id IN (...)`
leverages the existing index. No schema change required.

`producer_user_id` (UUID FK to users) and `sub_scores` (JSONB) are also on the
table, enabling per-participant and per-dimension breakdowns without joins to
sessions.

### 4.3 Backend -- WebSocket channels

Five channel namespaces (`pubsub.py:9-17`). Each is owned by its context's
`infrastructure/channels.py` as a builder function (e.g.,
`room_channel:conversation/infrastructure/channels.py:12`). No central registry
-- adding a new namespace means adding a new builder function in the owning
context.

`dispatch_activation_progress` (`broadcast.py:132-168`) already sends to
`ws:user:{started_by_user_id}` with cross-room scope (facilitator receives
progress from all rooms they started activities in). A project-level channel
would complement this with submission-level granularity.

### 4.4 Frontend -- Existing patterns

`UsageDashboard.vue` (keys slice) provides the reference pattern: time-window
selector (4 buttons via `SButton`), stat cards (flexbox with `STooltip`),
progress bars (`SProgressBar` with variant thresholds), threshold alerts
(`SAlert variant="warning"` at >=80%). No TanStack Query -- uses direct async
fetch with `ref` + `watch`.

No charting library is installed in `package.json`.

### 4.5 Frontend -- Slice dependencies

`SLICE_DEPS` (`eslint.config.js:20-45`): 13 slices. `conversation` has the
widest deps (8 slices). A new `dashboard` slice would need
`['activities', 'tenancy']` at minimum -- `activities` for activity data,
`tenancy` for project/member context. It does not need to import from
`conversation` directly if the backend provides room names in its aggregation
response.

### 4.6 Frontend -- Router

Routes are per-slice arrays spread in `router.ts:25-52`. A new slice adds its
routes via the same pattern: export from `dashboard/index.ts`, import and spread
at `router.ts`.

### 4.7 Existing requirements

[R30.10] (`REQUIREMENTS.md:2271`) defines the aggregation read model and
explicitly names "downstream dashboards" as a consumer. [R28.17]-[R28.18]
define observer presentation blocks (room-scoped). No existing requirement
covers a teacher dashboard UI or project-level WebSocket channel. SRS Delta
needed.

## 5. Design

### Options considered

**Option A -- Extend activities facade only (room-loop on client)**:
Frontend calls existing per-room aggregation endpoints in a loop for each room.
No backend change. Trade-offs: N+1 API calls (one per room), no atomic
cross-room snapshot, pagination complexity on the client, cannot efficiently
compute cross-room watchlist.

**Option B -- New project-scoped aggregation methods in activities facade**:
Backend adds `aggregate_for_project`, `timeseries_for_project`,
`watchlist_for_project` to `AggregationService` and `ActivitiesFacade`. Single
API call returns cross-room data. Frontend consumes via new endpoints.
Trade-offs: requires new backend methods and API endpoints, but all queries
leverage the existing `ix_activity_submissions_room_created` index. The
`aggregation_service.py` docstring already anticipates this extension.

**Option C -- New `dashboard` bounded context**:
Separate DDD context with its own tables and read models. Trade-offs:
over-engineering for what is fundamentally a read view over existing activity
data. Introduces cross-context coupling without a distinct domain model.

### Decision

**Option B.** The dashboard is a read view over existing activity data, not a
new domain. Extending the activities aggregation service is the natural home
(the docstring says so). The project-scoped methods parallel the existing
room-scoped ones, use the same indexes, and the facade's public API grows
additively.

## 6. Detailed Changes

### Backend

**`contexts/activities/application/aggregation_service.py`** -- new methods:

- `aggregate_for_project(*, project_id, activity_type_id=None)` -- returns a
  list of `RoomAggregate` (room_id, room_name, aggregate, last_submission_at,
  activation_status). Uses `chatroom_id IN (SELECT id FROM chatrooms WHERE
  workspace_id IN (SELECT id FROM workspaces WHERE project_id = ...))` with
  the existing `ix_activity_submissions_room_created` index.
- `timeseries_for_project(*, project_id, window, bucket_minutes)` -- returns
  time-bucketed submission counts per room. Uses `date_trunc` + `GROUP BY`.
- `watchlist_for_project(*, project_id, activation_id=None)` -- returns
  participants below a threshold (zero submissions, or below median), with
  subject codes (not names).

**`contexts/activities/interfaces/facade.py`** -- expose the three new methods.

**`app/api/v1/activities.py`** -- new endpoints:

- `GET /api/v1/projects/{project_id}/dashboard/summary` -- cross-room aggregate
- `GET /api/v1/projects/{project_id}/dashboard/timeseries?window=1h&bucket=5m`
- `GET /api/v1/projects/{project_id}/dashboard/watchlist`

All three require project membership (existing `Depends(current_project_member)`).

**`contexts/activities/infrastructure/channels.py`** (new file) -- project
channel builder:

- `project_channel(project_id: UUID) -> str` returning `f"ws:project:{project_id}"`

**`app/api/ws/project.py`** (new file) -- project-level WebSocket endpoint:

- `GET /api/ws/project/{project_id}` -- authenticates via existing WS auth,
  verifies project membership, subscribes to `ws:project:{project_id}`.
  Publishes: `dashboard.submission.validated` (room_id, type_key, is_valid,
  sub_scores summary), `dashboard.activation.changed` (room_id, status).

**`contexts/activities/interfaces/broadcast.py`** -- extend existing dispatch
functions to also publish to the project channel when a submission is validated
or an activation starts/ends.

**Migration**: No. All changes are read-side queries over existing tables and a
new WebSocket channel (Redis pub/sub, no persistence).

**`gen:api` rerun required**: Yes -- new response models and endpoints.

### Frontend

**New slice: `src/slices/dashboard/`**

Directory structure:
```
dashboard/
  index.ts              -- barrel: routes, types
  views/
    DashboardView.vue   -- main page
  components/
    RoomStatusCard.vue   -- per-room card with traffic light
    OutputChart.vue      -- Chart.js line chart
    StudentWatchlist.vue -- low-participation table
    DashboardAlerts.vue  -- threshold warnings
    TimeWindowSelector.vue -- 1h/6h/24h toggle (reuse UsageDashboard pattern)
  queries/
    useDashboardSummary.ts   -- useQuery for /dashboard/summary
    useDashboardTimeseries.ts -- useQuery for /dashboard/timeseries
    useDashboardWatchlist.ts -- useQuery for /dashboard/watchlist
  composables/
    useProjectSocket.ts  -- WebSocket subscription to ws:project:{id}
  types/
    index.ts             -- response types (generated via gen:api)
```

**`eslint.config.js`** -- add to `SLICES` and `SLICE_DEPS`:
```javascript
dashboard: ['activities', 'tenancy'],
```

**`router.ts`** -- import and spread `dashboardRoutes`. Route:
`/orgs/:orgId/projects/:projectId/dashboard`.

**`package.json`** -- add `chart.js` and `vue-chartjs` as dependencies.

**i18n** -- new namespace `dashboard` in `en.json` and `zh-TW.json`.

### Deploy/config

No env vars, Vault paths, or compose changes. The project-level WebSocket
channel uses the same Redis pub/sub infrastructure as existing channels.

## 7. NFR Checklist

- [x] i18n -- all user-facing strings through `$t()`, new `dashboard` namespace
- [x] Audit log -- no write operations; read-only dashboard. No audit events
      needed.
- [x] Tenant isolation -- all three endpoints gate on `current_project_member`
      (existing `Depends`). WebSocket endpoint verifies project membership
      before subscribing. Aggregation queries filter by project_id, never
      return data from other projects.
- [x] Error handling UX -- loading state via `SSkeleton`, error state via
      `SAlert variant="danger"`, empty state via `SEmptyState` when no rooms
      have activities.
- [x] Performance -- cross-room queries hit `ix_activity_submissions_room_created`.
      Timeseries uses `date_trunc` aggregation, not row-level fetch. Watchlist
      computes at query time (no materialized view needed at MVP scale of
      20-30 students across 5-7 rooms). Pagination on watchlist if > 50 rows.

## 8. Security Considerations

**WebSocket authZ for project channel.** The new `ws:project:{project_id}`
endpoint must:
1. Authenticate the WebSocket connection using existing `ws_auth.py` (token
   refresh, principal extraction).
2. Verify project membership before subscribing (reuse
   `_assert_project_member` pattern from `orchestration.py:52-65`).
3. Re-check membership on the auth watchdog interval (30s, per
   `connection.py`'s existing `_auth_check_interval`).
4. Never publish participant names or submission content over the project
   channel -- only room_id, type_key, is_valid, sub_scores keys, and
   aggregate counts.

**Aggregation endpoints.** Project membership is sufficient authZ (any project
member can view the dashboard). The watchlist returns truncated participant
codes (reuse `subject_code.py`), never user IDs, names, or emails -- matching
[R28.18]'s privacy constraint.

## 9. Quality Notes

**Existing debt** in touched files:
- `aggregation_service.py` uses raw SQLAlchemy `select()` without the
  repository pattern used elsewhere in the activities context. The new
  project-scoped methods should follow the same direct-query style for
  consistency within this file, not introduce a repository layer.

**Patterns to follow:**
- Channel builder: follow `conversation/infrastructure/channels.py:12`
  (`room_channel`) for the new `project_channel`.
- WebSocket endpoint: follow `app/api/ws/chatroom.py` for auth, connection
  lifecycle, and the `connection_loop` from `shared_kernel/realtime/connection.py`.
- Broadcast dispatch: follow `broadcast.py:84-117`
  (`dispatch_activation_started`) for the pattern of post-commit, best-effort
  WebSocket publishing.
- API endpoint authZ: follow `app/api/v1/activities.py` for
  `Depends(current_project_member)`.
- Frontend queries: use TanStack Vue Query (`useQuery`) -- do NOT follow
  `UsageDashboard.vue`'s direct `ref` + `watch` pattern, which predates the
  project's adoption of TanStack Query.
- Frontend components: use `<script setup>` + Composition API.

**Reuse inventory:**
- Backend: `subject_code.py` (truncated participant codes), `Publisher`/
  `Subscriber` (pubsub.py), `connection_loop` (connection.py), `ws_auth`
  (ws_auth.py), `ActivityAggregate` / `AttemptSummaryRow` (domain models).
- Frontend: `SCard`, `SProgressBar`, `SStatusBadge`, `SAlert`, `SSkeleton`,
  `SButton`, `SEmptyState`, `SPageHeader`, `STable`, `STabs`, `SPagination`,
  `STooltip`. `useBreakpoint()` for responsive layout. `useToast()` for
  alert notifications.

## 10. Risks and Rollback

1. **Chart.js bundle size.** Chart.js + vue-chartjs adds ~60KB gzip to the
   dashboard chunk. Since the dashboard is a lazy-loaded route, this does not
   affect the initial bundle budget ([R24.28] <= 250KB gzip). Risk: low.
2. **Cross-room query performance at scale.** With 5-7 rooms and 20-30
   students (NSTC proposal scope), queries are trivially fast. At larger
   scales (100+ rooms), the `IN (...)` subquery may need a materialized view
   or a dedicated index on `(project_id, chatroom_id, created_at)` -- but
   `project_id` is not on the submissions table, so the subquery through
   `chatrooms -> workspaces` adds one join. Risk: low at MVP scale.
3. **WebSocket connection pressure.** One additional connection per dashboard
   viewer. Per-user connection cap (R19.03) applies. At MVP scale (1-2
   teachers), negligible. Risk: low.

No migration, so rollback is revert-and-redeploy.

## 11. Acceptance Criteria

- [x] AC-1: `GET /api/v1/projects/{id}/dashboard/summary` returns a list of
      room aggregates (room_id, room_name, activation_status, total
      submissions, valid count, last_submission_at) for all rooms in the
      project where the caller is room creator. Returns 403 for non-members.
- [x] AC-2: `GET /api/v1/projects/{id}/dashboard/timeseries?window=1h&bucket=5m`
      returns time-bucketed valid submission counts per room. Supports
      windows: 1h, 6h, 24h. Bucket sizes: 1m, 5m, 15m, 1h.
- [x] AC-3: `GET /api/v1/projects/{id}/dashboard/watchlist` returns participants
      with zero submissions or below-median submission count for the active
      activation, using truncated subject codes (not names/emails).
- [ ] AC-4: `ws://host/api/ws/project/{id}` authenticates, verifies project
      membership, and delivers `dashboard.submission.validated` and
      `dashboard.activation.changed` events. (Code complete; needs running
      stack for WS integration verification.)
- [ ] AC-5: WebSocket connection is refused (4403) for non-project-members.
      Membership is re-checked every 30s; a revoked member is disconnected.
      (Code complete; needs running stack.)
- [x] AC-6: `DashboardView.vue` renders at `/orgs/:orgId/projects/:projectId/dashboard`
      with room status cards, a time-series chart, a student watchlist, and
      an alerts section. All strings via `$t()`.
- [x] AC-7: Each room status card shows a traffic-light indicator: green
      (submission within last 5 minutes), yellow (5-15 minutes), red (>15
      minutes or no active activation). The thresholds are constants, not
      user-configurable at MVP.
- [x] AC-8: The time-series chart (Chart.js) renders valid submission counts
      over the selected time window. Chart text colors adapt to light/dark
      theme. Chart is responsive (fills container width, min-height 200px).
- [x] AC-9: The student watchlist table shows subject_code, submission count,
      last submission time, and a "needs attention" badge for participants
      below median. Empty state shown when all participants are above median.
- [x] AC-10: `SAlert variant="warning"` appears when any room has had no valid
      submission for > 10 minutes during an active activation.
- [ ] AC-11: WebSocket events update the dashboard in real-time without manual
      refresh. A new submission updates the relevant room card's counters and
      the chart's latest data point. (Code complete; needs running stack.)
- [x] AC-12: `eslint.config.js` includes `dashboard` in `SLICES` and
      `SLICE_DEPS` with deps `['activities', 'tenancy']`. The boundary
      enforcement gate passes (`pnpm run check:boundaries-enforced`).
- [x] AC-13: `chart.js` and `vue-chartjs` added to `package.json`. Dashboard
      chunk (lazy-loaded) does not exceed 200KB gzip per-view budget.
- [x] AC-14: `pnpm lint`, `pnpm typecheck`, and `pnpm test` pass with no
      regressions.

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-1 | Unit + integration | `tests/unit/contexts/activities/` -- mock DB for unit; `pytest.mark.db` for cross-room query correctness |
| AC-2 | Unit + integration | Same; `date_trunc` bucketing needs `pytest.mark.db` (PostgreSQL-specific) |
| AC-3 | Unit | Median computation and subject-code truncation are pure logic |
| AC-4, AC-5 | Integration | `pytest.mark.wiring` with real WebSocket connection |
| AC-6-AC-11 | Component + manual | Vitest component tests for render states; manual verification via `run` skill against dev stack |
| AC-12 | CI gate | `pnpm run check:boundaries-enforced` (existing CI job) |
| AC-13 | CI gate | `pnpm run check:bundle-size` (existing CI job) |
| AC-14 | CI | Full suite |

## 13. SRS Delta

New section in `REQUIREMENTS.md`:

```
## S33 — Teacher Dashboard

[R33.01] The activities context exposes project-scoped aggregation methods
(summary, time-series, watchlist) that query across all chatrooms in a project.
These complement the room-scoped aggregation of [R30.10].

[R33.02] A dashboard view presents facilitators with cross-room activity
status, time-series output charts, a student watchlist (truncated subject
codes, never names or emails — extending [R28.18]), and threshold alerts.
The view is lazy-loaded and does not affect the initial bundle budget.

[R33.03] A project-level WebSocket channel (`ws:project:{project_id}`)
delivers real-time activity events to authenticated project members. The
channel never carries participant names, submission content, or payload
values — only room identifiers, type keys, validity flags, and aggregate
counts.

[R33.04] The project WebSocket endpoint verifies project membership at
connection time and re-checks on the auth watchdog interval. A revoked
member is disconnected.
```

## 14. Open Questions

- OQ-1: Should the dashboard be accessible to all project members or only to
  room creators (facilitators)? The spec assumes project membership is
  sufficient, but restricting to room creators would prevent students from
  seeing cross-room aggregates. This is a policy decision, not a technical
  one -- the authZ check is a one-line change.
- OQ-2: Should the traffic-light thresholds (5/15 minutes) and alert threshold
  (10 minutes) be configurable per project, or are fixed constants acceptable
  for the NSTC MVP? The spec uses constants.

## 15. Deviation Log

- **D-1**: The spec defines `aggregate_for_project(*, project_id, ...)` on
  `AggregationService`. The implementation uses `aggregate_for_rooms(*,
  chatroom_ids)` instead, with the API layer resolving project-to-room IDs via
  `ConversationFacade`. This respects the DDD boundary (activities context
  never queries conversation tables directly). Approved at plan review.

- **D-2**: The spec places query composables under `queries/`. The
  implementation uses `composables/` for TanStack Vue Query composables and
  `queries/` for the query key factory only, matching the established codebase
  pattern (keys slice uses `composables/useProjectKeys.ts` + `queries/index.ts`).

- **D-3**: The spec references `Depends(current_project_member)` for endpoint
  authZ. The codebase uses `assert_project_membership(db=..., principal=...,
  project_id=...)` called inside the handler, which is the established pattern.
  Functionally identical.

- **D-4**: `useProjectSocket` uses `wsManager.channel()` from the existing
  `@shared/transport/ws-manager` rather than raw `WebSocket`, matching the
  codebase's centralized WS management (ticket-based auth, reconnect with
  backoff, per-user connection cap).

## 16. Follow-ups

- **FU-1**: AC-4/AC-5/AC-11 are unticked (code complete, need running stack
  with real WebSocket for integration verification). The project-channel
  endpoint follows the same `connection_loop` + `authorize` pattern as
  `ws/chatroom.py` and reuses the same auth watchdog interval.

- **FU-2**: The `_resolve_project_id` helper in `broadcast.py` opens a
  short-lived DB session for each project-channel emit. At MVP scale (1-2
  teachers, 5-7 rooms) this is negligible. At higher scale, consider caching
  the chatroom-to-project mapping in Redis or threading project_id through
  the caller.

- **FU-3**: OQ-1 (dashboard access: all project members vs room creators only)
  is resolved as "project membership for authZ, `created_by_user_id` filter
  for data scope." If the policy changes, the filter in
  `_resolve_facilitator_rooms` is the single edit point.

- **FU-4**: OQ-2 (configurable thresholds) is deferred. The constants
  `_STALE_MINUTES_GREEN=5`, `_STALE_MINUTES_YELLOW=15`, `_ALERT_MINUTES=10`
  in `dashboard.py` are the single edit point for per-project configuration.
