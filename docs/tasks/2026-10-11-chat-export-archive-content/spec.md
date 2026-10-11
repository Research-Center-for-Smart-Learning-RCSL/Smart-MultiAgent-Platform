---
type: bugfix
status: approved
created: 2026-10-11
requirements: [R13.17]
depends_on: [2026-10-11-browser-file-delivery, 2026-10-11-chat-export-modal-states]
---

# Chat export archive: attachments, complete history, and failure on timeout

## 1. Summary

The chat export archive does not contain what [R13.17] says it does.

- No attachment file is ever included. The archive lists filenames and an internal
  `chat-uploads/...` path the reader cannot use.
- Rooms over 50,000 messages silently lose their newest messages.
- A job cancelled by the worker timeout is never marked failed, so the UI reports it as running
  for a day.

This dossier makes the export stream its transcript to a temporary file with no message cap for
Markdown and JSON. It refuses a PDF request that is too large before queueing it. It packages the
transcript with an `attachments/` folder into a ZIP when the user asks for attachments, and
records each missing attachment. It marks a cancelled job failed.

It originates from `docs/audits/2026-10-11-chat-and-research-export/findings.md` F-4, F-5 and
F-6. It also closes the batching half of FU-3 of
`docs/tasks/2026-07-22-chat-export-authz-and-polling/spec.md`, and adds a concurrency guard
toward its quota half, because removing the message cap would otherwise widen that open finding.

## 2. Observed vs Expected

**Observed.**

- **F-4.**
  - `chat_export_service.py:149-159` serializes attachment metadata only, including the internal
    `minio_path`.
  - The Markdown and PDF renderers print filenames (`:278-281`, `:312-315`).
  - No archive or folder is produced. The module docstring still promises "an attachments
    folder" (`export_service.py:4-5`).
- **F-6.**
  - `messages.all_for_chatroom(..., limit=_EXPORT_MAX_MESSAGES)` (`chat_export_service.py:43,117-123`)
    orders `created_at ASC` and then applies the limit (`message_repo.py:395-397`). The newest
    rows beyond 50,000 are dropped.
  - The manifest has no truncation marker (`chat_export_service.py:163-171`).
  - The whole manifest, then the whole rendered artifact, is held in memory, and each message
    makes two further queries (`:126-128`).
- **F-5.**
  - The job runs under the worker-wide `job_timeout = 600` (`app/workers/main.py:363`), and arq
    enforces it by cancelling the coroutine.
  - `CancelledError` is a `BaseException`, so the `except Exception` at
    `app/workers/tasks/conversation.py:272` does not run `mark_failed`.
  - `mark_running` re-armed the 24-hour record TTL (`export_service.py:29,92-96`), so the job
    reads `running` until the record expires.

**Expected.**

- [R13.17]: "Users can export a chat room's history as JSON + a folder of attachments
  (attachments are regenerated from MinIO if still alive; missing ones are noted)."
