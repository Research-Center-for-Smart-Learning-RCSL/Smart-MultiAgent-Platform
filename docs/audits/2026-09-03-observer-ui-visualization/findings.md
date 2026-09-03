---
type: audit
status: closed
created: 2026-09-03
requirements: [R28.02, R28.09, R28.10, R28.13, R28.16, R24.32]
---

# Audit: Observer UI visualization in the runtime environment

## 1. Scope

**Area.** The full observer (觀察者) chain as it reaches the user's eyes at runtime, plus
the relationship between the shipped example agent packs and that surface:

- Frontend `conversation` slice: `ObserverPanel.vue`, `ObservationCard.vue`, the six
  `Obs*Block.vue` presentation blocks, `ObserverDisclosureChip.vue`, `useObservations.ts`,
  `useTransientSurfaces.ts`, `useChatroomBindings.ts`, `ChatroomView.vue`,
  `ChatroomSettingsView.vue`, `useChatroomSettings.ts`, `stores/conversation.ts`,
  `constants/agentErrors.ts`, both locale files.
- Frontend `activities` slice, only where it renders the same data the observer figure
  claims to summarise (`plugins/mandala9grid/MandalaGrid.vue`, `schemaFields.ts`).
- Backend: `contexts/conversation` (access, observation service and repository, facade,
  error mapping), `contexts/agents/application/runtime` (`turn_engine.py`,
  `observer_tools.py`, `observation_blocks.py`, `tool_registry.py`),
  `contexts/activities/application/observation_aggregates.py`, `app/api/v1/observations.py`,
  `app/api/v1/chatrooms.py`, `shared_kernel/realtime`.
- Example content: every pack under `contexts/agents/infrastructure/examples/packs/`, the
  `creative-thinking` course JSON, and `docs/examples/creative-thinking-course.md`.

**Intent sources.** `REQUIREMENTS.md` §28 (Observer Agents, R28.01-R28.19) and its
`docs/traceability.csv:313-331` rows; `docs/UI/07-conversation.md:238-256,775`;
`docs/observer-agents/00-overview.md`; and the dossiers
`docs/tasks/2026-08-30-chatroom-approval-and-overlay-discoverability/spec.md`,
`docs/tasks/2026-08-24-agent-readable-live-drafts/spec.md`,
`docs/tasks/2026-08-24-observer-presentation-blocks/spec.md`,
`docs/tasks/2026-07-22-observation-binding-cleanup/spec.md`, and
`docs/examples/creative-thinking-course.md`. Intent coverage for this area is unusually
good, so most findings below are deviations from documented behavior rather than internal
inconsistencies.

**Depth.** Thorough. Five investigation lenses (state and lifecycle; realtime, concurrency
and cache desync; example packs versus the render path; boundary inputs and error paths;
documented intent versus behavior) produced 21 raw candidates, deduplicated to 16, each of
which then went through one adversarial verification round whose explicit task was to
refute it. Verification was static: no stack was launched, and no finding below was
reproduced against a running system.

## 2. Coverage

Read in full: every file named in §1 under the frontend `conversation` slice, the observer
runtime path in `turn_engine.py`, `observation_blocks.py`, `observer_tools.py`,
`tool_registry.py`, `observation_service.py`, `observation_repo.py`, `access.py`,
`app/api/v1/observations.py`, the observer sections of `app/api/v1/chatrooms.py`, all
example packs, and the observer chapters of `docs/examples/creative-thinking-course.md`.

Sampled rather than read in full: `shared_kernel/realtime` (read for the socket close and
pub/sub-replay questions only), `frontend/src/shared/transport/ws-manager.ts` (reconnect
and heartbeat paths only), the `activities` slice (only the mandala plugin and the schema
field ordering), and `backend/tests/` (searched for tests pinning specific behaviors, not
audited as a body of work).

Not covered, and therefore not claimed clean:

- **Runtime verification.** Nothing here was reproduced against a live stack. F-7 and F-15
  in particular depend on runtime timing (`refetchOnWindowFocus` behaviour across two
  side-by-side browser windows could not be settled statically because `node_modules` is
  not installed in this tree, so `@tanstack/vue-query@5.102.8`'s focus-manager listener set
  was never read).
- **Visual and layout correctness.** No screenshots, no rendered output, no CSS audit
  beyond the specific compact-band rules cited in F-12. Colour, contrast, spacing,
  responsive behaviour and accessibility of the observer panel were not judged.
- **Security and authorization as such.** Cross-tenant leakage was examined only where it
  changes what the UI displays; a proper AuthZ sweep belongs to `check-security`.
- **Structural quality.** Layer boundaries, duplication and component decomposition in the
  observer path were not judged; that is `check-quality`'s remit.
- **Observer prompt quality.** Whether the example agents produce *good* observations was
  not assessed, only whether the platform can render what they emit.
- **Locale parity beyond the observer keys.** The `agents` and `conversation` observer keys
  were checked in both locales and are complete; other slices were not checked.

## 3. Findings

Ordered by severity. Never renumber.

