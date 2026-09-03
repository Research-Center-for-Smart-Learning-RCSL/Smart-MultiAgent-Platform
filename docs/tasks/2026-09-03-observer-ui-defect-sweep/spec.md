---
type: bugfix
status: in-progress
created: 2026-09-03
requirements: [R24.32, R28.02, R28.09, R28.10, R28.13, R28.16]
depends_on: []
---

# Observer UI defect sweep

## 1. Summary

The observer surface tells its readers things that are not true. A participant can be
observed for an entire session without ever seeing the disclosure chip, because the room
DTO that carries `observers_present` is fetched once and never invalidated. A teacher who
enables guest links with disclosure left on is never warned, because the warning fires on
the inverted condition. An org owner is locked out of a legacy room's observer surface the
server would have granted them, because the client re-derives an authorization answer the
server already serialises. The shipped example analyst is instructed to make a tool call
its own schema rejects. An admin inspecting someone else's failing observer sees "idle".
A backend error is drawn as "No observations yet". An observer whose worker dies sits on
"analyzing" for the rest of the page's life.

This dossier consolidates all sixteen findings of
`docs/audits/2026-09-03-observer-ui-visualization/findings.md` — eight major, eight minor —
into one unit of work executed in **three phases**, each of which is a self-contained
milestone with its own tests, its own commit, and its own pass through the Definition of
Done. It is explicitly not a single-session task; see §7.1 for the phase contract.

## 2. Observed vs Expected

Each row restates one finding. `findings.md` holds the full evidence and the adversarial
verification record; this section states the deviation and its intent source so that a
reader of the spec alone can act.

### Phase 1 — Disclosure and access correctness

| Finding | Observed | Expected | Intent source |
|---|---|---|---|
| F-1 | `observers_present` reaches the header chip from a room fetched once at mount (`ChatroomView.vue:480-484`, key `convKeys.chatroom` = `['conversation','chatroom',id]` at `queries/index.ts:19`). Nothing invalidates that key anywhere in the slice — every invalidation targets the plural `['conversation','chatrooms']` or `['conversation','messages',id]` (`useChatroomSettings.ts:187,208,317`; `useChatroomSocket.ts:123,136`), and TanStack matches key elements exactly. No room-level WS event exists: `backend/app/api/v1/chatrooms.py` contains no `Publisher`, `publish`, `room_channel` or `user_channel` call, and `chatroom_service.py` emits audit records only. A participant is notified of observation only after a window blur/refocus or a reload. | Binding or unbinding an observer, or flipping disclosure, updates every live viewer's indicator. | [R28.09]; `docs/tasks/2026-07-22-observation-binding-cleanup/spec.md:186-191` claims no stale indicator is left behind, which is true of the DTO computation and false of the client. |
| F-2 | The guest-observer callout fires only on `allow_guest_links && hasObserver && !flags.disclose_observers` (`ChatroomSettingsView.vue:216-221`), and its copy (`locales/en.json:259`, `zh-TW.json:259`) says guests are observed without notice "while disclosure is off". But `chatrooms.py:238-240` forces `disclose_observers=False` **and** `observers_present=False` for every pure guest regardless of the room setting, pinned by `backend/tests/unit/test_observer_agents.py:136-139`. Disclosure defaults to true (`alembic/versions/0041_observer_agents.py:58`). So the higher-risk configuration is exactly the one that suppresses the warning. | The teacher is warned whenever guest links and an observer coexist, and the copy does not imply that disclosure changes what a guest sees. | [R28.02] (guest neutralisation) and [R28.09]; `docs/tasks/2026-08-24-agent-readable-live-drafts/spec.md:728-735` (D-8) records the suppression as deliberate, which makes the callout the defect. |
| F-3 | For a NULL-creator room the backend falls back to moderator semantics — `access.py:456-458` → `:82-83` → `:54-62`, where an inherited `ORG_OWNER` role counts with no `project_members` row. The client instead requires such a row: `useObservations.ts:63-71` and `ChatroomSettingsView.vue:201-209` scan `projectsApi.listMembers`, which serves `project_members` only (`backend/app/api/v1/projects.py:365-373`). `is_moderator` is already on the DTO for exactly this reason (`chatrooms.py:136,253,474,491`; `types/index.ts:42`) and already consumed at `ChatroomView.vue:742`. NULL-creator rooms still exist: `0041_observer_agents.py:32-43` backfills only from audit rows with a non-NULL actor and states at `:3-5` that unmatched rooms stay NULL. Second, independent path: `listMembers` is called with no pagination (`useObservations.ts:55`; `tenancy/api/projects.ts:77-81`), so the default `limit=100` (`backend/app/api/v1/deps.py:29`) drops a genuine owner past row 100. | The client's observer gate agrees with the server's, by reading the server's own answer. | [R28.02] at `REQUIREMENTS.md:2120`. The identical bug class is documented and already fixed elsewhere: `frontend/src/slices/tenancy/composables/useProjectRole.ts:1-16`, `backend/app/api/v1/projects.py:57-65`. |
| F-7 | The observations query sets `retry: false` (`useObservations.ts:105`), collapses `undefined` data to `[]` (`:114-116`) and returns no `isError`/`error` (`:239-253`). `ObserverPanel.vue:89-96` has no error prop, so once the `loading` gate at `:28-40` clears, `:42-47` paints `SEmptyState` with the positive copy at `locales/en.json:196-197`. No global handler compensates: `frontend/src/shared/query-client.ts:4-21` installs no `QueryCache.onError`, and `useServerErrors` is mutation-only (`shared/composables/useServerErrors.ts:16-55`). The panel stays mounted while the list is dead because `hasObserverSurface` (`:121-123`) is satisfied by the surviving bound-agents query. | A failed fetch is distinguishable from an empty room, and offers a retry. | [R28.16]; the locale copy itself asserts a fact the client has not established. |
| F-10 | `ObserverPanel.vue:18-24` gates the "no observer currently bound" alert on `!roster.length && observations.length` and sits above the `loading` block at `:28-40`, so no loading gate can suppress it. The roster is also empty when `boundAgentsQuery` failed (`ChatroomView.vue:492-496`, `retry: false`) and when an observer was bound in another session — `chatroom-agents` appears exactly once in `frontend/src` (`ChatroomView.vue:493`), nothing invalidates it, and `useChatroomBindings.ts:155-187` writes through raw API calls into local refs without touching the query client. | The alert claims an unbinding only when the roster is known to be empty. | `docs/tasks/2026-07-22-observation-binding-cleanup/spec.md` — the alert exists to explain observations that outlived their binding. |

### Phase 2 — Observer status truthfulness

