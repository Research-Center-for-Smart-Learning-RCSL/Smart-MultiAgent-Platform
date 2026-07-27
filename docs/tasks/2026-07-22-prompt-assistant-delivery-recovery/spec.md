---
type: bugfix
status: approved
created: 2026-07-22
approved: 2026-07-28
requirements: []
depends_on: []
---

# A Prompt Studio reply that misses the WebSocket is unrecoverable, and wedges the composer

## 1. Summary

The prompt-assistant turn result exists in exactly one place the client can reach — an
ephemeral Redis pub/sub frame — and that transport is fire-and-forget with no durable read
side. Delivery of the terminal frame is assumed, never verified, on either end.

`shared_kernel/realtime/pubsub.py:3-6` states the no-replay contract explicitly and names its
counterpart: "the server does not replay; client fetches delta on reconnect". Prompt Studio
implements the first half and not the second — the complete session HTTP surface is create and
post, with **no GET**. The data is there: the worker appends the assistant turn to the Redis
`SessionStore` before publishing. It is simply unreachable from the client.

Two symptoms follow from that one cause. A frame lost during the initial handshake produces
**silent loss** — most plausibly `prompt.error`, emitted within milliseconds of job start, so
the panel shows nothing at all. A frame lost on a mid-turn reconnect produces a **wedge**:
`streaming` never returns to false, and because the Send button binds
`:loading="sending || streaming"` and `SButton` turns `loading` into the real `disabled`
attribute, the composer is permanently unusable. The user pays either way — the message cap
and daily quota are charged synchronously before enqueue, and a re-POST cannot recover the
turn because the arq job id is derived from the session's message count and deduplicates.

Source: `docs/audits/2026-07-22-agent-config-runtime/findings.md` F-13 (major, confirmed).

**Freshness note (2026-07-28).** Re-verified against current `HEAD` before build. The defect itself
is unchanged and still unfixed — the session HTTP surface is still create+post only, no GET exists.
`useChatroomSocket.ts`/`ws-manager.ts`, cited throughout §6/§7 as the house pattern this fix should
copy, were rewritten in the interim by `docs/tasks/2026-07-22-chatroom-socket-lifecycle/` (heartbeat,
stability window, cap signal) and by `d557752`'s approval reconcile; every citation into those two
files was re-checked against current `HEAD` and corrected in place. All backend citations were
re-checked and found accurate except `session_store.py`'s TTL contract, corrected from `:48-51` to
`:96`.

## 2. Observed vs Expected

- **Observed.**
  - Session HTTP surface is complete at `backend/app/api/v1/prompt_studio.py:740-751` (create)
    and `:759-773` (post). No read route. The frontend mirror is identical —
    `frontend/src/slices/prompt-studio/api/index.ts:166-174`.
  - The reply *is* persisted: `backend/app/workers/tasks/prompt_assistant.py:146-147` appends
    to the `SessionStore` before publishing at `:148`.
  - No replay: `backend/shared_kernel/realtime/pubsub.py:3-6`.
  - No liveness check: `frontend/src/slices/prompt-studio/composables/usePromptAssistantSocket.ts:35,41`
    clears `streaming` only inside the `prompt.finished` / `prompt.error` cases; `:27-29` sets
    it true with nothing that can un-set it absent a terminal frame.
  - `streaming` is a control gate, not a spinner flag:
    `frontend/src/slices/prompt-studio/components/PromptAssistantPanel.vue:198` binds
    `:loading="sending || streaming"`; `frontend/src/shared/ui/SButton.vue:29` computes
    `isDisabled = props.disabled || props.loading`, applied as `disabled` at `:38`.
  - Cost is charged regardless: the message cap
    (`backend/contexts/prompt_studio/application/session_service.py:50`) and the daily quota
    INCR (`:57-60`) both run in the web process **before** enqueue at `:65-70`. Only narrow
    pre-provider failures refund (`prompt_assistant.py:36-39,73,91`).
  - A retry cannot rescue it: the job id is `f"prompt:{session_id}:{len(updated.messages)}"`
    (`session_service.py:69`), so a re-POST from the same state deduplicates. And the wedge
    disables the button first, so the user cannot attempt it without a reload — which mints a
    fresh session (`PromptAssistantPanel.vue:28,36-46`) and drops all history.

