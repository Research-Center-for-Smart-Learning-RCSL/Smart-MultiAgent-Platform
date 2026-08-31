---
type: bugfix
status: in-progress
created: 2026-08-30
requirements: [R15.10, R24.32, R24.49]
depends_on: []
---

# Chatroom approval and overlay discoverability

## 1. Summary

Close FU-9, FU-10 and FU-1 of `2026-08-19-chatroom-scroll-and-composer`. A live approval
currently uses the browser clock and can be inserted off-screen without an unseen-item pill. At
1024-1279px, the search, agent and people/observer surfaces lack one coherent focus, stacking and
dismissal contract. Search also omits its documented feed backdrop.

The responsive intent itself needs repair: R24.32 was amended on 2026-08-09 to require a persistent
right rail from 1024px, but a later user-approved dossier (`b4b25d1`, 2026-08-21) deliberately chose
and shipped the four-band 1024-1279 overlay design without applying an SRS delta. This dossier
records that later decision explicitly instead of silently implementing against contradictory
sources.

Freshness was re-verified against `main` at `73125821` (2026-08-28). All runtime defects and the
SRS/UI contradiction remain; §4-§6 cite the live paths.

## 2. Observed vs Expected

- **Approval chronology observed.** The room `approval.requested` event omits the persisted
  `Approval.started_at`; the client fills it with `new Date().toISOString()`
  (`backend/contexts/orchestration/application/approval_service.py:161-194`;
  `frontend/src/slices/conversation/composables/useChatroomSocket.ts:474-487`). A slow client clock
  sorts a new gate above recent server-dated messages and leaves the unseen count unchanged.
- **Approval chronology expected.** Room events carry the server timestamp. A mixed-version event
  with no valid timestamp is discoverable at the tail and is later replaced by the authoritative
  server DTO without consulting client wall time.
- **Overlay behavior observed.** At 1024-1279 both rails are raw absolute panels. Escape closes
  them, but focus is not moved/trapped/restored and there is no backdrop
  (`ChatroomView.vue:34-39,194-255,961-973,1273-1317`). Search is independently openable and has no
  backdrop or focus owner (`ChatroomView.vue:27,41-52,946-950`;
  `ChatroomSearchPanel.vue:1-21,70-110`).
- **Overlay behavior expected.** At most one transient chatroom surface is active. Opening another
  performs a focus-safe hand-off; normal close restores the initiating control. Search uses the
  documented 0.2 backdrop and 200ms token animation, collapsed under reduced motion
  (`docs/UI/07-conversation.md:709-777`, whose §3.8 specifies the slide at `:747` and the 0.2
  `--overlay-backdrop` dim at `:748`; [R24.49]).
- **Responsive intent observed.** [R24.32] says the right rail is persistent from 1024px, while the
  older detailed UI document and shipped code use overlays at 1024-1279
  (`REQUIREMENTS.md:1974`; `docs/UI/07-conversation.md:238-254`, whose worked media block is
  the source of the shipped `chatroom--compact` geometry). The same document's `:258` then calls
  everything below 1024px single-pane with drawers, which the shipped 768-1023 agent rail also
  contradicts; that band is the source dossier's deferred FU-6 and is treated as a recorded
  deviation rather than a defect this dossier fixes.
- **Responsive intent expected.** The later approved four-band decision is reflected in R24.32;
  768-1023 remains the source dossier's known deferred agent-rail deviation, not a behavior this
  dossier silently changes.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which follow-ups belong in this PR? | FU-9, FU-10 and FU-1 from `2026-08-19-chatroom-scroll-and-composer`. | They govern whether a gate or transient panel over the feed is discoverable and reachable and share ChatroomView state/tests and one viewport pass. |
