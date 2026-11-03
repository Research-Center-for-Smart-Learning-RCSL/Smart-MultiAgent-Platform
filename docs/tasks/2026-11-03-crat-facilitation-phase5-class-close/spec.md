---
type: feature
status: draft
created: 2026-11-03
requirements: [R15.27, R15.28, R15.29, R15.03, R30.37]
depends_on: []
---

# Classroom facilitation phase 5: a closed class stops waking its agents

## 1. Summary

Phase 4 made the teacher agent (TA) run a class on its own, but the platform has no notion of
the class having ended: the end exists only as TA's closing message. In the CRAT run of phase 4
D-31, while a student stayed connected after the close, silence wakes kept producing turns; TA
repeated its closing message nine minutes later and the peer agent (SA), silent through the
class, posted two unprompted study tips (phase 4 FU-16). This phase adds a room-level closed
state. TA sets it with a new `close_class` tool in the turn that closes the class, the room
creator can set it by hand, and the creator's existing class-start reset clears it. While a room
is closed, every automatic wake of its agents is skipped; an @mention and a creator release still
reach the agent. The same change makes an explicit trigger win when it coalesces with a parked
automatic one, so a closed room cannot swallow an @mention.

## 2. Goals and Non-goals

**Goals**

- A room carries a nullable closed timestamp, set once per class and cleared by the class-start
  reset.
- A granted TA can close the class with a `close_class` tool; the three course packs' teachers
  call it when they close.
- The room creator can close and reopen the class from the room settings, whether or not class
  start is configured.
- While the room is closed, no automatic trigger runs a turn for any binding in it: every-N,
  silence, creator message, on_join, class start, activity deadline, all-submitted, and the
  coalesced follow-ups of those.
- An @mention or a creator release that coalesces with a parked automatic trigger runs as the
  explicit trigger, in a closed room and in an open one.

**Non-goals**

- Closing does not end a running activation. The packs already end the activity with
  `end_activity` before closing; a creator ends a stray one from the facilitator panel.
- No class-state line or block in the agent's context (Q-4). An agent @mentioned after the close
  reads the close from the transcript.
- No change to peer or analyst prompts: the gate, not a prompt rule, keeps them quiet.
- No automatic close (by bank progress, idle time or the last student leaving). Bank progress is
  not known to the platform (phase 4 FU-3).
- No change to headless turns (A2A CALL/INSTRUCT, approver turns), which are not room wakes.
- No push of the updated pack prompts to existing installs; the pack loader copies on install
  only (§10).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Fix FU-16 with a platform state, a prompt rule, or both? | Platform state. | A prompt rule depends on the model obeying it, and phase 4 FU-13 already shows a pacing rule not holding. A gate in the worker holds whatever the model does. |
| Q-2 | Which wakes does a closed room skip? | Every automatic trigger; @mention and release pass. | One check at the worker's autonomous branch covers every automatic path, including the platform wakes and coalesced follow-ups, at no extra query (§4.2). Explicit calls are a human asking for a reply. |
| Q-3 | An explicit trigger that coalesces behind a parked automatic one runs under the automatic trigger's name and would be skipped. Fix it? | Yes: the explicit trigger wins the park. | Otherwise a closed room silently drops an @mention that lands while a turn is running. The same fix closes the existing equivalent gap with autostop and `trigger_off`. |
| Q-4 | Show agents that the class is closed? | No. | The gate is what matters; the transcript already carries TA's close. Keeps the turn context unchanged. |
| Q-5 | Who closes and how does it reopen? | TA's tool and the room creator by hand; the class-start reset and a creator reopen clear it. | A creator running the class without TA also needs to silence the agents once the lesson ends. |

## 4. Current State

### 4.1 Class-start record

Class start lives on `chatrooms` as `class_start_config`, `class_first_arrival_at` and
`class_started_at` (`backend/contexts/conversation/infrastructure/tables.py:66-70`, migration
`backend/alembic/versions/0106_class_start_and_time_limits.py:85-97`). The domain types are
`ClassStartConfig` and `ClassStartState`, whose `armed` is "enabled and not started"
(`backend/contexts/conversation/domain/models.py:320-377`). `ClassStartRepository`
(`backend/contexts/conversation/infrastructure/repositories/class_start_repo.py`) reads `_COLUMNS`
(:15), claims the start with one guarded UPDATE (`claim_start`, :88-96), and `write(reset=True)`
clears both timestamps (:105-107). The conversation facade wraps it (`facade.py:629-674`) and
audits a setting change as `chatroom.class_start_updated`. The creator-only routes are
`GET`/`PATCH /api/chatrooms/{id}/class-start` (`backend/app/api/v1/class_flow.py:104-156`);
`reset` is a flag on the PATCH, and the PATCH returns 422 for a room with no stored setting unless
all three fields are given (:130-140), so a room that never configured class start cannot be
reset today. The settings card is `frontend/src/slices/conversation/components/ClassStartCard.vue`
(state union :89-104, reset :142-157); locale keys are under `conversation.classStart` in
`frontend/src/slices/conversation/locales/en.json:468-491` and `zh-TW.json:468`.