| Finding | Observed | Expected | Intent source |
|---|---|---|---|
| F-6 | `_emit_observation_event` (`turn_engine.py:3407-3435`) publishes only to `user_channel(recipient)` where the recipient is literally `room.created_by_user_id` (`observation_service.py:106-114`), and publishes nothing when it is None. The observer branches deliberately avoid the room-wide `agent.finished` paths (`turn_engine.py:2961-2968,2978-2986`), pinned by `test_observer_agents.py:1341`. Roster status derives solely from the WS-written Pinia store (`useObservations.ts:75-90`, writers at `:150-182`), so with no events every observer falls through to `'idle'` at `:85`. The 30s poll added for these viewers (`:106-112`) refetches the list only; `ObservationOut` (`app/api/v1/observations.py:76-91`) carries no status. | A viewer with no status feed is told the status is unknown, not that it is idle. | [R28.10], [R28.13]. The W-1 comment at `useObservations.ts:106-109` states the intent to close this staleness gap and closes only the list half. |
| F-8 | `observation.started` sets the analyzing flag (`useObservations.ts:151-157`); only the three terminal handlers clear it (`:158-177`). No timer and no `onStatus`/`onDegraded` registration exists in the file. The room path has both (`useChatroomSocket.ts:39,327-346` watchdog; `:613` reconnect reset inside `onStatus` at `:603-624`). Three loss paths survive verification: a hard worker kill between the started emit (`turn_engine.py:2607`) and the terminal emit (`:3154-3163`); a terminal frame published while the socket is down, since Redis pub/sub does not replay (`shared_kernel/realtime/pubsub.py:3-7`); and a `CancelledError` inside the `_post_commit` scope at `:3154`, which `_post_commit` (`:714-735`) cannot catch and `:3307` re-raises without emitting. | A stale "analyzing" resolves itself, as it does on the room path. | [R28.10], [R28.13]. |
| F-11 | `release()` calls `patchReleased` only, with no invalidate and no version guard (`useObservations.ts:217-221`), unlike `remove()` at `:223-237` whose invalidate at `:236` is pinned by `__tests__/useObservations.test.ts:282-294`. The list endpoint does not filter released rows (`app/api/v1/observations.py:122-135` → `observation_service.py:97-104`), so a poll response issued before the release carries `released_at: null` and overwrites the patch. | An optimistic release survives a concurrent refetch. | [R28.13]. |
| F-12 | `observerPanelVisible = railTab === 'observer' && (isDesktop.value || peopleDrawerOpen.value)` (`ChatroomView.vue:654-657`), and `isCompactDesktop` (`:441`) is strictly inside `isDesktop` — but in that band `.chatroom--compact .chatroom__presence` is `visibility: hidden` (`:1459-1476`) and translated off-screen (`:1484-1488`) unless `.chatroom__panel--open` (`:1490-1493`). So `setPanelOpen(true)` runs (`useObservations.ts:130-133`) and pins `unreadCount` at 0 while the panel is invisible. | The panel counts as open only when it is actually visible. | AC-12 of `docs/tasks/2026-08-30-chatroom-approval-and-overlay-discoverability/spec.md:258-260`. |
| F-13 | `unreadCount` is incremented only by the WS handler (`useObservations.ts:128,170`), never by the query, so an `observation.created` lost to a socket gap raises no badge even after focus-refetch recovers the row itself. | The badge reflects observations the creator has not seen, regardless of how the row arrived. | [R28.13]. |
| F-14 | `app/api/v1/observations.py:167-185` returns 204 with no publish, while release emits at `:249-261`; `observation.deleted` exists only as an audit action (`observation_service.py:244`). `ObservationNotFound` maps to 404 (`interfaces/error_mapping.py:128-132`), and `ChatroomView.vue:710-715` catches delete failures with a bare toast and no refetch while `:681-700` handles 409 and `/invalid-release-target` only, so a 404 falls to the generic `setError` at `:697` with the dialog still open. | Acting on a row the server no longer has produces an actionable message and a corrected list. | None directly — the missing event is a gap in `docs/observer-agents/00-overview.md:151-156`. The asymmetry with `observation.released` is the evidence it was an oversight. |

### Phase 3 — Example packs and render fidelity

