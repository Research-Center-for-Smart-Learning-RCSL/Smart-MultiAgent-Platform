---
type: bugfix
status: draft
created: 2026-07-22
requirements: [R13.20, R24.23]
depends_on: []
---

# What a chatroom client fails to reconcile after a disconnect

## 1. Summary

Four of the five findings in this dossier are the same defect reached from four directions: the
chatroom WebSocket is fire-and-forget, and the client's reconnect path recovers only *created*
messages. `replayDelta` appends (`frontend/src/slices/conversation/composables/useChatroomSocket.ts:94-115`,
applying each row through `applyMessageCreated` at `:178-192`), and the `since` query it calls
cannot carry anything else — the backend filters `deleted_at IS NULL`
(`backend/contexts/conversation/infrastructure/repositories/message_repo.py:87-90`) and orders
strictly forward by `created_at` (`:121-145`), so a deletion has no row to return and an edit of an
older message sorts outside the window. A frame missed during a disconnect is therefore missed
permanently: a deleted message stays rendered (F-11), an approval gate never appears (F-13), and the
stale cached row that F-11 leaves behind poisons the `before` cursor so that all older history
becomes unreachable for the life of the tab (V-2).

**Freshness note (2026-07-28).** Re-verified against current `HEAD` before build. Two things changed
since this dossier was written and are folded in below rather than left stale: (1)
`docs/tasks/2026-07-22-chatroom-socket-lifecycle/` — whose own Q-1 disagreed with this dossier's
Q-1 — shipped and is `status: implemented` (confirmed an ancestor of current `HEAD`, 147 commits
back); Q-1 below is updated to record the actual resolution rather than the standing disagreement.
(2) `d557752` (2026-07-23, "dispatch approval-gate side effects post-commit") added a *partial*
approval reconcile (`useChatroomSocket.ts:122-148`, `orchestration.ts:51-82`) to fix an unrelated
rollback defect; it revisits approval cards the client already holds but cannot discover one whose
`approval.requested` was itself missed, so F-13 as described here is narrowed, not closed — see the
updated F-13 entries in §2, §4, §5 and §7 Layer 5. All other file:line citations in this dossier were
re-checked against current `HEAD`; those that drifted (mostly inside `useChatroomSocket.ts`, which
the socket-lifecycle dossier also rewrote) were corrected in place.

The fifth and sixth items are not that. F-19 is an ordering defect — the frame arrives, and the
refetch it triggers is the one async path in the file with no generation guard while every sibling
has one. F-17 is a loading-state defect: the feed paints "No messages yet" before the first fetch
resolves. F-17 is in this group because it lives in the same two composables, not because it shares
a cause; §5 says so plainly rather than forcing it into the frame.

Sources: `docs/audits/2026-07-22-agent-to-user-conversation/findings.md` F-11, F-13, F-17, F-19
(all confirmed) and `docs/audits/2026-07-22-conversation-verification-gap/findings.md` V-2
(confirmed). All five are authoritative and adversarially verified; this dossier does not re-derive
them.

## 2. Observed vs Expected

### F-11 — edits and deletions during a disconnect are never reconciled

- **Observed** — `replayDelta` (`useChatroomSocket.ts:94-115`) is the only reconnect recovery for
  message state, and it appends only (`:106`). The backend `since` window cannot express a deletion
  (`message_repo.py:87-90`) and never returns an edited older row
  (`message_repo.py:135-145` — strictly-later keyset, ascending). The `message.deleted` and
  `message.updated` handlers (`useChatroomSocket.ts:245-255`, `:231-244`) are live-only.
- **Expected** — `[R24.23]` — "on reconnect, composables replay a delta fetch … to avoid gaps";
  `[R13.20]`. A gap that silently excludes deletions is not a delta fetch that avoids gaps.

### F-13 — approvals raised while disconnected never appear

- **Observed** — the connect burst resyncs messages, presence, activation, and — since `d557752`
  (2026-07-23, added for an unrelated rollback defect) — pending approval cards
  (`useChatroomSocket.ts:383-402`, the fourth call at `:400`). `approval.requested` /
  `approval.resolved` (`:311-334`) remain the sole *writers of new cards* into the orchestration
  store (`frontend/src/shared/stores/orchestration.ts:17`); the added `reconcilePending`
  (`orchestration.ts:51-82`) only revisits cards already present in `liveApprovals[roomId]`
  (iterating `Object.values(map)` at `:59`), fetching each via the existing single-item
  `GET .../approvals/{id}` (`backend/app/api/v1/orchestration.py:216-226`) on connect and every
  `APPROVAL_RECONCILE_INTERVAL_MS = 30_000` (`useChatroomSocket.ts:122`). This closes the
  *resolution* half of the gap for a card the client already holds — a dropped `approval.resolved`,
  or a rolled-back gate, now self-heals within 30s instead of pinning `pending` forever. It does
  nothing for the *discovery* half: a missed `approval.requested` still creates no card to revisit,
  and the only REST list remains run-scoped (`backend/app/api/v1/orchestration.py:229-247`), which
  the conversation slice never calls. So the scenario this finding names — a gate raised and
  resolved entirely while disconnected — is still invisible for the rest of the run.
- **Expected** — `[R24.23]`, `[R13.20]`. Same contract as F-11.

### V-2 — a hard-deleted cached message poisons the `before` cursor permanently

- **Observed** — `loadEarlier` anchors on `messages.value[0]`
  (`frontend/src/slices/conversation/composables/useChatroomMessages.ts:135`) and sends it as
  `before` (`:139-142`). The backend anchor SELECT filters `deleted_at IS NULL` and raises on a
  miss (`message_repo.py:93-110`), which the route maps to 422
  (`backend/app/api/v1/messages.py:160-168`). The catch at `useChatroomMessages.ts:151-155` fires a
  toast and leaves `hasOlderMessages` true, so every retry repeats the 422.
- **Expected** — the codebase states the expectation itself: the identical 422 on the `since` cursor
  has an explicit `BUG-8` fallback to `qc.invalidateQueries`
  (`useChatroomSocket.ts:107-114`). A dead cursor must degrade to a refetch, not to a permanent
  error. Plus `[R24.23]`.

### F-19 — `message.updated` refetches are unsequenced

- **Observed** — `useChatroomSocket.ts:231-244` fires `getMessage(updatedId)` and writes the result
  into the cache on resolve, with no generation counter and no version comparison. Every sibling
  async path in the same file guards: `replayGeneration` (`:77`, captured `:102`, checked `:105`),
  `activationGeneration` (`:78`, `:160-176`), delete tombstones (`:81-92`).