### 4.2 Wake paths

Every automatic room turn runs through the Arq job `wakeup_agent`
(`backend/app/workers/tasks/orchestration.py:38`). It loads the room through
`ChatroomRepository.get` (:87), then explicit triggers (`EXPLICIT_TRIGGERS` = mention, release,
`backend/contexts/orchestration/domain/models.py:261`) take the branch at :116-120 that skips all
gates, and every other trigger takes :121-154: binding incarnation, trigger still on, `call_only`,
and autostop except for `PLATFORM_TRIGGERS` (class_start, activity_deadline, all_submitted,
`models.py:267`). The triggers that reach that branch are every-N (human, agent and
released-observation senders), creator message ([R15.29], `wakeup_service.py:493-524`), silence
(sweep `orchestration.py:285-357`, decided in `wakeup_service.py:260-311`), on_join
(`wakeup_service.py:355-396`), class start (`wakeup_service.py:402-473`), activity deadline and
all-submitted (`backend/app/wiring/platform_wakes.py:56-82`). The silence sweep pages
`ChatroomRepository.live_wakeup_states` (`chatroom_repo.py:1009`), whose
`_live_wakeup_select` already joins `chatrooms` (:978-985).

The `Chatroom` domain model (`backend/contexts/conversation/domain/models.py:90-109`) has no
class-start field; `_row_to_chatroom` (`chatroom_repo.py:53`) maps it.

### 4.3 Coalescing

A trigger that arrives while a turn holds the lock is parked by `_MARK_QUEUED_LUA`
(`backend/contexts/agents/application/runtime/turn_engine.py:854-859`), which writes the trigger
with `SET NX`: the first parked trigger names the follow-up. A parked platform notice then renames
the follow-up again, in `drain_queued_trigger` (:1135-1139) and in the retry path of `run_turn`
(:1715-1717). So an @mention that lands behind a parked silence or every-N trigger runs as that
trigger, and the worker's autonomous gates (autostop, `trigger_off`, and after this phase the
closed check) can drop it.

### 4.4 Activity tools

`start_activity` and `end_activity` live in
`backend/contexts/agents/application/runtime/activity_tools.py`. Holding an
`ActivityControlContext` is the authorization (:52-66); `resolve_activity_control` fails closed
unless the binding holds a creator-granted grant with at least one resolvable type (:69-110).
`build_activity_control_tools` (:178-195) is appended by `build_agent_tools`
(`builtin_tools.py:976-989`) only when that context exists. The tools act as the granting creator
plus the agent (:256-266, :336-342), never commit, and audit through `_audit_activity_tool` as
`mcp.tool_invoked` (:407-443). `BUILTIN_TOOL_NAMES` must reserve every built name
(`tool_registry.py:119-148`, drift test `backend/tests/unit/test_builtin_tools_wiring.py:200-220`);
a side-effecting tool stays out of `READ_ONLY_TOOL_NAMES` (`tool_registry.py:150-157`).

### 4.5 Course packs

The three teachers (`backend/contexts/agents/infrastructure/examples/packs/crat-word-puzzle-room.json`,
`character-building-room.json`, `object-reinterpretation-room.json`) close in the same sentence: on
an exhausted bank they summarise, call `end_activity` if an activity runs, then 「下課：之後只簡短回答提問，
其他時候不說話」 (CRAT `<步驟E2>`, charbuild `<步驟D3>`, objreinterp `<步驟E2>`). Their `<活動控制>` opening
names the two activity tools, and `<通用規則>` item 2 requires a turn that calls one of them to speak.
Pinning tests are `test_every_teacher_closes_when_the_bank_runs_out` and
`test_crat_teacher_closes_only_after_the_last_bank_round` in
`backend/tests/unit/test_agent_example_packs.py`.

## 5. Design

### Options considered

**Option A, prompt rule only.** Tell every teacher and peer to stay silent after the close.
Cheapest, but the D-31 turns were the model choosing to speak, and phase 4 has several cases of a
prompt rule not holding.

