---
type: bugfix
status: draft
created: 2026-08-30
requirements: [R15.10, R24.32]
depends_on: []
---

# Chatroom approval and overlay discoverability

## 1. Summary

Close three related chatroom follow-ups from
`2026-08-19-chatroom-scroll-and-composer`: a live approval card is dated with the browser's
clock and can be inserted off-screen without an unseen-item pill (FU-9), compact-desktop rail
overlays do not move/trap/restore keyboard focus or provide a dismissing backdrop (FU-10), and
the search panel omits the documented feed backdrop/outside-click behavior (FU-1). The PR makes
new approvals reliably discoverable and gives every in-chat overlay a deliberate, tested
keyboard/pointer dismissal contract.

Freshness was re-verified against `main` at `73125821` (2026-08-28). All three defects remain in
the current code; §5 cites the live paths.

## 2. Observed vs Expected

- **Observed — live approval ordering.** The room-channel `approval.requested` event omits the
  persisted `Approval.started_at` (`backend/contexts/orchestration/application/approval_service.py:182-194`).
  The client fills it with `new Date().toISOString()`
  (`frontend/src/slices/conversation/composables/useChatroomSocket.ts:474-487`). A slow client
  clock can therefore sort a just-arrived gate above recent server-dated messages; because the
  feed tail does not change, the unseen counter also remains zero. The source dossier records the
  complete failure chain at
  `docs/tasks/2026-08-19-chatroom-scroll-and-composer/spec.md:786-800`.
- **Expected — live approval ordering.** Approval cards are interleaved at the chronological
  position where the server created the request (`docs/UI/07-conversation.md:937,989`), and an
  `approval.requested` server event adds the card (`docs/UI/07-conversation.md:1404`). Client
  clock skew must not decide server-event chronology.
- **Observed — compact overlays.** At 1024-1279px the agent and people panels are raw absolutely
  positioned rails. Escape closes them, but focus remains in the header/feed, is not trapped,
  is not restored, and no backdrop catches outside clicks
  (`frontend/src/slices/conversation/views/ChatroomView.vue:1-42,196-273,952-969,1304-1317`).
  The source dossier records the keyboard impact at
  `docs/tasks/2026-08-19-chatroom-scroll-and-composer/spec.md:801-808`.
- **Expected — compact overlays.** The intermediate breakpoint uses toggleable overlay panels
  (`docs/UI/07-conversation.md:238`). The established overlay accessibility contract moves focus
  inside, traps Tab/Shift+Tab, restores focus on close and supports Escape
  (`docs/UI/11-responsive-a11y.md:243-280`).
- **Observed — search overlay.** `ChatroomSearchPanel` renders an absolute top panel with no
  backdrop markup or focus management
  (`frontend/src/slices/conversation/components/ChatroomSearchPanel.vue:1-21,70-110`).
