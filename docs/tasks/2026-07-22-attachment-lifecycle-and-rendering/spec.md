---
type: bugfix
status: approved
created: 2026-07-22
requirements: [R13.11, R22.15.04]
depends_on: []
---

# Attachment lifecycle and rendering: expired rows, untruncated partial writes, and SVG routed to an inline image

`depends_on` is empty and that is deliberate. None of the three defects requires another
dossier to land first: F-3 adds a retention policy and a read-path guard, F-14 adds a
truncation and a size reconciliation inside the TUS PATCH path, and V-3 changes one
frontend predicate and one comment. The one adjacency worth flagging is not a dependency:
V-8 of `docs/audits/2026-07-22-conversation-verification-gap/findings.md` proposes re-running
`resolve_room_access` inside the `chat_attachment` finalize arm
(`backend/contexts/conversation/application/tus_service.py:263-277`), which is the same
function F-14 edits. That is a merge-order conflict to expect, not an ordering constraint.
See §13.

## 1. Summary

Three confirmed defects on the chat attachment surface. **F-3 (major)**: the MinIO
`chat-uploads` bucket carries a bucket-wide three-day expiration rule
(`backend/smap/bootstrap/minio_init.py:67-77,141-149`), so an attachment's bytes are deleted
on day four while its `message_attachments` row stays `ACTIVE`; the UI therefore renders a
live paperclip link that presigns successfully and delivers a MinIO `NoSuchKey` body, and
the `[attachment expired]` affordance R13.11 mandates is dead code because nothing ever
writes the status that reaches it. **F-14 (minor, data integrity)**: when a TUS chunk write
fails part-way, the partially flushed bytes are never truncated off the staging file and the
client retries the same chunk on top of them, producing a file that is longer than declared
and contains duplicated bytes mid-stream, which is then uploaded and recorded with the
client-declared length as if valid. **V-3 (minor)**: SVG attachments are routed to an inline
`<img>` that the backend deliberately refuses to serve inline, so the user gets an
unlabelled filename button after two failed presign round-trips.

**They do not share a root cause.** They are grouped by change surface only: the same three
files (`attachment_service.py`, `tus_service.py`, `ChatroomMessageBubble.vue`) and the same
reviewer context. F-3 and F-14 rhyme thematically, in that each leaves a durable record
asserting a property of stored bytes that no code establishes (`status = ACTIVE` for F-3,
`size_bytes = upload_length` for F-14), but the causal chains do not touch and neither fix
affects the other. V-3 shares nothing with either beyond the file it renders into. Expect
three independent fixes, three independent failing tests, and no shared abstraction.

## 2. Observed vs Expected

### F-3: bytes deleted after three days, row stays `ACTIVE`

- **Observed** — `expires_at` is stamped on every attachment at creation
  (`backend/contexts/conversation/application/attachment_service.py:224`, and `:371` for
  agent artifacts, both `now() + ATTACHMENT_TTL` where `ATTACHMENT_TTL = timedelta(days=3)`
  at `:53`). `mark_expired` and `list_expired` exist
  (`backend/contexts/conversation/infrastructure/repositories/attachment_repo.py:260-286`)
  and a repo-wide grep returns only those two definitions plus the docstring reference at
  `:296`: **zero callers**. The only attachment policy registered in the nightly sweep is
  `_purge_message_attachments` (`backend/app/workers/tasks/retention.py:114-120`), which
  delegates to `ConversationFacade.purge_old_attachments`
  (`backend/contexts/conversation/interfaces/facade.py:272-295`), scoped to
  `message_id IS NULL` at `:285` and `:292`, so bound rows are never touched. Meanwhile the
  lifecycle rule is installed with `Filter(prefix="")`, bucket-wide, over
  `bucket_chat_uploads` (`backend/smap/bootstrap/minio_init.py:73`, wired at `:141-149`)
  with `days=settings.minio.chat_uploads_expiry_days`, default 3
  (`backend/app/config/settings.py:126`), so MinIO deletes message-bound objects, not just
  staging orphans. On the read path, `get_for_download`
  (`attachment_service.py:265-295`) checks `QUARANTINED` only, at `:273`, never `expires_at`
  and never whether the object still exists, then presigns at `:288-294`. The route wrapper
  duplicates the same two checks and no more (`backend/app/api/v1/attachments.py:106-109`).
  No `attachment.expired` audit is ever emitted.
- **Expected** — `[R13.11]` (`REQUIREMENTS.md:666`): "Messages keep a pointer to the object
  and, after expiry, surface the text `[attachment expired]` in the UI."
  `REQUIREMENTS.md:844` lists `attachment.expired` among the required chat audit actions.
  `REQUIREMENTS.md:1335` documents the `message_attachments (expires_at)` index as existing
  "for the nightly expiry sweep", under "TTL enforcement". The frontend is already built for
  this: `frontend/src/slices/conversation/components/ChatroomMessageBubble.vue:134-140`
  renders `conversation.chatroom.attachmentExpired` for any status that is neither `active`
  nor `quarantined`, with the string present in both catalogues
  (`frontend/src/slices/conversation/locales/en.json:100`, `zh-TW.json:100`), and the
  backend deliberately serves expired rows to the client for exactly this purpose
  (`backend/app/api/v1/messages.py:106-109`, whose comment cites R13.11 by name). Every
  piece of the intended behaviour exists except the sweep that writes the status and the
  read-path guard that refuses the dead presign.

### F-14: partial chunk write never truncated

- **Observed** — `backend/contexts/conversation/application/tus_service.py:229-234` opens
  the staging file in `"ab"` and writes the chunk in a worker thread. The `except OSError`
  handler at `:235-248` rolls back only the Redis offset (`:239`) and re-raises; there is no
  `truncate()` back to the pre-write size and no reconciliation of the file's actual size
  against the offset. A partially flushed write therefore leaves bytes on disk that no
  record accounts for, and the client's retry of the same chunk appends after them. The
  final PATCH then hands off `size_bytes=upload.upload_length`, the client-declared value,
  on all three purpose arms (`:274` chat, `:297` knowmap, `:324` RAG). Downstream,
  `finalize_tus` (`backend/contexts/conversation/application/attachment_service.py:165-207`)
  uploads whatever is on disk via `put_file(file_path=staging_path)` at `:191-196` and
  records the passed-in `size_bytes` at `:203` with no `os.path.getsize` check, no checksum,
  and no length assertion anywhere on the path.
