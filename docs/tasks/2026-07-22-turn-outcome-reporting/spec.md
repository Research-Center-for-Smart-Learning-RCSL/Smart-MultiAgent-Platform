---
type: bugfix
status: in-progress
created: 2026-07-22
requirements: [R11.02, R13.19, R13.27, R15.01, R24.23]
depends_on: []
---

# A turn's outcome is reported from whichever step failed last, not from what the turn actually did

## 1. Summary

Four findings on how a turn's outcome reaches the user. In two of them the outcome is
*derived from a proxy signal* rather than from the turn's real result: a successful,
committed agent reply is recorded and dispatched as a failure when the post-commit Redis
publish raises (F-6), and a healthy turn is reported to the user as `timeout` because the
client's 120-second watchdog has no event to re-arm on during the pre-stream assembly
window (F-15). In the other two the outcome is *not reported at all*: a failed `/compact`
clears the composer, surfaces nothing, and raises an unhandled rejection (F-9), and text
streamed during non-final tool rounds is silently discarded on `agent.finished` (F-40,
identical to agent-config-runtime F-32). The user-visible effect is a turn whose reported
outcome and whose actual outcome disagree in both directions — successful turns shown or
recorded as failed, and failed actions shown as nothing at all.

**Do the four share a root cause?** *Two of them do; four of them do not.* F-6 and F-15
are the same mistake on two sides of the wire: the outcome is inferred from the last thing
that went wrong (a publish exception; a silence) instead of from the turn's actual result
(a committed reply row; a running provider call). F-9 is an absent-report defect on a
different surface — the slash-command branch of the composer, which never enters the turn
engine at all. F-40 is a stream-presentation defect: the outcome is reported correctly, the
*intermediate* rendering is discarded. **F-9 and F-40 are grouped here by change surface,
not by cause, and this dossier says so rather than stretching one framing over four
defects.** The grouping is still the right one: F-15 and F-40 are both fixed by the same
new mid-turn progress event (§7), and F-9 sits in the file the F-15 work already opens.

Sources: `docs/audits/2026-07-22-agent-to-user-conversation/findings.md` F-6, F-9, F-15;
`docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md` F-40, which that audit
deferred to this triage (`findings.md:1190`) because the fix spans the streaming and
frontend-draft code examined here; `docs/audits/2026-07-22-agent-config-runtime/findings.md`
F-32 (`:1150-1173`), the same defect recorded from the comment side.

## 2. Observed vs Expected

### F-6 — a committed reply recorded as a failed turn

- **Observed** — `backend/contexts/agents/application/runtime/turn_engine.py:2200` and
  `:2209` emit `message.created` and `agent.finished` as bare `await`s inside the turn's
  main `try`, after the commit at `:2189`. `shared_kernel/realtime/pubsub.py:31-34`
  propagates whatever `redis.publish` raises, and `shared_kernel/auth/clients.py:42-49`
  sets only `retry_on_timeout=True`, so a `ConnectionError`, `MISCONF` or
  `OOM command not allowed` reaches `except Exception` at `:2220`. That handler rolls back
  (a no-op — the reply is already committed), writes an `agent.turn_failed` audit for a
  fully successful turn, requeues notifications the agent already consumed
  (`:2243`), restores the one-shot compact flag (`:2245`), and returns `status="failed"`
  (`:2246`) — so `_dispatch_agent_message_signal` (`:2213`) and
  `_dispatch_agent_reply_wakeups` (`:2217`) never run. `backend/app/workers/tasks/
  orchestration.py:146,164-165` then writes `wakeup.failed` and skips
  `on_agent_message_sent`, so the autostop round goes uncounted.
- **Expected** — the three post-commit steps are explicitly documented as best-effort. The
  docstring at `turn_engine.py:2210-2212` states they are "Best-effort, post-commit — never
  fails the turn"; `:2194-2195` states the publish is deliberately after the commit. Every
  other emit in the engine already swallows: `:1542-1561` (with a comment arguing the case),
  `:2228-2236`, `_emit_observation_event`'s internal guard at `:2290-2296`. A turn that
  committed a reply row must return `status="completed"`, audit `agent.turn_finished`
  (which it already did at `:2188`), and run its downstream dispatches. Intent source:
  internal inconsistency, plus R15.01 and R11.02 as cited in the code at `:2214-2216`.

### F-9 — `/compact` failure reports nothing

- **Observed** — `frontend/src/slices/conversation/composables/useChatroomMessages.ts:195-199`
  clears `draft.value` and then `await compactChatroom(chatroomId)` with no `try`; the
  surrounding `try` only begins at `:229`, after the early `return true` at `:198`.
  `frontend/src/slices/conversation/views/ChatroomView.vue:612-615` awaits `onSend` with no
  `.catch` and binds `send` as an event handler, so the rejection is unhandled. The user's
  text is gone, no toast fires, and `onSend` reports success.
- **Expected** — `docs/UI/12-shared-patterns.md:451-455`: every optimistic action has a
  rollback and an error toast. The correct pattern already exists two files over:
  `frontend/src/slices/conversation/views/ChatroomSettingsView.vue:180-197`
  (`try` → `toast.success(t('conversation.settings.compactRequested'))` /
  `catch` → `toast.error(t('conversation.settings.compactFailed'))` / `finally`).

### F-15 — the watchdog fires on healthy turns

- **Observed** — `frontend/src/slices/conversation/composables/useChatroomSocket.ts:24`
  sets `AGENT_THINKING_TIMEOUT_MS = 120_000`; `armThinkingTimeout` (`:160-170`) is called
  only from `agent.thinking` (`:234`), `agent.token` (`:241`) and `agent.finished` (`:265`).
  Between `agent.thinking` (`turn_engine.py:1783`) and the first `agent.token`
  (`:2677`, reached via `:2106`) the engine's only emit is `:1542` — `agent.warning`, for
  which the client `switch` has no `case` and falls to `default: break` (`:334-335`). That
  window contains `_pending_context_and_tools`, `_resolve_skills`, `_stage_workspace_inputs`
  and `_assemble_history`, the last of which takes `distributed_lock("compact:lock:{room}",
  ttl_s=300)` at `:2526` and may spend a full summariser provider call. On expiry
  `:166-168` calls `clearAllAgentThinking`, `clearAgentStream(roomId)` with **no** `agentId`
  — `frontend/src/slices/conversation/stores/conversation.ts:116-119` deletes the whole room
  key — and `setAgentError(roomId, 'timeout')`, which `ChatroomView.vue:698-702` turns into
  an error toast.
