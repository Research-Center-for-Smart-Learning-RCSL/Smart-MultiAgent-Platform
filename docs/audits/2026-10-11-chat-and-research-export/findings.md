---
type: audit
status: closed
created: 2026-10-11
requirements: [R13.17, R33.10]
---

# Audit: Chat history export and research data export

## 1. Scope

**Area.** The chat history export end to end: `POST /api/chatrooms/{id}/export` and
`GET /api/exports/{job_id}` (`backend/app/api/v1/exports.py`), the Redis job state
(`contexts/conversation/application/export_service.py`), the archive builder and renderer
(`chat_export_service.py`), the `chat_export` Arq task (`app/workers/tasks/conversation.py`),
the MinIO client and exports bucket bootstrap, and the frontend modal, composable and shared
poller (`ChatroomExportModal.vue`, `useChatroomExport.ts`, `usePolling.ts`). The research data
export (`app/api/v1/research_export.py`, `ResearchExportButton.vue`) was included at the
requester's choice, but only to check whether the defects recorded by the 2026-10-05 audit are
still present and whether the chat export's root cause also applies to it; it was not
re-audited from scratch.

**Intent sources.** `REQUIREMENTS.md` [R13.17] (line 734) and the API table (lines 1693-1694,
"Download link (MinIO signed URL, 24 h)"); permission-matrix row 19 (lines 198-208);
`docs/UI/07-conversation.md` §3.9 (lines 792-886); the dossiers
`docs/tasks/2026-07-22-chat-export-authz-and-polling/spec.md` and
`docs/tasks/2026-10-05-chat-export-guest-narrowing/spec.md`; for the research export, [R33.10]
and `docs/audits/2026-10-05-teacher-dashboard-and-research-export/findings.md`. Intent
coverage is adequate. One intent conflict is called out under F-2.

**Depth.** Thorough, with runtime reproduction. Two investigation agents covered the backend
(state and lifecycle, error paths, concurrency, boundary inputs) and the frontend and API
contract (event flow, contract desync, boundary inputs, isolation). Every surviving candidate
was then re-traced against the code by the auditor in one adversarial pass. The primary
finding was reproduced against a running test stack (section 2).

## 2. Coverage

**Runtime reproduction.** The compose test stack (`docker-compose.yml` + `compose.test.yml`)
was brought up with `backend-web` and `backend-worker` images built on 2026-10-07 09:16 UTC.
The only export-related commit after that build is 75647465 (sender label lookup), which does
not touch the job lifecycle, storage or URL path. Via the REST API as the seeded user, a room
was created, one message posted, and one export run in each of the three formats. All three
jobs reached `ready` within about five seconds. Each returned a URL of the form
`http://minio:9000/exports/<job_id>/<file>?X-Amz-...&X-Amz-Expires=900`. Fetching it from the
host failed with "The remote name could not be resolved: 'minio'". Fetching the same URL from
inside the compose network returned HTTP 200: `text/markdown` with 178 bytes, and
`application/pdf` with 90,734 bytes. Neither response carried a `Content-Disposition` header.
The backend pipeline therefore works, and the defect sits entirely in delivery. Nginx and the
built frontend were not brought up, so the browser click itself was not observed. Its
behavior follows from the URL, which the API returns verbatim.

**Read in full.** `exports.py`, `export_service.py`, `chat_export_service.py`, the `chat_export`
and `research_data_export` tasks, `minio_client.py`, `deploy/compose/nginx/conf.d/smap.conf`,
`research_export.py`, `useChatroomExport.ts`, `ChatroomExportModal.vue`, `usePolling.ts`,
`frontend/e2e/14-export-attachments.spec.ts`, and UI doc §3.9.

**Sampled.** `MessageRepository.all_for_chatroom`, `access.py` (row-19 scope only),
`SButton.vue` (anchor rendering only), `ResearchExportButton.vue`, the `markdown/sanitize.py`
allow-lists, and `app/workers/main.py` (registration and `job_timeout` only).

**Not covered, and therefore not claimed clean.**

- The PDF's visual rendering, including CJK glyph coverage. The run's message text was
  mangled by the PowerShell 5.1 test script's encoding before it was sent (see section 4), so
  CJK rendering was not observed.