- **Expected** — `[R22.15.04]` (`REQUIREMENTS.md:1636-1641`) makes offsets and sizes a
  server-side enforcement concern, and the module's own contract is that PATCH appends at
  the server's prior offset; the CAS in `tus_store.py:143-151` exists precisely so the
  offset is authoritative. A server that treats its offset as authoritative must guarantee
  the file matches it. Either the append is applied in full and the offset advances, or it
  is not applied at all and the offset does not. A third state, "offset says N, file holds
  more than N", must not be reachable, and if it somehow is, finalization must refuse rather
  than record the corrupt file as valid.

### V-3: SVG routed to an inline `<img>`

- **Observed** — `ChatroomMessageBubble.vue:311-313`: `isImage` tests only
  `mime.startsWith('image/')`, and is used at `:113` to select `AttachmentImage`, which
  renders a bare `<img>` (`AttachmentImage.vue:68-76`). Against that,
  `attachment_service.py:64-74` defines `_INLINE_SAFE_MIME` as
  `{png, jpeg, gif, webp, bmp, application/pdf, text/plain}` with `image/svg+xml`
  deliberately absent (the comment at `:59-63` says so and names SEC-M2), so
  `get_for_download` at `:280-287` presigns SVG as `application/octet-stream` with
  `Content-Disposition: attachment`. The `<img>` cannot decode that; `onError`
  (`AttachmentImage.vue:52-63`) invalidates the memo and retries once, the retry fails
  identically because the content type is a property of the object rather than of the URL's
  freshness, and the component collapses to the unlabelled fallback button at
  `AttachmentImage.vue:77-84`.
- **Expected** — the frontend predicate must agree with the backend allowlist: a type the
  backend will not serve inline must not be routed to an inline renderer. The intent source
  here is internal inconsistency, made explicit by the comment at
  `ChatroomMessageBubble.vue:309-310` asserting that "the presign endpoint forces a safe
  inline content-type for these MIME types", which is false for SVG. Expected behaviour is
  the existing download chip at `ChatroomMessageBubble.vue:118-126`.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Should the F-3 fix widen the existing `purge_old_attachments` sweep to cover bound rows? | No. Add a separate expiry policy that flips `status` to `EXPIRED`; leave `purge_old_attachments` scoped to `message_id IS NULL` exactly as it is. | Widening it would DELETE the row, and the row is what R13.11 requires the client to keep so it can render `[attachment expired]`. `backend/app/api/v1/messages.py:106-109` depends on the row surviving. Deleting and expiring are different operations with different intents; the facade docstring at `facade.py:273-277` already states its narrower scope. |
| Q-2 | Is the nightly sweep alone sufficient for F-3, given MinIO deletes on its own schedule? | No. The sweep is necessary but eventually consistent. `get_for_download` must additionally refuse a row whose `expires_at` has passed, regardless of `status`. | The bucket lifecycle and the nightly cron are independent clocks. Between MinIO's deletion and the next 03:30 sweep there is a window up to roughly 24 hours wide in which the row still reads `ACTIVE`. A status-only fix leaves the original symptom, a live link over deleted bytes, fully reachable inside that window. |
| Q-3 | For F-14, should finalization repair a size mismatch or refuse it? | Refuse. Raise, leave the staging file in place for the `finally` cleanup, and never create the attachment row. | A mismatch means the bytes on disk are not the bytes the client declared. There is no correct repair: truncating to the declared length would keep a file that is corrupt in the middle, and accepting it is the current defect. Refusing converts silent corruption into a visible, retryable failure. |
| Q-4 | For F-14, should already-stored files be repaired automatically? | No. Read-only reconciliation only, with mismatches escalated for manual review. See §7. | Corruption is only partially detectable after the fact and the automated action available (delete) is worse than the defect for any false positive. |
| Q-5 | For V-3, should SVG be made to render by adding it to the backend allowlist? | **Absolutely not.** The fix is in the frontend predicate. | Adding `image/svg+xml` to `_INLINE_SAFE_MIME` would reintroduce scriptable markup served inline from the storage origin, breaking the SEC-M2 control that `backend/tests/unit/test_attachment_download_disposition.py:80-86` and `:103-108` pin. See the warning in §7. |
| Q-6 | Should V-3 add a distinct "cannot preview" UI state? | No. Fall through to the existing download chip. | The chip at `ChatroomMessageBubble.vue:118-126` is already the correct affordance for a type that downloads, it is already localised, and adding a state means a new `$t()` key and a new visual for no behavioural gain. |

## 4. Reproduction

### F-3

Preconditions: a project, a chatroom the actor can post in, a running MinIO with the
bootstrap lifecycle rule applied (`smap bootstrap minio-init`, `minio_init.py:141-149`).

1. Upload an attachment and bind it to a message (either boundary:
   `POST /api/attachments` single-shot, or the TUS flow through
   `backend/app/api/v1/tus.py`). Note the returned attachment id.
2. Confirm the row: `status = 'active'`, `expires_at = created_at + 3 days`
   (`attachment_service.py:224`).
3. Advance past `expires_at` and let the bucket lifecycle run. Deterministic shortcut for a
   test environment: delete the object directly from the `chat-uploads` bucket and
   backdate `expires_at` to the past. This reproduces the exact end state the lifecycle
   produces, and it is the state the regression test should construct.
4. `GET /api/messages?chatroom_id=...`: the attachment is still returned with
   `status: "active"` (`messages.py:106-109` embeds it verbatim).
5. The bubble renders a live paperclip link, not `[attachment expired]`
   (`ChatroomMessageBubble.vue:118-126` wins over `:134-140` because the status branch at
   `:119` matches).
6. `GET /api/attachments/{id}`: 200 with a presigned URL
   (`attachments.py:126-128` after `get_for_download` passes its only status check at
   `attachment_service.py:273`). Following the URL yields MinIO's `NoSuchKey` XML.
7. Run the nightly sweep (`retention_sweep`, `retention.py:755`). The status is unchanged
   and no `attachment.expired` audit row is written, because nothing calls `mark_expired`.

### F-14

Preconditions: a TUS upload in progress and the ability to inject an `OSError` part-way
through the staging-file write. The natural trigger is ENOSPC on the staging volume; the
deterministic reproduction injects the fault.