- **Expected** — `docs/UI/07-conversation.md:488` defines the watchdog as detecting a
  *wedged* turn ("if no `agent.token` or `agent.finished` arrives within 120 seconds"). The
  state machine at `:454-458` has `Idle → Thinking → Streaming → Idle` with no long-assembly
  state, i.e. the spec assumes `Thinking` is short. A turn that is making progress must not
  be reported as `timeout`.

### F-40 / F-32 — multi-round tool text is streamed then discarded

- **Observed** — `turn_engine.py:2673-2679` emits `agent.token` for every `TokenDelta`
  inside the `for rounds in range(1, MAX_TOOL_ROUNDS + 1)` loop at `:2651`, unconditionally
  and not gated to the final round. `:2683` overwrites `last_text` each round; only the
  no-tool-calls exit at `:2686` or the post-loop `:2758` value is persisted, via
  `MessageService.send_agent` at `:2181`. The client appends every token into one per-agent
  draft with no per-round reset (`useChatroomSocket.ts:237-241`,
  `conversation.ts:97-103`) and clears it on `agent.finished` (`:243-252`). The user watches
  round-1 and round-2 preambles accumulate, then the round-3 answer, then everything but the
  round-3 answer vanishes.
- **Expected** — `docs/UI/07-conversation.md:522-528`, the Completion Transition: "No
  visible flash — the transition is seamless because streaming content matches final
  content." For a multi-round tool turn the streamed content does *not* match the final
  content, which is the defect. The related residue from F-32 is the comment at
  `useChatroomSocket.ts:180-183`, which asserts that `clearAgentStream` "defers to
  `applyMessageCreated` (post-append) to avoid the streamed-draft flicker" — a guarantee
  `:243-252` undoes one frame later.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Do the four share a root cause? | **Two do.** F-6 and F-15 both derive the outcome from a proxy (a publish exception; a silence) rather than the turn's result. F-9 (absent report on the slash-command path) and F-40 (stream presentation) are grouped by change surface. | Stated in §1 so nobody hunts for a single unifying patch. The grouping still holds: F-15 and F-40 share one fix (§7 C3/C4) and F-9 lives in the file C4 already opens. |
| Q-2 | Is F-15's false-positive window **caused by**, **masked by**, or **independent of** the 120-second socket reaping that `docs/tasks/2026-07-22-chatroom-socket-lifecycle/` fixes? | **Independent in cause; almost entirely masked in effect — and the mask is the socket-lifecycle dossier's to remove.** See the derivation below. | The watchdog's cause is purely client-side: no event re-arms it during assembly. But `useChatroomSocket.ts:347-356` runs `clearAllAgentThinking`, `clearAgentStream(roomId)` and **`clearThinkingTimeout()`** on every reconnect. a2u F-1 (`shared_kernel/realtime/connection.py:259-269`, `_IDLE_TIMEOUT_SECONDS = 120`) makes every socket reconnect on a ~121s cycle, so a reconnect lands inside virtually every 120s watchdog window and disarms the timer before it can fire. Today the user therefore usually sees a *different* wrong outcome — spinner and draft silently wiped at reconnect, no error at all — which is the same family of defect from a third angle (§6, S-4). |
| Q-3 | Do Q-2's two dossiers have an ordering constraint? | **Yes, and it is directional: this dossier's C3 must land before or with the socket-lifecycle fix.** No `depends_on` in the other direction. | Once a client `ping` keeps the socket alive, the reconnect at `:347-356` stops firing every two minutes and the mask is gone. F-15 then becomes deterministic: any compact-mode assembly exceeding 120s produces a spurious `timeout` toast that today almost never reaches the user. Landing socket-lifecycle alone converts a masked defect into a visible regression. Record this in that dossier too, not only here. |
| Q-4 | Does F-6 overlap `docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md` (draft), which adds a stranded-turn reaper and changes turn cleanup on cancellation? | **No textual overlap. One contiguous adjacency and two semantic couplings.** F-6 is about a turn's *recorded outcome*; that dossier is about a turn's *cleanup*. | Line ranges below. |
| Q-5 | Which side fixes F-40 — suppress non-final `agent.token` on the backend, or reset the draft on the frontend? | **Neither alone: emit an explicit mid-turn progress event and reset on it.** | Backend suppression is *impossible* without destroying streaming: `_stream_with_tools` only learns a round was non-final at `:2684`, after `StreamComplete` — it would have to buffer the entire round. Frontend-only reset has no boundary signal to reset on: the engine emits nothing between `:2679` and `:2679` of the next round. A round-boundary event supplies exactly the missing signal. |
| Q-6 | Does the F-40 fix require changing the unconditional draft clear at `useChatroomSocket.ts:243-252`? | **No — and this is deliberate, not evasion.** The new reset is a *mid-turn* clear at a round boundary; the *finish-time* clear stays exactly as it is. | The unconditional finish-time clear is a test-locked decision (the "BUG-1 fix", `useChatroomSocket.ts:247-251`, asserted at `frontend/src/slices/conversation/__tests__/useChatroomSocket.test.ts:138-148` and `:150-159`) guarding a ghost bubble when `message.created` is lost on reconnect. §8 records it as a decision to revisit — it contradicts `docs/UI/07-conversation.md:535` — but this fix does not depend on revisiting it. |
| Q-7 | F-6 has already persisted wrong outcomes. Repair or leave? | **Leave the rows; do not mutate audit history. Ship an identification query, not a backfill.** | Position stated in §7. |
| Q-8 | Reuse `conversation.settings.compact*` i18n keys for the composer's `/compact`, or add new ones? | **Reuse.** `frontend/src/slices/conversation/locales/en.json:257-258` and the zh-TW sibling already carry `compactRequested` / `compactFailed`, and both strings are surface-neutral. | Avoids two translations of one sentence. If a composer-specific wording is later wanted, add `conversation.chatroom.compact*` to **both** locale files. |