- The flow ends in a ready or a failed state (`docs/UI/07-conversation.md` §3.9 step 6).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which exports include attachment files? | A new form control, "Include attachments", checked by default, and the API field `include_attachments: bool = True`. When it is set, the result is a ZIP holding the transcript in the chosen format, `attachments/<message-id>/<filename>`, and a `README.txt` naming any omitted files. When it is clear, the result is the single transcript file as today. | Chosen at the 2026-10-11 spec session. It satisfies [R13.17] for every format, and it keeps a light single-file export for users who only want the text. The API default matches the SRS. |
| Q-2 | What counts as a missing attachment, and how is it recorded? | An attachment is missing when its status is `expired` or `quarantined` (`domain/models.py:46-49`), when its object no longer exists (stat returns `None`), or when including it would exceed the size budget in Q-3. Each missing attachment gets `"archived": false, "omitted_reason": "expired"`, `"quarantined"`, `"missing"` or `"size_budget"` in the JSON. The Markdown and PDF transcripts show an italic note, and `README.txt` lists it. `minio_path` is removed from every format, and archived files get `"archive_path"`. | [R13.17]'s "missing ones are noted". The internal path is useless to a reader and discloses storage layout. A quarantined file is a scan verdict and must never be shipped. |
| Q-3 | Size budget for attachments in one export. | 2 GiB of attachment bytes per archive, configurable as `SMAP_EXPORT_MAX_ATTACHMENT_BYTES`. Files are added in transcript order until the next one would exceed the budget. The rest are omitted with `size_budget`. | One TUS attachment can be 1 GiB (`attachment_service.py:53`). The bound keeps the archive downloadable and the worker's disk use predictable. In-order filling keeps the earliest context intact. |
| Q-4 | Rooms over 50,000 messages. | Markdown and JSON have no message cap. Messages are read in keyset pages of 1,000 by `(created_at, id)` and written incrementally to a temporary file. PDF keeps a cap of 5,000 messages: `POST /export` counts the window under the caller's sender scope and returns 422 `export_too_large_for_pdf` with the count, which the modal shows as an inline form error suggesting a narrower range or another format. | Chosen at the 2026-10-11 spec session. Text formats stream cheaply. WeasyPrint lays out the whole document in memory, and the staging worker has a 1 GiB limit (`docker-compose.staging.yml:227`). Refusing up front is honest, where truncating would repeat F-6's silent loss. |
| Q-5 | Making a cancelled job end in `failed`. | The task catches `asyncio.CancelledError`, records `mark_failed(error="timed out")` under `asyncio.shield`, and re-raises. `chat_export` is registered as `func(chat_export, name="chat_export", timeout=CHAT_EXPORT_TIMEOUT_S, max_tries=1)` with 1,800 seconds. | Re-raising preserves arq's cancellation contract. `max_tries=1` stops a re-run after a worker shutdown from producing a second archive and a second `message.exported` audit row, the pattern `wakeup_agent` already uses (`main.py:302-310`). 1,800 seconds allows a 2 GiB attachment copy, and the modal's "Check again" state (`2026-10-11-chat-export-modal-states` Q-1) covers a job that outlives the polling window. |
| Q-6 | Removing the message cap widens 2026-07-22 FU-3 (no export quota). Is anything added? | Yes. `POST /export` returns 409 `export_in_progress`, with the existing job id, while the caller already has a `queued` or `running` export for the same room. The guard is a Redis key `chat_export:active:{user}:{room}` set at create time and cleared on ready or failed, with the job TTL as a backstop. Edits and attachments are fetched per page with batched queries. | Without it, one user could queue unbounded full-history exports with attachments in parallel. One active job per (user, room) bounds that without a global quota design. The modal can resume polling the returned job id. A full per-user rate limit remains FU-1. |
| Q-7 | Where do temporary files live? | `tempfile.TemporaryDirectory()` on the worker's container filesystem, removed in `finally`. Upload uses the SDK's multipart `fput_object` (`minio_client.put_file`, `minio_client.py:106-125`). | `backend-worker` has no tmpfs (`docker-compose.yml:153-179`, `docker-compose.staging.yml:205-228`), so `/tmp` is disk-backed rather than RAM-backed, which the 1 GiB memory limit requires. `put_file` already exists and streams. |
| Q-8 | Dependencies. | `depends_on: [2026-10-11-browser-file-delivery, 2026-10-11-chat-export-modal-states]`, both overlap prerequisites. | `browser-file-delivery` sets the download filename and extension per format in `exports.py` and stores `room_name` in `ExportJobState`. This dossier adds the `.zip` variant to that same logic. `chat-export-modal-states` rewrites `ChatroomExportModal.vue` and `useChatroomExport.ts`, where this dossier adds the checkbox, the 409 resume, and the PDF-size error. |

## 4. Reproduction

Preconditions: the compose test stack, and a room the seeded user owns.

1. **F-4.** Upload a PNG attachment with a message, then export as JSON. The downloaded file is a
   single `manifest.json` whose attachment entry has `minio_path` and no file. No folder exists.
2. **F-6.** Seed 50,010 messages directly in `smap_test` (`INSERT ... SELECT generate_series`)
   and export Markdown. The transcript ends at message 50,000, and nothing says so.
3. **F-5.** Temporarily set `job_timeout` to 5 seconds in a local worker and export a room large
   enough to exceed it. `GET /api/exports/{id}` reads `running`, and it still does after the
   worker logs the timeout.

## 5. Root Cause Analysis

- **F-4.** The builder was written as a single-manifest serializer (`chat_export_service.py:125-171`).
  Attachment bytes were never fetched, so nothing could be packaged. The docstring describes the
  requirement, and the code never implemented it. The root cause is a missing feature path, not
  a regression.
- **F-6.** The builder loads the full result set into memory, so it needs a cap for memory
  safety (`:42-43`). The cap is applied to an ascending query. The root cause is the
  in-memory design. The ordering only decides which end is lost.
- **F-5.** The failure handler catches `Exception` only (`conversation.py:272-278`), and the job
  inherits a timeout it has no handler for. The root cause is the missing cancellation path.
  The aggravating factor is a re-armed 24-hour record.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every chat export with attachments (all of them omit files today), every room
over 50,000 messages, and every job that times out. Already-produced archives expire within
24 hours, so there is nothing to repair.

**Sibling suspects.**

- **`research_data_export`.** Same `except Exception` (`conversation.py:482-488`). Already
  recorded as 2026-10-05 F-14, routed to `browser-file-delivery` FU-2. Not fixed here.
- **`skill_export_bundle`.** `except Exception` (`skills.py:315-327`). It runs at the default
  timeout, and bundles are bounded at 64 MiB (`bundle_service.py:63-64`). Cleared as unlikely to
  time out, and recorded as FU-2.
