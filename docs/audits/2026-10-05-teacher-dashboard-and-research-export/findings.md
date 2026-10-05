---
type: audit
status: reviewed
created: 2026-10-05
requirements: [R13.17, R13.32, R30.10, R33.01, R33.02, R33.03, R33.04, R33.10, R8.08, R8.11]
---

# Audit: Teacher dashboard, workspace scope, and research data export

## 1. Scope

**Area.** The teacher dashboard as rescoped to a workspace, and the research data export
reachable from it. Backend: `app/api/v1/dashboard.py`, `app/api/v1/research_export.py`,
`app/api/ws/project.py`, the dashboard methods of `contexts/activities` (aggregation service,
`submission_repo.py`, `interfaces/broadcast.py`), the research export service and builder in
`contexts/conversation/application/`, the `research_data_export` worker task in
`app/workers/tasks/conversation.py`, and the shared pieces they lean on (`deps.py` membership
and owner gates, `tenancy` role resolver and facade, `shared_kernel/realtime`, MinIO bucket
bootstrap and the exports retention sweep). Frontend: the whole `slices/dashboard` slice,
`useProjectRole`, the persisted workspace store, `AppSidebar.vue`, `ws-manager.ts`, the
notification kind table, and the query client defaults.

**Intent sources.** `REQUIREMENTS.md` [R13.01], [R13.02], [R13.17], [R13.32], [R30.10],
[R33.01]-[R33.04], [R33.10], [R5.03], [R8.08], [R8.09], [R8.11]; and the three dossiers that
built the area: `docs/tasks/2026-09-13-teacher-dashboard/spec.md`,
`docs/tasks/2026-09-14-dashboard-workspace-scope/spec.md`, and
`docs/tasks/2026-09-21-research-data-export/spec.md`. Intent coverage is good: each dossier has
numbered acceptance criteria, and most findings below are deviations from a ticked AC. Two
places where the intent sources contradict each other are called out in the findings that
depend on them (F-9 and F-24).

**Depth.** Thorough. Six investigation lenses (state and lifecycle; boundary inputs and data
correctness; concurrency and async; isolation as correctness; event flow and frontend cache;
error paths) produced roughly 70 raw candidates, deduplicated to 30. Each of the 30 went
through one adversarial verification round whose explicit task was to refute it, run by four
independent verifiers grouped by code region. Verification was static: no stack was launched
and nothing below was reproduced against a running system. That limitation matters here more
than usual, because several dossier ACs in this area were ticked "verified by code
inspection" or left unticked for want of a running stack.

## 2. Coverage

Read in full: the three dossiers, the cited REQUIREMENTS entries, `dashboard.py`,
`research_export.py`, `ws/project.py`, `research_export_service.py`,
`research_export_builder.py`, the research export worker task and `WorkerSettings`, the
dashboard queries in `submission_repo.py` and `aggregation_service.py`, the dashboard emitters
in `broadcast.py`, `minio_init.py`, the exports branch of the retention sweep, the tenancy
role resolver and owner/member checks, `project_service.soft_delete`, and every file under
`frontend/src/slices/dashboard`.

Sampled rather than read in full: `shared_kernel/realtime/connection.py` (relay and watchdog
paths only), `ws-manager.ts` (event dispatch, close handling, reconnect and refresh only),
`AppSidebar.vue` (workspace switcher and dashboard link only), the notification slice (kind
table and card rendering only), and the installed arq worker (timeout and retry semantics
only). Backend tests were searched for pins on specific behaviors, not audited as a body.

Not covered, and therefore not claimed clean:

- **Runtime behavior.** Popup blocking (F-6), WebSocket delivery and close codes (F-16, F-17),
  and MinIO lifecycle timing (F-5) were established by reading code and configuration, not by
  observation.
- **Rendering.** `OutputChart.vue` theming, `RoomStatusCard` and `StudentWatchlist` layout,
  and the zh-TW copy were not reviewed beyond key existence.
- **De-identification completeness.** Whether free text in messages, observations and
  compaction summaries can carry names is a privacy question routed to FU-7, not judged here.
- **Scale.** The export holds everything in memory; behavior beyond the stated 20-30
  participants per class was not assessed.

## 3. Findings