- Behavior at scale: exports over 10,000 messages, and concurrent exports.
- The research export's de-identification and data shape beyond what the 2026-10-05 audit
  recorded.
- Other features that use the same presigned-URL pattern. These are listed under FU-6, not
  audited.

## 3. Findings

## F-1: The download link points at the Docker-internal MinIO host, so no export can be downloaded

- **Severity**: critical
- **Verdict**: confirmed (reproduced at the API; see section 2)
- **Evidence**:
  - `backend/app/api/v1/exports.py:137-142` returns `presigned_get()` unchanged.
  - `backend/shared_kernel/storage/minio_client.py:44-50` signs against `section.endpoint`.
  - `backend/app/config/settings.py:101,107` default that endpoint to `minio:9000` with
    `use_tls=False`. No staging or prod overlay overrides it.
  - `frontend/src/slices/conversation/components/ChatroomExportModal.vue:19-23` binds the URL
    straight into `SButton :as="'a'"`, which renders it as `href`
    (`frontend/src/shared/ui/SButton.vue:40`).
  - Nginx already exposes a `/minio-assets/` proxy that preserves the signed `Host`
    (`deploy/compose/nginx/conf.d/smap.conf:172-184`). Only canvas uses it, through its
    rewrite helper (`backend/app/api/v1/canvas.py:39-47`).
  - The research export returns the same raw URL (`backend/app/api/v1/research_export.py:130-134`).
- **Failure scenario**:
  1. On smap.rcsl.online, a user exports a room and the modal shows "Export ready!".
  2. Clicking "Download export" navigates the current tab to
     `http://minio:9000/exports/...`. The browser cannot resolve `minio`, so the tab lands on
     a DNS error page and the chatroom is unloaded.
  3. Every format, every role and every room fail identically. The research export's
     `window.open` leads to the same unresolvable host.
- **Blast radius**: The whole feature. This explains the report that export is "entirely
  broken": the job succeeds, but its output is unreachable. Rewriting the URL to
  `/minio-assets/` is necessary but not sufficient. The objects are stored as `text/markdown`,
  `application/json` and `application/pdf` (`chat_export_service.py:243-253`), and no
  `response_content_disposition` is passed, so once the URL is reachable the browser would
  render them inline in place of downloading them (see F-7).
- **Intent source**: [R13.17]; `REQUIREMENTS.md:1694`; UI doc §3.9 line 886.

## F-2: The link expires after 15 minutes, while the UI and the requirements promise 24 hours

- **Severity**: major
- **Verdict**: confirmed (`X-Amz-Expires=900` observed at runtime)
- **Evidence**:
  - `exports.py:141` signs with `expires=timedelta(minutes=15)`.
  - `frontend/src/slices/conversation/locales/en.json:191` reads "Link expires in 24 hours.";
    `zh-TW.json:191` carries the same promise.
  - Polling stops at `ready` (`useChatroomExport.ts:16,25`; `usePolling.ts:65-68`), so the URL
    is minted once and never refreshed.
- **Failure scenario**: The user leaves the ready modal open for 20 minutes, then clicks
  Download. MinIO answers 403 "Request has expired", although the object itself lives for
  24 hours. The only recovery is to run a new export.
- **Blast radius**: Any download not clicked within 15 minutes, once F-1 is fixed.
- **Intent source**: `REQUIREMENTS.md:1694` and UI doc lines 864 and 886 (24 h).
  **Intent conflict**: `docs/tasks/2026-07-22-chat-export-authz-and-polling/spec.md:427-428`
  describes the 15-minute URL lifetime as existing behavior without amending the 24-hour
  statements. The fix must choose between a longer signature and re-minting the URL on
  click.

## F-3: An export that takes longer than three minutes leaves the modal spinning forever

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**:
  - `useChatroomExport.ts:23-25` polls 60 times at the default 3-second interval
    (`usePolling.ts:36`), about 180 seconds in total.
  - `usePolling.ts:57` returns silently once `attempts > maxAttempts`, with no callback, so
    `exportJob` stays `queued` or `running`.
  - `usePolling.ts:70-74` treats every error as transient. A permanent 403, a 404
    (`ExportJobNotFound` after the Redis key is gone) or a 500 is retried until exhaustion and
    then ends in the same state.
  - The worker allows 600 seconds (`app/workers/main.py:363`).
  - The research export does show a timeout toast (`ResearchExportButton.vue:74`).