- **Expected** — Not a missing pattern — a missing *instance* of an established local pattern.
  `[R24.23]`'s ordering discipline, which the three siblings already implement. The downstream harm
  is concrete: the stale `version` left in the cache is what `useChatroomMessageEditing` sends as
  `If-Match` (`frontend/src/slices/conversation/composables/useChatroomMessageEditing.ts:25,37,50`),
  producing a spurious 412 on the next edit.

### F-17 — "No messages yet" before the first fetch resolves

- **Observed** — `useChatroomMessages` destructures nothing from `useQuery` beyond the data
  (`:79-86`) and returns no `isLoading`/`isPending`/`isError` (`:332-354`).
  `ChatroomView.vue:98-104` gates the empty state purely on
  `!messages.length && !streamingEntries.length && !liveApprovals.length`. `hasOlderMessages`
  initialises to `true` (`useChatroomMessages.ts:72`), so "Load earlier" renders above the false
  empty state at the same time.
- **Expected** — `docs/UI/12-shared-patterns.md:335`; `docs/UI/07-conversation.md:980-1001`. The
  house pattern is in the same slice — `ObserverPanel.vue:20-39` branches on loading first — and the
  identical defect was fixed one commit ago elsewhere in the product (`e381559`, "stop the key-group
  list claiming empty before it has loaded"), by exposing `isLoading` from the composable and
  binding it in the view. It was never generalised to the message feed.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | **Must this dossier land before `docs/tasks/2026-07-22-chatroom-socket-lifecycle/` (a2u F-1, the 120s reaping)?** | **RESOLVED — the other dossier's Q-1 records the actual decision: socket-first.** The user was shown both dossiers' arguments (this one's is preserved below) and chose the opposite of what this dossier recommends. `chatroom-socket-lifecycle` is `status: implemented` (approved and implemented 2026-07-24, confirmed an ancestor of current `HEAD`), so the ordering question is moot for scheduling — it already happened. What survives from that dossier's coordination note is a live obligation: re-derive F-11/F-13 against the post-fix baseline, folded into §2 and §5 rather than kept as a separate pending task. | Original argument, kept for the record since the reasoning is still sound even though the schedule went the other way: the audit states `findings.md:83-85` and `:355-357` record that F-11 "becomes *worse* once F-1 is fixed" — F-1 closed every idle socket at 120s pre-fix, so the connect burst ran roughly every two minutes for free, repairing nothing for F-11/F-13 but keeping every disconnect sub-second. Post-fix, disconnects are rare and arbitrarily long, raising per-incident stakes even as frequency falls. **What the post-fix baseline actually changed**, checked directly against current `HEAD`: disconnect frequency for a healthy socket did fall as predicted (heartbeat + 5s stability window shipped in `ws-manager.ts`), and separately — not predicted by either dossier — `d557752` (2026-07-23) added a partial connect-time/interval approval reconcile that narrows but does not close F-13 (see §2, §5, §7 Layer 5). Neither change alters this dossier's fix design; both are folded into the relevant sections rather than argued here. |
| Q-1a | Is there a code-level conflict between the two dossiers? | **Moot — already resolved by build order, and a third change landed in the same region.** `chatroom-socket-lifecycle` merged first and rewrote the `onStatus` connect burst as anticipated (heartbeat, stability window, cap signal). `d557752` (2026-07-23) then added a fourth resync, `reconcileApprovals()`, to the same block for an unrelated reason. The connect burst this dossier's Layer 1 must edit is therefore not the 3-call block either dossier originally described — it is now 4 calls (`useChatroomSocket.ts:383-402`); see the updated Layer 1/Layer 5 fix design in §7. | Recorded so a reader comparing this table to the shipped code understands why neither dossier's original code-conflict prediction matches what actually needed reconciling. |
| Q-2 | On connect, replace `replayDelta` with an invalidation, or fetch the page directly? | **Fetch the page directly on connect and merge it, keeping `applyMessageCreated`'s side-effects for agent rows.** Keep `replayDelta` unchanged for the degraded-mode poll (`:59-65`). | A bare `qc.invalidateQueries` would be one line and would reconcile correctly through `mergeMessages` — but it would **break an existing test**. `useChatroomSocket.test.ts:201-218` ("clears a stale badge when the recovery reply arrives via delta-replay") drives `statusHandlers → true` and asserts the per-agent error badge clears, which happens only inside `applyMessageCreated` (`:188-191`). That test is a deliberate pin, not an accident. Fetching the page directly keeps one request, reconciles deletions, and lets the agent-state side-effects run over the returned rows. |
| Q-3 | How far back does reconnect reconciliation reach — the recent query window only, or the paged-back `olderMessages` too? | **Recent window only.** `olderMessages` (`useChatroomMessages.ts:71`) is explicitly out of scope; the V-2 fallback makes a stale row there non-fatal instead of fatal. | `mergeMessages` deliberately keeps rows older than the fetched window (`frontend/src/slices/conversation/utils/mergeMessages.ts:26-31`, with the reason at `:9-11`), so a window refetch cannot reach them. Revalidating them needs either a bulk read-by-ids endpoint that does not exist, or discarding `olderMessages` on every reconnect — which destroys scroll position for a user who has paged back. Recorded as FU-1. |
| Q-4 | F-13 needs a read side, and the `approvals` row has no room. Accept a migration? | **Yes — persist `chatroom_id` on `approvals`, add a chatroom-scoped list endpoint, resync it on connect.** It is the last commit of the change and independently revertible. | `approvals` (`backend/contexts/orchestration/infrastructure/tables.py:45-78`) has no `chatroom_id`, and neither does `workflow_runs` (`:17-39`). The room is a transient publish parameter only: resolved at `backend/contexts/workflow/application/executors/approval_gate.py:67-76` from the node config **or** the trigger payload, threaded through `create_approval_gate` (`backend/contexts/orchestration/interfaces/facade.py:196-209`) and used solely to pick a channel (`backend/contexts/orchestration/application/approval_service.py:112-118`). Deriving it after the fact is only partly possible — `run_engine.py:176` persists `context={"trigger_payload": …}`, so the trigger-payload arm is recoverable and the node-config arm is not. A column is the honest answer. This decision predates `d557752`'s single-item reconcile (§2, §5) and is unchanged by it: that mechanism cannot discover an approval id it was never told about, so a list endpoint sourced from a room column is still the only way to close the discovery half. |
| Q-5 | Is F-13 worth a migration, given the audit calls it "an observability gap, not a stalled gate"? | **Yes, but it is the lowest-priority commit and the correct thing to drop if scope must be cut.** | `findings.md:400-401` is explicit that `ApprovalCard.vue` is display-only — agents vote, humans do not — so a missed frame stalls nothing. At the time this was written it was the only consumed channel state in this room with *no* read side at all; `d557752` has since added a partial one for an unrelated reason (§2, §5), but it only revisits known cards, so the migration's value is unchanged — the column is still three lines plus a migration. If it is dropped, it must be dropped as a whole (column, endpoint, resync) and recorded as a follow-up, not left half-built. |
| Q-6 | Does FU-1 of `docs/tasks/2026-07-22-prompt-assistant-delivery-recovery/spec.md` — replay/cursor semantics on the pub/sub layer — subsume any of these? | **Partially: the delivery half of F-11, F-13, and V-2's disconnect arm. None of the rest.** Do not couple this dossier to it. | See §5 for the full accounting. Summary: replayed frames would let the existing live handlers apply the missed deletion or approval, which is real subsumption. It would **not** cover V-2's second, disconnect-free trigger (the retention purge publishes nothing at all — `backend/contexts/conversation/application/retention_service.py:93-95`, recorded as FU-2 of the verification audit), it would not cover F-19 (an ordering defect that a replayed burst arguably makes *more* likely), and it has nothing to do with F-17. |
| Q-7 | F-19 — generation guard, or version comparison? | **Generation guard.** It is the file's established pattern, cited three times. | Three siblings in the same file already do it (`:77,102,105`; `:78,160-176`; `:81-92`), and the prompt-assistant dossier's §7 records the same guard independently present in `useWorkflowRunSocket.ts:24-34`, `useBuildStateSocket.ts:105-117` and `useRagConfigSocket.ts:94-101` — each acquired from a real audit finding. A version comparison would additionally require trusting `version` monotonicity across a refetch that may itself be stale. Guard on generation; the version then follows the winning response. |
| Q-8 | F-17 — loading state only, or also a distinct error state? | **Both.** Expose `isPending` and `isError`; render a skeleton while pending and an error state with a retry on failure. | The finding's own failure scenario turns on this (`findings.md:483-486`): after TanStack exhausts retries on a 5xx, the false empty state persists and is indistinguishable from a genuinely empty room. Loading-only leaves that half unfixed. `ObserverPanel.vue:20-39` shows the skeleton half of the pattern; the error half needs two new i18n keys in `frontend/src/slices/conversation/locales/en.json` and `zh-TW.json`. |
| Q-9 | Does this dossier depend on any other open dossier? | **No. `depends_on: []`.** | Q-1's constraint runs the other way — the socket dossier should follow this one. No dossier in the a2u hand-off map (`findings.md:668-683`) produces an artifact this change consumes. The prompt-assistant dossier is a sibling instance of the class, not a prerequisite: its §6 and §7 are reused here as established house patterns, which is a documentation reuse, not a code dependency. |

### Q-1 conflict note — historical record; resolved the other way

**This disagreement is resolved.** `docs/tasks/2026-07-22-chatroom-socket-lifecycle/spec.md` §3
Q-1 concluded the opposite of this dossier's recommendation below — that it should land first —
and that is the order the user chose and that actually shipped (`status: implemented`,
2026-07-24). The two arguments are kept here verbatim because the reasoning is still the
authoritative account of the trade-off, not because the scheduling question is still open.

`docs/tasks/2026-07-22-chatroom-socket-lifecycle/spec.md` §3 Q-1 concludes **the opposite**: that
it should land first, and that this dossier should follow and re-derive F-11 and F-13 against the
post-fix baseline. Both conclusions were reached independently, both are argued from the same
audit note (`findings.md:83-85`), and **they cannot both be honoured.** Neither dossier's author
saw the other's reasoning. Recording the disagreement rather than silently deferring to one, since
picking a winner here is a scheduling decision with a real regression on the losing side.

The two arguments, stated as fairly as each dossier states its own:

- **Socket-first (that dossier's case).** F-1's churn *generates* the gaps rather than repairing
  them: it manufactures ~30 disconnects per hour per socket, and the connect burst
  (`useChatroomSocket.ts:347-356` at the time; the block has since moved to `:383-402` and, per
  `d557752`, gained a fourth call that partially touches approvals — see the freshness note in §1)
  demonstrably does not reconcile deletions or edits — `replayDelta` appends only. So removing the
  churn removes *no repair* for F-11 or F-13 while removing 30 gaps per hour. The one piece of
  state the burst genuinely repairs is typing, which is F-18, fixed inside that same dossier.
- **Reconnect-first (this dossier's case).** Fixing F-1 changes the gap *profile*, not just the
  count: today every gap is ~1 second, after the fix a sleeping laptop produces a single gap of
  minutes. Per-incident exposure to exactly this reconciliation defect rises sharply even as
  incident frequency falls, and reconnect reconciliation is correct at any disconnect frequency,
  so landing it first is never wrong.

**Both are correct about their own half.** The honest synthesis is that F-1's fix trades *many
short* gaps for *few long* ones, and neither dossier establishes which product is larger — the
socket dossier counts incidents, this one weighs them. The disagreement is therefore about an
unmeasured quantity, not about a fact either side got wrong.

**Recommendation for the tie-break, as originally argued:** land this dossier first, on risk
asymmetry rather than on exposure arithmetic — reconnect reconciliation is unconditionally correct
at any disconnect frequency, while landing the socket fix first leaves a window with long gaps and
no reconciliation. **What actually happened:** the user weighed both cases and chose socket-first;
that dossier merged, shipped, and is `status: implemented`. This dossier now builds against that
post-fix baseline, which is why the window this recommendation warned about was real but is now
closed retroactively rather than avoided — see the freshness note in §1 and the updated Q-1 above
for what carried over from that window (lower F-11/F-13 frequency, plus the unrelated `d557752`
partial approval reconcile).

## 4. Reproduction

All four reconciliation paths are deterministic in unit tests, which is where the regression suite
should encode them. The transport is already mocked in
`frontend/src/slices/conversation/__tests__/useChatroomSocket.test.ts` with captured
`statusHandlers` (`:17`, registered at `:27-30`) that the existing suite drives in only four places
(`:209`, `:291`, `:312`, `:329`, `:369`, `:403`) and never for a drop-then-restore.

**F-11 — deletion missed during a disconnect.**

```
seedCursor(qc, 'm_old')                 // helper at :108-112
statusHandlers.forEach(h => h(false))   // socket drops
// author deletes m_old server-side; the message.deleted frame is never delivered
listMessagesMock.mockResolvedValueOnce([])   // the since-delta legitimately returns nothing
statusHandlers.forEach(h => h(true))    // reconnect
// today: m_old is still in the cache, forever
```

**V-2 — poisoned `before` cursor.** Manual, and it is the more instructive path. Open a busy room,
click "Load earlier" three times so `messages.value[0]` is an old row. Toggle DevTools → Network →
Offline for ~20 s. From a second browser as the author, delete that exact message. Come back online;
the socket reconnects and `replayDelta` fetches only newer rows, so the deleted row stays cached.
Click "Load earlier": `GET /messages?before=<deleted id>` returns 422
(`message_repo.py:109-110` → `messages.py:167-168`), the toast fires
(`useChatroomMessages.ts:151-155`), and `hasOlderMessages` is still `true` so the button remains.
Every subsequent click fails identically. **The disconnect is not required** — the retention purge
hard-deletes and publishes nothing (`retention_service.py:93-95`), so an overnight sweep produces
the same poisoned anchor for any tab left open.

**F-13 — approval raised while disconnected.** Requires a workflow with an `approval_gate` node
whose `chatroom_id` resolves (`approval_gate.py:67`). Drop the socket, let the gate open, reconnect.
The connect burst (`useChatroomSocket.ts:383-402`) now runs four resyncs, including
`reconcileApprovals()` (`:400`) — but that call only revisits cards already in
`liveApprovals[roomId]` (`orchestration.ts:57-59`). Since the `approval.requested` frame that would
have created the card was itself missed, there is nothing to reconcile, and the card still never
renders. A subsequent `approval.resolved` for the same gate is still discarded at
`orchestration.ts:33` (`if (!map?.[approvalId]) return`).

**F-19 — out-of-order edit refetches.** Deterministic with two deferred promises: emit
`message.updated` twice for the same id, resolve the *second* `getMessage` first and the first
second. The cache is left holding edit 1's content and its stale `version`. **Note for whoever
writes this test:** the existing `../api` mock declares only `listMessages`
(`useChatroomSocket.test.ts:57-59`), so `getMessage` is currently `undefined` in that file and the
`message.updated` branch has never been executed by any test. The mock must be extended first.

**F-17 — false empty state.** Open a chatroom on a cold cache with the network throttled. The feed
paints "No messages yet — Start the conversation…" (`ChatroomView.vue:98-104`) with the "Load
earlier" button above it, until the backlog arrives.

## 5. Root Cause Analysis

**These five findings are two root causes plus one unrelated defect. Stated plainly:**

### Group A — one root cause: a lost frame with no durable read side (F-11, F-13, V-2)

The causal chain, each link cited:

1. The chatroom channel is fire-and-forget. The contract is stated in the shared kernel and its
   second half is the client's job: `backend/shared_kernel/realtime/pubsub.py:3-6` — "the server does
   not replay; client fetches delta on reconnect".
2. The client implements that second half for **creations only**. `replayDelta`
   (`useChatroomSocket.ts:94-115`) calls `listMessages(roomId, {since})` and appends every row via
   `applyMessageCreated` (`:106`, `:178-192`).
3. The `since` window is structurally incapable of carrying anything else. It filters
   `deleted_at IS NULL` (`message_repo.py:87-90`) — and deletion is a genuine row DELETE
   (`message_repo.py:219-224`, per V-2), so there is no tombstone row that *could* be returned — and
   it is a strictly-forward keyset on `created_at` (`:135-145`), so an edit of an older row sorts
   outside the window by construction.
4. For approvals, a partial read side now exists but does not reach this case: `d557752`
   (2026-07-23) wired the existing single-item `GET .../approvals/{id}` into a connect-time and
   30s-interval reconcile (`useChatroomSocket.ts:122-148`, `orchestration.ts:51-82`) to fix an
   unrelated rollback defect. It can only revisit an approval id the client already holds; no
   chatroom-scoped *list* endpoint exists to discover one it never learned of, and the `approvals`
   row still carries no room (`tables.py:45-78`), so a missed `approval.requested` remains
   undiscoverable without the schema change proposed below.

**Root cause: step 2 — the reconnect recovery is append-only, over a delta window that cannot
express mutation.** It is the earliest link whose correction prevents all three symptoms, and it is
correctable client-side for messages.

**Why V-2 is not an independent defect, and where it is.** V-2's disconnect-triggered arm is F-11's
residue: the stale cached row that F-11 leaves behind *is* the poisoned anchor. Fixing F-11 removes
that arm. But V-2 has a **second, independent trigger** that no reconnect fix reaches — the
retention purge hard-deletes and publishes nothing (`retention_service.py:93-95`; the module imports
no `Publisher`). So V-2 needs its own fallback regardless, and that fallback is already specified by
the codebase: the `BUG-8` degrade-to-refetch at `useChatroomSocket.ts:107-114`, applied to the
`before` cursor as it was to `since`.

### Group B — a different root cause: a missing guard on one async path (F-19)

No frame is lost. `message.updated` arrives and is handled. The defect is that
`useChatroomSocket.ts:231-244` writes an async result into the cache with no ordering guard, in a
file where `replayGeneration` (`:77,102,105`), `activationGeneration` (`:78,160-176`) and the delete
tombstones (`:81-92`) all exist precisely to prevent this. **This is a missing instance of an
established local pattern, not a missing pattern.** Fixing Group A would not touch it; a pub/sub
replay layer would arguably make it more likely by delivering bursts of frames.

### Not a reconciliation defect at all — F-17

An empty state shown before the first fetch resolves is a **loading-state defect**. Nothing is lost,
nothing is out of order, and no socket is involved: the query result simply has no
pending/error projection (`useChatroomMessages.ts:79-86`, `:332-354`), so the view has nothing to
branch on (`ChatroomView.vue:98-104`). It is in this dossier because it lives in the same two
composables and would be reverted with them — the grouping rule the a2u hand-off states for itself
(`findings.md:664-666`) — not because it shares a cause with anything else here.

### What FU-1 would and would not subsume

`docs/tasks/2026-07-22-prompt-assistant-delivery-recovery/spec.md` FU-1 proposes replay/cursor
semantics on the pub/sub layer (Redis Streams), which `pubsub.py:4-7` anticipates. Accounting:

| Finding | Subsumed by FU-1? |
|---|---|
| F-11 | **Yes, the delivery half.** A replayed `message.deleted` / `message.updated` frame would reach the existing handlers (`useChatroomSocket.ts:231-255`) and apply correctly. |
| F-13 | **Yes, the delivery half.** A replayed `approval.requested` would reach `:311-334`. Note this is the *only* remedy that does not need the Q-4 migration — which is a genuine argument for deferring F-13 rather than for skipping it. `d557752`'s partial reconcile (§2, §5) does not change this row: it revisits known cards, not events, so a replayed frame is still the only thing that lets an *unknown* approval surface without the Layer 5 migration. |
| V-2 | **Half.** The disconnect arm goes away with F-11. The retention-purge arm does not: FU-1 replays frames that were published, and the purge publishes none. |
| F-19 | **No** — and plausibly worsened. |
| F-17 | **No.** Unrelated. |

The per-channel fixes in this dossier are correct and should ship regardless. FU-1 is what stops the
class recurring on the next channel added; §6 of the prompt-assistant dossier already surveys all
seven consumed channels and marks which have a recovery mechanism.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every chatroom client, every room, continuously — the defects are in the sole
real-time entry point (`useChatroomSocket.ts:1-8`). Concretely: a message deleted for moderation or
compliance stays rendered on a disconnected client's screen (F-11); all history older than a
poisoned anchor is unreachable for the life of the tab (V-2); an approval gate is invisible for the
rest of its run (F-13); a spurious 412 blocks the next edit (F-19); a busy room can look empty on
open (F-17). **No persisted data is wrong** — every affected state is client-side cache. See §7 for
the data-repair position.

**Sibling suspects.** §6 of `docs/tasks/2026-07-22-prompt-assistant-delivery-recovery/spec.md`
surveys all seven consumed WebSocket channels and marks each confirmed or cleared with citations;
that survey is reused here rather than re-derived. Its verdicts: `/workflow-runs/{id}`,
`/graphrag/{id}`, `/knowmap/{id}`, `/rag-configs/{id}` and the notification/ban-kick consumers of
`/user/{id}` are **cleared**, each having a resync-on-connect, a backstop poll, or REST as the
source of truth. `/prompt-assistant/{id}` is **confirmed vulnerable** and is that dossier's subject.
`useObservations.ts:144-175` is **cleared as a distinct defect** and recorded there as its FU-2.

That leaves `/chatroom/{id}`, which that survey marks **cleared** — "the exemplar; has both halves".
**This dossier narrows that verdict.** It is correct for the mechanisms that survey names
(`replayDelta` on connect, the degraded-mode poll, the re-armed watchdog) and correct for the
*created-message* half. It is wrong for mutation: the exemplar reconciles additions and nothing
else. Recorded here so the two documents do not disagree silently.

Siblings checked inside this slice:

- **`useChatroomMessages.refreshOlderMessage` (`:314-325`) — cleared, but for a weak reason.** It
  has the same shape as F-19 (an async `getMessage` writing into shared state on resolve) and the
  same absence of a guard. It is cleared only because it writes into `olderMessages`, a plain ref
  that no reconnect path touches, and because its failure mode is a drop rather than a stale write
  (`:320-324`). Two rapid edits to a paged-back message still land out of order there. **Fragile,
  not broken** — recorded as FU-3, and the natural second consumer of whatever guard F-19's fix
  produces.
- **`useChatroomMessages.confirmDelete` optimistic rollback (`:269-291`) — cleared.** The `catch`
  re-inserts by id rather than restoring a snapshot, with a comment at `:280-282` explaining
  precisely why `mergeMessages` must not be used there. It is already the careful version of this
  class.
- **The degraded-mode poll (`useChatroomSocket.ts:59-65`) — confirmed, same gap, same fix.** It
  calls `replayDelta` on a 10-second timer, so while a socket is degraded the client is
  append-only for as long as that lasts. The fix must decide whether the poll also reconciles;
  §7 keeps it on the delta path deliberately (a full page every 10 s is a different cost profile),
  which means degraded mode retains the F-11 gap. Stated as a limitation, not hidden.
- **`ChatroomSearchPanel` and the export modal — cleared.** Neither consumes WS state; both are
  request/response.
- **`mergeMessages` (`mergeMessages.ts:13-40`) — cleared, and it is the load-bearing asset.** Its
  in-window deletion drop (`:26-31`) is exactly the reconciliation F-11 needs; the fix consists
  largely of *reaching* it on reconnect. Its deliberate retention of out-of-window rows (`:9-11`,
  `:27-29`) is what leaves Q-3 out of scope, and its stated justification — "their deletions arrive
  via the `message.deleted` WS event" — is the assumption V-2 breaks. The comment is inaccurate
  today and must be corrected as part of this change.

## 7. Fix Design

Five layers, deliberately separable, ordered so each is independently revertible (§9 sequences the
rollback).

**1. Reconcile the recent window on connect (F-11).** Replace the connect-path `replayDelta()` call
at `useChatroomSocket.ts:396` with a page fetch — `listMessages(roomId, { limit: PAGE_SIZE })` —
merged into the cache through the same `mergeMessages` semantics the query function already uses
(`useChatroomMessages.ts:81-85`), then run `applyMessageCreated`'s agent-state side-effects
(`:188-191`) over the returned rows. `replayDelta` stays exactly as it is for the degraded poll
(`:64`) and for `message.created` (`:219`).

Four properties to get right:

- **Carry a generation guard**, reusing `replayGeneration` (`:77,102,105`). A flapping socket
  trivially overlaps two connect fetches.
- **Preserve the agent-badge clear.** `useChatroomSocket.test.ts:201-218` pins that a recovery reply
  arriving via the connect path clears the per-agent error badge. That test must keep passing
  unmodified; it is the reason a bare `invalidateQueries` is not the fix (Q-2).
- **Fire on every connect, including the first**, matching the existing block's behaviour
  (`:383-402`) — which also closes the handshake window at no extra cost.
- **Keep the request count at one per connect.** Under unfixed F-1 that matters (Q-1b of the socket
  dossier).

**Why this is not the contradiction it looks like.** The `message.created` handler carries a
`FIX-04` comment (`:220-221`) stating that a delta append replaced a "blind invalidation so the
additive merge cache is never replaced with a smaller window". A reviewer will read layer 1 as
reverting that. It does not: `mergeMessages` *is* the additive merge — it unions `prev` with the
fetched page and drops only rows inside the fetched window that the server no longer has
(`mergeMessages.ts:17-31`). The window never shrinks. FIX-04's hazard was a raw cache replacement,
not a merged refetch.

**2. `before`-cursor 422 fallback (V-2).** In `useChatroomMessages.loadEarlier`'s catch
(`:151-155`), distinguish the dead-cursor 422 from a transport failure: on 422, drop the poisoned
anchor from the caches that hold it (`olderMessages` at `:71` and the query cache), invalidate
`convKeys.messages(chatroomId)`, and retry once with the new oldest row — mirroring the `BUG-8`
fallback at `useChatroomSocket.ts:107-114` line for line, including its "degrade to a refetch rather
than to an error" intent. Keep the toast for the genuine-failure branch only. Also correct the now-
inaccurate comment at `mergeMessages.ts:9-11`.

**3. Generation guard on `message.updated` (F-19).** A fourth counter alongside the three existing
ones, captured before `getMessage` and checked on resolve (`:231-244`). Six lines, matching
`:102,105` exactly.

**4. Loading and error states (F-17).** Expose `isPending` and `isError` from `useChatroomMessages`
(`:79-86` → `:332-354`), bind them in `ChatroomView.vue:98-104`: skeleton while pending, error state
with a retry action on failure, empty state only when settled and genuinely empty. Also gate the
"Load earlier" button on the query having settled, since `hasOlderMessages` initialises `true`
(`:72`). Follow `ObserverPanel.vue:20-39` for the skeleton branch and commit `e381559` for the
composable-exposes-flag shape. Two new keys in
`frontend/src/slices/conversation/locales/{en,zh-TW}.json`, rendered via `$t()`.

**5. Approvals read side (F-13) — the only backend change.**

- Alembic migration: add nullable `chatroom_id` to `approvals` with an FK to `chatrooms`
  (`ON DELETE SET NULL`), mirroring `agent_instances` (`tables.py:160-165`). Forward-compatible:
  old code ignores the column.
- Persist it in `ApprovalService.create_gate` (`approval_service.py:58-79`, method continues to
  `:119`) — the value is already in hand as a parameter of the method (`:58`) and already used for
  channel selection at `:115`.
- New route `GET /api/chatrooms/{chatroom_id}/approvals`, gated by `resolve_room_access` +
  `ensure_can_read` like every other room-scoped read (`backend/app/api/v1/messages.py:153-158`),
  **not** by `_assert_project_member` — the room is the resource here. Explicit Pydantic projection
  matching the shape `orchestration.ts:20-25` already stores; reuse `_approval_with_votes_out`
  (`orchestration.py:216-226`).
- **Wire it into the existing reconcile, do not add a fifth resync.** The connect burst
  (`useChatroomSocket.ts:383-402`) already runs a fourth call, `reconcileApprovals()` (`:400`,
  landed via `d557752`, 2026-07-23, for the unrelated rollback defect — see §2/§5). That call and
  the new list endpoint solve complementary halves of the same problem — discovering a card the
  client is missing versus refreshing one it already has — so they belong in one code path: on
  connect, first fetch the room's approval list and `upsertApproval` (`orchestration.ts:20-25`) any
  gate not already in `liveApprovals[roomId]`, then let the untouched `reconcilePending`
  (`orchestration.ts:51-82`) continue reconciling state for cards now known. Adding a separate fifth
  resync alongside `reconcileApprovals` instead of extending it would leave two approvals-related
  connect calls doing overlapping work, which is exactly the kind of duplication this dossier's own
  Fix Design elsewhere argues against (see the F-19 generation-guard reuse in §7 layer 3). Own
  generation guard, own best-effort catch, same as the existing three/four.

**Why this corrects rather than masks.** The defect is that the client's recovery path cannot
express mutation, not that it runs too rarely. Making reconnects more frequent, or adding a poll,
would leave every missed deletion missed — it would only shorten the window in which the user sees
a message that no longer exists. Layer 1 reaches the reconciliation logic that already exists and
is already correct (`mergeMessages.ts:26-31`), which is why it is small. Layer 2 implements the
degrade-to-refetch behaviour the codebase already declares as its policy for a dead cursor. Layer 3
adds the file's own established guard to the one path missing it. Layer 5 supplies a read side where
there is none; nothing short of that recovers an approval, and the alternative — making
`resolveApproval` (`orchestration.ts:27-35`) synthesise a record for an unknown id — would render a
card whose content the client never received, which is masking of the worst kind.

**Data repair.** **None required, and none possible.** Every state these defects corrupt is
client-side: the TanStack cache, `olderMessages`, and the in-memory orchestration store
(`orchestration.ts:17`). All three are discarded on reload; nothing incorrect was ever persisted, no
message was retained or deleted wrongly, and no data was disclosed to anyone not entitled to it. The
one durable question — whether content deleted from the transcript survives in a derived copy — is a
real problem but a different one, tracked as V-1 of the verification audit against
`docs/tasks/2026-07-22-compaction-scoping-and-durability/`.

**Backfill position for layer 5's new column.** **Do not backfill.** Rows created before the
migration get `NULL`, and the room is only partly derivable after the fact —
`run_engine.py:176` persists the trigger payload so `approval_gate.py:67`'s second arm is
recoverable, but its first arm (a node-configured `chatroom_id`) is not. A partial backfill would
produce a column that is silently wrong for some rows, which is worse than one that is honestly
empty. Approvals are short-lived (`timeout_seconds` defaults to 1800 at `approval_gate.py:56`), so
the `NULL` population self-clears within an hour of normal operation. The new endpoint returns
nothing for pre-migration rows; that is correct and is a one-hour condition.

## 8. Regression Test Plan

**The failing test comes first**, in
`frontend/src/slices/conversation/__tests__/useChatroomSocket.test.ts` (extend; the 20 existing
tests must all keep passing, in particular `:201-218`, which constrains the design per
Q-2):

> **`reconciles a message deleted while the socket was down`** — seed the cursor via the existing
> `seedCursor` helper (`:108-112`) with two rows, drive `statusHandlers → false` then `→ true`, have
> the mocked fetch return only the surviving row, and assert the deleted row is absent from
> `qc.getQueryData(['conversation','messages',ROOM])`.
>
> **Why it fails today:** the connect handler calls `replayDelta` (`useChatroomSocket.ts:396`),
> which fetches `{since}` and appends (`:106`). Nothing in that path can remove a cached row — the
> only removal sites are the live `message.deleted` handler (`:245-255`) and `mergeMessages`
> (`mergeMessages.ts:26-31`), and neither is reached.

Then, in the same file:

| Test | Why it fails today |
|---|---|
| `reconnect reconciliation does not drop messages older than the fetched window` | guards Q-3's boundary; no reconciliation exists to test |
| `overlapping connect reconciliations cannot apply stale data` (two deferred fetches, resolve out of order) | no guard on the connect fetch beyond `replayDelta`'s own |
| `applies only the newest message.updated when two refetches resolve out of order` (F-19) | `:231-244` has no generation counter — the first-arriving-last response wins. **Requires extending the `../api` mock at `:57-59`, which declares only `listMessages`; `getMessage` is currently `undefined` and the `message.updated` branch has never been executed by any test in this file** |
| `does not leave a stale version in the cache after out-of-order edits` | same cause; this is the assertion that connects F-19 to the spurious 412 at `useChatroomMessageEditing.ts:37,50` |
| `discovers an approval raised while disconnected` (F-13) | no chatroom-scoped list endpoint or client call exists; the connect burst's existing `reconcileApprovals()` (`useChatroomSocket.ts:400`) only re-fetches ids already in `liveApprovals[roomId]` and has nothing to fetch for a gate the client never learned of |
| `does not overwrite a newer approval frame with a stale resync response` | mirrors the activation guard test at `:324-349` (`does not overwrite a newer activation event with a stale reconnect response`) |

**`frontend/src/slices/conversation/__tests__/useChatroomMessages.test.ts`** (extend; the api mock at
`:15-24` already declares `listMessages`, and the `msg()` factory at `:60-74` already produces
complete rows). **A grep for `loadEarlier` and `hasOlderMessages` across
`frontend/src/slices/conversation/__tests__/` returns zero hits — pagination has no test coverage
at all today**, so these are wholly new:

| Test | Why it fails today |
|---|---|
| `recovers from a 422 on the before cursor by dropping the dead anchor and refetching` (V-2) | `:151-155` is a bare catch: toast, no anchor removal, no invalidation, `hasOlderMessages` untouched |
| `does not repeat the same failing before request on a second click` | the poisoned anchor stays at `messages.value[0]` (`:135`), so every click reissues it |
| `still toasts on a genuine transport failure` | pins that the new 422 branch does not swallow real errors |
| `exposes isPending until the first fetch settles` (F-17) | `:332-354` returns no such flag |
| `exposes isError after the query exhausts retries` | ditto |

**`frontend/src/slices/conversation/__tests__/ChatroomView.test.ts`** (extend) — the assertion that
speaks to user impact: **`does not render the empty state while the first fetch is pending`**, and
its pair, `does not render "Load earlier" before the query settles`. Both fail today via
`ChatroomView.vue:98-104` and `useChatroomMessages.ts:72`.

**Backend — new `backend/tests/unit/test_chatroom_approvals_read.py`** (layer 5 only):

| Test | Why it fails today |
|---|---|
| room reader lists the room's approvals → 200, pending and resolved both present | route does not exist |
| an approval for a different room is not returned | pins the new column's scoping |
| a non-member → 403 via `ensure_can_read`, consistent with `messages.py:153-158` | route does not exist |
| unauthenticated → 401 | — |
| pre-migration rows with `chatroom_id IS NULL` are simply absent, not an error | pins the Q-4 no-backfill decision |

Extend `backend/tests/unit/test_orchestration_services.py:301-320` (`test_create_gate`, which
already passes `chatroom_id=_ROOM` at `:320`) to assert the value now reaches
`approvals.insert` — today it is used only for channel selection at `approval_service.py:112-118`.

**Full gate** (`frontend/CLAUDE.md`, `backend/CLAUDE.md`): `pnpm test`, `pnpm lint`,
`pnpm typecheck`, `pnpm build`, and — because layer 5 changes the OpenAPI surface —
`pnpm run gen:api` followed by `pnpm run check:openapi-drift`; `pytest -q`, `ruff check .`,
`ruff format --check .`, `mypy .`, plus `alembic upgrade head` on a clean database.

## 9. Risks and Rollback

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Layer 1 breaks the pinned badge-clear test** (`useChatroomSocket.test.ts:201-218`) | high if implemented as a bare invalidation | a real behaviour regression, caught in CI | Q-2's decision: fetch the page directly and run the agent-state side-effects over the rows. That test must pass unmodified — if it needs editing, the design is wrong |
| **Reads as a FIX-04 revert** (`useChatroomSocket.ts:220-221`) | medium | a reviewer reverts a correct change | §7 states why `mergeMessages` is the additive merge FIX-04 wanted; the "does not drop older messages" test pins it |
| **Larger payload on every connect** — a page instead of a delta | certain | bandwidth, at ~1/120 s per socket until F-1 lands | The request *count* is unchanged. Accepted, and it disappears when F-1 lands. If it proves material, the page can be capped below `PAGE_SIZE` (`useChatroomMessages.ts:70`) |
| **Overlapping connect fetches on a flapping socket** | medium | stale window applied over fresher data | Generation guard, layer 1. This is exactly why `replayGeneration` exists (`:74-77`) |
| **The 422 fallback loops** — the retry's new anchor is also dead | low | repeated failing requests | Retry **once**, then set `hasOlderMessages = false` and surface the error state rather than retrying again. Pinned by the "does not repeat the same failing request" test |
| **The 422 fallback swallows genuine errors** | medium | a real outage looks like a cursor problem | Branch on the status code, not on any failure; test both arms |
| **Degraded mode retains the F-11 gap** | certain, by design | a degraded client stays append-only | Stated as a limitation in §6 rather than hidden. Recorded as FU-4 |
| **Migration on a large `approvals` table** | low | brief lock | A nullable column with no default and no index is a metadata-only change on PostgreSQL |
| **OpenAPI drift** — client not regenerated after layer 5 | low | build break | `pnpm run gen:api`; CI gates it via `check:openapi-drift` |
| **Merge conflict with the socket dossier** in `useChatroomSocket.ts`'s `onStatus` block | high if both land in the same window | rework | Q-1a: land this first; the other change in that region is a single call |

**What this does not fix**, stated plainly: paged-back `olderMessages` are still not revalidated on
reconnect (Q-3, FU-1); the degraded-mode poll is still append-only (FU-4); a frame lost with no
reconnect at all — the tab closes before the socket recovers — remains unrecoverable until the
generic pub/sub replay of the prompt-assistant dossier's FU-1; and pre-migration approvals are
invisible to the new endpoint for their remaining lifetime (Q-4, by decision).

**Rollback**, in five independently revertible commits, in this order:

1. **F-19 generation guard** — six lines, no dependencies. Safe alone in either direction.
2. **F-17 loading and error states** — self-contained across one composable, one view and two locale
   files. Safe alone.
3. **V-2 `before`-cursor fallback** — self-contained in `useChatroomMessages`. Safe alone; worth
   shipping early because it is the only defect here that is reachable with **no disconnect at all**
   (the retention purge).
4. **F-11 connect reconciliation** — the largest behavioural change and the one most likely to be
   rolled back. Reverting restores today's append-only connect path with no other loss.
5. **F-13 approvals read side** — backend migration + route, then the frontend resync. **Deploy
   backend before frontend; roll back frontend before backend**, or every reconnect 404s. The
   migration itself need not be reverted: a nullable unused column is inert.

## 10. Acceptance Criteria

- [ ] AC-1: `reconciles a message deleted while the socket was down` (§8) fails against current code
      and passes after the fix.
- [ ] AC-2: a message edited while the client was disconnected shows its new content and new
      `version` after reconnect, within the fetched window.
- [ ] AC-3: messages older than the fetched window are **not** dropped by reconnect reconciliation
      (Q-3's boundary, pinned by test).
- [ ] AC-4: `useChatroomSocket.test.ts:201-218` passes **unmodified** — the connect path still clears
      a stale per-agent error badge.
- [ ] AC-5: a 422 on the `before` cursor drops the dead anchor, refetches once, and never reissues
      the same failing request; a genuine transport failure still toasts.
- [ ] AC-6: two `message.updated` frames whose refetches resolve out of order leave the cache holding
      the newer content **and** the newer `version`.
- [ ] AC-7: the message feed renders a loading state, not the empty state, before the first fetch
      settles; a failed fetch renders a distinct error state with a retry; "Load earlier" does not
      render before the query settles.
- [ ] AC-8: an approval raised while the client was disconnected renders after reconnect, exactly
      once, and a subsequent `approval.resolved` transitions it.
- [ ] AC-9: overlapping connect resyncs cannot apply stale data — pinned by a generation-guard test
      for both the message reconciliation and the approvals resync.
- [ ] AC-10: the chatroom approvals endpoint is gated by room access, returns nothing for another
      room's approvals, and returns nothing (not an error) for pre-migration `NULL` rows.
- [ ] AC-11: all new user-facing strings go through `$t()` and exist in both
      `frontend/src/slices/conversation/locales/en.json` and `zh-TW.json`.
- [ ] AC-12: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .`, `alembic upgrade head`
      pass in `backend/`; `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build`,
      `pnpm run check:openapi-drift` pass in `frontend/`.

## 11. SRS Delta

None for the defects themselves — `[R24.23]` and `[R13.20]` already state the contract these fixes
restore.

One documentation correction is in scope and is **not** optional: the comment at
`frontend/src/slices/conversation/utils/mergeMessages.ts:9-11` states that deletions of
out-of-window messages "arrive via the `message.deleted` WS event". That is false on two paths — a
disconnect (F-11) and the retention purge, which publishes nothing
(`backend/contexts/conversation/application/retention_service.py:93-95`). Correct the comment to
describe what the code actually guarantees after this change. The purge's silence itself is out of
scope and is already recorded as FU-2 of the verification audit.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — Paged-back `olderMessages` (`useChatroomMessages.ts:71`) are never revalidated on
  reconnect (Q-3). Closing it needs either a bulk read-by-ids endpoint or a decision to discard the
  paged-back window on reconnect and accept the scroll-position cost. The V-2 fallback makes the
  current gap recoverable rather than fatal, which is why it is deferred.
- **FU-2** — Generic replay/cursor semantics on the pub/sub layer, tracked as FU-1 of
  `docs/tasks/2026-07-22-prompt-assistant-delivery-recovery/spec.md`. Per §5 it would subsume the
  delivery half of F-11 and F-13 and half of V-2, and none of F-19 or F-17. **This dossier is the
  third known consumer of that follow-up**, after the prompt-assistant defect and alongside these.
  Whoever picks it up should read that dossier's §6 channel survey first.
- **FU-3** — `useChatroomMessages.refreshOlderMessage` (`:319-330`) has F-19's exact shape with no
  guard. Cleared in §6 because it writes into a ref no reconnect path touches and fails by dropping
  rather than by writing stale data, but it is fragile and is the natural second consumer of F-19's
  guard.
- **FU-4** — The degraded-mode poll (`useChatroomSocket.ts:59-65`) calls `replayDelta` and therefore
  remains append-only, so a client that stays degraded keeps the F-11 gap for the duration. Left out
  because a full page every 10 seconds is a materially different cost profile from one page per
  connect; worth revisiting if degraded mode proves common.
- **FU-5** — The a2u audit's F-15 (the turn watchdog firing on healthy turns) and F-18 (sticking
  typing indicators) both live in `useChatroomSocket.ts` and are assigned to other dossiers
  (`findings.md:670,674`). Three dossiers editing one composable is a coordination hazard in its own
  right — the file has already grown from 417 to 473 lines across the socket-lifecycle fix and
  `d557752`'s approval reconcile since this was written — whoever schedules the remaining work
  should consider ordering it strictly rather than in parallel.
- **FU-6** — `docs/tasks/2026-07-22-prompt-assistant-delivery-recovery/spec.md` §6 marks
  `/chatroom/{id}` **cleared** as "the exemplar; has both halves". That is right for created
  messages and wrong for mutation (§6 above). The survey row should be narrowed once this dossier
  lands, so the two documents do not disagree.
- **FU-7** — `reconcileApprovals`/`reconcilePending` (`useChatroomSocket.ts:134-136`,
  `orchestration.ts:51-82`, added by `d557752`, 2026-07-23) has no generation guard, unlike every
  other async resync in this file (`replayGeneration`, `activationGeneration`, and this dossier's
  own F-19 guard). A flapping socket can overlap two `reconcilePending` passes; the store's
  `map[a.id]` re-check at `orchestration.ts:68` narrows but does not close the window, since a
  resolve can still land after a fresher one for the same id. Whoever builds Layer 5's list-based
  extension (§7) should add the guard then, rather than compounding a gap this dossier's own Q-7
  argues against leaving unguarded.
</content>