- **The admin audit CSV.** Synchronous, with no worker. Cleared.

## 7. Fix Design

1. **Repository.**
   - Add `MessageRepository.iter_for_export(chatroom_id, *, created_after, created_before,
     own_user_id, page_size=1000)`, an async generator using keyset pagination on
     `(created_at, id)` with the same predicate as `all_for_chatroom`
     (`message_repo.py:353-399`, including row 19's positive sender-type list). It is served by
     `ix_messages_chatroom_created` (`alembic/versions/0017_messages.py:74-75`).
   - Add `count_for_export(...)`, using the same predicate, for Q-4.
   - Remove `all_for_chatroom`'s `limit` parameter once nothing passes it.
   - Use the existing `MessageAttachmentRepository.list_for_messages` (`attachment_repo.py:109`)
     per page, and add an equivalent batched `list_for_messages` to the edit repository
     (`message_repo.py:457`).
2. **Builder (`chat_export_service.py`).** It becomes a streaming writer.
   - **JSON** writes the header object, then each message object incrementally into the
     `messages` array (`json.dumps` per message, `ensure_ascii=False`).
   - **Markdown** writes per message.
   - **PDF** collects at most 5,000 messages (already guaranteed by the API check), builds the
     HTML, and renders it as today.
   - **Attachments.** With `include_attachments`, the builder stats and downloads each
     attachment of the page with `download_to_path` (`minio_client.py:144-161`) into
     `attachments/<message-id>/<sanitised filename>`, applying Q-2 and Q-3. The ZIP is written
     with `zipfile.ZipFile(..., ZIP_DEFLATED)`, storing already-compressed types with
     `ZIP_STORED`.
   - **Upload** goes through `put_file`.
   - **Audit.** The `message.exported` audit metadata gains `include_attachments`,
     `attachments_archived` and `attachments_omitted`, which are counts only.
   - **Transaction.** The DB transaction is limited to the read phase. Pages are read in one
     read-only transaction, the session is closed before download, render and upload, and the
     audit row is written in a new short transaction. This also resolves audit FU-3, the
     misleading `flush()` comment at `:173-176`.
3. **Task (`app/workers/tasks/conversation.py:216-279`).** Cancellation handling and the `func`
   registration per Q-5. On ready or failed, clear the Q-6 active key.
4. **API (`exports.py`).**
   - `ExportCreateIn.include_attachments: bool = True`.
   - The PDF count check (Q-4) returns 422 with `{"code": "export_too_large_for_pdf",
     "message_count": n, "limit": 5000}`.
   - The Q-6 guard returns 409 `{"code": "export_in_progress", "job_id": ...}`.
   - `ExportJobState` records `include_attachments`.
   - The download filename extension becomes `.zip` when it is set (filename logic from
     `browser-file-delivery` §7.5).
5. **Frontend.**
   - The "Include attachments" checkbox (`SCheckbox`, checked by default) is added to the
     modal form.
   - A 409 resumes polling the returned job.
   - The PDF-size 422 shows the inline error from `chat-export-modal-states`' `invalid` phase,
     with the count.
   - New locale keys in en and zh-TW: `exportIncludeAttachments`, `exportTooLargeForPdf`.
6. **Quality notes.**
   - *Existing debt not to imitate.* The per-message N+1 at `chat_export_service.py:126-128`
     and the in-memory manifest are both removed by this change, not copied.
   - *Patterns.*
     - Application services stay framework-free.
     - The worker task stays a thin orchestrator around the service (`conversation.py:222-226`).
     - The repository owns the SQL.
     - Typed domain errors (`ExportTooLargeForPdf`, `ExportInProgress`) are mapped in
       `contexts/conversation/interfaces/error_mapping.py`, the same place `ExportJobNotFound`
       is mapped.
   - *Reuse.*
     - `download_to_path`, `put_file`, `stat`.
     - `_safe_header_filename` for archive entry names (`attachment_service.py:78-82`), plus
       path-separator stripping as in `export_key` (`minio_client.py:326-331`).
     - `render_safe_html`, `_sender_names` (`chat_export_service.py:204-233`) per page.
     - `audit.emit`.
     - `func` registration as in `main.py:310`.
7. **Security considerations.**
   - **Archive entry names** are attacker-influenced (uploaded filenames). Every entry name is
     built from the message UUID plus the sanitized basename, with no separators, no `..`, and
     no absolute paths, so the archive cannot carry a zip-slip entry.
   - **Quarantined files** are never read.
   - **Row-19 narrowing** applies unchanged, because attachments are fetched only for messages
     the predicate returned. Another user's attachments are never read (the 2026-10-05
     guest-narrowing guarantee).
   - **Resource exhaustion** is bounded by Q-3, Q-4 and Q-6.

