---
type: audit
status: reviewed
created: 2026-07-22
requirements: [R13.16, R13.18, R13.21, R13.23, R13.24, R13.25, R24.23]
---

# Audit: closing the verification gap in the agent-to-user conversation audit

## 1. Scope

- **Area** — the eleven candidates that `docs/audits/2026-07-22-agent-to-user-conversation/findings.md`
  §2 records as investigated but never adversarially verified. That audit states the condition
  itself and prescribes the remedy: *"A second pass should verify these before any of them is
  converted into a task dossier."* This is that pass. It examines nothing else.

  The eleven: **M-2 – M-7** (the verification batch was terminated mid-run; M-1 was verified by
  that auditor and is confirmed), **A-6** (reached the verifier, not completed; its reachability
  half was unconfirmed), and **AT-4, AT-5, AT-6, AT-8** (never dispatched).

  Each survived in that document as a single-line description inside §2 and appears nowhere
  else — never promoted to a finding, never refuted. So this pass had to reconstruct each claim
  before it could verify it, and reconstruction is reported per candidate: a description that
  could not be turned into a concrete, cited claim was to be dropped rather than filled in.

- **Intent sources** — `REQUIREMENTS.md` entries as cited per finding, `docs/UI/07-conversation.md`,
  and internal consistency. Where a candidate turned out to have **no** governing intent source,
  that is stated rather than papered over: it changes what the finding can mean, and in one case
  (F-3 below) it is the reason for a caveat.

- **Depth** — one investigation-plus-refutation pass per candidate, three agents grouped by area
  (message/retention/search; attachments and TUS; the activities schema form). Each was
  instructed to default to REFUTED when uncertain, to check `backend/tests/` and
  `frontend/src/**/__tests__/` for tests already pinning the behavior, and to say plainly when a
  one-line description could not be reconstructed.

- **Why this is a separate dossier.** The user directed that verification results be recorded
  separately rather than written back into the other audit, to preserve authorship separation.
  The originating audit remains the record of what its author found; this one records what a
  second pass established about the part that author flagged as unfinished.

## 2. Coverage

**Read closely, per candidate area.** `backend/contexts/conversation/` (message service and
repository, search, retention, export, attachment service, TUS service and store);
`backend/app/api/v1/` (messages, attachments, tus, search); `backend/contexts/agents/application/runtime/transcript.py`
and the compaction region of `turn_engine.py`; `backend/contexts/activities/application/`
(submission service, validators); `frontend/src/slices/conversation/` (message composables,
bubble, search panel, settings views); `frontend/src/slices/activities/` (SchemaForm and its
helpers); plus the corresponding test files in both trees.

**Not covered.** Everything outside the eleven candidates. This pass did not re-verify F-1 – F-22
of the originating audit, did not extend its scope, and ran nothing against a live stack — every
verdict here is a static trace, the same constraint the originating audit records for itself.

**One reconstruction was uncertain and is flagged as such**: M-2's surviving description was
only "moderator UI affordance". The reconstruction below is the reading the surrounding code
best supports, and it is labelled as a reconstruction rather than as the original claim.

## 3. Findings

Numbered `V-n` to keep them distinct from the originating audit's `F-n`. Ordered by severity,
then by disposition.

## V-1: Content deleted from the transcript survives inside a compaction summary

- **Severity**: major if realised
- **Verdict**: **plausible** — the mechanism is fully traced; the magnitude is not statically
  decidable, and is not zero either
- **Origin**: candidate M-6
- **Evidence**: `backend/contexts/agents/application/runtime/transcript.py:176-192` —
  `replace_range_with_summary` performs a single INSERT of the summary text as a `SYSTEM`
  message with `metadata={"type": "compact_summary", "compacted_ids": [...]}`. It does not
  UPDATE, does not set `deleted_at`, and does not touch the folded rows.
  `backend/contexts/conversation/application/message_service.py:339-383` — the delete path:
  `get`, pull attachment paths, `hard_delete`, MinIO removal, audit. No reference to `metadata`,
  `compact_summary` or `compacted_ids`; a repo-wide search for `compacted_ids` returns eleven
  code sites, **none in a deletion path**.
  The summary is user-visible, not merely model-visible:
  `backend/contexts/conversation/infrastructure/repositories/message_repo.py:79-149` (`list`)
  filters on `chatroom_id` and `deleted_at` only, so the row is served to the client and
  `frontend/src/slices/conversation/components/ChatroomMessageBubble.vue:22-34` renders
  `sender_type === 'system'` into the feed; `message_repo.py:289-301` (`all_for_chatroom`)
  applies the same two filters, so it is exported too; and
  `backend/contexts/agents/application/runtime/turn_engine.py:1883-1887` injects it into every
  subsequent turn's prompt.
  Retention is defeated structurally: `backend/contexts/conversation/application/retention_service.py:52-93`
  selects victims by `created_at < horizon`, and a summary is created at compaction time, so it
  is always **newer** than every message it folds.