- **Expected.** A reply the user has been charged for is retrievable, and no lost frame can
  permanently disable a control.

  **Intent source.** `requirements: []` is a positive claim — no `[Rxx.yy]` governs
  prompt-assistant delivery. The expectation rests on the codebase's own stated contract
  (`pubsub.py:5-6`, whose second half is unimplemented here) and on internal consistency: this
  is the only consumed WebSocket channel in the product with neither a recovery fetch nor a
  watchdog, and the only one where a lost terminal frame disables a control. See §6.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which of the four candidate fixes is load-bearing? | **The session read endpoint plus refetch-on-connect.** The watchdog is a mandatory safety net, not the fix. | Only the refetch recovers the content the user paid for, and it is the missing half of the codebase's own contract. A watchdog alone gives the user an unwedged button and still no answer — cosmetic. |
| Q-2 | Should the fix also gate `postMessage` on the socket being open, to close the first-message window? | **No — subsumed, and it introduces a worse failure.** | Once the refetch fires on *every* `onStatus(connected)` transition including the first, the handshake window closes by itself, because the refetch after the handshake picks up whatever was published during it. This is exactly how `useChatroomSocket.ts:383-402` handles the same window. Gating the POST would also mean a user who cannot open a socket cannot send anything — converting a silent-loss bug into a hard block. **Ordering requirement: the refetch must fire on the initial connect, not be conditioned on an `everConnected` flag.** |
| Q-3 | Should the pub/sub layer gain replay/cursor semantics (Redis Streams)? | **No — out of scope.** | It is the structurally right answer and `pubsub.py:4-7` anticipates it, but it is a `shared_kernel` change affecting all seven consumed channels, needing stream trimming, cursor persistence and per-connection replay semantics. Do not couple this fix to it; recorded as FU-1. |
| Q-4 | What is the AuthZ model for the new read endpoint? | Per-session ownership only, via the existing `require_owned_session`. **No membership dependency, and no admin bypass.** | `post_message` (`prompt_studio.py:762-773`) takes only `current_principal` and delegates entirely to `require_owned_session` (`session_service.py:72-82`); the WS route does the same through the facade (`app/api/ws/prompt_assistant.py:37`). Ownership is strictly stronger than membership, and project membership is already enforced once at session creation (`prompt_studio.py:743`). This is deliberately stricter than admin: a platform admin must not read another user's prompt session through this route, which is the current behaviour of both existing entry points and therefore the conservative choice. |
| Q-5 | Should a lost `prompt.error` also become recoverable? | **Yes — persist a failure marker before publishing.** | Today the worker's error paths (`prompt_assistant.py:52,65-68,74-77,92-95,138-142`) publish only; nothing is written to the session. So a refetch after a failed turn returns a history ending in the *user* turn, indistinguishable from "still running". Persisting a marker alongside the existing `append_message` closes the last content gap for almost no cost. It changes the session message shape, so it is called out rather than assumed. |
| Q-6 | Does this depend on any open dossier, or overlap the a2a orchestration audit? | No. `depends_on: []`. | Checked against `BOARD.md`. The a2a audit covers orchestration and turn locking; nothing there touches prompt studio. |

## 4. Reproduction

**Path (b), the wedge — deterministic, in a unit test.** Far more reliable than the manual
route, and this is what the regression suite should encode. In
`frontend/src/slices/prompt-studio/__tests__/usePromptAssistantSocket.test.ts` the transport is
already mocked with captured `statusHandlers` (`:13,21-24`) that no existing test ever drives:

```
emit({ type: 'prompt.token', text: 'partial' })
statusHandlers.forEach(h => h(false))   // socket drops
statusHandlers.forEach(h => h(true))    // reconnects; the terminal frame was lost
// today: api.streaming.value === true, forever
```

**Path (b) — manual.** Send a prompt that produces a long reply; once tokens appear, toggle
DevTools → Network → Offline for ~2s and back. The socket reconnects (backoff floor
`INITIAL_BACKOFF_MS = 1_000`, `frontend/src/shared/transport/ws-manager.ts:34`, doubling at
`:347`); the worker finishes and publishes into a channel with no subscriber. The streaming
bubble (`PromptAssistantPanel.vue:168-175`) freezes with the partial text and the Send button
(`:195-204`) stays disabled forever. Reload → new session, empty history.