## F-1: `observers_present` never refreshes in a live room, so a participant can be observed for a whole session with no indicator

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/views/ChatroomView.vue:480-484` (room
  fetched once under `convKeys.chatroom`), `frontend/src/slices/conversation/queries/index.ts:19`
  (`['conversation','chatroom',id]`) versus `:16` (`['conversation','chatrooms',wsId]`);
  every invalidation in the slice targets the plural key or the messages key
  (`useChatroomSettings.ts:187,208,317`; `ChatroomListView.vue:156,202`;
  `useChatroomSocket.ts:123,136`), and TanStack matches key elements exactly, so the
  singular key is never invalidated anywhere in the slice; no room-level WS event exists
  (`useChatroomSocket.ts:351-599` handles no room or settings event; `chatroom.updated` is
  an audit action only, `contexts/conversation/application/chatroom_service.py:254`;
  `backend/app/api/v1/chatrooms.py` contains no publish call); reconnect reconciles
  messages, presence, activation and approvals but not the room
  (`useChatroomSocket.ts:603-624`); the chip consumes the stale value at
  `ChatroomView.vue:25` → `ChatroomHeader.vue:34,155`.
- **Failure scenario**: a participant keeps the room tab focused and chats continuously.
  The creator binds an observer mid-session, or flips disclosure on. The participant's
  header shows no `ObserverDisclosureChip` until they blur and refocus the window
  (`refetchOnWindowFocus`, still at its default because
  `frontend/src/shared/query-client.ts:4-21` overrides only `retry`) or reload. The bug is
  symmetric: an unbind leaves a stale "observers enabled" chip on every other client.
- **Blast radius**: every non-creator participant in every room where observers are bound
  or unbound mid-session. Notice is the whole point of the chip.
- **Intent source**: [R28.09]; `docs/tasks/2026-07-22-observation-binding-cleanup/spec.md:186-191`
  asserts "No stale 'observers enabled' indicator is left behind" — true of the DTO
  computation, false of the client.

## F-2: The guest-observer warning fires on the inverted condition and tells the teacher that disclosure removes a risk it does not remove

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/views/ChatroomSettingsView.vue:216-221`
  gates the callout on `allow_guest_links && hasObserver && !flags.disclose_observers`,
  rendered at `:634`; copy at `frontend/src/slices/conversation/locales/en.json:259` and
  `zh-TW.json:259` says guests are observed without notice "while disclosure is off". But
  `backend/app/api/v1/chatrooms.py:238-240` forces `disclose_observers=False` and
  `observers_present=False` for every pure guest regardless of the room setting, pinned by
  `backend/tests/unit/test_observer_agents.py:136-139`; the chip is the only guest-facing
  observer surface (`ChatroomHeader.vue:34`); disclosure defaults to true
  (`backend/alembic/versions/0041_observer_agents.py:58`, `server_default=true`).
- **Failure scenario**: a teacher enables guest links, binds an observer, and leaves
  disclosure at its `true` default. The callout is suppressed. Every external guest is
  observed with no indicator, and the copy the teacher did read implies that state is safe.
- **Blast radius**: every room using guest links with an observer bound — i.e. the
  classroom flow the example packs are built for. External guests are the population least
  able to discover the observation any other way.
- **Intent source**: [R28.02] (guest neutralisation) and [R28.09] (notice);
  `docs/tasks/2026-08-24-agent-readable-live-drafts/spec.md:728-735` (D-8) records the
  guest suppression as deliberate, which makes the callout condition and its copy the
  defect, not the backend.

## F-3: On legacy NULL-creator rooms an org owner is locked out of every observer surface the server would grant them

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: backend falls back to moderator semantics —
  `backend/contexts/conversation/application/access.py:456-458` → `:82-83` → `:54-62`
  (`PROJECT_OWNER in roles or ORG_OWNER in roles`), and roles come from the tenancy
  resolver (`:113-117`), so an inherited org role counts with no `project_members` row. The
  client instead re-derives the answer from a membership list:
  `frontend/src/slices/conversation/composables/useObservations.ts:63-71` (and the
  duplicate at `ChatroomSettingsView.vue:201-209`) require
  `membersQuery.data.find(m => m.user_id === me.id)?.role === 'owner'`, fed by
  `projectsApi.listMembers`, which serves `project_members` rows only
  (`backend/app/api/v1/projects.py:365-373`). The server already ships the answer as
  `is_moderator` on the DTO (`backend/app/api/v1/chatrooms.py:136,253,474,491`;
  `frontend/src/slices/conversation/types/index.ts:42`) and it is consumed for messages at
  `ChatroomView.vue:742` but not here. NULL-creator rooms still exist:
  `backend/alembic/versions/0041_observer_agents.py:32-43` backfills only from audit rows
  with a non-NULL actor and states at `:3-5` that unmatched legacy rooms stay NULL; the
  column is nullable at `:47-55` with no later NOT NULL migration. This is the exact bug
  class the codebase already diagnosed and fixed elsewhere —
  `frontend/src/slices/tenancy/composables/useProjectRole.ts:9-16` and
  `backend/app/api/v1/projects.py:57-65` document it verbatim; these two observer sites
  were not migrated.
