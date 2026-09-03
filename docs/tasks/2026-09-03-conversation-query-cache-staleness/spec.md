---
type: bugfix
status: approved
created: 2026-09-03
requirements: [R13.16, R13.29, R28.09, R28.10]
depends_on: []
---

# Conversation slice query-cache staleness

## 1. Summary

Five defects in the conversation slice's TanStack Query cache let the client show data the
server has already changed, in each case with no error and no visible symptom that anything
is wrong. A user types `@AgentName` and the agent is never woken, because the map that
resolves names was fetched before the agent was bound. A manager grants a member group
access to a room, is told it saved, watches the checkbox flip back, clicks again, and the
second click deletes the grant on the server. A participant reconnects after a backend
deploy and never learns they are being observed, because the frame announcing it was
published while their socket was down and nothing re-reads the room on reconnect. A deleted
room stays in the sidebar rail and leads to a misleading error. A hard-deleted message stays
legible in scrollback.

This dossier consolidates all five findings of
`docs/audits/2026-09-03-conversation-query-cache-sweep/findings.md`, plus FU-7 and FU-11 of
`docs/tasks/2026-09-03-observer-ui-defect-sweep/spec.md`, which the audit re-verified as
still open and which share F-3's root cause. Two quality items approved at triage are folded
in: converting the four `setQueryData` sites in `useChatroomSocket.ts` to `convKeys.messages`,
and deleting the two dead factory entries. Executed in **three phases**, each a self-contained
milestone with its own tests, commit, and pass through the Definition of Done. See §7.1.

The audit is the source for every Observed row; it holds the full evidence and the
adversarial verification record, including the two candidates that were refuted.

## 2. Observed vs Expected

### Phase 1 — Room freshness: a write that changes the room is announced, and a reconnect re-reads it

| Finding | Observed | Expected | Intent source |
|---|---|---|---|
| F-3 | The reconnect handler (`useChatroomSocket.ts:621-642`) reconciles agent-thinking state, messages, presence, activation and approvals. `reconcileMessages()` is its only query-cache write and it writes only `convKeys.messages` (`:300-317`). `convKeys.chatroom` and `convKeys.chatroomAgents` are absent, and their only invalidator is the frame handler at `:418-423`, whose own comment at `:407-417` states the key "has no other invalidator". `useObservations.ts:198-226` adds a refetch of the observations query alone. Redis does not replay (`shared_kernel/realtime/pubsub.py:3-6`), so a `chatroom.updated` published during a socket gap is lost permanently. | A viewer who reconnects re-reads the room DTO and the agent roster, so a disclosure change made during the gap reaches them. | [R28.09], [R28.10]. RC-2 of `2026-09-03-observer-ui-defect-sweep/spec.md:164-171` names this as one of three conditions that each independently masked that dossier's F-1; phase 1 closed two of the three. |
| FU-7 | `patch_chatroom` gates its emit on `if fields & _DISCLOSURE_FIELDS` (`backend/app/api/v1/chatrooms.py:552`), and `_DISCLOSURE_FIELDS` is exactly `{"disclose_observers", "disclose_drafts"}` (`:73`). A rename or any access-flag patch publishes nothing at all, so every live viewer keeps a stale room name. | Every patch that changes a field a viewer can read announces itself. | `spec.md:979-985`; the comment at `chatrooms.py:553-556` states the defect and defers it. |
| FU-11 | `patch_chatroom_agent_activity_control` (`chatrooms.py:876-920`) ends at `await db.commit()` (`:920`) and publishes nothing, while its structurally identical sibling `patch_chatroom_agent_draft_access` emits at `:975-980`. A creator who grants activity control in one tab leaves another tab rendering the old grant. | The grant reaches the creator's other sessions. | `spec.md:1006-1018`. |

### Phase 2 — The name map and the two write races

| Finding | Observed | Expected | Intent source |
|---|---|---|---|
| F-1 | `['conversation','project-agents', projectId]` (`ChatroomView.vue:526-531`) has exactly one reference in all of `frontend/src` and no invalidator. It feeds `agentNames` (`:533-541`) → `agentList` (`:572-584`, name falling back to `agent_id.slice(0, 8)`) → `mentionAgents` (`:775-782`) → `resolveMentions` (`useChatroomMessages.ts:288`), which matches `agent.name` only (`utils/mentions.ts:36`) with no id fallback. The backend does not re-resolve: `triggers.py:84-115` filters the client's supplied ids and never parses the text. | An agent bound while a viewer is in the room is nameable by that viewer. | `utils/mentions.ts:4-6`, which states that resolution happens on the client "against the room's known agents". [R28.04] presupposes that mention resolution works. |
| F-2 | `boundQuery` (`ChatroomSettingsView.vue:121-125`) overrides nothing, so `staleTime` is 0 and focus refetch is on. `toggleGroup` (`:169-193`) writes the applied set with `setQueryData` (`:181`) and cancels nothing first; `cancelQueries` appears nowhere in `frontend/src`. `@tanstack/query-core@5.101.4` calls `this.setData(data)` unconditionally when a fetch resolves, with no comparison against an intervening manual write, so a GET issued **before** the PUT and resolved **after** it overwrites the applied set. The next click rebuilds `next` from the reverted value (`:174`) and the endpoint replaces, so it deletes the binding the first click added. `PUT /chatrooms/{id}/member-groups` (`chatrooms.py:623-658`) emits nothing, so no frame repairs it. | An optimistic write survives a refetch that was already in flight when it landed. | [R13.29]; the surface is explicitly treated as a room access control at `ChatroomSettingsView.vue:130-139`. |
| F-4 | `convKeys.chatrooms(wsId)` is `['conversation','chatrooms',wsId]` and `convKeys.recentChatrooms(pid)` is `['conversation','chatrooms','recent',pid]` (`queries/index.ts:16-18`); element [2] differs, so the first is not a prefix of the second. `ChatroomListView.vue:156,202` and `WorkspaceListView.vue:80,102` therefore miss the recent list, which only `useChatroomSettings.ts:187,208,317` reaches via the bare two-element prefix. This is the one key with no safety net: `staleTime: 60_000` (`useRecentChatrooms.ts:42`) in a sidebar that never unmounts, because `App.vue:67-77` does not key the layout. | Creating or deleting a room, or deleting a workspace, refreshes the recent rail from every surface that can do it. | `queries/index.ts:9-11`, which claims the nesting exists so a broad invalidation refreshes the recent list. The claim holds for the call sites it names; the defect is the asymmetry with the ones it does not. |

### Phase 3 — Scrollback reconcile and key-literal hygiene