| Q-2 | Fix approval ordering in the frontend or event contract? | Add persisted `started_at` to the room `approval.requested` event and consume it. | The backend owns chronology; a client-derived timestamp can never be correct under skew. |
| Q-3 | What happens when an old backend omits `started_at`? | Insert an explicit invalid-date sentinel at the tail, then replace the entire local DTO from an authoritative fetch even while the approval remains pending. | Tail placement preserves discovery. Full replacement is necessary because current reconciliation updates only terminal state and discovery skips existing ids. |
| Q-4 | Should compact rails use `SDrawer`? | No. Keep in-chat overlay geometry and reuse/extract focus behavior without document-modal scroll locking. | `SDrawer` teleports to body and uses modal geometry/z-index; the approved 1024-1279 design is feed-adjacent at `--z-dropdown`. |
| Q-5 | How do the two compact rail overlays interact? | They are mutually exclusive; opening one closes the other and transfers focus ownership atomically. | Two open rail overlays cannot both own the same feed area or focus trap. |
| Q-6 | What is the search contract? | Feed-scoped 0.2 backdrop, documented 200ms token slide, auto-focus, Escape/backdrop/result/explicit close, focus restoration, and reduced-motion collapse. Focus restoration is the only new decision recorded here; everything else implements text that already exists. | `07-conversation.md:747-748` specifies the slide and the 0.2 backdrop, `:752` the auto-focus, `:764` the result-click close, and `:766-769` all three close actions including "clicking outside the panel (on the dimmed overlay)". The document therefore already mandates the backdrop and its outside-click, and this dossier implements them rather than deciding them. What no clause covers is where focus goes after any of those close paths, which is what makes the surface operable by keyboard and deterministic. |
| Q-7 | Does this depend on another active dossier? | No; `depends_on: []`. | No predecessor touches approval announcements or this overlay implementation. Checked against all four other non-implemented dossiers: `2026-08-30-runtime-contract-integrity` does not touch `ChatroomView` once its clipboard sweep is returned to follow-up, `2026-08-30-identity-onboarding-policy-hardening` is confined to the identity/Admin surface, and `2026-07-07-graphrag-two-axis-redesign` and `2026-07-19-large-artifacts-silently-dropped` to `AgentDetailView.vue`/`turn_engine.py`. No file overlap in either direction, so there is no rebase edge either. |
| Q-8 | May search and a compact rail be open together? | No. At 1024-1279 search, agents and people/observer form one transient group. Below 1024, search and a drawer are mutually exclusive. At >=1280 persistent rails are outside the group. | Competing backdrops/traps/restoration targets create nondeterministic keyboard behavior; persistent rails do not. |
| Q-9 | Which responsive source wins? | The later user-approved four-band decision in `b4b25d1` wins; amend R24.32 at approval. | The 2026-08-21 approval is later than R24.32's 2026-08-09 amendment and was implemented in `bdea016`; the missing SRS delta is the documentation defect. |

## 4. Reproduction

### R1 — client-clock approval

1. Open a room with recent messages and scroll to the bottom.
2. Set the client clock several minutes behind the server.
3. Trigger a room-bound approval.
4. The new card sorts above the tail and the unseen pill remains zero.

Deterministic test: deliver a room event after two messages whose timestamps are newer than the fake
client clock. Current code writes that fake clock in the socket insertion branch.

### R2 — competing compact overlays

1. Set the viewport to 1100px and open Agents from the keyboard.
2. Open search without closing Agents, or toggle People.
3. Observe independent state, no single focus owner, no backdrop and no defined restoration target.

### R3 — search backdrop and motion

1. Open search with the header control or shortcut.
2. The feed is not dimmed and has no click target that closes the panel.
3. Close search; focus restoration is not guaranteed by the panel.

### R4 — mixed-version sentinel

1. Insert a pending card with an invalid `started_at`, as a new client must do for an old event.
2. Run the existing discovery/reconciliation against a still-pending authoritative row.
3. `discoverApprovals` skips the existing id and `reconcilePending` does not replace pending DTOs,
   so the sentinel remains (`useChatroomSocket.ts:164-173`;
   `frontend/src/shared/stores/orchestration.ts:69-80`).

## 5. Root Cause Analysis

### Approval chronology

1. The repository/domain row carries server `started_at`
   (`backend/contexts/orchestration/infrastructure/repositories.py:59-96`;
   `backend/contexts/orchestration/domain/models.py:387-400`).
2. `announce_gate` re-reads that durable row but omits the timestamp from the room event
   (`approval_service.py:161-194`).
3. The socket handler invents a valid client timestamp to satisfy `ApprovalWithVotes`, and the feed
   correctly trusts valid timestamps (`useChatroomSocket.ts:474-487`;
   `ChatroomView.vue:752-795`).
4. A sentinel alone is insufficient: current reconciliation removes null or updates terminal state
   only, and discovery seeds only missing ids (`orchestration.ts:69-80`;
   `useChatroomSocket.ts:164-173`).

The event omission causes skew; incomplete full-DTO reconciliation would make the rollout fallback
permanent.

### Overlay accessibility and responsive intent

1. Mobile drawers already use `useFocusTrap` through `SDrawer`
   (`frontend/src/shared/ui/SDrawer.vue:31-109`;
   `frontend/src/shared/composables/useFocusTrap.ts:21-100`).
2. Compact rails reuse only booleans and visibility CSS, not the behavior
   (`ChatroomView.vue:34-39,194-255,1273-1317`).