1. Create a TUS upload with `Upload-Length = L` (`tus.py` create, `tus_service.py:99-160`).
2. PATCH a chunk of size `C` at offset `0`, with the write configured to flush `C/4` bytes
   and then raise `OSError`. The CAS at `tus_service.py:226` has already advanced the Redis
   offset to `C`; the handler at `:239` rolls it back to `0`; the staging file now holds
   `C/4` bytes that no record accounts for.
3. Retry the same PATCH at offset `0`, this time succeeding. The file now holds `C/4 + C`
   bytes; Redis says `C`.
4. Continue normally to `upload_offset == L`. Finalization proceeds
   (`tus_service.py:251-277`), `finalize_tus` uploads the `L + C/4` byte file
   (`attachment_service.py:191-196`) and records `size_bytes = L`
   (`tus_service.py:274` into `attachment_service.py:203`).
5. Download the attachment: a file with `C/4` duplicate bytes wedged mid-stream, recorded as
   valid, with nothing in the audit log or metrics indicating anything went wrong.

Nondeterminism note: step 2 requires a partial-write fault, which is why the audit rates
this minor. The bug is fully deterministic once the fault occurs; only the fault is rare.
The regression test injects it rather than waiting for it.

### V-3

1. In a chatroom, have an agent with code execution produce an SVG, for example
   `plt.savefig('chart.svg')`; the kernel derives the MIME via `mimetypes.guess_type`
   (`deploy/sandbox/code-exec/kernel/kernel.py:74-76`), yielding `image/svg+xml`. Or upload
   an `.svg` directly: `backend/app/api/v1/attachments.py:90` takes `mime or
   file.content_type` with no allowlist.
2. Observe the bubble: `isImage('image/svg+xml')` is true
   (`ChatroomMessageBubble.vue:311-313`), so `AttachmentImage` mounts (`:112-117`).
3. The presign returns `application/octet-stream` + `attachment` disposition
   (`attachment_service.py:282-287`). The `<img>` fails to decode.
4. `onError` (`AttachmentImage.vue:52-63`) invalidates and retries once; the retry fails
   identically. The component settles on the unlabelled fallback button.

## 5. Root Cause Analysis

### F-3

Causal chain:

1. `minio_init.py:67-77` builds the lifecycle rule with `rule_filter=Filter(prefix="")`, and
   `:141-149` applies it to `bucket_chat_uploads` with `days=3`. Bucket-wide means
   message-bound objects, not just staging orphans.
2. `attachment_service.py:224` stamps `expires_at = now() + 3 days` on every row, so the
   database already knows exactly when each object dies.
3. Nothing reads that column to act on bound rows. `attachment_repo.py:260-286` defines the
   two operations required (`mark_expired`, `list_expired`) and nothing calls them. The
   registered attachment policy (`retention.py:114-120` into `facade.py:272-295`) is scoped
   to `message_id IS NULL`, correctly for its own purpose, so it does not and must not cover
   this.
4. The read path never compensates: `attachment_service.py:270-274` checks only
   `QUARANTINED` before presigning, and presigning does not verify object existence
   (`:288-294`).
5. The client receives `status: "active"` (`messages.py:106-109`), takes the live-link
   branch (`ChatroomMessageBubble.vue:119-126`), and the `[attachment expired]` branch at
   `:134-140` is unreachable.

**Root cause**: the nightly expiry sweep that `REQUIREMENTS.md:1335` names, and whose
repository primitives were written, was never wired to a caller. Correcting that earliest
link, a policy that calls `list_expired` and `mark_expired` and emits `attachment.expired`,
prevents the symptom for every attachment past its horizon.

**Aggravating factor, not the root cause**: `get_for_download` presigns without checking
`expires_at`. Even with the sweep in place this leaves the up-to-24-hour lag window between
MinIO's deletion and the next cron run fully symptomatic, which is why §7 fixes both. But
the read-path check alone would leave the row permanently `ACTIVE`, the audit action
permanently unemitted, and the UI affordance permanently dead, so it is not the root cause.

### F-14

Causal chain:

1. `tus_service.py:226` claims the new offset in Redis via CAS *before* the write, so the
   offset is a statement of intent rather than of fact. This is deliberate and correct for
   the concurrency hazard it was built for (`tus_store.py:141-151`), and it is not the
   defect.
2. `tus_service.py:229-234` performs the append with no record of the pre-write file size
   and no guarantee of atomicity. A `write()` that raises `OSError` may have flushed an
   arbitrary prefix of the chunk.
3. `tus_service.py:235-248` handles the failure by restoring the offset and nothing else.
   The file is left dirty. This is the earliest link whose correction prevents the symptom:
   truncating back to `offset` here makes the file match the offset the handler restores,
   and the retry then appends onto a clean prefix.
4. `tus_service.py:263-277` finalizes on the offset alone, and passes
   `size_bytes=upload.upload_length` (`:274`, mirrored at `:297` and `:324`), so the
   declared length becomes the durable record.
5. `attachment_service.py:191-203` uploads the file by path and records the declared value.
   No code on this path ever asks the filesystem how many bytes are actually there.

**Root cause**: the failure handler at `tus_service.py:235-248` restores the offset without
restoring the file, breaking the invariant the offset is supposed to describe.

**On what the durable record of validity is, precisely.** Today there are two records and
neither observes the bytes. The in-flight record is the Redis `upload_offset`
(`tus_store.py:143-151`), which reflects what the server intended to write. The persisted
record is `message_attachments.size_bytes`, set from `upload.upload_length`, which is what
the *client* declared before any byte was transferred. The file on disk is the only
authority on what was actually stored, and nothing reads its size at any point between the
first PATCH and the MinIO put. The fix must make the file the record: reconcile
`os.path.getsize(staging_path)` against `upload_offset` before handing off, and record the
reconciled value rather than the declared one.

**Aggravating factor**: no checksum is computed for chat attachments, so nothing downstream
can detect the discrepancy either. Note that the RAG and knowmap arms *do* compute a
sha256 (`backend/contexts/knowledge/application/rag_tus_finalizer.py:83`,
`knowmap_tus_finalizer.py:74`) but this does not help, because the digest is taken over the
already-corrupt staged file. It is a dedup key, not a validator. Stated plainly so nobody
mistakes it for existing integrity coverage.

### V-3

Causal chain: `ChatroomMessageBubble.vue:311-313` implements a broader predicate
(`image/*`) than the backend's serving policy (`attachment_service.py:64-74`, seven
enumerated types). At `:113` the broader predicate selects the inline renderer. At
`attachment_service.py:282-287` the narrower policy refuses to serve inline. The `<img>`
cannot resolve the disagreement and fails.