| Finding | Observed | Expected | Intent source |
|---|---|---|---|
| F-5 | `olderMessages` (`useChatroomMessages.ts:85`) is rendered, not buffered: `:124` filters it against the recent cache and `:125` concatenates both into the visible feed. Its seven mutation sites (`:85,186,195,334,342,377,385`) include no reconnect hook, and `reconcileMessages` refetches only the latest page into the query cache. The dedup makes it worse: once the row leaves the cache, `recentIds` no longer contains it, so `:124` stops filtering it and the deleted row is restored into the render. `message_service.py:343` hard-deletes, so there is no tombstone to fall back on. | A message deleted during a socket gap does not survive the reconnect. | [R13.16]. |
| Key hygiene (quality, approved at triage; the audit's FU-1 and FU-4, the observer dossier's FU-4) | Six literals in `useChatroomSocket.ts` (`:124,137,281,310,380,401`) spell `['conversation','messages',roomId]` by hand rather than calling `convKeys.messages`, and the fourteen literals in `__tests__/useChatroomSocket.test.ts` duplicate the same string, so production and tests would drift together and the suite would stay green. `convKeys.search` (`queries/index.ts:26-27`) and `convKeys.export` (`:28`) have zero readers and zero writers. | The factory is the single definition of every key, and a dead entry does not sit in it looking live. | `queries/index.ts:20-24`, written about this exact class of bug. |

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which findings does this dossier cover? | All five, plus FU-7 and FU-11 of the observer dossier, plus the two approved quality items. | The user's explicit triage. F-3, FU-7 and FU-11 are one root cause and the observer dossier already recorded that the latter two are "worth closing together, with the invalidation-storm question FU-7 raises answered once for both" (`spec.md:1015-1017`). Splitting them would answer that question twice. |
| Q-2 | Does widening `patch_chatroom`'s emit break Q-9's audience split? | No. Widen to `if fields:` with `room_visible=True`, unchanged. | Verified field by field against `_to_out` (`chatrooms.py:216-258`). All eight fields `ChatroomPatchIn` accepts (`:92-105`) are serialized into the DTO a non-creator reads **and** into a pure guest's, since `_to_out:234-238` copies the five access flags with no viewer conditioning and `ChatroomOut:112-116` declares them non-optional. The only four viewer-conditioned fields are `created_by_user_id`, `disclose_observers`, `observers_present` and `is_moderator`, none of them patchable access flags. So there is no field for which a frame would be followed by an unchanged DTO, which is the precondition of Q-9's oracle. `version` (`_to_out:239`) moves on every successful patch besides, so the precondition can never hold here. An empty-body `PATCH {}` leaves `fields` falsy and stays silent, which is the same truthiness `:505` already relies on. |
| Q-3 | What `room_visible` value does FU-11's emit take? | `False`, unconditionally, with a comment pointing at this row. | The opposite of Q-2. `list_chatroom_agents` serialises `may_control_activities` and `activity_type_allowlist` as `None` for a non-creator (`chatrooms.py:752-753`) under `response_model_exclude_none=True` (`:721`), so the fields are dropped from the response entirely, and neither appears in `ChatroomOut`. After this grant a non-creator re-reads an unchanged DTO **and** an unchanged listing, which is exactly the "an invisible write happened" signature Q-9 forbids. Chosen over mirroring the draft-access site's conditional shape (`:975-980`): that one is conditional because the grant moves `drafts_readable`, which is on the DTO for everyone; activity control has no analogous DTO field and no analogous disclosure flag, so a conditional would be a constant wearing a disguise. Chosen over inventing a `disclose_activity_control` flag to restore symmetry, which is a new product surface, not a bugfix, and nothing in the SRS asks for it. |
| Q-4 | Is FU-7's invalidation-storm concern real? | No. Recorded and dismissed with the measurement. | `useChatroomSettings.ts` sends one PATCH per user action, never a batch: `onSave` (`:181-194`) patches the name alone and says so at `:177-180`; `patchFlag` (`:200-221`) is one call per invocation; `setFlag` (`:240-272`) ends every branch in exactly one `patchFlag`, three of them carrying two fields in **one** request to keep [R13.04]'s exclusive pair atomic. All are re-entrancy-guarded (`:182`, `:203`). Amplification is therefore 1 frame per action, independent of write count. Per live non-creator viewer that is 2 GETs after phase 1 and 3 after phase 2, since all the invalidated keys are active queries on `ChatroomView`. **The creator pays more, not the same**: both the room-channel and the user-channel copies arrive, and the second invalidation does not coalesce with the first — `invalidateQueries` defaults `cancelRefetch` to true (`queryClient.js:165`), so `query.js:187-189` aborts the first refetch and starts another. The creator therefore issues up to twice the key count, with the first response of each pair discarded: up to 4 GETs after phase 1 and 6 after phase 2. Still bounded by viewer count rather than write count, which is the claim FU-7's concern needed tested, but the number is not the one a naive reading gives. |
| Q-5 | How is F-1 fixed, given the key is project-scoped and the event is room-scoped? | Invalidate the bare prefix `['conversation','project-agents']` from both `chatroom.updated` handlers. Add a documented prefix-only factory entry for it. | `useChatroomSocket(roomId)` (`:49`) has no project id and cannot get one from the frame, whose payload is `{"chatroom_id": ...}` by construction (`chatrooms.py:330`) and must stay that way. Threading an accessor from `ChatroomView` (`workspaceQuery.data.value?.project_id`, `:514-519`) would work but must be an accessor rather than a value, because the workspace read resolves after setup, and it would have to be threaded twice. The prefix needs no threading, and it additionally matches the transient `['conversation','project-agents', undefined]` entry the computed key produces before the workspace read lands, which an exact key would not. Over-invalidation is bounded by TanStack's default `refetchType: 'active'` and by only one `ChatroomView` being mounted at a time; the refetch is `listProjectAgentNames`, the cheap projection FU-12 of the observer dossier introduced for exactly this path. |
| Q-6 | Why does F-1's fix land in two files? | Both `chatroom.updated` handlers must invalidate: `useChatroomSocket.ts:418-423` (room channel) and `useObservations.ts:337-341` (creator's user channel). | The backend splits delivery deliberately (`chatrooms.py:313-317,331-333`). For an observer binding in a room with disclosure off, `room_visible` is false (`:813-818`), so the creator's user channel is the **only** delivery. A fix landing in the socket handler alone leaves the creator's other tabs stale, which is the case F-1 is most likely to be noticed in. |
| Q-7 | Can F-1 also fix the rename case? | No, and the dossier says so rather than implying parity. | `backend/app/api/v1/agents.py` contains no `Publisher`, `publish` or `emit` at all, and `agent_service.py`'s `create` and `patch` call `audit.emit` only. No event exists for an agent create or rename, so a renamed agent's stale name is not repairable client-side. The reachable arm is "bound mid-session", where the id is absent from the map. Recorded as FU-1. The comment at `ChatroomView.vue:784-786` claims its own precedent handles "new authors (and renames)" while the guard at `:797` is an absence test, so it has the identical hole; §7.4 corrects that comment rather than copying its claim. |
| Q-8 | How is F-2 fixed, given the codebase has no `cancelQueries` and no `onMutate` anywhere? | **Both**: `await qc.cancelQueries({ queryKey })` before the PUT **and** `void qc.invalidateQueries({ queryKey })` after the `setQueryData`. Neither alone is sufficient. | The two halves close different windows, which is why the first draft of this row was wrong to present them as alternatives. **Cancel-before** kills a fetch already in flight when the click happens, which is the ordering §4's repro produces. It cannot help against a fetch that *starts* after it: `toggleGroup` disables the checkbox (`savingGroups`) but not `refetchOnWindowFocus`, so a focus refetch issued while the PUT is in flight reads pre-PUT state, resolves after `:181`, and reverts the checkbox — F-2 reproduced against a cancel-only fix. **Invalidate-after** closes that one, and it genuinely aborts rather than merely racing: verified in `@tanstack/query-core@5.101.4` that `invalidateQueries` funnels into `refetchQueries` with `cancelRefetch: options.cancelRefetch ?? true` (`queryClient.js:156,165`), and `query.js:187-189` then calls `this.cancel({silent: true})` on the in-flight fetch whenever `state.data !== undefined`, which holds here because the picker only renders once `isSuccess`. It cannot replace cancel-before either, because between the click and the invalidate there is still a window in which a pre-PUT response can land and a second click can rebuild `next` from it. Rejected: **the house hand-rolled style** (a generation counter, as at `useChatroomSocket.ts:95-103`) cannot work at all, because the racing write is TanStack's own internal `setData` on fetch resolution, which no application-level guard can intercept; and **disabling focus refetch** would trade this defect for a staler picker on a room access control. |
| Q-9 | How is F-5 repaired, and where is it wired? | Re-fetch the paged-in range on reconnect and merge through `mergeMessages`; wire it as an `onReconnect` callback passed into `useChatroomSocket`. | Chosen over dropping `olderMessages` to `[]`, which is one line and zero requests but collapses the feed's `scrollHeight` with nothing calling the scroll-preserve hooks (`useChatroomScroll.ts:117-130` are wired only around `loadEarlier`), jumping the reader's viewport and leaving the load sentinel near the top where it can immediately re-page the rows just dropped. Chosen over a per-id refetch, which is N requests and, worse, a behaviour regression: `refreshOlderMessage`'s catch arm (`:386-390`) drops the row on **any** error and cannot tell a 404 from a transient 5xx, so running it over N rows at reconnect (precisely when the network is unreliable) would silently delete visible history. The range repair detects deletions for free through `mergeMessages`' window semantics (`utils/mergeMessages.ts:8-15,17-44`), the same instrument `reconcileMessages` already uses, and keeps ids stable so the viewport does not move. The `onReconnect` seam is chosen over a view-owned `onStatus` subscription because both reconciles write history into the same rendered union and only the callback shape *can* sequence them; it also lets the new pass share `replayGeneration`. Note "can" is doing real work there: the existing burst fires everything un-awaited, so the seam makes the ordering possible without supplying it, and §7.4 constraint 3 is what actually requires it. |
| Q-10 | Should the sweep extend beyond the conversation slice? | No. | The slice holds 17 of the frontend's 25 hand-written key literals. The other eleven slices contribute eight sites, five of them deliberate bare prefixes of the kind Q-11 describes, and none is a confirmed defect. Recorded as FU-3 of the audit and routed to `check-quality`. |
| Q-11 | Should `convKeys` gain zero-argument prefix entries? | Yes, for `['conversation','chatrooms']` and `['conversation','project-agents']`, each documented as prefix-only. | No type-level change is needed: nothing consumes `convKeys` generically (no `keyof typeof`, no index access anywhere in `frontend/`), all ten entries already return `as const` tuples, and `invalidateQueries` accepts a `readonly unknown[]`. The argument for adding them is `queries/index.ts:20-22` itself, written about this exact class of bug: without an entry, this dossier leaves six hand-written literals naming the `chatrooms` prefix. The risk to guard is that a later reader passes a prefix entry to `useQuery` or `setQueryData` and creates a real third cache entry no `queryFn` feeds, so each entry carries a comment saying it is for invalidation only. |
| Q-12 | Does this depend on any active dossier? | No. `depends_on: []`. | Only two dossiers under `docs/tasks/` are not `implemented`/`superseded`/`abandoned`: `2026-07-07-graphrag-two-axis-redesign` (a blueprint for the graphrag axis, naming none of these files) and `2026-07-19-large-artifacts-silently-dropped` (working on `turn_engine.py`'s `_persist_artifacts` and kernel descriptor path; this dossier's only backend file is `app/api/v1/chatrooms.py`). No logical and no overlap prerequisite. |
| Q-13 | What is the branch base? | `fix/observer-ui-sweep-phase3`, not `main`. | PRs #182 ← #183 ← #184 are all still open and unmerged as of 2026-09-03, verified with `gh pr list`. `main` has none of them, and phases 1 to 3 rewrote large parts of `useChatroomSocket.ts`, `ChatroomView.vue` and `useObservations.ts` — the files this dossier edits. Every line citation here was read at that branch. If those PRs merge before this starts, rebase onto `main` and re-verify §2 before touching anything. |