## 4. Reproduction

| # | Finding | Tier | Recipe |
|---|---|---|---|
| R1 | F-6 | unit | Patch `turn_engine.Publisher` so `emit("message.created", …)` raises `redis.exceptions.ConnectionError`; run `_run_locked` with a fake router returning plain text and a fake `MessageService.send_agent` that records the call. Observe: `send_agent` was called, `TurnResult.status == "failed"`, an `agent.turn_failed` audit was written, `_requeue_notifications` was called, and neither `_dispatch_agent_message_signal` nor `_dispatch_agent_reply_wakeups` ran. |
| R2 | F-6 end-to-end | integration | Commit a reply, then `SIGSTOP`/firewall Redis between `:2189` and `:2200`. Only form that proves the durable half; **R1 is the gate, R2 is optional evidence.** |
| R3 | F-9 | unit (Vitest) | Mount `useChatroomMessages` with `compactChatroom` rejecting (the mock already exists at `frontend/src/slices/conversation/__tests__/useChatroomMessages.test.ts:22`). Set `draft.value = '/compact'`, call `onSend()`. Observe: the returned promise rejects, `draft.value === ''`, and `toast.error` was never called. |
| R4 | F-15 | unit (Vitest, fake timers) | Emit `agent.thinking`, advance 120_000 ms with no further events. Observe `agentError[ROOM] === 'timeout'` and `agentStreams[ROOM]` deleted. The test harness in `__tests__/useChatroomSocket.test.ts` already installs fake timers (`vi.useRealTimers()` teardown at `:124`). |
| R5 | F-15 masked path (Q-2 evidence) | unit (Vitest, fake timers) | Same as R4, but drive the status callback to `false` then `true` at t=60s. Observe the watchdog never fires because `:352` disarmed it, and the spinner and draft are gone with **no** error set. Documents the mask that the socket-lifecycle dossier removes. |
| R6 | F-40 | unit (pytest) | `test_agent_turn_loop.py:69-118`'s `_FakeRouter` already yields `TokenDelta("think")` in the tool round and `TokenDelta("done")` in the final round; `:113-114` asserts **both** were published. That existing assertion *is* the reproduction — it pins the round-1 token as user-visible while `:100` pins `text == "done"` as the only persisted value. |

F-15 and F-40 are deterministic under fake timers / fake routers; only R2 needs infrastructure.

## 5. Root Cause Analysis

**F-6.** Trigger: `redis.publish` raises inside `pubsub.publish`
(`shared_kernel/realtime/pubsub.py:34`) because `clients.py:42-49` retries only on timeout.
Link 1: `turn_engine.py:2200`/`:2209` are the **only** post-commit emits in the engine
without a guard (§6). Link 2: they sit inside the `try` opened at `:1778`, whose scope was
sized for the *whole* turn including the pre-commit work. Link 3: `except Exception` at
`:2220` treats every exception in that scope as "the turn failed", because at the time the
scope was written every exception in it *did* mean that. **Root cause: the turn's failure
scope extends past the point where the turn's result becomes durable.** The earliest
correction that prevents the symptom is to end the failure scope at the commit — everything
after `:2189` is post-outcome bookkeeping and must report its own failures without changing
the turn's outcome. The unguarded emit is the trigger, not the root cause: guarding only the
emit would leave `_persist_artifacts` (`:2193`) and the two dispatches (`:2213`, `:2217`)
inside the same over-wide scope, each of which already documents itself as best-effort.

**F-9.** Trigger: `compactChatroom` rejects. Link 1: the slash-command branch at
`useChatroomMessages.ts:195-199` returns before the `try` at `:229`. Link 2: the branch
performs the optimistic mutation (`draft.value = ''`, `:196`) *before* the await, with no
rollback path. Link 3: `ChatroomView.vue:612-615` neither catches nor is caught.
**Root cause: an early-return branch was added inside a function whose error handling is
implemented after the early return.** Not an outcome-derivation defect — an outcome that is
never derived at all.

**F-15.** Trigger: assembly exceeds 120 s (reachable: `:2526`'s compaction lock is
`ttl_s=300`, and `STREAM_TIMEOUT` on the summariser call is per-read, not wall-clock).
Link 1: `armThinkingTimeout` is re-armed only by the three cases at `:234`, `:241`, `:265`.
Link 2: the engine emits nothing between `agent.thinking` (`:1783`) and the first token
(`:2677`) except `agent.warning` (`:1542`), which the client's `switch` drops at `:334`.
Link 3: on expiry, silence is *interpreted* as failure — `setAgentError(roomId, 'timeout')`
at `:168`. **Root cause: the client infers a turn's outcome from absence of evidence, and
the protocol provides no evidence of liveness during the phase that legitimately produces
none.** Aggravating factor, not cause: the room-wide `clearAgentStream(roomId)` at `:167`
(no `agentId`), which makes a single stuck agent wipe every agent's draft
(`conversation.ts:116-119`).