- **Failure scenario**: an org owner with no `project_members` row opens a pre-0041 room
  whose `created_by_user_id` is NULL. `GET /chatrooms/{id}/observations` would return 200,
  but `isCreator` is false, so the query is never enabled and `hasObserverSurface` is
  false: no Observer tab, no disclosure toggle, no observer role selector. The observations
  are unreachable through the UI.
- **Blast radius**: org owners on pre-0041 rooms. A second, independent path reaches the
  same wrong verdict for *current* rooms: `listMembers` is called with no pagination
  (`useObservations.ts:55`; `frontend/src/slices/tenancy/api/projects.ts:77-81`), so the
  server default `limit=100` applies (`backend/app/api/v1/deps.py:29`) and a genuine
  project owner beyond row 100 also resolves to `isCreator === false`.
- **Intent source**: [R28.02] — "Rooms whose creator is NULL fall back to moderator
  semantics (project/org owner)" (`REQUIREMENTS.md:2120`).

## F-4: The shipped AA observer prompt instructs a `present_observation` call its own schema rejects

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/infrastructure/examples/packs/creative-thinking-room.json:80`
  states that every block except `prose` must carry a `basis` it may not remove — covering
  `key_points`, `timeline`, `field_coverage`, `mandala_grid` and `attempt_table`. But
  `backend/contexts/agents/application/runtime/observation_blocks.py:275-290`
  (`_coverage_branch`, used for both `field_coverage` and `mandala_grid` — see `:133,:135`,
  there is no separate mandala branch) and `:293-314` (`_attempt_table_branch`) declare
  `additionalProperties: False` with no `basis` property; the server stamps basis itself at
  `:387`. `backend/contexts/agents/application/runtime/tool_registry.py:320-328` runs
  `schema_violations` before `invoke`, so the guidance in
  `observer_tools.py:226-240` is never reached; the block array is one argument validated
  as a whole (`tool_registry.py:246-247`), so one bad element kills the valid blocks
  alongside it. The normaliser that might have saved it does not apply
  (`tool_registry.py:200-229` strips `additionalProperties: false` only when
  `patternProperties` was present). No test pins the prompt sentence
  (`backend/tests/unit/test_agent_example_packs.py:454-467` checks the block-kind names and
  the "not a score" sentence only).
- **Failure scenario**: install `creative-thinking-room`, bind AA as observer in a room
  running `mandala-9grid`, let its silence trigger fire. AA follows its prompt and emits
  `[{kind:"key_points",basis:"transcript",...},{kind:"mandala_grid",...,basis:"server_facts"}]`.
  The call is rejected with a bare `Additional properties are not allowed ('basis' was
  unexpected)`. AA can retry within its tool-round budget, so the realistic harm is burnt
  rounds and a fallback to plain prose: the Observer panel shows a markdown note with no
  grid and no key-points block — exactly the figure the mandala's `filled_count_coverage`
  design exists to produce.
- **Blast radius**: every installation of the flagship example pack; the observer panel is
  the teacher-facing payoff of that pack.
- **Intent source**: `docs/examples/creative-thinking-course.md:787-791` — "Computed blocks
  are not offered the choice at all"; `observation_blocks.py:18-24`. Only the shipped
  prompt disagrees.

## F-5: The design pack's DA prompt still teaches the retired quoting rule, so every TA/SA prompt it drafts licenses quoting content no agent can see

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/infrastructure/examples/packs/creative-thinking-design.json:27`
  lists `mandala-9grid` and `time-traveler-next-steps` as quotable, inside DA's mandatory
  constraint list. The room pack moved off that rule —
  `creative-thinking-room.json:28,54,80` each state the agent cannot see mandala content —
  because `mandala-9grid` uses `filled_count_coverage`
  (`backend/contexts/activities/infrastructure/examples/courses/creative-thinking.json:7-11`;
  `time-traveler-next-steps` uses `filled_count` at `:72-76`), documented at
  `docs/examples/creative-thinking-course.md:728,733-739`. The guard that would have caught
  it excludes DA: `backend/tests/unit/test_agent_example_packs.py:335-356` asserts the
  exact literal is absent, parametrized over `ta-guidance-teacher`, `sa-peer-catalyst` and
  `aa-silent-analyst` only — while its own docstring at `:324-327` argues DA should be
  covered. The same line omits `six-hats-shared-case` from both quoting columns and from
  `binds_activity_types` (`creative-thinking-design.json:21-26`), so DA's default clause
  ("類型不在清單上一律當成不可引述") makes a drafted prompt forbid quoting a group task the
  room pack explicitly permits (pinned for ta/sa/aa only at `test_agent_example_packs.py:358-364`).
- **Failure scenario**: a teacher installs both packs and asks DA to draft a TA prompt for
  another Mandala unit. The drafted prompt says mandala cells are quotable. Pasted into TA,
  a student asks what they wrote in a specific cell; TA believes it may quote, has no cell
  text in context, and fabricates — the exact failure the dry-run checklist warns about at
  `docs/examples/creative-thinking-course.md:893-897`.
- **Blast radius**: no runtime effect on its own, since DA emits text a human pastes. It is
  a defect generator: every prompt drafted through the designer inherits the stale rule.
