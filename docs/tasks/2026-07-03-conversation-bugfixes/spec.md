---
type: bugfix
status: approved
created: 2026-07-03
requirements: [R13.16, R13.20, R13.24, R15.05b, R15.02, R13.19, R15.10]
supersedes:
---

# Conversation Vertical — Bugfix Batch (F-1..F-5)

Fixes the five verified findings from the 2026-07-03 conversation-vertical audit
(`docs/tasks/2026-07-03-conversation-vertical-audit/findings.md`). Batched into one
dossier by decision Q-1; each finding keeps its own root cause, fix, regression test, and
acceptance criteria (B1..B5) so `/build` can still commit them as independent fix∕test
pairs.

## 1. Summary

Five independent functional defects in the conversation vertical: a silence-trigger
wakeup that fires into an empty room and burns provider keys (B1, major); a deleted
message that a racing replay can resurrect in the UI (B2); an unscoped message-list
cursor anchor that mis-anchors pagination (B3); a `clearTyping` that throws on a
non-existent identifier and leaves stale typing indicators (B4); and an
`approval.requested` room-channel payload missing `workflow_run_id` (B5, latent).

## 2. Observed vs Expected

**B1 — silence wakeup into empty room**
- Observed: after the last live user disconnects *uncleanly*, a silence-enabled agent
  with `allow_self_open=true` still fires the silence trigger and posts into the empty
  room. `backend/contexts/orchestration/application/wakeup_service.py:199-223`.
- Expected: [R15.05b] the silence timer pauses when the live-user set becomes empty,
  regardless of how the last user left. No fire into an empty room.

**B2 — deleted message resurrected**
- Observed: a `message.deleted` that is applied before an in-flight `replayDelta`
  resolves does not prevent the delta from (re)adding the deleted message.
  `frontend/src/slices/conversation/composables/useChatroomSocket.ts:100-106, 165-173`.
- Expected: [R13.16][R13.24] deleted content stays removed from the client view.

**B3 — unscoped cursor anchor**
- Observed: `message_repo.list` resolves the `before`/`since` anchor by id only, so a
  cursor id from another room the caller belongs to is accepted and mis-anchors room A's
  window. `backend/contexts/conversation/infrastructure/repositories/message_repo.py:94-100, 114-120`.
- Expected: a cursor id not in this room raises the same not-found → 422 as any missing
  cursor, mirroring the fixed `observation_repo` sibling. Not a cross-room leak (the page
  query is room-scoped); a correctness/parity gap.

**B4 — clearTyping throws**
- Observed: `clearTyping` reads `typing.value`, but the store field is `typingUsers`;
  the `ReferenceError` is swallowed by `resyncPresence`'s catch.
  `frontend/src/slices/conversation/stores/conversation.ts:30, 69, 71` and
  `useChatroomSocket.ts:90-98`.
- Expected: on reconnect resync, stale typing indicators are cleared. [R13.20].

**B5 — approval payload missing run id (latent)**
- Observed: the room-channel `approval.requested` payload omits `workflow_run_id`; the
  frontend stores `''`. `backend/contexts/orchestration/application/approval_service.py:97-109`;
  `useChatroomSocket.ts:234`.
- Expected: [R13.19][R15.10] an approval carries its workflow run id. Currently unconsumed,
  so no live symptom — a latent contract gap.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | One dossier per finding, or one combined? | Combined into this conversation-bugfix dossier | User decision this session; per-finding AC groups keep independent commits possible. |
| Q-2 | Is FU-1 (typecheck gate no-op) in this dossier? | No — separate refactor dossier `2026-07-03-frontend-typecheck-gate` | Different type (tooling), broad blast radius, breaks CI until a backlog is cleared. |
| Q-3 (open, for /build) | B1: where does the silence-reset hook live given the SoC boundary? | See §7 B1 — recommended: retention worker wrapper | `scrub_stale_presence` is in `conversation/infrastructure`; silence state is in `orchestration`. Resolve during implementation. |

## 4. Reproduction

- **B1**: one live user + agent with `silence_minutes.enabled=true`, `allow_self_open=true`,
  `is_silence_active` set; kill the user's socket without a close frame (e.g., pull
  network); wait for the conns key to lapse (~150s) and the retention sweep to run; the
  wakeup evaluator fires with an empty roster.
