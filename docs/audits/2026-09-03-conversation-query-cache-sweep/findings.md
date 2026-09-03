---
type: audit
status: closed
created: 2026-09-03
requirements: [R13.16, R13.29, R28.09, R28.10]
---

# Audit: conversation slice query-cache layer

## 1. Scope

**Area.** The TanStack Query cache layer of `frontend/src/slices/conversation`: every query
key the slice reads or writes, the server-side write paths that can change what each key
caches, and whether anything invalidates the key when they do. The backend was read only as
far as needed to establish which writes exist and which of them publish a WebSocket frame.

This is FU-2 of `docs/tasks/2026-09-03-observer-ui-defect-sweep/spec.md:966-968`, which
recorded that the observer-UI audit found two orphaned keys (`convKeys.chatroom` and
`chatroom-agents`), fixed both, and never checked the rest. FU-4 of the same dossier
(`spec.md:972-975`, hand-written key literals that bypass the `convKeys` factory) was folded
in, and §6 records its disposition.

**Intent sources.** `REQUIREMENTS.md` entries [R13.16], [R13.29], [R28.09] and [R28.10]; the
approved dossier `docs/tasks/2026-09-03-observer-ui-defect-sweep/spec.md` (RC-2 in particular,
which states the root cause this sweep extends); and, for three findings, a contract the code
states about itself in a comment and then does not keep. The last category is weaker than an
SRS entry but is not merely internal consistency: in each case the comment is the only written
statement of the invariant, and the code violates it.

