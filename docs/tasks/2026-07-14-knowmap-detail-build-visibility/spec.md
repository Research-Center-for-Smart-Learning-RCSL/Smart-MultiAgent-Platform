---
type: bugfix
status: approved
created: 2026-07-14
requirements: [R24.23]
---

# F-22: Automatic Knowledge Map rebuilds are invisible to an idle detail page

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-22).

## 1. Summary

The Knowledge Map config detail page only opens its build-state WebSocket subscription when
the config is **already** in an in-progress state at load, or when the user clicks the
explicit rebuild button. Uploading or deleting a document — both of which enqueue an
automatic backend rebuild — invalidates only the documents query; neither calls
`watchBuild` nor refreshes the config row. Because the shared build-state engine's recovery
mechanisms (resync-on-connect, backstop poll, seeding) are all scoped to configs handed to
`watch()`, an auto-build started while the page shows `idle` has no subscriber and no poller,
so the build badge can stay `idle` through the entire build and completion. The engine itself
is correct — the sibling `useBuildStateSocket` already implements the resync+poll pattern that
the F-21/F-28 fix relies on — so the fix is at the view's call site: subscribe the active
detail config continuously (on config load, not gated on in-progress state), so the
always-open channel receives the `idle → running` transition from any auto-build and the
poll/reconnect-resync backstops engage once it is in progress.

## 2. Observed vs Expected

- **Observed:**
  - The only mount-time subscribe is gated on already-in-progress state
    (`frontend/src/slices/agents/views/KnowledgeMapConfigDetailView.vue:106-114`):
    `watch(config, cfg => { if (cfg && GRAPHRAG_IN_PROGRESS.has(cfg.last_build_state)) watchBuild(configId, cfg.last_build_state) }, { immediate: true })`. If the config loads
    `idle`, `watchBuild` is never called.
  - The upload success handler (`onFiles`, `:290-316`) invalidates only the documents query on
    success (`:309-310`) — no `watchBuild`, no config invalidation.
  - The document-delete success handler (`:319-326`) likewise invalidates only documents
    (`:321-324`) — no `watchBuild`, no config invalidation.
  - The badge is driven by `effectiveState = liveState[configId] ?? config.last_build_state ??
    'idle'` (`:103`), rendered at `:469-478`. With no live subscription and a stale/`idle`
    config row, it shows `idle`.
  - The only other `watchBuild` call is the explicit rebuild button (`startBuild`, `:137-140`,
    `watchBuild(configId, 'running')`).
  - The engine's recovery is subscription-scoped: `useBuildStateSocket.watch()` seeds, opens the
    channel, wires `onStatus → syncStatus` and starts the poll only for watched configs
    (`frontend/src/slices/agents/composables/useBuildStateSocket.ts:100-129`); the poll only
    covers configs with a known in-progress `liveState` (`isInProgress`, `:40-47`; `undefined`
    is not polled, `:44-46`).
  - The auto-build is real: upload enqueues a build via the ingest service
    (`backend/contexts/knowledge/application/knowmap_ingest_service.py:116,167,258-263` →
    `enqueue_knowmap_build`, `backend/contexts/knowledge/application/knowmap_triggers.py:40-63`),
    and delete enqueues directly (`backend/app/api/v1/knowmap.py:572-574`). Both drive the
    backend `last_build_state` to `running`.
- **Expected** — the active Knowledge Map detail page reflects build progress for any build that
  starts while it is open, including automatic rebuilds triggered by an upload or delete, without
  requiring a manual reload. This is the Phase 4b WebSocket + polling progress contract, and the
  same [R24.23]-family live-progress recovery the sibling RAG/GraphRAG sockets already honor.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Subscribe continuously on mount, or only in the upload/delete success handlers? | **Both** — de-gate the mount subscribe **and** re-subscribe in the mutation handlers. | Second-angle verification (see §7.1) found these are not alternatives: the engine tears down a config's channel on **every** terminal state (`idle` is terminal, `useBuildStateSocket.ts:74-84`), so the mount subscription self-destructs after the first build completes and the mount `watch(config)` does not re-fire (the config ref is structurally shared/unchanged). The mount subscribe makes build #1 visible; a `watchBuild` re-call in each upload/delete `onSuccess` re-opens the channel for every subsequent auto-build. `watch()` is idempotent (early-returns if already watched, `:109`), so doing both is safe. |
| Q-2 | Fix the identical gating in the sibling `GraphragConfigListView`? | **Yes**, in scope, with a cost note. | `GraphragConfigListView.vue:134-142` has the identical in-progress-gated `watchBuild` pattern and the same defect class. A list opens one channel per subscribed row, so continuous subscription there has a per-row channel cost (§9); the fix must be bounded to rendered rows and is noted as a risk. |