## 4. Reproduction

Preconditions common to all: a project with at least one workspace, one chatroom, and an
agent bound to it.

**F-1.** Participant B opens room R and keeps the tab focused. In another session, create a
new agent and bind it to R as a normal-role agent. B's rail shows the new agent labelled with
an eight-character hex fragment. B types `@<the agent's real name>` and sends. No agent turn
starts. Typing `@<the hex fragment>` instead does wake it.

**F-2.** As a manager on a project with at least two member groups, open Chatroom Settings for
a room with `allow_member_groups` on. Switch to another browser tab and back, which starts a
focus refetch of the bound-groups query, then click a group checkbox within a second or so.
The success toast appears and the checkbox reverts. Click it again: the room ends with the
group unbound. Throttle the network to widen the window if it does not reproduce first try.

**F-3.** Participant B sits in room R with the tab focused. Restart the backend (or otherwise
drop only the WebSocket while HTTP stays reachable, which is what an nginx 502 on the ticket
fetch produces). While B's socket is down, the creator binds an observer to R with disclosure
on. B's socket reconnects. No disclosure chip appears, and none appears for as long as B does
not blur the window.

**F-4.** Open a workspace's chatroom list with the sidebar visible. Delete a room from the
list. The table updates; the sidebar recent rail still shows the room. Click it: the header
falls back to an id fragment and the message area renders a generic load-failure alert with a
Retry button. Symmetrically, creating a room from this page does not add it to the rail.