3. Search has a separate boolean and panel with no shared transient-surface coordinator
   (`ChatroomView.vue:27,41-52,946-950`).
4. Detailed UI docs defined 1024-1279 overlays in June (`07-conversation.md:238-254`); R24.32 was
   amended to persistent >=1024 in August; a still-later approved dossier selected and shipped
   overlays but recorded no SRS change.

The runtime root cause is visibility state without a behavioral owner. The documentation root cause
is a later approved design decision that bypassed the SRS Delta protocol.

## 6. Blast Radius and Sibling Suspects

- Every room-bound approval delivered live over WebSocket. Reconnect discovery already receives the
  server timestamp; workflow-run socket consumers only invalidate queries.
- Search, agent rail and people/observer rail at 1024-1279. Below 1024 the drawer/search exclusion is
  affected; >=1280 persistent rails retain geometry and resizing.
- `SModal` and `SDrawer` are cleared: they already use `useFocusTrap`. Dropdown roving focus is a
  separate contract.
- The 768-1023 agent rail remains the source dossier's FU-6 and is pinned as an intentional current
  deviation; this dossier does not quietly remove it.

## 7. Fix Design

1. Add a typed room approval-request payload containing persisted ISO `started_at`. Emit the value
   from the re-read durable row and update realtime documentation and backend payload tests.
2. Consume `started_at` in `useChatroomSocket`. Missing/invalid values receive an explicit non-date
   sentinel and no `Date`/`Date.now` fallback. Extend `reconcilePending` so an authoritative result
   replaces the complete local approval DTO even when still pending. After await, re-check that the
   same local pending generation remains before applying it, so a late pending fetch cannot
   overwrite a concurrent `approval.resolved`. Null still removes the card.
3. Add one conversation-local transient-surface coordinator for search, compact agents and compact
   people/observer. At 1024-1279 exactly one is active. Opening another closes the old surface
   without restoring stale focus, records the new opener and moves focus. Normal close paths restore
   the active opener. Below 1024 the same coordinator closes search before opening a drawer and vice
   versa; >=1280 persistent rails are not transient.
4. Extract/reuse a focus boundary for the two in-chat rail panels: labelled panel ref, first-focus
   fallback to container, Tab/Shift+Tab containment, Escape, backdrop and restoration. Extend the
   shared utility only if this can preserve existing modal/drawer behavior and avoid scroll lock.
5. Add search's documented feed-scoped backdrop and 200ms `--transition-normal` slide. Backdrop,
   Escape, result selection and explicit close use one path. Under `prefers-reduced-motion: reduce`,
   the transition is removed per R24.49.
6. Keep compact panels at `--z-dropdown`; do not promote them to modal. Update
   `docs/UI/07-conversation.md` in two places: §3.8 Search (`:709-777`) gains the focus-restoration
   rule its existing backdrop/animation/close-action text does not cover, and the
   intermediate-breakpoint block (`:238-254`) gains the mutual-exclusion contract for the three
   transient surfaces. The R24.32 amendment in §11 is applied to `REQUIREMENTS.md` after the
   responsive acceptance criteria pass, so the requirement never precedes the behavior.

## 8. Regression Test Plan

The failing tests come first.

- **T-1 backend event** — require room `approval.requested.started_at` to equal the repository row.
- **T-2 socket chronology** — deliver a fixed server timestamp under a skewed client clock; assert
  the store uses the event value and never calls the clock fallback.
- **T-3 old-event fallback** — omit/garble timestamp; assert tail placement and unseen behavior.
- **T-4 pending reconciliation** — seed a pending sentinel, return a still-pending authoritative DTO,
  assert complete replacement and restored chronology. Resolve locally before a deferred fetch
  completes and assert the late pending response cannot regress state.
- **T-5 compact coordinator** — at 1100px, open each of search/agents/people from its trigger; assert
  exactly one active, focus-safe hand-off, Tab containment, Escape/backdrop close and restoration.
- **T-6 search motion** — assert backdrop, input focus, all close paths, token transition and a
  reduced-motion rule that removes it.
- **T-7 responsive bands** — pin the four behaviors described by AC-10 through AC-13.

## 9. Risks and Rollback

- **Late reconciliation.** Guard full replacement by current card identity/state/generation so a
  pending fetch cannot resurrect a resolved gate.
- **Focus dead-end.** A panel with no focusable child focuses its labelled container. Unmount and
  surface hand-off release the old boundary exactly once.
- **Stacking regression.** Structural tests assert feed containing block and token z-index; manual
  testing opens every surface at 1100px.