**Root cause**: the frontend predicate was written as a category test rather than as a
mirror of the backend allowlist, and the comment at `ChatroomMessageBubble.vue:309-310`
records the author's belief that the backend served every `image/*` inline. That belief is
false and is the reason the predicate was written the way it was.

## 6. Blast Radius and Sibling Suspects

### Blast radius

- **F-3**: every attachment older than three days, which is eventually all of them, in every
  room and every tenant. Both producer paths are affected: user uploads
  (`attachment_service.py:224`) and agent artifacts (`:371`), since both stamp the same
  `ATTACHMENT_TTL`. Spec'd behaviour is absent end to end: no sweep, no `attachment.expired`
  audit row (`REQUIREMENTS.md:844`), and a dead UI branch. Data already written: every
  bound `message_attachments` row whose `expires_at` has passed currently claims `ACTIVE`
  while its object is gone. That set is exactly enumerable, see §7.
- **F-14**: any TUS upload of any purpose that hits a partial-write fault on the staging
  volume. The corruption is silent at every layer and survives into MinIO. The scope is
  bounded by the rarity of the fault, not by anything in the code. Because the defect lives
  in the shared PATCH path, all three purposes are affected identically, and one fix at
  `tus_service.py:229-248` covers all three.
- **V-3**: every SVG attachment, agent-produced or user-uploaded. The same predicate
  mismatch covers all `image/*` subtypes outside the five-raster allowlist (`avif`, `heic`,
  `tiff`), though for those the outcome is less certain since raster sniffing may still
  succeed. SVG is the certain case. No data loss and no security exposure: the backend's
  refusal to serve inline is what makes this cosmetic rather than dangerous.

### Sibling suspects

- **Agent artifacts and F-3 — CONFIRMED, same defect, same fix.**
  `attachment_service.py:371` stamps `expires_at = now() + ATTACHMENT_TTL` on artifact rows
  created via `create_agent_artifact`, and they live in the same bucket under the same
  bucket-wide lifecycle rule. An agent-produced chart dies on day four exactly as a user
  upload does. The sweep proposed in §7 is driven by `expires_at` and `status`, not by
  producer path, so it covers them without extra work. Named here so the acceptance criteria
  cover both producers.
- **`ConversationFacade.purge_old_attachments` — CLEARED, and must stay as it is.**
  `facade.py:272-295` restricts to `message_id IS NULL` at `:285` and `:292`, and its
  docstring at `:273-277` states that scope deliberately. It is not a half-implemented
  version of the missing sweep; it is a different policy (delete never-bound orphans) that
  happens to share the `expires_at` column. Evidence it must not be widened: the row is what
  `messages.py:106-109` embeds so the client can render `[attachment expired]` per R13.11,
  so deleting bound rows would destroy the very affordance F-3 exists to restore.
- **The exports bucket — CLEARED, and it is the template for the F-3 fix.**
  `minio_init.py:152-158` installs a one-day lifecycle on `bucket_exports`, the same class
  of bucket-side deletion, but here the application-side counterpart exists and is
  registered: `_purge_exports_bucket` (`retention.py:569-581`) deletes objects older than 24
  hours and is wired into `_POLICIES` at `:748`. The pattern F-3 is missing was applied
  correctly one bucket over, which is evidence this is an oversight rather than a design
  position.
- **Single-shot upload sizing and F-14 — CLEARED.**
  `ingest_single_shot` (`attachment_service.py:122-161`) records `size_bytes=len(data)` at
  `:157`, the actual byte count of the in-memory payload it just wrote, and there is no
  incremental staging file to leave dirty. The declared-length defect does not exist on this
  boundary.
- **RAG and knowmap TUS finalize arms and F-14 — CONFIRMED, same defect, covered by the
  same fix.** `tus_service.py:297` and `:324` pass `size_bytes=upload.upload_length` exactly
  as the chat arm does at `:274`, and the corrupt staging file reaches them through the same
  `_append`. The sha256 at `rag_tus_finalizer.py:83` and `knowmap_tus_finalizer.py:74` does
  not clear them: it is computed over the staged file after corruption, so it certifies
  nothing. Placing the size reconciliation before the purpose branch (see §7) fixes all
  three arms in one place, which is why the fix location matters.
- **Other `image/*` predicates in the frontend and V-3 — CLEARED as a systemic pattern.** A
  repo-wide grep for `startsWith('image/` across `frontend/src` returns exactly one
  production site, `ChatroomMessageBubble.vue:312`; the only other hits are MIME literals in
  test fixtures (`frontend/src/slices/conversation/__tests__/ChatroomMessageBubble.test.ts:171,177`,
  `ChatroomView.test.ts:130`, `frontend/src/slices/skills/__tests__/SkillFiles.test.ts:20`).
  This is a single-site defect, not an instance of a repeated mistake.
- **The `QUARANTINED`-only check appearing twice and F-3 — CONFIRMED as duplication worth
  noting.** The same two-condition status check exists at `attachment_service.py:271-274`
  and again at `attachments.py:106-109`. Whatever guard F-3 adds must be added in the
  service, which is the path both the route and any other caller funnel through
  (`attachments.py:126` calls `get_for_download`), so the route copy does not need to change
  and will inherit the behaviour. Recorded so /build does not add the check in only one of
  the two and believe it is done.

## 7. Fix Design

### F-3

Two changes, both required, plus the audit action.

1. **Add the missing sweep.** A new facade method on `ConversationFacade`, alongside but
   separate from `purge_old_attachments` (`facade.py:272-295`), that batches over
   `attachment_repo.list_expired(horizon=now())` (`attachment_repo.py:267-286`, whose
   predicate already excludes rows that are already `EXPIRED` at `:280`) and calls
   `mark_expired` (`:260-265`) for each, emitting one `attachment.expired` audit event per
   row with the attachment id and chatroom id, as `REQUIREMENTS.md:844` requires. Register
   it in `_POLICIES` (`retention.py:728-752`) with its own `_emit_summary` line, following
   `_purge_message_attachments` (`:114-120`) as the structural model and
   `_purge_exports_bucket` (`:569-581`, registered at `:748`) as the semantic model. Order
   within `_POLICIES` is unconstrained relative to the other attachment policy: the two
   operate on disjoint row sets (`message_id IS NULL` versus all expired rows not yet
   `EXPIRED`) and neither creates work for the other.