## 4. Reproduction

Preconditions: a Knowledge Map config in `idle`/terminal state with the detail page open.

1. Open the Knowledge Map config detail page; the badge shows `idle` (or a prior terminal
   state). No subscription is opened (`:106-114` guard is false).
2. Upload a document (or delete an existing one). The success handler invalidates the documents
   query only (`:309-310` / `:321-324`); no `watchBuild`.
3. The backend enqueues `knowmap_build` and drives `last_build_state` to `running`
   (`knowmap.py:572-574`; `knowmap_ingest_service.py:167`).
4. Observed: the badge remains `idle` (no `liveState` writer, no poll — the poll skips configs
   whose `liveState` is not a known in-progress value, `useBuildStateSocket.ts:44-46`) through
   completion; only a manual reload reflects the true state.

## 5. Root Cause Analysis

The engine (`useBuildStateSocket`) is correct and complete: `watch()` seeds `liveState`, opens
the channel, resyncs authoritative state on every (re)connect (`onStatus → syncStatus →
applyState`, `:118-120,87-93,72-85`), and runs a 15s backstop poll for in-progress configs
(`:63-70`). **The root cause is the view never calling `watch()` for the idle case**: the sole
mount-time subscribe is gated on `GRAPHRAG_IN_PROGRESS.has(cfg.last_build_state)`
(`KnowledgeMapConfigDetailView.vue:109`), and the mutation success handlers do not subscribe
(`:309-310`, `:321-324`). With no `watch()` call, none of the engine's recovery runs, so a
build that begins after load is unobservable. The earliest correcting link is de-gating the
subscribe so the channel is open before any auto-build starts; once open, the live `build.state`
`running` frame drives `liveState`, which engages the poll and terminal-invalidation that already
exist (`:116-120`). The backend does emit this frame: the build worker publishes `build.state=running`
at build start to `ws:knowmap:{config_id}` — the exact channel the client subscribes to
(`backend/contexts/knowledge/application/graphrag_builder.py:216-223`;
`backend/contexts/knowledge/interfaces/channels.py:16-17`; WS route
`backend/app/api/ws/knowmap.py:16-19`) — so a client whose channel is open before the worker starts
receives it.

**A second cause compounds it for repeat builds.** The engine unwatches (tears down) a config's
channel on **every** terminal state (`applyState` defers `unwatch` when the state is not in-progress,
`useBuildStateSocket.ts:74-84`; a successful build ends at terminal `idle`,
`graphrag_builder.py:476-483`). So even a de-gated mount subscription covers only the **first**
build; after it completes the channel is gone, and the mount `watch(config)` does not re-fire
(TanStack structural sharing keeps the config ref identical across an `idle→idle` refetch). Without
an explicit re-subscribe, the second auto-build reproduces the original bug. The fix therefore needs
both a mount subscribe and a per-mutation re-subscribe (§7).

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — live build feedback for every automatic (upload/delete-triggered) Knowledge
  Map rebuild viewed on the detail page; the badge and the terminal-driven documents refetch
  (`:116-120`) both fail to fire.
- **Sibling suspects:**
  - **`GraphragConfigListView.vue:134-142` (confirmed, in scope per Q-2).** Identical
    in-progress-gated `watchBuild`; an auto-build (e.g. a Concept Map rebuild) started while a
    row shows idle is invisible until reload. Fixed by the same de-gate, bounded to rendered rows
    (§7.3, §9).
  - **`useBuildStateSocket` engine (cleared — this is the exemplar).** It already resyncs on
    connect and polls; the F-21 spec names it the cleared exemplar
    (`docs/tasks/2026-07-14-rag-ingestion-ws-resync/spec.md:94-101,106-107`). No engine change.
    Its own `watch()` comment confirms the seed rationale: a defined `initialState` is what lets
    the backstop poll recover a build even when the socket never connects
    (`frontend/src/slices/agents/composables/useBuildStateSocket.ts:95-108`, "audit C6").
  - **RAG detail page (cleared — separate finding).** RAG uses a different composable
    (`useRagConfigSocket`) fixed under F-21/F-28; not this dossier.
  - **`KnowledgeMapConfigListView` (verified — a *different*, milder gap, FU-3, not the same
    bug).** The Knowledge Map list view exists but does **not** use `useKnowmapSocket` at all — it
    renders `row.last_build_state` statically from the config-list query
    (`frontend/src/slices/agents/views/KnowledgeMapConfigListView.vue:284-288`), with no
    `watchBuild`. It never triggers document builds (no upload/delete there), so it is not the
    detail-page auto-build bug; its only gap is that a build running elsewhere is not reflected
    live until the list refetches. Recorded as FU-3 rather than expanded here, since a socket-less
    list view is a different fix shape.
  - **`GraphragConfigListView` (verified — the true identical-gating sibling, in scope per Q-2).**
    `GraphragConfigListView.vue:134-142` has the exact in-progress-gated per-row `watchBuild`; a
    Concept Map build started elsewhere (e.g. a message-trigger auto-build) is invisible on the
    list until reload. Fixed by the same de-gate, bounded to rendered (paginated) rows (§7.3, §9).
  - **`RagConfigListView` (cleared — different composable).** The RAG list uses the F-21
    ingestion socket family, not `useBuildStateSocket`; out of scope here.