| Finding | Observed | Expected | Intent source |
|---|---|---|---|
| F-4 | `packs/creative-thinking-room.json:80` instructs AA that every block except `prose` must carry a `basis` it may not remove — covering `key_points`, `timeline`, `field_coverage`, `mandala_grid` and `attempt_table`. But `_coverage_branch` (`observation_blocks.py:275-290`, serving both `field_coverage` and `mandala_grid` per `:133,:135`) and `_attempt_table_branch` (`:293-314`) declare `additionalProperties: False` with no `basis` property; the server stamps it at `:387`. `tool_registry.py:320-328` runs `schema_violations` before `invoke`, so `observer_tools._refusal`'s guidance (`observer_tools.py:226-240`) is never reached, and the block array is one argument validated whole (`tool_registry.py:246-247`), so one bad element kills the valid blocks with it. | The shipped prompt describes calls the schema accepts. | `docs/examples/creative-thinking-course.md:787-791` — "Computed blocks are not offered the choice at all"; `observation_blocks.py:18-24`. |
| F-5 | `packs/creative-thinking-design.json:27` still lists `mandala-9grid` and `time-traveler-next-steps` as quotable inside DA's mandatory constraint list, while the room pack moved off that rule (`creative-thinking-room.json:28,54,80`) because `mandala-9grid` uses `filled_count_coverage` (`activities/infrastructure/examples/courses/creative-thinking.json:7-11`). The guard excludes DA: `test_agent_example_packs.py:335-356` is parametrized over the three room agents only, while its own docstring at `:324-327` argues DA should be covered. The same line omits `six-hats-shared-case` from both quoting columns and from `binds_activity_types` (`creative-thinking-design.json:21-26`), so DA's default clause forbids quoting a task the room pack permits. | The designer teaches the rule the platform actually enforces. | `docs/examples/creative-thinking-course.md:728,733-739`; the fabrication failure mode at `:893-897`. |
| F-9 | `renderMarkdown()` returns sanitised HTML only (`utils/renderMarkdown.ts:100-104`); the post-pass is the separate `enhanceRenderedMarkdown` at `:185-187`. Its only caller is `useMarkdownEnhance.ts:32`, wired once at `ChatroomView.vue:859` against `listRef` (`:473`) — the message list at `:76-83`. Both `ObserverPanel` mounts are outside that subtree (`ChatroomView.vue:252-264` rail, `:313` drawer), and the unenhanced bindings are `ObservationCard.vue:28-31` and `:39-44`. | The preview a release decision is made against renders the same as the released message. | `docs/tasks/2026-08-24-observer-presentation-blocks/spec.md` — the panel previews what release publishes. |
| F-15 | The participant renderer places `center` by name at index 4 regardless of declared order (`activities/plugins/mandala9grid/MandalaGrid.vue:24-26,41-44,48-53`), while the observer aggregate uses declared `x-order` only (`contexts/activities/application/observation_aggregates.py:137-166`, chunked row-major at `:99-103`). The docstring at `:141-143` asserts that order "is the order the participant's own form renders in", which is false for any type whose `center` is not fifth. The admission gate requires only nine declared properties (`observer_tools.py:70,155-158,221-223`). The shipped course coincides (`creative-thinking.json:40-44`, `x-order: 5`). | The figure labelled `server_facts` puts each count on the cell the participant saw. | The docstring at `observation_aggregates.py:141-143`; `docs/examples/creative-thinking-course.md:848-851`. |
| F-16 | The name fallback is `agentId.slice(0, 8)` (`useObservations.ts:84`, `ObserverPanel.vue:106-108`). `agentNames` comes from `listProjectAgents` (`conversation/api/index.ts:148-150`), which sends no `limit`, so the default 100 applies (`app/api/v1/agents.py:387-400`; `deps.py:29`). Agents are soft-deleted (`agent_service.py:963-975`) and `AgentRepository.list_for_project` filters `deleted_at IS NULL` (`agents/infrastructure/repositories.py:229-236`), so the `ON DELETE CASCADE` at `docs/tasks/2026-07-22-observation-binding-cleanup/spec.md:208` never fires and observations outlive the resolvable name. | A stranded observation is headed by a name or an explicit "deleted agent" label. | None defends these two cases; `ChatroomView.vue:498-501` scopes its acceptance of the truncated id to query gating only. |

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which findings does this dossier cover? | All sixteen, executed in three phases rather than one session. | The user's explicit direction. The alternative — sixteen dossiers, or four — multiplies the freshness re-verification and the board churn without changing the work, since the findings share three tight file clusters. Phasing inside one dossier keeps the analysis in one place while keeping each milestone independently revertible. |
| Q-2 | How should F-1 refresh `observers_present`? | Add a room-channel `chatroom.updated` event carrying ids only; the frontend invalidates `convKeys.chatroom` on receipt. | Chosen over a reconnect-only invalidate (leaves a healthy connection stale for the whole session, which is the reported defect) and over a 30s poll on the room query (adds a periodic request to every open room and still delays notice by up to 30s). The room channel has **no per-recipient filtering** — `_pubsub_fanin` (`shared_kernel/realtime/connection.py:331-338`) delivers every frame to every subscriber including guests — so the payload must carry no room content. An ids-only "refetch me" frame is safe by construction: each client then re-GETs through `_to_out` (`chatrooms.py:212-254`), which re-applies the guest neutralisation per viewer. This is also the first WebSocket publish in `chatrooms.py`, so it establishes a pattern; follow `approval_service.py:183` for the `Publisher(room_channel(...)).emit(...)` shape. |
| Q-3 | How far should F-6 go? | Render an explicit "status unknown" for viewers who receive no event feed, and say why in the panel. Do not widen the push recipients in this dossier. | Chosen over a reverse-resolver in `access.py` fanning out N `user_channel` publishes (there is no "list the users who may read this room's observations" direction today — every helper is principal-in/bool-out, and enumerating admins is unbounded) and over persisting per-turn observer status for the REST poll to carry (analyzing/error/skip are purely client-side transients today; persisting them is a new storage design). The defect being fixed is the affirmative false claim, not the absence of a feed. Real delivery is FU-1. |
| Q-4 | How are the three phases cut? | By blast radius: P1 disclosure and access correctness, P2 observer status truthfulness, P3 example packs and render fidelity. | Chosen over cutting by severity (one phase would span frontend, backend events and pack JSON at once, producing an unreviewable diff) and over cutting by technical layer (F-1 needs backend emit and frontend handler together; splitting it leaves an unverifiable intermediate state). P1 and P2 both edit `useObservations.ts` and `ChatroomView.vue` and must run serially. P3 touches those files only for F-9's one-line enhance wiring and F-16's fallback, so it can run in parallel with either — see §7.1. |
| Q-5 | Are F-12 and F-16 in scope, given F-12 has no user-visible effect today and F-16 is partly established behaviour? | Both are fixed. | F-12 is a three-term condition change plus a test that pins it; leaving it costs nothing now and produces a silent regression the moment a badge is added to the compact-band header toggle (`ChatroomHeader.vue:85-95`). F-16's two live paths (soft-delete, >100 agents) are defended by no dossier — the truncated-id posture elsewhere (`docs/tasks/2026-08-24-agent-readable-live-drafts/spec.md:95-98`) exists to avoid disclosing human identity, which does not apply to an agent the creator bound. |
| Q-6 | Should this depend on `2026-07-19-large-artifacts-silently-dropped`, which is `in-progress` and also edits `turn_engine.py`? | No. Recorded as a rebase note, not a `depends_on`. | The regions are disjoint: that dossier works on `_persist_artifacts` and the kernel descriptor path (`turn_engine.py:1124-1171`, per its §2 and §6), while F-8's only backend touch is the observer emit path at `:3154-3163`/`:3307-3326`. The contract treats an overlap that resolves to two unrelated functions in the same file as not a dependency, and `BOARD.md` records the same call for two earlier `turn_engine.py` pairs. Whoever lands second rebases. |
| Q-7 | Should this depend on `2026-07-07-graphrag-two-axis-redesign` (`approved`)? | No. | It is a blueprint dossier for the graphrag axis; its own board row questions whether its remaining scope is even live. No file it names is in this dossier's touched set. |
| Q-9 | The `chatroom.updated` frame's *existence* is disclosive, not just its payload. What closes it? | Split the audience. The **room channel** carries the frame only when the write moved something a non-creator can actually see; the creator's other sessions are refreshed over their own **user channel** instead. | Raised by a `/code-review` pass after the phase-1 PR was green, and settled inside phase 1 rather than deferred. The frame is emitted only by binding and disclosure writes, so a viewer who receives one and then sees an unchanged DTO **and** an unchanged agent listing has learned that an *invisible* write happened — and in a room with disclosure off the only invisible write is an observer binding, the fact [R28.10] and O-8 exist to withhold. The sharp case is a pure guest: `ws_chatroom` admits one (`can_read` is satisfied by a guest link), so `_to_out`'s careful neutralisation of `observers_present` / `disclose_observers` was being undone by a side channel. Chosen over three alternatives. **Per-recipient filtering on the room channel** does not exist — `_pubsub_fanin` has no notion of a recipient — and building it is a transport change, not a bugfix. **Fanning out per-user publishes** needs a "who may read this room" direction that Q-3 already established does not exist, and enumerating admins is unbounded. **Accepting it** was the original FU-8 disposition and was rejected once the guest case was understood. The chosen split costs nothing on four of the five sites (the room is already in `access.chatroom` or in hand) and one facade call on the fifth. Its residual is recorded in §9. |
| Q-8 | Do the F-4/F-5 prompt corrections need a data-repair step for already-installed packs? | No migration. Correct the JSON and extend the upgrade note in `docs/examples/creative-thinking-course.md`. | Install copies `system_prompt` into an `agents` row (`example_service.py:251-264` → `agent_service.py:669`) and is idempotent **by agent name within the project** (`example_service.py:233,244-247`), with no update path and no pack version column (`catalogue.py:42` `_PACK_FIELDS` has no version field; `tables.py:17-24` has no `pack_key`). The project has already met this exact situation twice and documented the answer both times — `docs/examples/creative-thinking-course.md:344-347` and `:627-636`: edit the prompt by hand, or delete that agent and re-install. Inventing a migration now would contradict a stated posture for a one-agent text change. |

## 4. Reproduction

Each phase's findings reproduce independently. Preconditions common to all: a project with
an agent bound to a chatroom as `observer`, and a provider key group the observer can use.

**F-1.** Two browsers. A: the room creator on the chatroom. B: an ordinary project member
on the same chatroom, tab kept focused. Creator binds an observer from Chatroom Settings.
B's header shows no `ObserverDisclosureChip` for as long as B does not blur the window.
Blur and refocus B: the chip appears. Symmetric on unbind.

**F-2.** As creator, enable `allow_guest_links`, bind an observer, leave `disclose_observers`
at its default (on). The settings page shows no guest callout. Open the guest link in a
private window: no chip. Turn disclosure off: the callout appears, describing a state the
guest experiences identically.

**F-3.** Requires a room with `created_by_user_id IS NULL` (a pre-0041 room, or set the
column to NULL directly on a test row). Sign in as an org owner who holds no
`project_members` row for that project. `GET /api/chatrooms/{id}/observations` returns 200
via curl; the UI shows no Observer tab.

**F-7.** As creator with observations present, block `GET /api/chatrooms/*/observations`
at the network layer (devtools request blocking) and reload. The panel reads "No
observations yet".

**F-10.** Creator with the room open in tab A. In tab B, bind an observer from settings and
let it produce one observation. Tab A, kept focused, shows the Observer tab with an empty
roster and the "no observer currently bound" alert.

**F-6.** As a platform admin, open a room owned by another user whose bound observer fails
every turn (bind an observer whose key group cannot serve its model, producing
`key_group_scope` at `turn_engine.py:2523-2526`). The roster shows "idle".

**F-8.** Not reliably reproducible by hand — it needs a terminal frame to be lost.
Deterministic proxy: with the panel open, call `store.setObserverAnalyzing(roomId, agentId,
true)` and never deliver a terminal event; the status never clears. The production trigger
is a worker kill between `turn_engine.py:2607` and `:3155`, or a socket gap over the
terminal frame; nondeterminism is in the timing of the kill relative to the emit.

**F-11.** As an admin (30s poll active), throttle the network so a poll response resolves
slowly, then release an observation while that request is in flight. The Release control
reappears when the poll resolves.

**F-12.** Resize to 1100px, open People, switch to the Observer tab, close the overlay with
Escape. `unreadCount` stays 0 as observations arrive — observable today only in devtools,
which is why §10 pins it by unit test rather than by eye.

