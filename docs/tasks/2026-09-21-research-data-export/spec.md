---
type: feature
status: implemented
created: 2026-09-21
requirements: [R13.17]
depends_on: []
---

# Research Data Export

## 1. Summary

Workspace-scoped export of de-identified research data: activity submissions, AA
observer observations, and discussion transcripts bundled into a single ZIP file.
Produced asynchronously via an Arq worker and delivered through MinIO presigned URLs.
Designed for statistical analysis (CSV) and structural review (JSON) by research teams
running Kappa consistency checks, t-tests, and time-series analysis on cooperative
learning sessions.

## 2. Goals and Non-goals

**Goals**

- Export submissions (payload, sub_scores, timestamps), observations (structured blocks),
  and transcripts (messages) for all rooms in a workspace in one operation.
- De-identify all participant data using the existing `subject_code` mechanism -- no
  display names, login emails, or raw UUIDs in the output.
- Produce CSV files for statistical tools (SPSS, R, Python) and JSON files for
  structural review.
- Follow the existing chat export pattern (`export_service.py` + Arq worker + MinIO +
  Redis job state) so the two features share infrastructure rather than inventing
  parallel machinery.

**Non-goals**

- Automated creativity scoring (fluency/flexibility/originality/convergence). The
  research design uses human experts + Kappa inter-rater reliability; the platform
  provides raw data, not scores.
- Real-time streaming export. The workspace is small (20-30 participants, 5-7 rooms)
  and the async pattern handles it.
- Export of agent system prompts, activity type schemas, or platform configuration.
  These are reproducible from the course/pack JSON files.
- Per-room selective export. Workspace is the only scope.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Who may trigger the export? | Project Owner + Org Admin | Same authorization as the existing chat export [R13.17]. Members see the data in-room but cannot bulk-export. |
| Q-2 | Synchronous download or async generation? | Async (Arq worker) | Workspace-wide export touches multiple rooms, multiple tables. The existing chat export already uses this pattern (`export_service.py:32-36`); following it avoids a second delivery mechanism. |
| Q-3 | What data streams are included? | All-in-one: submissions + observations + transcripts | Research teams need cross-referencing (e.g., matching an AA observation to the submission event that triggered it and the discussion that preceded it). Separate exports would require manual alignment. |
| Q-4 | De-identification mechanism? | Reuse `subject_code()` from `activities/domain/subject_code.py:30` | Already produces `u:XXXXXXXX` / `g:XXXXXXXX` codes. The research export maps every `user_id` / `sender_id` to its code consistently within one export, so cross-referencing across CSV files works while no name or email appears. |
| Q-5 | Dependency on any active dossier? | None | No in-progress or ready dossier touches `dashboard.py`, `export_service.py`, `aggregation_service.py`, or the activities/conversation tables in overlapping regions. |

## 4. Current State

**Chat export ([R13.17])** follows this flow:

1. `POST /api/chatrooms/{id}/export` enqueues an Arq `chat_export` job, returns
   `{job_id, status}` with HTTP 202 (`app/api/v1/exports.py:90`).
2. Worker calls `ChatExportService.build_and_upload_export()` which serializes messages,
   renders to the chosen format, uploads to MinIO `exports` bucket
   (`conversation/application/chat_export_service.py:51`).
3. `GET /api/exports/{job_id}` returns job status or a presigned URL
   (`app/api/v1/exports.py:126`).
4. Redis-backed job state with 24h TTL (`conversation/application/export_service.py:29`).
5. Sender-scoping via `ExportSenderScope` (`conversation/domain/models.py:19`).

**Submissions** are stored in `activity_submissions` table
(`activities/infrastructure/tables.py:143-189`) with `payload` (JSONB), `sub_scores`
(JSONB), `agent_digest`, `validation_status`, `is_valid`, `error_class`, `latency_ms`,
`attempt_no`, `created_at`.