## 7. Fix Design

Subscribe the active detail config continuously and let the existing engine handle recovery.

**7.1 De-gate the detail-view subscribe.** Replace the in-progress-gated watch
(`KnowledgeMapConfigDetailView.vue:106-114`) with an unconditional subscribe on config load:
call `watchBuild(configId, effectiveState.value)` (a **defined** initial state — `cfg.last_build_state ?? 'idle'`) once the config is available, keeping it watched for the page
lifetime. `useBuildStateSocket.watch()` early-returns if already watched (`:109`) and unwatches on
unmount (`:131-135`), so this is idempotent and self-cleaning. Passing a defined `initialState`
(not `undefined`) matters: the seed guard (`:105-108`) and the poll (`:44-46`) both need a defined
value for the backstop to engage once the state becomes in-progress. This mount subscribe covers
the **first** build after load; because the engine tears the channel down on each terminal state
(§5, `useBuildStateSocket.ts:74-84`), repeat builds need §7.2.

**7.2 Re-subscribe on mutation success (covers every subsequent auto-build).** In the upload
(`:309-310`) and delete (`:321-324`) success handlers, call `watchBuild(configId,
effectiveState.value)` in addition to invalidating the documents query. This is the load-bearing
part for repeat builds: after the first build's terminal `idle` frame tore the channel down (§5),
`watchBuild` re-opens it so the worker's next `build.state=running` frame is delivered to a live
subscriber. `watch()` is idempotent — it early-returns if still watched and re-subscribes if the
channel was torn down (`:109`) — so it composes safely with the §7.1 mount subscribe. The seed value
passed is unimportant (the worker has usually not flipped `last_build_state` to `running` at
`onSuccess`, so a config refetch here would still read `idle` — the reliable path is the re-opened
live channel, not a refetch). Also invalidate `agentKeys.knowmapConfig(configId)` as a cheap
secondary so a later refetch converges once the worker has flipped the row.

**7.3 Apply the same de-gate to `GraphragConfigListView` (bounded).** De-gate the per-row
`watchBuild` (`GraphragConfigListView.vue:134-142`) so each rendered row subscribes regardless of
its initial state, passing the row's `last_build_state` as the defined `initialState`. Bound the
subscription to rows actually rendered (do not pre-subscribe an unbounded/paginated set); the
existing unwatch-on-unmount and the engine's single shared poll interval keep the cost bounded
(§9). The same terminal-teardown limitation (§5) applies per row: after a row's build ends the row's
channel is torn down. Re-subscribe when the config-list query refetches with a changed row (the list
already re-runs its `watch(configs)` on list refetch, which — unlike the detail view's single-config
ref — does deliver changed row objects); the list's own per-row rebuild button already re-subscribes
optimistically (`GraphragConfigListView.vue:156-159`).

No backend, API, schema, or data changes. `agentsApi.getKnowmapConfig` (returns
`last_build_state`) and the `agentKeys` query keys already exist
(`frontend/src/slices/agents/queries/index.ts:22-27`).

## 8. Regression Test Plan

Frontend (Vitest). There is currently **no** `KnowledgeMapConfigDetailView` test (the frontend
"every view has >=1 test" gate is unmet for it), so the view test is net-new; place socket-style
tests in `frontend/src/slices/agents/__tests__/` to match `useKnowmapSocket.test.ts` /
`useGraphragSocket.test.ts`.

1. **Idle-page auto-build becomes visible (primary red-first).** Mount the detail view with a
   config whose `last_build_state='idle'`; assert `watchBuild(configId, 'idle')` is called on
   load (today it is not — the guard at `:109` is false). Then deliver a live `build.state`
   `running` frame and assert `effectiveState`/the badge becomes `running`. Fails today: no
   subscription exists, so the frame is never received.
2. **Upload success re-subscribes.** Simulate an upload success; assert `watchBuild(configId, …)`
   is invoked (the channel is re-opened) and the config query is invalidated, so a subsequent live
   `running` frame is reflected.