## 8. Regression Test Plan

Write these failing tests first:

- **`backend/tests/unit/test_chat_export_archive.py` (new).** Using fake repositories and a fake
  MinIO:
  - JSON with `include_attachments` produces a ZIP containing `manifest.json` and
    `attachments/<mid>/<name>` with the original bytes;
  - expired, quarantined, missing and over-budget attachments are omitted with the right
    reasons and listed in `README.txt`;
  - no `minio_path` appears anywhere;
  - a hostile filename (`../../etc/passwd`, `a/b`, CRLF) yields a single safe entry.

  These fail today because no archive is produced.
- **`backend/tests/db/test_message_export_iter.py` (`pytest.mark.db`).**
  - 2,500 messages across three pages arrive complete and in order, including identical
    `created_at` ties.
  - The row-19 predicate holds per page.
  - `count_for_export` matches.
- **`backend/tests/unit/test_chat_export_task.py` (new).** The task cancelled mid-build leaves
  the record `failed` with "timed out" and re-raises `CancelledError`. This fails today because
  the record stays `running`.
- **`backend/tests/unit/test_export_routes.py` (new, or extending the route tests from
  `browser-file-delivery`).**
  - A PDF over 5,000 messages returns 422 with the count.
  - A second create while one is active returns 409 with the first job id.
- **Frontend.**
  - The checkbox is sent as `include_attachments`.
  - A 409 resumes the returned job.
  - The PDF-size error renders inline.
- **e2e (`frontend/e2e/14-export-attachments.spec.ts`).** Export a room with one attachment,
  and the downloaded ZIP contains it. This extends the download assertion added by
  `browser-file-delivery`.

## 9. Risks and Rollback

- **Larger, slower jobs.** A 2 GiB ZIP takes worker disk and minutes of I/O. It is bounded by
  Q-3, Q-6 and the 1,800-second timeout.
- **Worker disk.** Concurrent exports need disk equal to their archive sizes. With `max_jobs = 50`
  (`main.py:364`), the theoretical worst case is large, which Q-6 bounds to one per user per
  room. FU-1 tracks a global cap.
- **API contract.** One new optional request field and two new error codes. Regenerate the
  client.
- **JSON shape change.** `minio_path` is removed, and `archived`, `omitted_reason` and
  `archive_path` are added. `schema_version` moves from 1 to 2 (`chat_export_service.py:164`),
  so a consumer can tell the shapes apart.
- **Rollback.** Revert the series. Job records gain fields that older code ignores. The Q-6
  Redis key expires with the job TTL.

## 10. Acceptance Criteria

- [ ] AC-1: The §8 tests fail before the fix and pass after.
- [ ] AC-2: On the test stack, a JSON export of a room with a PNG and a TXT attachment downloads
  a ZIP containing `manifest.json` (`schema_version: 2`) and both files byte-identical to the
  uploads.
- [ ] AC-3: An expired attachment and a quarantined attachment are absent from the ZIP, noted
  in the transcript, and listed in `README.txt`.
- [ ] AC-4: A Markdown export of a 50,010-message room contains all 50,010 messages, with the
  worker's peak RSS below 300 MB (`docker stats`).
- [ ] AC-5: A PDF request over 5,000 messages is refused before any job is queued, and the modal
  shows the count with a suggestion.
- [ ] AC-6: A job cancelled by its timeout reads `failed` on the next status poll, and the modal
  shows the failed state.
- [ ] AC-7: A second export request for the same room while one is running returns the running
  job, and the modal follows it.
- [ ] AC-8: An Org Member's narrowed export (row 19) contains only their own attachments plus
  agent and system attachments.

## 11. SRS Delta

[R13.17] is reworded to state the delivered behavior precisely. It was applied at approval on 2026-10-11.

> **[R13.17]** Users can **export** a chat room's history as Markdown, JSON or PDF. By default the
> export is a ZIP containing the transcript and an `attachments/` folder. Attachments are
> regenerated from MinIO if still alive; expired, quarantined, missing or over-budget ones are
> omitted and noted. Markdown and JSON exports have no message cap. A PDF export is limited to
> 5,000 messages and is refused up front when the window exceeds it. A user may have one
> export in progress per room.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1.** A per-user export rate limit and a global worker disk budget. This is the remainder
  of 2026-07-22 FU-3 after Q-6.
- **FU-2.** `skill_export_bundle` and `research_data_export` share the `except Exception`
  cancellation gap. Research is tracked under `browser-file-delivery` FU-2. Skills is unlikely
  to time out at its 64 MiB bound but has the same shape.
- **FU-3.** The WeasyPrint render thread cannot be cancelled. If PDF renders at the 5,000 cap
  prove slow on staging, move rendering to a subprocess so a timeout can stop it.