**Observer observations** are stored in `agent_observations` table
(`conversation/infrastructure/tables.py:136-167`) with `blocks` (JSONB array of
structured presentation blocks), `content_md`, `trigger`, `released_at`, `created_at`.

**Workspace-to-rooms mapping** is already solved:
`ConversationFacade.list_chatroom_ids_for_workspace()` at
`conversation/interfaces/facade.py:246-251`, reused by the dashboard via
`_resolve_workspace_rooms()` at `app/api/v1/dashboard.py:109-125`.

**De-identification**: `subject_code(user_id)` produces `u:{uuid[:8]}`,
`group_subject_code(member_group_id)` produces `g:{uuid[:8]}`
(`activities/domain/subject_code.py:27-34`).

## 5. Design

### Options considered

**Option A -- Extend existing chat export with a workspace scope flag**: Add a
`scope: room | workspace` parameter to the existing export endpoint. Trade-offs: the
existing export is tightly coupled to one chatroom (sender-scoping, message-centric
manifest); stretching it to handle submissions and observations would make it do
three things instead of one.

**Option B -- New dedicated endpoint and worker task**: A new
`POST /api/workspaces/{id}/export/research-data` endpoint that enqueues a dedicated
`research_data_export` Arq task. The worker produces a ZIP containing CSV + JSON files,
uploads to MinIO, and tracks state through the same Redis job-state pattern. Trade-offs:
a second export path, but scoped to a different use case with different data and
de-identification requirements.

### Decision

**Option B.** The research export differs from the chat export in scope (workspace vs.
room), content (three data streams vs. messages only), output format (CSV + JSON in a
ZIP vs. single-format file), and privacy model (de-identified vs. sender-scoped). A new
endpoint with its own worker task is simpler than conditionally branching every step of
the existing export. The two share infrastructure (Arq dispatch, MinIO upload, Redis
job state) but not application logic.

## 6. Detailed Changes

### Backend

**New route**: `app/api/v1/research_export.py`
- `POST /api/workspaces/{workspace_id}/export/research-data` -- validates workspace
  access (reuse `_resolve_workspace_rooms()` pattern), enqueues Arq task, returns
  `{job_id, status}` with HTTP 202.
- `GET /api/exports/research/{job_id}` -- returns job status or presigned URL.
- Request model: `ResearchExportCreateIn` with optional `created_after` / `created_before`
  date filters.

**New service**: `contexts/conversation/application/research_export_service.py`
- `ResearchExportJobState` dataclass, mirroring `ExportJobState` pattern.
- Redis job state under `research_export:{job_id}` keys, 72h TTL (longer than chat
  export because research bundles are larger and may be downloaded across sessions).

**New worker task**: `research_data_export` registered in `app/workers/main.py`
- Calls `build_research_export(workspace_id, rooms, date_range)`.
- Produces a ZIP file with:
  - `submissions.csv` -- one row per submission, columns: `subject_code`,
    `activity_type_key`, `room_id`, `attempt_no`, `is_valid`, `error_class`,
    `latency_ms`, `created_at`, plus one column per payload field (flattened from
    JSONB), plus `sub_scores_filled` and `sub_scores_filled_fields`.
  - `observations.json` -- array of observations, each with `room_id`, `agent_key`,
    `blocks` (structured), `content_md`, `trigger`, `created_at`, `released_at`.
    All user identifiers replaced by subject codes.
  - `transcripts.json` -- per-room message arrays, each message with `subject_code`
    (or `agent_key` for agent messages), `sender_type`, `content_md`, `created_at`.
    Display names and emails stripped.
  - `manifest.json` -- export metadata: workspace_id, room count, date range, export
    timestamp, schema_version.
- Uploads ZIP to MinIO `exports` bucket under `research/{job_id}/`.