- **Failure scenario**: room R has an agent bound with `context_mode=compact` and a small
  `context_token_cap`. A user posts a message containing a client name and a contract figure.
  History crosses the cap; the agent's turn folds that message into a summary whose text carries
  the name and figure. The user then deletes their message: the row is hard-deleted, the bubble
  disappears, `message.deleted` is audited. The summary row remains — rendered as a centred
  system line in the same feed, included verbatim in the next export, and injected as
  `[Earlier conversation summary]` into every subsequent turn. Five years later the retention
  purge removes the original and leaves the summary.
- **The untraceable step, stated plainly**: whether a given summary reproduces enough of a given
  deleted message to matter is a property of an LLM's output, not of code. It cannot be
  established statically. The mechanism is fully traced; the magnitude is not.
- **Blast radius**: rooms with at least one compact-mode agent that has actually folded.
  Data-subject deletion, moderation deletion and the retention control are each incomplete on
  that surface, with no signal anywhere.
- **Intent source**: `[R13.16]` (`REQUIREMENTS.md:677`) — deletion "removes messages immediately
  from both DB and search index"; **`[R13.24]` (`:698`)** — "removes the content row and index;
  edit history for that message is also purged", which is explicit that deletion reaches derived
  copies; `[R13.25]` (`:702`).
- **Not a duplicate.** `docs/tasks/2026-07-22-compaction-scoping-and-durability/` covers three
  compaction defects — cross-agent scoping, empty summaries, and the lock/commit misalignment —
  and none of its five follow-ups concerns deletion. This is a fourth defect on the same surface,
  and that dossier's own reasoning is where it becomes visible: its §7 argues repair is safe
  *because* the operation only INSERTs and "the originals are intact". The same property, read in
  the other direction, is exactly this finding.
- **Correction it forces on that dossier**: its AC-9 and §9 assert the transcript change is
  "model-facing" and "never what users see". That is **not accurate for the summary row itself**,
  which `message_repo.list` serves and `ChatroomMessageBubble` renders. The dossier half-concedes
  it when describing an empty summary as a visible divider. That assertion must be corrected
  whether or not this finding is adopted.

## V-2: A hard-deleted message left in the client cache poisons the `before` cursor permanently

