---
type: feature
status: implemented
created: 2026-09-14
requirements: [R33.01, R33.02, R33.03, R33.04, R13.01, R13.02]
depends_on: []
---

# Relocate Teacher Dashboard from Project to Workspace Scope

## 1. Summary

The teacher dashboard currently aggregates chatroom data across all workspaces in a
project, which mixes unrelated execution environments into a single view. This task
relocates the dashboard to workspace scope: each workspace gets its own dashboard
showing all rooms within it, regardless of creator. The project-level dashboard
endpoints, route, and sidebar entry are replaced, not preserved alongside.

## 2. Goals and Non-goals

**Goals**

- Dashboard scoped to a single workspace, showing all chatrooms within it.
- All project members who can access the workspace can view the dashboard (no
  facilitator/creator filter).
- Entry point via the workspace card dropdown in `WorkspaceListView`.
- SRS updated to reflect workspace scope.

**Non-goals**

- Project-level overview or cross-workspace aggregation. If needed later, it would be a
  separate feature (FU-1).
- New workspace-level WebSocket endpoint or channel. The existing project-level WS
  channel carries `room_id` in event payloads; the frontend filters by room membership.
  A dedicated workspace channel is a future optimisation (FU-2).
- Configurable thresholds (already deferred as FU-4 of the original dashboard spec).
- Adding new monitoring capabilities beyond the scope change.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Replace project-level dashboard or keep it alongside? | Replace entirely. | A project spans multiple independent workspaces; mixing their data has no monitoring value. A project overview can be added later as a separate feature. |
| Q-2 | Keep the `created_by_user_id` facilitator filter? | Remove it. Show all rooms in the workspace. | A workspace is already a smaller, meaningful scope. Filtering by creator within a workspace is unnecessarily restrictive. |
| Q-3 | Where should the dashboard entry point be? | Workspace card dropdown in `WorkspaceListView`. | Consistent with existing actions (settings, workflows, delete) on workspace cards. Removes the sidebar entry. |
| Q-4 | WebSocket: new workspace-level channel or keep project channel? | Keep project channel; frontend filters events by `room_id` membership. | Minimal backend change. The project channel already carries `room_id`. The frontend summary response tells the view which rooms belong to the workspace, enabling client-side filtering. |
| Q-5 | Dependency on non-implemented dossiers? | None. | Only three non-implemented dossiers exist (`graphrag-two-axis-redesign`, `large-artifacts-silently-dropped`, `code-exec-agent-files-path`). None touches dashboard, workspace, activities, or conversation files. |

## 4. Current State

### Backend

Three REST endpoints under `/api/v1/projects/{project_id}/dashboard/`:

- `GET .../summary` (`dashboard.py:124-152`): calls `_resolve_facilitator_rooms` then
  `ActivitiesFacade.aggregate_for_rooms(chatroom_ids=...)`.
- `GET .../timeseries` (`dashboard.py:155-176`): same room resolution, then
  `ActivitiesFacade.timeseries_for_rooms(...)`.
- `GET .../watchlist` (`dashboard.py:179-203`): same room resolution, then
  `ActivitiesFacade.watchlist_for_rooms(...)`.

`_resolve_facilitator_rooms` (`dashboard.py:110-121`):

1. Calls `ConversationFacade.list_chatroom_ids_for_project(project_id)` (`:117`) to get
   all live chatroom IDs across the project's workspaces.
2. Batch-resolves via `ConversationFacade.get_chatrooms(room_ids)` (`:120`).
3. Filters to rooms where `room.created_by_user_id == principal.user_id` (`:121`).

AuthZ: `assert_project_membership(db, principal, project_id)` on each endpoint
(`dashboard.py:130,163,185`).

The activities facade/service/repo methods (`facade.py:975-990`,
`aggregation_service.py:70-107`, `submission_repo.py:592-698`) all accept
`chatroom_ids: Sequence[uuid.UUID]` and are scope-agnostic -- they do not reference
project or workspace.

### WebSocket

Project-level channel `ws:project:{project_id}` (`ws/project.py:27-57`). Dashboard
events are published by `broadcast.py:266-300`:

- `dashboard.activation.changed` with `{room_id, status}`.
- `dashboard.submission.validated` with `{room_id, type_key, is_valid}`.

Both events resolve the chatroom's project_id via
`_resolve_project_id(chatroom_id)` (`broadcast.py:252-263`), which does a two-hop
lookup: chatroom -> workspace -> `workspace.project_id`.