2. **Guard the read path.** In `get_for_download` (`attachment_service.py:265-295`), refuse
   a row whose `expires_at` has passed or whose status is `EXPIRED`, alongside the existing
   `QUARANTINED` refusal at `:273`. This requires a new domain error next to
   `AttachmentQuarantined` (`backend/contexts/conversation/domain/errors.py:67`) and a
   mapping entry beside it in `backend/contexts/conversation/interfaces/error_mapping.py:64-80`.
   410 Gone is the honest status for a resource that existed and was deliberately removed;
   404 would be defensible but loses the distinction from a bad id, which the client already
   has a separate rendering for.

**Why this corrects rather than masks.** The symptom is a dead link. The masking fix would
be to make the frontend probe the URL or to swallow the `NoSuchKey` response, which would
leave the row lying about its own status, the required audit action unemitted, and the
retention record incomplete. This fix instead makes the database state true, which is what
every downstream consumer (`messages.py:106-109`, the bubble at
`ChatroomMessageBubble.vue:134-140`, the audit log) was already written to depend on. The
read-path guard is not a mask either: it closes the sweep's lag window, and it is the layer
that must be correct regardless, since MinIO's clock and the cron's clock are independent.

**Data repair for F-3 — required, and safe.** Rows pointing at deleted bytes exist today:
every bound `message_attachments` row with `expires_at < now()` and `status = 'active'`.
Detection is exact and needs no probing of MinIO, because `expires_at` is stamped on every
row at creation (`attachment_service.py:224`, `:371`) and the bucket rule uses the same
three-day horizon (`settings.py:126`); the column and the lifecycle agree by construction.
The repair is simply the first run of the new sweep, which will find the entire historical
backlog rather than one night's worth. Two operational notes for /build: batch it, since
`list_expired` already takes `limit=500` (`attachment_repo.py:271`) and the worker should
loop rather than attempt one transaction over the backlog; and expect a large one-time burst
of `attachment.expired` audit rows on that first run, which is correct rather than a defect,
but should be called out in the deploy note so it is not mistaken for an incident. No
migration is needed: `EXPIRED` is already a valid value in the schema
(`backend/alembic/versions/0017_messages.py:28`).

### F-14

Three changes, at two locations.

1. **Truncate on failure, at the point of failure.** In `tus_service.py:229-248`, the
   `except OSError` handler must truncate the staging file back to `offset` (the pre-write
   size) *before* rolling the Redis offset back at `:239`. The truncation belongs here and
   nowhere else: this is the only writer, and it is the only place that still knows the
   pre-write size without having to derive it. Ordering matters. Truncate first, then roll
   back, so that at no point is the offset lower than the file. If the truncation itself
   fails, **do not roll the offset back**: leave it advanced and log at the same severity as
   the existing unrecoverable branch at `:240-247`, because a client that retries onto a
   file the server could not clean is exactly how the corruption is produced. Refusing to
   let the upload continue is the correct outcome for an unrecoverable staging file; the 24h
   TTL (`tus_store.py:35`) and the `tus_parts` cleanup policy (`retention.py:750`) reclaim
   it.
2. **Reconcile before handing off.** Immediately before the purpose branch at
   `tus_service.py:263-264`, assert `os.path.getsize(upload.staging_path) ==
   upload.upload_length` and raise on mismatch. This location is load-bearing: placed here
   it is above all three arms (`:264` chat, `:278` knowmap, `:304` RAG) and inside the
   `try` whose `finally` at `:331-334` already removes the staging file and the Redis
   record, so a refusal cleans up on the existing path with no new cleanup code. Placing it
   inside `finalize_tus` instead would cover only the chat arm.
3. **Record the observed size, not the declared one.** Once (2) guarantees they are equal,
   pass the value read from the filesystem rather than `upload.upload_length` at `:274`,
   `:297`, and `:324`. This is not redundant with (2): it makes the filesystem the source of
   the durable record permanently, so any future path that skips the assertion still records
   what was stored rather than what was promised.

**Why this corrects rather than masks.** The masking fix is to record
`os.path.getsize()` at finalize and call it done, which would make `size_bytes` accurate
about a file that is still internally corrupt (duplicate bytes wedged mid-stream). The
truncation at (1) is what prevents the corruption from being created; (2) and (3) are the
detection and the honest record for anything the truncation could not prevent.

**Data repair for F-14 — no automated repair, and here is why, plainly.** Corrupt files may
exist. Detectability is partial:

- **Detectable**: the specific failure mode described in the audit produces a stored object
  *longer* than the recorded `size_bytes`. Comparing MinIO's `stat_object(...).size` against
  `message_attachments.size_bytes` for every row is a genuine detector for that class, and
  it is cheap and read-only.
- **Not detectable**: no checksum is stored for chat attachments. `_create_row_and_audit`
  (`attachment_service.py:211-261`) records filename, mime, size, and path, and no digest.
  So a corrupt file whose length happens to equal the declared length is undetectable by any
  means available after the fact. The sha256 on the RAG and knowmap paths
  (`rag_tus_finalizer.py:83`, `knowmap_tus_finalizer.py:74`) does not help: it was computed
  over the corrupt staged file, so it matches the corrupt object and certifies nothing about
  integrity.

**Position**: ship a read-only reconciliation script that reports rows where the stored
object size disagrees with `size_bytes`, and stop there. Do not delete, do not re-derive,
do not flip status automatically. A size mismatch has causes other than this defect, and
deleting a user's attachment on a heuristic is a worse outcome than a corrupt download.
Escalate the report for manual review. Also set expectations honestly: this defect requires
a partial-write disk fault to have occurred on a staging volume. If the operator has no
ENOSPC or I/O-error history on that volume, the expected size of the affected set is zero,
and a clean reconciliation report is the likely and unsurprising outcome. Run it once, keep
the script, do not schedule it.

### V-3

Change `isImage` (`ChatroomMessageBubble.vue:311-313`) to test membership in the raster
subset of the backend's `_INLINE_SAFE_MIME`, namely `image/png`, `image/jpeg`, `image/gif`,
`image/webp`, `image/bmp`, normalising the parameter suffix and case the same way the
backend does at `attachment_service.py:280` (split on `;`, trim, lowercase) so that
`image/svg+xml; charset=utf-8` is classified as the base type rather than slipping through.
Replace the false comment at `:309-310` with one that states the actual contract: this list
mirrors the raster entries of `_INLINE_SAFE_MIME` in `attachment_service.py:64-74`, and
anything absent from it is served as a download by design. Everything not matching falls
through to the existing download chip at `:118-126`, which is already localised, so no new
`$t()` key is required and no template string is added.