**Option B, closed state checked at each trigger's decision point.** Gate `on_message_created`,
`evaluate_silence_trigger`, `on_participant_joined`, `on_class_start_check`, `granted_targets`
and `on_creator_message` separately. Saves enqueues, but is six places to keep in step, and a job
already queued or parked before the close still runs.

**Option C, closed state checked in the worker, plus the sweep filter.** One check in
`wakeup_agent`'s autonomous branch, which sees every automatic turn at the moment it would run,
including jobs queued before the close and coalesced follow-ups. The silence sweep also filters
closed rooms in its query, since it is the one path that would otherwise enqueue a job every
30 seconds per binding for as long as a student stays connected.

### Decision

Option C. The worker check is the only place that holds for jobs enqueued before the close, and
the room row it needs is already loaded. The sweep filter is an efficiency measure, not the guard.
Other decision points stay as they are: their enqueues are bounded by human actions or by
once-per-class claims, and the worker skips them.

Because the closed check sits in the autonomous branch, an explicit trigger must not be renamed
into an automatic one by coalescing (Q-3). The park becomes "explicit beats automatic": an
explicit trigger overwrites a parked automatic one, an automatic trigger never overwrites a parked
explicit one, and a parked notice attaches its text to an explicit follow-up without renaming it.

Closing is a guarded UPDATE that succeeds once per class, like `claim_start`. Reopening clears the
timestamp. A closed room is not armed for class start, so a room closed before its class started
does not start until it is reopened.

## 6. Detailed Changes

- **Backend, conversation context.**
  - Migration `0107_class_closed_at`: nullable `chatrooms.class_closed_at` timestamptz; no index
    (every read is by primary key or rides the sweep's join). Downgrade drops it.
  - `tables.py`: the column. `Chatroom` gains `class_closed_at: datetime | None = None`, mapped in
    `_row_to_chatroom`. `ClassStartState` gains `closed_at: datetime | None = None`; `armed`
    additionally requires `closed_at is None`.
  - `ClassStartRepository`: `_COLUMNS` and `_state` carry the column; `_armed()` adds
    `class_closed_at IS NULL` (still implies the 0106 index predicate); `write(reset=True)` also
    clears it; new `claim_close(chatroom_id) -> bool` (guarded UPDATE where live and not closed,
    returning id) and `reopen(chatroom_id) -> bool` (guarded UPDATE where closed).
  - Facade: `close_class(chatroom_id, *, actor_user_id, actor_agent_id, actor_ip, request_id)` and
    `reopen_class(...)`, each audited (`chatroom.class_closed`, `chatroom.class_reopened`, resource
    `chatroom`, metadata `by` = `agent` or `creator` and the agent id when set). Caller commits.
  - `ChatroomRepository.live_wakeup_states` / `_live_wakeup_select`: add
    `chatrooms.class_closed_at IS NULL` to the sweep's read only (the shared select is also used
    by non-sweep reads; the filter goes on the sweep query, not the shared helper).
- **Backend, orchestration and worker.** `wakeup_agent` (`orchestration.py:121`): at the top of
  the autonomous branch, `if room.class_closed_at is not None: return "skipped:class_closed"`,
  logged like the other skips. The `wakeup.skipped` audit is not written for it, matching the other
  worker-level skips.
- **Backend, agents runtime.**
  - `activity_tools.py`: `close_class`, built alongside the two activity tools from the same
    `ActivityControlContext`, no arguments (shape of `_END_SCHEMA`). It calls
    `ConversationFacade.close_class` acting as the granting creator plus the agent, returns
    "Closed the class; automatic wake-ups in this room are off until the room creator reopens it."
    or, when already closed, "The class was already closed; nothing changed." Audited through
    `_audit_activity_tool`. Reserved in `BUILTIN_TOOL_NAMES`; not read-only.
  - Coalescing (`turn_engine.py`): `_MARK_QUEUED_LUA` takes a flag saying whether the incoming
    trigger is explicit. Explicit: plain `SET` (overwrites). Automatic: `SET NX`, and also a no-op
    when the parked value is explicit (NX already gives that). The notice override at :1135-1139
    and :1715-1717 keeps an explicit parked trigger and its message id and passes the notice text
    as `platform_notice`. `EXPLICIT_TRIGGERS` is already imported by the engine
    (`turn_engine.py:113`), so no new cross-context edge is needed.