- **Severity**: minor
- **Verdict**: confirmed
- **Origin**: candidate M-3
- **Evidence**: `frontend/src/slices/conversation/composables/useChatroomMessages.ts:135`
  (`const oldest = messages.value[0]`), `:139-142` (sends `before: oldest.id`);
  `backend/contexts/conversation/infrastructure/repositories/message_repo.py:93-110` — the anchor
  SELECT filters `deleted_at IS NULL` and raises `ValueError` when it misses;
  `backend/app/api/v1/messages.py:167-168` maps that to **422**. Deletion is a genuine row DELETE
  (`message_repo.py:219-224`, from `message_service.py:354`), so the anchor is unrecoverable.
  `useChatroomMessages.ts:151-155` — a bare `catch` fires a toast and leaves `hasOlderMessages`
  true, so the button persists and every retry repeats the 422.
  **The asymmetry is the tell**: `useChatroomSocket.ts:97-104` handles the identical 422 on the
  `since` cursor with an explicit `BUG-8` comment and falls back to `qc.invalidateQueries`. The
  same failure on `before` was never given a fallback.
  Two reachable sources of a cache entry the client was never told about: the retention purge,
  which hard-deletes and publishes nothing (`retention_service.py:91-93`; the module imports no
  `Publisher`), and a delete during a disconnect (the mechanism of the originating audit's F-11).
  Neither is repaired by a focus refetch — `frontend/src/slices/conversation/utils/mergeMessages.ts:27-29`
  deliberately keeps rows older than the window, with a comment at `:10-11` saying their
  deletions "arrive via the `message.deleted` WS event", precisely the event that was missed; and
  `olderMessages` is a separate ref that no refetch path touches.
- **Failure scenario**: a user pages back three times, so `messages.value[0]` is `m_old`. Their
  socket drops for 20 seconds. The author deletes `m_old`. On reconnect `replayDelta` fetches only
  newer rows, so `m_old` stays cached. The user clicks "Load earlier": the request 422s, a toast
  fires, the button stays. Every subsequent click fails identically. All history older than
  `m_old` is unreachable for the life of the tab.
- **Blast radius**: one tab, one room, until reload. Bounded, but self-perpetuating and silent
  about its cause, and the retention path makes it reachable with no disconnect at all.
- **Intent source**: `[R24.23]`, plus the codebase's own `BUG-8` fallback, which is its statement
  that a dead cursor must degrade to a refetch rather than to an error.

## V-3: SVG attachments are routed to an inline `<img>` the backend deliberately refuses to serve inline

- **Severity**: minor
- **Verdict**: confirmed
- **Origin**: candidate AT-5
- **Evidence**: `frontend/src/slices/conversation/components/ChatroomMessageBubble.vue:311-313` —
  `isImage` tests only `mime.startsWith('image/')`, used at `:113` to select `AttachmentImage`,
  which renders a bare `<img>` (`AttachmentImage.vue:69-76`). Against that,
  `backend/contexts/conversation/application/attachment_service.py:64-74` — `_INLINE_SAFE_MIME`
  is `{png, jpeg, gif, webp, bmp, application/pdf, text/plain}` and `image/svg+xml` is
  **deliberately** absent, so `get_for_download` (`:280-287`) presigns SVG as
  `application/octet-stream` with `Content-Disposition: attachment`.
  **The backend is correct and test-pinned**: `backend/tests/unit/test_attachment_download_disposition.py:80-86`
  parametrizes `image/svg+xml` and asserts `application/octet-stream`, and `:103-108` pins that
  `image/svg+xml; charset=utf-8` is treated identically (SEC-M2).
  The tell is the comment immediately above the predicate, `ChatroomMessageBubble.vue:309-310`,
  asserting that "the presign endpoint forces a safe inline content-type for these MIME types" —
  false for SVG.
  Reachable on both producer paths: agent artifacts (`deploy/sandbox/code-exec/kernel/kernel.py:74-76`
  derives MIME via `mimetypes.guess_type`, so `plot.svg` → `image/svg+xml`, carried unmodified
  through `turn_engine.py:1455-1458`) and direct user uploads
  (`backend/app/api/v1/attachments.py:90` takes `mime or file.content_type` with no allowlist).
- **Failure scenario**: an agent runs `plt.savefig('chart.svg')`. `isImage` returns true,
  `AttachmentImage` mounts, the `<img>` fails to decode octet-stream SVG, `onError`
  (`AttachmentImage.vue:52-63`) invalidates the memo and retries once, the retry fails identically
  because the content type is a property of the object rather than the URL's freshness, and the
  component collapses to an unlabelled filename button. The user asked for a chart and gets a
  download link with no explanation, having burned a second presign round-trip on an
  unrecoverable failure.
- **Blast radius**: every SVG attachment, agent-produced or user-uploaded. The same predicate
  mismatch covers all `image/*` subtypes outside the five-raster allowlist (`avif`, `heic`,
  `tiff`), though for those the outcome is less certain since raster sniffing may still succeed.
  SVG is the certain case.
- **Intent source**: internal inconsistency — the false comment at `ChatroomMessageBubble.vue:309-310`
  against `attachment_service.py:64-74`.
- **Load-bearing note for whoever fixes it**: align the **frontend** predicate to the backend
  allowlist. Do **not** add `image/svg+xml` to `_INLINE_SAFE_MIME` — that would reintroduce
  scriptable markup in the storage origin and break the SEC-M2 test above. The originating §2
  one-liner ("SVG artifacts render broken") misattributes the defect, and a reader who follows it
  to `attachment_service.py` risks "fixing" the security control.

## V-4: Project and org owners get no edit or delete affordance on other users' messages

- **Severity**: minor
- **Verdict**: confirmed (claim reconstructed — see Scope)
- **Origin**: candidate M-2
- **Evidence**: the backend honours both tiers — `backend/app/api/v1/messages.py:472` allows
  delete on `principal.is_admin or access.is_moderator or is_author`, and
  `backend/contexts/conversation/application/message_service.py:258` takes the moderator edit path
  on `authority.is_admin or authority.is_moderator`, where `is_moderator` is project-owner or
  org-owner (`backend/contexts/conversation/application/access.py:47`). The frontend implements
  only the platform-admin half: `useChatroomMessages.ts:53-61` (`canEdit`) and `:63-66`
  (`canDelete`) derive from `session.me?.is_admin` (`:45`) and own-authorship alone, and a
  repo-wide search for `isModerator` across `frontend/src` returns **zero** hits. The DTO cannot
  support the affordance either: `ChatroomOut` (`backend/app/api/v1/chatrooms.py:74-89`) carries
  `workspace_id` but no `project_id` and no role flag.
  **The pattern exists in the same slice and was never applied here**:
  `ChatroomSettingsView.vue:98-111` already resolves project-owner status via
  `projectsApi.listMembers`, built for the observer surface.
  Coverage is one-sided on both sides: `frontend/e2e/13-message-edit-delete.spec.ts:78-94` covers
  only "admin can edit another user message", with a comment at `:87-88` noting `canEdit`
  "short-circuits on `is_admin`"; `backend/tests/unit/test_conversation_services.py:671-676`
  asserts the moderator edit path works at the service layer.
- **Failure scenario**: Alice is Project Owner of P but not a platform admin. A member posts an
  off-policy message in a room under P. The hover toolbar shows neither Edit nor Delete on any
  message but her own, and hers only within five minutes. Both API calls would succeed for her.
  She must escalate to a platform admin or call the API by hand.
- **Blast radius**: moderation is unreachable through the UI for every non-admin project or org
  owner, in every room. No data loss and no disclosure — the backend capability is intact, so this
  is an affordance gap, not an AuthZ gap. **Scope note**: the fix is not one branch. It needs a
  moderator signal on `ChatroomOut`, or a reuse of the `ChatroomSettingsView` member lookup.
- **Intent source**: `[R13.23]` (`REQUIREMENTS.md:697`), `[R13.21]` (`:695`), and
  `docs/UI/07-conversation.md:377-379`, which states the affordance rule verbatim: "Edit visible:
  author within 5 minutes of `created_at`, **or user has admin/owner role**".

## V-5: `oldest_kept_at` carries two different meanings, and is false on every purge chunk but the last

- **Severity**: minor
- **Verdict**: confirmed
- **Origin**: candidate M-7
- **Evidence**: `backend/contexts/conversation/application/retention_service.py:63-64` — on the
  no-op path the field is `min(messages.created_at)`, the true oldest surviving row. At `:104` and
  `:113` — on the purge path it is `horizon`, i.e. `now() - RETENTION` from `:49`. Different
  quantity, same field name. On the purge path the value is an assertion the code has not
  established: `:52-60` selects victims with `LIMIT PURGE_CHUNK` (500, `:29`) and **no
  `ORDER BY`**, so a call deletes an arbitrary 500 of the eligible rows and the room may still
  hold many older than the horizon — while the audit at `:96-107` records
  `oldest_kept_at: horizon` for that room. `backend/app/workers/tasks/retention.py:94-98` loops at
  most 100 chunks, so a backlog above 50 000 messages leaves older rows in place until the next
  night, with 100 audit rows each asserting otherwise.
  No consumer reads the field operationally (`retention.py:97-100` uses only `messages_deleted`
  and `attachments_objects_removed`), which is what makes this an accuracy defect rather than a
  behavioural one. Existing tests pin the empty path only:
  `backend/tests/unit/test_retention_deep.py:104` and `:141`; the two purging tests (`:35-59`,
  `:63-86`) assert only `count` and `action`.
- **Failure scenario**: a room holds 1 200 messages past the horizon, the oldest dated 2019-01-01.
  The sweep runs three chunks. Chunk 1 deletes an arbitrary 500 and emits
  `message.purged_by_retention` with `oldest_kept_at: "2021-07-22"`. The room still holds ~700
  messages from 2019 and 2020. A compliance reader taking the audit at face value concludes
  nothing older than 2021-07-22 remains — wrong by two and a half years, and wrong until chunk 3.
- **Blast radius**: audit and compliance record accuracy for an event `[R13.25]` mandates by name.
  No message is retained or deleted incorrectly; the sweep itself is sound.
- **Intent source**: `[R13.25]` (`REQUIREMENTS.md:702`). Beyond the field's existence this is
  internal inconsistency — one field, two meanings, one of them unestablished by the code that
  sets it.

## V-6: Message search has no tiebreak, so a `LIMIT`-ed result set is not reproducible

- **Severity**: minor
- **Verdict**: confirmed, **with a caveat on what it can mean** (see Intent source)
- **Origin**: candidate M-5
- **Evidence**: `message_repo.py:261` — `.order_by(sa.desc("rank"))` is the sole ordering key, with
  `LIMIT`/`OFFSET` at `:262-263`. `ts_rank_cd` is called at `:246` with no normalization argument,
  so two messages each containing one occurrence at the same weight receive an identical rank —
  the common case in a chat room, not a corner case. With `ORDER BY` on a non-unique key plus
  `LIMIT`, output among equal keys follows plan-dependent input order, so the same query re-run can
  return a different set in a different order. `OFFSET` is exposed publicly to 10 000
  (`backend/app/api/v1/search.py:43`) and passed straight through
  (`message_service.py:113-128`), advertising a paging contract the ordering cannot support.
  No test pins ordering; the only search coverage is transport-shaped
  (`frontend/src/slices/conversation/api/__tests__/index.spec.ts:393-396`).
- **Failure scenario**: a room holds 300 messages each mentioning "revenue" once. A search with the
  default `limit=50` returns an arbitrary 50; re-running the same search returns a different 50.
  Results are also in no chronological order, while `ChatroomSearchPanel.vue:40-58` renders them in
  server order with per-row timestamps and `docs/UI/07-conversation.md:713-723` illustrates them
  ascending by time.
- **Blast radius**: search only. No data loss, no disclosure, no incorrect content — every hit
  returned is a genuine match. What is wrong is that the result set is not reproducible and the
  exposed `offset` cannot be paged safely. Currently bounded further because the frontend never
  sends `offset` (`frontend/src/slices/conversation/api/index.ts:327-332`), so no client hits the
  duplicate-and-skip half today.
- **Intent source**: **absent, and that is part of the finding.** `[R13.18]` and
  `docs/implement/F-chat-realtime.md:226` state no ordering contract. The defect is the absence of
  a deterministic one rather than a deviation from a stated one, so against a strict
  "deviation from documented intent" bar this sits just under the line. Recorded as a finding
  because the exposed `offset` parameter makes the absence user-visible; a reasonable triage could
  demote it to a follow-up.

## V-7: `SchemaForm` asserts an empty array for an untouched optional multi-select

- **Severity**: minor
- **Verdict**: **plausible** (array half); the boolean half of the original candidate is
  **refuted** — see §4
- **Origin**: candidate A-6
- **Evidence**: the defect is in the pure helper, not the component.
  `frontend/src/slices/activities/components/schemaFields.ts:94-95` documents its own contract —
  "Turn the raw input model into the payload object to submit. **Empty optional values are
  omitted**" — and four of its six branches honour it. `:117` does not:
  `case 'enum-array': payload[f.name] = Array.isArray(v) ? v : []`, unconditional. Contrast the
  string branch at `:119-126`, which carries an explicit comment naming exactly this hazard class:
  omit an empty optional string "so a `minLength`/`pattern`/`format` constraint on an optional
  field is not tripped by a blank submission". `minItems` is to an optional array what `minLength`
  is to an optional string; the guard was reasoned through for one kind and never generalised.
  `number`, `enum` and `json` all omit (`:109-114`, `:127-137`), so the offender is anomalous
  within its own switch. Initial state is set at `:65-86` (`enum-array → []`), so an untouched
  control is guaranteed to reach `assemblePayload` in the state that emits the key.
  The server rejects: `backend/contexts/activities/application/submission_service.py:89-91` runs
  full Draft 2020-12 validation and raises `SubmissionPayloadInvalid` → 422. Client-side validation
  cannot catch it: `zodForField` (`schemaFields.ts:162-166`) applies `.min(1)` only when the field
  is `required`, and `jsonSchemaToZod` never reads `minItems`.
- **Failure scenario**: a project owner registers an activity type whose `payload_schema` declares
  an optional `tags` array with `items.enum` and `minItems: 1`. A participant fills the rest and
  submits without ticking a tag. Client Zod passes; the POST carries `tags: []`; `payload_errors`
  returns "[] is too short"; the submit 422s and `ActivityHost.vue:90-96` renders the raw
  jsonschema string. The participant is blocked on a field the form marks optional, with an error
  naming no field, and the only escape is to tick a box representing an answer they did not give.
- **The untraceable step**: the rendering path is fully reachable and is in fact the *only* path
  (`ActivityHost.vue:83-88` falls back to `SchemaForm` whenever `getActivityPlugin` misses, and the
  registry ships empty by design — `plugins/registry.ts:1-3`). What could not be traced is the
  *rejecting schema*: no such `payload_schema` ships, there is no seeded activity type anywhere in
  `backend/smap/`, no frontend UI registers one, and the column defaults to `'{}'::jsonb`
  (`backend/contexts/activities/infrastructure/tables.py:33`), which accepts anything. `minItems`
  on an optional multi-select is ordinary JSON Schema, not exotic — but it cannot be traced
  statically to a configuration that exists.
- **Blast radius**: any activity type whose `payload_schema` declares a non-required `enum` array
  with a non-empty constraint. Zero in the current build; unbounded for operator-authored schemas.
  Failure is a hard-blocked submission with a non-attributable error, not data loss.
- **Intent source**: internal inconsistency — the docstring at `schemaFields.ts:94-95` against
  `:117`, with the reasoning at `:120-122` establishing the policy the array branch breaks.
- **A test currently pins the violation.**
  `frontend/src/slices/activities/__tests__/schemaForm.test.ts:79-83` is titled
  "omits empty optional values" and its second assertion is `expect(payload.tags).toEqual([])` —
  the untouched optional array asserted to be *present*. Any fix must change that assertion, so it
  must be treated as a deliberate-looking pin rather than an oversight during triage.
- **Not the same class as the shared-control defect.**
  `docs/audits/2026-07-22-agent-config-runtime/findings.md` F-22 lives in `shared/ui/SInput.vue`
  and affects every consumer; this lives entirely in slice-local `schemaFields.ts`, and
  `SCheckbox`/`SSelect` are clean. They are related in a way worth recording though:
  `SchemaForm.vue:113-116` carries the F-22 **workaround in its own source**, rendering numbers as
  text inputs precisely so an untouched numeric field is not submitted as a value the user never
  entered. The author was demonstrably alert to the "emit a value the user never entered" class,
  applied the guard to numbers and strings, and left arrays doing what the guard exists to prevent.

## V-8: TUS room authorization is proved once at create and never re-proved

- **Severity**: minor
- **Verdict**: confirmed as stale authorization; **the exposure is contained to a storage write**
- **Origin**: candidate AT-8
- **Evidence**: create gates properly — `backend/app/api/v1/tus.py:100-109` runs
  `resolve_room_access` + `ensure_can_send`, and derives `project_id` from the room rather than
  client metadata. PATCH does not: the only check is upload-token ownership,
  `backend/contexts/conversation/application/tus_service.py:208`
  (`if upload is None or upload.user_id != user_id`). Finalize does not either
  (`tus_service.py:264-277` → `attachment_service.py:165-207`, no access call). The module
  docstring states this as the design (`tus.py:7-9`): the create POST is "the ONLY point the caller
  proves authorisation", and the upload id is "a capability token".
  Reachable: the Redis record TTLs at 24 hours
  (`backend/contexts/conversation/infrastructure/tus_store.py:35`) and every successful PATCH
  refreshes it (`:149`), so a membership revocation between create and the final PATCH is reachable
  across a window up to a day wide, and nothing invalidates in-flight uploads on membership change.
- **Failure scenario**: user U starts a 900 MB TUS upload into room R. Mid-transfer an owner
  removes U from the project. U's remaining PATCHes all succeed and the final PATCH writes 900 MB
  into the room's key prefix plus an `ACTIVE` orphan row.
- **Why the exposure is contained** — each edge checked: U **cannot make it visible**, because
  binding runs only through message creation, which re-gates
  (`backend/app/api/v1/messages.py:189` calls `ensure_can_send` before `bind_to_message`), and the
  repository binding is additionally scoped on `message_id`, `chatroom_id` **and**
  `uploaded_by_user_id` (`attachment_repo.py:126-133`). U **cannot read it back**, because
  `read_attachment` (`attachments.py:112-124`) resolves room access on `row.chatroom_id`. It **does
  not persist**, because the row stays `message_id IS NULL` and is covered by the orphan sweep and
  the 3-day MinIO lifecycle rule.
- **Blast radius**: storage consumption and an orphan row per stale upload, bounded by the
  per-upload cap and the 24h TTL, with an `attachment.uploaded` audit row naming the actor. No
  cross-tenant read, no message visibility, no disclosure. The key prefix's `project_id` is derived
  from the room at create, never from client metadata, so a forged prefix is not available.
- **Intent source**: internal — `tus.py:7-9` states the capability-token design; the finding is
  that the design does not account for the grant expiring mid-upload.
- **Disposition note**: not a standalone bugfix. The proportionate fix is one line — re-run
  `resolve_room_access` + `ensure_can_send` inside the `chat_attachment` finalize arm
  (`tus_service.py:264-277`), costing one query on the last PATCH only. Route to `check-security`
  alongside the originating audit's F-12, since both are "gate proved once, never re-proved" of the
  same shape.

## V-9: The TUS `agent_workspace` purpose is unreachable dead code, not a broken feature

- **Severity**: trivial
- **Verdict**: confirmed, **but the originating one-liner is misleading**
- **Origin**: candidate AT-4
- **Evidence**: `backend/app/api/v1/tus.py:177-200` implements a full `agent_workspace` branch —
  parsing `agent_id`, loading the agent, deriving `project_id`, running a `RESOURCE_CREATE_EDIT`
  decision — and then `tus_service.py:117-121` rejects the purpose unconditionally against an
  allowlist of three, mapping to 400 (`interfaces/error_mapping.py:89-93`). The break is deeper
  than create: the finalizer has no arm for it (`tus_service.py:264-330` branches
  `chat_attachment` / `knowmap_source` / `else: assert upload.rag_config_id is not None`), and
  `TusUpload` has no `agent_id` field at all (`tus_store.py:47-59`), so the agent identity is never
  persisted past create.
  **No caller exists**: `frontend/src/shared/transport/tus.ts:21` types the union as the three
  supported purposes, so `agent_workspace` is not expressible, and the three call sites cover
  exactly those. **The spec never listed it**: `REQUIREMENTS.md:1631` (R22.15.03) names
  `chat_attachment`, `rag_source`, `skill_bundle`. **The real path ships elsewhere**:
  `backend/app/api/v1/agent_workspace.py:70-104` is a 32 MB multipart route whose `_require_edit`
  gate (`:60-67`) duplicates the same decision the dead branch performs. No test pins it.
- **Failure scenario**: only reachable by hand-crafting a TUS creation POST. An agent row read plus
  an authz decision execute, then 400. Wasted work on a probe, nothing more.
- **Blast radius**: none in production traffic. The cost is maintenance — a reader of `tus.py`
  reasonably concludes resumable workspace uploads are supported.
- **Disposition note**: **do not cut a bugfix dossier.** Delete the branch (falling through to the
  existing `else: 403 "purpose is not enabled"` at `:201-206`) or finish it; route to
  `check-quality`. The originating §2 description, "TUS `agent_workspace` purpose always 400s", is
  technically correct but implies a broken user-facing feature; the accurate statement is that the
  branch is unreachable from any shipped client and was never in the spec.

## 4. Refuted Candidates

- **M-4 — a `created_at` tie at a page boundary skips or duplicates a message.** Refuted, and the
  refutation is worth keeping because the guard is easy to miss and someone will re-raise it. The
  keyset is composite on both sides and the two agree: `message_repo.py:92` orders by
  `(created_at DESC, id DESC)` and the `before` predicate at `:111-120` is the matching
  `created_at < anchor.created_at OR (created_at = anchor.created_at AND id < anchor.id)`, with
  `since` mirrored at `:135-145`. The frontend's weaker comparator
  (`useChatroomMessages.ts:102-104`, sorting on `created_at` only) does not defeat it: any tied row
  with a greater id was necessarily returned earlier in the same descending page and is already
  cached, so a cursor picked from the wrong end of a tie yields a strict superset, and duplicates
  are removed by the id filter at `:145-149`. No skip is constructible. Residue, not a defect:
  among same-microsecond messages the rendered order can differ from the backend's canonical order
  — cosmetic, worth at most a `check-quality` note on the comparator.

- **A-6, boolean half — `SchemaForm` emitting `false` for an untouched checkbox.** Refuted.
  Emitting `false` for an unchecked box is the correct reading of the control: an `SCheckbox` in
  this form has no unset state, so absence and `false` are not distinguishable to the user in the
  first place. Breaking it requires `const: true` or `default: true` on an optional boolean, and
  note that `enum` on a boolean routes to the `SSelect` path instead (`schemaFields.ts:28`, enum is
  checked before `type`), so even `enum: [true]` never reaches the checkbox branch. `payload_errors`
  does not apply JSON Schema defaults, so a declared default is not silently overridden at the
  validation boundary either. One worthwhile observation: `SCheckbox` **already supports
  `indeterminate`** (`shared/ui/SCheckbox.vue:7,25-29,48`) and `SchemaForm` never uses it — if
  unanswered-versus-false ever needs to be distinguishable in the research record, the shared
  control is ready and only the form is not.

- **AT-6 — no MIME or type enforcement at either upload boundary.** Refuted **as a functional
  defect**; the claim is literally accurate but describes a posture, not a deviation. Neither
  boundary validates the declared MIME (`backend/app/api/v1/attachments.py:90` takes
  `mime or file.content_type`; `tus_service.py:122-127` requires only non-empty), no magic-byte
  library is a dependency, and no extension allowlist exists — but two deliberate, tested
  downstream controls absorb the risk. First, serve-time override:
  `attachment_service.py:276-287` explicitly does not trust the stored `mime` (its docstring says
  so) and collapses everything outside `_INLINE_SAFE_MIME` to `application/octet-stream` plus
  `Content-Disposition: attachment`, pinned by `test_attachment_download_disposition.py` — so a
  forged MIME buys the uploader nothing at render time, which is precisely why an upload-boundary
  allowlist was not built. Second, the AV scan: `_create_row_and_audit` enqueues
  `file_scan_requested` for every attachment on both boundaries (`attachment_service.py:256`), and
  `record_scan_result` (`:395-417`) flips `status` to quarantined, which both `read_attachment`
  and `get_for_download` refuse. Enforcement is also **symmetric** — neither boundary validates —
  so there is no one-sided gap to file, and `[R22.15.03]` mandates only that `mime` be *present*,
  which is what the code enforces. Route to `check-security` as a defence-in-depth question.
  **One residual worth carrying**: agent artifacts skip the AV scan entirely —
  `persist_agent_artifacts` (`attachment_service.py:349-391`) calls `create_agent_artifact`
  directly and never `_create_row_and_audit`, with a docstring at `:357-359` stating "AV scan
  skipped — the bytes came from the gVisor sandbox". That is a recorded trust decision about
  sandbox output, not an oversight.

## 5. Hand-off

Triaged 2026-07-22 in the same pass that commissioned this audit: the user elected to fix every
finding across all three concurrent audits, so every row below is `fix` unless its own disposition
note routes it elsewhere.

| Finding | Decision | Task dossier |
|---|---|---|
| V-1 | fix | Append to `docs/tasks/2026-07-22-compaction-scoping-and-durability/` — same rows, same maintenance command, same test seam. Its AC-9/§9 "never what users see" assertion must be corrected regardless. |
| V-2 | fix | Group with the originating audit's F-11 (reconnect reconciliation) — same cache, same missed-frame cause |
| V-3 | fix | Group with the originating audit's F-3 (attachment lifecycle) |
| V-4 | fix | Group with the originating audit's F-7/F-8 (settings and permission affordances) |
| V-5 | fix | Group with retention work |
| V-6 | fix | Standalone, or demote to a follow-up per its Intent-source caveat — the user's call at spec time |
| V-7 | fix | Group with the originating audit's F-20 (activities) |
| V-8 | route | `check-security`, alongside the originating audit's F-12 |
| V-9 | route | `check-quality` — delete or finish the dead branch; not a bugfix |

## 6. Out-of-scope Observations

- **FU-1** — The originating audit's §2 one-line descriptions were, in two cases, actively
  misleading rather than merely terse: AT-4's "always 400s" implies a broken feature that no
  shipped client can reach, and AT-5's "SVG artifacts render broken" points at the backend, whose
  behaviour is correct and test-pinned, so a reader following it would weaken SEC-M2. Terminating a
  verification batch mid-run leaves descriptions that were written as *hypotheses* looking like
  *conclusions*. Worth a note in the audit skill: unverified candidates should be phrased as
  questions.
- **FU-2** — `frontend/src/slices/conversation/utils/mergeMessages.ts:10-11` documents that
  out-of-window deletions "arrive via the `message.deleted` WS event". The retention purge never
  emits that event (`retention_service.py:91-93` publishes nothing and the module imports no
  `Publisher`), so the stated contract has a hole independent of V-2. Either emit on purge, or
  correct the comment.
- **FU-3** — `retention_service.py:52-60` selects purge victims with no `ORDER BY`, so a chunked
  purge deletes an arbitrary subset rather than the oldest first. V-5 is the audit-accuracy
  consequence; the arbitrary ordering is worth considering on its own, since oldest-first would
  make partial progress monotone and the audit field truthful for free.
- **FU-4** — `SCheckbox` supports `indeterminate` and `SchemaForm` never uses it (see §4). If the
  research record ever needs unanswered distinguished from false, the shared control is ready.
</content>