- **Responsive scope leak.** Compact selectors must not reach the deliberately deferred 768-1023
  layout. AC-11 pins current behavior.
- **Rollback.** Revert coordinator/search changes and event consumer, then the additive backend
  field. The SRS/UI amendment must be reverted with the design if the product returns to persistent
  rails at 1024.

## 10. Acceptance Criteria

- [ ] AC-1: T-1 fails before the fix and passes after; the room event timestamp exactly equals the
  persisted approval timestamp.
- [ ] AC-2: a skewed client clock cannot move a live approval away from server chronology or
  suppress the unseen pill.
- [ ] AC-3: an old/malformed event is inserted at the tail without reading client wall time; the next
  successful authoritative reconciliation replaces its sentinel even while pending, and a late
  pending fetch cannot overwrite a concurrently resolved card.
- [ ] AC-4: at 1024-1279 exactly one of search, agent or people/observer is active; opening another
  performs a focus-safe hand-off, and normal close restores the initiating control.
- [ ] AC-5: search renders the 0.2 token backdrop, focuses input, closes through Escape/backdrop/
  result/explicit action, restores focus, uses the documented 200ms token transition and disables
  it under reduced motion.
- [ ] AC-6: the four responsive bands pass AC-10 through AC-13; this dossier neither ships the
  deferred tablet redesign nor regresses wide persistent rails.
- [ ] AC-7: every transient panel has an accessible name/relationship and every keyboard-focused
  control retains the global visible focus indicator.
- [ ] AC-8: no client wall-clock call remains in the approval-requested insertion path.
- [ ] AC-9: targeted backend/frontend tests, frontend lint/typecheck/build and source-scan contracts
  pass; manual 1100px, reduced-motion and skewed-clock checks are recorded.
- [ ] AC-10: below 768px the chatroom remains single-pane; both side panels use `SDrawer`, compact
  rail overlays do not mount, and opening search and a drawer is mutually exclusive.
- [ ] AC-11: at 768-1023 this dossier preserves the deferred state: the current agent rail remains,
  people/observer remains a drawer, `chatroom--compact` is absent, and no compact backdrop or resize
  handle mounts.
- [ ] AC-12: at 1024-1279 the three transient surfaces follow AC-4, occupy the in-chat overlay layer
  and render no resize handle.
- [ ] AC-13: at >=1280 both rails remain persistent, right-rail resize persists, search remains
  feed-scoped, and no compact rail backdrop or focus trap mounts.

## 11. SRS Delta

Amend **[R24.32]** to:

> **[R24.32]** Chat uses four responsive layout bands. Below 768px the feed is single-pane and the
> agent and people/observer side panels open as drawers. From 768 through 1023px the target layout
> is likewise single-pane with both side panels available as drawers; until the announced change
> that moves it there, this band keeps a persistent agent rail beside the feed with people/observer
> as a drawer, which is a recorded and pinned deviation rather than a permitted alternative. From
> 1024 through 1279px the feed occupies the available layout width and search, agent and
> people/observer surfaces open as header-controlled, mutually exclusive in-chat overlays positioned
> below the header; no rail resize handle is shown. At 1280px and above the agent and
> people/observer rails are persistent, and the right rail width is resizable between the documented
> minimum and maximum, persisted locally per browser and re-clamped so the message column retains
> its minimum share. Wherever a
> side panel is transient, it is mutually exclusive with chatroom search. Every drawer, overlay
> panel and persistent rail scrolls within its own reachable region; content is never clipped
> without a reachable scroll surface.

Approved on 2026-08-31 with the application deferred: the delta is written to `REQUIREMENTS.md`
only once AC-10 through AC-13 pass, so the amended requirement never lands ahead of the behavior
it describes.

The 768-1023 clause deliberately states the target and the current deviation in the same sentence.
Stating the target alone would put a requirement into `REQUIREMENTS.md` that AC-11 of this very
dossier pins as violated on the day it lands, and stating the deviation alone would lose the
decision that FU-2 exists to carry out. FU-2 removes the second half of the clause when it moves
that band; nothing else in this dossier changes it.

## 12. Deviation Log

None — implementation has not started.

## 13. Follow-ups

- FU-1: automate broader feed geometry after E2E seeding is idempotent; this remains source FU-3.
- FU-2: move the 768-1023 agent rail into a drawer in its own announced responsive change; this
  remains source FU-6 and the intended R24.32 state.
- FU-3: promote the in-page focus boundary to shared only after a second non-conversation surface
  needs the same non-modal geometry.
