---
type: audit
status: reviewed
created: 2026-07-22
requirements: [R6.02, R13.04, R13.06, R13.08, R13.11, R13.16, R13.17, R13.19, R13.20, R13.23, R13.25, R18.02, R19.03, R22.15.04, R24.14, R24.23, R28.06, R28.07, R28.14, R30.01]
---

# Audit: Agent-to-User Conversation Runtime

## 1. Scope

- **Area** — agent-to-user runtime behavior: streaming event lifecycle and ordering;
  WebSocket connection lifecycle (auth, reconnect, presence, typing); message lifecycle as
  the user experiences it (create/edit/delete/page/search/export); observer-agent
  observation release; structured activities; frontend conversation-slice state fidelity;
  attachments, uploads and agent artifacts; room access modes, guest links and
  notifications.

  This is the second of two dossiers agreed with the user. The first,
  `docs/audits/2026-07-22-agent-to-agent-orchestration/`, covers agent-to-agent
  orchestration.

- **Relationship to concurrent audits** — two other audits of the same codebase carry the
  same date and overlap this one:
  `docs/audits/2026-07-22-agent-config-runtime/` (32 findings, triaged) and this audit's
  own sibling above. Overlaps are recorded per finding and in §4; candidates that merely
  restate a finding already on the books were refuted rather than renumbered here.

- **Intent sources** — `REQUIREMENTS.md` §5.2 (permission matrix), §6 (accounts), §13
  (chat rooms, all subsections), §18 (notifications), §19 (rate limiting), §22.14-22.15
  (WebSocket and TUS endpoints), §24 (frontend architecture), §28 (observer agents), §30
  (structured activities); `docs/UI/07-conversation.md`, `10-notifications.md`,
  `12-shared-patterns.md`; `docs/implement/F-chat-realtime.md`;
  `docs/observer-agents/`; the `docs/tasks/2026-07-13-activities-*`,
  `2026-07-03-observer-*`, and `2026-07-19-*` dossiers.

- **Depth** — thorough for investigation, **incomplete for verification**. Eight read-only
  lenses produced 47 candidates. Five adversarial verification batches were run; one batch
  (message lifecycle, M-1..M-7) was terminated by a session limit before completing, and a
  further set of candidates was never dispatched. §2 lists exactly what remains unverified.
  Of the candidates that were verified, 11 were refuted or reclassified.

## 2. Coverage

**Read closely.** `contexts/conversation/` (message, chatroom, access, attachment, tus,
observation, retention, export services and their repositories);
`shared_kernel/realtime/` (connection, ws_auth, pubsub, distributed_lock);
`app/api/ws/chatroom.py` and siblings; `app/api/v1/` (messages, chatrooms, exports,
attachments, tus, observations, activities, search, guests); `contexts/activities/` in
full; `contexts/notification/` in full; the emit paths in
`contexts/agents/application/runtime/turn_engine.py`; `frontend/src/slices/conversation/`
in full; `frontend/src/shared/transport/ws-manager.ts`;
`frontend/src/slices/{activities,notifications}/`.

**Sampled, not read in full.** `contexts/knowledge/`; `frontend/src/slices/agents/` (only
where a conversation surface reached into it); the sandbox kernel
(`deploy/sandbox/code-exec/kernel/kernel.py`) was read only for artifact MIME derivation.

**Not covered.** Anything requiring a running stack — every finding here is derived
statically. No e2e or Playwright verification was run. i18n key completeness was not
checked. Performance and load behavior were not examined.

**Verification gaps — findings below are marked `unverified` where they apply.** The
following candidates were investigated but never adversarially verified, and must not be
read as carrying the same confidence as the rest:

- **M-2..M-7** (moderator UI affordance; stale `before` cursor after a hard delete;
  `created_at` tie at a page boundary; search tiebreak; deleted content surviving in a
  compaction summary; `oldest_kept_at` semantics) — the verification batch was terminated
  mid-run. **M-1 was verified directly by the auditor** and is confirmed.
- **A-6** (SchemaForm emitting keys for untouched optional booleans/arrays) — reached the
  verifier but was not completed; its reachability half is unconfirmed.
- **AT-4, AT-5, AT-6, AT-8** (TUS `agent_workspace` purpose always 400s; SVG artifacts
  rendering broken; no MIME/type enforcement at either upload boundary; TUS room
  authorization proved only at create) — never dispatched for verification.

A second pass should verify these before any of them is converted into a task dossier.

## 3. Findings

Ordered by severity. Never renumber — F-n identifiers are cited from spec dossiers.

Note on coupling: **F-1 is the root cause several others depend on.** F-8, F-11 and F-13
are defects only because F-1 keeps focused tabs reconnecting every two minutes; F-11
conversely becomes *worse* once F-1 is fixed. Re-derive all four together.

---

