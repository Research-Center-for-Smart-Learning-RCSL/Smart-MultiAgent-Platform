---
type: bugfix
status: in-progress
created: 2026-08-16
requirements: [R15.01, R15.02, R15.03, R28.04, R30.17]
depends_on: []
---

# Activity submissions never touch the wake-up system, so agents read worksheet time as a lull

## 1. Summary

Submitting an activity writes a real, transcript-visible message into the room, but the submit
route never evaluates wake-ups. The per-agent silence clock is therefore never re-armed by a
submission. During a silent worksheet phase - the exact phase the shipped course is built
around - the peer agent SA sees an untouched clock, decides the room has gone quiet, and posts
into it; the teacher agent TA wakes on SA's message and replies; SA fires once more before its
autostop cap. Four agent messages interrupt a deliberately silent phase, while the submissions
that are the actual content of the lesson produce no reaction at all.

[R15.02] defines the silence trigger as "no message arrives for T minutes", and a submission
demonstrably writes a message row. F-2 of
`docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`, the strongest of the
four runtime findings.

## 2. Observed vs Expected

**Observed.**

- A submission writes a room message: `SubmissionService.submit` calls
  `ConversationFacade.insert_system_message(...)` at
  `backend/contexts/activities/application/submission_service.py:205-221`, with
  `_ECHO_TYPE = "activity_submission"` (`:55`).
- `insert_system_message` (`backend/contexts/conversation/interfaces/facade.py:200-221`) is a
  pure `self._messages.create(...)` wrapper that stamps `metadata["type"]` (`:214`). It triggers
  no orchestration; traced in full.
- The submit route commits (`backend/app/api/v1/activities.py:769`) then calls only
  `_dispatch_submission` (`:770`), whose entire body (`:801-831`) is a WS emit plus two
  enqueues. It never calls `evaluate_message_wakeups`.
- The only three production callers of `evaluate_message_wakeups` are
  `backend/app/api/v1/messages.py:269`,
  `backend/contexts/agents/application/runtime/turn_engine.py:3177`, and
  `backend/app/api/v1/observations.py:209`.
- So `touch_silence_timestamp` (reached only from
  `backend/contexts/orchestration/application/wakeup_service.py:105`), `reset_autostop`
  (`:107-108`) and `increment_message_count` (`:111`) never fire for a submission.
- The silence trigger reads that same timestamp
  (`wakeup_service.py:226`, compared against `t_minutes` at `:231-232`).
- The shipped SA config is `silence_minutes {enabled: true, t_minutes: 3, autostop_rounds: 2}`
  with `every_n_messages {enabled: false}`
  (`backend/contexts/agents/infrastructure/examples/packs/creative-thinking-room.json:37-41`).
- The presence gate does not save it: `evaluate_silence_trigger` gates on
  `PresenceTracker.list_room` (`wakeup_service.py:243-246`), which is WS-connection based with a
  150s TTL refreshed on every inbound frame
  (`backend/contexts/conversation/infrastructure/presence.py:45`, `:125-143`, `:243-248`).
  Students sitting in the room filling a worksheet hold live sockets, so the roster is non-empty
  and the gate passes.

**Expected.** A submission is room activity: it re-arms the silence clock, so an agent
configured to speak "on a lull" does not treat active worksheet time as a lull.

**Intent sources.**

- **[R15.02]** - the silence trigger fires when no message arrives for T minutes. The echo is a
  message.
- `wakeup_service.py:76-77` - the trigger's own docstring says it "counts all messages in the
  room - user + agent".