- **Backend, packs.** The three teachers: `<活動控制>` names `close_class` beside the two activity
  tools; the closing sentence of each teacher's close step calls `close_class` in the same turn as
  the closing message, after `end_activity`; `<通用規則>` item 2 includes `close_class`. Peers and
  analysts unchanged.
- **API contract.** `ClassStartOut` gains `closed_at`. New creator-only routes in `class_flow.py`:
  `POST /api/chatrooms/{id}/class-close` (idempotent; 200 with `ClassStartOut`) and
  `DELETE /api/chatrooms/{id}/class-close` (reopen; idempotent). Both use `_creator_access`. Both
  work whether or not class start is configured. `gen:api` rerun required: yes.
- **Frontend, conversation slice.** `ClassStartCard.vue`: a `closed{at}` state checked before
  `started`, with text 「已於 {at} 下課。」; a "Close class" button (confirm dialog) when not closed and
  a "Reopen" button when closed, both visible for an unconfigured room too; `canReset` also true
  when `closed_at` is set. API wrappers `closeClass` / `reopenClass` in `api/index.ts`. New keys
  under `conversation.classStart` in en and zh-TW: `close`, `closeTitle`, `closeMessage`,
  `closeDone`, `reopen`, `reopenDone`, `state.closed`; `resetMessage` mentions that a reset also
  reopens a closed class.
- **Frontend, simulator.** The hand-written `ClassStartOut` in `frontend/sim/setup/room.ts:120-125`
  gains `closed_at`; the start check (:159-164) also refuses a closed room.
- **Deploy/config.** None.

## 7. NFR Checklist

- [ ] i18n: the card's new strings in en and zh-TW; tool result text is model-facing, not UI.
- [ ] Audit log: `chatroom.class_closed` and `chatroom.class_reopened`; the tool's
  `mcp.tool_invoked` as for the activity tools.
- [ ] Tenant isolation: the new routes use `_creator_access`; the tool is offered only with a
  creator-granted grant on this room's binding.
- [ ] Error handling UX: the card's buttons show the existing `saveFailed` toast on failure and
  stay enabled for retry; 404 for a missing room as the other class-flow routes.
- [ ] Performance: no new query on the wake path (the worker already loads the room); the sweep
  read gains one predicate on a joined table.

## 8. Security Considerations

The tool is a new agent-callable write. It takes no arguments, acts only on the room of the turn,
and is offered only under the same fail-closed grant as `start_activity`, so a prompt-injected
student message cannot reach it without the creator having granted TA, and can at worst close a
class the creator can reopen in one click. The effect is to reduce what agents do, not to widen
it. The reopen route is creator-only; a TA cannot reopen, so an injected TA cannot undo a creator's
close. The coalescing change gives explicit triggers priority but leaves their existing checks
(bound, role) where they are; it adds no way to wake an agent that could not be woken before.

## 9. Quality Notes

- **Existing debt.** `wakeup_agent` imports `ChatroomRepository` from conversation infrastructure
  directly (`orchestration.py:76`); keep using the row it already loads rather than adding a facade
  call beside it, and do not refactor the import here. `class_flow.py` imports a private helper
  from `chatrooms.py` (phase 4 FU-10); do not imitate. `backend/CLAUDE.md` gives the migration
  range as 0000-0079, which is stale; not fixed here.
- **Patterns to follow.** `claim_start` for the guarded UPDATE; `end_activity` for an argument-free,
  idempotent, non-committing tool and its tests (`backend/tests/unit/test_activity_control_tools.py`);
  `test_migration_0106_class_start_and_time_limits.py` for the migration test; the class-start
  card's reset flow for the confirm-then-mutate pattern.
- **Reuse inventory.** `resolve_activity_control` and `ActivityControlContext`;
  `_audit_activity_tool`, `_marked_unrecorded`, `_reraise_if_infrastructure`; `_creator_access`,
  `_class_start_out`; `ClassStartRepository._live`; `formatDateTime` from
  `@shared/utils/datetime`; the card's existing confirm dialog and toasts; `convKeys.classStart`
  with `setQueryData`.

## 10. Risks and Rollback

The migration adds one nullable column and is reversible; old code ignores it. Rolling back the
code leaves closed rooms behaving as today. The main behavioural risk is the coalescing change: it
alters which trigger names a follow-up, which several tests in
`backend/tests/unit/test_turn_lock_and_coalescing.py` pin; a wrong ordering could double-run a turn
or drop a notice, so the build adds explicit cases for each ordering. A creator who closes by
mistake loses agent wakes until they reopen; the card states the closed state and offers reopen.
Pack prompts reach only new installs; staging's existing course agents need the prompt edited by
hand before the live run (AC-9), as in phase 4.