**F-40.** Trigger: any turn with ≥ 1 tool round. Link 1: `:2673-2679` emits every
`TokenDelta` regardless of round. Link 2: `:2683` keeps only the last round's text and
`:2181` persists only that. Link 3: the client accumulates all rounds into one draft
(`:237-241`) with no reset point. **Root cause: the streamed representation of a turn and
the persisted representation of a turn are produced by two rules that were never reconciled
— "stream everything" and "persist the last round".** The client is a faithful renderer of
the first rule and is then corrected by the second.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** F-6 is gated on Redis availability; on a hard outage the user sees nothing
either way, so the durable damage is (a) `agent.turn_failed` audit rows for successful turns,
(b) lost workflow `message` triggers and other agents' `every_n`/silence wakeups for replies
that exist in the database, (c) notifications requeued into the agent's next turn that it
already consumed, and (d) `wakeup.failed` plus an uncounted autostop round
(`orchestration.py:146,164-165`). F-9 affects any user typing `/compact` during a backend
blip. F-15 affects every room in compact mode whose history crosses the cap, but is masked
today (Q-2). F-40 is cosmetic and affects every multi-round tool turn in every room; the
intermediate text is not lost from the model's reasoning, which is folded forward at
`turn_engine.py:2688,2720-2728`.

**Sibling suspects.**

- **S-1 — `empty_reply` branch, `turn_engine.py:2128-2130`. CONFIRMED, in scope.** Same
  shape as F-6: the emit is post-commit (commit at `:2125`) and unguarded inside the same
  `try`. A publish failure there turns `skipped/empty_reply` into `failed` and takes the
  same wrong cleanup path. Fixed by the same change.
- **S-2 — `no_input` branch, `turn_engine.py:2084`. CLEARED.** Unguarded, but **pre**-commit
  (`await self._db.commit()` at `:2092`). Nothing durable exists yet, so routing to the
  failure path is the correct outcome.
- **S-3 — observer branch, `turn_engine.py:2170`. CLEARED.** Post-commit (commit at `:2166`)
  but routed through `_emit_observation_event`, which catches and logs internally at
  `:2290-2296`.
- **S-4 — reconnect clears an in-flight turn, `useChatroomSocket.ts:347-356`. CONFIRMED,
  deliberately out of scope.** On every reconnect the client runs `clearAllAgentThinking`,
  `clearAgentStream(roomId)` and `clearThinkingTimeout()`. A healthy in-flight turn loses
  its spinner and draft with **no** outcome reported — the third instance of this dossier's
  pattern. It is out of scope because the correct fix (replay in-flight turn state on
  reconnect) belongs with the socket-lifecycle work, which owns that code path. FU-1.
- **S-5 — `_persist_artifacts`, `turn_engine.py:2193`. CLEARED as a defect, IN SCOPE as
  region.** It is post-commit and unguarded *at the call site*, but the function opens its
  own `try` and documents itself as "Best-effort and in its own transaction so a storage
  hiccup never rolls back the already-committed reply" (`:1398-1414`). Its intent already
  matches the fix; moving the call inside the new post-commit guard is a no-op for it and
  makes the intent structural rather than conventional.
- **S-6 — user-send and edit/delete emits, `backend/app/api/v1/messages.py:203-214`,
  `:438-449`, `:482-488`. CLEARED.** Every post-commit publish on the REST message path is
  already wrapped in `try/except Exception`. **`turn_engine.py:2200/2209` and `:2128` are
  the only unguarded post-commit emits in the repo** — which is itself worth stating: this
  is a singular omission, not a systemic pattern, so the fix does not need generalising.
- **S-7 — other optimistic composer actions in `useChatroomMessages.ts`. CLEARED.** The
  normal send path at `:190-250` implements the full pattern: optimistic insert (`:217`),
  cache seed (`:238-242`), rollback with draft restore in `catch` (`:245-249`). F-9's branch
  is the one that returns before reaching it.
- **S-8 — the compaction lock's other consumer, `turn_engine.py:2526`. CLEARED for this
  dossier.** It is the *source* of F-15's long window, not a second instance of the defect.
  Shortening it is owned by `docs/tasks/2026-07-22-compaction-scoping-and-durability/`;
  F-15's fix must not assume that work lands, because any long pre-stream phase reproduces it.

## 7. Fix Design

Four independently revertible commits. **C1 and C2 are corrections at the point where the
outcome is decided; C3 and C4 supply the missing signal rather than lengthening a timer.**