**Path (a), silent loss.** The window is `[postMessage enqueues] → [ticket fetch + WS upgrade +
owner check completes]`, against a `prompt.error` emitted within milliseconds of job start
(`prompt_assistant.py:49-53`). To reproduce reliably: force an immediate worker error (the
`session not found` path at `:49-53` fires before any DB session is opened), and widen the
client side by blocking `/ws/` at the proxy for one attempt — the reconnect backoff floor
guarantees ≥1s unsubscribed. **Observed**: the panel shows the optimistically-pushed user turn
(`usePromptAssistantSocket.ts:82-85`) and then nothing — no alert (`PromptAssistantPanel.vue:178-184`
is gated on `errorMessage`), no spinner (`streaming` was never set, since no token arrived).
Confirm the quota was still charged by inspecting `prompt:quota:{config_id}:{user_id}:{day}`
(`backend/contexts/prompt_studio/infrastructure/session_store.py:33-34`).

## 5. Root Cause Analysis

**Root cause: the channel is fire-and-forget with no durable read side**, so any lost frame is
unrecoverable by construction. Three composing facts:

1. No replay, by contract — `pubsub.py:3-6`.
2. No read side — the session HTTP surface is create + post only
   (`prompt_studio.py:740-773`), despite the reply being persisted at
   `prompt_assistant.py:146-147`.
3. No client liveness check — `usePromptAssistantSocket.ts:35,41`.

Paths (a) and (b) differ only in **which** frame is lost and therefore how the loss presents.
The wedge is a second-order consequence of (3) layered on the same cause, not an independent
defect: `streaming` gates a control rather than a spinner, so any state a lost frame can
strand is a permanently stranded control.

**Notable asymmetry making the fix cheap:** ownership verification and session read already
exist server-side (`session_service.py:72-82`,
`backend/contexts/prompt_studio/interfaces/facade.py:30-42`) and are exercised by the WS route.
The missing piece is purely an HTTP projection of a capability the backend already has.

## 6. Blast Radius and Sibling Suspects

**Direct blast radius**: every Prompt Studio assistant panel — personal, org, admin and project
scope all render the same `PromptAssistantPanel.vue`.

Seven WebSocket paths are consumed by the frontend. (`/ws/audit-tail` has a backend route but
no frontend consumer at all.)

| Channel | Recovery mechanism | Verdict |
|---|---|---|
| `/chatroom/{id}` | `replayDelta()` on connect (`useChatroomSocket.ts:396`) + degraded-mode 10s poll (`:59-65`) + a 120s re-armed watchdog (`:26,196-213,284`) | **Cleared** — the exemplar; has both halves |
| `/workflow-runs/{id}` | `syncOnReconnect()` on every connect (`useWorkflowRunSocket.ts:29-52,83`) fetching authoritative steps | **Cleared.** No watchdog, but run state is driven by REST invalidation (`:65-71`), not a socket-only flag, so no control wedges |
| `/graphrag/{id}`, `/knowmap/{id}` | 15s backstop poll (`useBuildStateSocket.ts:17,80-87`) + resync on connect (`:146-148`) + `initialState` seed (`:128-136`) | **Cleared** — explicitly hardened by prior audits |
| `/rag-configs/{id}` | REST is the source of truth by design (`useRagConfigSocket.ts:5-12`); `syncState()` on mount/connect/activate (`:174-189`) + 15s poll (`:119-127`) | **Cleared** — arguably the strongest: WS frames are pure change-triggers |
| `/prompt-assistant/{id}` | **none** | **Confirmed vulnerable — this defect** |
| `/user/{id}` | mixed, three additive subscribers | **Partially vulnerable — see below** |

The `/user/{id}` channel, broken out:

- `useNotificationsSocket.ts:35-39` — a lost frame means one missed invalidation; the list and
  unread count are TanStack queries refetched by normal means. **Cleared.**
- `useBanKickGuard.ts` — a lost ban/kick frame delays a forced logout until the next auth
  round-trip; the server rejects the banned user regardless. Fails safe. **Cleared.**
- `useObservations.ts:144-175` — **structurally the same shape as this defect, one notch less
  severe.** `observation.started` sets `setObserverAnalyzing(..., true)` (`:147`), cleared only
  by the terminal frames (`:154,159,168`), with no watchdog. A lost terminal frame strands the
  flag permanently. **But**: the flag is consumed at `:79` to derive a display badge only and
  gates no `disabled` binding anywhere, so **no control wedges**; and the observation *content*
  is recoverable, since `observation.created` triggers a REST invalidation (`:162`) and the
  query has a 30s `refetchInterval` (`:110-111`). **Stale badge, not this defect** — recorded
  as FU-2, and the natural second beneficiary of whatever watchdog helper this fix produces.