## F-1: No client ever sends `ping`, so the server reaps every idle socket every 120 seconds

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/shared_kernel/realtime/connection.py:49-54` documents a periodic
  client `ping`; `:259-269` closes with 1013 after `_IDLE_TIMEOUT_SECONDS = 120` without a
  `receive_text()`. No client sends one — grep for `'ping'` across `frontend/src` returns
  zero, and the only outbound frames are `typing.start`/`typing.stop`
  (`ChatroomView.vue:632,637`, user-driven) and `refresh` (`ws-manager.ts:286`) at
  `exp − 60s` with `access_ttl_seconds = 900` (`settings.py:154`), i.e. ~840s — seven times
  the timeout. The `ping`/`pong` handler at `connection.py:298-300` has no caller.
- **The premise survived direct attack**: protocol-level pings do **not** reset the
  timeout. `uvicorn/protocols/websockets/websockets_impl.py:360-385` builds
  `{"type": "websocket.receive"}` only from data frames; `wsproto_impl.py:136-137,218`
  routes `events.Ping` to `handle_ping()` (auto-Pong) and never enqueues a receive;
  `starlette/websockets.py:116-121` reads only that message type. Uvicorn's own
  `ws_ping_interval/ws_ping_timeout` defaults (`uvicorn/config.py:188-189`) keep the TCP
  link healthy, which is precisely why the application-level reaper is the only thing
  killing the socket.
- **Failure scenario**: open any chatroom (or merely log in — `/ws/user/{id}` is affected
  identically) and touch nothing. At t=120s the server closes 1013.
  `ws-manager.ts:152-156` reconnects at the 1s floor. `chatroom.py:130-142` publishes
  `presence.left` — and `_notify_presence(has_live_users=False)` when it was the last
  member — then `presence.joined` on reopen. Perpetual ~121s churn per socket, each cycle
  costing a ws-ticket mint, a handshake, a `replayDelta` + `resyncPresence` +
  `resyncActivation` HTTP burst, and an agent silence-timer transition.
- **Blast radius**: every connected client, continuously. Presence flaps visibly to other
  room members; agent silence timers oscillate; each ~1s gap drops pushed events (F-8,
  F-11, F-13).
- **Intent source**: R13.19/R13.20 (§13.7); §22.14; `docs/implement/F-chat-realtime.md:170-179`.

## F-2: Chat export ignores the permission matrix — any room reader exports everyone's messages

- **Severity**: major
- **Verdict**: confirmed (verified directly by the auditor, not by a delegated pass)
- **Evidence**: `Capability.CHAT_EXPORT` is defined at
  `backend/shared_kernel/auth/permissions.py:66` and mapped at `:234-240` with
  `ORG_MEMBER`, `PROJECT_MEMBER` and `GUEST` → `Outcome.OWN_ONLY`. A repo-wide grep for
  `CHAT_EXPORT` across `backend/` returns **only** those two sites plus
  `tests/integration/test_permission_matrix.py:102` — **no route or service references it**.
  `Outcome.OWN_ONLY` is honored only inside `decide()` (`permissions.py:336`), which
  requires an explicit call. `create_export` (`app/api/v1/exports.py:89-118`) calls
  `resolve_room_access` + `ensure_can_read` (`:97-102`) and never calls `decide()`.
  Downstream, `chat_export_service.py:77-95` re-checks only `ensure_can_read` and then
  reads `messages.all_for_chatroom(...)`, whose window is room + date only
  (`message_repo.py:272-301`) with no sender filter.
- **Failure scenario**: room R has `allow_guest_links = true`. A guest enrolls via the
  permanent link, opens the room, and POSTs `/{R}/export` with `format=json`,
  `date_range=all`. `ensure_can_read` passes because the guest satisfies the room flag
  tier. The worker builds a manifest containing `content_md`, sanitized HTML, the full
  `edits[]` history, and attachment `minio_path` for every message by every participant,
  and the status endpoint returns a presigned URL.
- **Blast radius**: confidentiality. Matrix row 19 exists specifically to prevent this and
  is dead code; the integration test that "covers" it exercises the matrix table with a
  `FakeResolver`, never the route.
- **Intent source**: REQUIREMENTS §5.2 row 19; R13.17.

## F-3: An attachment's bytes are deleted after 3 days but the row stays `ACTIVE`, so the UI offers a dead link

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `attachment_repo.py:260-286` defines `mark_expired` and `list_expired`; a
  repo-wide grep returns only those definitions plus a docstring reference at `:296` —
  **zero callers**. No `attachment.expired` audit is ever emitted, though
  `REQUIREMENTS.md:844` lists it as a required action and `:1335` documents the
  `(expires_at)` index as being "for the nightly expiry sweep". Meanwhile
  `smap/bootstrap/minio_init.py:67-76,141-149` installs the lifecycle rule with
  `Filter(prefix="")` — **bucket-wide** on `bucket_chat_uploads`, `Expiration(days=3)`
  (`settings.py:126`) — so it deletes message-bound objects, not just staging orphans.
  `expires_at` is stamped on every attachment at creation
  (`attachment_service.py:224,371`), but the only sweep that reads it
  (`facade.py:272-295`) is restricted to `message_id IS NULL`, so bound rows are never
  touched. `get_for_download` (`attachment_service.py:265-295`) checks `QUARANTINED` only,
  never `expires_at`, and presigning does not verify the object exists. The frontend's
  `[attachment expired]` branch (`ChatroomMessageBubble.vue:127-140`) is reachable only
  for a status nothing ever writes.
- **Failure scenario**: a user attaches `report.pdf` and sends. On day 4 MinIO deletes the
  object. The message still renders a live paperclip link; clicking it returns 200 with a
  presigned URL, and the browser receives a MinIO `NoSuchKey` XML body. For an image,
  `AttachmentImage.vue:52-63` retries once and then falls back to an unlabelled filename
  button — never "expired". Nothing in the audit log explains it.
- **Blast radius**: every attachment older than three days, i.e. all of them eventually.
  Spec'd behavior is absent end to end: no sweep, no audit action, and a dead UI branch.
- **Intent source**: R13.11 (`REQUIREMENTS.md:666`); `REQUIREMENTS.md:844`, `:1335`.

## F-4: Hitting the per-user socket cap produces an unbounded 1 Hz reconnect storm

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `connection.py:212-218` calls `ws.accept(...)` and *then*
  `ws.close(1013, "per-user WS cap reached")`. The browser completes the 101 handshake
  before processing the close, so `onopen` fires — and `ws-manager.ts:135-141` resets
  `backoff = INITIAL_BACKOFF_MS` and `consecutiveFailures = 0`. `onclose` (`:152-156`)
  takes no event argument, so the 1013 code is never read; `scheduleReconnect` (`:217-224`)
  re-increments to 1, below `DEGRADED_THRESHOLD = 3`, so the channel never degrades and
  never backs off. Contrast `chatroom.py:66`, which closes **before** accept (HTTP 4403, no
  `onopen`) and therefore does grow backoff — the inconsistency between the two paths is
  the tell. The cap is per **user**, not per path (`connection.py:90-91`), and each tab
  opens two sockets, so three chatroom tabs reach 6 against
  `ws_concurrent_per_user: 5` (`settings.py:385`).
- **Failure scenario**: a user opens a third chatroom tab. The 6th socket is accepted and
  closed. Every second thereafter: one `POST /api/auth/ws-ticket`, one Redis
  `ZREMRANGEBYSCORE`+`ZADD`+`ZCARD`+`EXPIRE`+`ZREM`, one WS handshake. The status pill
  oscillates live/reconnecting rather than settling on `degraded`, so the REST polling
  fallback never engages either.
- **Blast radius**: server-side load proportional to the number of over-cap users; the
  user sees a flickering connection indicator and no explanation. Self-resolves only when
  a tab is closed.
- **Intent source**: R19.03 (per-user cap); R24.14 ("reconnect, backoff … centralized").

## F-5: The empty-room presence transition can be permanently missed, leaving agent silence timers armed

- **Severity**: major
- **Verdict**: confirmed (impact re-framed from the original claim)
- **Evidence**: `presence.py:150` `list_room` is a bare `SMEMBERS` of the roster key with
  no cross-check against the per-user connection sets, so a crashed pod's ghost entry is
  returned to roster reads; `:143` `_ROSTER_LEAVE_LUA` SCARDs that ghost-inflated set, so
  `chatroom.py:141` never observes `roster_size == 0`; `:181-189` SREMs without publishing
  `presence.left`; `:118` a live user's heartbeat re-EXPIREs the room key. The only
  reconciler is `_scrub_stale_presence`, called solely from `retention.py:680-707`, wired
  at `:751` into the nightly `retention_sweep` (`app/workers/main.py:318`, 03:30 UTC).
- **Correction to the original claim**: the "~18h ghost" figure is wrong in general —
  `_SET_TTL_SECONDS = 300` (`presence.py:36`) expires the roster key within five minutes
  once nothing heartbeats the room. But that makes the outcome **worse**, not better: when
  the key expires that way the room never lands in the sweep's `emptied_rooms`, so
  `evaluate_presence_change(has_live_users=False)` (`retention.py:701`) never fires for it
  at all. The transition is permanently lost rather than merely delayed.
- **Failure scenario**: a backend pod is OOM-killed while user U is connected to room R.
  `on_close` never runs, so U stays in `ws:presence:{R}`. No further joins occur; the
  roster key expires at T+300s. The `has_live_users=False` edge is never delivered, so R's
  bound agents keep their silence timers armed and can self-open into a room with nobody
  in it — the exact condition R15.05b's presence gate exists to prevent.
- **Blast radius**: agent turns (and provider spend on the user's own key) into empty
  rooms, after any abnormal backend termination.
- **Intent source**: R13.19 (§13.7); R15.05b; the "at most one TTL window of UI lag"
  contract stated at `presence.py:18-21`.

---

## F-6: A successful turn is recorded as failed when a post-commit publish fails

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `turn_engine.py:2198-2209` — the `message.created` and `agent.finished`
  emits are bare `await`s with no `try`, unlike every other emit in the engine
  (`:1542-1561`, `:2228-2236`, `:2282-2296`, `channels.py:24-31`, all of which swallow).
  `pubsub.py:31-34` propagates whatever `redis.publish` raises, and
  `shared_kernel/auth/clients.py:42-49` sets only `retry_on_timeout=True` — a
  `ConnectionError`, `MISCONF` or `OOM command not allowed` still raises.
- **Failure scenario**: Redis drops between the commit at `:2189` and the emit at `:2200`.
  The reply row is durable, but control jumps to `except Exception` at `:2220`, which
  rolls back (a no-op), writes an `agent.turn_failed` audit for a fully successful turn,
  requeues notifications the agent already consumed, and returns `status="failed"` — so
  `_dispatch_agent_message_signal` (`:2213`) and `_dispatch_agent_reply_wakeups` (`:2217`)
  never run. Workflow `message` triggers and other agents' `every_n`/silence wakeups never
  see a reply that exists in the database. `app/workers/tasks/orchestration.py:146,164-165`
  then writes `wakeup.failed` and skips `on_agent_message_sent`, so the autostop round goes
  uncounted.
- **Blast radius**: gated on Redis availability. On a hard outage the user sees nothing
  either way; the durable damage is the false audit trail, the lost orchestration signals,
  and the duplicated notifications injected into the agent's next turn.
- **Intent source**: internal inconsistency — the dispatch docstrings at `:2210-2217`
  state these are "best-effort, post-commit — never fails the turn", which the unguarded
  emit above them breaks.

## F-7: Access-mode toggles never revert when the save is rejected

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `ChatroomSettingsView.vue:160-163` — `setFlag` mutates `flags[key]` then
  `void onSave()`; `useChatroomSettings.ts:101-113` — the non-409 catch sets `saveError`
  only, with no revert and no toast. The correct pattern is implemented in the same file at
  `:125-157` (`saveDisclosure` reverts at `:150-153`) and was never applied to `setFlag`.
- **One sub-claim refuted**: the "double-toggle is silently dropped" half does not hold —
  every toggle carries `:disabled="saving"` (`:337,356,372,388`) and `onSave` sets
  `saving.value = true` synchronously before its first `await` (`useChatroomSettings.ts:85`),
  so the UI blocks the second toggle rather than dropping it.
- **Failure scenario**: the creator flips "Allow guest links" on; the PATCH returns 500 or
  403. The toggle stays ON and the dependent "Guest Link" card appears
  (`v-if="flags.allow_guest_links"`, `:425`), while the only error signal is an inline
  `SAlert` rendered in the General card far above. The operator believes external guest
  access is open when it is not. A separate minor path: `onSave` always sends
  `name: name.value` (`:95`), so flipping any toggle commits a half-typed rename that has
  its own gated Save button.
- **Blast radius**: a security-relevant control displaying a state the server rejected.
  Marked plausible rather than confirmed because the failure requires a rejected PATCH,
  which was not reproduced against a running stack.
- **Intent source**: `docs/UI/07-conversation.md:1157-1158` — "On error: toggle reverts,
  `useToast().error()` with failure message."

## F-8: A settings form painted from a stale cache silently reverts another user's changes after a 409

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `useChatroomSettings.ts:65-73` — `loadRoom` returns straight out of
  `findInCache()` with no background revalidation, so both the form fields and
  `room.value.version` can be arbitrarily stale. `:102-108` — the 409 branch assigns
  `room.value = await getChatroom(...)` but never calls `applyRoom`, unlike
  `saveDisclosure:145` which does and documents why.
- **Failure scenario**: A and B both open settings. B renames the room. A, painting from
  the stale list cache, toggles a flag → 409 → `room.value` refreshes to B's version while
  `name.value` and `flags` still hold A's stale values → A clicks Save again → it succeeds
  with the fresh version and silently reverts B's rename. The 409 mechanism that exists to
  prevent exactly this instead launders the stale write.
- **Blast radius**: last-write-wins on room settings whenever two people have the page open.
- **Intent source**: `docs/UI/12-shared-patterns.md:305-309` §4.3 Optimistic Concurrency.

## F-9: `/compact` failure clears the composer, reports nothing, and raises an unhandled rejection

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `useChatroomMessages.ts:195-199` — the slash-command branch sets
  `draft.value = ''` then `await compactChatroom(chatroomId)` with no try/catch; the
  surrounding try begins only at `:229`, after the early return.
  `ChatroomView.vue:612-615` — `send()` awaits `onSend` with no catch and is bound as an
  event handler, so the rejection is unhandled. The settings path does it correctly
  (`ChatroomSettingsView.vue:188-196`, try/catch/finally with both toasts).
- **One sub-claim refuted**: a separate audit's report that the compact endpoint always
  403s for non-admins does **not** hold — `chatrooms.py:598-619` gates on
  `Capability.CHAT_SEND`, which any sending member has. The trigger is a genuine transport
  or 5xx failure, not every non-admin call.
- **Failure scenario**: a user types `/compact` while the backend is restarting. The input
  clears, no toast fires, no error state renders, and the user waits for a compaction that
  never happened.
- **Intent source**: `docs/UI/12-shared-patterns.md:451-455` (every optimistic action has a
  rollback and an error toast).

## F-10: Observations are stranded when the last observer binding is removed

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `ChatroomView.vue:441-443` — `showObserverTab = isCreator &&
  observerAgents.length > 0`; `useObservations.ts:75-78` derives that roster purely from
  `boundAgents.filter(role === 'observer')`, never from the observations themselves.
  `ObserverPanel` is mounted in exactly two places (`ChatroomView.vue:152`, `:209`), both
  inside `<template #tab-observer>` of an `STabs` whose observer entry is conditional
  (`:454-463`); no route, admin view or settings page renders it, and no other component
  calls `listObservations`. The backend keeps serving the rows —
  `observation_repo.py:91-137` filters on `chatroom_id` and `deleted_at` only, and
  `_require_creator` (`observations.py:106-114`) never consults bindings.