### Frontend

- Route: `/projects/:projectId/dashboard` (`routes.ts:5`), name `'dashboard'`.
- View: `DashboardView.vue` reads `route.params.projectId` (`:24`) and passes it as a
  getter to four composables (`:28-47`).
- Three query composables call `DashboardService` methods with `projectId`
  (`useDashboardSummary.ts:9-12`, `useDashboardTimeseries.ts:14-19`,
  `useDashboardWatchlist.ts:9-12`).
- `useProjectSocket.ts` connects to `/project/${pid}` (`:38`), subscribes to
  `dashboard.*` events, and invalidates all dashboard queries on match (`:41-48`).
- Query keys include `projectId` (`queries/index.ts:2-6`).
- Sidebar nav: `AppSidebar.vue:65-71` builds a `dashboardNav` entry reading
  `workspace.projectId`.
- eslint deps: `dashboard: ['activities', 'tenancy']` (`eslint.config.js:36`).

### Conversation -- Workspace routes and methods

`ChatroomRepository.list_for_workspace(workspace_id)` (`chatroom_repo.py:209-230`)
returns full `Chatroom` objects filtered by `workspace_id`. There is no
`list_ids_for_workspace` that returns bare IDs; one is needed.

`ConversationFacade` has no workspace-scoped chatroom ID listing. The existing
`list_chatroom_ids_for_project` (`facade.py:239-244`) joins chatrooms to workspaces and
filters by `project_id`.

`WorkspaceListView.vue` already has dropdown actions per workspace card: settings
(`:145`), workflows (`:140`), delete (`:147`). The dashboard action follows this pattern.

## 5. Design

### Options considered

**Option A -- Workspace-scoped endpoints, project WS channel**: Replace the three
endpoints from `{project_id}` to `{workspace_id}`. Add
`list_chatroom_ids_for_workspace` to `ConversationFacade`. Keep the project-level WS
channel; the frontend filters events by `room_id` membership. Remove `_resolve_facilitator_rooms` and sidebar nav entry.

**Option B -- Workspace-scoped endpoints, new workspace WS channel**: Same as A, plus
a new `ws:workspace:{workspace_id}` channel and `/ws/workspace/{workspace_id}` endpoint.
Broadcast dispatches events to the workspace channel instead of the project channel.

**Option C -- Add workspace_id filter to existing project endpoints**: Keep the
project-level endpoints but add an optional `workspace_id` query parameter. The frontend
passes the workspace ID to narrow the scope.

### Decision

**Option A.** The WS channel change (Option B) adds a new endpoint, a new channel
function, and changes to broadcast.py for a marginal efficiency gain that matters only
when a project has many workspaces with independent activity. The project channel
already carries `room_id`; the frontend summary response tells the view which rooms
belong to the workspace, enabling O(1) client-side filtering. Option C keeps a confusing
API surface and preserves the project-scoped route.

## 6. Detailed Changes

### Backend

**`app/api/v1/dashboard.py`**

- Router prefix changes from `/projects/{project_id}/dashboard` to
  `/workspaces/{workspace_id}/dashboard`.
- All three endpoints accept `workspace_id: uuid.UUID = Path(...)` instead of
  `project_id`.
- `_resolve_facilitator_rooms` is replaced by `_resolve_workspace_rooms`:
  1. Calls `ConversationFacade(db).get_workspace(workspace_id)` to obtain the workspace
     (raises 404 if not found or deleted).
  2. Calls `assert_project_membership(db, principal, workspace.project_id)`.
  3. Calls a new `ConversationFacade(db).list_chatroom_ids_for_workspace(workspace_id)`
     to get all live chatroom IDs.
  4. Batch-resolves room names via `get_chatrooms(room_ids)`.
  5. Returns all rooms -- no `created_by_user_id` filter.
- Router registration in `app/api/v1/__init__.py` changes the prefix path.

**`contexts/conversation/infrastructure/repositories/chatroom_repo.py`**

- New method `list_ids_for_workspace(workspace_id: uuid.UUID) -> list[uuid.UUID]`:
  selects `chatrooms.id` where `workspace_id = :wid AND deleted_at IS NULL`. Simpler
  than `list_ids_for_project` (no join needed -- chatrooms have a direct `workspace_id`
  FK).