## F-1: Room status and stall alerts never age while the page is open

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/app/api/v1/dashboard.py:98-106,152` (traffic light computed once per
  request); `frontend/src/slices/dashboard/components/RoomStatusCard.vue:14-30` and
  `DashboardAlerts.vue:14-16` (render the server `status` only);
  `frontend/src/slices/dashboard/composables/useDashboardSummary.ts:6-15`,
  `useDashboardWatchlist.ts:6-15` and `frontend/src/shared/query-client.ts:4-21` (no
  `refetchInterval` anywhere); `useDashboardSocket.ts:57-66` (the only invalidation source is a
  `dashboard.*` event)
- **Failure scenario**: A teacher opens the dashboard on a projector at 10:00, two minutes after
  the last submission, and every card is green. The class goes silent, so no submission or
  activation event is published and nothing refetches. At 10:30 the cards still read "Active"
  and the alert strip is empty. Silence is exactly the condition the alert exists to catch, and
  it is the one condition that produces no trigger.
- **Blast radius**: Every dashboard left open without focus changes, which is the normal
  classroom use. F-16 currently masks this when other rooms in the project are busy, so fixing
  F-16 alone makes this finding worse.
- **Intent source**: [R33.02]; teacher-dashboard AC-7, AC-10, AC-11

## F-2: Traffic light and alerts ignore activation state and submission validity

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `dashboard.py:37-43` (`RoomSummaryOut` has no `activation_status`);
  `dashboard.py:98-106` (`_traffic_light` reads only `last_submission_at`);
  `backend/contexts/activities/infrastructure/repositories/submission_repo.py:599-613`
  (`last_submission_at` is the max over all rows, valid, invalid, pending or error);
  `DashboardAlerts.vue:15` (alert on `red`, i.e. more than 15 minutes) against
  `frontend/src/slices/dashboard/locales/en.json:21` ("over 10 minutes"); `dashboard.py:28`
  (`_ALERT_MINUTES = 10`, unreferenced)
- **Failure scenario**: Since the workspace rescope the summary lists every room in the
  workspace, including the default room [R13.02] and other chat-only rooms. Each of those is red
  with a permanent "no valid submission for over 10 minutes" warning although no activity was
  ever started there. Conversely, a room in an active round that receives only invalid answers
  for 20 minutes stays green with no alert, a room whose round ended a minute ago stays green,
  and a room 12 minutes into a silent round is yellow with no alert.
- **Blast radius**: Every workspace with more than one room; the alert strip becomes noise that
  teachers learn to ignore. `dashboard.activation.changed` is published and consumed but has no
  visible effect, because nothing in the summary depends on activation.
- **Intent source**: teacher-dashboard AC-1, AC-7, AC-10 (all ticked). Only the 10-versus-15
  minute half is recorded as debt (workspace-scope §9, FU-3).

## F-3: The watchlist can never list a participant with zero submissions

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `submission_repo.py:664-698` (`participant_submission_counts` selects from
  submissions joined to sessions and groups by subject, so every row has a count of at least
  one); `backend/contexts/activities/application/aggregation_service.py:102-121` (no other
  source is consulted); `backend/app/api/v1/activities.py:1403-1404` (sessions are opened by
  the first submission, so a session-based source would not help);
  `backend/tests/unit/test_dashboard_aggregation.py:53-66` (`test_zero_submissions_always_included`
  mocks a `(code, 0, None)` row the real query cannot produce)
- **Failure scenario**: A room has five students; four submit three times each and one never
  submits. The watchlist lists the four submitters and omits the one student who most needs
  attention. The counts also include invalid and pending attempts, so a student spamming invalid
  answers ranks as productive.
- **Blast radius**: The primary purpose of the watchlist. The test that names this exact case
  passes regardless of behavior, so the suite gives false assurance.
- **Intent source**: teacher-dashboard §2 Goals, AC-3; [R33.02]

## F-4: The watchlist counts all-time submissions instead of the active activation

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `aggregation_service.py:102-106` and `submission_repo.py:664-686` (no activation
  parameter or filter); teacher-dashboard §6 specified `activation_id=None` on the signature, and
  Deviation D-1 records only the `project_id` to `chatroom_ids` change
- **Failure scenario**: Student A submitted 20 times in last week's round and nothing today,
  while peers have three each today. A's all-time count is far above the median, so A is never
  flagged during the round where A is idle.
- **Blast radius**: Any workspace used for more than one round, which is every course.
- **Intent source**: teacher-dashboard AC-3 ("for the active activation")

## F-5: The research ZIP is purged after 24-48 hours while the API reports it ready for 72

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/conversation/application/research_export_builder.py:97-108`
  and `backend/shared_kernel/storage/minio_client.py:326-331` (upload to the shared `exports`
  bucket at `{job_id}/research-data.zip`); `backend/smap/bootstrap/minio_init.py:67-77,152-158`
  (bucket-wide `Expiration(days=1)`); `backend/app/workers/tasks/retention.py:599-644` (daily
  sweep deletes exports objects older than 24 hours); `research_export_service.py:19,133-137`
  (job state TTL 72 hours); `backend/app/api/v1/research_export.py:72,129-134` (every GET
  presigns a fresh 72-hour URL without checking the object exists)