- **Failure scenario**: The shared Arq queue is busy with orchestration turns, so a job sits
  queued for four minutes, or a large PDF renders for four minutes. The modal keeps showing the
  indeterminate bar with no Retry and no message, even after the job becomes `ready`.
- **Blast radius**: Every slow export, and every export whose status endpoint starts failing
  permanently.
- **Intent source**: UI doc §3.9 steps 4-6 (the flow ends in a ready or a failed state).

## F-4: Attachment files are never included in the export

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**:
  - `chat_export_service.py:149-159` serializes attachment metadata only: filename, mime,
    size, internal `minio_path` and status.
  - The markdown and PDF renderers list filenames only (`:278-281`, `:312-315`). No archive,
    folder or "missing" note is produced.
  - The module docstrings still describe the intended behavior: `export_service.py:4-5`
    ("plus an attachments folder").
- **Failure scenario**: A room contains a PDF a student uploaded. The JSON export carries
  `"minio_path": "chat-uploads/..."`, which the reader cannot fetch, and the markdown export
  says only "_Attachments: report.pdf_". The file is not in the archive.
- **Blast radius**: Every room with attachments. The JSON form also discloses internal bucket
  paths that are of no use to the reader.
- **Intent source**: [R13.17] ("JSON + a folder of attachments (attachments are regenerated
  from MinIO if still alive; missing ones are noted)").

## F-5: A job cancelled by the worker timeout stays "running" for 24 hours

- **Severity**: minor
- **Verdict**: confirmed (mechanism traced; needs an export over 600 seconds)
- **Evidence**:
  - `app/workers/main.py:363` sets `job_timeout=600`.
  - Arq enforces the timeout by cancelling the coroutine. `CancelledError` is a
    `BaseException`, so `except Exception` at `app/workers/tasks/conversation.py:272` does not
    catch it and `mark_failed` never runs.
  - `mark_running` re-armed the 24-hour TTL (`export_service.py:29,92-96`).
  - The WeasyPrint render runs in `asyncio.to_thread` (`chat_export_service.py:249`) and cannot
    be cancelled.
- **Failure scenario**: A PDF of a large room renders for 11 minutes. The API reports `running`
  until the Redis key expires a day later, and the render thread keeps consuming CPU after the
  job has been abandoned.
- **Blast radius**: Rare at current room sizes. The research export has the same pattern
  (2026-10-05 audit, F-14).
- **Intent source**: UI doc §3.9 step 6.

## F-6: Rooms over 50,000 messages silently lose their newest messages

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**:
  - `chat_export_service.py:43,117-123` passes `limit=_EXPORT_MAX_MESSAGES` (50,000).
  - `message_repo.py:395-397` orders `created_at ASC` before applying the limit.
  - The manifest carries no truncation marker (`chat_export_service.py:163-171`).
- **Failure scenario**: An "All Messages" export of a 60,000-message room ends at message
  50,000. The most recent 10,000 messages are missing, and nothing in the archive or the UI
  says so.
- **Blast radius**: Only very large rooms. The loss is silent, and it falls on the most recent
  history.