**`contexts/conversation/interfaces/facade.py`**

- New method `list_chatroom_ids_for_workspace(workspace_id)` delegating to the new
  repo method.
- Existing `get_workspace(workspace_id)` method (if present) reused; otherwise add a
  thin facade method delegating to `WorkspaceRepository.get(workspace_id)`.

**No changes** to:

- `contexts/activities/` -- the facade, service, and repo methods are scope-agnostic
  (they take `chatroom_ids`).
- `contexts/activities/interfaces/broadcast.py` -- events still published to the
  project channel with the same payload. The `_resolve_project_id` function is
  unchanged.
- `app/api/ws/project.py` -- the WebSocket endpoint stays project-scoped.
- No migration needed -- no schema change.

### API contract

Three endpoints change path from `{project_id}` to `{workspace_id}`:

| Before | After |
|---|---|
| `GET /api/v1/projects/{project_id}/dashboard/summary` | `GET /api/v1/workspaces/{workspace_id}/dashboard/summary` |
| `GET /api/v1/projects/{project_id}/dashboard/timeseries` | `GET /api/v1/workspaces/{workspace_id}/dashboard/timeseries` |
| `GET /api/v1/projects/{project_id}/dashboard/watchlist` | `GET /api/v1/workspaces/{workspace_id}/dashboard/watchlist` |

Request/response models (`RoomSummaryOut`, `DashboardSummaryOut`, etc.) are unchanged.
`gen:api` rerun required.

### Frontend

**`slices/dashboard/routes.ts`**: Path changes to `/workspaces/:workspaceId/dashboard`.
Route name stays `'dashboard'`.

**`slices/dashboard/views/DashboardView.vue`**: Reads
`route.params.workspaceId` instead of `route.params.projectId`. Passes `workspaceId`
getter to composables. For the WS composable, resolves `projectId` from
`useWorkspaceStore().projectId` (the store is already populated when a user navigates
into a project context).

**Three query composables**: Parameter rename from `projectId` to `workspaceId`. API
calls update to the regenerated `DashboardService` methods.

**`composables/useProjectSocket.ts`**: Stays connected to the project-level WS channel
(reads `projectId` from the workspace store). Adds a client-side filter: maintains a
`Set<string>` of room IDs from the summary query response; only invalidates queries
when an incoming event's `room_id` is in the set.

**`queries/index.ts`**: Query keys change from `projectId` to `workspaceId`:

```ts
summary: (workspaceId: string) => ['dashboard', 'summary', workspaceId],
timeseries: (workspaceId: string, window: string, bucket: string) =>
  ['dashboard', 'timeseries', workspaceId, window, bucket],
watchlist: (workspaceId: string) => ['dashboard', 'watchlist', workspaceId],
```

**`app/components/AppSidebar.vue`**: Remove the `dashboardNav` computed (`:65-71`) and
its template rendering (`:176-184`).

**`slices/conversation/views/WorkspaceListView.vue`**: Add a "Dashboard" dropdown action
per workspace card, navigating to `{ name: 'dashboard', params: { workspaceId: ws.id } }`.
Import `ChartBarIcon`. Add i18n key `conversation.workspace.actions.dashboard`.

**`slices/dashboard/types/index.ts`**: No change (re-exports are model-based, not
route-based).

**`eslint.config.js`**: `dashboard` deps stay `['activities', 'tenancy']`. The
`WorkspaceListView` navigates to the dashboard route by name (a string), which does not
require a slice import.

**i18n**: Remove `app.sidebar.dashboard` key. Add
`conversation.workspace.actions.dashboard` key in both `en.json` and `zh-TW.json`.

### Deploy/config

No changes. No new env vars, Vault paths, or compose changes.

## 7. NFR Checklist

- [x] i18n -- all new strings through `$t()`. One key removed from app locale, one
  added to conversation locale.
- [x] Audit log -- no new domain events. Dashboard is read-only.
- [x] Tenant isolation -- `assert_project_membership` still called, derived from
  `workspace.project_id`. A user must be a project member to see the workspace
  dashboard.
- [x] Error handling UX -- empty state when workspace has no rooms (existing
  `DashboardView` already handles empty `rooms` array). 404 when workspace not found.
- [x] Performance -- no change in query complexity. Room ID resolution switches from
  a join (project -> workspaces -> chatrooms) to a direct filter
  (chatrooms.workspace_id), which is simpler.