- **Failure scenario**: An export finishes on day 1 at 09:00. The researcher returns on day 2 or
  3, as the dossier intends ("may be downloaded across sessions"). `GET` returns
  `status: ready` with a URL, and the URL answers `NoSuchKey`. Separately, a GET at hour 71 mints
  a URL valid until hour 143, past the job state it is supposed to match.
- **Blast radius**: Every export not downloaded within the first day. The dossier's own key
  layout (`research/{job_id}/`) was not used either, so no prefix-scoped lifecycle exemption is
  possible today.
- **Intent source**: research-export §6, §8 ("72h, matching the Redis job TTL"), AC-9

## F-6: A finished export cannot be reached from the UI once polling stops or the popup is blocked

- **Severity**: major
- **Verdict**: confirmed (the popup-blocker part is inferred from browser behavior, not observed)
- **Evidence**: `frontend/src/slices/dashboard/components/ResearchExportButton.vue:48-63`
  (`window.open(status.url)` runs after at least one 3-second timer and two awaited requests,
  return value unchecked, success toast shown, dialog closed, URL never rendered); `:26-27,49,74`
  (polling aborts on unmount or after 60 polls, with a "you will be notified" toast);
  `backend/app/workers/tasks/conversation.py:475-481` (notification carries only `job_id` and
  `workspace_id`); `frontend/src/slices/notifications/lib/kindConfig.ts:80-85` (no `action` for
  `export.ready`) and `NotificationCard.vue:17,66-73` (links render only from `action`)
- **Failure scenario**: A teacher clicks Export; the worker is ready at the second poll, about
  six seconds in, after the click's user activation has lapsed. The browser blocks the window,
  the dialog closes with "ready", and nothing downloads. Or the export takes four minutes, the
  poll gives up, and the promised notification arrives with no link. In both cases there is no
  UI path to `GET /api/exports/research/{job_id}`, and the only recourse is to run the export
  again, which meets the same problem.
- **Blast radius**: The deliverable of the whole feature. The chat export avoids this by
  rendering the URL (`useChatroomExport.ts`), and `useChatroomMessages.ts:371-375` shows the
  in-gesture pre-open pattern.
- **Intent source**: research-export §6 frontend ("opens the presigned URL download"), §7
  ("download link on ready"), AC-13 ("so they do not need to poll")