**Why this corrects rather than masks.** The masking fix would be to keep the broad
predicate and improve the `AttachmentImage` failure path, for example by rendering a nicer
message after the second failure. That would still burn two presign round-trips per SVG on
a failure that is known in advance from the MIME type alone. Aligning the predicate makes
the client stop asking for something the server has already decided not to give.

**Data repair for V-3: none.** No data was written incorrectly. This is purely a rendering
decision made at display time from the row's `mime`, so correcting the predicate corrects
every existing attachment on the next render.

### The warning that must not be lost

**Do not add `image/svg+xml` to `_INLINE_SAFE_MIME`** (`attachment_service.py:64-74`). SVG is
scriptable markup; serving it inline from the storage origin is exactly the attack the
allowlist exists to prevent, as the comment at `:59-63` states and as SEC-M2 designates.
That control is pinned by `backend/tests/unit/test_attachment_download_disposition.py:80-86`,
which parametrizes `image/svg+xml` and asserts `application/octet-stream`, and by `:103-108`,
which pins that `image/svg+xml; charset=utf-8` is treated identically. Those assertions must
remain green and unmodified through this work; see AC-11.

This warning is not hypothetical caution. The originating audit's one-line description of
this candidate ("SVG artifacts render broken") **misattributes the defect to the backend**,
as `docs/audits/2026-07-22-conversation-verification-gap/findings.md` V-3 and its FU-1 both
record. A reader who follows that description rather than this dossier goes to
`attachment_service.py`, finds the MIME that is "missing" from the allowlist, adds it, sees
the SVG render, and has just deleted a security control while believing they fixed a bug.
The backend is correct. The frontend is wrong. The fix is in the frontend.

## 8. Regression Test Plan

Failing tests first, in all three cases. /build writes these before touching any production
code and confirms each fails for the stated reason.

### F-3 — new file `backend/tests/unit/test_attachment_expiry_sweep.py`

Follow the fake-repo and fake-MinIO construction already used by
`backend/tests/unit/test_attachment_download_disposition.py:40-76`, which builds an
`AttachmentService` with `db=None` and substituted collaborators, so these stay unit tests
with no database.

- `test_sweep_marks_rows_past_their_horizon_expired` — build two rows, one with `expires_at`
  in the past and one in the future, both `ACTIVE` and both bound to a message; run the new
  sweep; assert `mark_expired` was called for the first and only the first. **Fails today**
  because no sweep exists (the import does not resolve), and because
  `attachment_repo.mark_expired` and `list_expired` (`attachment_repo.py:260-286`) have zero
  callers anywhere in the tree.
- `test_sweep_emits_an_attachment_expired_audit_per_row` — assert one audit event with
  `action == "attachment.expired"` carrying the attachment id and the chatroom id. **Fails
  today** because no code path emits that action, though `REQUIREMENTS.md:844` requires it.
- `test_sweep_leaves_rows_that_are_already_expired_alone` — assert idempotence across two
  consecutive runs. **Fails today** for the same missing-function reason; after the fix it
  passes via the `status != EXPIRED` predicate already present at `attachment_repo.py:280`,
  and it exists to pin that predicate against future edits, since without it the first run
  after deploy would re-emit an audit row for the entire backlog on every subsequent night.
- `test_download_of_an_expired_attachment_is_refused` — a row with `expires_at` in the past
  and `status == ACTIVE`; assert `get_for_download` raises the new expired error and that
  the fake MinIO recorded **zero** `presigned_get` calls. **Fails today** because
  `get_for_download` checks only `QUARANTINED` at `attachment_service.py:273` and proceeds
  to presign at `:288-294`.
- `test_download_of_a_row_marked_expired_is_refused` — the post-sweep state, status
  `EXPIRED`. **Fails today** for the same reason: the status is not in the refusal set.

### F-3 — extend `backend/tests/unit/test_retention_deep.py`

- `test_expire_attachments_policy_is_registered_in_the_sweep` — mirror the existing facade
  assertion pattern at `test_retention_deep.py:596-606` (which patches the facade and
  asserts `purge_old_attachments.assert_awaited_once_with(max_age_days=3)`), asserting the
  new policy is present in `_POLICIES` and is awaited by `retention_sweep`. **Fails today**
  because `_POLICIES` (`retention.py:728-752`) contains no attachment-expiry entry; its only
  attachment policy is `_purge_message_attachments` at `:114-120`, which covers a disjoint
  row set.

### F-14 — new file `backend/tests/unit/test_tus_partial_write.py`

Drive `TusService.patch` against a real temporary staging file and a fake store, injecting
the fault by patching the module-level file write so it flushes a prefix and then raises
`OSError`.

- `test_a_failed_chunk_write_truncates_the_staging_file_back_to_the_prior_offset` — write a
  clean chunk, then a chunk whose write flushes a quarter and raises; assert the call raises
  and that `os.path.getsize(staging_path)` equals the offset before the failed chunk.
  **Fails today**: `_append` (`tus_service.py:229-234`) leaves the flushed prefix on disk and
  the handler at `:235-248` only calls `update_offset`, so the file is larger than the
  restored offset.
- `test_a_retry_after_a_failed_write_produces_a_byte_exact_file` — after the failure, retry
  the same chunk successfully and complete the upload; assert the staging file's contents
  equal the concatenation of the chunks exactly, with no duplicated region. **Fails today**
  for the same reason, and this is the assertion that pins the actual user-visible harm
  (a corrupt file) rather than only the intermediate state.
- `test_finalize_refuses_a_staging_file_whose_size_disagrees_with_the_declared_length` —
  construct the mismatched end state directly, drive the final PATCH, assert it raises and
  that the chat finalizer (`attachment_service.finalize_tus`) was **never awaited**. **Fails
  today**: `tus_service.py:263-277` branches straight into the finalizer with
  `size_bytes=upload.upload_length` (`:274`) and no size check exists anywhere on the path.
- `test_finalize_size_check_covers_the_rag_and_knowmap_arms_too` — same construction with
  `purpose="rag_source"`; assert `KnowledgeFacade.finalize_rag_upload` is never awaited.
  **Fails today** identically (`:319-329` passes `size_bytes=upload.upload_length` at
  `:324`). This test is what pins the *placement* of the check above the branch rather than
  inside one arm.
