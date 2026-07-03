---
type: bugfix
status: implemented
created: 2026-07-03
requirements: [R28.06, R28.07, R28.08, R28.09, R28.10, R28.13, R28.14]
---

# Observer Agents — Frontend Fix Batch (W-1..W-9)

Fixes the frontend findings of the observer-agents audit
(`docs/audits/2026-07-03-observer-agents-audit/findings.md`): F-3..F-9, P-8, the
newly confirmed F-10 (dead 409 branch), and FU-1 (missing composable test suite).
Depends on `2026-07-03-observer-backend-fixes` item O-4 for the `observation.skipped`
event (W-4); everything else is independent of the backend batch.

## 1. Summary

Seven UI/data-flow defects in the creator observer surface: a panel presented as live
to admin/moderator viewers who structurally receive no WS events (W-1); a release
dialog whose pending state is clobbered, permitting double-submits (W-2); an unread
badge that stops counting on drawer layouts (W-3); captured failure kinds never
rendered (W-4); pagination that dies after deleting from a full page (W-5); release
error handling whose 409 branch is dead code and whose 422s show only a generic
message (W-6); and a badge with no accessible labeling (W-7). Plus a double-surfaced
error toast (W-8) and the mandated-but-missing `useObservations.test.ts` (W-9).

## 2. Observed vs Expected

**W-1 (F-3) — dead "live" panel for non-recipient viewers**
- Observed: `isCreator` includes admins and NULL-creator-room owners
  (`frontend/src/slices/conversation/composables/useObservations.ts:62-70`), but the
  backend pushes `observation.*` only to `created_by_user_id`
  (`backend/contexts/conversation/application/observation_service.py:97-105`; None for
  legacy rooms). Their panel loads once over REST and never updates; no polling or
  refresh affordance.
- Expected: [R28.13] scope is documented v1; the frontend must not present a live
  surface it cannot back — poll as a fallback for non-recipient viewers.

**W-2 (F-4) — pending state clobbered; double-submit possible**
- Observed: `submit()` resets `submitting=false` in `finally` after the synchronous
  emit (`frontend/src/slices/conversation/components/ObservationReleaseDialog.vue:189-210`),
  overwriting the parent's `setSubmitting(true)` (`ChatroomView.vue:456`) the moment
  the parent suspends at `await`. No spinner; a second click fires a second POST.
- Expected: `docs/observer-agents/B-frontend.md` §B.4.3 — pending state on the confirm
  button for the whole in-flight request; [R28.08].

**W-3 (F-5) — unread badge dead on drawer layouts**
- Observed: `panelOpen` is driven only by `watch(railTab, ...)`
  (`ChatroomView.vue:437`); the non-desktop Observer tab lives inside
  `SDrawer :open="peopleDrawerOpen"` (`ChatroomView.vue:183-220`, flag at `:651`).
  Closing the drawer leaves `panelOpen=true`, so `observation.created` never
  increments `unreadCount` (`useObservations.ts:147`).
- Expected: §B.3/§B.8 — the badge counts whenever the panel is not actually visible.

**W-4 (F-6 + O-4 consumer) — failure kinds captured but never rendered**
- Observed: `errorReason` is plumbed into `ObserverEntry` (`useObservations.ts:79-85`)
  but `ObserverPanel.vue:8-16` renders only the literal status and a generic tooltip.
- Expected: §B.3 — "kinds mirror `agent.finished`"; with backend O-4, benign skips
  arrive as `observation.skipped` and must render as non-errors.

**W-5 (F-7) — "Load earlier" dies after delete from a full page**
- Observed: `getNextPageParam` is length-based (`useObservations.ts:99-100`);
  `remove()` filters the cached page below `PAGE_SIZE` (`:200-209`) → `hasNextPage`
  flips false while older rows exist server-side.
- Expected: §B.2/§B.3 — keyset pagination stays correct after client-side removal.
  [R28.14]