## F-7: The export button is gated on the sidebar's project, not the viewed workspace's project

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/dashboard/views/DashboardView.vue:57-71`
  (`useProjectRole(() => workspace.projectId)` against the persisted store);
  `frontend/src/shared/stores/workspace.ts:15-47` (loaded from localStorage, written only by
  `OrgProjectSwitcher.vue:78-86` and the sidebar); `useProjectRole.ts:44-48` (`decided` stays
  false while the project id is empty); `useDashboardSocket.ts:18-28` (already resolves the
  workspace's real `project_id` and does not expose it)
- **Failure scenario**: A non-admin Project Owner opens a bookmarked
  `/workspaces/W/dashboard` in a fresh browser; the store has no project, the role is never
  decided, and the button never renders. With the store on project P1 (owner) while viewing a
  P2 workspace (member), the button shows and the POST returns 403; the reverse hides it from
  a real P2 owner.
- **Blast radius**: Every owner who reaches a dashboard by link or from another project.
  Workspace-scope D-1 recorded and fixed this exact deep-link defect for the socket only.
- **Intent source**: research-export AC-11

## F-8: Research export refuses Org Owners while the UI offers them the button

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `research_export.py:93` (`assert_project_owner`); `backend/app/api/v1/deps.py:66-98`
  and `backend/contexts/tenancy/interfaces/facade.py:134-140` (only an explicit
  `project_members` OWNER row passes, plus the platform-admin bypass); `project_service.py:94-99`
  (only the creator gets that row); `useProjectRole.ts:37-39` and
  `backend/app/api/v1/projects.py:119-134` (`is_moderator` includes inherited org owners);
  `backend/contexts/conversation/application/access.py:444-473` and the permission matrix
  (chat export admits `ORG_OWNER`)
- **Failure scenario**: Org Owner O2 did not create project P. O2 opens a P dashboard, sees
  Export, clicks it, and gets 403 with an error toast.
- **Blast radius**: Every org owner who is not the project creator, which in a school
  deployment is typically the research lead. The route tests mock the gate, so nothing pins it.
- **Intent source**: [R33.10] ("Project Owners and Org Admins"); research-export Q-1 ("same
  authorization as the existing chat export"); [R8.08]. "Org Admin" is not a defined role, and
  some other features deliberately use the strict explicit-row owner; neither applies here,
  because Q-1 points at the chat-export rule.

## F-9: The dashboard reveals names and activity of rooms the caller cannot open

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `dashboard.py:109-125` (`_resolve_workspace_rooms` gates only on project
  membership and returns every live room); `dashboard.py:138-155,171-178,191-204` (room names,
  counts, timeseries and watchlist subject codes for all of them); `access.py:238-311`
  (`visible_room_ids`, the batch read gate the listing paths use);
  `backend/app/api/v1/workspaces.py:68-93` (the sibling listing applies [R13.32])
- **Failure scenario**: Workspace W holds room A (`allow_project_members`) and room B
  (`allow_project_owners_only`, named "Exam grading"). A plain project member sees W because A
  is readable, opens the dashboard, and receives B's name, counts and status and the subject
  codes of B's participants. Opening B itself returns 403.
- **Blast radius**: Any workspace mixing room access tiers or member-group restrictions. The
  project channel also broadcasts bare room ids for every room, which [R33.03] explicitly allows.
- **Intent source**: [R13.32]. **Intent conflict**: workspace-scope §8 states that room flags
  "govern chat access, not dashboard access" on the grounds that the dashboard shows no message
  content, which overlooks that it returns room names. That dossier's SRS delta amended only
  [R33.01]/[R33.02] and never [R13.32], so the SRS rule stands; the conflict still needs an
  explicit decision before a fix is specced.

## F-10: All guests collapse into one identifier in the research transcripts

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `research_export_builder.py:214-219` (only `agent` and `human`/`user` senders
  are mapped; every other sender type falls through to the literal string); guest messages are
  stored with `sender_type=guest` and `sender_id` set to the guest session
  (`backend/app/api/v1/messages.py:191-199`)
- **Failure scenario**: Thirty students join by guest link. In `transcripts.json` every one of
  them is `"guest"`, so speaker-level analysis and cross-referencing with `submissions.csv` are
  impossible for exactly the population a classroom pilot produces.
- **Blast radius**: Every research bundle from a room with guest links. The companion guest audit
  (`docs/audits/2026-10-05-guest-anonymous-session/`) found the same identity gap in agent
  prompts and the member roster.
- **Intent source**: research-export Q-4 (every sender mapped to its code consistently), AC-6;
  [R33.10]

## F-11: The watchlist flags participants at the median and hard-codes "needs attention"

- **Severity**: major
- **Verdict**: confirmed (an unrecorded deviation, pinned by tests)
- **Evidence**: `aggregation_service.py:120` (`count <= median`); `dashboard.py:200`
  (`needs_attention=True`); `test_dashboard_aggregation.py:20-32` (class docstring restates AC-3
  as "at or below median" and pins the all-equal case)
- **Failure scenario**: Four students with five submissions each are all listed and badged. In
  general at least half the class is always badged whenever any submission exists, and the AC-9
  empty state can only appear when nobody has submitted.
- **Blast radius**: Usefulness of the watchlist; together with F-3 and F-4 the panel neither
  includes the right students nor excludes the wrong ones.
- **Intent source**: teacher-dashboard AC-3, AC-9 ("below median"). AC-9's own wording ("all
  above median") is not satisfiable under any median rule and should be restated in the fix.

## F-12: A notification failure turns a finished export into a failed one

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/app/workers/tasks/conversation.py:472-488` (`mark_ready`, then
  `NotificationFacade.send`, inside the same `try`; the `except` calls `mark_failed` and
  re-raises); `backend/app/workers/tasks/skills.py:313-327` (the skills export handles the same
  ordering deliberately)