- **Intent source**: [R13.17] (a room's history).

## F-7: Download replaces the chat tab instead of opening a new tab

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**:
  - `ChatroomExportModal.vue:19-23` renders a bare `<a href>` with no `target`, `rel` or
    `download` attribute (`SButton.vue:40`).
  - Stored content types are inline-renderable and no attachment disposition is requested
    (`chat_export_service.py:243-253`, `exports.py:138-142`).
- **Failure scenario**: Once F-1 is fixed, clicking Download navigates the SPA tab to the raw
  markdown or JSON text, or to the browser's PDF viewer. The user leaves the chatroom and must
  navigate back, which re-establishes the socket.
- **Blast radius**: Every download.
- **Intent source**: UI doc §3.9 line 886 ("opens the signed URL in a new tab").

## F-8: Invalid input and worker failures are both reported as "Failed to start export", and Retry repeats the same input

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**:
  - The submit guard checks only that the custom dates are non-empty
    (`ChatroomExportModal.vue:129`); `end < start` reaches the API and returns 422
    (`exports.py:55-56`).
  - `useChatroomExport.ts:41-44` maps every create error to the `failed` state plus the
    "Failed to start export." toast.
  - The failed state reuses the same string for a job that started and then failed in the
    worker (`ChatroomExportModal.vue:34-44`), and the API's `error` field is never displayed.
  - Retry calls `submit` with the unchanged form values (`:39-44`, `:195-202`).
- **Failure scenario**: The user picks an end date before the start date. They see "Failed to
  start export.", and each Retry sends the same invalid range. The form is only reachable
  again by closing and reopening the modal.
- **Blast radius**: Custom-range users, and anyone diagnosing a worker failure.
- **Intent source**: UI doc §3.9 step 6 ("Export failed. Unable to generate the archive.",
  with Cancel and Retry).

## F-9: Reopening the modal while a create request is in flight shows the old job in the new form

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**:
  - `ChatroomView.vue:1701-1706` calls `resetExport()` on open, which clears `activeJobId`.
  - The pending `createExport` then resolves and writes `activeJobId` and `exportJob`
    (`useChatroomExport.ts:37-40`) without checking whether it was superseded.
  - Nothing disables the submit button while the request is pending. Two quick clicks create
    two jobs: both calls see `activeJobId === null`, so neither cancels the other, and the
    losing job's poller runs to exhaustion.
- **Failure scenario**: The user submits, closes the modal immediately, and reopens it. The
  fresh configuration form is replaced by the previous job's progress state. This is the
  2026-07-22 F-16 symptom in the create window, which that fix did not cover.
- **Blast radius**: Cosmetic confusion, plus extra worker jobs and duplicate
  `message.exported` audit rows on double-submit.
- **Intent source**: `docs/tasks/2026-07-22-chat-export-authz-and-polling/spec.md` (F-16:
  a superseded job must not render into the fresh form).

## 4. Refuted Candidates

- **WeasyPrint missing from the image.** Refuted at runtime: the PDF export produced a valid
  90,734-byte `%PDF-1.7` object.
- **API/worker queue mismatch, or an unregistered task.** Refuted: both use arq's default
  queue, `chat_export` is registered (`app/workers/main.py:300`), and all three formats
  completed at runtime.
- **Empty or partial POST body rejected.** Refuted: `body: ExportCreateIn | None = None`
  yields defaults, and the frontend's `{format, date_range, start?, end?}` parses.
- **CJK text corrupted in the export.** The runtime archive contained mojibake, but the bytes
  were already mangled when sent: PowerShell 5.1 read the test script as the ANSI code page. It
  is a test artifact, not a product defect.
- **Sender rendered as "user (160d3084)" in place of a name.** The seed user's
  `display_name` is NULL, so the fallback label is the designed behavior
  (`chat_export_service.py:256-268`).
- **Generated client drift.** `ExportCreateIn`, `ExportCreateOut` and `ExportStatusOut` match
  `exports.py` and `openapi.json`.

## 5. Hand-off

Triaged with the requester on 2026-10-11. Every finding will be fixed, grouped by root cause
into three dossiers:

- The delivery dossier (named `browser-file-delivery` once spec analysis widened it to all six
  file-delivery paths) carries F-1, F-2 and F-7. It also carries the same raw-URL defect on
  the research export path, and the shared URL-publishing helper that FU-6 calls for.
- The F-2 intent conflict was decided in favor of re-minting the presigned URL when the user
  clicks Download. The signature stays short-lived. The UI copy and the 24-hour statements in
  `REQUIREMENTS.md:1694` and UI doc §3.9 are reworded to describe how long the archive is
  retained, not how long the link lives.

Dossier paths below are the planned slugs. Each becomes a link once `/spec` creates it.

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | fix (2026-10-11) | [`docs/tasks/2026-10-11-browser-file-delivery/`](../../tasks/2026-10-11-browser-file-delivery/spec.md) |
| F-2 | fix (2026-10-11) | [`docs/tasks/2026-10-11-browser-file-delivery/`](../../tasks/2026-10-11-browser-file-delivery/spec.md) |
| F-3 | fix (2026-10-11) | [`docs/tasks/2026-10-11-chat-export-modal-states/`](../../tasks/2026-10-11-chat-export-modal-states/spec.md) |
| F-4 | fix (2026-10-11) | [`docs/tasks/2026-10-11-chat-export-archive-content/`](../../tasks/2026-10-11-chat-export-archive-content/spec.md) |
| F-5 | fix (2026-10-11) | [`docs/tasks/2026-10-11-chat-export-archive-content/`](../../tasks/2026-10-11-chat-export-archive-content/spec.md) |
| F-6 | fix (2026-10-11) | [`docs/tasks/2026-10-11-chat-export-archive-content/`](../../tasks/2026-10-11-chat-export-archive-content/spec.md) |
| F-7 | fix (2026-10-11) | [`docs/tasks/2026-10-11-browser-file-delivery/`](../../tasks/2026-10-11-browser-file-delivery/spec.md) |
| F-8 | fix (2026-10-11) | [`docs/tasks/2026-10-11-chat-export-modal-states/`](../../tasks/2026-10-11-chat-export-modal-states/spec.md) |
| F-9 | fix (2026-10-11) | [`docs/tasks/2026-10-11-chat-export-modal-states/`](../../tasks/2026-10-11-chat-export-modal-states/spec.md) |

## 6. Out-of-scope Observations

- **FU-1 (check-security, SSRF).** The PDF path calls `HTML(string=html)` with no
  `url_fetcher` (`chat_export_service.py:326`), while the sanitizer allows `<img src>`
  (`shared_kernel/markdown/sanitize.py:47,70`). A message or an agent reply containing
  `![](http://<internal-service>/...)` makes the worker, which sits on the internal networks,
  issue that GET during any PDF export of the room. This is server-side request forgery (SSRF).
- **FU-2 (check-security).** `mark_failed(error=str(exc))` (`app/workers/tasks/conversation.py:277`)
  returns raw exception text, including SQL and S3 error detail, through `GET /api/exports/{id}`.
  The same class was recorded for the research export (2026-10-05 FU-1).
- **FU-3 (check-quality).** The comment at `chat_export_service.py:173-176` claims `flush()`
  releases the DB connection. Inside `session.begin()` it does not, so the connection stays
  idle in transaction through the render and the up-to-60-second upload. Separately, an
  audit-insert or commit failure after the upload marks a delivered export as failed.
- **FU-4 (UX decision).** Custom date ranges are resolved as UTC days (`exports.py:66-69`), so
  for UTC+8 users the window is shifted by eight hours. This is the same open decision as
  2026-10-05 FU-5.
- **FU-5 (test coverage).** `frontend/e2e/14-export-attachments.spec.ts:41-45` passes on either
  "Download export" or "Failed to start export" and never inspects the `href`. Unit tests mock
  the URL. No test can detect F-1, which is why it reached staging green.
- **FU-6 (same root cause, other features).** The raw `minio:9000` presigned URL is also
  returned by attachment download (`attachment_service.py:307`), the admin audit export
  (`admin_audit.py:152`) and the skill bundle export (`skills.py:1760`). These were not
  reproduced here. A shared fix in the MinIO client, or a single URL-publishing helper,
  would cover all of them. Attachment download is likely broken in the same way and warrants
  its own check.
- **FU-7 (process).** None of the research export defects from
  `docs/audits/2026-10-05-teacher-dashboard-and-research-export/findings.md` (F-5 to F-15) have
  been fixed. The six task dossiers its hand-off table names
  (`2026-10-05-research-export-delivery`, `-data-shape`, `-dashboard-and-export-authz`, and
  others) were never created, so that audit's `reviewed` status overstates its hand-off. In
  particular, its F-6 (the popup-blocked `window.open`) still holds at
  `ResearchExportButton.vue:59`, and F-1 of this audit now also applies to that path.