- **Failure scenario**: a creator binds agent W as observer, W records six observations,
  none released. The creator then decides W should join the conversation and flips its role
  to `normal` (`useChatroomBindings.ts:112-124`) or unbinds it (`:126-138`). The Observer
  tab disappears entirely — it does not render empty. The six private analyses remain live
  in `agent_observations` with no UI affordance to read, release (R28.06/R28.07) or
  soft-delete them (R28.14).
- **Blast radius**: recoverable by rebinding any agent as an observer, and nothing is
  disclosed to anyone new — which is why this is minor rather than major.
- **Intent source**: R28.03, R28.06, R28.07, R28.14; `docs/observer-agents/B-frontend.md` §B.2/§B.3.

## F-11: Message edits and deletions that occur during a disconnect are never reconciled

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `message_repo.py:121-136` — the `since` query returns strictly-later rows
  and filters `deleted_at IS NULL`; it carries no tombstones and no edits of older rows.
  `useChatroomSocket.ts:84-105` `replayDelta` appends only, via `applyMessageCreated`
  (`:135-149`).
- **A corrective path the original claim missed**: `useChatroomMessages.ts:79-86` runs
  `mergeMessages(prev, page)`, and `mergeMessages.ts:26-31` drops any cached row inside the
  refetched window that is absent from the fresh page. With no `staleTime` override in
  `query-client.ts`, a tab blur→focus fully reconciles both deletes and edits.