- `docs/examples/creative-thinking-course.md:160` ("TA ... responds to every message") and
  `:268-271` ("Room agents read a digest of recent structured activity, which is what lets TA
  respond to what the class actually wrote"). Both describe a loop that does not close; §7.4
  corrects them rather than the code, per Q-1.
- No `[Rxx.yy]`, comment, or dossier entry excludes `activity_submission` messages from
  wake-ups, so this is a gap rather than a documented decision.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Should a submission wake `every_n_messages` agents, re-arm only the silence clock, or neither (document the exclusion)? | **Re-arm the silence clock only.** Submissions do not count toward `every_n_messages`. | User decision. Full evaluation is the idiomatic-looking choice and is what the docs currently promise, but TA ships `every_n_messages {enabled: true, n: 1}` (`creative-thinking-room.json:16`) and nothing in the counting path suppresses by message type (`wakeup_service.py:110-113`), so a 28-student class would produce up to 28 TA turns on the teacher's own provider key. Turn coalescing does not help - it collapses a *simultaneous* burst (`turn_engine.py:409-448`), not submissions spread over eight minutes - and the 30-turns/300s rate limit (`turn_engine.py:238-239`, `:3216-3227`) caps the storm rather than preventing it. That is worse than the defect. |
| Q-2 | Should a submission reset `autostop`? | **No.** Leave `reset_autostop` gated on `sender_is_user` as today. | Not a user question, and deliberately conservative. [R15.03] lifts the cap "until a user sends a new message"; a submission is a user *action* but not a user message, and the fix chosen in Q-1 already establishes that distinction. Resetting would make SA available for unbounded further rounds across a long activity, which is a chattiness change nobody asked for. Recorded as FU-1 so it can be revisited with evidence from a classroom run. |
| Q-3 | Where does the call belong: the route, or `SubmissionService`? | **The route** (`app/api/v1/activities.py`), inside `_dispatch_submission`. | Not a user question - the layering decides it. `contexts/activities/interfaces/facade.py:5-6` states the context "imports only the conversation facade + shared_kernel"; calling orchestration from `SubmissionService` would create a new `activities -> orchestration` edge. The route already imports `contexts.conversation.interfaces` and `.interfaces.access` (`activities.py:40-46`), exactly as `messages.py:33-37` does. The echo is also written *inside* the request transaction (`submission_service.py:205-221`), before the route commits at `:769`, so a service-level call would violate the never-dispatch-inside-the-transaction rule at `triggers.py:12-18`. |
| Q-4 | Reuse an existing entry point or add one? | **Add a sibling in `triggers.py`** delegating to the existing `OrchestrationFacade.on_users_present`. | Not a user question. The exact machinery already exists: `evaluate_presence_change` (`backend/contexts/conversation/application/triggers.py:122-144`) -> `OrchestrationFacade.on_users_present` (`backend/contexts/orchestration/interfaces/facade.py:148-157`) -> `WakeupService.on_users_present` (`wakeup_service.py:255-269`), whose entire body is `touch_silence_timestamp` per bound agent. But `evaluate_presence_change`'s docstring says "a user joined a room" (`:127-133`), so calling it for a submission would be a semantic lie. A sibling function is ~15 lines, adds no facade method and no new context edge. |
| Q-5 | Does any unfinished dossier conflict? | **No - `depends_on: []`.** | `docs/tasks/BOARD.md` lists `2026-07-07-graphrag-two-axis-redesign` (graphrag) and `2026-07-19-large-artifacts-silently-dropped`, whose surface is `kernel.py` / `turn_engine.py:1133-1136` / `attachment_service.py`. This task touches `turn_engine.py` not at all. `2026-08-16-example-docs-corrections` also edits `docs/examples/creative-thinking-course.md`, but a different section; rebase rather than sequence. |

## 4. Reproduction

**Preconditions.** A chatroom in project P with the shipped `creative-thinking-room` pack
installed and TA and SA bound; an activation of `mandala-9grid` running; students connected over
WebSocket.

**Steps.**

1. Note the time. Have every student open the activity form and begin filling it in without
   sending any chat message.
2. Have students submit as they finish, spread over roughly eight minutes.
3. Send no chat message at any point.

**Actual.** At roughly T+3 minutes SA posts a peer comment into the room even though students
have been submitting continuously; TA (`every_n_messages n=1`) wakes on SA's message and
replies; SA fires again at roughly T+6 before its `autostop_rounds: 2` stops it, and TA replies
again. Four agent messages land in a phase the lesson plan intends to be silent. Meanwhile every
submission produces no TA reaction.

**Expected.** SA does not fire while submissions keep arriving inside its three-minute window.
When a genuine three-minute lull occurs with neither a submission nor a chat message, SA fires
exactly once as designed.

**Deterministic shortcut for a test**: assert that `POST /api/chatrooms/{id}/activity-submissions`
results in a silence-timestamp write for each bound agent. That is AC-1.

## 5. Root Cause Analysis

1. **Root cause.** `_dispatch_submission` (`backend/app/api/v1/activities.py:801-831`) is the
   submit path's single post-commit fan-out point, and it performs three side effects - WS emit,
   validation enqueue, workflow-signal enqueue - and not the fourth. The message the transaction
   just wrote is never announced to the wake-up system. Adding that announcement here prevents
   the symptom.
2. The wake-up state is written only from `WakeupService`, and `touch_silence_timestamp`
   (`backend/contexts/orchestration/infrastructure/wakeup_state.py:103-110`) is reached from
   exactly three places: `on_message_created` (`wakeup_service.py:105`), the first arm of
   `evaluate_silence_trigger` (`:228`), and `on_users_present` (`:269`), plus a post-fire
   debounce in `backend/app/workers/tasks/orchestration.py:305`. None is on the submit path.
3. There is **no generic "last room activity" timestamp** to fall back on - the clock is
   per-`(agent, room)` in Redis (`wakeup_state.py:25-38`), so nothing else could have covered
   this incidentally.

**Why it was not caught.** There is **no test anywhere** for `_dispatch_submission` or the
submissions route: a grep across `backend/tests` for `_dispatch_submission`,
`submit_activity` and `activity-submissions` returns nothing. The eight tests that cover
`SubmissionService.submit` (`backend/tests/unit/test_activities_services.py:668-950`) all stub
`ss.ConversationFacade`, so they assert the echo's *content* and never observe what the route
does with it afterwards.

**A closely related precedent that shows the fix is idiomatic.**
`backend/app/api/v1/observations.py:209` calls `evaluate_message_wakeups` with
`sender_is_user=True` for a message that is structurally identical to the echo -
`SenderType.SYSTEM`, `sender_id=None`
(`backend/contexts/conversation/application/observation_service.py:175-181`) - with the comment
at `observations.py:194-196`: "R28.06: a released room message is an ordinary message from the
wake-up system's point of view". `sender_is_user` is a caller-supplied boolean at all three call
sites and is never derived from the row, so the platform already treats some system rows as
ordinary messages. This dossier deliberately does **not** go that far (Q-1), but it establishes
that the seam is a policy choice rather than a type constraint.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every chatroom running any activity type with any agent configured on
`silence_minutes`, not only the shipped packs. The symptom is agent noise during structured
work and, symmetrically, a silence trigger that fires early because it measured the wrong thing.
No data is affected; no submission is lost or mis-scored.

**Sibling suspects** - every other path that writes a room message without announcing it:

| Site | Verdict |
|---|---|
| `backend/app/api/v1/messages.py:269` (user send) | **cleared** - calls `evaluate_message_wakeups` post-commit. |
| `backend/contexts/agents/application/runtime/turn_engine.py:3177` (agent reply) | **cleared** - same, with `sender_is_user=False`. |
| `backend/app/api/v1/observations.py:209` (observation release) | **cleared** - same, with `sender_is_user=True` deliberately. |
| `backend/contexts/activities/application/submission_service.py:205-221` (submission echo) | **confirmed** - this defect. |
| `backend/contexts/agents/application/runtime/transcript.py:238` (compaction summary) | **cleared, and correctly so.** It is the only other production caller of `insert_system_message`, and it deliberately wakes nobody: a maintenance row, rendered to the model with role `"system"` (`transcript.py:135`), unlike the activity echo which renders as role `"user"`. The codebase already distinguishes maintenance rows from participant-visible ones. |
| Activation start / end broadcasts (`activities.py:843-847`, `dispatch_activation_ended` at `:852-863`) | **cleared** - these emit WS events; they write no message row, so there is nothing for the wake-up system to observe. |

`insert_system_message` therefore has exactly two production callers, and the fix distinguishes
them on the ground the codebase already uses.

## 7. Fix Design

**7.1 A sibling trigger entry point.** `backend/contexts/conversation/application/triggers.py`
gains a function beside `evaluate_presence_change` (`:122-144`) - name it for what it means, e.g.
`evaluate_room_activity(db, *, chatroom_id)` - that lists the room's bound agents via the
existing `list_bound_agents` (`:39-45`) and calls `OrchestrationFacade.on_users_present(room_id,
agent_ids)`. Its docstring must state plainly that it re-arms the silence clock **without**
counting toward `every_n_messages`, and why (Q-1).

Reusing `on_users_present` rather than adding a facade method is deliberate: its entire body is
the per-agent `touch_silence_timestamp` this fix needs (`wakeup_service.py:255-269`). Two benign
differences from the `on_message_created` touch, both worth a comment: it does not skip inert or
`call_only` agents (`:268-269` versus `:102-105`), so it may write a key an inert agent never
reads - harmless, and TTL-expired; and it covers observers, which is correct, since AA also runs
on `silence_minutes` (`creative-thinking-room.json:61-65`).

**7.2 Call it post-commit from the route.** `_dispatch_submission`
(`backend/app/api/v1/activities.py:801-831`) gains a `db` parameter - the route already commits
at `:769` before calling it at `:770` - and calls the new function in the same best-effort style
the function already documents at `:804-805` ("the submission is committed, so a Redis or
pub/sub hiccup must never surface as a failed submission"). Follow `messages.py:236-240`'s
`_rollback_quietly` idiom for the failure arm.

**7.3 What is deliberately not done.** No call to `evaluate_message_wakeups`, and specifically
**not** a call whose result is discarded - that would still run `increment_message_count`
(`wakeup_service.py:111`), silently consuming the modulus for every agent with `n > 1` and
drifting them off their intended cadence against real chat messages. This trap is named here
because the discard version looks like a safe middle ground and is not.

**7.4 Documentation.** `docs/examples/creative-thinking-course.md:160` and `:268-271` must state
the exclusion: TA responds to every **chat** message; a submission re-arms the silence clock so
SA does not mistake worksheet time for a lull, but does not itself wake an agent. The
`wakeup_service.py:76-77` docstring gains the same carve-out.

**Why this does not mask the symptom.** The symptom is a silence trigger measuring the wrong
thing; the cause is that the room's activity is not reported to it. The fix reports it. What it
consciously does **not** do is make TA reactive to submissions - that is a separate capability
(FU-2), not a defect.

**Data repair.** None.

## 8. Regression Test Plan

The failing test comes first. Note §5: there is currently **no test at all** for this route or
dispatcher, so the first task is to create the harness.

**8.1 The failing test.** New, in the idiom of
`backend/tests/unit/test_message_wakeup_dispatch.py:177-199` (monkeypatch the trigger function
and assert the call): submit an activity, assert the new `triggers` function is awaited once with
the room id, **after** the route's commit. Fails today because nothing on the submit path calls
it.

**8.2 The negative assertion, which is half the point of Q-1.** Assert that
`evaluate_message_wakeups` is **not** called and that no `wakeup_agent` job is enqueued for the
submission. Without this, a later well-meaning change to "make TA react to submissions" would
pass silently and reintroduce the 28-turn storm.

**8.3 Trigger-level unit test.** In the idiom of
`backend/tests/unit/test_agent_trigger_wiring.py:175-191` (which pins `evaluate_presence_change`
forwarding to `on_users_present`): assert the new function forwards every bound agent id,
including observers, and returns without enqueuing anything.

**8.4 Behavioural assertion at the wake-up layer.** Assert that after the new call, a subsequent
`evaluate_silence_trigger` within `t_minutes` does **not** fire - the direct expression of the
reproduction in §4. `backend/tests/unit/test_wakeup_service.py` owns this area.

**8.5 Must stay green unmodified.** The eight `SubmissionService.submit` tests
(`test_activities_services.py:668-950`), which stub `ss.ConversationFacade` - the fix must not
touch the service, so if any of them needs changing, Q-3's layering decision has been violated.
Also `backend/tests/wiring/test_wiring.py:294-305` (a user send still reaches
`on_message_created` and fires `every_n_messages`).

## 9. Risks and Rollback

- **Over-suppression.** After the fix, a class that only submits and never chats gets no agent
  turn at all until a genuine lull. That is the deliberate consequence of Q-1 and is the
  conservative direction: the platform is never noisier than today. FU-2 records the capability
  that would change it.
- **The docs promise more than the code will deliver.** `:160` currently says TA "responds to
  every message"; §7.4 narrows it. An educator who read the old sentence may expect reactive
  feedback on submissions. Stated in the docs rather than discovered in a classroom.
- **Redis write volume.** Each submission now writes one key per bound agent. Bounded by
  submissions times bound agents, both small, with a 7-day TTL (`wakeup_state.py:22`). The same
  write already happens on every presence change.
- **Best-effort by design.** If the new call fails, the submission still succeeds and the clock
  is simply not re-armed - i.e. today's behaviour. That is the correct failure mode and matches
  the surrounding dispatcher.
- **Rollback**: `git revert`. No migration, no API contract, no stored data.

## 10. Acceptance Criteria

- [x] AC-1: The test from §8.1 fails before the fix and passes after: submitting an activity
  re-arms the silence clock for every bound agent in the room, including observers.
- [x] AC-2: A submission does **not** call `evaluate_message_wakeups`, does not increment any
  agent's message count, and enqueues no `wakeup_agent` job (§8.2).
- [x] AC-3: A submission does not reset `autostop` (Q-2).
- [x] AC-4: With SA at `t_minutes: 3`, a stream of submissions arriving inside three minutes of
  each other does not fire the silence trigger; a genuine three-minute gap with neither a
  submission nor a chat message does fire it exactly once (§8.4).
- [x] AC-5: The new call happens **after** the route's commit and cannot fail the submission -
  a raising trigger leaves the submission committed and returns 2xx.
- [x] AC-6: `SubmissionService` is unchanged; `contexts/activities` gains no import of
  `contexts.orchestration`, verified by the existing AST tripwires plus a manual check of
  `contexts/activities/interfaces/facade.py:5-6`'s claim.
- [x] AC-7: `docs/examples/creative-thinking-course.md:160` and `:268-271` state the exclusion,
  and `wakeup_service.py:76-77`'s docstring carries the carve-out.
- [x] AC-8: The eight `SubmissionService.submit` tests and `test_wiring.py:294-305` pass
  unmodified.
- [ ] AC-9: Gates green: `ruff check . && ruff format --check .`, `mypy .`, `pytest -q`;
  `wiring` tier on CI.

## 11. SRS Delta

**Amend [R15.02]** to state what re-arms the silence clock, since this fix establishes that a
service-authored activity echo does so while not counting as a message for [R15.01]. Apply
verbatim on approval:

> - **[R15.02]** The silence trigger fires for an agent when no room activity has been observed
>   for its configured T minutes. Room activity means a user or agent message, a participant
>   joining, or an activity submission. An activity submission re-arms the clock but is not
>   counted by `every_n_messages` ([R15.01]) and does not reset the autostop cap ([R15.03]):
>   it is evidence that the room is working, not a turn the room is waiting on.

[R15.01] and [R15.03] are unchanged; the amended [R15.02] states their boundary explicitly so
the asymmetry is documented rather than implied. [R28.04] and [R30.17] are cited as context
(mention handling and the activity rendering path) and need no change.

## 12. Deviation Log

- **D-1** — **The route reaches the trigger through `ConversationFacade`, not by importing
  `contexts.conversation.application.triggers`. Q-3's justification was factually wrong.**
  Q-3 states "the route already imports `contexts.conversation.interfaces` and
  `.interfaces.access` (`activities.py:40-46`), exactly as `messages.py:33-37` does". Those are
  two different things and only the first half is true of `activities.py`. Checked at build
  time: `activities.py` imports **only** from `contexts.conversation.interfaces` (`:40`, `:41`),
  so it is currently clean under `backend/CLAUDE.md`'s route rule
  ("`app/api/v1/` → calls `contexts/*/interfaces/facade.py`", stated as a hard rule);
  `messages.py:23-33` is the one importing `contexts.conversation.application.*`, and it is
  pre-existing non-compliance. Following Q-3 literally would have **introduced** a fresh
  `app/api/v1/ → contexts/*/application/` violation into a compliant file.

  So `ConversationFacade` gains `note_room_activity`, which delegates to the new trigger with an
  in-method import — the cycle-breaking idiom the facade already uses at `:63`. The call site,
  the behaviour, the post-commit ordering and the "silence clock only" semantics are all exactly
  as approved; only the import path changed, and it changed to comply rather than to redesign.
- **D-2** — **`evaluate_presence_change` and `evaluate_room_activity` share a `_re_arm_silence`
  helper** rather than the latter duplicating the former's four lines. Not specified either way;
  recorded because it means a future change to how the clock is touched lands in one place.
- **D-3** — **The re-arm is ordered after the realtime emit, not before it.** §7.2 did not
  specify a position within the fan-out. Placed last-but-one deliberately: the emit is what the
  participant's client is waiting on, and the re-arm costs a DB read they should not queue
  behind. Caught by the self-audit gate.
- **D-4** — **§8.4's behavioural test became a pair.** The spec asked for "a re-armed clock does
  not fire within `t_minutes`". Asserting only that is weak — it would also pass if the trigger
  were suppressed outright — so a converse test asserting a genuine lull past the window *does*
  still fire was added beside it.

## 13. Follow-ups

- **FU-1**: Whether a submission should reset `autostop` (Q-2) was decided conservatively
  without evidence. A classroom dry-run would show whether SA's two-round cap is exhausted too
  early in a long activity. Revisit with data rather than by argument.
- **FU-2**: **TA reacting to a submission is a real capability, not a defect.** The digest
  reaches agents only on their next chat-triggered or silence-triggered turn
  (`activity_context_provider.py:31`, `:38-55`). A dedicated `activity_submitted` wake-up
  trigger - opt-in per agent, with its own throttle - would let a teacher agent give feedback as
  work arrives without making every agent answer every submission. That is a feature dossier.
- **FU-3**: **The submissions route has no test coverage at all.** A grep for
  `_dispatch_submission`, `submit_activity` or `activity-submissions` across `backend/tests`
  returns nothing, which is why a missing side effect in the dispatcher went unnoticed. This
  dossier adds tests for the wake-up arm only; the WS emit, the validation enqueue and the
  workflow-signal enqueue remain untested.
- **FU-4**: `sender_is_user` is a caller-supplied boolean at all three existing call sites and
  is never derived from the message row (`observations.py:209` passes `True` for a
  `SenderType.SYSTEM` row). That is defensible - it encodes intent rather than provenance - but
  it means the wake-up semantics of a message are decided by whoever writes the dispatch call,
  with nothing to check them against. Worth a named enum or a documented rule.