**C1 — end the turn's failure scope at the commit (F-6, S-1).** In `_run_locked`, move the
post-commit steps at `:2193-2217` out of the outcome-deciding `try` — either into a
`_finish_committed_turn` helper whose body is individually guarded, or by wrapping the block
in its own `try/except Exception` that logs and continues. Each of the four steps
(`_persist_artifacts`, the two emits, the two dispatches) fails independently: a failed
publish must not skip the dispatches, and a failed dispatch must not skip the other. Log
every swallowed failure with `_log.warning(..., exc_info=True)`, following the precedent and
the reasoning already written at `:1556-1561` ("a warning that can vanish without trace is
not a signal") — do **not** use a bare `except: pass`.

*Why this corrects rather than masks.* Wrapping only the two emits in `try/except` would
also stop the symptom, but it would leave the real defect — a failure scope that outlives
the turn's result — intact for the next post-commit step somebody adds. The correction is
structural: after `:2189` the turn's outcome is a fact, and no later code may change it.
That is precisely the guarantee `:2210-2212` already claims in prose.

*Data repair position (Q-7).* **No backfill, no mutation.** `audit_logs` is append-only and
rewriting it to say a turn succeeded would destroy the only record that the incident
occurred. The affected rows are exactly identifiable — a turn that emitted **both**
`agent.turn_finished` (written at `:2188`, committed at `:2189`) and `agent.turn_failed`
(written at `:2238`) for the same agent and room — because F-6 is the only path that can
produce that pair. Ship that identification query in the dossier and, if an operator wants
one, a read-only report; do not automate a correction. The non-durable consequences are not
retroactively repairable and must be stated as accepted loss: the lost workflow `message`
signal and `every_n`/silence wakeups cannot be replayed after the fact, the duplicated
notifications were already folded into the agent's next prompt, and the uncounted autostop
round has already shifted that run's accounting. All are bounded to Redis-outage windows.

**C2 — report `/compact`'s outcome (F-9).** In `useChatroomMessages.ts:195-199`, wrap the
call in `try/catch`, restore `draft.value` to the original text on failure,
`toast.error(t('conversation.settings.compactFailed'))`, and `return false`; on success
`toast.success(t('conversation.settings.compactRequested'))` and `return true`. `toast` and
`t` are already in scope at `:38-39`. Both keys exist in both locale files
(`locales/en.json:257-258`) per Q-8, so no i18n addition is needed — all user-facing text
goes through `$t()` unchanged. This mirrors `ChatroomSettingsView.vue:180-197` exactly. As a
defensive second layer, add a `.catch` in `ChatroomView.vue:612-615` so no future branch of
`onSend` can produce an unhandled rejection from an event handler.

**C3 — emit turn liveness, and consume it (F-15).** Add one new WS event,
`agent.progress`, carrying `{agent_id, phase}`. Emit it from the engine at the named
boundaries of the currently silent window — after `_pending_context_and_tools` (`:1796`),
after `_resolve_skills` (`:1817`), after `_stage_workspace_inputs` (`:1821`), and after
`_assemble_history` (`:1868`), including around the compaction call at `:2526` — each emit
guarded like `:1542-1561`. In `useChatroomSocket.ts` add a `case 'agent.progress'` that
calls `armThinkingTimeout()`. Also add the missing `case 'agent.warning'` re-arm, since
`:1542` is already a proof of liveness the client currently discards.

*Why this corrects rather than masks.* Raising `AGENT_THINKING_TIMEOUT_MS` would mask it:
the watchdog would still be inferring outcome from silence, just over a longer silence, and
would still be wrong for any assembly slower than the new constant while being slower to
catch a genuinely wedged turn. Supplying evidence of liveness removes the inference. As a
secondary correction in the same commit, scope the expiry's `clearAgentStream(roomId)` at
`:167` to the agents actually in `agentThinking[roomId]` rather than the whole room key, so
a single stuck agent stops wiping its neighbours' drafts.

**C4 — reset the draft at a tool-round boundary (F-40, F-32).** Reuse C3's event: emit
`agent.progress` with a round-boundary phase at `_stream_with_tools:2688`, immediately
before the assistant tool-use turn is appended — i.e. exactly when the round's streamed text
has been superseded. In `useChatroomSocket.ts`, that phase additionally calls
`store.clearAgentStream(roomId, agentId)` for the emitting agent. The result is that the
draft shows the current round only, and on `agent.finished` the streamed content and the
final content match — which is what `docs/UI/07-conversation.md:522-528` asserts.

*Why this corrects rather than masks.* The alternative — suppressing non-final
`agent.token` emits — is not implementable (Q-5) and would in any case leave the user
staring at a blank bubble through several provider calls, replacing a wrong outcome with no
outcome. Per Q-6, this does **not** touch the finish-time clear at `:243-252`, so the
test-locked BUG-1 decision is untouched by design, not routed around. Finally, correct the
stale comment at `:180-183` (F-32's actionable residue) so it describes what the code
actually guarantees.

**Ordering.** C3 before C4 (C4 consumes C3's event). C1 and C2 are order-free. Per Q-3, C3
must be merged before or with `docs/tasks/2026-07-22-chatroom-socket-lifecycle/`.

## 8. Regression Test Plan

**The failing test comes first in each group.**

**New `backend/tests/unit/test_turn_outcome_reporting.py`** (F-6, S-1). Patch
`turn_engine.Publisher` per the existing precedent at `test_agent_turn_loop.py:72-79`.

- **`test_committed_turn_survives_a_failed_message_created_publish`** — the failing test.
  Asserts `TurnResult.status == "completed"`, `TurnResult.message_id` is the committed row's
  id, `_dispatch_agent_message_signal` and `_dispatch_agent_reply_wakeups` were both called,
  and no `agent.turn_failed` audit was written, when `emit("message.created", …)` raises.
  **Fails today**: control reaches `:2220` and returns `status="failed"` at `:2246` with the
  audit written at `:2238`.
- `test_committed_turn_survives_a_failed_agent_finished_publish` — same, raising at `:2209`.
  **Fails today** for the same reason.
- `test_committed_turn_does_not_requeue_notifications_on_publish_failure` — asserts
  `_requeue_notifications` is not called. **Fails today**: `:2243` runs unconditionally on
  the failure path.
- `test_empty_reply_publish_failure_stays_skipped` (S-1) — asserts
  `status="skipped", reason="empty_reply"` when `:2128` raises. **Fails today**: same `:2220`
  handler.
- `test_dispatch_failure_does_not_change_turn_outcome` — make `_dispatch_agent_message_signal`
  raise; assert `status == "completed"` and that `_dispatch_agent_reply_wakeups` still ran.
  **Fails today**: `:2213` raising skips `:2217` and lands on `:2220`.

**`frontend/src/slices/conversation/__tests__/useChatroomMessages.test.ts`** (F-9). The
`compactChatroom` mock already exists at `:22`.

- **`test /compact failure restores the draft and toasts`** — the failing test. Reject the
  mock; assert `onSend()` resolves `false` (not rejects), `draft.value === '/compact'`, and
  `toast.error` was called with `conversation.settings.compactFailed`. **Fails today**:
  `useChatroomMessages.ts:196` clears the draft before the await and `:197` has no catch, so
  the promise rejects, the draft stays empty and no toast fires.
- `test /compact success toasts and clears` — asserts the success toast and `draft.value === ''`.
  **Fails today**: no success toast exists on this path.

**`frontend/src/slices/conversation/__tests__/useChatroomSocket.test.ts`** (F-15, F-40).
Fake timers are already installed (teardown at `:124`).

- **`test agent.progress re-arms the thinking watchdog`** — the failing test. Emit
  `agent.thinking`, advance 119_000 ms, emit `agent.progress`, advance a further 119_000 ms;
  assert `agentError[ROOM]` is falsy and the agent is still in `agentThinking[ROOM]`.
  **Fails today**: `handleEvent`'s `switch` has no `agent.progress` case and falls to
  `default: break` (`:334-335`), so the timer armed at `:234` expires and `:168` sets
  `'timeout'`.
- `test agent.warning re-arms the thinking watchdog` — same shape. **Fails today** for the
  same reason; `turn_engine.py:1542` already emits this event and the client drops it.
- `test the watchdog clears only the thinking agents' drafts` — two agents, only one
  thinking; assert the idle agent's draft survives expiry. **Fails today**: `:167` calls
  `clearAgentStream(roomId)` with no `agentId`, which deletes the whole room key at
  `conversation.ts:116-119`.
- `test a round boundary resets the streaming draft` (F-40) — emit `agent.thinking`, tokens
  `"round one"`, `agent.progress` with the round-boundary phase, token `"final"`; assert
  `agentStreams[ROOM][AGENT] === 'final'`. **Fails today**: `:237-241` appends with no reset
  point, yielding `'round onefinal'`.

**`backend/tests/unit/test_agent_turn_loop.py`** (F-40 backend half).

- `test_stream_with_tools_emits_a_round_boundary_event` — extend the existing
  `test_stream_with_tools_runs_one_tool_round` (`:69-118`), whose `_Pub` already collects
  every emit into `events`. Assert an `agent.progress` round-boundary event appears in
  `events` between the `"think"` and `"done"` tokens. **Fails today**: the engine emits
  nothing between rounds — the only emits in the loop are the `agent.token` calls at `:2677`.

**Two existing tests deliberately pin behaviour this area touches. Both are decisions to
revisit, not obstacles.**

1. **`test_agent_turn_loop.py:113`** asserts
   `("agent.token", {"text": "think", …}) in events` — it *pins the discarded round-1 text
   as user-visible*, which is F-40's symptom asserted as a requirement. C4 does not break it
   (tokens still stream; only the client's accumulation resets), so it can stay green. But
   it should be re-read after C4 with the question it was never asked: **is streaming
   superseded text intended, or was it merely what the code did when the test was written?**
   If the answer is "unintended", the honest follow-up is to gate the emit behind an explicit
   decision, not to leave the assertion standing as accidental intent. FU-2.
2. **`useChatroomSocket.test.ts:138-148` and `:150-159`** pin the unconditional finish-time
   draft clear (the "BUG-1 fix", `useChatroomSocket.ts:247-251`), guarding a ghost bubble
   when `message.created` is lost on reconnect. `docs/audits/2026-07-22-agent-config-runtime/findings.md:1168-1173`
   established this is deliberate and test-locked. **C4 is designed not to disturb it (Q-6).**
   However, `:150-159` — which asserts the draft is dropped on `agent.finished{error}` —
   **directly contradicts `docs/UI/07-conversation.md:535`**, which specifies that on error
   "the incomplete streaming bubble remains visible but the cursor stops blinking". One of
   the two is wrong, and the discrepancy predates this dossier. Do not silently change
   either: record it, decide it with the user, and update the losing side. FU-3.

## 9. Risks and Rollback

| Risk | Mitigation |
|---|---|
| **C1 hides a real failure.** A turn whose reply committed but whose dispatches all failed now reports `completed`, which is true of the turn but not of its side effects. | This is the correct outcome by the contract at `:2210-2212`, and it is what the REST message path already does (S-6). Every swallowed failure is logged with `exc_info` per `:1556-1561`, so the signal survives even though the outcome does not change. |
| **C3 adds WS traffic on every turn.** Four to six extra frames per turn per room. | Negligible against `agent.token`, which is one frame per token (`:2677`). The events are ids-and-phase only — no content — so they carry no disclosure risk on the room channel, unlike the concern documented at `:1547-1553`. |
| **C3's new event is not understood by an old client.** | Additive: `handleEvent`'s `default: break` (`:334-335`) ignores unknown types, so an un-upgraded tab degrades to today's behaviour rather than erroring. |
| **C4 resets a draft the user was reading.** The round-1 preamble disappears mid-turn rather than at the end. | That is the point — it disappears either way today, just later and all at once. Losing it at the boundary is what makes the finish-time transition seamless per `docs/UI/07-conversation.md:522-528`. |
| **Q-3 ordering is missed and socket-lifecycle merges first.** Spurious `timeout` toasts on healthy compact-mode turns become visible. | AC-8 makes the ordering an acceptance criterion here; §9's coordination note must be copied into the socket-lifecycle dossier. |
| Migration | **None.** No schema change in any commit. |

**Rollback.** All four commits are pure code and independently revertible. Unwind C4 before
C3 (C4 consumes C3's event). Reverting C1 restores the wrong outcome but loses no data —
nothing it writes is new. No data migration to unwind, which is a direct consequence of the
Q-7 no-backfill position.

**Coordination (no `depends_on`, per Q-9 of the sibling dossiers).**

- **With `docs/tasks/2026-07-22-chatroom-socket-lifecycle/`.** Per Q-2/Q-3:
  the 120 s reaping currently masks F-15 by disarming the watchdog at
  `useChatroomSocket.ts:352` on every reconnect. **C3 should merge before or with that
  dossier.** That dossier should also pick up S-4 (`:347-356` silently discards an in-flight
  turn's spinner and draft on reconnect), which is this dossier's pattern in code that
  dossier owns.
- **With `docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md` (draft).** Per Q-4:
  edit regions `2193-2218` (here) and `2220-2246` (there) are disjoint and contiguous; its
  C1 already states the invariant C1-here needs (`spec.md:119-120`); its C6 touches `:2181`,
  one line above this region. Named in both rather than left to a merge.

## 10. Acceptance Criteria

- [ ] AC-1: `test_committed_turn_survives_a_failed_message_created_publish` (§8) fails
      against current code and passes after.
- [ ] AC-2: a turn whose reply committed reports `status="completed"`, audits only
      `agent.turn_finished`, and runs both post-commit dispatches, regardless of any
      post-commit publish, artifact-persistence or dispatch failure — and the same holds for
      the `empty_reply` branch reporting `skipped`.
- [ ] AC-3: every swallowed post-commit failure is logged with a stack, and none of them
      requeues notifications or restores the compact flag.
- [x] AC-4: a failed `/compact` restores the user's text, surfaces an error toast via
      `$t()`, resolves `false`, and produces no unhandled rejection.
      *Verified by the three tests in `useChatroomMessages.test.ts`'s
      `/compact outcome reporting (F-9)` block, all three failing before C2 for the
      documented reason.*
- [ ] AC-5: the thinking watchdog is re-armed by `agent.progress` and `agent.warning`, and a
      turn that emits progress every < 120 s never reports `timeout`.
      *Half done: the `agent.warning` re-arm shipped (that event already exists), verified by
      `useChatroomSocket.test.ts`'s `re-arms the timeout on agent.warning (F-15)`. The
      `agent.progress` half waits on C3's backend emit — see D-1/FU-7.*
- [x] AC-6: when the watchdog does fire, it clears only the drafts of agents currently
      marked thinking in that room.
      *Verified by `clears only the thinking agents drafts when it fires`, failing before the
      change. A second test pins the empty-set fallback (D-7).*
- [ ] AC-7: on a multi-round tool turn the streamed draft contains only the current round's
      text, so the draft at `agent.finished` matches the persisted reply.
- [ ] AC-8: the Q-3 ordering constraint is recorded in
      `docs/tasks/2026-07-22-chatroom-socket-lifecycle/` before that dossier is approved.
- [ ] AC-9: the two test-locked decisions in §8 are either left green with the reasoning
      recorded, or changed with the user's decision recorded in §12 — neither is silently
      edited.
- [x] AC-10: the comment at `useChatroomSocket.ts:180-183` describes what the code
      guarantees (F-32 residue). *Now at `:297-302`; rewritten to say the deferral is a
      flicker preference rather than a guarantee, since `agent.finished` clears the draft
      unconditionally whichever frame wins.*
- [ ] AC-11: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass in
      `backend/`; `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build` pass in
      `frontend/`.

## 11. SRS Delta

**One addition, one correction.**

- **Addition.** No `[Rxx.yy]` states that a turn's reported outcome must reflect its durable
  result rather than its last-failed step. R13.19 enumerates the chatroom WS event set but
  says nothing about the relationship between `agent.finished`, the persisted reply, and the
  `agent.turn_finished` / `agent.turn_failed` audit pair. The contract lives only in the
  docstring at `turn_engine.py:2210-2212`, which is exactly the condition that let the
  unguarded emit above it go unnoticed. Draft: *a turn that has committed a reply reports
  `completed`; post-commit publication and dispatch are best-effort and never alter the
  reported outcome.*
- **Correction.** `docs/UI/07-conversation.md:530-535` (Error State) says the incomplete
  streaming bubble remains visible on error; `useChatroomSocket.ts:243-252` clears it and
  `__tests__/useChatroomSocket.test.ts:150-159` pins the clear. The UI doc and the code
  disagree and have done since before this dossier. Resolve per FU-3 and correct whichever
  side loses — do not leave both standing.

If the new `agent.progress` event is added, it must also be listed in
`docs/UI/07-conversation.md:1378`'s event table alongside `agent.token`.

## 12. Deviation Log

- **D-1 — only C2 was built; C1, C3 and C4 are deferred.** Decided with the user on
  2026-07-31 at the start of the build. `turn_engine.py` is **under concurrent
  construction** by `2026-07-22-turn-idempotency-and-locking`: at the moment of the
  decision its C1 sat uncommitted in the working tree (`_finalize_failed_turn`, the
  `except BaseException` cleanup path, `_run_uncancellable`, and the drain moved into
  `run_turn`'s `finally`), it landed as `2fafc4e` while this build was in flight, and the
  file was dirty again with that dossier's next commit before this one closed. C1, C3 and
  C4 all edit that same file and C1 edits the same function, and CLAUDE.md's commit
  discipline forbids staging another task's in-progress work. The frontend-only C2 has
  zero overlap, so it shipped alone.
  **The remaining three commits are unstarted, not partially done** — the working tree
  carries nothing from them. See FU-7.
- **D-2 — AC-8 was satisfied in substance but not in the letter, and cannot now be.** It
  required the Q-3 ordering constraint to be recorded in
  `docs/tasks/2026-07-22-chatroom-socket-lifecycle/` *before that dossier is approved*.
  That dossier is `status: implemented` and landed 2026-07-24; it recorded the constraint
  at close-out instead, as its FU-8, which names this dossier as the owner of the fix and
  recommends prioritising it. Nothing further is achievable here.
- **D-3 — the §11 SRS addition shipped as `[R13.27]`, worded slightly wider than the
  draft.** Placed in §13.7 Realtime after R13.20 (`REQUIREMENTS.md:723`). The draft covered
  the committed-reply case; the shipped text also names the committed *skip* branches
  (`empty_reply`, `no_input`, `knowledge_starved`), which are the same invariant on the
  same commit boundary and would otherwise have to be inferred. The §11 *correction* was
  not applied — it is FU-3's to resolve, per D-4.
- **D-4 — FU-3 decided: leave both test-locked assertions green (AC-9, first branch).**
  Decided with the user on 2026-07-31. `useChatroomSocket.test.ts` and
  `docs/UI/07-conversation.md:535` still contradict each other on whether the streaming
  bubble survives an error; neither side was edited. Reasoning: C4 is designed not to
  disturb the finish-time clear (Q-6), so the contradiction blocks nothing here, and
  FU-3's own note is that the whole decision is worth re-deriving once reconnect
  reconciliation lands — deciding it now would be deciding it on stale premises.
- **D-5 — a sibling the spec did not have was found during freshness re-verification and
  is added to C1's scope.** The `knowledge_starved` branch (`turn_engine.py:2438` commit →
  `:2443`/`:2447` unguarded emit) is F-6's exact shape, alongside S-1. S-2 (`no_input`) was
  re-checked and stays correctly cleared: its emit at `:2454` is still pre-commit
  (`:2462`). C1's post-commit block has also grown two members the spec did not list —
  `self._compact_forced_rooms.pop()` and `_settle_pending_approvals` — both of which must
  move inside the new guard with their own individual guards.
- **D-7 — C3's two backend-independent slices shipped ahead of the rest, and AC-6 gained a
  fallback the spec did not ask for.** With the user's agreement on 2026-07-31, the
  `agent.warning` re-arm (AC-5, half) and the watchdog draft scoping (AC-6) landed without
  waiting on `turn_engine.py`, since neither needs a new event: `agent.warning` is already
  emitted (`turn_engine.py:1794`) and already documented (`REQUIREMENTS.md:717`). AC-10
  went with them. **`case 'agent.progress'` was deliberately not added**: its payload shape
  and the round-boundary phase value C4 keys off are contract details C3's backend half
  must settle, and writing them with no producer would pin them prematurely.
  The fallback: when the thinking set is empty the clear stays room-wide. The literal AC
  would clear nothing there and strand a draft with no later event to remove it. That state
  needs a lost `agent.thinking` frame with no reconnect — near-unreachable, since a
  reconnect clears both — so AC-6's scoping is defence in depth and the fallback keeps
  today's behaviour for the one case it does not cover. Both arms are tested.
- **D-6 — every `path:line` in §1–§9 is stale.** `turn_engine.py` is now 3372 lines (was
  ~2760 when this was written) and `useChatroomSocket.ts` was reshaped by
  `chatroom-socket-lifecycle`. Every cited *behaviour* was re-verified as still present at
  approval; the numbers were left in place rather than rewritten, matching the convention
  `turn-idempotency-and-locking`'s §9 set. Current addresses: F-6 `:2597` commit /
  `:2608`+`:2619` emits / `:2643` handler; S-1 `:2504` commit / `:2507` emit; F-9
  `useChatroomMessages.ts:241-245` and `ChatroomView.vue:667-670`; F-15 watchdog
  `useChatroomSocket.ts:269-279`, `default: break` at `:458`; F-40 round boundary
  `turn_engine.py:3226`; AC-10's stale comment `useChatroomSocket.ts:289-291`.

## 13. Follow-ups

- **FU-1** — S-4: `useChatroomSocket.ts:347-356` discards an in-flight turn's spinner and
  draft on every reconnect with no outcome reported. Same family as F-15; belongs with
  `docs/tasks/2026-07-22-chatroom-socket-lifecycle/`, which owns that code path. Its correct
  fix is to replay in-flight turn state on reconnect, which needs a server-side notion of
  "turns currently running in this room" that does not exist today.
- **FU-2** — `test_agent_turn_loop.py:113` asserts that superseded round-1 text is published
  to the room. C4 keeps it green, but it pins as intent something that was probably an
  accident. Decide it explicitly.
- **FU-3** — `useChatroomSocket.test.ts:150-159` and `docs/UI/07-conversation.md:535`
  contradict each other on whether the streaming bubble survives an error. Decide with the
  user and correct the losing side. Related: the BUG-1 ghost-bubble guard that the test pins
  (`useChatroomSocket.ts:247-251`) exists because `message.created` can be lost on
  reconnect — which is a2u F-11/F-13's territory, so once reconnect reconciliation is fixed
  the guard's premise weakens and the whole decision is worth re-deriving.
- **FU-4** — The `agent.warning` event (`turn_engine.py:1542-1561`) has no client handler at
  all. C3 makes it re-arm the watchdog, but the warning itself — "skills unavailable" — is
  still never shown to anyone, and `:1556-1561` argues at length that it must not vanish
  without trace. It currently does.
- **FU-5** — F-6's non-durable consequences (lost workflow `message` signals, lost
  `every_n`/silence wakeups, an uncounted autostop round) have no detection mechanism. The
  identification query from §7 finds the audit-row evidence after the fact; nothing notices
  at the time. A counter on swallowed post-commit dispatch failures would make the class
  observable.
- **FU-7** — **C1, C4 and the backend half of C3 are unbuilt** (D-1). They are blocked only
  on `2026-07-22-turn-idempotency-and-locking` finishing with `turn_engine.py`, not on any
  finding here. What remains, in order: **C1** (end the failure scope at the commit —
  F-6, S-1, plus the `knowledge_starved` sibling of D-5), **C3's backend half** (emit
  `agent.progress` at the four named assembly boundaries and around the compaction call,
  then add its client `case`), then **C4** (reuse that event at the round boundary,
  `turn_engine.py:3226`, and reset the draft on it). C3's frontend-independent slices and
  AC-10 are already done (D-7). Unblocked ACs remaining: AC-1, AC-2, AC-3, the
  `agent.progress` half of AC-5, and AC-7.
- **FU-8** — `onSend` (`useChatroomMessages.ts:236`) mixes slash-command dispatch,
  optimistic insert, scroll, mention resolution, the POST and rollback across ~77 lines;
  C2 added 13 of them. Extracting the `/compact` branch into a named local would undo the
  increment. Raised by the quality gate as Info, not fixed, because C2's design was
  explicitly an in-place mirror of `ChatroomSettingsView.vue:183-190`.
- **FU-9** — that mirroring is now literal duplication: the
  `compactChatroom` → `compactRequested`/`compactFailed` pair exists in both
  `useChatroomMessages.ts:243-256` and `ChatroomSettingsView.vue:183-190`. Only ~5 lines
  are shared and the surroundings differ (confirm dialog + spinner vs draft restore +
  boolean return), so extraction is not yet worth it. A third surface would change that.
- **FU-6** — `AGENT_THINKING_TIMEOUT_MS` (`useChatroomSocket.ts:24`) and
  `_IDLE_TIMEOUT_SECONDS` (`shared_kernel/realtime/connection.py`) are both 120 and are
  unrelated constants that happen to collide, which is what makes Q-2's masking near-total.
  After the socket-lifecycle fix, neither should be chosen with reference to the other, and
  a comment on each saying so would prevent a future reader inferring a relationship.
</content>