## 11. Acceptance Criteria

- [ ] AC-1: Migration 0107 adds a nullable `chatrooms.class_closed_at` and its downgrade removes
  it; a db-tier migration test covers both directions.
- [ ] AC-2: `claim_close` returns True once per class and False for a closed or deleted room;
  `reopen` clears it; `write(reset=True)` clears it; a closed room is not armed and is not in
  `awaiting_start`. Covered at the db tier.
- [ ] AC-3: `wakeup_agent` returns `skipped:class_closed` without running a turn for each of
  every_n_messages, silence_minutes, creator_message, on_join, class_start, activity_deadline and
  all_submitted in a closed room, and runs the turn for mention and release.
- [ ] AC-4: The silence sweep's read returns no binding of a closed room.
- [ ] AC-5: With a turn holding the lock, an explicit trigger parked after an automatic one, before
  one, and with a parked platform notice, each produces exactly one follow-up named by the explicit
  trigger with its message id, and the notice text, when present, reaches that follow-up; two
  automatic triggers keep first-wins.
- [ ] AC-6: A granted TA is offered `close_class`; an ungranted binding is not. The tool closes the
  room, reports an already-closed room without change, emits `chatroom.class_closed` with the
  agent's id and `mcp.tool_invoked`, and does not commit. `close_class` is a reserved built-in name
  and not read-only.
- [ ] AC-7: `POST` and `DELETE /api/chatrooms/{id}/class-close` close and reopen for the room
  creator, are idempotent, work for a room with no class-start setting, return 403 for a
  non-creator and 404 for a missing room, and audit; `GET /class-start` returns `closed_at`.
- [ ] AC-8: The class-start card shows the closed state and time, closes after confirmation and
  reopens, in both locales, including for an unconfigured room; component tests cover each state.
- [ ] AC-9: The three teacher prompts call `close_class` in their closing step after `end_activity`
  and name it in `<活動控制>` and `<通用規則>`; pinned by pack tests.
- [ ] AC-10: On staging, the CRAT autonomous scenario with a student kept connected for at least
  15 minutes after TA's close shows TA's `close_class` call, no agent message after the close, and
  an @mention of TA after the close answered.

## 12. Test Plan

AC-1 and AC-2 at the db tier (`backend/tests/integration/test_migration_0107_class_closed_at.py`,
`test_class_flow_db.py`). AC-3 in `backend/tests/unit/test_agent_trigger_wiring.py` beside the
existing worker guards, parametrized over triggers. AC-4 at the db tier against
`live_wakeup_states`. AC-5 in `backend/tests/unit/test_turn_lock_and_coalescing.py`, whose `_FakeRedis`
interprets the known scripts rather than running Lua (:31, :56) and must learn the new branch;
because that fake cannot catch a Lua error, one integration test runs the real
`_MARK_QUEUED_LUA` against Redis for the explicit-over-automatic and automatic-behind-explicit
orderings. AC-6 in
`backend/tests/unit/test_activity_control_tools.py` and `test_builtin_tools_wiring.py`. AC-7 in
`backend/tests/unit/test_class_flow_routes.py`. AC-8 in
`frontend/src/slices/conversation/__tests__/ClassPacingCards.test.ts` plus a browser check of the
card. AC-9 in `backend/tests/unit/test_agent_example_packs.py`. AC-10 as a staging run of
`crat-autonomous` with the scenario's close step extended to hold a student connected and send an
@mention at the end, recorded as a D-n entry.

## 13. SRS Delta

Amend [R15.27], replacing its sentence "the start is recorded on the room and the room creator may
reset it to run another class." with:

> the start is recorded on the room and the room creator may reset it to run another class; a
> reset also reopens a closed class ([R15.30]).

Add after [R15.29]:

> - **[R15.30]** **Class close.** A room records at most one close per class. It is set by a
>   binding holding an activity grant ([R30.37]) through the `close_class` tool, or by the room
>   creator, whether or not class start is enabled; the room creator may reopen it, and a class
>   start reset ([R15.27]) also reopens it. While a room is closed, no automatic trigger runs a
>   turn for any of its bindings, including every-N, silence, on_join, class start, activity and
>   creator message wakes and their coalesced follow-ups; an @mention and a release ([R28.07])
>   still run. A closed room does not start a class. Closing ends no activation. When an explicit
>   trigger and an automatic one coalesce into one follow-up turn, the follow-up runs as the
>   explicit trigger.

## 14. Open Questions

None.

## 15. Deviation Log

## 16. Follow-ups