- **B2**: in a busy room, post message M (newer than the cursor), then hard-delete M
  within the `replayDelta` round-trip such that the `message.deleted` WS frame beats the
  delta HTTP response; M appears and persists until refetch.
- **B3**: as a member of rooms A and B, call
  `GET /api/chatrooms/{A}/messages?since=<id from B>`; the request succeeds (no 422) and
  pages A from B's timestamp.
- **B4**: view "B is typing…", drop A's socket, have B stop typing during the outage,
  reconnect A; the indicator never clears.
- **B5**: trigger an approval gate while watching a room; inspect the stored approval —
  `workflow_run_id === ''`.

## 5. Root Cause Analysis

- **B1**: `scrub_stale_presence` (`presence.py:180-181`) drops roster members via raw
  `SREM` and never routes through `evaluate_presence_change`, the sole path that clears
  `is_silence_active` (`wakeup_service.py` clean-close at `chatroom.py:130-142`;
  `wakeup_state.set_silence_active(...,False)`). In `evaluate_silence_trigger` the only
  liveness gate before firing is the now-stale `is_silence_active`
  (`wakeup_service.py:200-201`); the real roster re-check (`:217-220`) is inside
  `if not cfg.allow_self_open:`, so `allow_self_open=true` skips it.
- **B2**: no tombstone. `applyMessageCreated` (`useChatroomSocket.ts:100-106`) appends on
  an in-cache id dedup only; the `message.deleted` handler (`:165-173`) filters the cache
  but does not bump `replayGeneration` (`:65`), so an in-flight delta is not invalidated
  and re-adds the row the delta snapshotted while it was still live.
- **B3**: both anchor lookups (`message_repo.py:96-98`, `:116-118`) filter on
  `messages.c.id == cursor` with no `chatroom_id` conjunct; the fixed sibling
  `observation_repo.py:105-113` adds it with an explanatory comment. No upstream guard
  validates cursor ownership.
- **B4**: typo — `typing` where the ref is `typingUsers`; undefined identifier throws at
  runtime, swallowed by a best-effort catch. Shipped because the typecheck gate is inert
  (see the FU-1 dossier), so TS2304 was never raised.
- **B5**: the room-channel emit block (`approval_service.py:99-109`) simply doesn't
  include the field; the run id is in scope at the call site (used for the
  workflow-channel emit at `:111-117`) but not added to the room payload.

## 6. Blast Radius and Sibling Suspects

- **B1**: any room whose last member drops uncleanly with a self-opening silence agent;
  cost impact (BYO provider keys). Sibling: confirm no other trigger kind (call_only,
  scheduled) reads `is_silence_active` or a similarly stale flag without a live re-check.
- **B2**: any viewer during create-then-immediate-delete; moderation/privacy relevant.
  Sibling: `message.updated` (`:151-163`) is a no-op if the row isn't cached yet — same
  ordering family; verify it doesn't strand pre-edit content (audit hunch, out of scope
  here → FU).
- **B3**: cross-member using a wrong cursor. Sibling sweep already done in the audit:
  observation_repo is fixed; other repos (attachment, chatroom, workspace) re-derive
  scope correctly. Also fold FU-2 (anchor omits `deleted_at IS NULL`) into this fix.
- **B4**: cosmetic. Sibling: grep the store for other methods referencing a mis-named
  ref (none found in the audit, re-confirm).
- **B5**: latent only; no consumer today.

## 7. Fix Design

- **B1**: have the removal path reset silence state when a room roster becomes empty.
  Respect SoC — `scrub_stale_presence` sits in `conversation/infrastructure` and must not
  import `orchestration`. Recommended: `scrub_stale_presence` returns the set of rooms it
  emptied (it already tracks removals), and the retention worker wrapper
  `_scrub_stale_presence` (`app/workers/tasks/retention.py:427-440`, an app-layer
  orchestrator that may cross contexts) invokes the orchestration facade to run the
  presence-changed∕silence-pause logic for those rooms. Independently, make
  `evaluate_silence_trigger`'s liveness re-check unconditional (move `:218-220` out of the
  `allow_self_open` guard) as defense in depth. Confirm the exact hook in Q-3 during build.
- **B2**: keep a short-lived tombstone `Set<string>` of recently deleted message ids in
  the socket composable; the `message.deleted` handler adds to it, `applyMessageCreated`
  skips any id present. Prefer this over bumping `replayGeneration` on delete, which would
  also discard legitimate new messages in the same in-flight delta. Bound the set (evict
  after the delta window / on room reset) to avoid unbounded growth.