**Conclusion**: prompt-assistant is the sole channel with *neither* mechanism and the sole
channel where a lost terminal frame disables a control. The audit's "internal inconsistency"
framing holds under a full sweep.

## 7. Fix Design

Three layers, deliberately separable (§9 sequences them).

**1. Session read endpoint.** `GET /api/prompt-assistant/sessions/{session_id}` on
`session_router` (`prompt_studio.py:69`), returning `{session_id, messages: [{role, content}]}`.
`Depends(current_principal)` plus a `require_owned_session` call — **reuse, do not
reimplement**: `backend/tests/unit/test_ws_prompt_assistant.py:1-8` documents a prior
regression where the WS route hand-rolled the check and diverged. The response must be an
explicit Pydantic projection, not a passthrough, so a future field added to `AssistantSession`
cannot leak by default.

**2. Refetch on connect, with a generation guard.** In `usePromptAssistantSocket`'s `onStatus`
handler, mirroring `useChatroomSocket.ts:383-402`. Four properties to copy deliberately:

- Fire on **every** connect transition, not only reconnects (Q-2).
- Carry a **monotonic generation guard** (`useChatroomSocket.ts:77,102,105`): capture
  `++generation`, drop the result if a newer invocation started meanwhile. The same guard
  appears independently in `useWorkflowRunSocket.ts:24-34`, `useBuildStateSocket.ts:105-117`
  and `useRagConfigSocket.ts:94-101` — it is the house pattern for any async resync, each
  instance acquired from a real audit finding, and a flapping socket trivially overlaps two
  refetches. **This is the single most important detail to get right.**
- **Reconcile, do not blindly append.** Prompt-studio messages have no ids
  (`backend/contexts/prompt_studio/domain/models.py:96-98` — role and content only), so
  id-based dedup is unavailable. Treat the server list as authoritative and replace wholesale,
  keeping the local optimistic user turn (`usePromptAssistantSocket.ts:82-85`) only when the
  server list is shorter.
- **Clear stale in-flight state on connect, before refetching** — the lines at
  `useChatroomSocket.ts:392-395` that make chatroom immune to the wedge: a reconnect
  unconditionally resets the thinking flags, so a lost terminal frame cannot strand them. This
  is arguably the cheapest correct fix for the wedge on its own and belongs in the change
  regardless of the watchdog.