The SRS does not define `@mention` wake resolution directly. [R28.04] presupposes it ("they
are excluded from the @mention candidate set and from mention resolution"), and the operative
contract is stated at `frontend/src/slices/conversation/utils/mentions.ts:1-6`. F-1 is judged
against that, and the gap is noted here rather than hidden.

**Depth.** Thorough. Four investigation lenses run in parallel (three literal keys traced
end to end; every `convKeys` entry swept; factory-versus-literal drift; WebSocket event flow
against cache invalidation), then two independent adversarial verification rounds whose only
instruction was to refute. Two candidates were refuted and are recorded in §4. Every line
citation carrying a finding was re-read directly rather than taken from a lens report, and
the one library behaviour the analysis depends on was verified by reading the installed
package source rather than assumed.

## 2. Coverage

**Read in full.** `queries/index.ts`; `useChatroomSocket.ts`; `useObservations.ts`;
`useChatroomMessages.ts`; `useChatroomMessageEditing.ts`; `useChatroomSettings.ts`;
`useChatroomBindings.ts`; `useRecentChatrooms.ts`; `ChatroomView.vue`;
`ChatroomSettingsView.vue`; `ChatroomListView.vue`; `WorkspaceListView.vue`;
`WorkspaceSettingsView.vue`; `utils/mentions.ts`; `shared/query-client.ts`;
`shared/composables/useNetworkStatus.ts`. All thirteen `useQuery`/`useInfiniteQuery` call
sites in the slice are accounted for, and every `invalidateQueries`/`setQueryData`/
`getQueryData`/`removeQueries` site in the slice was enumerated by grep and classified.

**Read as far as the question required.** `backend/app/api/v1/chatrooms.py`, `messages.py`,
`observations.py`, `activities.py`; `turn_engine.py`'s publish sites;
`shared_kernel/realtime/pubsub.py`. The backend was surveyed for *which writes publish a
frame*, not audited for correctness. A backend write path that changes a room DTO in a way no
frontend query caches would not have been noticed by this sweep.

**Verified against the installed package, not assumed.** TanStack key prefix-matching
semantics, and the behaviour on which F-2 depends: `@tanstack/query-core@5.101.4`
`build/modern/query.js` calls `this.setData(data)` unconditionally when a fetch resolves,
with no comparison against a manual `setQueryData` that landed while the fetch was in flight.

**Deliberately not covered.**

- **Other slices.** One counting pass was run to size the question and is reported in §6 as
  FU-3; no other slice's cache was analysed. The eleven other slices each have their own
  `queries/index.ts` factory and were not swept.
- **Structural quality.** Two-sources-of-truth patterns, dead factory entries and
  factory-bypassing literals are recorded in §6 and routed to `check-quality`. §4 explains
  why one of them is *not* a functional defect, which is the load-bearing half of that
  routing.
- **Security.** Cross-tenant or cross-room leakage through the cache was not a lens. The
  observer-disclosure question in F-3 is treated as a correctness defect against [R28.09],
  not as a vulnerability.
- **Runtime observation.** Docker was unavailable, so no finding here was reproduced against
  a running stack. Every failure scenario is traced through code, and F-2's timing window in
  particular is argued from the library source rather than measured. §3 marks what that
  leaves unproven per finding.
- **Already-recorded staleness.** FU-7 and FU-11 of the observer dossier (`spec.md:979-985`,
  `:1006-1018`) were re-verified as still open and are *not* restated as new findings; §5
  records why they belong in the same unit of work.

## 3. Findings

Ordered by severity, and within severity by reachability.

## F-1: the agent-name map has no invalidator, so an agent bound while a viewer is in the room cannot be summoned by name

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/views/ChatroomView.vue:526-531` (the query,
  keyed `['conversation','project-agents', projectId]`), `:533-541` (`agentNames`),
  `:572-584` (`agentList`, whose `name` falls back to `a.agent_id.slice(0, 8)`), `:775-782`
  (`agentList` passed as `mentionAgents`);
  `frontend/src/slices/conversation/composables/useChatroomMessages.ts:288`;
  `frontend/src/slices/conversation/utils/mentions.ts:36` (matches `agent.name` only, with
  no id fallback), `:1-6` (the contract: the backend trusts the client's resolved id list and
  never re-parses names); `backend/contexts/conversation/application/triggers.py:84-115`
  (the server only filters the supplied ids, it does not resolve names);
  `frontend/src/slices/conversation/composables/useChatroomSocket.ts:407-423` (the
  `chatroom.updated` handler invalidates `convKeys.chatroom` and `convKeys.chatroomAgents`,
  and deliberately not this key). The key has exactly one reference in all of `frontend/src`
  and no invalidator anywhere.
- **Failure scenario**: participant B has room R open and focused. In another session, a
  manager creates agent "Research Bot" and binds it to R. B's socket receives
  `chatroom.updated`, so `useChatroomSocket.ts:421` refetches the roster and the agent
  appears in B's rail, labelled with an eight-character hex fragment, because its id was
  never in B's cached project-agent list. B types `@Research Bot`. `resolveMentions` finds no
  agent whose `name` matches, `mention_agent_ids` goes up empty, and
  `_dispatch_mention_wakeups` returns early. The agent is never woken and B is told nothing.
  Only typing `@a1b2c3d4`, the fragment shown in the rail, would work. The same holds for a
  rename: the agent keeps its old label *and* its old mention handle in every already-open
  room.
- **Blast radius**: every viewer sitting in a room when an agent is created, bound or renamed
  by someone else. Self-heals on a window blur and refocus (`staleTime` defaults to 0 and
  `refetchOnWindowFocus` is on), so the harm is bounded by the viewer's next alt-tab, which
  for someone watching a live room may be never. The failure is silent in the direction that
  matters: an explicit user action produces no effect and no error.
- **Intent source**: `utils/mentions.ts:4-6`, which states that resolution happens on the
  client "against the room's known agents". The map that names those agents is not kept
  current, so the room's known agents and the room's *nameable* agents diverge. The precedent
  that this is an oversight rather than a design is in the same file: `ChatroomView.vue:784-805`
  implements exactly this self-heal for the human roster, refetching once per unseen
  `sender_id`. No equivalent exists for agents.
- **Not proven**: that a real user types the name rather than accepting the autocomplete.
  The autocomplete (`ChatroomView.vue:592-599`) offers only names it already has, so a user
  who always uses it never hits this. Free-typing is not prevented, and the composer does not
  mark an unresolved `@token`.

## F-2: an optimistic member-group write is reverted by a refetch issued before it, and the next click then deletes the binding on the server

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/views/ChatroomSettingsView.vue:121-125`
  (`boundQuery` overrides nothing, so `staleTime` is 0 and focus refetch is on), `:169-193`
  (`toggleGroup`), `:181` (the optimistic `setQueryData`), `:174-177` (the next payload is
  rebuilt from `boundGroupIds`), `:130-139` (the surface is explicitly treated as a room
  access control), `:155-163` (the comment asserting the invariant this finding breaks);
  `frontend/src/shared/query-client.ts:4-21` and `frontend/src/app/main.ts:56`;
  `@tanstack/query-core@5.101.4` `build/modern/query.js`, where a resolving fetch calls
  `this.setData(data)` unconditionally with no check against an intervening manual write;
  `backend/app/api/v1/chatrooms.py:623-658`, which emits no `chatroom.updated`, so no frame
  repairs it. `cancelQueries` appears nowhere in `frontend/src`.
- **Failure scenario**: the manager alt-tabs away from the settings page and back.
  `visibilitychange` starts a focus refetch of `boundQuery`, which is always stale. Fifty
  milliseconds later they click a group checkbox. The PUT commits and returns the applied
  set; `:181` writes it into the cache and `:182` shows a success toast. The GET, issued
  first and therefore certainly reading pre-PUT state, resolves afterwards and overwrites the
  cache with the old set. The checkbox visibly flips back under a success toast. The manager
  clicks it again; `:174` now rebuilds `next` from the reverted set, and because the endpoint
  replaces rather than patches, the second PUT removes the binding the first one added. The
  room ends with the group unbound, and the user was told twice that it saved.
- **Blast radius**: any project using member groups. [R13.29] makes a bound group's
  membership satisfy the room's access check, so the dropped write is a silent revocation of
  room access, not a cosmetic one. Confined to managers holding capability #14 ([R13.31]).
- **Intent source**: [R13.29], and the comment at `ChatroomSettingsView.vue:155-163`, which
  states the invariant precisely and then reaches the wrong conclusion from it: "The server
  already returns the applied set, so it is written straight into the query cache and no
  refetch has to be raced." Writing the applied set into the cache does not win a race
  against a fetch that was issued *before* the write and resolves *after* it. The sibling
  surface in the same feature already has the guard this one lacks: `useChatroomSettings.ts`
  compares versions, and `__tests__/useChatroomSettings.test.ts:414-444` pins it as "drops a
  revalidation that lands after a newer save".
- **Not proven**: the timing window was not measured against a running stack. The ordering
  argued above requires no server-side reordering (the GET is issued first and reads first),
  which is why it is stated in preference to the reverse ordering, which could not be traced.

## F-3: the WebSocket reconnect reconcile omits the room DTO and the agent roster, so a disclosure change lost to a socket gap is lost permanently

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/composables/useChatroomSocket.ts:621-642`,
  the entire reconnect block, which reconciles agent-thinking state, messages
  (`reconcileMessages()`, the only query-cache write, and it writes only
  `convKeys.messages`), presence, activation and approvals. `convKeys.chatroom` and
  `convKeys.chatroomAgents` are absent. Their only invalidator is the frame handler at
  `:418-423`, whose own comment at `:407-417` states that the key "has no other invalidator".
  `useObservations.ts:198-226` adds one more refetch on reconnect, of the observations query
  alone. `backend/shared_kernel/realtime/pubsub.py:3-6` states that messages published while
  a subscriber is disconnected are lost. `backend/app/api/v1/chatrooms.py:552-574` publishes
  the disclosure patch to the room channel, which is a participant's only delivery path.
- **Failure scenario**: the creator flips `disclose_observers` on, or binds an observer,
  while a participant's socket is down. The frame is published to a channel the participant
  is not currently subscribed to and Redis does not replay it. On reconnect the participant's
  client re-reads messages, presence, activation and approvals, and does not re-read the room
  DTO. `observers_present` stays false in the cache and the disclosure chip stays dark for as
  long as the participant keeps the tab focused. This is the F-1 symptom of the previous
  audit, reached through the reconnect door instead of the missing-handler door.
- **Blast radius**: participants and guests in rooms with an observer. Two of the three
  originally supposed triggers do not reach it and are recorded in §4; what survives is a
  socket-only gap with HTTP healthy, which is what a backend restart or deploy produces (nginx
  answers the ticket fetch with a 502, which is a response, so `useNetworkStatus` marks the
  connection *restored* rather than lost and its blanket invalidation never fires), and what
  a connection-cap eviction or slow-consumer close produces for one client at a time.
- **Intent source**: [R28.09] and [R28.10]. More directly, RC-2 of the observer dossier
  (`spec.md:164-171`) names three conditions that each independently masked F-1, one of them
  being that "`useChatroomSocket.ts:603-624` reconciles four things on reconnect and the room
  is not among them". Phase 1 closed the first two and left this one, because §7.2
  (`spec.md:273-274`) weighed reconnect invalidation as an *alternative* to the event ("it
  does not correct the symptom to invalidate on reconnect only, because the reported failure
  is a healthy connection") rather than as a complement to it. That reasoning is sound about
  which fix to choose and does not close the reconnect leg.
- **Not proven**: that a disclosure change actually coincides with a given viewer's gap. The
  conjunction is genuinely narrow. What makes it worth recording at this severity is the
  direction of the failure and the fact that the previous audit already identified the
  mechanism.

## F-4: the recent-chatrooms rail is not invalidated by chatroom create or delete from the list view, nor by workspace delete

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/queries/index.ts:16-18` gives
  `chatrooms(wsId)` = `['conversation','chatrooms',wsId]` and `recentChatrooms(pid)` =
  `['conversation','chatrooms','recent',pid]`; element [2] is a workspace id in one and the
  literal `'recent'` in the other, so the first is not a prefix of the second.
  `ChatroomListView.vue:156` (delete) and `:202` (create) invalidate
  `convKeys.chatrooms(workspaceId)`, which therefore misses. `WorkspaceListView.vue:102`
  (workspace delete, which cascades its rooms) invalidates only
  `convKeys.workspaces(projectId)`, which misses too. Only `useChatroomSettings.ts:187,208,317`
  use the bare two-element prefix that reaches it. `useRecentChatrooms.ts:42` sets
  `staleTime: 60_000`, the only such override in the slice, and its consumer
  `SidebarChatroomList` sits inside `AppSidebar` inside `AppShell`, which `App.vue:67-77`
  does not key, so it never unmounts and never gets a mount refetch.
- **Failure scenario**: a user on the workspace's chatroom list deletes room R. The table
  updates. The sidebar "recent" rail keeps rendering R, and because the rail never unmounts
  and its query is fresh for sixty seconds, neither of the two nets that rescue every other
  key applies. Clicking R routes to a room the server no longer serves; the header falls back
  to an id fragment and the message list renders a generic "failed to load" alert with a
  Retry button, which reads as a transient network problem rather than as a room the user
  just deleted. The symmetric case for create leaves a newly made room absent from the rail.
- **Blast radius**: every user, on an ordinary navigation surface. No data is written wrongly
  and nothing is disclosed; the cost is a dead link and a misleading error.
- **Intent source**: `queries/index.ts:9-11`, which states that `recentChatrooms` nests under
  the `chatrooms` prefix "so the broad `invalidateQueries(['conversation','chatrooms'])`
  (rename/delete in useChatroomSettings) also refreshes the project-wide recent list". The
  claim is true of the call sites it names and the nesting does work; what makes this a defect
  rather than a documented limit is the asymmetry, since deleting a room from the settings
  page refreshes the rail and deleting the same room from the list page does not.
- **Not proven**: nothing material. The key algebra was checked directly and the mount
  behaviour of the sidebar was traced to `App.vue`.

## F-5: paged-in older messages are never reconciled after a socket gap, so a hard-deleted message stays legible

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `useChatroomMessages.ts:121-154`, where `olderMessages` is filtered against
  the recent cache and concatenated into the rendered list, so it is half the visible feed
  rather than a pagination buffer; its only mutation sites are `:186`, `:195`, `:334`, `:342`,
  `:377` and `:385`, none of which is a reconnect hook. `ChatroomView.vue:932-933` wires the
  live `message.updated`/`message.deleted` frames into it. `useChatroomSocket.ts:300-317`
  refetches only the latest page into the messages cache on reconnect.
  `backend/contexts/conversation/application/message_service.py:343` hard-deletes, so there is
  no tombstone the client could fall back on.
- **Failure scenario**: a participant scrolls back two hundred messages, the socket drops for
  fifteen seconds, and a moderator deletes one of the paged-in messages inside that window.
  The reconnect refetch repairs the cache half of the feed and never touches `olderMessages`.
  The dedup at `:124` makes it worse rather than better: once the row leaves the cache it
  stops being filtered out of `olderMessages`, so the deleted message is restored into the
  render. It stays on screen until the user navigates out of the room.
- **Blast radius**: narrow, because it needs scrollback, a socket gap and a concurrent
  moderator delete inside that gap. The direction is what earns it a place: content the
  server has destroyed remains readable.
- **Intent source**: [R13.16], which requires deleted messages to be "removed immediately
  from both DB and search index". The client is outside that sentence's literal scope, which
  is why this is minor rather than major, but a delete the viewer never sees take effect does
  not serve the requirement's purpose.
- **Not proven**: reachability was argued from `canDelete` at `useChatroomMessages.ts:78-81`
  granting any admin or moderator a delete on any message, not observed. Nothing in
  `slices/conversation/__tests__/` references `olderMessages` at all.

## 4. Refuted Candidates

**The `chatroom-members` key is not an orphan.** It was one of the three keys this sweep was
commissioned to examine and it has the same surface signature as the two the previous audit
fixed: a hand-written literal, one reference, no `invalidateQueries` anywhere. The guard is a
targeted refetch rather than an invalidation, at `ChatroomView.vue:790-805`, which watches the
message list and refetches the roster once per sender id it cannot yet name. That covers the
dominant change path, which is a new human author appearing. Two residues are real and both
are minor: a guest who enrols and never posts stays out of the mention autocomplete for
already-open viewers, and the comment at `:784-786` claims the mechanism handles "new authors
(and renames)" while the condition at `:797` short-circuits for anyone already named, so a
rename never triggers it. Recorded as FU-6.

**`useChatroomBindings`'s plain refs do not produce a cross-tab write conflict.** The
composable holds binding state in refs rather than the query cache, the settings view mounts
no socket subscription, and refs get no focus refetch, which together look like permanent
session staleness with a clobber path. The refutation is that there is no bulk save to clobber
with: every write at `useChatroomBindings.ts:155-256` is a per-agent delta naming one agent id,
and each is followed by an unconditional `loadBindings()` that re-reads from the server. A
stale ref therefore cannot overwrite another session's change, and the panel self-heals on the
operator's next action. The one true replace-semantics control on that page, the member-group
picker, is backed by real queries and is the subject of F-2. What remains is display staleness
in the Add-agent dropdown, which is the structural two-sources-of-truth smell the observer
dossier already recorded as FU-3 and routed to `check-quality`. That routing stands.

## 5. Hand-off

Triaged 2026-09-03. All five findings were selected for fixing and consolidated into one
dossier, phased by blast radius. §6 carries the observations this skill does not judge.

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | fix (phase 2) | `docs/tasks/2026-09-03-conversation-query-cache-staleness/` |
| F-2 | fix (phase 2) | `docs/tasks/2026-09-03-conversation-query-cache-staleness/` |
| F-3 | fix (phase 1) | `docs/tasks/2026-09-03-conversation-query-cache-staleness/` |
| F-4 | fix (phase 2) | `docs/tasks/2026-09-03-conversation-query-cache-staleness/` |
| F-5 | fix (phase 3) | `docs/tasks/2026-09-03-conversation-query-cache-staleness/` |

Two of the out-of-scope observations below were also taken into that dossier at the user's
direction rather than left for a later pass: FU-1's four `setQueryData` sites (converted
together with the fourteen test literals that would otherwise drift with them) and FU-4's two
dead factory entries. FU-2's prefix-only factory entries were added as the instrument F-4's
fix needed. The rest stand as recorded.