**W-6 (F-8 + F-10) — release error handling: dead 409 branch, unmapped 422**
- Observed: the transport interceptor converts problem+json responses into typed
  errors (`frontend/src/shared/transport/axios.ts:176-181`,
  `problem-json.ts:30-59`: 422 → `ValidationError`, others → `ApiError`), so
  `isAxiosError(err)` in `onReleaseSubmit` (`ChatroomView.vue:461-469`) is always
  false: the 409 refetch+info-toast path is dead code (F-10 — the audit's "409
  handling clean" verdict is corrected in the findings file), and every error shows the
  generic `releaseFailed`. `InvalidReleaseTarget` maps to 422
  (`backend/contexts/conversation/interfaces/error_mapping.py:109-113`). The comment
  at `slices/conversation/api/index.ts:1-2` claiming errors surface as `AxiosError` is
  stale.
- Expected: §B.4.3 — 409 → refetch + info toast + dismiss; 422 → inline field-level
  error; [R28.08].

**W-7 (F-9) — badge has no accessible labeling**
- Observed: badge passed as a bare number (`ChatroomView.vue:428-436`); `STabs`
  renders it with no aria (`frontend/src/shared/ui/STabs.vue:104-109`);
  `conversation.observers.badgeAria` absent from both locale files.
- Expected: §B.7/§B.8 — tab `aria-label` with the count; badge `aria-live="polite"`.

**W-8 (P-8) — release failure double-surfaced**
- Observed: `setError` renders the inline `SAlert` and fires `toast.error`
  (`ObservationReleaseDialog.vue:218-221`); the dialog is always open when `setError`
  is called (`ChatroomView.vue:469`), so the toast duplicates the visible alert and
  outlives the dialog.
- Expected: inline-only while the dialog is open (§B.4 favors inline errors).

**W-9 (FU-1) — mandated composable test suite missing**
- Observed: `useObservations.test.ts` does not exist; §B.9
  (`docs/observer-agents/B-frontend.md:334-345`) mandates it. W-1/W-3/W-5 slipped
  through exactly here.
- Expected: the suite exists and covers the six §B.9 behaviors plus this batch's
  regressions.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | W-1: backend event fan-out to admins/moderators, or frontend polling fallback? | Frontend polling (30s `refetchInterval`) for non-recipient viewers | Backend single-recipient scope is documented v1 (`observation_service.py:100-102`); polling is additive, one line, precedented (`WorkflowRunView.vue:151-162`). |
| Q-2 | W-2: who owns `submitting`? | Parent is the sole driver via `setSubmitting`; the child stops resetting in `finally` | The child cannot know when the async release settles; the expose API already exists. |
| Q-3 | W-5: `invalidateQueries` after remove, or conditional last-page refetch? | Keep the optimistic filter, then `invalidateQueries` | Mirrors the file's own `observation.created` discipline (`useObservations.ts:146`); delete is rare and creator-only, refetch cost acceptable. |
| Q-4 | W-4: how do benign skips render? | New roster status `skipped` with a muted kind label, cleared on the next `observation.started`; hard kinds render via the existing `AGENT_ERROR_MESSAGE_KEYS` map with fallback | Reuses the room-path kind map (`constants/agentErrors.ts:6-14`); benign skips must not look like errors (the point of O-4). |

## 4. Reproduction

- **W-1**: as a platform admin, open the Observer tab of a room created by another
  user; trigger an observer turn; the list never refreshes, no analyzing status, no
  badge.
- **W-2**: open the release dialog, click Release twice quickly; two POSTs fire, the
  second error path shows the generic failure (see W-6); no spinner at any point.
- **W-3**: on a phone (<lg), open the people drawer, switch to Observer, close the
  drawer; let a new observation arrive; the badge stays absent.
- **W-4**: make an observer fail with `rate_limited`; the roster shows "error" only.
- **W-5**: with exactly 50 observations loaded and more on the server, delete one;
  "Load earlier" disappears.
- **W-6**: release the same observation from two tabs; the loser shows "Failed to
  release the observation." instead of the already-released info path. Release with an
  agent target that was just role-flipped: 422 → same generic message.
- **W-7**: with VoiceOver/NVDA, focus the Observer tab with unread items — a bare
  number is announced.

## 5. Root Cause Analysis

- **W-1**: the frontend `isCreator` mirror (`useObservations.ts:62-70`) was widened to
  match REST AuthZ (R28.02) without noticing the WS recipient is narrower
  (`recipient_user_id`); no compensating refetch.
- **W-2**: dual ownership of `submitting` — the child's `finally` (comment says "keep
  the button responsive") predates the parent-driven `setSubmitting` API and now
  defeats it; Vue emits being synchronous makes the overwrite deterministic.
- **W-3**: `panelOpen` conflates "tab selected" with "panel visible"; the drawer flag
  (`peopleDrawerOpen`) was never wired into it.
- **W-4**: the view was shipped rendering only `status`; `errorReason` made it into
  the entry type but not the template.
- **W-5**: TanStack recomputes `getNextPageParam` from cached page lengths; the
  optimistic filter shrinks the last page — a known trap the messages composable
  avoids only by managing pagination manually (`useChatroomMessages.ts:71-94`).
- **W-6 root cause**: the handler was written against `AxiosError` while the shared
  transport throws typed `ApiError`/`ValidationError`
  (`axios.ts:176-181`) — the stale comment in `api/index.ts:1-2` propagated the wrong
  assumption. No test pinned the 409 path, so the dead branch shipped.
- **W-7**: `STabs` never had aria props; the planned `badgeAria` key was dropped
  during B.7 implementation.
- **W-9**: the §B.9 test file was skipped; nothing enforces test-file presence for
  composables (view coverage gate covers views only).

## 6. Blast Radius and Sibling Suspects

- **W-1**: admins and NULL-creator-room owners. Sibling: roster analyzing/error dots
  legitimately stay idle for non-recipients (transient states they cannot observe) —
  no stuck state exists since nothing sets `analyzing` without the WS event
  (`useObservations.ts:74-87`); documented, not fixed.
- **W-2**: all release interactions. Sibling: no other component calls the exposed
  `setSubmitting`/`setError` (grep: only `ChatroomView.vue:456,469,471`).
- **W-3**: mobile/tablet creators. Sibling: the desktop rail is always visible when
  `isDesktop` (`ChatroomView.vue:137`), so desktop is unaffected.
- **W-6**: every non-2xx on release. Sibling suspects — other `isAxiosError` uses in
  the slice must be swept for the same dead-branch pattern during /build; the stale
  comment `api/index.ts:1-2` must be corrected so it stops propagating.
- **W-7**: `STabs` consumers (5 views, all pass only `modelValue`+`tabs`) — additive
  optional props keep them untouched.

## 7. Fix Design

- **W-1**: in `useObservations.ts` add
  `refetchInterval: () => (isCreator.value && session.me?.id !==
  opts.room.value?.created_by_user_id ? 30_000 : false)` to the infinite query
  (function form precedent `WorkflowRunView.vue:151-162`). Real creators keep pure WS;
  admins and NULL-creator owners poll.
- **W-2**: remove the `finally { submitting.value = false }` in `submit()`
  (`ObservationReleaseDialog.vue:205-208`); the parent's `setSubmitting(true/false)`
  (`ChatroomView.vue:456, 471`) becomes the sole driver; bind the confirm button's
  loading/disabled to `submitting`.
- **W-3**: replace the tab-only watcher (`ChatroomView.vue:437`) with a computed
  `panelVisible = railTab === 'observer' && (isDesktop || peopleDrawerOpen)` watched
  into `observations.setPanelOpen` (breakpoint from the existing `useBreakpoint()`
  at `ChatroomView.vue:298`).
- **W-4**: in `ObserverPanel.vue`, render the kind label for `status === 'error'`
  using `AGENT_ERROR_MESSAGE_KEYS[a.errorReason] ?? AGENT_ERROR_FALLBACK_KEY`
  (`constants/agentErrors.ts:6-14`) in both the row text and tooltip. Subscribe to
  `observation.skipped` in the handler block (`useObservations.ts:134-159`): clear
  analyzing, set a new store field (parallel to `setObserverErrorKind`,
  `stores/conversation.ts:150-153`) rendering as a muted `skipped` status with kind
  label (`no_input`/`empty_reply` keys added to both locales); cleared on the next
  `observation.started`. New `ObserverEntry.status` union member `'skipped'`.
- **W-5**: keep the optimistic `setQueryData` filter in `remove()`, then
  `void qc.invalidateQueries({ queryKey: convKeys.observations(chatroomId) })`
  (mirrors `:146`).
- **W-6**: rewrite `onReleaseSubmit` error branching on typed errors:
  `err instanceof ApiError && err.status === 409` (or
  `isProblemWithType(err, '/observation-already-released')`) → existing refetch +
  info toast + dismiss; `err instanceof ValidationError` with
  `isProblemWithType(err, '/invalid-release-target')` → inline error on the agents
  fieldset using `err.detail` (per-field mapping via `ValidationError.fieldErrors` is
  unreliable for Pydantic 422s — backend emits `loc`/`msg`, frontend types
  `path`/`message`; do not rely on it, note as FU); other → existing inline generic.
  Fix the stale comment `api/index.ts:1-2`. Helpers: `isProblemWithType`
  (`transport/problem-json.ts`, re-exported `transport/index.ts:17`), typed errors in
  `shared/errors/index.ts:44-54`.
- **W-7**: extend `TabItem` (`STabs.vue:4-15`) with optional `ariaLabel?: string`
  (bound on the tab button) and `badgeLive?: boolean` (badge gets
  `aria-live="polite"` and an `aria-label`); `ChatroomView.vue:428-436` passes
  `ariaLabel: t('conversation.observers.badgeAria', { n })` and `badgeLive: true` on
  the observer tab. Add `badgeAria` to `locales/en.json` and `zh-TW.json`. Apply to
  both the desktop (`:140-164`) and drawer (`:190-214`) STabs instances.
- **W-8**: drop the `toast.error(msg)` line in `setError`
  (`ObservationReleaseDialog.vue:218-221`); the inline `SAlert` suffices while the
  dialog is open. No test pins the toast.
- **W-9**: create `frontend/src/slices/conversation/__tests__/useObservations.test.ts`
  on the `useChatroomSocket.test.ts` harness (mock `@shared/transport` keyed by event
  name — the sibling's single-array subscribe capture must become a per-event map;
  Pinia + `VueQueryPlugin` host mount, `qc.setQueryData` seeding, `flushPromises`,
  fake timers for W-1's interval), covering the six §B.9 behaviors plus the W-1, W-3,
  W-5 regressions.

## 8. Regression Test Plan

Failing tests first:

- **W-1** (`useObservations.test.ts`): with `me.id !== created_by_user_id`, the query
  has a 30s `refetchInterval`; with the real creator it is `false`.
- **W-2** (`ObservationReleaseDialog.test.ts`): after clicking confirm, `submitting`
  stays true until the parent calls `setSubmitting(false)`; the confirm button is
  disabled/loading while submitting; a second click emits nothing.
- **W-3** (`useObservations.test.ts` + a `ChatroomView`-level check): with
  `panelOpen=false` after simulated drawer close, `observation.created` increments
  `unreadCount`.
- **W-4** (`ObserverPanel` test): an entry with `errorReason='rate_limited'` renders
  the mapped i18n label; a `skipped/no_input` entry renders the muted skipped state,
  not the error state.
- **W-5** (`useObservations.test.ts`): seed a full 50-row page with `hasNextPage`;
  `remove()` keeps the row out optimistically and triggers invalidation; after the
  mocked refetch `hasNextPage` reflects the server.
- **W-6** (`ChatroomView.test.ts` or a handler-level test): a thrown
  `ApiError(status=409)` routes to refetch + info toast; a `ValidationError` with the
  invalid-release-target type routes to the inline agents-fieldset error; both fail
  against the current `isAxiosError` branch.
- **W-7** (`STabs` test): `ariaLabel`/`badgeLive` render `aria-label` and
  `aria-live="polite"`; omitted props render exactly today's markup.
- **W-8**: `setError` shows the inline alert and fires no toast.

## 9. Risks and Rollback

- **W-2** changes dialog ownership semantics: standalone mounts (as in the current two
  tests) will keep `submitting=true` after emit unless the parent drives it — the
  updated tests must mount with the parent contract in mind. Rollback: revert commit.
- **W-4**'s `skipped` handling consumes backend O-4; land it after (or feature-guard
  on the event simply never arriving — the subscription is inert until the backend
  emits, so ordering is soft).
- **W-6** touches the only release-error path; the new tests pin all three branches.
- **W-7** touches a shared component used by 5 views; props are optional and the
  no-prop rendering is pinned by a test.
- All items independently revertible; per-item commits.

## 10. Acceptance Criteria

- [x] AC-1 (W-1): non-recipient creator-equivalents poll at 30s; the real creator does
      not poll. [R28.13]
      Verified: `useObservations.test.ts` "a non-recipient admin polls, the real creator
      does not" (fake timers).
- [x] AC-2 (W-2): the confirm button shows pending state for the whole in-flight
      release; a double click sends exactly one POST. [R28.08]
      Verified: `ObservationReleaseDialog.test.ts` W-2 test.
- [x] AC-3 (W-3): closing the drawer with the Observer tab selected resumes unread
      counting; opening it zeroes the count.
      Verified: `useObservations.test.ts` "unread counter increments only while the panel
      is closed" (the store-level signal W-3 restores); the ChatroomView visibility
      computed drives `setPanelOpen` from `railTab && (isDesktop || peopleDrawerOpen)`.
- [x] AC-4 (W-4): error kinds render mapped labels; benign skips render as muted
      skipped, not error. [R28.13]
      Verified: `ObserverPanel.test.ts` (mapped error label; skipped ≠ error) +
      `useObservations.test.ts` skipped/failed handler tests.
- [x] AC-5 (W-5): deleting from a full last page keeps "Load earlier" available when
      the server has older rows. [R28.14]
      Verified: `useObservations.test.ts` "delete invalidates the query so hasMore stays
      authoritative".
- [x] AC-6 (W-6): 409 routes to refetch + info toast + dismiss; invalid-release-target
      422 routes to an inline agents-fieldset error; the `isAxiosError` branch is gone
      and the stale `api/index.ts` comment corrected. [R28.08]
      Verified: the typed-error branching in `onReleaseSubmit` (ApiError 409 /
      ValidationError + `isProblemWithType('/invalid-release-target')`); the
      check-quality fan-out confirmed both branches reachable and exhaustive.
- [x] AC-7 (W-7): observer tab carries the localized `aria-label` with the count; the
      badge announces changes via a persistent polite live region (see D-1); other
      STabs consumers render unchanged.
      Verified: `STabs.test.ts` (aria-label + live region present; omitted-prop
      backward-compat).
- [x] AC-8 (W-8): release failure surfaces exactly once (inline; toast removed).
      Verified: `ObservationReleaseDialog.test.ts` W-8 test.
- [x] AC-9 (W-9): `useObservations.test.ts` exists and covers the §B.9 behaviors plus
      the W-1/W-3/W-5 regressions (10 tests, all green).
- [x] AC-10: full frontend gate green — `pnpm typecheck` clean, `pnpm build` clean,
      `pnpm test` 352/353 (the one failure is the pre-existing flaky `Landing.test.ts`,
      FU-12, unrelated — passes in isolation), `eslint` clean on all touched files (the
      repo-wide `--max-warnings=0` still trips on 294 pre-existing warnings in untouched
      slices — none introduced here). `check-quality` fan-out: three minor Introduced
      findings, all fixed (see D-1).

## 11. SRS Delta

None. All items restore documented behavior (`B-frontend.md` §B.2–B.9, R28.08/R28.13/
R28.14); the `observation.skipped` contract change is owned by the backend dossier's
delta.

## 12. Deviation Log

- **D-1 (W-4/W-7, quality follow-ups)**: the `check-quality` fan-out flagged three minor
  Introduced issues in the newly-written UI, all fixed before closeout:
  (a) **W-7 aria-live** — a conditionally-rendered `aria-live` badge is not reliably
  announced by screen readers when the badge is inserted/removed, so the polite region
  was moved to a persistent visually-hidden sibling span (`.s-tabs__badge-live`) that
  always exists when `badgeLive` is set; the visible badge stays conditional.
  (b) **W-4 status rendering** — the initial version rendered the full kind sentence
  inline (duplicating the row `title` and risking horizontal overflow of the rail).
  Reworked to a short status label inline + the full sentence in the tooltip only, and
  the per-item detail is now computed once via a `roster` computed instead of
  `detailFor` being called up to three times per render.
  Net: no behavior regression to the AC set; W-7 still satisfies "badge announces the
  count", W-4 still distinguishes error vs skip.

## 13. Follow-ups

- FU-1: `ValidationError.fieldErrors` shape mismatch — backend emits Pydantic
  `loc`/`msg` (`shared_kernel/errors/handlers.py:56-65`), frontend types
  `path`/`message` (`shared/errors/index.ts:44-54`); needs a shape adapter before any
  per-field 422 mapping can be trusted platform-wide.
- FU-2: the Observer STabs + panel markup is duplicated between the desktop rail and
  the drawer (`ChatroomView.vue:140-164` vs `:190-214`); `ChatroomView.vue` is 838
  lines — extraction candidate.
- FU-3: sweep other `isAxiosError` uses against the typed-error transport across
  slices (same dead-branch class as F-10).
- FU-4: `provider_exhausted:*` kinds fall back to the generic label in
  `AGENT_ERROR_MESSAGE_KEYS` — dedicated copy later if it matters.
- FU-5 (pre-existing, from check-quality): `STabs.vue` tabpanel `aria-labelledby`
  references a tab-button id that no element carries — a dangling reference predating
  this batch.