- Handle 404 as a distinct state, not a transient error (see §9's TTL risk).

**3. Streaming watchdog**, transliterating `useChatroomSocket.ts:26,196-213`: a module constant
exported for tests, an idempotent `clear`, an `arm` that clears then sets, **re-armed on every
token** so the bound is inter-token silence rather than total duration (`ASSISTANT_MAX_TOKENS =
2_048` at `domain/models.py:32` bounds total output, so this is safe). On fire, clear the
stranded flag **and** set an error state so the user sees why rather than a silent reset. Tear
down in `onDeactivated`/`onBeforeUnmount` and in the `sessionId` watch
(`usePromptAssistantSocket.ts:62-74`).

**4. Failure marker** (Q-5): the worker persists a marker to the session before publishing
`prompt.error`, alongside the existing `append_message` at `prompt_assistant.py:147`.

**Why this corrects rather than masks.** The defect is a missing durable read side, not a
missing timer. Adding only the watchdog would leave every lost reply lost while making the UI
pretend nothing happened. Adding the read side restores the second half of the contract
`pubsub.py:5-6` already states, and the watchdog then bounds the residual case where the
refetch itself cannot help (network down, session expired).

## 8. Regression Test Plan

**Frontend — `usePromptAssistantSocket.test.ts`** (extend; `statusHandlers` at `:13,21-24` are
already captured and never driven; the four existing tests at `:62-110` must keep passing).

**The failing test comes first** — `clears streaming when the socket reconnects mid-turn`: emit
`prompt.token`, drive `statusHandlers → false` then `→ true`, expect
`api.streaming.value === false`. **Fails today**: `:36,41` clear `streaming` only in the
terminal-frame branches; a status transition touches only `connected` (`:51-53`).

Then:

| Test | Why it fails today |
|---|---|
| `refetches the session on every connect, including the first` | no such API method exists (`api/index.ts:166-174`) and the composable makes no call from `onStatus` |
| `reconciles a reply that arrived while disconnected` (assert the assistant turn appears exactly once and the optimistic user turn is not duplicated) | no refetch, no reconciliation |
| `clears streaming after the watchdog timeout with no terminal frame` (fake timers) | no timer is ever armed |
| `re-arms the watchdog on each token` | ditto — mirrors `useChatroomSocket.ts:284` |
| `tolerates a 404 from the session refetch` (expired session → `streaming` false, expiry signalled, no unhandled rejection) | the call does not exist |

**Frontend — `PromptAssistantPanel.test.ts`** (extend; currently two tests at `:20-38`). Add
the control-level assertion, which is the one that speaks to user impact:
`re-enables the Send button after a mid-turn reconnect` — assert the `button` element has no
`disabled` attribute. Fails today via `PromptAssistantPanel.vue:198` → `SButton.vue:29,38`.
Note this file uses MSW and the `renderView`/`settle` helpers from `__tests__/kit.ts`, so the
WS transport is not mocked the way it is in the composable test; it will need a targeted
`vi.mock('@shared/transport', …)` consistent with the sibling file.

**Frontend — `api/__tests__/index.spec.ts`**: assert `promptStudioApi.getSession(id)` dispatches
to the generated service and returns the bare body. Requires `pnpm run gen:api` after the
backend route lands; `pnpm run check:openapi-drift` gates staleness.

**Backend — new `backend/tests/unit/test_prompt_studio_session_read.py`.** There is currently
**no test anywhere under `backend/tests/` exercising the session HTTP routes**; the
service-level tests exist and the WS ownership gate is well covered
(`test_ws_prompt_assistant.py:66-137`). The route layer is the gap.

| Test | Why it fails today |
|---|---|
| owner reads own session → 200, messages in order | route does not exist |
| non-owner → **404, not 403**, with `prompt-studio/session-not-found` | pins the deliberate not-found/wrong-owner collapse (`session_service.py:75-81`, `interfaces/error_mapping.py:64`) so a refactor cannot regress it into an existence oracle |
| expired session → 404 | pins the TTL contract (`session_store.py:96`) |
| unauthenticated → 401 | — |
| response carries no key material | guards R29.14 (`prompt_studio.py:10`) against a future widening of the response model |

If the failure marker (Q-5) ships, extend `test_prompt_assistant_worker.py`: the worker
persists a marker before publishing `prompt.error`, asserting `append_message` is called on the
error paths where today only the success path persists (`:147`).

## 9. Risks and Rollback

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Refetch/optimistic-turn duplication** — messages have no ids, so dedup cannot be id-based | medium | visible UI corruption | Treat the server list as authoritative and replace wholesale; explicit test |
| **Overlapping refetches on a flapping socket** — a slow earlier fetch resolves last over fresher data | medium | stale history | Generation guard (§7). Four composables have it; each acquired it from a real finding |
| **Session-expired 404 treated as a transient error** | medium | confusing error if unhandled | `SESSION_TTL_SECONDS` is 2 hours (`domain/models.py:29`), refreshed on every `append_message` (`session_store.py:80,96`), so the clock restarts at the reply — a realistic reconnect is nowhere near the boundary. But a tab left overnight genuinely loses the session: handle 404 as a distinct state, allow a fresh session |
| **Watchdog too short** — trips on a slow provider | low | spurious error, wasted turn | Re-arm on every token, not a total-duration bound |
| **Refetch storm** — every reconnect issues a GET, and backoff retries indefinitely (`ws-manager.ts:335-348`) | low | backend load | Guard against in-flight duplicates; the generation guard gives this naturally. The GET is a single Redis read |
| **OpenAPI drift** — client not regenerated | low | build break | `pnpm run gen:api`; CI gates it |

**What this does not fix**, stated plainly: a reply lost after the 2h TTL has expired is gone
permanently (Redis is the only store — `session_store.py:96`, `domain/models.py:29`, and
"no server-side persistence beyond the live session (R29.07)" at `session_store.py:4-5`); the
quota and message-cap charge for a lost turn is never refunded on the success path
(`_refund_quota` covers only pre-provider failures); and a frame lost with no reconnect at all
remains unrecoverable until FU-1.

**Security.** The endpoint returns prompt-session content — the user's messages and the
assistant's replies, which routinely contain the system prompts and template drafts being
authored. Not key material, but genuinely sensitive. Non-negotiables: **404, never 403**, for a
non-owner (documented twice in comments and once in the error mapping, and pinned by a test);
**no admin bypass** (Q-4); no key material in the response (satisfied by construction, enforced
by an explicit projection); and no logging of the response body, which is user prose that may
embed credentials the user pasted. Rate limiting deserves a decision — the POST is bounded by
the quota and message cap, a GET has neither, and it will be called on every reconnect.

**Rollback**, in three independently revertible layers, committed separately:

1. **Backend read endpoint** — purely additive; nothing else calls it. Reverting removes an
   unused endpoint. No migration (Redis-only).
2. **Frontend watchdog** — self-contained. Safe to ship **before** (1), and worth doing so: it
   independently unwedges the composer, which is the user-visible harm, while the read endpoint
   follows.
3. **Frontend refetch/reconciliation** — depends on (1). Reverting alone is safe and degrades
   to watchdog-only. **Order any rollback (3) → (1)**, and deploy backend first, or every
   reconnect 404s.

The optional failure marker is a fourth piece; if it ships, (1)'s response model must tolerate
its absence, since the 2h TTL means the mixed population self-clears within two hours.

## 10. Acceptance Criteria

- [ ] AC-1: `clears streaming when the socket reconnects mid-turn` (§8) fails against current
      code and passes after the fix.
- [ ] AC-2: a reply that arrives while the client is disconnected is present in the panel after
      reconnect, exactly once.
- [ ] AC-3: the Send button is never permanently disabled — after any reconnect, and after the
      watchdog fires, it is usable again.
- [ ] AC-4: the watchdog is re-armed on every token, so a long legitimate reply does not trip
      it.
- [ ] AC-5: a non-owner requesting a session receives 404, not 403; a platform admin receives
      404 for another user's session.
- [ ] AC-6: an expired session yields 404 and the client renders an expiry state rather than a
      transient error.
- [ ] AC-7: overlapping refetches cannot apply stale data — pinned by a generation-guard test.
- [ ] AC-8: the response body contains only role/content pairs.
- [ ] AC-9: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass in `backend/`;
      `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm run check:openapi-drift` pass in
      `frontend/`.

## 11. SRS Delta

None. No `[Rxx.yy]` governs prompt-assistant delivery; this implements the client half of the
delivery contract `shared_kernel/realtime/pubsub.py:3-6` already states. See FU-3.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — Replay/cursor semantics on the pub/sub layer (Redis Streams). `pubsub.py:4-7`
  anticipates it and notes the `Publisher`/`Subscriber` shape would survive. It is the generic
  fix for every lost-frame class on all seven channels; deliberately not coupled to this
  dossier. **It now has at least three known consumers**: this defect, and F-11 ("message edits
  and deletions that occur during a disconnect are never reconciled") and F-13 ("approvals
  raised while a socket is disconnected never appear") of
  `docs/audits/2026-07-22-agent-to-user-conversation/`. All three are the same class — a frame
  lost during a disconnect with no durable read side — reached from different channels. The
  per-channel fixes in this dossier and in that audit's dossiers are correct and should ship;
  FU-1 is what would stop the class recurring on the next channel added. Whoever picks it up
  should read all three findings together.
- **FU-2** — `useObservations.ts:144-175` strands `observerAnalyzing` on a lost terminal
  observation frame. Content is recoverable and no control wedges, so it is a stale badge
  rather than this defect — but it is the same shape and the natural second consumer of the
  watchdog helper this fix produces.
- **FU-3** — No SRS entry states the prompt-assistant delivery guarantee. The contract lives
  only in a `shared_kernel` docstring.
- **FU-4** — The quota and message-cap charge for a turn whose reply is lost is never refunded
  on the success path. `_refund_quota` (`prompt_assistant.py:36-39`) covers only pre-provider
  failures. Out of scope here, but it is real money on a BYO-key product.
- **FU-5** — `connection_loop` accepts an `on_open` hook (`shared_kernel/realtime/connection.py:183`)
  that no prompt-assistant caller uses. A server-side snapshot pushed on open would close the
  handshake window from the other side; redundant once the read endpoint exists, but worth
  knowing the seam is there.
</content>