Two defects already recorded by the observer dossier were re-verified as still open and are
deliberately not restated as findings here, because they are the same root cause as F-3 (a
backend write that changes the room DTO and publishes nothing) and the dossier already
proposes closing them together (`spec.md:1015-1017`). Whoever specs F-3 should fold them in:

- **FU-7** (`spec.md:979-985`) — `patch_chatroom` gates its emit on `fields &
  _DISCLOSURE_FIELDS` (`backend/app/api/v1/chatrooms.py:552`), so a rename or an access-flag
  patch publishes nothing.
- **FU-11** (`spec.md:1006-1018`) — `patch_chatroom_agent_activity_control`
  (`chatrooms.py:876-921`) publishes nothing, while its structurally identical sibling
  `patch_chatroom_agent_draft_access` does.

## 6. Out-of-scope Observations

FU numbers here are local to this audit.

- **FU-1** (route to `check-quality`) — **FU-4 of the observer dossier is closed as a
  functional question and should be reopened as a quality one.** All thirteen key literals in
  the slice that duplicate a `convKeys` entry are byte-identical to the factory's output
  today, and no invalidation in the slice currently matches nothing. There is no live defect.
  The hazard is asymmetric in a way worth recording before it is acted on: `invalidateQueries`
  prefix-matches, so a factory key that *grew* a segment would leave the two invalidation
  literals at `useChatroomSocket.ts:124,137` still working, while `setQueryData` hashes
  exactly, so the same change would silently orphan the four writes at `:282`, `:311`, `:379`
  and `:400`, and the message feed would stop receiving every WebSocket append, edit and
  delete with no exception and no console warning. The fourteen test literals in
  `__tests__/useChatroomSocket.test.ts` hand-write the same string rather than calling the
  factory, so they would drift with it and the suite would stay green. Those four sites are
  the ones to convert first.