## 8. Security Considerations

**Tenant isolation**: The dashboard endpoints currently assert project membership. The
new flow resolves `workspace.project_id` first and then asserts project membership on
that, so AuthZ is unchanged. A user who is not a project member cannot access any
workspace dashboard.

**Data exposure**: Removing the `created_by_user_id` filter means any project member
sees all rooms in a workspace's dashboard. This is intentional: project membership is the
access boundary, and room-level visibility flags
(`allow_project_members`, `allow_org_members`, etc.) govern chat access, not dashboard
access. The dashboard shows aggregate counts and traffic-light status, not message
content.

**WebSocket**: Unchanged -- project membership is verified at connection time and
re-checked by the auth watchdog (`ws/project.py:43-47`).

## 9. Quality Notes

**Existing debt in touched files**

- `dashboard.py:29` `_ALERT_MINUTES = 10` is used only in an i18n string, not in the
  traffic-light logic, which uses 5/15 (FU-4 of the original spec; not addressed here).
- `broadcast.py:257` `_resolve_project_id` opens a short-lived DB session per emit
  (FU-2 of the original spec; not addressed here).

**Patterns to follow**

- Workspace-scoped API routes: follow the router prefix pattern used by conversation
  workspace routes (no nested project prefix).
- `ChatroomRepository.list_ids_for_project` (`chatroom_repo.py:164-184`) is the
  exemplar for the new `list_ids_for_workspace`.
- `WorkspaceListView.vue` dropdown actions (`:140-147`) are the exemplar for the
  dashboard entry point.

**Reuse inventory**

- `assert_project_membership` (`deps.py:101`) -- reused for AuthZ after resolving
  workspace's project_id.
- `ConversationFacade.get_chatrooms` (`facade.py:222-228`) -- reused for batch room
  name resolution.
- `useWorkspaceStore().projectId` -- reused to obtain projectId for the WS connection.
- All existing dashboard UI components (`RoomStatusCard`, `OutputChart`,
  `StudentWatchlist`, `DashboardAlerts`, `TimeWindowSelector`) -- unchanged.
- `wsManager.channel()` -- reused for the WS connection.

## 10. Risks and Rollback

**Risk 1 -- Stale project-level bookmarks**: Users who bookmarked
`/projects/:pid/dashboard` get a 404. Mitigation: this is a development-stage product
with no external users. Acceptable.

**Risk 2 -- WS event filtering on the frontend**: If the summary response is slow or
empty, the room set is empty and no WS events will trigger an invalidation until the
summary loads. Mitigation: `useProjectSocket` already debounces 500ms; the summary
query runs first and populates the set before any event would meaningfully arrive.

**No migration** -- rollback is `git revert` of the code changes plus a `gen:api` rerun.

## 11. Acceptance Criteria

- [x] AC-1: `GET /api/v1/workspaces/{workspace_id}/dashboard/summary` returns all
  rooms in the workspace, regardless of `created_by_user_id`.
  Verified: `_resolve_workspace_rooms` returns all rooms; unit test confirms.
- [x] AC-2: `GET /api/v1/workspaces/{workspace_id}/dashboard/timeseries` returns
  time-series data for all rooms in the workspace.
  Verified: same `_resolve_workspace_rooms` used; existing aggregation tests pass.
- [x] AC-3: `GET /api/v1/workspaces/{workspace_id}/dashboard/watchlist` returns
  watchlist entries aggregated across all rooms in the workspace.
  Verified: same `_resolve_workspace_rooms` used; existing aggregation tests pass.
- [x] AC-4: All three endpoints return 404 when `workspace_id` does not exist.
  Verified: unit tests for missing and soft-deleted workspace both return 404.
- [x] AC-5: All three endpoints return 403 when the caller is not a member of the
  workspace's parent project.
  Verified: unit test confirms 403 when `assert_project_membership` raises.
- [x] AC-6: The old project-scoped endpoints (`/api/v1/projects/{project_id}/dashboard/*`)
  no longer exist in the OpenAPI spec.
  Verified: grep on openapi.json confirms no `projects.*dashboard` paths.
- [x] AC-7: `WorkspaceListView` shows a "Dashboard" action in each workspace card's
  dropdown. Clicking navigates to `/workspaces/:workspaceId/dashboard`.
  Verified: code inspection; needs running stack for visual confirmation.