- **Intent source**: `docs/examples/creative-thinking-course.md:728,733-739,893-897`; the
  room pack's own text.

## F-6: Non-creator viewers who legitimately read the panel see a roster that is permanently "idle"

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/runtime/turn_engine.py:3407-3435`
  publishes observation events only to `user_channel(recipient)` where the recipient is
  literally `room.created_by_user_id`
  (`contexts/conversation/application/observation_service.py:106-114`), and publishes
  nothing at all when it is None (`:3427-3428`). The observer branches deliberately choose
  `_emit_observation_event` over the room-wide `agent.finished` paths
  (`turn_engine.py:2961-2968,2978-2986`), so no room channel carries the failure either.
  Roster status derives solely from the Pinia store
  (`frontend/src/slices/conversation/composables/useObservations.ts:75-90`), whose only
  production writers are the five WS handlers at `:150-182`; with no events, status falls
  through to `'idle'` at `:85`. The 30s poll added for exactly these viewers
  (`:106-112`, whose comment claims to close the staleness gap) refetches the observations
  list only — the roster comes from `boundAgentsQuery`, and `ObservationOut`
  (`backend/app/api/v1/observations.py:76-91`) carries no status field.
- **Failure scenario**: an admin opens a teacher-owned room whose bound observer fails
  every turn with `key_group_scope` (`turn_engine.py:2523-2526`). The creator's panel shows
  "error" with the mapped tooltip; the admin's identical panel shows "idle" for every
  observer indefinitely, and no other surface reports the failure.
- **Blast radius**: platform admins and NULL-creator moderator-fallback viewers — the
  audience least able to cross-check, since it is not their room. The panel makes an
  affirmative wrong claim rather than showing "unknown".
- **Intent source**: [R28.10] (roster), [R28.13] (events); the W-1 comment at
  `useObservations.ts:106-109` states the intent to close this staleness gap for these
  viewers and closes only the list half of it.

## F-7: A failed observations fetch is rendered as "No observations yet"

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/composables/useObservations.ts:105`
  (`retry: false`), `:114-116` (`observations` collapses to `[]` when data is undefined),
  `:239-253` (the returned object exposes `observationsLoading` but no `isError`/`error`);
  `frontend/src/slices/conversation/components/ObserverPanel.vue:89-96` has no error prop
  and `:42-47` paints `SEmptyState` once the `loading` gate at `:28-40` goes false, which
  it does in the error state; the copy is a positive claim at
  `frontend/src/slices/conversation/locales/en.json:196-197`. No global handler suppresses
  it: `frontend/src/shared/query-client.ts:4-21` installs no `QueryCache.onError`, and
  `useServerErrors` is opt-in and unused here. The panel is up while the list is dead
  because `hasObserverSurface` (`useObservations.ts:121-123`) is satisfied by the surviving
  `boundAgentsQuery`.
- **Failure scenario**: the creator opens the Observer tab while
  `GET /api/chatrooms/{id}/observations` returns 500. The panel states "No observations yet
  — Observers write here after they analyze the conversation." The creator concludes the
  observer produced nothing. A transient failure heals on tab refocus (no `staleTime`,
  `refetchOnWindowFocus` at its default); a persistent one does not.
- **Blast radius**: every creator during any backend or network failure of that endpoint.
  An error indistinguishable from an empty state is worse than an error.
- **Intent source**: [R28.16]; the locale copy itself, which asserts a fact the client has
  not established.