**F-13.** With the panel closed, background the tab until the socket drops, produce an
observation, then return. The row appears; the tab badge does not increment.

**F-14.** Creator in two tabs. Delete an observation in A. In B, immediately click delete on
the same row: a bare "delete failed" toast, row still present until the focus-refetch lands.

**F-4.** Install `creative-thinking-room` into a project running the `creative-thinking`
course, bind AA as observer in a room with a `mandala-9grid` activity, and trigger its
silence condition. AA's `present_observation` call is rejected with `Additional properties
are not allowed ('basis' was unexpected)`; the panel shows prose with no grid.

**F-5.** Install `creative-thinking-design`, ask DA to draft a TA prompt for a Mandala unit.
The drafted prompt lists `mandala-9grid` as quotable.

**F-9.** Have an observer emit a `prose` block containing a ```` ```mermaid ```` fence. The
panel shows raw fence text; release it and the feed renders a diagram.

**F-15.** Author a nine-field activity type keyed `mandala-9grid` whose `center` property
carries `x-order: 1`. The participant form puts `center` in the middle box; the observer's
`mandala_grid` block puts it top-left.

**F-16.** Soft-delete the observer agent that produced an observation. Its card is headed by
an eight-character hex id.

## 5. Root Cause Analysis

Four distinct root causes account for all sixteen findings; the phases are organised around
them.

**RC-1 — The client re-derives answers the server already computed, and re-derives them
from a different source.** F-3 is the direct instance: `is_moderator` is on the DTO
(`chatrooms.py:253`) precisely because an org owner moderates without a `project_members`
row, and `useObservations.ts:63-71` scans `project_members` anyway. The same shape produces
F-2, where the client decides when a guest is unnotified using a room flag the backend
overrides per viewer at `chatrooms.py:238-240`. The earliest link whose correction prevents
both symptoms is the derivation itself, not the individual conditions.

**RC-2 — The room DTO has no invalidation path.** F-1's causal chain: no writer in
`chatrooms.py` or `chatroom_service.py` publishes anything (verified by grep over both:
audit emits only), so no event can carry a room change; `useChatroomSocket.ts:603-624`
reconciles four things on reconnect and the room is not among them; and every invalidation
in the slice names a key that does not prefix-match `convKeys.chatroom`. Any one of the
three would have masked the others. The root cause is the absent event: the client cannot
invalidate on a signal that is never sent. F-10(c) is the same cause one key over —
`chatroom-agents` has exactly one reference in the whole frontend and no invalidator.

**RC-3 — Observer status is a client-side transient with no floor and no feed for
non-creators.** F-8, F-6 and F-13 are three faces of it. The store holds
`observerAnalyzing`/`observerErrors`/`observerSkips` written only by WS handlers
(`useObservations.ts:150-182`), so the status is exactly as reliable as the socket. The
room path solved the same problem twice — a watchdog and a reconnect reset
(`useChatroomSocket.ts:39,327-346,613`) — and neither was carried across. For viewers who
receive no events at all, the absence is additionally rendered as a positive claim, because
`:85` uses `'idle'` as its fall-through rather than a distinct unknown state.

**RC-4 — Shipped prompt text and the schema it must satisfy have no shared guard.** F-4 and
F-5 are both prompt text contradicting a platform rule, and in both cases a test exists
that would have caught it but does not cover the agent in question:
`test_agent_example_packs.py:335-356` is parametrized over three of the four shipped agents,
and no test at all asserts the `basis` sentence against the block schema. F-15 is the same
class one layer down — `observation_aggregates.py:141-143` states the participant-order
contract in a docstring and nothing enforces it.

The remaining findings are local defects rather than instances of a systemic cause: F-7 (no
error state), F-9 (a second render root never wired), F-11 (a missing invalidate), F-12 (a
breakpoint term), F-14 (a missing event and an unhandled status), F-16 (a fallback that
outlived its justification).

## 6. Blast Radius and Sibling Suspects

**Blast radius.** F-1 and F-2 affect every participant and guest of every room with an
observer, which is the entire classroom flow the example packs exist for; they are notice
defects, so the harm is to people who never see the surface. F-3 affects org owners on
pre-0041 rooms and any project whose member list exceeds 100 rows. F-4 affects every
install of the flagship pack. F-5 affects every prompt drafted through the designer, so its
blast radius grows with use. F-6 through F-16 affect creators and admins, whose harm is
wrong information rather than lost access. No finding writes bad data: the only persisted
artefacts in this area are observation rows, and none of the defects corrupts one, so **no
data repair is required**.

**Sibling suspects.**

- **Other consumers of the member-list derivation (RC-1)** → **confirmed, both in scope.**
  `useObservations.ts:63-71` and `ChatroomSettingsView.vue:201-209` are the only two sites;
  `useProjectRole.ts:37` and `ChatroomView.vue:742` already read the server's answer.