**F-5.** Not reliably reproducible by hand; it needs a delete inside a socket gap. Scroll back
past the first page in a room, drop the socket (devtools offline is the wrong instrument here
because it also arms `useNetworkStatus`'s blanket invalidation — kill the WebSocket alone),
have a moderator delete one of the paged-in messages, then restore the socket. The deleted
message is still rendered.

**FU-7.** Two tabs on room R. Rename R from Settings in tab A. Tab B's header keeps the old
name until the window is blurred and refocused.

**FU-11.** Two tabs, both the creator's. Grant activity control to an agent in tab A. Tab B's
roster keeps the old grant state until reload.

## 5. Root Cause Analysis

Three root causes account for all seven items, and the phases are organised around them.

**RC-1 — A room write that changes what a viewer reads is not always announced, and a
reconnect does not compensate.** F-3, FU-7 and FU-11 are one defect seen from three angles.
The observer dossier's phase 1 established the mechanism (`chatroom.updated`) and wired it to
two keys, but scoped the emit to the fields that move `observers_present` and `drafts_readable`
(`chatrooms.py:552`), leaving rename, access flags and activity-control grants silent. It also
chose the event *instead of* a reconnect invalidation rather than in addition to it
(`spec.md:273-274` weighs them as alternatives, correctly for the question it was answering),
so a frame lost to a socket gap has no second delivery and Redis does not replay it
(`pubsub.py:3-6`). The earliest link whose correction prevents all three symptoms is the pair:
announce every visible write, and re-read on reconnect so a missed announcement is survivable.
Neither half alone is sufficient, which is why they are one phase.

**RC-2 — A cache key with no invalidator is indistinguishable from working code.** F-1 and F-4
are two instances. This is the audit's own RC-2 restated one sweep later, and both instances
share the shape the previous audit described: a key that looks right at its one call site.
F-1's key has exactly one reference in the frontend; F-4's is reached from three call sites and
missed by four. What makes F-4 the more serious of the two despite the milder symptom is that
it is the only key in the slice with no safety net, because `staleTime: 60_000` in a
never-unmounting sidebar defeats both the focus refetch and the mount refetch that downgrade
every other orphan in this slice to a delay.

**RC-3 — State that lives outside the query cache does not participate in any of the cache's
repair mechanisms.** F-2 and F-5 are the two instances, in opposite directions. F-2 writes into
the cache and loses to the cache's own refetch, because the write was never sequenced against
it. F-5 keeps rendered state in a plain `ref` that no invalidation, no focus refetch and no
reconnect reconcile can reach. Both are cases where a mechanism that works for everything else
in the slice does not apply, and nothing marks the boundary.

Aggravating rather than causal: the global client sets no `staleTime` and no
`refetchOnWindowFocus` override (`shared/query-client.ts:4-21`, installed unmodified at
`app/main.ts:56`), so most orphans self-heal on a blur and refocus. That is why every finding
here presents as an unbounded delay for a focused viewer rather than as permanent staleness,
and why F-4, which opts out of that net, is the one whose symptom persists.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** F-1 affects every viewer in a room when an agent is bound by someone else,
and its harm is a silently dropped user action. F-2 affects managers on projects using member
groups, and [R13.29] makes the dropped write a silent revocation of room access. F-3 affects
participants and guests in observed rooms, on every backend deploy that coincides with a
binding or disclosure change. F-4 affects every user on an ordinary navigation surface. F-5 is
the narrowest, needing scrollback plus a gap plus a concurrent delete. FU-7 and FU-11 affect
anyone with a second tab open. **No finding writes bad data except F-2**, whose second click
issues a real `PUT` that removes a binding; §7.5 records why no data repair is required
nonetheless.

**Sibling suspects.**

- **Other keys in `convKeys` with an incomplete invalidator** → **swept, five clean.**
  `workspaces`, `workspace`, `chatrooms`, `messages` and `observations` were each traced to
  their server-side change paths and cleared, with reasons recorded in the audit's §3. Two
  deserve their reasoning repeated because the obvious reading is wrong: `workspace` is clean
  because **no workspace rename API exists** (`api/index.ts` exposes only list, create, get,
  delete and `setWorkspaceConceptMapEnabled`) and its one mutable field renders in the sole
  component that invalidates it, not because any invalidation reaches it from the plural; and
  `messages` is clean partly because `mergeMessages` is a windowed replace rather than the pure
  append its call-site comments describe.
- **Other hand-written key literals that have drifted from the factory** → **none.** All
  thirteen factory-duplicating literals in the slice are byte-identical to the factory's output
  today, and no invalidation in the slice currently matches nothing. The key-literal hazard is
  therefore a maintenance question rather than a live defect, which is why only its sharpest
  edge is in scope here (§7.4) and the rest routes to `check-quality` as FU-5.
- **Other optimistic cache writes with no cancellation (F-2's pattern)** → **confirmed as the
  only one.** `cancelQueries` and `onMutate` appear nowhere in `frontend/src`; every optimistic
  mutation in the repo is hand-rolled around `setQueryData` with an explicit rollback. The
  others are safe for reasons that do not apply to F-2: `useChatroomMessages.ts:265-315` writes
  to a separate `pendingMessages` ref rather than into a query's own entry;
  `useObservations.ts:359-396` invalidates after patching; `useKeyGroups.ts:54-110` shadows the
  query with a parallel ref. F-2 is the only site that writes directly into a focus-refetchable
  query's entry and then leaves.
- **Other state rendered from a plain ref rather than the cache (F-5's pattern)** →
  **confirmed and cleared, one each.** `olderMessages` is confirmed and in scope.
  `useChatroomBindings`' binding refs looked identical and were **refuted**: every write there
  is a per-agent delta followed by an unconditional `loadBindings()`, so a stale ref cannot
  clobber another session's change and the panel self-heals on the operator's next action. That
  remains the structural smell recorded as FU-3 of the observer dossier and routed to
  `check-quality`.
- **Other backend room writes that publish nothing** → **two confirmed, one deliberate.**
  `patch_chatroom`'s non-disclosure path (FU-7) and `patch_chatroom_agent_activity_control`
  (FU-11) are in scope. `PUT /chatrooms/{id}/member-groups` (`chatrooms.py:623-658`) also
  publishes nothing; it is **deliberately left alone**, because F-2's fix makes the writing tab
  correct and a cross-tab member-group frame would be a new audience-split analysis of exactly
  the kind Q-3 had to do. Recorded as FU-2.
- **Other consumers of the `chatroom.updated` frame** → **two, both in scope.**
  `useChatroomSocket.ts:418-423` and `useObservations.ts:337-341`. Q-6 records why both must
  change together.

## 7. Fix Design

### 7.1 The phase contract

Three phases, each ending in a commit that passes the full Definition of Done. Phases are
serial: P1 and P3 both edit the reconnect block of `useChatroomSocket.ts`, and P2 and P3 both
edit `queries/index.ts`.

| Phase | Items | Files (primary) | Ordering |
|---|---|---|---|
| P1 — Room freshness | F-3, FU-7, FU-11 | `backend/app/api/v1/chatrooms.py`, `backend/tests/unit/test_observer_agents.py`, `useChatroomSocket.ts` | First. |
| P2 — Name map and write races | F-1, F-2, F-4 | `queries/index.ts`, `useChatroomSocket.ts`, `useObservations.ts`, `ChatroomView.vue`, `ChatroomSettingsView.vue`, `ChatroomListView.vue`, `WorkspaceListView.vue`, `frontend/tests/mocks/handlers.ts` | After P1. |
| P3 — Scrollback reconcile and key hygiene | F-5, the six `messages` key literals, dead entries | `useChatroomMessages.ts`, `useChatroomSocket.ts`, `ChatroomView.vue`, `queries/index.ts`, `__tests__/useChatroomSocket.test.ts` | Last. |

### 7.2 Phase 1

**FU-7.** Replace `if fields & _DISCLOSURE_FIELDS:` with `if fields:` at `chatrooms.py:552`,
keeping `room_visible=True` at `:572`. Q-2 establishes this is safe for every field. The
comment block at `:553-568` argues for the narrow gate and names FU-7 at `:555-556`; it must be
rewritten, not merely edited around, and the rewrite should state the field-visibility fact
Q-2 established rather than restate the old argument. `_DISCLOSURE_FIELDS` stays: `:505` and
`:524` still use it for the capability gate and the per-field creator gate, which are unrelated
to the emit.

**FU-11.** Add the emit to `patch_chatroom_agent_activity_control`, with
`room_visible=False` and a comment pointing at Q-3 so a later reader does not read the constant
as an oversight. Two mechanical constraints: `_emit_chatroom_updated` commits internally at
`:329`, so the existing `await db.commit()` at `:920` must be **replaced by** the emit call, not
kept beside it, or `test_creator_may_grant_activity_control`'s `db.commit.assert_awaited_once()`
(`test_observer_agents.py:237`) breaks. And `access.chatroom.created_by_user_id` is already in
hand from `:898`, so this costs no extra query.

**F-3.** Add `convKeys.chatroom(roomId)` and `convKeys.chatroomAgents(roomId)` to the
reconnect block at `useChatroomSocket.ts:628-641`, alongside the four reconciles already there.
This is the second delivery the event mechanism lacks, not a replacement for it: the event
remains the fast path on a healthy connection, and the reconnect invalidation is what makes a
lost frame survivable. It does not correct the symptom to invalidate only on reconnect, which
is what `spec.md:273-274` established and is still true; the change here is that both now
exist.

### 7.3 Phase 2

**F-1.** Add a prefix-only factory entry for `['conversation','project-agents']` (Q-11) and
invalidate it from both `chatroom.updated` handlers (Q-6): `useChatroomSocket.ts:418-423` and
`useObservations.ts:337-341`. No project id is threaded (Q-5). Then correct the overstated
comment at `ChatroomView.vue:784-786` so it describes the absence test the code actually
performs, following the house convention for a comment whose claim was wrong
(`useChatroomSettings.ts:44-51` rewrote such a comment in place rather than deleting it).

**F-2.** In `toggleGroup` (`ChatroomSettingsView.vue:169-193`), add
`await qc.cancelQueries({ queryKey: <the bound-groups key> })` immediately before the
`setChatroomMemberGroups` call, keep the `setQueryData` at `:181` unchanged, and add
`void qc.invalidateQueries({ queryKey: <the same key> })` immediately after it. Q-8 records
why **both** are required and why neither alone closes the defect: the cancel handles a fetch
already in flight at click time, the invalidate handles one that starts during the PUT, and
the invalidate aborts rather than races because `invalidateQueries` defaults `cancelRefetch`
to true. Separately, move the doc block currently at `:155-163` onto
`toggleGroup` at `:169`, where it belongs: it describes `toggleGroup`'s read-back contract and
sits above `reloadGroups`, which does the opposite. Its final sentence ("no refetch has to be
raced") becomes true only once the cancel is in place, so correcting the placement and the fix
belong in one change.

**F-4.** The two view pairs need **different** operations, and conflating them would regress
the workspace list.

- `ChatroomListView.vue:156,202` currently name `convKeys.chatrooms(workspaceId)`, which is a
  strict extension of the prefix, so these two are **widened** to the new `chatrooms` prefix
  entry (Q-11). Nothing is lost: the prefix still matches the view's own list.
- `WorkspaceListView.vue:80,102` currently name `convKeys.workspaces(projectId)`, which the
  `['conversation','chatrooms']` prefix does **not** match. These two get an **added** second
  invalidation and keep the existing one. Replacing it would break the workspace list itself:
  a deleted workspace's row would stay in the table and a created one would be missing from it
  until a blur and refocus, which is a worse defect than the one being fixed.

Also convert the three existing bare literals at `useChatroomSettings.ts:187,208,317` to the
new prefix entry so no hand-written spelling of it survives. `WorkspaceListView.vue:80` is
included because `createWorkspace` returns a
`default_chatroom_id` (`api/index.ts:50-56`), so a new workspace immediately contributes a room
to the rail; fixing `:102` alone would leave the inconsistency. The prefix is the right
instrument at the workspace sites rather than a targeted `recentChatrooms(pid)`, because
`WorkspaceListView` derives its project id from `route.params` (`:46`) while the rail's query is
keyed from the persisted `useWorkspaceStore().projectId` (`SidebarChatroomList.vue:13-16`), and
the two can disagree.

### 7.4 Phase 3

**F-5.** Give `useChatroomMessages` a `reconcileOlder()` export that re-reads the paged-in
range and merges through `mergeMessages`, and pass it into `useChatroomSocket` as an
`onReconnect` callback so it joins the ordered burst at `:628-641` (Q-9). Five constraints the
implementation must respect; the last three are where a plausible implementation goes wrong.

1. It is multi-request (`⌈N/200⌉` sequential pages, since `messages.py:149` caps `limit` at 200
   against a `PAGE_SIZE` of 100) and each page's anchor comes from the previous response, so it
   must be guarded by a monotonic generation counter in the file's existing style
   (`useChatroomSocket.ts:95-103`) or a flapping socket will interleave two passes.
2. It must not touch `hasOlderMessages` or the view-local `autoLoadExhausted` latch
   (`ChatroomView.vue:1217`). The range is unchanged in extent, and re-opening pagination is the
   drop strategy's problem, not this one.
3. **The reconnect burst must await it in order.** `:628-641` fires everything un-awaited
   (`void reconcileMessages()` at `:634`), so merely adding a callback there does **not**
   sequence it after the recent-page repair, which Q-9 claims is the whole reason for choosing
   this seam. The two passes write overlapping history into the same rendered union, so
   `reconcileOlder` must run after `reconcileMessages` has resolved, not merely after it was
   started.
4. **The first `before` anchor must not come from `olderMessages` itself.** Its newest row is
   stale client state and may be the very message deleted during the gap, in which case the
   request 422s on a dead anchor and the reconcile repairs nothing — in exactly the scenario
   the fix exists for. `useChatroomMessages.ts:161-169` documents this failure mode for
   `loadEarlier` and carries a one-shot retry for it (`:187-202`); this pass needs the same
   defence, or an anchor taken from the recent page the just-completed `reconcileMessages`
   refetched, which the server has confirmed.
5. **`mergeMessages` alone does not detect a deletion at the bottom boundary.**
   `mergeMessages.ts:21-26` derives `windowStart` from the oldest row **in the response**, and
   `:31` keeps any prior row older than that. If the deleted message is the oldest row of the
   paged-in range, the final page simply omits it, `windowStart` moves up to the next-oldest
   surviving row, and the deleted row is preserved rather than dropped; a page returning zero
   rows leaves `windowStart` null and drops nothing at all. The reconcile must therefore bound
   its own window explicitly by the range it requested rather than by what came back, and drop
   prior rows inside that range that the server did not return. T-9 must cover this case
   specifically, or it passes while the boundary stays broken.

**The `messages` key literals** (the audit's FU-1; the observer dossier calls the same hazard
its FU-4). Convert all six production literals in `useChatroomSocket.ts` to
`convKeys.messages(roomId)`: the two invalidation targets at `:124` and `:137`, and the four
write sites, whose literals are at `:281` and `:310` (the `const key = ...` lines, not the
`setQueryData(` calls a line below each) and inline at `:380` and `:401`. The seventh literal,
`messagesKey` at `:699`, is **deliberately excluded** and stays with FU-5: it is a comparison
rather than a key, it matches on indices 0 to 2 without checking length, and fixing it properly
means adding that length check, which is a behaviour change rather than a rename.

**Convert the fourteen literals in `__tests__/useChatroomSocket.test.ts` in the same change.**
Converting production alone leaves the blindness intact: the tests hand-write the same string,
so they would drift with a future factory change and the suite would stay green while the
message feed stopped receiving every append, edit and delete. The tests are the half that makes
this fix worth making.

**Dead entries.** Delete `convKeys.search` (`queries/index.ts:26-27`) and `convKeys.export`
(`:28`), and the matching `['conversation','search', ...]` line from the header comment at `:7`.
Both have zero readers and zero writers; the real implementations never went through TanStack
(`useChatroomSearch.ts:17` and `useChatroomExport.ts:23` resolve into plain refs).

### 7.5 Data repair plan

**None required.** F-2 is the only finding whose second click writes to the server, and what it
writes is a valid replacement set that simply omits a binding the user intended to add. The
resulting state is indistinguishable from the user having never added it, so there is nothing to
detect and nothing to repair: an affected manager re-adds the group, which after this fix
persists. No migration, no backfill.

## 8. Regression Test Plan

Every phase is test-first: the listed test is written, observed failing against current code,
and only then is the fix applied. Exceptions are marked as guards.

### Phase 1

| Test | File | Asserts | Fails today because |
|---|---|---|---|
| T-1 | `backend/tests/unit/test_observer_agents.py` | A rename patch emits an ids-only `chatroom.updated` on the room channel. This **inverts** the existing `test_non_disclosure_patch_emits_nothing` (`:1157-1180`), which was written as FU-7's placeholder and says so in its docstring at `:1158-1160`. Rename the test with the assertion. | The emit is gated on the disclosure fields. |
| T-2 | `test_observer_agents.py` | An activity-control grant emits `chatroom.updated` on the creator's user channel and **nothing** on the room channel, via `_assert_creator_only` (`:797-809`). | The route publishes nothing. |
| T-2a | `test_observer_agents.py` | **Guard, and it must be written first.** `_wire_grant_route` (`:181-211`) does not install `_spy_room_publisher`, so the seven tests using it construct the real `Publisher`, whose failure `_emit_chatroom_updated` swallows in its `try/except` (`chatrooms.py:335-343`). T-2 would therefore pass vacuously against a route that emits nothing. Add the spy to that helper and assert the existing grant tests still pass, before T-2 is written. | Not a failing-first arm; it is what makes T-2 able to fail. |
| T-3 | `frontend/src/slices/conversation/__tests__/useChatroomSocket.test.ts` | An `onStatus(true)` reconnect invalidates `convKeys.chatroom` and `convKeys.chatroomAgents`. Drive it the way the file already does (`statusHandlers.forEach((h) => h(true))`, e.g. `:696`) and spy via `mountSocket()`'s returned `qc` (`:108-128`). Note the `chatroom.updated` describe at `:1203` does not call `vi.useFakeTimers()`, unlike the streaming describe. | The reconnect block touches neither key. |

### Phase 2

| Test | File | Asserts | Fails today because |
|---|---|---|---|
| T-4 | `useChatroomSocket.test.ts` | A `chatroom.updated` frame for the current room invalidates the `project-agents` prefix; a frame for another room invalidates nothing. | The handler invalidates two keys and not this one. |
| T-5 | `__tests__/useObservations.test.ts` | The same, on the user-channel handler, via `mountObs()`'s returned `qc` (`:101-145`). | Same. |
| T-6 | `__tests__/ChatroomSettingsView.test.ts` | **Two cases, one per window.** (a) A bound-groups GET issued *before* the toggle and resolved after the PUT does not revert the checkbox. (b) A GET issued *while the PUT is in flight* and resolved after it does not revert it either. Use `deferred()` (`frontend/tests/utils/deferred.ts:9-17`, documented at `:1-2` as being for exactly this ordering), and **bind `seededClient(...)` to a local** — every current call passes it inline (`:401-405`, `:450-454`) so no test in that file can reach the client. | (a) nothing cancels the in-flight fetch and query-core's `setData` on resolution is unconditional; (b) the same, and the cancel-before half of the fix cannot reach a fetch that has not started yet. |
| T-7 | `__tests__/ChatroomListView.test.ts` | Create and delete each invalidate a key that prefix-matches `convKeys.recentChatrooms`. | Both name `convKeys.chatrooms(workspaceId)`, whose element [2] differs. |
| T-8 | `__tests__/WorkspaceListView.test.ts` | Workspace create and delete each invalidate a key that prefix-matches `convKeys.recentChatrooms`, **and still invalidate `convKeys.workspaces(projectId)`.** The second assertion is what stops the fix being applied as a replacement rather than an addition (§7.3). | Neither invalidates anything matching the recent list. |

T-7 and T-8 need a mount that exposes the query client: both files are render-smoke only today
(two tests each) and pass no `queryClient` to `renderView`, so the default client from
`frontend/tests/utils/render.ts:31-33` is used and is unreachable. Build a local client in the
test rather than changing `renderView`'s signature, matching `ChatroomSettingsView.test.ts`'s
`seededClient` shape.

A default msw handler for `GET /chatrooms/:id/member-groups` and
`GET /projects/:id/member-groups` must be added for T-6; neither is registered today, so both
fall to the catch-all 404 at `frontend/tests/mocks/handlers.ts:385-388`. Insert them **above**
that catch-all, since msw resolves the first matching handler and the catch-all must stay last.

### Phase 3

| Test | File | Asserts | Fails today because |
|---|---|---|---|
| T-9 | `__tests__/useChatroomMessages.test.ts` | **Three cases.** After a reconnect: (a) a paged-in message the server no longer returns is removed from the rendered feed; (b) one it still returns survives with its updated content; (c) **the same as (a) when the deleted row is the oldest row of the paged-in range** — the boundary `mergeMessages` alone cannot detect, per §7.4 constraint 5. Without (c) the phase can ship with the bottom boundary broken and T-9 still green. | Nothing reconciles `olderMessages`, and the dedup at `:124` restores the row once it leaves the cache. |
| T-10 | `useChatroomMessages.test.ts` | **Guard.** A second reconnect while the first reconcile is in flight does not apply the older pass twice, and the later pass wins. Pins the generation counter. | Passes in both directions by design; it protects the fix rather than proving the defect. |
| T-11 | `useChatroomSocket.test.ts` | **Guard.** The fourteen hand-written `['conversation','messages',ROOM]` literals are replaced by `convKeys.messages(ROOM)`, so a future factory change turns the suite red instead of leaving it green. Assert by construction: the file imports `convKeys` already (`:84`). | Not a failing-first arm. It is the half of FU-4's fix that has any effect. |

## 9. Risks and Rollback

**FU-7 widens what the room channel announces, and that is the change with a delivery
surface (P1).** Q-2 establishes there is no field for which the frame is a pure signal, so the
Q-9 oracle does not reopen. The residual is volume: a rename now costs two GETs per live
viewer. Q-4 measures the amplification at one frame per user action, so this is bounded by
viewer count, not by write count. T-1 constrains the payload to ids only by asserting
**equality** rather than the id's presence, so a later field addition fails the test rather than
passing it.

**FU-11's `room_visible=False` means this route never uses the room channel (P1).** A later
reader will ask why the parameter is a constant, which is the same question `spec.md:1023-1024`
records for `patch_chatroom_agent_role`. The comment required by §7.2 is the mitigation, and it
must state the reason (the fields are absent from a non-creator's listing) rather than merely
that the value is deliberate.

**F-3's reconnect invalidation runs on every reconnect, including healthy flaps (P1).** Two
extra GETs per reconnect per viewer, on top of the four reconciles already there. A flapping
socket therefore costs more than it did. This is accepted for the same reason the existing four
are: a reconnect is the one moment the client knows its state may be wrong, and the alternative
is leaving the frame's loss undetectable.

**F-2 introduces `cancelQueries` to a codebase that has never used it (P2).** The risk is
cancelling more than intended if the key is written loosely; the mitigation is that the key is
exact, not a prefix. A cancelled fetch is not an error path in TanStack, so no error handling
changes. The added invalidate costs one extra GET per successful toggle, which is the price of
closing the second window; Q-8 records why neither half alone suffices. Note that the invalidate
also aborts an in-flight fetch, so the two halves do not double-cancel anything — they apply at
different moments.

**F-4's widened invalidations cost one extra query on the list screens (P2).** The over-match is
bounded by TanStack's default `refetchType: 'active'`, so other workspaces' entries are marked
stale rather than refetched, and the only active queries under the prefix are the view's own
list and the sidebar rail. The rail's refetch is a fan-out of `1 + W` requests for `W`
workspaces (`useRecentChatrooms.ts:29-38`), so the cost is real but paid once per create or
delete. A favourable second-order effect: `useChatroomSettings.ts:121-130` already prefix-reads
the same entry, so this makes that path fresher rather than introducing a new hazard.

**F-5's range refetch is the largest new request volume in the dossier (P3).** `⌈N/200⌉`
sequential requests per reconnect, and auto-pagination makes a large N routine rather than
exceptional. The generation guard bounds concurrency but not volume. If this proves too
expensive in practice, the fallback is to bound the reconcile to the most recent page of
scrollback rather than all of it, which narrows the repair without changing its shape; that is
a follow-up, not a rollback.

**Rollback.** Each phase is one commit and reverts independently. P1's revert restores the
narrow emit and the reconnect gap; it strands no data, because none of the three changes has a
persisted counterpart. P2's revert restores the two races and the stale rail. P3's revert
restores the scrollback gap and the key literals. The only existing test whose meaning changes
is `test_non_disclosure_patch_emits_nothing`, which T-1 inverts; reverting P1 restores it with
the test.

## 10. Acceptance Criteria

### Phase 1

- [ ] AC-1: T-1, T-2, T-2a and T-3 each behave as §8 states (T-1, T-2 and T-3 fail against
      current code and pass after; T-2a passes in both directions and is what lets T-2 fail).
- [ ] AC-2: `_wire_grant_route` installs `_spy_room_publisher`, and the seven tests that use it
      still pass. Verified by reading the helper, not only by a green run, since the failure
      mode being closed is a test that passes while asserting nothing.
- [ ] AC-3: A rename emits exactly one room-channel frame whose payload is
      `{"chatroom_id": ...}` and nothing else, asserted by equality.
- [ ] AC-4: An activity-control grant emits on the creator's user channel and produces zero
      room-channel frames.
- [ ] AC-5: With two browsers on one room, renaming it in one updates the other's header
      without a reload or a window blur; and binding an observer while the second browser's
      socket is down leaves the disclosure chip correct after the socket reconnects. Executed
      against a running stack, or left unticked with the reason recorded.

### Phase 2

- [ ] AC-6: T-4 through T-8 each fail against current code and pass after the phase.
- [ ] AC-7: Binding a newly created agent to a room a second session has open makes that
      agent's real name resolve in the second session's rail and in `@mention` send-time
      resolution, without a reload or a blur. Executed against a running stack, or left
      unticked.
- [ ] AC-8: Both orderings are covered, not just the one §4 reproduces. A refetch already in
      flight when the toggle is clicked leaves the group bound once both settle; **and** a
      refetch that starts while the PUT is in flight does the same. A second toggle after
      either does not remove the group. Q-8 records that these are two windows closed by two
      different halves of the fix, so a test covering one proves nothing about the other.
- [ ] AC-9: Deleting or creating a room from the chatroom list, and deleting or creating a
      workspace, each refresh the sidebar recent rail — **and the workspace list itself still
      refreshes on both of its own mutations.** The second clause is not redundant: the
      workspace sites keep their existing `convKeys.workspaces` invalidation and gain a second
      one, and replacing rather than adding would regress the list while leaving AC-9's first
      clause green.
- [ ] AC-10: No hand-written spelling of the `['conversation','chatrooms']` prefix remains in
      `frontend/src` — the three existing literals in `useChatroomSettings.ts` are converted
      along with the four new call sites.
- [ ] AC-11: The corrected comment at `ChatroomView.vue:784-786` describes the absence test the
      code performs and no longer claims renames are handled.

### Phase 3

- [ ] AC-12: T-9 fails against current code and passes after; T-10 and T-11 behave as guards
      per §8.
- [ ] AC-13: `convKeys.messages` has no hand-written duplicate anywhere in
      `frontend/src/slices/conversation`, **tests included**, with exactly one documented
      exception: the `messagesKey` comparison at `useChatroomSocket.ts:699`, which FU-5 owns
      because closing it requires a length check rather than a rename. A grep for the literal
      returns the factory and that one site.
- [ ] AC-14: `convKeys.search` and `convKeys.export` are gone, along with the stale line in the
      header comment, and nothing referenced them.
- [ ] AC-15: After a socket gap in which a paged-in older message is deleted, that message is
      absent from the feed once the socket reconnects, and the reader's scroll position is not
      moved except by the removal itself. The scroll half is a browser observation; execute it
      against a running stack or leave it unticked with the reason recorded.

### All phases

- [ ] AC-16: `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`, `pnpm test`,
      `pnpm lint`, `pnpm run typecheck` and `pnpm build` pass at the end of **each** phase, not
      only at the end of the dossier. Backend `pytest` in full and the rest of the matrix come
      from CI: the local host has no Postgres, Redis or Vault, so its `tests/wiring/` tier fails
      with `socket.gaierror` for reasons unrelated to any change. Locally, run
      `pytest tests/unit -q` against the recorded baseline of 7938 passed / 6 skipped, and
      `pnpm test` against 1788 passed across 233 files.

## 11. SRS Delta

None. Every item restores documented behaviour: [R28.09] and [R28.10] for the disclosure and
roster freshness, [R13.29] for the member-group binding, [R13.16] for the deleted message. The
one place the SRS is thin is `@mention` wake resolution, which [R28.04] presupposes without
defining; the operative contract is stated at `utils/mentions.ts:1-6` and F-1 is judged against
that. That gap is noted rather than closed here, because writing a requirement for a mechanism
this dossier is not changing would be inventing scope. Recorded as FU-4.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — An agent rename does not reach an open room. `backend/app/api/v1/agents.py`
  publishes nothing at all and `agent_service.py` emits audit rows only, so no client-side fix
  can see a rename; Q-7 records why F-1's repair is bounded to the "bound mid-session" arm.
  Closing it needs an agent-level event, which is a new publishing surface and a new audience
  question (an agent belongs to a project, not a room, so "who should hear it" is not the
  question `chatroom.updated` already answered).
- **FU-2** — `PUT /chatrooms/{id}/member-groups` (`chatrooms.py:623-658`) publishes nothing, so
  a member-group change made in one session never reaches another. F-2's fix makes the writing
  tab correct and leaves the cross-tab case open. Deliberately out of scope: adding a frame here
  needs the same audience analysis Q-3 had to do, and the fields in question gate room access,
  so getting the split wrong has a disclosure cost.
- **FU-3** — Nothing marks an unresolved `@token`. `activeMention` (`utils/mentions.ts:57-62`)
  never consults the agent list, `useMentionAutocomplete.ts:34` closes the popover when a token
  matches nothing (indistinguishable in the UI from the caret not being in a mention), and
  `onSend` never compares typed-token count against resolved count. This is complementary to
  F-1 rather than an alternative: it would tell the user the mention failed while the rail still
  showed an id fragment. There is wording precedent at
  `AgentActivityControl.vue:50-63,169-172` (`conversation.activityControl.unresolved`).
- **FU-4** — The SRS does not define `@mention` wake resolution, though [R28.04] presupposes
  it. Worth an entry the next time §28 or §15 is amended for another reason.
- **FU-5** (route to `check-quality`) — The remainder of the observer dossier's FU-4: the
  factory-bypassing literals this dossier does not convert, and the element-wise queryKey
  comparison at `useChatroomSocket.ts:697-716`, which matches on indices 0 to 2 without checking
  length and so would keep matching a grown key after the `setQueryData` sites had already gone
  dark. The audit's §6 carries the full list, including the two sites worth touching in other
  slices (`AgentGroupDetailView.vue:58`, `useConfigEditor.ts:35`).
- **FU-6** — `useChatroomSettings.ts`'s room ref and `useChatroomBindings`' binding refs are a
  second source of truth beside the `chatroom-agents` cache. The audit refuted the cross-tab
  clobber this appeared to enable, so it stays what the observer dossier's FU-3 called it: a
  structural smell for `check-quality`, not a defect.
- **FU-7** — `patchReleased` synthesises `released_at` client-side
  (`useObservations.ts:374`) rather than taking the server's value when mirroring a release into
  another of the creator's tabs, and that path patches without invalidating (`:319-322`) unlike
  the locally initiated release at `:408`. Whether it is user-visible depends on whether the
  panel renders that timestamp, which was not traced.
- **FU-8** — `account-deleted` (`admin_service.py:453`) has no frontend consumer; a deleted
  user's tab keeps rendering its populated cache until the next request 401s. An identity-slice
  question rather than a conversation-cache one.
- **FU-9** — `ChatroomListView.vue` (423 lines) and `WorkspaceListView.vue` (311 lines) are
  near-identical: search ref plus filter computed, create modal state, create and delete
  mutations with the same invalidate-plus-toast shape, the same confirm-then-mutate action
  handler, and the same `useProjectRole` gate. This dossier edits four of those mutation sites
  and deliberately does not extract the duplication, which would turn a four-line bugfix into a
  refactor of two views. Route to `check-quality`.