- **Failure scenario**: The ZIP is uploaded and the audit row committed, then a transient Redis
  or DB error during the notification insert. The job flips to `failed`, the poller shows an
  error, the user retries, and a second bundle and a duplicate `research_data.exported` audit row
  are produced.
- **Blast radius**: Needs a transient fault; arq does not retry a plain exception.
- **Intent source**: research-export AC-8, AC-13, §7 error handling

## F-13: Export authority is checked only when the job is requested

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `conversation.py:371-381` (the worker checks only that the job arguments match
  the stored state); `research_export.py:125-126` (GET checks only the stored owner id);
  `backend/contexts/conversation/application/chat_export_service.py:84-105` (the chat export
  re-derives access in the worker)
- **Failure scenario**: An owner requests an export and is demoted or removed before the job
  runs. The worker still bundles the workspace and audits it under their id, and they can keep
  minting download URLs until the object is purged.
- **Blast radius**: Narrow window; data the user was entitled to at request time.
- **Intent source**: [R33.10]; research-export service docstring ("mirrors the chat export
  pattern")

## F-14: A worker timeout leaves the export stuck in "running"

- **Severity**: minor
- **Verdict**: confirmed (mechanism traced; unlikely at the stated scale)
- **Evidence**: `backend/app/workers/main.py:363` (`job_timeout=600`); arq cancels the coroutine,
  and `CancelledError` bypasses `except Exception` at `conversation.py:482`; `mark_running`
  re-armed the 72-hour TTL
- **Failure scenario**: A slow query pushes the job past 600 seconds. The state reads `running`
  for 72 hours; the UI gives up after three minutes with no failure state. On a worker shutdown
  with retries enabled, the re-run has no status guard and can re-upload and re-audit.
- **Blast radius**: Rare; the chat export task has the same pattern (`conversation.py:272-278`).
- **Intent source**: research-export §7 ("error state with retry")

## F-15: `submissions.csv` does not have the specified columns and escapes CJK text

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `research_export_builder.py:141-176` (only `payload_json` and `sub_scores_json`;
  no flattened payload fields, no `sub_scores_filled` or `sub_scores_filled_fields`; `json.dumps`
  without `ensure_ascii=False`, unlike the JSON files at `:197,229`);
  `backend/tests/unit/test_research_export_builder.py:126-144` (`test_columns_match_spec` pins the
  non-spec header)
- **Failure scenario**: A Chinese-character creativity answer `{"answer":"林"}` is written as
  `{"answer": "林"}`; in SPSS or Excel the column is unreadable without re-parsing.
- **Blast radius**: Every analysis of submissions; the data is present but not in the shape the
  "CSV files for statistical tools" goal needs.
- **Intent source**: research-export §6 (columns), §10 (raw JSON only alongside flattened fields)

## F-16: The dashboard socket's room filter never applies

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/dashboard/composables/useDashboardSocket.ts:62-63` reads
  `(ev.payload)?.room_id`; frames are flat (`backend/shared_kernel/realtime/pubsub.py:52-56`,
  relayed unchanged by `connection.py:332-339,362-364`; `ws-manager.ts:24-27,283-289`); this is
  the only `ev.payload` read in `frontend/src`
- **Failure scenario**: A submission in a W2 room invalidates and refetches all three dashboard
  queries for a teacher viewing W1 in the same project.
- **Blast radius**: Extra refetches, not wrong data. Fix ordering matters: this currently masks
  F-1, and `roomIds` comes only from the last summary, so a naive fix also drops events from
  rooms created after page load.
- **Intent source**: workspace-scope Q-4, AC-10 (ticked "verified" by inspection; the view test
  mocks the composable)

## F-17: The project WebSocket refuses users the REST dashboard admits, then retries forever

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/app/api/ws/project.py:37-47` (`is_project_member`, explicit row only)
  versus `deps.py:101-123` (role resolver, includes inherited owners and org members);
  `frontend/src/shared/transport/ws-manager.ts:203-229` (no terminal handling for a refused
  handshake; backoff capped at 30 seconds)
- **Failure scenario**: An Org Owner without a project row loads the dashboard over REST, the
  project socket is refused, and the browser fetches a ticket and retries roughly every 30
  seconds indefinitely with no live updates.
- **Blast radius**: Live updates for inherited owners; retry noise.
- **Intent source**: [R33.04] read with [R5.03]/[R8.08]; teacher-dashboard AC-4, AC-9

## F-18: A soft-deleted project's dashboards and exports keep working

- **Severity**: minor
- **Verdict**: confirmed (dashboard); plausible (export, where the actor is the data's owner)
- **Evidence**: `project_service.py:204-242` (soft delete stamps the project and cascades only to
  skills and graph configs; workspaces and `project_members` survive); `dashboard.py:117-120` and
  `research_export.py:87-93` check only the workspace; `role_resolver.py:37` loads the project
  with `include_deleted=True` on purpose; `access.py:110-114` (chat itself does check the
  project, so rooms 404)
- **Failure scenario**: Project P is soft-deleted. A former member opens a bookmarked dashboard
  and receives room names, counts and subject codes; the project socket still connects.
- **Blast radius**: Platform-wide: `workspaces.py:129-154` and other routes on the same gates
  share the gap. Dossier 2026-08-20 D-17 recorded this class and fixed one listing only.
- **Intent source**: [R8.11]

## F-19: The output chart collapses empty time buckets

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `submission_repo.py:634-662` (only non-empty buckets returned, no gap fill
  downstream); `frontend/src/slices/dashboard/components/OutputChart.vue:45-70` (category axis
  built from the union of returned buckets)
- **Failure scenario**: Valid submissions at 10:00 and 10:05, then none until 10:40. The axis
  ends at 10:05 or joins 10:05 to 10:40 as adjacent points, so the stall is invisible; a room
  with no valid submissions in the window has no series at all.
- **Blast radius**: The chart is a secondary signal after the cards, but it is the one a
  teacher reads for trend.
- **Intent source**: teacher-dashboard AC-8 ("counts over the selected time window")

## F-20: Watchlist and chart failures render as reassuring empty states

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `DashboardView.vue:37-48,74-93` (no `error` read for timeseries or watchlist;
  summary error shows both the danger alert and the "no chatrooms" empty state);
  `StudentWatchlist.vue:25-28` with `en.json:14`
- **Failure scenario**: The watchlist endpoint returns 500 after retries; the panel says "All
  participants are above median. No attention needed."
- **Blast radius**: A backend fault is reported to the teacher as good news.
- **Intent source**: teacher-dashboard §7 (error state via a danger alert), AC-9

## F-21: Events missed while the dashboard socket reconnects are never recovered

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `useDashboardSocket.ts:68-70` (status only sets a flag, which
  `DashboardView.vue:53` discards); no `onDegraded` subscription despite
  `ws-manager.ts:57-59,119-126`; pub/sub has no replay (`pubsub.py:3-5`)
- **Failure scenario**: A backend restart drops the socket for 20 seconds while five submissions
  land. After reconnect the counters and chart stay stale until another event or a focus change.
- **Blast radius**: Partially self-healing via focus refetch and the next event.
- **Intent source**: teacher-dashboard AC-11 ("without manual refresh")

## F-22: The dashboard validity flag is wrong, and swept submissions emit no dashboard event

- **Severity**: minor
- **Verdict**: confirmed (latent: no consumer reads the flag today)
- **Evidence**: `backend/app/workers/tasks/activities.py:162,182` (`is_valid` is
  `result_status == "validated"`, true for an invalid verdict because `record_validation` writes
  `VALIDATED` for both, `submission_repo.py:250-252`); `activities.py:188-239` (watchdog emits
  no dashboard event); `backend/app/api/v1/activities.py:1411-1421` (pending submits emit none)
- **Failure scenario**: A validation enqueue is dropped; the watchdog sweeps the submission to
  error after 15 minutes, and the open dashboard never hears of it. Any future client that reads
  `is_valid` will show invalid answers as valid.
- **Blast radius**: Compounds F-1; latent contract violation.
- **Intent source**: [R33.03] ("validity flags"); teacher-dashboard AC-11

## F-23: Project socket membership is re-checked every 60 seconds, not 30

- **Severity**: minor
- **Verdict**: confirmed (spec/code mismatch)
- **Evidence**: `backend/shared_kernel/realtime/connection.py:74,81,387`
  (`_AUTH_RECHECK_SECONDS=30`, room re-auth every second tick); `ws/project.py:43-57` uses the
  same loop; teacher-dashboard §8.3 cites a nonexistent `_auth_check_interval`
- **Failure scenario**: A member removed from the project keeps receiving `ws:project` events for
  up to about 60 seconds.
- **Blast radius**: Room ids and validity flags only; token expiry and denylist still run every
  30 seconds.
- **Intent source**: teacher-dashboard AC-5 (unticked); [R33.04] is looser ("on the auth
  watchdog interval")

## F-24: Org Members who are not project members pass the dashboard gate

- **Severity**: minor
- **Verdict**: plausible (the behavior is confirmed; whether it is a defect depends on an intent
  conflict)
- **Evidence**: `backend/contexts/tenancy/interfaces/role_resolver.py:36-47` (any org member
  gets `ORG_MEMBER` at project scope); `deps.py:118-123` (any non-empty role set passes);
  the same "any role" rule backs `require_membership`
  (`backend/shared_kernel/auth/dependencies.py:130-157`), 18 `assert_project_membership` sites
  and 25 `require_membership` sites
- **Failure scenario**: An org member who can open one `allow_org_members` room in W sees W
  listed, opens its dashboard, and receives every room in W (the F-9 exposure).
- **Blast radius**: Platform-wide semantics; on its own the reach is limited because listings
  are [R13.32]-filtered and ids are UUIDs.
- **Intent source**: **Intent conflict.** [R8.09] ("Org Members are not implicitly Project
  Members"), [R33.02] and workspace-scope §8 ("a user who is not a project member cannot access
  any workspace dashboard") against the platform-wide code convention that "membership" means
  any role, which `allow_org_members` rooms depend on. Needs a decision, not a local fix.

## 4. Refuted Candidates

- **Export date window in UTC days** (`research_export.py:49-56`). Not a deviation: it mirrors
  the chat export's deliberate UTC bounds (`exports.py:60-69`), a test pins it
  (`test_research_export_routes.py:192-204`), and the dossier defines no timezone. The UX cost
  for UTC+8 users is real and is routed to FU-5.
- **Hard-coded English notification title** (`conversation.py:478`). Every notification kind
  does this (`keys/threshold_worker.py:154`, `invite_service.py:203`, and others), so it is not
  specific to this area. Routed to FU-4.
- **Sidebar workspace `<select>` binds an unvalidated id** (`AppSidebar.vue:189-193`). The select
  renders only once the list has loaded, and the immediate watcher at `:66-75` normalizes the
  stored id before render, so the bound value always equals the validated one.
- **Restored sidebar Dashboard link** (`AppSidebar.vue:228-233`, commit ea1a850b). Contradicts
  [R33.02] and workspace-scope AC-8, but the follow-up commit e822af27 shows it was deliberate.
  This is SRS drift, not a behavior defect; routed to FU-3.

## 5. Hand-off

Triaged with the requester on 2026-10-05: every finding is fixed, grouped by root cause. The two intent conflicts were decided as follows. F-9: [R13.32] governs, so the dashboard shows only rooms the caller can open (owners still see every room, since they can open every room); workspace-scope §8 is superseded on this point. F-24: the dashboard is restricted to project members per [R8.09] and [R33.02], changing only the dashboard gate and not the platform-wide any-role membership convention that `allow_org_members` rooms depend on. Dossier paths below are the planned slugs; each becomes a link once `/spec` creates it.

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-status-semantics/` |
| F-2 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-status-semantics/` |
| F-3 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-watchlist-correctness/` |
| F-4 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-watchlist-correctness/` |
| F-5 | fix (2026-10-05) | `docs/tasks/2026-10-05-research-export-delivery/` |
| F-6 | fix (2026-10-05) | `docs/tasks/2026-10-05-research-export-delivery/` |
| F-7 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-and-export-authz/` |
| F-8 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-and-export-authz/` |
| F-9 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-and-export-authz/` |
| F-10 | fix (2026-10-05) | `docs/tasks/2026-10-05-research-export-data-shape/` |
| F-11 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-watchlist-correctness/` |
| F-12 | fix (2026-10-05) | `docs/tasks/2026-10-05-research-export-delivery/` |
| F-13 | fix (2026-10-05) | `docs/tasks/2026-10-05-research-export-delivery/` |
| F-14 | fix (2026-10-05) | `docs/tasks/2026-10-05-research-export-delivery/` |
| F-15 | fix (2026-10-05) | `docs/tasks/2026-10-05-research-export-data-shape/` |
| F-16 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-status-semantics/` |
| F-17 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-and-export-authz/` |
| F-18 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-and-export-authz/` |
| F-19 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-chart-and-error-states/` |
| F-20 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-chart-and-error-states/` |
| F-21 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-status-semantics/` |
| F-22 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-status-semantics/` |
| F-23 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-status-semantics/` |
| F-24 | fix (2026-10-05) | `docs/tasks/2026-10-05-dashboard-and-export-authz/` |

## 6. Out-of-scope Observations

- **FU-1 (check-security).** `mark_failed(error=str(exc))` (`conversation.py:487`) returns worker
  exception text to the client through the GET `error` field; the skills export sanitizes the
  same field (`skills.py:321`).
- **FU-2 (check-quality).** The MinIO upload (up to 120 seconds) runs while a DB transaction is
  held open (`conversation.py:384`, `research_export_builder.py:100-109`), and the export builds
  the whole bundle in memory although research-export §7 claims streaming.
- **FU-3 (SRS decision).** The restored sidebar Dashboard link contradicts [R33.02] ("not a
  global sidebar link") and workspace-scope AC-8, which is still ticked, and the AC-8 test was
  deleted in bc0dd362. Either amend [R33.02] and AC-8 or remove the link.
- **FU-4 (platform i18n).** Notification titles are hard-coded English in every sender; the
  frontend renders them raw (`NotificationCard.vue:60`), which breaks the `$t()` rule for zh-TW
  users.
- **FU-5 (UX decision).** Export date ranges are UTC days; for a UTC+8 class, activity before
  08:00 local is attributed to the previous day. Decide whether the API should take the
  viewer's timezone.
- **FU-6 (intent conflict, privacy).** The research bundle includes unreleased observations to
  any Project Owner (`observation_repo.py:223-246`), while [R28.03]/[R28.09] limit observation
  reads to the room creator. [R33.10] explicitly bundles observations, so the two requirements
  need reconciling.
- **FU-7 (check-security, privacy).** De-identification is structural only: message
  `content_md`, observation blocks and compaction summaries can carry names typed by
  participants or echoed by agents, and full room and workspace UUIDs appear in the bundle,
  against the letter of [R33.10]. Students in the pilot are partly minors.
- **FU-8 (test coverage).** No `pytest.mark.db` test executes any new dashboard or export query
  (`timeseries_for_rooms`, `participant_submission_counts`, `list_for_research_export`, the
  `list_for_rooms` repos), although teacher-dashboard §12 asked for db-tier tests. A static
  compile of the timeseries query with the asyncpg dialect is type-valid, so this is a coverage
  gap, not a found defect.
- **FU-9 (UX).** Navigating to a dashboard from a workspace card does not move the sidebar
  workspace switcher, so "New chat" targets the sidebar's workspace rather than the one on
  screen. e822af27 makes the sidebar the stated target, so this is an inconsistency, not a
  contract break.
- **FU-10 (known).** `research_export.py` imports the application layer directly (already
  research-export FU-4).