- **Other unpaginated `listMembers`/`listProjectAgents` calls** → **confirmed, two live.**
  `useObservations.ts:55` (F-3's second path) and `conversation/api/index.ts:148-150`
  (F-16's second path). Both in scope.
- **Other singular query keys with no invalidator** → **`chatroom-agents` confirmed**
  (F-10(c), in scope). A sweep of every key in `convKeys` for orphaned invalidation is
  deliberately not attempted here; recorded as FU-2.
- **Other WS-only client state with no watchdog (RC-3)** → **cleared for the room path**
  (`agentThinking` has both guards at `useChatroomSocket.ts:327-346,613`); **confirmed for
  the observer path** (F-8, in scope). The activities store's per-room state
  (`slices/activities/stores/activities.ts:232`) is reset on room change like the
  conversation store and has its own resync at `useChatroomSocket.ts:617`, so it is
  cleared.
- **Other optimistic cache writes without an invalidate (F-11's pattern)** → **cleared
  within the observer surface.** `remove()` invalidates (`useObservations.ts:236`);
  `patchReleased` on the WS path (`:178-181`) is server-sourced and needs none.
- **Other block kinds whose prompt guidance could contradict the schema (RC-4)** →
  **`prose`, `key_points` and `timeline` cleared**: the first has no `basis` property and
  the prompt exempts it; the other two require `basis` (`observation_blocks.py:213,218,250,255`)
  and the prompt supplies it. Only the two computed branches are wrong.
- **Other pack prompts against the retired quoting rule (F-5's pattern)** → **the three
  room agents cleared** by the existing parametrized guard; **DA confirmed**. There are
  four shipped agents in two packs, so the sweep is complete.
- **Other nine-field activity types affected by F-15** → **cleared in shipped content**
  (`creative-thinking.json:40-44` gives `center` `x-order: 5`). The defect is reachable only
  through project-authored types.
- **Other `v-html` sites missing the enhance pass (F-9)** → **confirmed and in scope**:
  `ObservationCard.vue:28-31` and `:39-44`. `ObservationBlocks.vue` and the six
  `Obs*Block.vue` files contain no `v-html` at all, so the two card bindings are the
  complete set.

## 7. Fix Design

### 7.1 The phase contract

Three phases, each ending in a commit that passes the full Definition of Done. A phase is
not started before its predecessor is committed where §7.1's ordering says so.

| Phase | Findings | Files (primary) | Ordering |
|---|---|---|---|
| P1 — Disclosure and access correctness | F-1, F-2, F-3, F-7, F-10 | `chatrooms.py`, `chatroom_service.py`, `useChatroomSocket.ts`, `useObservations.ts`, `ChatroomView.vue`, `ChatroomSettingsView.vue`, `ObserverPanel.vue`, both conversation locale files | First. |
| P2 — Observer status truthfulness | F-6, F-8, F-11, F-12, F-13, F-14 | `useObservations.ts`, `ChatroomView.vue`, `ObserverPanel.vue`, `stores/conversation.ts`, `observations.py`, `types/index.ts`, both locale files | After P1: it edits the same regions of `useObservations.ts` and `ChatroomView.vue`. |
| P3 — Example packs and render fidelity | F-4, F-5, F-9, F-15, F-16 | `packs/*.json`, `test_agent_example_packs.py`, `observation_blocks.py` (prompt-facing descriptions only), `observation_aggregates.py`, `ObservationCard.vue`, `useObservations.ts` (one line), `conversation/api/index.ts`, `docs/examples/creative-thinking-course.md` | May run in parallel with P1 or P2. Its only shared files are one line of `useObservations.ts` (F-16's fallback) and `ObservationCard.vue`, which neither other phase edits. Whoever lands second rebases. |

`/build` moves this dossier to `in-progress` when P1 starts and to `implemented` only when
all three phases are done; the AC checkboxes in §10 are how a resumed session knows where
work stopped.

### 7.2 Phase 1

**F-1.** Add `chatroom.updated` on the room channel, carrying `{"chatroom_id": ...}` and
nothing else. Emit it from the four writers that can change a viewer-visible room field:
`add_chatroom_agent` (`chatrooms.py:676-718`), `patch_chatroom_agent_role` (`:721-746`),
`remove_chatroom_agent` (`:872-905`), and the `patch_chatroom` disclosure path. Follow the
`Publisher(room_channel(...)).emit(...)` shape used at
`contexts/orchestration/application/approval_service.py:183`; this is the first publish in
`chatrooms.py`, so the import lands there for the first time. On the client, subscribe in
`useChatroomSocket.ts` alongside the existing handlers and invalidate
`convKeys.chatroom(roomId)` **and** `['conversation','chatroom-agents',roomId]` — the
second key is F-10(c)'s fix and comes free from the same event. Emit after the write
commits; the `db_session` dependency commits at `shared_kernel/db/session.py:111-131`, so
the emit must not sit inside the request handler before that point where a rollback could
still occur. It does not correct the symptom to invalidate on reconnect only, because the
reported failure is a healthy connection.

**F-2.** Change the callout condition to `allow_guest_links && hasObserver`
(`ChatroomSettingsView.vue:216-221`) and rewrite `conversation.guestObserverCallout` in both
locales so it states the actual guarantee: external guests are never shown the observer
indicator, whatever the disclosure setting says. The backend is correct and is not touched.

**F-3.** Replace both member-list derivations with the DTO's own answer. `isCreator` becomes
`me.is_admin || room.created_by_user_id === me.id || (room.created_by_user_id === null &&
room.is_moderator)`. Delete `membersQuery` and its `projectsApi.listMembers` import from
`useObservations.ts` (`:53-61`), which also removes F-3's pagination path, and apply the
same substitution at `ChatroomSettingsView.vue:201-209`. This deletes code rather than
adding a special case, which is why it corrects the cause rather than the symptom.

**F-7.** Return `isError` and `refetch` from `useObservations`, pass them into
`ObserverPanel`, and render `SQueryError` (`shared/ui/SQueryError.vue:1-28`, exported at
`shared/ui/index.ts:29`) in place of the empty state when the query errored. Follow the
`isError → SQueryError → refetch` chain already used at
`slices/notifications/views/NotificationsView.vue:28-33` and
`slices/admin/components/ActivityExamplesSection.vue:10-24`.

**F-10.** Two halves. The event from F-1 fixes trigger (c). For triggers (a) and (b), gate
the alert on knowing the roster: `ObserverPanel` takes a `rosterKnown` prop (the bound-agents
query having settled successfully) and renders the alert only when it is true. Do not simply
move the alert below the `loading` block — that block gates the observations list, not the
roster, and the two queries settle independently.

### 7.3 Phase 2

**F-6.** Add `'unknown'` to `ObserverEntry['status']` (`useObservations.ts:31-37`). A viewer
who receives no observation events — everyone except the literal creator, i.e.
`session.me?.id !== room.created_by_user_id`, the same predicate the W-1 `refetchInterval`
already computes at `:110-111` — gets `'unknown'` as the fall-through instead of `'idle'`.
Add the label and a row tooltip in both locales explaining that live status is delivered only
to the room's creator. The predicate must be the event-delivery predicate, not the
authorization one: an admin who *is* the creator still gets live status.

**F-8.** Port the room path's two guards. Register `onStatus` on the user channel inside the
existing `watch` (`useObservations.ts:141-187`) and, on `connected === true`, clear the
room's `observerAnalyzing` set and refetch the observations query — mirroring
`useChatroomSocket.ts:613`. Add a watchdog on the same shape as `armThinkingTimeout`
(`useChatroomSocket.ts:320-346`): armed on `observation.started`, disarmed by any terminal
event, and on fire clearing the analyzing flag and setting an error kind of `timeout`, which
`constants/agentErrors.ts:1-8` already defines as the client-side watchdog kind. Note the
additive-subscriber rule documented at `useObservations.ts:3-8`: subscribe and `connect()`
only, never `close()`, because `useBanKickGuard` owns that channel's lifecycle.

**F-11.** Follow `remove()`: after `patchReleased`, `void qc.invalidateQueries({ queryKey:
convKeys.observations(chatroomId) })`. The optimistic patch stays — it is what keeps the UI
instant — and the invalidate is what makes the server the last writer.

**F-12.** Change the term to `railTab === 'observer' && ((isDesktop.value &&
!isCompactDesktop.value) || peopleDrawerOpen.value)` (`ChatroomView.vue:654-657`).

**F-13.** Increment `unreadCount` from the reconnect reconcile added for F-8 when the refetch
returns rows newer than the newest one already rendered, not only from the WS handler. The
comparison must be against what the panel has shown, not against the previous page contents,
or a focus-refetch that merely re-serves known rows would inflate the badge.

**F-14.** Two halves. Emit `observation.deleted` on the creator's user channel from
`app/api/v1/observations.py:167-185`, mirroring the release emit at `:249-261`, and handle it
in `useObservations` by filtering the row out of the cache — the same shape as `patchReleased`.
Then branch the two catch sites on 404: `ChatroomView.vue:710-715` (delete) and `:681-700`
(release) both refetch the list and report that the observation no longer exists, rather than
falling through to the generic failure message. Add the event to the union at
`types/index.ts:160` and to the event table in `docs/observer-agents/00-overview.md:151-156`.

### 7.4 Phase 3

**F-4.** Rewrite the `basis` sentence in `packs/creative-thinking-room.json:80` so it covers
`key_points` and `timeline` only, and states that computed blocks receive their basis from
the server. The prompt edit must keep satisfying every existing string assertion over AA:
`test_agent_example_packs.py:530-558`, `:402`, `:430`, `:454`, `:469`, plus the all-agent
parametrized assertions at `:234-309`. Additionally, make the schema say so: extend the
`description` on the two computed branches (`observation_blocks.py:275-290`, `:293-314`) to
state that basis is stamped server-side, so a model reading the tool schema is told the same
thing the prompt says. Do **not** add `basis` to the computed branches — that would let an
agent assert a provenance it did not earn, which is the property
`docs/examples/creative-thinking-course.md:787-791` exists to guarantee.

**F-5.** Correct DA's constraint list at `packs/creative-thinking-design.json:27` to the
current per-type rule, and add `six-hats-shared-case` to both the quoting clause and
`binds_activity_types` (`:21-26`). Then close the guard gap that let it drift: extend the
parametrization at `test_agent_example_packs.py:335-356` and `:358-364` from the three room
agents to every agent in every shipped pack, which is what its own docstring at `:324-327`
already argues for. Add the upgrade note to `docs/examples/creative-thinking-course.md`
following the two existing precedents at `:344-347` and `:627-636` — an existing install
keeps the old text; edit by hand or delete the agent and re-install. No migration (Q-8).

**F-9.** Call `useMarkdownEnhance` a second time in `ObservationCard.vue` against a root ref
covering its two `v-html` regions. The composable holds no per-instance global state and
re-schedules on `onUpdated` (`useMarkdownEnhance.ts:52-56`), and all three passes are
idempotent by consumption (`renderMarkdown.ts:112-134,137-155,158-182`), so a second call
site is safe. It must be called during setup, and its root ref must be non-null by the time
the 120ms debounce fires.

**F-15.** Make the aggregate honour the same centre rule as the participant form: in
`observation_aggregates.py:137-166`, after the `x-order` sort, splice a property named
`center` to index 4 exactly as `MandalaGrid.vue:41-53` does. Then correct the docstring at
`:141-143` so it describes what the code now guarantees rather than what it assumed. The
alternative — tightening the nine-property gate at `observer_tools.py:155-158` to require a
`center` property at a fixed order — was rejected because it would reject nine-field types
that render fine today.

**F-16.** Give the fallback a name. Where `agentNames` has no entry, render the label from
`conversation.observers.unknownAgent` in both locales rather than an id prefix, and pass the
page size on `listProjectAgents` (`conversation/api/index.ts:148-150`) so the >100 path stops
being reachable at all. The truncated id stays only where it is genuinely a mid-load
placeholder, per `ChatroomView.vue:498-501`.

**Data repair plan.** None. §6 establishes that no finding writes bad data. The only
already-persisted artefact this dossier's corrections do not reach is the `system_prompt`
copied into `agents` rows by earlier installs of the two packs, which Q-8 resolves by
documentation rather than migration, following the project's twice-stated precedent.

## 8. Regression Test Plan

Every phase is test-first: the listed test is written, observed failing against current
code, and only then is the fix applied.

### Phase 1

| Test | File | Asserts | Fails today because |
|---|---|---|---|
| T-1 | `backend/tests/unit/test_observer_agents.py` | Binding an observer, flipping its role, unbinding it, and patching `disclose_observers` each publish `chatroom.updated` on the room channel with the room id and no other room content. | No publish exists in `chatrooms.py`. Note this deliberately narrows `test_observer_turn_emits_nothing_on_room_channel` (`:1341`), which pins the absence for observer **turns**; that test stays and the new one covers writes. |
| T-2 | `frontend/src/slices/conversation/__tests__/useChatroomSocket.test.ts` | A `chatroom.updated` frame for the current room invalidates both `convKeys.chatroom` and the `chatroom-agents` key, and a frame for another room invalidates neither. | No handler exists. |
| T-3 | `frontend/src/slices/conversation/__tests__/ChatroomSettingsView.test.ts` | The guest-observer callout renders whenever guest links and an observer coexist, in both disclosure states. | The condition requires `!disclose_observers`. |
| T-4 | `frontend/src/slices/conversation/__tests__/useObservations.test.ts` | On a room with `created_by_user_id: null` and `is_moderator: true`, `isCreator` is true and no member-list request is made. | `isCreator` requires a `project_members` owner row. |
| T-5 | `frontend/src/slices/conversation/__tests__/ObserverPanel.test.ts` | A failed observations query renders `SQueryError` with a retry, not `SEmptyState`. | The panel has no error branch. |
| T-6 | `ObserverPanel.test.ts` | The "no observer currently bound" alert does not render while the bound-agents query is unsettled or errored, and does render when the roster is known empty with observations present. | The alert is gated only on roster length. |

### Phase 2

| Test | File | Asserts | Fails today because |
|---|---|---|---|
| T-7 | `useObservations.test.ts` | For a viewer who is not the room's creator, every roster entry reports `unknown`; for the creator with no events, `idle`. | Both fall through to `idle`. |
| T-8 | `useObservations.test.ts` | An `onStatus(true)` after an `observation.started` with no terminal event clears the analyzing flag and refetches. | No `onStatus` registration exists. |
| T-9 | `useObservations.test.ts` | With fake timers, an `observation.started` with no terminal event within the watchdog window clears analyzing and sets the `timeout` error kind. | No watchdog exists. |
| T-10 | `useObservations.test.ts` | `release()` invalidates the observations query, so a stale in-flight response cannot revert the released row. | `release()` only calls `setQueryData`. |
| T-11 | `ChatroomView.test.ts` | At 1100px with the People overlay closed and the Observer tab selected, `setPanelOpen` is not called with `true`; opening the overlay calls it. | `isCompactDesktop` is inside `isDesktop`. |
| T-12 | `useObservations.test.ts` | A reconnect refetch that returns a row newer than any rendered increments the unread count; one that returns only known rows does not. | Only the WS handler increments. |
| T-13 | `backend/tests/unit/test_observer_agents.py` | `DELETE` on an observation emits `observation.deleted` to the creator's user channel. | The route emits nothing. |
| T-14 | `ChatroomView.test.ts` | A 404 from delete or release refetches the list and reports that the observation no longer exists, and closes the release dialog. | Both catches fall through to a generic message. |

### Phase 3

| Test | File | Asserts | Fails today because |
|---|---|---|---|
| T-15 | `backend/tests/unit/test_agent_example_packs.py` | AA's prompt does not instruct a `basis` on any computed block kind, expressed against the kinds `observation_blocks.py` actually declares rather than a hardcoded list. | The prompt says every kind but `prose`. |
| T-16 | `backend/tests/unit/test_observation_blocks.py` | A block array carrying `basis` on a computed block is rejected by `schema_violations`, and the same array without it is accepted — pinning the contract the prompt must describe. | Passes today; it is written to make the prompt's error legible and to protect the fix. |
| T-17 | `test_agent_example_packs.py` | The retired-quoting-rule assertion and the group-task binding assertion are parametrized over **every** agent in every shipped pack. | DA is excluded and carries the retired rule. |
| T-18 | `frontend/src/slices/conversation/__tests__/ObservationCard.test.ts` | The card's rendered root is passed to the markdown enhancement pass on mount and after an update. | No enhance call site exists. |
| T-19 | `backend/tests/unit/test_observation_aggregates.py` | For a nine-field type whose `center` carries a non-fifth `x-order`, the emitted grid places `center` at index 4 and preserves the remaining eight in declared order. | The aggregate uses declared order only. |
| T-20 | `ObserverPanel.test.ts` | An observation whose agent id is absent from `agentNames` renders the localized unknown-agent label, not an id prefix. | The fallback slices the id. |

## 9. Risks and Rollback

**The new room-channel event is the only change with a delivery surface (P1).** The room
channel fans out to every subscriber with no per-recipient filtering
(`shared_kernel/realtime/connection.py:331-338`), so the risk is a payload carrying more
than an id. §7.2 and T-1 both constrain it to the room id; T-1 asserts the absence of other
fields rather than the presence of the id alone, which is what makes the constraint
enforceable. A second, milder risk is invalidation storms: a settings page that writes
several fields in sequence would emit several frames. Each frame costs one cached GET per
viewer, and TanStack deduplicates concurrent refetches of the same key, so the amplification
is bounded by viewer count, not by write count.

**The frame's existence is disclosive, and one sliver of that is accepted (P1).** Q-9
splits the audience so the room channel is silent whenever a write is invisible to
non-creators, which closes the observer-existence oracle: binding, unbinding or granting
draft access in a non-disclosing room now produces no room-channel frame at all. What it
does **not** close is a `disclose_observers` toggle. That frame must reach the room —
flipping the flag is exactly what moves `observers_present` for every member, and
announcing it is the disclosure — but a **pure guest** sees `disclose_observers` and
`observers_present` forced false in both states, so for them it is a frame with no
observable delta. A guest can therefore learn that *the room's observer-disclosure setting
was changed*. That is materially weaker than what FU-8 originally described: it does not
say in which direction, does not say an observer exists (a room may disclose with none
bound), and does not distinguish itself from a `disclose_drafts` toggle, which is visible
to guests anyway. Closing it too would require withholding the frame from members who are
entitled to it, or per-recipient filtering the transport does not have. Accepted, and the
`patch_chatroom` call site carries a comment pointing here so a later reader does not
mistake it for an oversight.

**F-3 changes an authorization-adjacent client gate (P1).** Widening `isCreator` cannot grant
access — every endpoint re-checks server-side (`access.py:439-463`) — so the failure mode is
a surface rendered for someone whose requests then 403, which T-4 does not cover. The
mitigation is that the new predicate is strictly the server's own published answer, so the
two can only disagree if the DTO itself is wrong.

**F-8's watchdog can fire on a slow-but-healthy observer (P2).** An analysis legitimately
longer than the timeout would be reported as `timeout` while still running, and a later
`observation.created` would correct it. The room path accepts the same trade at 120s
(`useChatroomSocket.ts:39`); matching that constant keeps the two surfaces explicable
together rather than inventing a second number.

**F-15 changes an already-published figure's layout (P3).** For the shipped course the change
is a no-op by construction (`center` is already fifth), so only project-authored types move —
and for those, the current layout is the wrong one. Observations already stored are not
re-rendered from the aggregate: the block is materialised at turn time
(`observation_blocks.py:342-388`), so historical cards keep whatever they were drawn with.

**F-4 and F-5 reach only future installs (P3).** Q-8 records why; the risk is that an operator
reads the corrected documentation and assumes their existing install was fixed. The upgrade
note must be explicit that it was not, matching the wording already used at
`docs/examples/creative-thinking-course.md:344-347`.

**Rollback.** Each phase is one commit and reverts independently. P1's revert restores the
stale chip and the member-list derivation; it does not strand data, because the new event has
no persisted counterpart. P2 and P3 revert to purely client-side and text-level prior states.
Only T-1's narrowing of the room-channel silence assertion touches an existing test's meaning;
reverting P1 restores it with the test.

## 10. Acceptance Criteria

### Phase 1

- [x] AC-1: T-1 through T-6 each fail against current code and pass after the phase.
      Observed: the six backend cases all failed with
      `AttributeError: module 'app.api.v1.chatrooms' has no attribute 'Publisher'`
      (the finding itself — no publish existed), and the frontend six failed on the
      member-list scan, the inverted callout condition and the two missing panel branches.
- [ ] AC-2: With two browsers on one room, binding and unbinding an observer updates the
      non-creator's disclosure chip without a reload or a window blur, in both directions.
      **Not executed** — needs a running stack and two sessions; Docker was unavailable in
      the build session. Not ticked on the strength of T-1 plus T-2, which verify the two
      halves separately and never that the frame crosses between them.
- [x] AC-3: The room-channel frame emitted by every one of the four writers contains the room
      id and no other room field, verified by reading the frame, not only by T-1.
      The emit is a single literal `{"chatroom_id": str(chatroom_id)}` in one helper all four
      writers call, and T-1 asserts payload **equality** rather than the id's presence, so a
      later field addition fails the test rather than passing it.
- [x] AC-4: With guest links enabled and an observer bound, the settings callout renders with
      disclosure both on and off, and its copy in both locales states that guests never see the
      indicator. T-3 is parametrized over both disclosure states; both locale strings were
      rewritten to say the guarantee ("never shown the observer indicator, whatever the
      disclosure setting says") instead of the old "while disclosure is off".
- [ ] AC-5: An org owner holding no `project_members` row opens a NULL-creator room and reaches
      the Observer tab, the disclosure toggle and the observer role selector. Executed against a
      real stack with a NULL-creator row, or left unticked.
      **Not executed, deliberately unticked** — it needs a `created_by_user_id IS NULL` row and
      an org owner with no `project_members` row, which cannot be built without a live database.
      T-4 covers the predicate, not the round trip.
- [x] AC-6: `projectsApi.listMembers` is no longer imported by `useObservations.ts` or
      `ChatroomSettingsView.vue`. Both imports are gone; `useObservations` no longer imports
      `@slices/tenancy` at all, and T-4 asserts the call is never made rather than only that
      the import is absent.
- [x] AC-7: A blocked observations request renders `SQueryError` with a working retry, and the
      "no observer currently bound" alert is absent while the bound-agents query is unsettled.
      Verified at component level (T-5, T-6) rather than by blocking a live request.

### Phase 2

- [ ] AC-8: T-7 through T-14 each fail against current code and pass after the phase.
- [ ] AC-9: An admin viewing another user's room sees every observer reported as unknown, with
      a tooltip explaining that live status reaches the room's creator only; the creator viewing
      the same room still sees analyzing/error/skipped.
- [ ] AC-10: After an `observation.started` with the socket forcibly dropped and reconnected,
      the roster does not remain on analyzing; after an `observation.started` with no terminal
      event and no reconnect, the watchdog clears it within the timeout window.
- [ ] AC-11: Releasing an observation while a list refetch is in flight leaves the row released
      once both settle.
- [ ] AC-12: Deleting an observation in one session removes it from a second session's panel
      without a reload, and a 404 on delete or release reports that the observation no longer
      exists and refreshes the list.
- [ ] AC-13: `docs/observer-agents/00-overview.md`'s event table lists `observation.deleted`
      alongside the existing five.

### Phase 3

- [ ] AC-14: T-15 through T-20 each fail against current code and pass after the phase.
- [ ] AC-15: AA, installed fresh from the corrected pack into a room with a `mandala-9grid`
      activity, produces an observation containing a rendered `mandala_grid` block. Executed
      against a real stack with a provider key, or left unticked with the reason recorded.
- [ ] AC-16: DA's prompt names the current per-type quoting rule and `six-hats-shared-case`
      appears in both its quoting clause and `binds_activity_types`; the extended
      parametrization covers all four shipped agents.
- [ ] AC-17: `docs/examples/creative-thinking-course.md` states that an existing install keeps
      the old AA and DA prompts and names both remedies.
- [ ] AC-18: An observation containing a mermaid fence, a math fence and a code fence renders
      identically in the Observer panel and in the message feed after release.
- [ ] AC-19: The observer panel shows the localized unknown-agent label, never an id prefix, for
      a soft-deleted agent's stranded observation.

### All phases

- [x] AC-20: `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`, `pnpm test`,
      `pnpm lint`, `pnpm run typecheck` and `pnpm build` pass at the end of each phase, not only
      at the end of the dossier.
      Verified on CI (PR #182, run `33710318491`): `backend-lint`, `backend-typecheck`,
      `backend-test`, `backend-db`, `backend-integration`, `backend-wiring`, `frontend-lint`,
      `frontend-typecheck`, `frontend-test`, `frontend-e2e` and all seven `frontend-gate-*`
      jobs pass. Backend `pytest` was run on CI rather than locally: the local host has no
      Postgres/Redis/Vault, so its `tests/wiring/` tier fails with `socket.gaierror` for
      reasons unrelated to any change. The local unit tier is green at 7910 passed / 6 skipped.

      **One unrelated job is red: `dependency-audit`**, on three `pypdf` 6.15.0 advisories
      (CVE-2026-84309/84310/84311, fixed in 6.16.1) published after `main`'s last green run.
      This dossier touches no dependency manifest and the same failure reproduces on `main`;
      the bump is not phase 1's to make.
- [x] AC-21: Both locale files stay at parity for every key this dossier adds, and no template
      gains a bare string literal (frontend gate #12). Two keys added
      (`conversation.observers.loadError`, `.retry`) and one rewritten
      (`.guestObserverCallout`), all three in `en.json` and `zh-TW.json`; `pnpm lint` passes
      with `--max-warnings=0`, which is where gate #12 runs.
- [ ] AC-22: `findings.md`'s Hand-off table links this dossier for all sixteen findings and its
      status is `closed`.

## 11. SRS Delta

None. Every finding restores behavior [R24.32], [R28.02], [R28.09], [R28.10], [R28.13] or
[R28.16] already documents, or corrects text against documentation that is already correct
(`docs/examples/creative-thinking-course.md`, `observation_aggregates.py`'s own docstring).

One documentation change that is **not** an SRS delta: `docs/observer-agents/00-overview.md`
gains `observation.deleted` in its event table (AC-13). That file specifies the observer
subsystem's event vocabulary, not a requirement, and the addition is part of P2's fix rather
than a change to what the platform must do.

## 12. Deviation Log

### Phase 1

Freshness re-verification found **no drift at all**: the spec was written against `b33d404`
and phase 1 started on that same commit with a clean tree, so every `path:line` in §2 and
§7.2 was re-read and confirmed rather than corrected. No D-n entry exists for a citation.

**D-1 — the emit runs after an explicit `await db.commit()`, which changed five existing
test fixtures.** §7.2 requires the frame to be published after the write commits but does
not say by what mechanism, and there was no reusable one: `flush_tail_events` is bound to
the audit channel and its fixed `audit_event` name, while `db_session` commits on FastAPI's
function exit stack, i.e. only after the handler has returned. The handler therefore has to
commit for itself, which `shared_kernel/db/session.py` explicitly sanctions and which
`patch_chatroom_agent_activity_control`, `patch_chatroom_agent_draft_access` and
`delete_chatroom` already do in this same file. Consequence: five tests that passed
`db=object()` to the four writers now pass a double with an awaitable `commit`. No
assertion was changed.

**D-2 — `chatroom-agents` gained a `convKeys` entry instead of being invalidated by a
literal.** §7.2 names the raw key `['conversation','chatroom-agents',roomId]`. Writing it
by hand a second time would have reproduced the exact condition that hid F-1 and F-10(c) —
a key that looks right at its one call site and matches nothing. `convKeys.chatroomAgents`
is now used by both the query in `ChatroomView.vue` and the new invalidation, so the two
cannot drift. This is a narrower instance of FU-4 and does not close it.

**D-3 — `UseObservationsOptions.projectId` was deleted along with `membersQuery`.** F-3
removes the only consumer of that option; leaving it would advertise a dependency the
composable no longer has. Removed from the interface, from the `ChatroomView.vue` call
site and from the test harness. `observerProjectId` itself stays — `ActivityPanel` uses it.

**D-4 — `remove_chatroom_agent` emits unconditionally, including on the role-scoped
no-op.** Not addressed by §7.2. `remove_agent` does not report whether it matched a row,
and adding that signal would have to be consumed here — which is exactly where O-5
established that a non-creator's unbind of an observer must be indistinguishable from a
successful one. Emitting only on a real deletion would have made the frame's absence the
oracle O-5's silent 204 exists to prevent. The cost of emitting always is one cached GET
per viewer on a no-op nobody but the caller triggered.

**D-7 — `chatroom.updated` has two audiences, where §7.2 specified one.** §7.2 says to
emit on the room channel from four writers. That shape made the frame's *existence* a
signal in rooms with disclosure off (Q-9). The event now takes `room_visible` and
`creator_user_id`: the creator's own other sessions are always refreshed over
`user_channel`, and the room channel is used only when a non-creator can see the
difference — `role is NORMAL or disclose_observers` on bind, `True` on a role patch (which
adds or removes a row from every non-creator's listing in both directions), the target's
role and `disclose_observers` on unbind, `disclose_drafts` on a draft grant, and `True` on
a disclosure patch. Four of the five read the room from `access.chatroom`, which they
already resolve; `remove_chatroom_agent` gains one
`ConversationFacade.agent_role_in_chatroom` call, a facade method that exists for exactly
this reason ("the role is a **disclosure rule**, not just a routing one").

Two consequences worth naming. `add_chatroom_agent` now resolves room access on the normal
path too, where it previously did so only for observer bindings — it needs the disclosure
flag either way. And **the unbind site deliberately does not apply the rule for a
non-creator caller**: there the frame is emitted unconditionally, because a frame that
varied with the target's role would answer "was that an observer?" — reinstating the
oracle O-5's silent 204 exists to prevent. A parametrized test pins that the frame is
identical for both roles.

**D-6 — `patch_chatroom_agent_draft_access` also emits, which §7.2 did not ask for.**
Raised by a `/code-review` pass after the PR was green. §7.2 names four writers, all
chosen for `observers_present`. But `_to_out`'s `drafts_readable` is
`disclose_drafts AND has_draft_readers` ([R32.05]), and this phase put an emit on
`patch_chatroom` — which moves the first term, since `disclose_drafts` is in
`_DISCLOSURE_FIELDS` — while the route that moves the second term had none. That left the
pair half-fresh in a way phase 1 itself introduced: flipping the disclosure would refresh
every participant's "an agent here can read what you are typing" chip, while granting the
reading it discloses would not. Fixed rather than deferred, because the asymmetry is this
phase's own and the fix is one call to the helper the phase already added, with a test.

**D-5 — the F-7 error banner renders beside the cached rows, not instead of them.** §7.2
says to render `SQueryError` "in place of the empty state", which is what shipped; the
first implementation went further and replaced the list as well, and a self-audit caught
that it contradicted its own copy ("the list below may be incomplete or out of date") and
destroyed readable observations to report a transport failure. Only the **empty** state is
suppressed on error, because only its copy asserts a fact a failed request never
established.

## 13. Follow-ups

- **FU-1** — Real observer status delivery to non-creator readers. Q-3 fixes the false "idle"
  claim without building a feed. Doing it properly needs either a reverse resolver in
  `access.py` ("which user ids may read this room's observations") fanning out per-user
  publishes, or a persisted per-turn observer status the existing 30s poll can carry. Both are
  design work, not a bugfix.
- **FU-2** — A sweep of every `convKeys` entry for orphaned invalidation. `chatroom` and
  `chatroom-agents` were both found orphaned by this audit; the audit did not check the rest,
  and the same defect in another key would look exactly like working code.
- **FU-3** (route to `check-quality`) — `useChatroomBindings.ts:155-187` writes bindings through
  raw API calls into local refs while the same data lives in the TanStack cache under
  `chatroom-agents`. P1 makes the cache correct; the two-sources-of-truth pattern remains.
- **FU-4** (route to `check-quality`) — `useChatroomSocket.ts` writes the messages query key as
  a raw literal at `:123,136,280,309,681` rather than through `convKeys.messages`. This is the
  same class of hazard that hid F-1 (a key that looks right and does not match) and is worth
  closing across the slice.
- **FU-5** — No `frontend/e2e` spec covers the observer surface at all; the only match for
  "observ" in that tree is an unrelated comment (`e2e/fixtures/auth.ts:23`). Several of this
  dossier's ACs are manual for want of one.
- **FU-7** (opened by phase 1) — `patch_chatroom` emits `chatroom.updated` only when the
  patch names a disclosure field, so a **rename** still goes stale on every live viewer
  until reload. §7.2 scoped the emit to the disclosure path and this build did not widen
  it, because the access flags and the name were never analysed as part of F-1's blast
  radius. Widening is a one-line change (`if fields` in place of
  `if fields & _DISCLOSURE_FIELDS`) plus a test; the reason to think before making it is
  that a settings form writing several fields in sequence would then emit several frames.

- ~~**FU-8**~~ — **resolved inside phase 1, not deferred.** See Q-9 for the decision and
  D-7 for what was built. The residual signal it does not close is recorded in §9 as an
  accepted risk rather than as outstanding work.

- **FU-6** — Record which pack revision an installed agent came from. Q-8's answer is correct
  under today's design, but the reason there is no update path is that `agents` has no
  `pack_key` or version column (`tables.py:17-24`, `catalogue.py:42`), so nothing can even
  identify which installed agents a corrected pack affects. This is the third time a pack text
  correction has shipped with a hand-edit note.