- **FU-2** (route to `check-quality`) — **the bare `['conversation','chatrooms']` prefix in
  `useChatroomSettings.ts:123,187,208,317` is deliberate and correct; leave the behaviour
  alone.** Verified against the installed `@tanstack/query-core`: the two-element prefix
  matches both `convKeys.chatrooms(wsId)` and `convKeys.recentChatrooms(pid)` and does not
  match `convKeys.chatroom(id)`, so `queries/index.ts:9-11` is accurate and
  `findInCache` at `useChatroomSettings.ts:121-130` depends on it. Narrowing it to
  `convKeys.chatrooms(wsId)` would break the recent-list refresh. The gap is that the intent
  is stated only in `queries/index.ts`, a file a maintainer editing `useChatroomSettings.ts`
  has no reason to open, and that no zero-argument factory entry exists to name the prefix.
  Note the interaction with F-4: if F-4 is fixed by widening the list-view call sites to this
  same prefix, a named factory entry stops being cosmetic.
- **FU-3** (route to `check-quality`) — **a whole-frontend extension of this sweep is small
  and mostly not worth doing.** A counting pass over the other eleven slices found eight
  hand-written key literals against conversation's seventeen. Five of the eight are deliberate
  bare prefixes of the kind FU-2 describes. Only two look worth touching:
  `agent-groups/views/AgentGroupDetailView.vue:58` hand-writes the *agents* slice's key
  `['agents','list',projectId]`, duplicating `agents/queries/index.ts:3` across a slice
  boundary, and `prompt-studio/composables/useConfigEditor.ts:35` uses
  `['prompt-studio','my-keys']` with no factory counterpart. No other slice's cache was
  analysed for orphaned invalidation, so this is a count, not a clean bill.