- `test_a_failed_truncation_does_not_roll_the_offset_back` — make the truncation itself
  raise; assert the Redis offset is left advanced rather than restored. **Fails today**
  because there is no truncation at all, so the offset is unconditionally rolled back at
  `tus_service.py:239` and a retry is invited onto a dirty file.

### V-3 — extend `frontend/src/slices/conversation/__tests__/ChatroomMessageBubble.test.ts`

Add to the existing `describe('ChatroomMessageBubble attachments')` block at `:167-187`,
reusing the `attachment()` factory at `:62-74` and the msw handler pattern at `:169-173`.

- `renders an svg attachment as a download chip, not an inline image` — an attachment with
  `mime: 'image/svg+xml'` and `filename: 'chart.svg'`; assert a `.attachment-link` chip
  containing `chart.svg` exists. **Fails today**: `isImage`
  (`ChatroomMessageBubble.vue:311-313`) returns true for `image/svg+xml`, so the `v-if` at
  `:113` selects `AttachmentImage` and the chip branch at `:118-126` never renders, so the
  assertion finds no matching chip.
- `treats a parameterised svg mime identically` — `mime: 'image/svg+xml; charset=utf-8'`.
  **Fails today** for the same reason, and it exists to mirror the backend normalisation
  already pinned at `test_attachment_download_disposition.py:103-108`, so the two sides stay
  aligned on parameter handling rather than only on the bare type.
- `still renders a png inline` — an explicit non-regression on the existing behaviour that
  `:168-186` covers, restated inside the new predicate's test group so a future narrowing of
  the allowlist cannot silently break raster rendering. **Passes today**; it is a guard, not
  a failing test.

### Guard, not a new test

`backend/tests/unit/test_attachment_download_disposition.py:80-86` and `:103-108` must remain
green **and unmodified**. If a change to this dossier's work requires editing either
assertion, that is the signal that the fix went into the backend allowlist by mistake. See
the warning in §7 and AC-11.

## 9. Risks and Rollback

- **F-3, the sweep's first run over the historical backlog.** Every bound attachment past
  its horizon flips to `EXPIRED` in one night and emits one audit row each. On an
  established install this is a large single burst of writes. Mitigation: the batching
  already implied by `list_expired(limit=500)` (`attachment_repo.py:271`) plus a bounded
  loop, following the chunk-loop shape `retention.py:94-98` already uses for messages. Risk
  of getting this wrong is worker time and audit volume, not data loss, since the operation
  is an idempotent status update.
- **F-3, over-refusal on the read path.** If the horizon comparison is wrong, live
  attachments could be refused. This is the highest-consequence risk in the dossier because
  it takes working downloads away. Mitigation: the comparison is against the same
  `expires_at` the row already carries and the same three-day constant the bucket uses
  (`attachment_service.py:53`, `settings.py:126`), and the refusal is a read-path behaviour
  with no persistent effect, so reverting the guard restores access immediately with no data
  to unwind.
- **F-3, clock skew between the bucket rule and the column.** If an operator changes
  `chat_uploads_expiry_days` (`settings.py:126`) without changing `ATTACHMENT_TTL`
  (`attachment_service.py:53`), the two horizons diverge and the sweep marks rows either too
  early (usable attachments shown as expired) or too late (the original symptom returns in a
  wider window). This is pre-existing coupling, not introduced here, but the fix makes it
  consequential where it previously was inert. Worth a note in the deploy documentation, and
  recorded as FU-1.
- **F-14, refusing uploads that previously succeeded.** The size assertion turns a
  previously silent success into a failure. That is the intent, but if the assertion is
  wrong (for example if a purpose can legitimately stage a file of a different length than
  `upload_length`) it would break working uploads. Evidence it is not: all three arms pass
  `upload.upload_length` as the size today (`tus_service.py:274,297,324`), and the PATCH
  path enforces `new_offset <= upload_length` at `:219-222` with completion gated on
  `new_offset == upload_length` at `:251`, so equality is already the contract everywhere.
- **F-14, truncation on a filesystem that does not support it.** `os.truncate` is available
  on every platform the backend targets. Non-risk, recorded so it is not re-litigated.
- **V-3, over-narrowing.** If the raster list is transcribed wrongly, working inline images
  become download chips. Bounded, cosmetic, immediately visible, and covered by the
  `still renders a png inline` guard test.
- **Rollback.** All three are independently revertible. F-3: remove the `_POLICIES` entry to
  stop the sweep and revert the `get_for_download` guard; rows already marked `EXPIRED`
  stay marked, which is a truthful state and needs no unwinding, and the frontend renders
  them per R13.11 either way. F-14: revert `tus_service.py`; no persisted state changes. V-3:
  revert the predicate; render-time only. Commit them as three separate commits so a revert
  of one does not disturb the others.

## 10. Acceptance Criteria

- [ ] **AC-1**: Every regression test named in §8 is written first, fails against current
      code for the reason stated there, and passes after the fix.
- [ ] **AC-2**: A nightly policy marks every attachment whose `expires_at` has passed as
      `EXPIRED`, is registered in `_POLICIES` (`retention.py:728-752`), covers both user
      uploads and agent artifacts, and is idempotent across consecutive runs.
- [ ] **AC-3**: Each row expired by that policy emits one `attachment.expired` audit event
      (`REQUIREMENTS.md:844`) carrying the attachment id and chatroom id.
- [ ] **AC-4**: `get_for_download` refuses an attachment that is `EXPIRED` or whose
      `expires_at` has passed, without issuing a presign, mapped to 410 through
      `interfaces/error_mapping.py`.
- [ ] **AC-5**: `ConversationFacade.purge_old_attachments` (`facade.py:272-295`) is
      unchanged and still scoped to `message_id IS NULL`, so bound rows survive for the
      R13.11 affordance.
- [ ] **AC-6**: A failed chunk write leaves the staging file at exactly the pre-write
      offset, and a subsequent successful retry of the same chunk yields a byte-exact file.
- [ ] **AC-7**: If truncation after a failed write cannot be completed, the Redis offset is
      not rolled back and the failure is logged at the existing unrecoverable severity
      (`tus_service.py:240-247`).
- [ ] **AC-8**: Finalization refuses to hand off a staging file whose size disagrees with
      `upload_length`, for **all three** purposes, with the check placed above the purpose
      branch at `tus_service.py:263-264`, and the staging file and Redis record are still
      cleaned up by the existing `finally` at `:331-334`.