3. **Delete success mirrors upload.** Same assertion for the document-delete success path.
4. **Second consecutive build after a terminal state (regression for the §5 teardown gap).** Drive
   the config through a full first build to terminal `idle` (channel torn down by the engine), then
   simulate a second upload success and deliver a `running` frame; assert the badge shows `running`
   again. Fails a mount-subscribe-only fix — the torn-down channel has no subscriber for build #2.
5. **Backstop poll engages after subscribe.** With the config subscribed and driven to `running`,
   assert the engine poll re-syncs and transitions to a terminal state (e.g. `idle`) when the
   config's `last_build_state` completes, then unwatches.
6. **Sibling list de-gate.** In `GraphragConfigListView.test.ts` (extend the existing test),
   assert a rendered row with `last_build_state='idle'` subscribes on render (today it does not),
   and reflects a `running` frame.

Primary red-first test: (1); the teardown-gap regression is (4).

## 9. Risks and Rollback

- **Continuous channel per open detail page.** One always-open WS channel per open Knowledge Map
  detail page (vs zero when idle today). Multiplexed via `wsManager`, unwatched on unmount, and
  polled only while in-progress — matching the existing `useBuildStateSocket` budget. Acceptable.
- **List-view per-row channels (§7.3).** De-gating the GraphRAG list opens one channel per
  rendered row instead of only in-progress rows. Bounded to rendered rows and torn down on
  unmount; the engine shares a single poll interval. If a list can render many rows, an FU
  (§13) can restrict subscription to the viewport. Flagged as the main cost risk.
- **Repeat builds after terminal teardown.** The engine unwatches a config's channel on each
  terminal state (§5), so a mount-only subscribe would make only the first build visible. Mitigated
  by §7.2's `watchBuild` re-call in every mutation `onSuccess`, which re-opens the channel before the
  next worker publishes `running`. The §8.4 regression test guards this specifically.
- **Missed live `running` frame without reconnect.** If the sole `idle → running` frame is dropped
  while the channel stays connected (no reconnect to trigger resync), the poll will not engage (seed
  is `idle`). Low-likelihood: the channel is re-opened right before each build (§7.2), making live
  delivery the normal path, and any reconnect resyncs authoritative state; the §7.2 config
  invalidation is a further backstop once the worker has flipped the row.
- **Rollback** — revert the view (and list) call-site changes; frontend-only, no API/schema
  change.

## 10. Acceptance Criteria

- [ ] AC-1: The idle-page auto-build test (§8.1) fails before the fix and passes after.
- [ ] AC-2: On config load, the detail view subscribes to build state unconditionally with a
  defined initial state (not gated on in-progress), so a build that starts after load is
  reflected in the badge without a manual reload.
- [ ] AC-3: Upload and document-delete success handlers re-call `watchBuild(configId, …)` to
  re-open the channel (and invalidate the config query), so each subsequent auto-build re-subscribes.
- [ ] AC-4: An automatic rebuild triggered by an upload or delete drives the badge from `idle`
  through `running` to the terminal state on the open detail page, with the backstop poll and
  terminal documents refetch (`:116-120`) engaging.
- [ ] AC-5: `GraphragConfigListView` rows subscribe regardless of initial state, bounded to
  rendered rows (§7.3), verified by test (§8.6).
- [ ] AC-6: `pnpm test`, `pnpm lint`, `pnpm typecheck`, and `pnpm build` pass in `frontend/`.
- [ ] AC-7: A **second** consecutive auto-build (after a first build reached terminal state and the
  engine tore the channel down) is reflected live on the open detail page without reload — the §8.4
  regression fails a mount-subscribe-only fix and passes with the §7.2 re-subscribe.

## 11. SRS Delta

None. This restores the documented Phase 4b live-progress contract and the [R24.23]-family
recovery the sibling sockets already implement; no new requirement.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 (viewport-bounded list subscription):** if a Concept/Knowledge Map list can render many
  rows, restrict continuous subscription to the visible viewport (or a small cap) rather than all
  rendered rows, to bound open channels. Out of scope for this bugfix.
- **FU-2 (shared detail-page subscription helper):** the RAG (F-21), GraphRAG, and Knowledge Map
  detail pages now share a "subscribe the active config continuously + resync/poll" shape; a
  future refactor could extract a common primitive. Out of scope here.
- **FU-3 (`KnowledgeMapConfigListView` has no live build state):** the Knowledge Map list view
  never subscribes to `useKnowmapSocket` (`KnowledgeMapConfigListView.vue:284-288`), so a build
  running elsewhere is stale on the list until it refetches. A different, milder gap than this
  finding (no build trigger on the list); a follow-up could subscribe rendered rows like the
  GraphRAG list or refetch the list query on a modest cadence.