- **Expected — search overlay.** The feed is dimmed with `--overlay-backdrop` at 0.2 opacity,
  search receives focus on open, and clicking the dimmed area closes the panel
  (`docs/UI/07-conversation.md:738-769`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which follow-ups belong in this PR? | FU-9, FU-10 and FU-1 from `2026-08-19-chatroom-scroll-and-composer`. | All three govern whether an item or panel layered over the chatroom feed is discoverable and reachable. They share `ChatroomView`, its overlay state/tests and the same manual viewport pass. The dossier's feed-geometry E2E harness (FU-3) and tablet rail redesign (FU-6) are larger, independent initiatives. |
| Q-2 | Fix approval ordering in the frontend or event contract? | Add the persisted `started_at` to the room `approval.requested` event and consume it. | The backend already owns the authoritative timestamp. A client-derived timestamp can never be correct under skew, while a server field makes live delivery and reconnect discovery use the same value. |
| Q-3 | What should a new frontend do during a rolling deploy when an old backend omits `started_at`? | Use an invalid/absent timestamp sentinel that the feed deliberately places at the tail; never synthesize wall-clock time. Reconciliation replaces it with the server value. | Tail placement preserves discoverability and raises the unseen pill. It degrades to the pre-merge behavior and avoids creating a second, merely less likely clock-skew defect. |
| Q-4 | Should compact rails use `SDrawer`? | No. Keep them in-chat absolute overlays, but give them the same focus/dismissal semantics through a small conversation-local overlay shell and shared focus utility. | `SDrawer` teleports to `body`, covers the viewport and uses modal z-index/geometry; the 1024-1279 design explicitly calls for panels positioned inside the chatroom at `--z-dropdown`. Reusing behavior is correct; reusing the wrong geometry is not. |
| Q-5 | How do the two compact rail overlays interact? | At most one is open. Opening agents closes people and vice versa; Escape or backdrop closes the active one and restores focus to its trigger. | Two simultaneously open overlays compete for the same feed area and cannot both own a focus trap. Mutual exclusion makes focus ownership deterministic. |
| Q-6 | What is the search backdrop contract? | Feed-scoped, 0.2-opacity token backdrop; outside click and Escape close; opening focuses search and closing restores the search trigger. No new motion is introduced. | This is the behavior already written in `07-conversation.md`; no stacking or animation policy has to be invented. Search remains a top panel rather than a viewport-modal drawer. |
| Q-7 | Does this depend on another active dossier? | No; `depends_on: []`. | The active GraphRAG blueprint, activities historical status residue and large-artifact dossier do not touch approval announcements, conversation overlay markup or these tests. |

## 4. Reproduction

### R1 — approval hidden by client clock skew

1. Open a chatroom with at least two recent messages and scroll to the bottom.
2. Set the browser/OS clock several minutes behind the server (or fake `Date` in the component
   test).
3. Trigger a workflow approval gate bound to the room.
4. Observe that the new card sorts above the recent messages and no unseen-item pill appears.

Deterministic automated reproduction: deliver an `approval.requested` frame after two messages
whose server timestamps are newer than the fake client clock. Current code writes the fake clock
at `useChatroomSocket.ts:484`, so the feed merge inserts the card before the tail.

### R2 — compact overlay keyboard path

1. Set the viewport to 1100px and focus the header's Agents toggle with the keyboard.
2. Activate it, then press Tab.
3. Observe that focus walks the feed/header sequence rather than entering/cycling inside the open
   panel; close it and observe no defined restoration target. Click the uncovered feed and observe
   that the panel stays open.

### R3 — search backdrop

1. Open chatroom search with Ctrl/Cmd+K or the header button.
2. Observe that the feed is not dimmed and has no click target that closes search.
3. Close search and observe that focus restoration to the opener is not guaranteed by the panel.

## 5. Root Cause Analysis

### Approval chronology

1. `ApprovalRepository.insert` stores a server-side `started_at`, and the domain model carries it
   (`backend/contexts/orchestration/infrastructure/repositories.py:59-96`,
   `backend/contexts/orchestration/domain/models.py:387-400`).
2. `announce_gate` re-reads that durable row after commit, but the room event copies every display
   field except `started_at`
   (`backend/contexts/orchestration/application/approval_service.py:161-194`).
3. The socket handler must satisfy the non-optional `ApprovalWithVotes.started_at` contract, so it
   invents a client timestamp (`useChatroomSocket.ts:474-487`).
4. `ChatroomView` merges server-dated messages and approval timestamps on one chronological axis;
   invalid timestamps already go to the tail, but the synthetic timestamp is valid and therefore
   trusted (`frontend/src/slices/conversation/views/ChatroomView.vue:752-795`).

The root cause is the event-contract omission in step 2. Client-clock synthesis is the aggravating
factor; changing sort heuristics without fixing the event would only mask the omission.

### Overlay accessibility

1. Mobile/tablet panels use `SDrawer`, whose `useFocusTrap` integration supplies focus move,
   trapping, restoration, Escape and backdrop behavior
   (`frontend/src/shared/ui/SDrawer.vue:31-44,47-109`;
   `frontend/src/shared/composables/useFocusTrap.ts:21-100`).
2. The intermediate breakpoint reuses only the panels' open booleans and raw content, rendering
   absolute `.chatroom__panel--open` blocks instead of an overlay primitive
   (`ChatroomView.vue:1-42,196-212,1304-1317`).
3. Review added an Escape listener, but no element owns a focus boundary, previous-focus record or
   backdrop (`ChatroomView.vue:961-969`).
4. `ChatroomSearchPanel` likewise owns only the visible panel, even though the documented design
   includes a backdrop, autofocus and outside-click dismissal (`ChatroomSearchPanel.vue:1-21,95-110`).

The root cause is that in-chat overlay geometry was implemented as visibility CSS without a
behavioral shell. The aggravating factor is sharing drawer state without sharing drawer semantics.

## 6. Blast Radius and Sibling Suspects

- **Approval blast radius** — every room-bound approval delivered live over WebSocket. Reconnect
  discovery is cleared: it already receives the API model's server `started_at`
  (`frontend/src/slices/conversation/composables/useChatroomSocket.ts:155-177`;
  `frontend/src/shared/types/workflow.ts:13-36`). The workflow-run socket is also cleared: it only
  invalidates the approval query and does not construct a card
  (`frontend/src/slices/workflow/composables/useWorkflowRunSocket.ts:74-77`).
- **Approval sibling** — the workflow-channel event also omits `started_at`, but its consumer only
  invalidates queries. Add the field there for event-shape consistency only if the backend event
  DTO is shared; no frontend workflow behavior depends on it.
- **Overlay blast radius** — search, agent rail and people/observer rail inside `ChatroomView` at
  the 1024-1279 band. Mobile/tablet `SDrawer` and >=1280 persistent rails retain their existing
  markup and behavior.
- **Overlay siblings cleared** — `SModal` and `SDrawer` already use `useFocusTrap`; no defect is
  claimed there. Dropdown focus has its own roving-focus contract and is not a dialog.

## 7. Fix Design

1. Add a typed room approval-request event payload containing the persisted ISO `started_at`.
   `announce_gate` emits the value from the re-read durable approval. Update the realtime event
   documentation and backend payload test.
2. In `useChatroomSocket`, consume `started_at` from the event. During mixed-version rollout, use
   a tail sentinel when absent/invalid; remove all `Date`/`Date.now` chronology synthesis from this
   branch. Reconciliation may later replace the sentinel with the API's authoritative value.
3. Extract a conversation-local overlay shell/composable for the compact agent/people panels. It
   owns a labelled panel ref, focus move/trap/restore, Escape handling, a click-catching backdrop,
   and one-active-panel state. Extend the shared focus utility with a narrowly typed option only if
   required to avoid locking the entire document for an in-page overlay; preserve its default
   modal/drawer behavior and tests.
4. Add the documented feed-scoped search backdrop. Opening search records/focuses the search
   control; Escape, close, result selection and backdrop click use one close path that restores
   focus to the opener. The backdrop uses a 20% mix of `--overlay-backdrop` and adds no animation.
5. Keep z-indexes within the documented in-chat stack (`--z-dropdown`); do not promote these
   panels to `--z-modal`. Ensure the approval card/pill and search/rail overlays remain mutually
   reachable rather than hiding one another.

## 8. Regression Test Plan

The failing tests come first.

- **T-1 backend unit** — extend `test_announce_gate_room_payload_carries_run_id` in
  `backend/tests/unit/test_orchestration_services.py:481-503` to require `started_at` equal to the
  repository model's timestamp. It fails against the current payload.
- **T-2 socket unit** — deliver a room `approval.requested` with a fixed server timestamp while the
  browser clock is skewed; assert the store retains the event value and no `Date` fallback is used.
- **T-3 rolling-version unit** — omit/garble `started_at`; assert the card is appended at the feed
  tail and the unseen pill rises when the reader is scrolled up.
- **T-4 compact overlay component** — at 1100px, open each rail from its trigger and assert focus
  enters, Tab/Shift+Tab cycle, opening the other closes the first, Escape/backdrop close, and focus
  returns to the correct trigger.
- **T-5 search component/view** — assert token backdrop rendering, input focus, outside-click and
  Escape close, result-selection close, and trigger focus restoration.
- **T-6 responsive negative tests** — assert <1024 still uses `SDrawer`, >=1280 retains persistent
  rails, and compact-only backdrop/focus behavior does not mount in either band.

## 9. Risks and Rollback

- **Mixed-version events** — guarded by tail-sentinel behavior; a new client remains discoverable
  against an old backend. Old clients ignore the added backend field.
- **Focus dead-end** — a panel with no focusable child must focus its labelled container, and
  unmount must restore focus/release any trap exactly once. Mutation tests remove each behavior to
  prove the assertions are not vacuous.
- **Stacking regression** — backdrop and panel tests assert their containing block and token z-index;
  the manual pass covers search, approval pill and both rails together.
- **Scroll regression** — do not reuse document-modal scroll locking for in-chat overlays. The feed
  remains in place when a rail opens/closes.
- **Rollback** — revert the frontend overlay and event-consumer commits, then the additive backend
  event-field commit. No schema or stored-data rollback is required.

## 10. Acceptance Criteria

- [ ] AC-1: T-1 fails before the fix and passes after; the room event's `started_at` exactly equals
  the persisted approval timestamp.
- [ ] AC-2: a skewed client clock cannot move a live approval away from its server chronology or
  suppress the unseen pill.
- [ ] AC-3: a new client receiving an old/malformed event places the approval at the tail and
  remains functional until reconciliation supplies the authoritative timestamp.
- [ ] AC-4: at 1024-1279px, opening either rail moves focus inside, traps Tab/Shift+Tab, allows only
  one rail open, and Escape/backdrop close and restore focus to the initiating toggle.
- [ ] AC-5: chatroom search renders the documented 0.2-opacity token backdrop, focuses its input,
  closes on Escape/outside click/result selection, and restores focus to its opener.
- [ ] AC-6: <1024 `SDrawer` behavior and >=1280 persistent-rail behavior are unchanged.
- [ ] AC-7: all overlay controls retain accessible names/relationships and every keyboard-focused
  control has the global visible focus indicator.
- [ ] AC-8: no client wall-clock call remains in the approval-requested insertion path.
- [ ] AC-9: targeted backend/frontend tests, frontend lint/typecheck/build and relevant source-scan
  contracts pass; manual 1100px and skewed-clock checks are recorded.

## 11. SRS Delta

None. The fix restores the approval chronology and overlay behavior already documented in
`docs/UI/07-conversation.md` and the focus contract in `docs/UI/11-responsive-a11y.md`; it does not
change the workflow or responsive product requirements.

## 12. Deviation Log

None — implementation has not started.

## 13. Follow-ups

- FU-1: automate the broader feed-geometry browser harness once E2E seeding is idempotent; this is
  still FU-3 of the source dossier.
- FU-2: the 768-1023 agent-rail-to-drawer correction remains source FU-6 and is deliberately not
  coupled to the compact overlay semantics here.
- FU-3: if a second feature needs in-page non-document-modal focus containment, promote the local
  behavior into a shared primitive after comparing both consumers; do not generalize from one.