- [ ] **AC-9**: The persisted `size_bytes` is the size observed on disk, not
      `upload.upload_length`, on all three arms.
- [ ] **AC-10**: An SVG attachment renders as a download chip, including when its MIME
      carries a parameter suffix, and raster images still render inline.
- [ ] **AC-11**: `_INLINE_SAFE_MIME` (`attachment_service.py:64-74`) is unchanged, and
      `backend/tests/unit/test_attachment_download_disposition.py:80-86` and `:103-108` are
      green and unmodified.
- [ ] **AC-12**: The false comment at `ChatroomMessageBubble.vue:309-310` is replaced with
      one that names the backend allowlist it mirrors.
- [ ] **AC-13**: A read-only reconciliation script exists that reports attachments whose
      stored object size disagrees with `size_bytes`; it performs no writes and is not
      scheduled. Its output is recorded in §12, including a clean result.
- [ ] **AC-14**: No new user-facing string is introduced; if one becomes necessary it goes
      through `$t()` with entries in both `en.json` and `zh-TW.json`.
- [ ] **AC-15**: Full Definition of Done: `pytest -q`, `ruff check . && ruff format --check .`,
      `mypy .` in `backend/`; `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build` in
      `frontend/`.
- [ ] **AC-16**: The three fixes land as three separate commits so each is independently
      revertible.

## 11. SRS Delta

None for F-14 and V-3: both restore behaviour the code already contracts for, and V-3's
intent source is internal inconsistency rather than a requirement.

For F-3, one clarification is worth drafting, not because the SRS is wrong but because it is
distributed. R13.11 (`REQUIREMENTS.md:666`) states the UI outcome, `:844` names the audit
action, and `:1335` names the index "for the nightly expiry sweep", but no requirement
states the sweep's existence or its relationship to the bucket lifecycle rule, and the
lifecycle horizon lives in configuration (`settings.py:126`) independently of
`ATTACHMENT_TTL` (`attachment_service.py:53`).

**Adopted at approval**, inserted after R13.11 (`REQUIREMENTS.md:694`) using the `Rxx.yya`
suffix this SRS already uses for clarifications appended to an existing requirement
(`R7.09a`, `R9.10a`, `R14.07a`, `R15.05a`):

> - **[R13.11a]** The `[attachment expired]` state of R13.11 is reached by a nightly
>   application-side expiry sweep that marks every attachment whose `expires_at` has passed
>   as `EXPIRED` and emits one `attachment.expired` audit event per row. The sweep's horizon
>   and the MinIO bucket lifecycle horizon of R13.10 are required to agree; independently of
>   that agreement, the read path refuses to presign an attachment whose `expires_at` has
>   passed, regardless of its recorded status.

The final clause extends the clarification §11 originally proposed. Without it AC-4, the
read-path guard, has no home in the SRS, and Q-2 establishes that the guard is a required
layer rather than a redundancy: the bucket lifecycle and the cron are independent clocks, so
"the two horizons agree" does not by itself close the window. Stating only the agreement
would misrepresent the guard as belt-and-braces.

Note on citations: the line numbers cited for `REQUIREMENTS.md` throughout this dossier were
written against an earlier revision and are offset by roughly 28-33 lines (R13.11 is at
`:694`, not `:666`; the chat audit action row is at `:877`, not `:844`; the
`message_attachments (expires_at)` index row is at `:1368`, not `:1335`). The quoted text at
each location is unchanged, so this is line drift only.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — `ATTACHMENT_TTL` (`attachment_service.py:53`) and
  `settings.minio.chat_uploads_expiry_days` (`settings.py:126`) are two independent
  three-day constants that must agree for expiry to be correct, and nothing enforces or
  documents the coupling. After this dossier lands, divergence produces either premature
  refusal of live attachments or a wider dead-link window. Worth deriving one from the other,
  or asserting their equality at bootstrap.
- **FU-2** — Chat attachments store no content digest (`_create_row_and_audit`,
  `attachment_service.py:211-261`, records filename, mime, size, path, and no checksum),
  which is what makes F-14's data-repair question only partially answerable. A sha256 taken
  at the moment bytes are accepted, rather than over an already-staged file as the RAG and
  knowmap finalizers do (`rag_tus_finalizer.py:83`, `knowmap_tus_finalizer.py:74`), would
  make future integrity questions answerable. Out of scope here because it touches both
  upload boundaries and the schema.
- **FU-3** — The `QUARANTINED` status check is duplicated between
  `attachment_service.py:271-274` and `backend/app/api/v1/attachments.py:106-109`, with the
  route reaching into `service._repo` directly at `attachments.py:105` (annotated
  "intended") before calling the service, which then re-fetches the same row. Two status
  policies in two layers is how one of them gets missed. Worth consolidating into the
  service, which this dossier deliberately does not do because the change is wider than the
  defects it fixes.
- **FU-4** — `AttachmentImage.onError` (`AttachmentImage.vue:52-63`) retries once on any
  image decode failure, including failures that are deterministic properties of the object
  rather than of URL freshness. V-3 removes the SVG case from reaching it, but the retry is
  still unconditional for any future mismatch. A comment recording that the retry exists for
  presign expiry only, or a narrower condition, would prevent the next such mismatch costing
  a wasted round-trip.
- **FU-5, cross-reference, not scope** — Two findings from
  `docs/audits/2026-07-22-conversation-verification-gap/findings.md` live in the very files
  this dossier edits and were routed elsewhere. **V-8** (TUS room authorization is proved
  once at create and never re-proved) proposes re-running `resolve_room_access` +
  `ensure_can_send` inside the `chat_attachment` finalize arm, `tus_service.py:263-277`,
  which is the exact region F-14 modifies with its size reconciliation; it was routed to
  `check-security` alongside the originating audit's F-12, and its findings entry contains
  the full analysis and the reason the exposure is contained. **V-9** (the TUS
  `agent_workspace` purpose is unreachable dead code, `backend/app/api/v1/tus.py:177-200`
  rejected unconditionally by `tus_service.py:117-121`) was routed to `check-quality` with an
  explicit instruction not to cut a bugfix dossier. Recorded here so the next reader of these
  files knows both exist, knows they are deliberately not in this dossier's scope, and does
  not re-raise either as a new discovery. Consult those entries rather than this summary
  before acting on either.
</content>