## F-8: An observer can sit on "analyzing" for the rest of the page's life

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/composables/useObservations.ts:151-157`
  sets the analyzing flag; the only clears are the three terminal handlers at `:158-177`.
  No timer and no `onStatus`/`onDegraded` registration exists in the file (subscriptions
  end at `:182`), and no other production writer exists (`stores/conversation.ts:143-150`,
  plus `resetRoom` at `:191-209` and `clearAll` at `:219`). The room path has both guards:
  `useChatroomSocket.ts:39` (`AGENT_THINKING_TIMEOUT_MS = 120_000`), watchdog at `:327-346`,
  reconnect reset at `:613` inside `onStatus` (`:603-624`). Three loss paths survive
  verification: a hard worker kill between the `observation.started` emit
  (`turn_engine.py:2607`) and the terminal emit (`:3154-3163`); a terminal frame published
  while the user socket is down, since Redis pub/sub is fire-and-forget by design
  (`backend/shared_kernel/realtime/pubsub.py:3-7`) and this composable has no reconnect
  reconcile; and a `CancelledError` raised inside the `_post_commit("observation.created
  emit")` scope, which `_post_commit` (`turn_engine.py:714-735`) cannot catch because
  `CancelledError` is not an `Exception`, after which `:3307` re-raises without emitting.
  `__tests__/useObservations.test.ts:186-222` pins the happy path only.
- **Failure scenario**: an observer turn starts and the roster shows "analyzing"; the arq
  worker is OOM-killed mid-provider-call. No terminal frame is ever produced. The roster
  reads "analyzing" indefinitely — through every later turn, since each new
  `observation.started` re-sets the same flag. `resetRoom` clears it only on real unmount
  (`useChatroomSocket.ts:674`); the keep-alive `onDeactivated` path (`:648-656`) does not,
  so navigating away and back to a cached view preserves the stuck status.
- **Blast radius**: the creator's roster in any room whose observer turn dies without a
  terminal frame. Release and delete keep working, so the harm is a persistent false
  status rather than a blocked flow.
- **Intent source**: [R28.10], [R28.13]. Note: arq `job_timeout` is *not* a trigger —
  `turn_engine.py:3307-3314` skips finalization only when the outcome is already committed,
  and `:3316-3326` → `_finalize_failed_turn` → `:3356-3361` does emit `observation.failed`
  for observer turns.

## F-9: Observation markdown never receives the KaTeX / Mermaid / highlight.js post-pass, so the creator decides on a degraded preview

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/utils/renderMarkdown.ts:100-104` returns
  sanitised HTML only; the post-pass is the separate DOM function at `:185-187`. Its only
  caller in `src/` is `useMarkdownEnhance.ts:32`, whose only caller is
  `ChatroomView.vue:859`, bound to `listRef` (`:473`) — the message `<ol class="messages">`
  at `:76-83`. Both `ObserverPanel` mounts are outside that subtree: the desktop rail at
  `ChatroomView.vue:252-264` (inside `.chatroom__presence`, a sibling of `.chatroom__feed`
  at `:61`) and the people drawer at `:313`. The unenhanced `v-html` bindings are
  `ObservationCard.vue:28-31` (prose slot) and `:39-44` (markdown body); no
  `Obs*Block.vue` or `ObservationBlocks.vue` calls the enhancer.
- **Failure scenario**: an observation contains a ```` ```mermaid ```` fence, `$$…$$` math,
  or any code fence. In the Observer panel the creator sees raw fence text
  (`renderMarkdown.ts:158-161` for mermaid, `:138` for math) and unhighlighted code
  (markdown-it's `highlight` only escapes, `:35-41`). The creator releases it and the same
  text renders as a diagram in the feed: same content, two renderings, and the release
  decision was made against the wrong one.
- **Blast radius**: creators reviewing observations containing math, diagrams or code —
  likely for an analytical agent, though the six presentation blocks carry most structured
  content and are unaffected.
- **Intent source**: `docs/tasks/2026-08-24-observer-presentation-blocks/spec.md` (the panel
  is a preview of what release will publish).

## F-10: The panel asserts "No observer currently bound" while an observer is bound

- **Severity**: minor
- **Verdict**: confirmed for two of three triggers; plausible for the third
- **Evidence**: `frontend/src/slices/conversation/components/ObserverPanel.vue:18-24` gates
  the alert on `!roster.length && observations.length` and sits *above* the `loading` block
  at `:28-40`, which covers only the list — so no loading gate can suppress it. The copy is
  a positive claim at `locales/en.json:198-199`. The roster is empty in three non-unbound
  situations. (a) `boundAgentsQuery` failed: `ChatroomView.vue:492-496` sets `retry: false`
  — **confirmed**, though the finder's "terminal for the session" is wrong, since
  `refetchOnWindowFocus` refetches an errored query on tab return. (b) The observations
  query settled first on mount — **plausible only**: structurally the ordering is backwards
  (`boundAgentsQuery` fires unconditionally at `:492` while observations wait on `isCreator`,
  which waits on `roomQuery`), so it needs the agents endpoint to be slower than
  room+observations or a warm `gcTime` cache. (c) An observer bound in another tab or
  session — **confirmed**: `chatroom-agents` appears exactly once in `frontend/src`
  (`ChatroomView.vue:493`), nothing invalidates it, `useChatroomBindings.ts:155-187` writes
  to local refs via raw API calls and never touches the query client, and
  `observation.created` invalidates only the observations key (`useObservations.ts:169`).
- **Failure scenario**: the creator has the room open in tab A and binds "Watcher" as an
  observer from settings in tab B. Watcher produces an observation; tab A receives
  `observation.created`, refetches the list, and shows the Observer tab with an empty roster
  and the alert "No observer currently bound … an observer that is no longer bound to this
  room." The statement is false. It survives only while tab A stays focused.
- **Blast radius**: creators working across two windows or two devices, and any creator
  during a transient failure of the bound-agents endpoint. Self-heals on focus.
- **Intent source**: `docs/tasks/2026-07-22-observation-binding-cleanup/spec.md` (the alert
  exists to explain observations that outlived their binding).

## F-11: `release()` writes the cache without invalidating, so an in-flight list refetch can revert the released badge

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/composables/useObservations.ts:217-221`
  calls `patchReleased` only — no `invalidateQueries`, no `cancelQueries`, no version guard
  — unlike `remove()` at `:223-237`, whose invalidate at `:236` is pinned by
  `__tests__/useObservations.test.ts:282-294`. Two refetch sources can be in flight: the
  30s `refetchInterval` for non-literal-creator viewers (`:110-111`, pinned at
  `useObservations.test.ts:158-184`) and the `invalidateQueries` fired by every
  `observation.created` (`:169`). The list endpoint does not filter released rows
  (`backend/app/api/v1/observations.py:122-135` → `observation_service.py:97-104`), so a
  stale snapshot really does carry `released_at: null`. `ObservationCard.vue:62` hides the
  Release control on `released_at`. TanStack offers no automatic protection: `setQueryData`
  during an in-flight fetch neither cancels nor survives it.
- **Failure scenario**: an admin (reaching this path via the bypass at
  `contexts/conversation/application/access.py:451-452`) has the panel open. The 30s poll's
  request is issued, the admin releases observation X, `patchReleased` hides the button, and
  the slow poll response then overwrites the cache with the pre-release snapshot. X shows as
  unreleased again; a second click yields the "already released" 409 toast
  (`ChatroomView.vue:684-689`), which does refetch and repair the row.
- **Blast radius**: admins and moderator-fallback viewers, in a narrow window (the poll must
  resolve after a release that started later). Self-healing on the next tick, and for the
  literal creator the server's own `observation.released` frame re-applies the patch.
- **Intent source**: [R28.13].

## F-12: `observerPanelVisible` reports true in the 1024-1279 compact band while the panel is hidden, pinning the unread counter at zero

- **Severity**: minor
- **Verdict**: confirmed as a state bug; its stated user-visible failure is refuted
- **Evidence**: `frontend/src/slices/conversation/views/ChatroomView.vue:654-657` computes
  `railTab === 'observer' && (isDesktop.value || peopleDrawerOpen.value)`, and
  `isCompactDesktop` (`:441`) is strictly inside `isDesktop`; but in that band
  `.chatroom--compact .chatroom__presence` is `visibility: hidden` (`:1459-1476`) and
  translated off-screen (`:1484-1488`) unless `.chatroom__panel--open` (`:1490-1493`, bound
  to `peopleDrawerOpen` at `:235`). So `setPanelOpen(true)` runs
  (`useObservations.ts:130-133`) and pins `unreadCount` at 0 while the panel is invisible.
  The band-change watcher at `ChatroomView.vue:998` resets surfaces but never `railTab`.
  **However**: the badge is rendered only on the STabs tab
  (`ChatroomView.vue:620-635`, consumed at `:627,629`), which lives inside the very element
  that is hidden, and `ChatroomHeader.vue:85-95` carries the compact-band People toggle with
  no badge. `unreadCount` has no other consumer, so fixed and broken code look identical
  today.
- **Failure scenario**: latent. It becomes user-visible the moment an unread badge is added
  to the compact-band header toggle — at which point the creator at 1100px who closed the
  overlay would never be told an observation arrived. The correct term is
  `(isDesktop && !isCompactDesktop) || peopleDrawerOpen`.
- **Blast radius**: none today; creators in the 1024-1279 band once a header badge exists.
- **Intent source**: AC-12 in
  `docs/tasks/2026-08-30-chatroom-approval-and-overlay-discoverability/spec.md:258-260`
  ("at 1024-1279 the three transient surfaces occupy the in-chat overlay layer"). Note the
  paraphrase "compact rails are overlays, not rails" appears nowhere in that dossier.

## F-13: A missed `observation.created` never raises the Observer tab's unread badge, even after the row itself is recovered

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/composables/useObservations.ts:128` and
  `:170` — `unreadCount` is incremented only by the WS handler, never by the query. Redis
  pub/sub does not replay for a disconnected subscriber
  (`backend/shared_kernel/realtime/pubsub.py:3-7`), and the server closes sockets during
  normal operation (`backend/shared_kernel/realtime/connection.py:334-337` slow consumer
  1013, `:217-226` per-user cap).
- **Failure scenario**: the creator's tab is backgrounded and the socket is closed as a slow
  consumer. An observation is created during the reconnect window. On tab return,
  `refetchOnWindowFocus` recovers the row into the list, but the Observer tab shows no
  unread badge — so a creator who does not open the tab is not told anything arrived.
- **Blast radius**: creators after any socket gap. The observation itself is not lost; only
  the notification is.
- **Intent source**: [R28.13]. This is the surviving residue of a broader candidate ("the
  observation is permanently invisible") that verification refuted — see §4.

## F-14: Deleting an observation emits no event, and the resulting 404 is handled as a generic failure with no refetch

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/app/api/v1/observations.py:167-185` returns 204 with no publish,
  while release does emit at `:249-261`. `observation.deleted` exists only as an audit
  action string (`contexts/conversation/application/observation_service.py:244`), never as a
  WS event, and the client event union
  (`frontend/src/slices/conversation/types/index.ts:160`) has no such member — the event
  table at `docs/observer-agents/00-overview.md:151-156` never defined one either, so this
  is a spec gap rather than a deviation. `ObservationNotFound` maps to 404
  (`contexts/conversation/interfaces/error_mapping.py:128-132`, raised at
  `observation_service.py:220,240`). `ChatroomView.vue:710-715` catches delete failures with
  a bare toast and no refetch; `:681-700` handles 409 and `/invalid-release-target` only, so
  a 404 falls to the generic `setError` at `:697` with the dialog still open.
- **Failure scenario**: the creator deletes an observation in one tab; a second session
  still lists it. Acting on the ghost row yields a bare "delete failed" or "release failed"
  toast with the row still visible. In practice this is a sub-second window — focusing the
  second tab triggers the focus-refetch that drops the row — but a user who clicks
  immediately on focus can hit it, and the 404 branch produces an unactionable message.
- **Blast radius**: creators working across two sessions; narrow window.
- **Intent source**: none directly — the missing event is a gap in
  `docs/observer-agents/00-overview.md:151-156`, and the asymmetry with
  `observation.released` is the evidence it was an oversight rather than a decision.

## F-15: The observer's `mandala_grid` figure and the participant's own 9-cell form disagree on centre placement

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: the participant renderer places `center` by name at index 4 regardless of
  declared order (`frontend/src/slices/activities/plugins/mandala9grid/MandalaGrid.vue:24-26,41-44,48-53`,
  fed by the `x-order` sort at `frontend/src/slices/activities/components/schemaFields.ts:54-74`).
  The observer aggregate uses declared order only, with no centre rule
  (`backend/contexts/activities/application/observation_aggregates.py:137-166` sorts by
  `x-order`; `:99-103` chunks row-major). The docstring at `:141-143` asserts that order
  "is the order the participant's own form renders in" — an intent stated in the code and
  violated by the code beneath it. The gate admitting a type to `mandala_grid` requires only
  nine declared properties (`backend/contexts/agents/application/runtime/observer_tools.py:70,155-158,221-223`).
  The shipped course coincides (`creative-thinking.json:40-44` gives `center` `x-order: 5`),
  which is why nothing catches it; the plugin binds by key, not type id
  (`docs/examples/creative-thinking-course.md:848-851`).
- **Failure scenario**: a Project Owner authors a nine-field type keyed `mandala-9grid`
  whose `center` is not fifth. Students see `center` in the highlighted middle box; the
  observer figure puts it top-left and shifts the other eight cells one position, under a
  basis line asserting the numbers were computed by the server over this room's
  submissions. The teacher reads fill counts against the wrong cells.
- **Blast radius**: unreachable with shipped content; requires a project-scoped nine-field
  type keyed `mandala-9grid` with a non-fifth `center`. When reached, eight of nine cells
  are misattributed under a `server_facts` claim.
- **Intent source**: the docstring at `observation_aggregates.py:141-143`;
  `docs/examples/creative-thinking-course.md:848-851`.

## F-16: Soft-deleted and beyond-page-100 observer agents display as a truncated UUID

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: the fallback is `agentId.slice(0, 8)` at
  `frontend/src/slices/conversation/composables/useObservations.ts:84` (roster) and
  `components/ObserverPanel.vue:106-108` (cards). `agentNames` comes from
  `listProjectAgents` (`frontend/src/slices/conversation/api/index.ts:148-150`), which sends
  no `limit`, so the server default 100 applies (`backend/app/api/v1/agents.py:387-400`;
  `backend/app/api/v1/deps.py:29`). Agents are soft-deleted
  (`contexts/agents/application/agent_service.py:963-975`) and
  `AgentRepository.list_for_project` filters `deleted_at IS NULL`
  (`backend/contexts/agents/infrastructure/repositories.py:229-236`), so the
  `ON DELETE CASCADE` documented at
  `docs/tasks/2026-07-22-observation-binding-cleanup/spec.md:208` never fires and
  observations outlive the resolvable name.
- **Failure scenario**: a creator deletes the observer agent, or the project holds more than
  100 agents. The stranded observations — which are designed to outlive the binding
  (`useObservations.ts:118-123`) — head their cards with e.g. `a3f81c2b` instead of a name
  or a "deleted agent" label.
- **Blast radius**: creator-only, cosmetic, no authorization consequence. The >100 trigger is
  rare in a classroom-scoped project.
- **Intent source**: none defends these two cases. `ChatroomView.vue:498-501` scopes its
  acceptance of the truncated id to query gating (missing room data must not raise errors),
  and the truncated-id posture elsewhere
  (`docs/tasks/2026-08-24-agent-readable-live-drafts/spec.md:95-98`, `typingNames`) exists to
  avoid disclosing *human* identity, which does not apply to an agent the creator bound.

## 4. Refuted Candidates

- **"Observations created during a WS drop are permanently invisible to the creator."**
  Refuted as stated. `frontend/src/shared/query-client.ts:4-21` sets no `staleTime` and does
  not disable `refetchOnWindowFocus`, so returning to a backgrounded tab refetches every
  page and recovers the row — the repo already relies on this mechanism by name at
  `frontend/src/slices/admin/components/EmailDomainPolicyForm.vue:192-193` and
  `frontend/src/slices/notifications/components/NotificationBell.vue:33`. Only the unread
  badge is genuinely lost, which is F-13. The idle-timeout close path
  (`backend/shared_kernel/realtime/connection.py:271-277`, 120s) is also unreachable while
  the client heartbeat runs (`frontend/src/shared/transport/ws-manager.ts:42`, 30s).
- **"A demoted creator sees a 403 rendered as 'No observations yet'."** Refuted: the
  scenario cannot reach the panel. Client and server diverge only when `access.roles` is
  empty, and with empty roles `ensure_can_read` also rejects the room read
  (`backend/contexts/conversation/application/access.py:164-198`), leaving `opts.room`
  undefined so `isCreator` is false. In the one surviving sub-case (a demoted creator who is
  also a `chatroom_guests` row) `boundAgentsQuery` 403s independently
  (`backend/app/api/v1/chatrooms.py:647-648`), so `hasObserverSurface` is false and the
  Observer tab never renders. The generic-500 half of the same candidate survives as F-7.
- **"A ghost observation card persists until reload after a delete in another tab."**
  Refuted as stated, for the same focus-refetch reason: acting on the ghost requires
  focusing that tab, which triggers the refetch that removes it. The sub-second window and
  the unhandled 404 branch survive as F-14.
- **"arq `job_timeout` leaves the roster stuck on analyzing."** Refuted:
  `turn_engine.py:3307-3314` skips finalization only when the outcome is already committed;
  otherwise `:3316-3326` runs `_finalize_failed_turn`, which emits `observation.failed` for
  observer turns at `:3356-3361`. The hard-kill and lost-frame paths survive as F-8.
- **"The unread badge is dead in the compact band, so the creator is never told."** The
  state bug is real (F-12) but this stated outcome is refuted: the badge's only render site
  is inside the hidden element, so fixed and broken code produce the same user-visible
  result today.
- **Checked and found correct**, recorded so the next audit does not re-open them: keyset
  pagination (`observation_repo.py:105-131` matches `getNextPageParam` at
  `useObservations.ts:102-103`, and the anchor lookup deliberately ignores `deleted_at` so a
  soft-deleted cursor still pages); the W-5 delete/`hasNextPage` invalidate
  (`useObservations.ts:232-236`); `patchReleased` immutability (`:193-213`); per-room state
  cleanup on unmount (`stores/conversation.ts:191-209` via `useChatroomSocket.ts:674`);
  missing-i18n fallbacks (`ObservationCard.vue:123-128`, `ObserverPanel.vue:114-123` via
  `te()`, `constants/agentErrors.ts:38-56` guarding prototype-named kinds); unknown block
  kinds (`ObservationBlocks.vue:40-43`); observer locale parity across `en`/`zh-TW` in both
  the `conversation` and `agents` slices; `mandala_grid` cell ordering, `g:` group codes and
  the empty-turn guard in the aggregates.

## 5. Hand-off

All sixteen findings were selected for fixing on 2026-09-03 and consolidated into one
dossier executed in three phases, grouped by blast radius so that each phase is
independently reviewable and revertible. Phase 1 covers F-1, F-2, F-3, F-7 and F-10;
phase 2 covers F-6, F-8, F-11, F-12, F-13 and F-14; phase 3 covers F-4, F-5, F-9, F-15
and F-16. Nothing was declined.

Two dispositions are narrower than the finding as written, and the dossier records why.
F-6 is corrected to stop asserting "idle" to viewers who receive no event feed, rather
than by building that feed — real status delivery to non-creator readers is that
dossier's FU-1, since it needs either a reverse resolver in `access.py` or a persisted
per-turn status, both design work. F-4 and F-5 correct the shipped pack JSON but do not
repair already-installed copies: install copies `system_prompt` into an `agents` row and
is idempotent by agent name with no update path, and the project has twice documented
the hand-edit remedy instead (`docs/examples/creative-thinking-course.md:344-347,627-636`).

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | fix (phase 1) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-2 | fix (phase 1) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-3 | fix (phase 1) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-4 | fix (phase 3) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-5 | fix (phase 3) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-6 | fix (phase 2, narrowed — see above) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-7 | fix (phase 1) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-8 | fix (phase 2) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-9 | fix (phase 3) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-10 | fix (phase 1) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-11 | fix (phase 2) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-12 | fix (phase 2) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-13 | fix (phase 2) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-14 | fix (phase 2) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-15 | fix (phase 3) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |
| F-16 | fix (phase 3) | `docs/tasks/2026-09-03-observer-ui-defect-sweep/` |

## 6. Out-of-scope Observations

- **FU-1** (route to `check-quality`): the creator-resolution logic is duplicated between
  `useObservations.ts:63-71` and `ChatroomSettingsView.vue:201-209`, and both re-derive an
  answer the server already serialises as `is_moderator`. F-3 fixes the correctness half;
  the duplication itself is a structural concern.
- **FU-2** (route to `check-quality`): `useChatroomBindings.ts:155-187` performs writes
  through raw API calls into local refs while the same data is also held in the TanStack
  cache under `chatroom-agents`. Two sources of truth for one dataset is what makes F-10(c)
  possible; the general pattern is broader than that finding.
- **FU-3** (test coverage): `backend/tests/unit/test_agent_example_packs.py:335-364`
  parametrizes its quoting-rule and binding guards over the three room agents only. Beyond
  fixing F-5's pack text, the parametrization should cover every agent in every pack, or the
  next retired rule will survive in whichever agent the list forgets.
- **FU-4** (spec gap, route to `/spec`): `docs/observer-agents/00-overview.md:151-156`
  defines five observation events and no `observation.deleted`. F-14 is the symptom; whether
  the event should exist is a design question, not a bugfix.