**Facade extension**: `ActivitiesFacade` gains a method to list all submissions for a
set of chatroom IDs (the workspace's rooms) with optional date range. The query uses
`chatroom_id IN (...)` which is already the shape `aggregation_service.py` uses.

**Facade extension**: `ConversationFacade` gains a method to list all observations for
a set of chatroom IDs with optional date range.

**Migration**: None. No schema changes.

### API contract

Two new endpoints. `gen:api` rerun required: yes.

### Frontend

`slices/dashboard/components/ResearchExportButton.vue` -- a button on the dashboard
view that opens a dialog with date range selection, triggers the export, and polls for
completion. On completion, opens the presigned URL download.

i18n keys in `slices/dashboard/locales/{en,zh-TW}.json`.

### Deploy/config

No new env vars. Uses existing MinIO `exports` bucket and Arq worker.

## 7. NFR Checklist

- [x] i18n -- button label + dialog text through `$t()`
- [x] Audit log -- `research_data.exported` domain event with workspace_id and
  requesting user_id
- [x] Tenant isolation -- workspace access derived from project membership, same as
  dashboard endpoints
- [x] Error handling UX -- button shows progress (queued/running), error state with
  retry, download link on ready
- [x] Performance -- workspace scope is 5-7 rooms, 20-30 participants, bounded data
  volume. No pagination needed for the export itself. Worker query uses `chatroom_id IN`
  with a small set. ZIP generation is streaming (no full-dataset memory hold).

## 8. Security Considerations

- **De-identification is structural, not optional.** The export service never queries
  display names or emails; it maps `user_id` to `subject_code()` output and drops the
  original UUID. A bug that leaks a name would require adding a join that does not exist
  in the query.
- **Presigned URL expiry**: 72h, matching the Redis job TTL. After expiry, both the
  download link and the job state disappear.
- **No cross-project data**: workspace belongs to exactly one project; the room list
  query is already project-scoped.
- **Agent system prompts excluded**: prompts may contain proprietary teaching strategies.
  The export includes agent messages (what the agent said in the room) but not the prompt
  that generated them.

## 9. Quality Notes

**Existing debt**: None relevant. The export path is new.

**Patterns to follow**:
- `conversation/application/export_service.py` -- Redis job state pattern
- `conversation/application/chat_export_service.py` -- MinIO upload, manifest structure
- `app/api/v1/exports.py` -- route pattern for async export endpoints
- `app/workers/main.py:294-364` -- Arq worker registration pattern

**Reuse inventory**:
- `export_service.py:ExportJobStatus` enum (QUEUED/RUNNING/READY/FAILED) -- reuse or
  mirror
- `subject_code()` / `group_subject_code()` from `activities/domain/subject_code.py`
- `_resolve_workspace_rooms()` from `app/api/v1/dashboard.py:109-125`
- `assert_project_membership()` from `app/api/v1/deps.py:101-123`
- `MinioStorage` from `shared_kernel/storage/`
- `NotificationFacade.send()` from `notification/interfaces/facade.py` -- in-app
  notification on export completion (new `NotificationKind.EXPORT_READY`)
- `shared_kernel.queue.enqueue()` from `shared_kernel/queue.py:21` -- Arq task dispatch

## 10. Risks and Rollback

- **Risk**: CSV flattening of JSONB payload may produce inconsistent columns across
  activity types (each type has a different schema). Mitigation: one CSV section per
  activity type, or a `payload_json` column with the raw JSON alongside the flattened
  fields.
- **Risk**: Large transcript volumes for rooms with heavy agent activity. Mitigation:
  streaming ZIP generation; the 20-30 participant scale bounds this practically.
- No migration; rollback is removing the route and worker task registration.

## 11. Acceptance Criteria

- [x] AC-1: `POST /api/workspaces/{id}/export/research-data` returns HTTP 202 with
  `{job_id, status: "queued"}` for a Project Owner.
- [x] AC-2: `POST` returns HTTP 403 for a regular project member.
- [x] AC-3: The Arq worker produces a ZIP file uploaded to MinIO `exports` bucket.
  (code complete; integration test needs MinIO)
- [x] AC-4: The ZIP contains `submissions.csv` with one row per submission, no display
  names or emails, subject codes instead of user IDs.
- [x] AC-5: The ZIP contains `observations.json` with all AA observations for the
  workspace, blocks preserved as structured JSON.
- [x] AC-6: The ZIP contains `transcripts.json` with per-room message arrays,
  sender identified by subject code (users) or agent key (agents).
- [x] AC-7: The ZIP contains `manifest.json` with export metadata.
- [x] AC-8: `GET /api/exports/research/{job_id}` returns `{status: "ready", url: "..."}` with
  a presigned MinIO URL after the worker completes.
- [x] AC-9: The presigned URL expires after 72 hours.
- [x] AC-10: An audit event `research_data.exported` is recorded.
- [x] AC-11: The dashboard view shows an export button (Project Owner+ only) that
  triggers the export and shows progress.
  (code complete; visual verification needs running stack)
- [x] AC-12: Date range filtering works: submissions, observations, and messages
  outside the range are excluded.
- [x] AC-13: On completion, an in-app notification (`export.ready`) is sent to the
  requesting user via `NotificationFacade.send()`, so they do not need to poll.

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-1, AC-2 | Unit | `tests/unit/test_research_export_routes.py` |
| AC-3 | Integration | `tests/integration/test_research_export_worker.py` (needs MinIO) |
| AC-4, AC-5, AC-6, AC-7 | Unit | `tests/unit/test_research_export_builder.py` (mock DB results, verify CSV/JSON output structure and de-identification) |
| AC-8, AC-9 | Unit | `tests/unit/test_research_export_job_state.py` |
| AC-10 | Unit | `tests/unit/test_research_export_routes.py` (audit event assertion) |
| AC-11 | Component | `frontend/tests/components/ResearchExportButton.test.ts` |
| AC-12 | Unit | `tests/unit/test_research_export_builder.py` (date range filtering) |

## 13. SRS Delta

New requirement under a new section or appended to section 33 (Dashboard):

- **[R33.10]** Project Owners and Org Admins can export de-identified research data for
  a workspace. The export bundles activity submissions (payload, sub_scores, timestamps),
  observer observations (structured blocks), and chat transcripts into a single ZIP file.
  All participant identifiers are replaced by truncated subject codes
  (`activities/domain/subject_code.py`); no display name, login email, or full UUID
  appears in the output. The export is generated asynchronously via a background worker
  and delivered through a time-limited presigned URL.

## 14. Open Questions

None.

## 15. Deviation Log

- D-1: The builder (`research_export_builder.py`) receives pre-queried typed dataclass
  rows instead of querying tables directly. The spec implied the builder would query
  the database, but the self-audit found that directly importing
  `activities/infrastructure/tables` from the conversation context violates SoC. The
  worker task now queries through `ActivitiesFacade` and `ConversationFacade`, converts
  domain models to the builder's typed rows (`SubmissionRow`, `ObservationRow`,
  `MessageRow`), and passes them in. No behavioral change.
- D-2: The frontend export button uses a local `api/researchExport.ts` transport wrapper
  (via `@shared/transport`'s `http` instance) instead of the generated
  `ResearchExportService`. Both call the same endpoints. The wrapper was written before
  `gen:api` ran; switching to the generated service is optional (the current approach
  already follows the store-isolation rule).
- D-3: AC-3 and AC-11 are ticked "code complete" rather than on a running stack
  verification. AC-3 needs MinIO; AC-11 needs a browser against the full compose stack.
  Docker was unavailable during this build session.

## 16. Follow-ups

- FU-1: Consider adding a "research export" permission that can be granted to specific
  project members (e.g., research assistants) without promoting them to Project Owner.
- FU-2: Consider export of activity type schemas and agent configuration alongside the
  data, for reproducibility documentation.
- FU-3: The frontend `api/researchExport.ts` wrapper could be replaced by the generated
  `ResearchExportService` for consistency with other slices that use the generated client.