- **Failure scenario**: user A has message M rendered. Their socket drops. Author B deletes
  M — the `message.deleted` frame goes to a channel A is not subscribed to. A reconnects;
  `replayDelta` fetches only newer messages. M stays rendered, including when it was
  deleted for moderation or compliance reasons — for as long as the tab keeps focus.
- **Blast radius**: bounded by the window-focus refetch, and therefore **contingent on F-1**
  — which currently guarantees a focused tab reconnects every two minutes.
- **Intent source**: R24.23 ("On reconnect, composables replay a delta fetch … to avoid
  gaps"); R13.20.

## F-12: Any room member can close another participant's activity session

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `session_service.py:64-68` validates only
  `session is None or session.chatroom_id != chatroom_id` — there is no
  `subject_user_id` comparison anywhere in the file. The route gates at
  `resolve_room_access` + `ensure_can_send` (`activities.py:336-346`), i.e. any member who
  can post. The identifier is readable: `list_activity_submissions` (`:388-398`) gates on
  `ensure_can_read` and returns every room submission unfiltered, and `_submission_out`
  (`:184-197`) includes `session_id` (`ActivitySubmissionOut.session_id`, `:118`).
- **Failure scenario**: participants A and B both join an activated type. B reads A's
  `session_id` from `GET /activity-submissions`, then calls
  `PATCH .../activity-sessions/{A_session}/close`. It succeeds. A's next submission finds
  no open session and `_resolve_session` (`submission_service.py:322-329`) lazily opens a
  fresh one; `next_attempt_no` (`submission_repo.py:76-88`) is scoped to `session_id`
  alone, so A restarts at attempt 1. A's attempt history is split across two sessions, and
  every per-session aggregate and the `rolling.same_error_count` are corrupted for A.
- **Blast radius**: research-record integrity for the affected participant. Arguably an
  AuthZ gap; flagged here as the functional consequence, and worth routing to
  `check-security` for the authorization view.
- **Intent source**: R30.01 (an `ActivitySession` groups **a subject's** submissions and
  carries a server-assigned monotonic attempt number).

## F-13: Approvals raised while a socket is disconnected never appear

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `useChatroomSocket.ts:340-357` resyncs messages, presence and activation on
  connect but never re-fetches approvals; `:268-291` are the sole writers into the
  orchestration store, and `orchestration.ts:17` is pure in-memory WS state with no fetch.
  The only REST list is `listApprovalsForRun(workflowRunId)`
  (`frontend/src/slices/workflow/api/index.ts:130`, backend
  `app/api/v1/orchestration.py:230`), keyed by run and never called from the conversation
  slice; no chatroom-scoped approvals endpoint exists.
- **Failure scenario**: a workflow requests approval during one of F-1's reconnect gaps.
  Redis publishes to a channel with no subscriber and the frame evaporates. On reconnect
  only the message delta runs. The approval card never renders, and because
  `orchestration.ts:33` makes the later `approval.resolved` a no-op on an unknown id, the
  gate stays invisible for the rest of the run.
- **Blast radius**: smaller than it first appears — `ApprovalCard.vue` is display-only;
  agents vote, humans do not. A missed frame is an observability gap, not a stalled gate.
- **Intent source**: R24.23; R13.20.

## F-14: A partial TUS chunk write is never truncated, producing a corrupt file recorded as valid

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `tus_service.py:229-248` — `_append` opens the staging file in `"ab"` and
  writes; the `except OSError` handler rolls back only the Redis offset (`:239`) and
  re-raises. There is no `truncate()` back to the pre-write size and no size
  reconciliation. `:266-277` finalizes with `size_bytes=upload.upload_length`, the
  client-**declared** length. `attachment_service.py:165-207` uploads whatever is on disk
  via `put_file(file_path=staging_path)` and then records the declared value — no
  `os.path.getsize` check, no checksum, no length assertion anywhere on the path.
- **Failure scenario**: the staging volume hits ENOSPC after 4 MB of a 16 MB chunk has been
  flushed. The Redis offset rolls back and the client retries the same chunk, which is
  appended after the orphaned 4 MB. Offsets still reach `upload_length`, so finalization
  proceeds: a file 4 MB longer than declared, with 4 MB of duplicate bytes wedged
  mid-stream, is uploaded and recorded with the smaller declared size. It downloads as a
  corrupt PDF or archive with no error anywhere.
- **Blast radius**: requires a partial-write disk fault, not ordinary operation — hence
  minor despite the outcome being silent data corruption.
- **Intent source**: R22.15.04 (server-side enforcement of offsets); the module's own
  contract that PATCH appends at the server's prior offset.

## F-15: The turn watchdog fires on healthy turns during the silent pre-stream window

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: the frontend watchdog (`useChatroomSocket.ts:160-170`,
  `AGENT_THINKING_TIMEOUT_MS = 120_000`) is re-armed only by `agent.thinking`,
  `agent.token` and `agent.finished`. Between `agent.thinking` (`turn_engine.py:1783`) and
  the first `agent.token` (`:2677`, reached via `:2106`) the only emit in the engine is
  `:1542` — `agent.warning`, for which the frontend has no case, so it does not re-arm.
  That window contains `_pending_context_and_tools` (`:1796`), `_resolve_skills` (`:1817`),
  `_stage_workspace_inputs` (`:1821`) and `_assemble_history` (`:1868`), which at `:2526`
  takes `distributed_lock("compact:lock:{room}", ttl_s=300)` and may spend a full
  summariser provider call (documented at `:2062-2067`). `STREAM_TIMEOUT` is a per-read
  timeout, not a wall clock, so exceeding 120s is reachable. When it fires,
  `clearAgentStream(roomId)` at `:167` passes no `agentId` and
  `stores/conversation.ts:116-119` deletes the entire room key.
- **Failure scenario**: an agent is triggered in a compact-mode room whose history crosses
  the cap. `agent.thinking` lands; the compaction summariser call takes over 120s. The
  spinner disappears and a `timeout` error is surfaced for a turn that is running fine; the
  real reply arrives about a minute later with no preceding spinner.
- **Blast radius**: narrower than the room-wide clear suggests — `armThinkingTimeout` is a
  single shared timer re-armed by *any* agent's token, so a second agent's draft is
  collateral damage only if it too is silent for the full 120s. The core defect is the
  spurious user-visible failure on a healthy turn.
- **Intent source**: `docs/UI/07-conversation.md:488` defines the watchdog as detecting a
  *wedged* turn; the state machine at `:457` has no long-assembly state.

## F-16: A stale export poll overwrites the export modal after the user starts a new one

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `usePolling.ts:60-68` — `start(key)` accepts a key but `stop()` is global
  and permanent (`disposed = true`); there is no per-key cancel.
  `useChatroomExport.ts:22-25` — `onResult` assigns `exportJob.value` unconditionally, for
  any key. `ChatroomView.vue:774-777` — `openExport()` nulls `exportJob` and opens the
  modal without stopping the in-flight poller, which was created in the view's setup
  (`:595`) and therefore outlives the modal.
- **Failure scenario**: the user starts a PDF export (job A, polling every 3s for up to 3
  minutes), closes the modal while A is running, then reopens export. Within 3s A's tick
  fires and replaces the fresh configuration form with A's progress bar; when A completes,
  `ChatroomExportModal.vue:14-22` offers a download for the **previous** export's format
  and date range, which the user believes is the one they just requested.
- **Blast radius**: bounded — same room, same user's own earlier export.
- **Intent source**: `docs/UI/12-shared-patterns.md` §5.3, §7.2.

## F-17: The message feed shows "No messages yet" before the first fetch resolves

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `useChatroomMessages.ts:79-86,327-348` destructures the `useQuery` result
  into `messages` only and returns no `isLoading`/`isPending`/`isError`.
  `ChatroomView.vue:97-103` gates the empty state purely on
  `!messages.length && !streamingEntries.length && !liveApprovals.length`; a grep for
  `isLoading|isPending|Skeleton` in that view returns only `loadingOlder` and the observer
  panel's own flags. `hasOlderMessages` initialises to `true`
  (`useChatroomMessages.ts:72`), so the "Load earlier" button renders above the false empty
  state simultaneously.
- **Failure scenario**: opening a busy chatroom on a cold cache paints
  "No messages yet — Start the conversation…" before the backlog arrives. If
  `GET /messages` 5xxs after TanStack's retries, the false empty state persists until a
  refetch trigger, indistinguishable from a genuinely empty room.
- **Blast radius**: same class as commit `e381559` ("stop the key-group list claiming empty
  before it has loaded"); the fix was never generalised to the message feed, though
  `ObserverPanel.vue:20-39` does branch on loading first.
- **Intent source**: `docs/UI/12-shared-patterns.md:335`; `docs/UI/07-conversation.md:980-1001`.

## F-18: A "typing…" indicator sticks when the typing user has a second connection

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `chatroom.py:130-142` publishes `presence.left` only when the closing
  socket was the user's last (`presence.py:139-141` returns `left=False` otherwise), and
  never publishes `typing.stop` at all. The client clears typing only on `typing.stop` or
  `presence.left` (`useChatroomSocket.ts:216-224`); there is no server-side typing TTL
  (`chatroom.py:75-84` merely republishes) and no client expiry timer
  (`stores/conversation.ts:41-51` are plain set operations).
- **Failure scenario**: a user has a room open in two tabs. In tab 1 they start typing;
  before the 3s debounce sends `typing.stop` (`ChatroomView.vue:636-639`) tab 1's network
  drops. `presence.leave` sees tab 2 still connected, so neither `presence.left` nor
  `typing.stop` is published. Other members see "U is typing…" indefinitely.
- **Blast radius**: two corrections narrow this. `ChatroomView.vue:683` filters
  `uid !== myId`, so only *other* users are affected, not the typist's own sibling tab; and
  `resyncPresence` calls `store.clearTyping(roomId)` on every reconnect
  (`useChatroomSocket.ts:111`), which under F-1 happens every two minutes. "Forever" is
  false in the current build and becomes true only once F-1 is fixed.
- **Intent source**: R13.19 typing indicators (§13.7).

## F-19: `message.updated` refetches are unsequenced, so two rapid edits can land out of order

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `useChatroomSocket.ts:188-201` fires `getMessage(updatedId)` and writes
  `prev?.map(...)` on resolve, with no generation counter and no version comparison — while
  every sibling async path in the same file does guard (`replayGeneration` at `:67,92,95`;
  `activationGeneration` at `:68,118-127`; tombstones for `message.deleted` at `:76-82`).
- **Failure scenario**: an author edits a message twice within one round-trip. Two
  `message.updated` frames produce two `GET /messages/{id}` calls; edit 2's response
  returns first and edit 1's second, so the cache is left holding edit 1's content **and**
  its stale version — which `useChatroomMessageEditing.startEdit` then sends as `If-Match`,
  producing a spurious 412 on the next edit.
- **Intent source**: `docs/UI/07-conversation.md` WS reference table; R24.23's ordering
  discipline, which the sibling handlers follow.

## F-20: The stalled-validation watchdog notifies nobody

- **Severity**: minor
- **Verdict**: confirmed (one half of the original claim refuted)
- **Evidence**: `app/workers/tasks/activities.py:164-187` calls only `sweep_stalled` plus
  `audit.emit`; neither `_emit_validated` (`:86`) nor `_emit_activity_signal` (`:98`)
  appears on that path. `submission_repo.py:210-240` is a bulk
  `UPDATE ... WHERE id IN (batch)` returning a rowcount, so it never surfaces the swept ids
  — emitting would require a repository change. No `refetchInterval`, `useQuery` or
  invalidation exists anywhere in `frontend/src/slices/activities`: submission status lives
  entirely in the Pinia store (`stores/activities.ts:53`) fed by the WS `activity.validated`
  event and rendered by `ActivityOutcomeBadge.vue:25-26`.
- **The workflow half is refuted**: `wait_for_event.py:94-101` parks with
  `timeout_ms = timeout_seconds * 1000` (default 600s at `:45`), and since that is shorter
  than `_PENDING_TTL_SECONDS = 900` (`activities.py:32`), a parked node times out *before*
  the watchdog fires. Nothing waits forever.
- **Failure scenario**: an MCP validator stalls past 900s. The watchdog writes `error` to
  the database, but the participant's badge stays on the pending clock icon until the
  component remounts, and an impasse rule listening on the `activity` signal never sees the
  error verdict.
- **Intent source**: R30.06 + R30.12; `docs/tasks/2026-07-13-activities-plugin-sdk` §7 NFR.

## F-21: A release wake-up to an agent unbound in the race window fails silently

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `turn_engine.py:1724-1727` — the `not_bound` branch emits only when
  `trigger == "mention"`. The intended parity is stated in the worker itself:
  `app/workers/tasks/orchestration.py:80` uses `if trigger in ("mention", "release")` for
  the `agent_gone` guard and `:113` the same tuple for the autostop bypass, with comments at
  `:83-87` and `:106-107` saying release "is the same shape of explicit call and must not
  fail silently either".
- **Failure scenario**: a creator releases an observation privately to agent X with
  `wake=true`. `ObservationService.release` validates X as a normal-role binding and
  commits, then the wake is dispatched as an Arq job. X is unbound before the job runs. The
  job's `role_of` returns `None`, so it returns `skipped:not_bound` emitting nothing on any
  channel. The release UI reports success; the note is never delivered and lingers as
  misrouted until its TTL.
- **Intent source**: R28.07; `docs/observer-agents/A-backend.md` §A.9.

## F-22: Search snippet highlighting is specified, styled, and produced in three incompatible forms

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `message_repo.py:247-252` passes `ts_headline` only
  `MaxWords=35,MinWords=15,ShortWord=3` — no `StartSel`/`StopSel`, so PostgreSQL emits
  `<b>`. `renderMarkdown.ts:47-81` — `ALLOWED_TAGS` contains `b` (`:49`) but not `mark`.
  `ChatroomSearchPanel.vue:165-169` styles `:deep(mark)`.
  `docs/UI/07-conversation.md:751-752` specifies `<mark>` with `--color-warning-tint`.
- **Failure scenario**: a search renders matches in bold rather than the specified yellow
  highlight, and the `:deep(mark)` rule is dead CSS. If the backend were later corrected to
  emit `StartSel=<mark>`, `sanitizeSnippet` would strip the tags and highlighting would
  disappear entirely — so the obvious fix breaks it.
- **Intent source**: `docs/UI/07-conversation.md:751-752`.

## 4. Refuted Candidates

- **Multi-round tool text is streamed then discarded.** Refuted as a *new* finding — the
  mechanism is real and fully verified, but it is verbatim F-40 of
  `docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md`. The additional detail
  worth carrying over: after `MAX_TOOL_ROUNDS` is exhausted, `turn_engine.py:2747-2757`
  re-streams the entire final answer onto the same unreset draft.
- **`agent.warning` has no frontend consumer.** Refuted — already tracked as FU-23 in
  `docs/tasks/2026-07-16-agent-skills/spec.md:3182-3188` (verified 2026-07-17, with a
  prescribed Phase 2 acceptance criterion), and already deliberately not re-filed by the
  config-runtime audit.
- **`agent.finished` clears the draft before the refetched row lands.** Refuted — recorded
  as F-32 in `docs/audits/2026-07-22-agent-config-runtime/`, and that audit's assessment
  ("only the comment is actionable") is correct: `useChatroomSocket.test.ts:138-148` and
  `:150-159` both fail if the clear is made conditional.
- **A stale socket's `onclose` degrades a healthy channel.** Refuted — the mechanism exists
  (`ws-manager.ts:180-183` nulls the socket with handlers attached, `:135-156` have no
  identity guard), but its only trigger is `onDeactivated`, and a repo-wide search for
  `KeepAlive` in `frontend/src` returns only two comments in `ws-manager.ts:171,174`. No
  `<KeepAlive>` wraps any route, so `disconnect()` is dead code. Additionally `disconnect()`
  sets `paused = true` before closing, and `onclose` is guarded by `!this.paused`.
- **Observer roster status never updates for admin viewers.** Refuted —
  `docs/tasks/2026-07-03-observer-frontend-fixes/spec.md:155-158` explicitly triages this
  sibling: non-recipients legitimately stay `idle` for transient states they cannot
  observe, and no stuck state exists because nothing sets `analyzing` without the WS event.
- **The rolling `same_error_count` window can never fire for async validators.** Refuted —
  the arithmetic does not hold. `_ROLLING_WINDOW_SECONDS = 60`
  (`submission_service.py:51`); the scored row ages out only if validator latency exceeds
  60s, whereas mcp/webhook round-trips are normally seconds. Worked example: submits at
  t=0/20/40 validated at t=5/25/45 yield `count=3`. The implementation also matches
  `docs/tasks/2026-07-13-activities-reactive-rules/spec.md:103-104` verbatim, which anchors
  the window on submission time by design.
- **Ending and restarting an activation merges two windows into one session.** Refuted as a
  defect — `docs/tasks/2026-07-13-activities-activation-ux/spec.md:49-51` lists force-closing
  participant sessions as an explicit non-goal, and Q-3 (`:68`) records that repeated
  attempts within an open session are allowed. The restart consequence is the mechanical
  corollary of a recorded decision. Worth a follow-up (FU-2), not a bugfix.
- **`open_session` is not gated on an active activation.** Refuted — the asymmetry with
  `submit` is real, but submission is the gate that matters and it is closed
  (`submission_service.py:85-87`). A pre-opened session is exactly the intended
  one-open-session-per-subject row the participant would have received anyway.
- **List and aggregate are not snapshot-consistent.** Refuted — platform-core AC-11's
  "in a single query" scopes the *aggregate*, and the same AC specifies the endpoint is
  paginated, which presupposes a separate page read.
- **Inline `b64` artifacts are dropped with no signal.** Refuted — the drop at
  `turn_engine.py:1448-1450` *is* the intended persist-time backstop described in
  `docs/tasks/2026-07-19-large-artifacts-silently-dropped/spec.md` D-6, and the warning at
  `:1463-1473` naming every dropped file with its size is that dossier's AC-8, signed off
  with a test. `_artifact_note`'s docstring (`builtin_tools.py:312-318`) is explicit that
  it asserts *written*, never *returned*.
- **KaTeX output bypasses DOMPurify.** Real inconsistency with the file's own stated
  pipeline (`renderMarkdown.ts:5-8` vs `:130-137`, against the sanitized Mermaid path at
  `:161-163`), but the input is already-DOMPurified `textContent` and KaTeX runs with the
  default `trust: false`, which blocks `\href`/`\url`/`\htmlData`. No exploit found or
  claimed. Zero functional impact — routed to `check-security` as hardening, recorded here
  as FU-3 rather than as a finding.

## 5. Hand-off

Triaged 2026-07-22: the user elected to fix **every** finding. No declines.

**Both preconditions this section set have now been met**, in the order it prescribed.

1. *"the §2 verification gaps must be closed first"* — done, in
   `docs/audits/2026-07-22-conversation-verification-gap/findings.md`. Of the eleven
   candidates: seven confirmed or plausible (recorded there as V-1 – V-9, two of which are
   routed rather than fixed), two refuted (M-4's composite keyset is correct; A-6's boolean
   half is correct behaviour), and two reclassified — AT-4 is unreachable dead code rather
   than a live 400, and AT-6 is a security posture rather than a functional defect. That pass
   was recorded separately, at the user's direction, to preserve authorship of this document.
   **Its V-findings are triaged into the groups below**, since they belong to this audit's
   area.
2. *"the three hand-off tables should be merged into one dossier map in a single pass"* —
   done. The map spans this audit, `docs/audits/2026-07-22-agent-config-runtime/` and
   `docs/audits/2026-07-22-agent-to-agent-orchestration/`. Concretely: the config audit's nine
   overlapping findings were handed to this audit's and the a2a audit's groups rather than
   double-specced, and the a2a audit's F-40 — which that audit explicitly deferred to *this*
   triage because its fix spans the streaming and frontend-draft code examined here — is
   folded into the turn-outcome group below.

Findings are grouped by **change surface**, matching the a2a audit's stated rule: findings
that touch the same files and would be reverted together share one dossier. Where a group
mixes a confirmed defect with a plausible one, the dossier's scope note must say so.

| Finding | Decision | Task dossier |
|---|---|---|
| F-1, F-4, F-18 | fix | `docs/tasks/2026-07-22-chatroom-socket-lifecycle/` |
| F-2, F-16 | fix | `docs/tasks/2026-07-22-chat-export-authz-and-polling/` |
| F-3, F-14, **V-3** | fix | `docs/tasks/2026-07-22-attachment-lifecycle-and-rendering/` |
| F-5, F-21 | fix | `docs/tasks/2026-07-22-presence-transition-and-release-wakeup/` |
| F-6, F-9, F-15, **a2a F-40** | fix | `docs/tasks/2026-07-22-turn-outcome-reporting/` |
| F-7, F-8, **V-4** | fix | `docs/tasks/2026-07-22-settings-form-reconciliation/` |
| F-11, F-13, F-17, F-19, **V-2** | fix | `docs/tasks/2026-07-22-reconnect-reconciliation/` |
| F-10 | fix | `docs/tasks/2026-07-22-observation-binding-cleanup/` |
| F-12, F-20, **V-7** | fix | `docs/tasks/2026-07-22-activity-session-authz-and-validation/` |
| F-22, **V-6** | fix | `docs/tasks/2026-07-22-search-determinism-and-highlighting/` |
| **V-5** | fix | `docs/tasks/2026-07-22-retention-audit-accuracy/` |
| **V-1** | fix | appended to `docs/tasks/2026-07-22-compaction-scoping-and-durability/` — same rows, same maintenance command, same test seam |
| **V-8** | route | `check-security`, alongside F-12 — both are "gate proved once, never re-proved" |
| **V-9** | route | `check-quality` — delete or finish the dead branch; not a bugfix |

**Three sequencing notes the dossiers must carry.**

- **F-18's blast radius depends on F-1.** §3 already records that `resyncPresence` clears
  typing on every reconnect, which under F-1 happens every two minutes — so "typing sticks
  forever" is false in the current build and **becomes true once F-1 is fixed**. The socket
  dossier must fix both together, or fixing F-1 alone regresses F-18. The same coupling note
  at the head of §3 applies to F-8, F-11 and F-13.
- **V-2 and F-11 share one cause**, a frame lost during a disconnect with no durable read
  side, and that cause has a third instance outside this audit: F-13 of
  `docs/audits/2026-07-22-agent-config-runtime/`, whose dossier
  (`docs/tasks/2026-07-22-prompt-assistant-delivery-recovery/`) records the generic remedy —
  replay or cursor semantics on the pub/sub layer — as its FU-1. Whoever picks up the
  reconnect dossier should read all three together.
- **V-1 forces a correction to the compaction dossier** regardless of whether it is adopted:
  that dossier's AC-9 and §9 assert the transcript change is "never what users see", which is
  not accurate for the summary row itself — `message_repo.list` serves it and
  `ChatroomMessageBubble` renders it.

## 6. Out-of-scope Observations

- **FU-1** — `frontend/src/shared/types/workflow.ts:75` sets
  `DEFAULT_WAKEUP.autostop_rounds: 3` while the backend default is 5
  (`contexts/orchestration/domain/models.py:110,159`; R15.04 says 5), and the comment at
  `:64-68` claims these client defaults mirror the backend. An agent with an omitted value
  is shown 3 and silently persisted as 3 on the next save. Belongs to the agent-config
  surface.
- **FU-2** — activity sessions carry no `activation_id`, so attempts from two activation
  windows are unseparable after the fact. Documented as a non-goal in the activation-ux
  dossier; raise as a design follow-up if the research record needs per-activation grouping.
- **FU-3** — KaTeX `innerHTML` without a second sanitization pass (see §4). Route to
  `check-security`.
- **FU-4** — `observer_autostop_rounds` (R28.12) has no editor control
  (`SWakeupEditor.vue:106,286` exposes `autostop_rounds` only). Reachability gap, not data
  loss, since `normalizeNestedTriggers` preserves an API-set value.
- **FU-5** — the `autostop_rounds = 0` divergence recorded as F-21 in the agent-to-agent
  audit and F-24 in the config-runtime audit applies identically to
  `observer_autostop_rounds`; both flow through `autostop_limit_for`
  (`models.py:117-121`), so a fix scoped to one arm will miss the other.
- **FU-6** — `store.setActive(chatroomId)` (`ChatroomView.vue:317`) is never reset on
  unmount and no code reads `activeChatroomId`. Dead state; route to `check-quality`.
- **FU-7** — `docs/UI/07-conversation.md:922` claims keep-alive scroll restoration on
  back-navigation, and `:885` specifies auto-triggering `loadEarlier()` near the feed top.
  Neither is implemented (no `<KeepAlive>` exists; `useChatroomScroll.ts` has no
  top-proximity watcher). Page size also drifts: the code uses 100
  (`useChatroomMessages.ts:70`), the doc says 50 (`:887`). Documentation-vs-code drift.