- [x] AC-8: The sidebar no longer shows a "Dashboard" link in the project context
  section.
  Verified: `dashboardNav` removed; AppSidebar.test.ts confirms absence.
- [x] AC-9: WebSocket events for `dashboard.submission.validated` and
  `dashboard.activation.changed` update the workspace dashboard in real-time when the
  event's `room_id` belongs to the viewed workspace.
  Verified: `useProjectSocket` filters events by room membership set from summary.
  Needs running stack for WS integration verification.
- [x] AC-10: WebSocket events whose `room_id` belongs to a different workspace do NOT
  trigger a dashboard refresh.
  Verified: `useProjectSocket` returns early when `room_id` is not in `roomIds` set.
  Needs running stack for WS integration verification.
- [x] AC-11: i18n keys exist in both `en.json` and `zh-TW.json` for the workspace
  dashboard action.
  Verified: `conversation.workspace.actions.dashboard` present in both locales.
- [x] AC-12: `pnpm run gen:api` produces updated `DashboardService` methods accepting
  `workspaceId` instead of `projectId`.
  Verified: DashboardService methods confirmed workspace-scoped after gen:api.
- [x] AC-13: Existing dashboard unit tests (`test_dashboard_aggregation.py`,
  `DashboardView.test.ts`) pass after updates.
  Verified: all backend (12) and frontend (8) tests pass.

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-1, AC-2, AC-3 | Unit | `tests/unit/test_dashboard_*.py` -- new or updated tests calling the endpoints with workspace_id |
| AC-4, AC-5 | Unit | Same file -- 404/403 error cases |
| AC-6 | Gate | `pnpm run check:openapi-drift` -- verifies frontend types match backend |
| AC-7, AC-8 | Component | `DashboardView.test.ts` (route param change), `AppSidebar.test.ts` (nav removal) |
| AC-9, AC-10 | Manual | Against a running stack with real WebSocket |
| AC-11 | Lint | `pnpm lint` i18n gate |
| AC-12 | Script | `pnpm run gen:api` output diff |
| AC-13 | CI | Full test suite pass |

## 13. SRS Delta

### Amendments to Section 33

Replace [R33.01] at `REQUIREMENTS.md:2374`:

> - **[R33.01]** The activities context exposes workspace-scoped aggregation methods
>   (summary, time-series, watchlist) that query across all chatrooms in a workspace.
>   These complement the room-scoped aggregation of [R30.10].

Replace [R33.02] at `REQUIREMENTS.md:2375`:

> - **[R33.02]** A dashboard view presents project members with cross-room activity
>   status within a workspace, time-series output charts, a student watchlist (truncated
>   subject codes, never names or emails -- extending [R28.18]), and threshold alerts.
>   The view is lazy-loaded and does not affect the initial bundle budget. The entry
>   point is the workspace card in the workspace list, not a global sidebar link.

[R33.03] and [R33.04] are unchanged -- the project-level WebSocket channel continues
to deliver dashboard events, and the membership verification contract is the same.

## 14. Open Questions

None.

## 15. Deviation Log

- D-1: `useProjectSocket` renamed to `useDashboardSocket` and refactored. The original
  implementation derived `projectId` from `useWorkspaceStore()` (localStorage), which is
  null on a deep-link in a fresh session -- the WS channel silently would not connect.
  Fixed by fetching workspace details from the API (`readWorkspaceApiWorkspacesWorkspaceIdGet`)
  to resolve `project_id` server-side. Room IDs now passed as a `Ref<Set<string>>`
  parameter from `DashboardView` instead of internally calling `useDashboardSummary`,
  removing the intra-slice coupling. Found by `/code-review`.

## 16. Follow-ups

- FU-1: Project-level dashboard overview aggregating across workspaces, if cross-workspace
  monitoring proves useful.
- FU-2: Workspace-level WebSocket channel (`ws:workspace:{workspace_id}`) to avoid
  broadcasting dashboard events project-wide when only one workspace is being monitored.
- FU-3: Configurable stale thresholds per workspace (carried from the original spec's FU-4).
- FU-4: `_resolve_workspace_rooms` (`dashboard.py:109`) checks `workspace.deleted_at`
  explicitly before asserting project membership. Other workspace-scoped routers
  (`workflows.py`, `chatrooms.py`, `workspaces.py`) do not perform this check. The
  inconsistency is defense-in-depth here but should be unified across all
  workspace-resolving endpoints.