- **B3**: AND `messages.c.chatroom_id == chatroom_id` into both anchor `select`s
  (`:96-98`, `:116-118`), mirroring `observation_repo.py:105-113`. Fold FU-2: add
  `messages.c.deleted_at.is_(None)` to the anchor as well.
- **B4**: rename `typing` → `typingUsers` at `conversation.ts:69, 71`.
- **B5**: add `"workflow_run_id": str(workflow_run_id)` to the room payload
  (`approval_service.py:101-108`).

## 8. Regression Test Plan

Each failing test is written first (test-first per /build).

- **B1**: unit test in `backend/tests/unit/` (near `test_retention_deep.py:431` and the
  wakeup tests) — simulate stale-presence removal emptying a room and assert silence state
  is reset so `evaluate_silence_trigger` returns False for an `allow_self_open=true` agent.
- **B2**: `frontend/src/slices/conversation/__tests__/useChatroomSocket.test.ts` — deliver
  `message.deleted` for M, then resolve an in-flight delta containing M; assert M is not in
  the cache.
- **B3**: backend unit test mirroring
  `test_observer_agents.py:852` (`test_observation_list_before_anchor_scoped_by_chatroom_id`)
  for `message_repo.list` — a foreign-room cursor id raises `ValueError` (→ 422), for both
  `before` and `since`.
- **B4**: store unit test for `clearTyping` (no `stores/__tests__` exists yet — create
  one) asserting it empties the room's typing set without throwing.
- **B5**: extend the approval-service test that pins the emit
  (`test_orchestration_services.py` around the approval action assertion) to check the
  room payload carries `workflow_run_id`.

## 9. Risks and Rollback

- **B1** carries the real design risk (cross-context hook placement); a wrong placement
  could reintroduce an SoC violation or miss non-retention removal paths (clean close
  already handled). Mitigate by centralizing the "roster became empty" signal. Rollback:
  per-finding `git revert`.
- **B2** tombstone must be bounded to avoid a memory leak; evaluate eviction in review.
- **B3/B4/B5** are low-risk, localized. All fixes are independently revertible; keep them
  as separate commits so one regression doesn't force reverting the batch.

## 10. Acceptance Criteria

- [ ] AC-1 (B1): the B1 regression test fails before the fix and passes after; an
      `allow_self_open=true` silence agent does not fire after an unclean disconnect
      empties the room. [R15.05b]
- [ ] AC-2 (B1): `evaluate_silence_trigger`'s empty-roster check no longer depends on
      `allow_self_open`.
- [ ] AC-3 (B2): the B2 regression test fails before and passes after; a message deleted
      during an in-flight replay does not appear in the cache. [R13.16][R13.24]
- [ ] AC-4 (B3): a foreign-room cursor id raises not-found (→ 422) for both `before` and
      `since`; regression test mirrors the observation sibling.
- [ ] AC-5 (B3): the anchor lookup also excludes soft-deleted rows (FU-2 folded in).
- [ ] AC-6 (B4): `clearTyping` clears the room's typing set without throwing; covered by a
      new store test.
- [ ] AC-7 (B5): the room-channel `approval.requested` payload includes a non-empty
      `workflow_run_id`; the frontend no longer defaults to `''`.
- [ ] AC-8: `check-quality` on the diff shows no new Introduced-Critical∕Warning; the B1
      fix introduces no cross-context (SoC) upward import.

## 11. SRS Delta

None. All five restore or align with already-documented behavior
([R15.05b], [R13.16], [R13.24], [R13.20], [R13.19]); no new requirements.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- FU-1: typecheck gate is inert — separate dossier `2026-07-03-frontend-typecheck-gate`.
- FU-3 (latent): `MessageService.get`∕`ConversationFacade.get_message` globally unscoped;
  add a scoping guard or docstring contract.
- FU-4 (latent): `observation_service.release` non-atomic CAS→create→mark; add
  compensation for cancellation between steps.
- FU-5 (minor): released-observation `message.created` hardcodes `sender_id: None`, so the
  agent's error badge isn't eagerly cleared on release.
- `message.updated` pre-edit-content stranding when the row isn't cached yet (audit
  hunch) — verify and file if real.