- **FU-4** (route to `check-quality`) — **`convKeys.search` and `convKeys.export` are dead
  entries.** `queries/index.ts:26-27` and `:28` have zero readers and zero writers in all of
  `frontend/src`. The features exist but never went through TanStack: `useChatroomSearch.ts:17`
  and `useChatroomExport.ts:23` both resolve into plain refs. Deleting them, and the matching
  line in the header comment at `:7`, removes two entries that a future reader would otherwise
  have to check before trusting a sweep like this one.
- **FU-5** — **the `account-deleted` frame has no consumer.** `admin_service.py:453` publishes
  it on the user channel and no `.subscribe('account-deleted', ...)` exists anywhere in
  `frontend/src`; `useBanKickGuard.ts:35` subscribes to `ban-kick` alone. A deleted user's tab
  keeps rendering its populated cache until the next request 401s. This is an identity-slice
  question rather than a conversation-cache one, which is why it is here rather than in §3.
- **FU-6** — **two minor residues on the `chatroom-members` refetch**, from the refutation in
  §4: the comment at `ChatroomView.vue:784-786` claims renames are handled and the condition
  at `:797` short-circuits for anyone already named, and a guest who enrols without posting is
  absent from the mention autocomplete for already-open viewers. Both self-heal on window
  focus. The comment is worth correcting whether or not the behaviour is.
- **FU-7** — **`patchReleased` synthesises `released_at` client-side.**
  `useObservations.ts:374` writes `new Date().toISOString()` rather than the server's value
  when mirroring an `observation.released` frame into another of the creator's tabs, and that
  path patches without invalidating (`:319-322`), unlike the locally initiated release at
  `:408`. Whether it is user-visible depends on whether the panel renders that timestamp,
  which was not traced. Self-corrects on the next refetch.
