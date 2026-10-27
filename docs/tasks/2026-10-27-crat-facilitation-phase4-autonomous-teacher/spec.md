---
type: feature
status: approved
created: 2026-10-27
requirements: [R13.73, R15.02, R15.03, R15.05, R15.26, R30.21, R30.22, R30.37, R30.44, R30.45]
depends_on: [2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence]
---

# Classroom facilitation phase 4: the Teacher Agent runs the class

## 1. Summary

The three example classroom courses (CRAT word puzzle, character building, object
reinterpretation) are taught by a teacher agent, TA, which in this dossier always means the
Teacher Agent: the agent is the class's teacher, not an assistant to a human teacher. Today
the room creator still has to speak or act at every stage change: open the class, start an
activity when TA holds no grant, say that individual work is over, confirm the end of a
round, confirm the next round, and close the class. Phase 2 made that dependence deliberate
(its Q-4 and AC-15) because the platform gave TA no clock it could be woken by and no
authoritative completion fact. This dossier adds those platform facts and rewrites the three
teacher prompts so that a class runs from the first student's arrival to the closing summary
with no message or panel action from the room creator. The room creator configures the room
once before class and may still overrule TA at any time by speaking or by ending an activity
from the panel.

## 2. Goals and Non-goals

**Goals**

- A class opens without a creator message: once enough students are connected, or the first
  student has waited long enough, the platform wakes TA to give its opening (Q-1).
- Individual work ends without a creator message: the platform wakes TA when the activity's
  time limit is reached or when every connected student has a valid submission, whichever
  comes first, and TA ends the activity itself (Q-2, Q-5).
- An activity type carries a default time limit, and a room may override it per type or set
  no limit (Q-9).
- TA sees, on every turn, how long the current activity has run, its time limit, and for an
  individual activity how many of the currently connected students have submitted.
- The three teacher prompts move between stages, rounds and the close by rules instead of by
  the creator's confirmation. A class ends when the course's bank is used up (Q-6). The CRAT
  teacher judges an answer that differs from its bank answer itself and explains why (Q-7).
- The room creator may be absent. When present, a creator message or panel action overrides
  TA, along the paths phase 2 and phase 3 already verified (Q-3).
- The adoption guide and the simulator scenarios describe and exercise the autonomous class.

**Non-goals**

- One-click course rooms, automatic grants, or any change to the rule that only the room
  creator grants activity control ([R30.37]). Pre-class setup by the creator stays manual
  (Q-8, FU-1).
- A per-room "autonomous mode" switch. The rewritten prompts are the only behavior; there is
  no second, creator-confirmed variant to maintain (Q-3).
- The platform ending an activity on its own at the deadline. It only wakes TA (Q-5).
- A class duration limit or a fixed round count. The bank decides the length (Q-6).
- Per-round time limits inside one activation. CRAT keeps one activation across its rounds
  (phase 3 D-6) and ships with no time limit (FU-2).
- A server-side lesson plan, round counter or bank progress. Round tracking stays in the
  prompt and transcript (FU-3).
- Scheduling a class for a clock time. The opening is arrival-based only (Q-1).
- Any change to the peer (SA) and analyst (AA) prompts beyond removing references to the
  creator's confirmation.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | With no creator opening, when does TA start the class? | When a configured minimum of students is connected, or a configured wait has passed since the first student arrived. | Fits a scheduled lesson and self-study alike. Starting on the first arrival alone would make late students miss the rules; a clock-time schedule needs a scheduler this dossier does not otherwise need. |
| Q-2 | How does individual work end? | At the activity's time limit, or earlier when every connected student has a valid submission, whichever comes first. | A time limit alone makes a finished class wait; completion alone stalls on one absent or disconnected student. |
| Q-3 | What is the room creator's role? | The creator may be absent. When present, a creator message or panel action overrides TA. No per-room switch. | Keeps the override paths already verified live (phase 2 D-18, phase 3) without maintaining two behaviors. |
| Q-4 | Which courses are in scope? | All three room teachers at once. | They share the platform mechanism and most prompt blocks; changing one would let the three diverge again. |
| Q-5 | At the time limit, who closes the form? | The platform only wakes TA; TA announces and calls `end_activity`. | The requester chose it over a platform fallback and over a platform end. The cost is recorded in §10: a TA that misses the wake leaves the form open until a later wake or the creator. |
| Q-6 | How does a class end? | When the course bank is used up. | The requester chose the bank over a class duration and over a fixed round count. CRAT's eight rounds may run longer than one period; the creator can end early. |
| Q-7 | How does CRAT judge a group's answer that differs from the bank answer? | TA checks the answer against each of the three words itself, accepts it when all three form words, otherwise names the word that fails, then gives the bank answer. A present creator may overrule. | Keeps reasonable creative answers from being marked wrong with no human in the room. |
| Q-8 | Is pre-class setup an intervention? | No. The creator sets up the room once (bind agents, grant TA, set opening and time limits) before class. | Smallest scope; keeps [R30.37]'s grant rule. A one-click course room is FU-1. |
| Q-9 | Where is the time limit configured? | A default on the activity type (the examples ship 5 minutes for character building and object reinterpretation), overridable per room and type, including "no limit". | A default means a room that forgets the setting still has a limit; the room override covers different classes. |
| Q-10 | Should this depend on phase 2? | Yes, on `2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence`. | Overlap and logical prerequisite: both edit the same three teacher prompts, and this dossier replaces phase 2's creator-confirmation contract, so phase 2's live evidence must be gathered on its own prompts first. |

## 4. Current State

**Wake triggers.** An autonomous agent turn is always a `wakeup_agent` Arq job
(`backend/app/workers/tasks/orchestration.py:38-224`). The configurable kinds are
`every_n_messages`, `silence_minutes`, `call_only` and `on_join`
(`backend/contexts/orchestration/domain/models.py:207-241`); a turn can additionally be woken
by `mention` and `release` (`backend/contexts/agents/application/runtime/turn_engine.py:583-589`).
Nothing wakes an agent at a time or after a duration. Silence is a sweep: the cron
`evaluate_silence` runs every 30 seconds (`backend/app/workers/main.py:388`,
`backend/app/workers/tasks/orchestration.py:273-340`). The closest precedent for a delayed
wake is `on_join`, enqueued with `_defer_by` 60 seconds (`backend/app/api/ws/chatroom.py:67-77`).

**Opening.** `on_join` never fires for the room creator's arrival
(`backend/contexts/orchestration/application/wakeup_service.py:330-332`) and, unless
`allow_self_open` is true, never in a room with no messages (`:343-348`; [R15.05], [R15.26]).
No pack sets `allow_self_open`, which defaults to false (`models.py:268`). A turn whose request
has no messages returns `skipped/no_input` (`turn_engine.py:3539-3560`), and `wakeup_agent`
passes no input text (`orchestration.py:173-180`), so a wake in an empty room produces no
reply. Room creation emits only an audit row (`backend/contexts/conversation/application/chatroom_service.py:169`).
In every live run so far the creator's first message opened the class
(`docs/tasks/2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence/ac20-live-run-protocol.md:27`).

**Autostop.** Each completed autonomous turn increments the autostop count and only a user
message resets it (`wakeup_service.py:126-127, 368-386`; [R15.03]). The teacher packs set
`autostop_rounds` to 2 (`backend/contexts/agents/infrastructure/examples/packs/character-building-room.json:15-26`).
Explicit triggers bypass the worker's gates (`orchestration.py:115-119`).

**Activations.** `activity_activations` has no duration, deadline or round column
(`backend/contexts/activities/infrastructure/tables.py:107-143`), and `activity_types` has no
time field (`:21-56`). Start and end go through `ActivationService.start` and `.end`
(`backend/contexts/activities/application/activation_service.py:91-169, 171-232`). Start and
end write a system notice (`activation_notice.py:35-40`) that re-arms the silence clock and
wakes nobody (`backend/contexts/activities/interfaces/broadcast.py:88-111`); a submission
likewise wakes nobody (`backend/app/api/v1/activities.py:1397-1403`).

**Agent activity control.** `start_activity` and `end_activity` require a live grant on the
binding (`backend/contexts/agents/application/runtime/activity_tools.py:69-110`). The only
write path for the grant is the room creator's
`PATCH /chatrooms/{id}/agents/{agent_id}/activity-control`
(`backend/app/api/v1/chatrooms.py:970-1036`); the pack field `may_control_activities` is
advisory only (`backend/contexts/agents/infrastructure/examples/catalogue.py:99-106`).

**What TA sees.** The time line gives the current time, the age of the last message and the
wake reason (`turn_engine.py:713-725`). `[Activity state]` gives the activity name, mode and
start timestamp, or the last activity's end
(`backend/contexts/activities/application/activity_context_provider.py:437-455`), and states
that it makes no whole-class completion claim (`:414-418`; [R30.44]). It carries no elapsed
time and, for an individual activity, no submission count: `submitted_groups` keeps only group
subjects (`backend/contexts/activities/infrastructure/repositories/submission_repo.py:677`).
`[Participants]` marks each person `connected`, `not connected` or `connection unknown`
(`backend/contexts/agents/application/runtime/participant_facts.py:134-149`) from Redis
presence that can lag about 150 seconds after an unclean disconnect
(`backend/contexts/conversation/infrastructure/presence.py:18-21, 34-47`); a Redis failure is
reported as unavailable, not as an empty room (`backend/contexts/conversation/interfaces/facade.py:588-606`).
A comparison of presence with submissions exists only in the teacher dashboard's watchlist
(`backend/contexts/activities/application/aggregation_service.py:105-167`) and the
creator-only progress read (`backend/app/api/v1/activities.py:1053-1071`).

**Creator touchpoints in the prompts.** All three teacher prompts are line 27 of their pack.
- CRAT (`crat-word-puzzle-room.json`):
  - `<步驟B1>` and `<步驟D1>` ask the creator to start when TA has no tool.
  - `<步驟E1>` hands the creator a target TA cannot read, and an answer that differs from the bank.
  - `<步驟E2>` leaves the next round and the end to the creator.
  - `<活動控制>` 4 ends only after the creator confirms the last round.
- Character building (`character-building-room.json`):
  - `<步驟B2>` asks the creator to start without a tool, and announces 「時間由老師決定」.
  - `<步驟C1>`'s only exit is the creator's 「時間到」.
  - `<步驟D1>`, `<步驟D2>` and `<步驟D3>` follow the creator's next-round decision.
- Object reinterpretation (`object-reinterpretation-room.json`):
  - `<步驟B2>`: same as character building.
  - `<步驟C1>`'s only exit is the creator's 「發想時間到」.
  - `<步驟D3>` waits for the creator with no timeout.
  - `<步驟E2>` follows the creator's next-round decision.
- The pack tests pin this wording:
  - `backend/tests/unit/test_agent_example_packs.py:905-912` bans timed or inferred transitions (phase 2 AC-15).
  - `:914-919` requires waiting for the creator; `:1447-1450` covers the CRAT last-round confirmation.
  - `:1155-1161` and `:1476-1484` pin CRAT's referrals to the creator.
- The adoption guide states that the creator's 「時間到」 ends a work phase and that the platform does not time phases (`docs/examples/chinese-character-courses-adoption.md:137-140`).

## 5. Design

### Options considered

**Timed wake.**
- *Option A: deferred Arq job.* Enqueue `wakeup_agent` with `_defer_by` at activation start, as
  `on_join` does. Cheap, but the job lives only in Redis, and nothing in the codebase can cancel
  a deferred job: an activity ended early would still fire its stale wake.
- *Option B: persisted deadline plus the existing 30-second sweep.* Store `deadline_at` on the
  activation and let `evaluate_silence`'s sweep claim due deadlines. Durable, and early ends
  cancel naturally (the sweep reads only active activations). Fires up to 30 seconds late.

**Completion.**
- *Option A: TA infers it from `[Participants]` and `[Recent room activity]`.* Phase 2 rejected
  this; the window is "not a roster" (`activity_context_provider.py:64-73`).
- *Option B: the platform counts it at each valid submission.* It compares the connected
  students with the students holding a valid submission in this activation, and wakes TA once.

**Opening.**
- *Option A: set `allow_self_open` on the teacher packs and reuse `on_join`.* This fires on
  every arrival, at most once a minute (`wakeup_service.py:349-353`), not once per class, and
  the empty-room wake still has no input.
- *Option B: a dedicated `class_start` wake.* A room setting holds a minimum student count and
  a maximum wait, and the class start is claimed once per room.

**The creator's role.**
- *Option A: a per-room autonomous switch.* Off would restore phase 2's behavior.
- *Option B: one behavior, with the creator's word as an override.*

### Decision

Option B in each case.

**Platform.** Phase 4 adds three explicit wake reasons. All three bypass autostop, none counts
toward `every_n_messages`, and each fires at most once per class or activation:

| Wake reason | Fires when | Claimed on |
|---|---|---|
| `class_start` | Enough students are connected, or the first student has waited long enough. | The room, through `class_started_at`. |
| `activity_deadline` | A persisted deadline is reached. The existing 30-second sweep finds it. | The activation. |
| `all_submitted` | A valid submission leaves no connected student without one. Computed by the platform. | The activation. |

**What TA sees.** `[Activity state]` gains elapsed time, the time limit and remaining time, and,
for an individual activity, the submitted count among connected students. This amends
[R30.44]'s "no whole-class completion claim" into a bounded, stated claim about connected
students only.

**Prompts.** The three teacher prompts replace every creator wait with a rule tied to these
facts, and keep one override sentence per stage.

**What was given up.**
- **Q-5:** with no platform fallback at the deadline, a missed wake leaves the form open until
  the next wake or the creator.
- **Q-6:** CRAT can run longer than one period.
- **Option A of the creator's role:** retiring phase 2's confirmation contract means a
  classroom that wants every transition confirmed must now rely on the creator speaking before
  TA's rule fires. AC-15 of phase 2 describes the behavior this dossier replaces; that dossier
  is not edited, and this dossier's SRS delta carries the change.

## 6. Detailed Changes

### Backend (orchestration context)

**Domain** (`contexts/orchestration/domain/models.py`)
- Add a `class_start` designer trigger (`ClassStartTrigger(enabled)`), parsed tolerantly in `WakeupConfig.from_dict`.
- It is not self-toggleable, and it is off for observer bindings.
- The teacher packs enable it. SA and AA do not.

**Wake reasons**
- Add `class_start`, `activity_deadline` and `all_submitted` to `EXPLICIT_TRIGGERS` (`:254`).
- Add them to `_WAKE_REASONS` (`turn_engine.py:583-589`) with plain wording. Example: "the class starting", "the activity's time limit", "every connected participant having submitted".

**Input for the turn.** `wakeup_agent` gains an optional platform notice: a short `[Room notice]` line built from counts only, never names.
- `class_start`: "The class is starting: N participants are connected."
- `activity_deadline`: "The time limit of "<name>" was reached."
- `all_submitted`: "Every connected participant (N) has a valid submission to "<name>"."

This gives the turn an input, so a wake in an empty room is not skipped as `no_input` (`turn_engine.py:3539-3560`).

**`WakeupService`**
- `on_class_start_check(room)`:
  - Counts connected human principals other than the room creator, from `room_presence_snapshot`. Unavailable presence counts as no signal.
  - Fires when that count reaches `min_participants`, or when `max_wait_minutes` has passed since the first arrival.
  - Claims atomically (`UPDATE chatrooms SET class_started_at = now() WHERE class_started_at IS NULL RETURNING`).
  - Wakes each normal, non-`call_only` binding whose effective config has `class_start` on.
- It is called from `evaluate_presence_change` (`contexts/conversation/application/triggers.py:144-173`) and from the 30-second sweep. The sweep covers the max-wait case.

**The 30-second sweep** (`app/workers/tasks/orchestration.py:273-340`) also:
- Claims due deadlines (`UPDATE activity_activations SET deadline_notified_at = now() WHERE status='active' AND deadline_at <= now() AND deadline_notified_at IS NULL RETURNING`) through the activities facade.
- Wakes the bindings in that room whose live grant allowlists the activation's type.
- Rooms with no such binding wake nobody. The creator ends the activity there.

### Backend (activities context)

**Tables**
- `activity_types`: `default_time_limit_minutes` (nullable int, 1 to 180).
- New `chatroom_activity_time_limits` (`chatroom_id`, `activity_type_id`, `time_limit_minutes` nullable, where null means no limit, unique on the pair). It is cascade-deleted with the room and with the type.
- `activity_activations`: `time_limit_minutes`, `deadline_at`, `deadline_notified_at` and `completion_notified_at`, all nullable.
- Migration 0106 adds all of these and is reversible.

**`ActivationService.start`** (`activation_service.py:91-169`)
- Resolves the limit in this order: room override, then the type default, then none.
- Snapshots it as `time_limit_minutes` and `deadline_at = created_at + limit` on the new row.
- A later change to either setting does not move a running deadline.

**All-submitted check**
- `SubmissionService._record` (`submission_service.py:195-373`) runs it after a valid individual submission commits:
  - The connected set comes from presence, as above.
  - The submitted set is the distinct subjects with a valid submission in this activation.
  - If the connected set is non-empty and is a subset of the submitted set, it claims `completion_notified_at` and wakes the granted bindings.
- The wake is enqueued after commit, in the same place the existing broadcast runs (`app/api/v1/activities.py:1397-1403`).
- A group activity never fires it (FU-2).

**`ActivitiesFacade.room_activity_state`** (`facade.py:517-541`) adds the following to `RoomActivityState`:
- `elapsed_minutes`
- `time_limit_minutes`
- `deadline_at`
- `remaining_minutes`
- For an individual activity, `submitted_connected` and `connected`. Both are absent when presence is unavailable.

**`render_activity_state`** (`activity_context_provider.py:437-455, 491-513`) renders these.
- The preamble wording changes from "makes no claim about whether the whole class has finished" to: the count covers participants connected now, excluding the room creator; it is not attendance; a disconnected student is not in it.

### Backend (conversation context)

- `chatrooms` gains three columns:
  - `class_start_config` (JSONB, holding `{enabled, min_participants 1..100, max_wait_minutes 1..60}`, null meaning off)
  - `class_first_arrival_at`
  - `class_started_at`
- The first arrival time is recorded in `evaluate_presence_change` once per class.
- A room-creator action resets `class_started_at` and `class_first_arrival_at` so the room can run another class.

### API contract

- `GET` and `PATCH /api/chatrooms/{id}/class-start`. Both are room-creator gated (`ensure_room_creator`, as at `chatrooms.py:998`).
  - The PATCH body is Pydantic-validated (bounded integers).
  - The PATCH accepts `reset: true`.
  - The response includes `class_started_at`.
- `GET` and `PUT /api/chatrooms/{id}/activity-time-limits`, also room-creator gated.
  - The body is a list of `{activity_type_id, time_limit_minutes | null}`.
  - Every type id is validated against the room's project, as the grant route does (`chatrooms.py:1007`).
- Activity type create, update and read gain `default_time_limit_minutes`. Editing it does not bump `version`, because it is not a behavioral definition ([R30.23]).
- Active activation reads gain `time_limit_minutes` and `deadline_at`.
- `gen:api` must be rerun.

### Frontend

**`slices/conversation`, in room settings**
- A "class start" card with enable, minimum students, maximum wait, the start state and a reset button.
- A per-activity-type time-limit list, with "use default" and "no limit".

**`slices/activities`**
- The type editor shows the default limit.
- The participant's activity panel shows the remaining time when a deadline exists. It reuses the existing active-activation read.
- The facilitator panel shows the same.

**i18n.** New keys in every locale file of both slices.

### Packs and documents

**Activity types**
- The character-building and object-reinterpretation example types ship `default_time_limit_minutes: 5`.
- The CRAT type ships null.
- The pack loader passes the field through. An existing install is not changed ([R30.23], phase 2 AC-19 behavior).

**Teacher prompts.** All three are rewritten stage by stage (one subsection per pack in the implementation notes).
- **Opening.** `<步驟A1>` runs on the `class_start` wake, or on the first student message if the class was opened another way.
- **No grant.** "Ask the creator to start" is kept only as the fallback when TA has no grant. The adoption guide makes the grant part of setup.
- **End of work.** `<步驟B2>`'s 「時間由老師決定」 becomes the time limit read from `[Activity state]`. C1's exit becomes the `activity_deadline` or `all_submitted` wake: TA announces, calls `end_activity`, applies wake-up change b), and continues to sharing in the same turn.
- **Rounds.**
  - The sharing and next-round steps (character building D3, object reinterpretation D3 and E2, CRAT E2) move on by rule after the sharing and summary are done, until the bank is exhausted.
  - At exhaustion TA summarizes, ends any running activity and closes the class.
- **CRAT judging.** CRAT E1 judges a differing answer per Q-7. A target TA cannot read is handled by asking the group to say which character they submitted.
- **Override.** Every stage keeps one sentence: if the room creator speaks, follows the creator; if the activity is found ended, treats it as ended by the teacher (existing rule 5).

**Pack tests.**
- Replace the creator-wait pins (`test_agent_example_packs.py:905-919, 1133-1161, 1387-1392, 1447-1450, 1476-1484`) with pins on the new rules, the override sentence and the wake reasons.
- Keep the ban on promises the platform does not back: no "全班", no "大多數人完成", and no fixed-minute phrasing unless it is read from `[Activity state]`.

**Documents.**
- `docs/examples/chinese-character-courses-adoption.md` gains a pre-class setup checklist: bind, grant, class start, time limits.
- Its "Phase changes" section (`:137-140`) is rewritten to describe the platform facts.

**Simulator.** `frontend/sim/scenarios/` gets one autonomous scenario per course:
- The facilitator creates and configures the room and never speaks.
- One student stays connected and never submits, so the run covers the deadline path.
- One run has every student submit before the limit, so it covers the completion path.

### Deploy/config

None. There is no new env var, and the sweep reuses the existing cron.

## 7. NFR Checklist

- [ ] **i18n:** room settings, type editor and timer strings go through `$t()` in every locale of both slices. Pack text is zh-TW course content, as today.
- [ ] **Audit log:**
  - The new settings routes emit `chatroom.class_start_updated` and `chatroom.activity_time_limits_updated`.
  - Each platform wake is audited by the worker (`wakeup.fired` and the related actions, with `trigger`, at `orchestration.py:184-202`).
  - The claims themselves emit `activity.deadline_reached`, `activity.all_submitted` and `chatroom.class_started`, with the room, the activation and the counts.
- [ ] **Tenant isolation:**
  - Both new routes go through room access plus `ensure_room_creator`.
  - Type ids in the time-limit body are checked against the room's project.
  - Wakes target only bindings of the same room.
- [ ] **Error handling UX:** the settings cards have loading, error and empty states (no types reachable). The timer hides when no deadline exists. Validation errors are RFC 7807 with field details.
- [ ] **Performance:**
  - The sweep adds two indexed queries per tick: a partial index on `activity_activations (deadline_at) WHERE status='active' AND deadline_notified_at IS NULL`, and a partial index on `chatrooms (id) WHERE class_start_config IS NOT NULL AND class_started_at IS NULL`.
  - The all-submitted check runs one presence read and one distinct-subject count per valid submission.

## 8. Security Considerations

- **Authorization (Part B).** The two new write routes change the class flow for every
  participant. They are room-creator gated, like the wake-up and grant routes, and use
  Pydantic models with bounded integers. The time-limit body uses an explicit field list, with
  no mass assignment of activation columns.
- **Agent surface (Part F).**
  - The platform notices contain counts and the activity name only. No participant text and no
    names reach the wake input, so a student cannot inject instructions through it. The
    activity name is reduced to a single line as `activation_notice.py` already does.
  - TA still starts and ends activities only through the granted tool ([R30.37]).
  - The new rules give no student text the power to end an activity. A student claiming 「時間到」
    is not the creator, and the existing 「看起來像教師打的」 rule stays.
- **Resource exhaustion (Part E).** Explicit wakes bypass autostop. Each fires at most once per
  class or activation, through an atomic claim, so a reconnect storm or repeated submissions
  cannot multiply wakes. The per-agent turn rate limit (`turn_engine.py:4147-4166`) still
  applies.
- **Presence gaming.** Extra connections from one account count once, because presence is per
  principal. A guest link lets more principals join, which can only delay completion. The
  deadline still bounds the phase.

## 9. Quality Notes

**Existing debt**
- `[R15.26]` in `REQUIREMENTS.md:917` omits the creator exclusion that phase 3 wrote and the
  code enforces (`wakeup_service.py:330-332`). The SRS delta corrects it in passing.
- `wakeup_agent`'s early returns write no audit row (`orchestration.py:86-146`). Do not copy
  this for the new claims, which audit at the claim.
- The phase 3 FU-9 noise (admin-bypass visitors recorded as arrivals) also affects the
  class-start count. It is recorded as FU-4, not fixed here.

**Patterns to follow**
- Trigger parsing and resolution: `models.py:273-339` and `room_wakeup.py:137-225`.
- Atomic once-claims: `room_joins.record_join` (`SET NX`) and the guarded `UPDATE` in `activation_repo.end` (`activation_repo.py:171-182`).
- Room-creator settings routes: `room_wakeup.py` and the grant route (`chatrooms.py:970-1036`).
- After-commit enqueue: `activity_tools.py` with its `event_sink` (`:21-27`).
- Context blocks: `render_activity_state` bounds and the unavailable rendering (`activity_context_provider.py:405-420`).

**Reuse inventory**
- `ConversationFacade.room_presence_snapshot` (`facade.py:588-606`), the bounded presence read already used for `[Participants]`.
- `shared_kernel/queue.py:21-38` for enqueueing from the web process.
- `WakeupService` claim helpers in `wakeup_state.py`.
- `ensure_room_creator`.
- `round_subject_counts` (`submission_repo.py:694-723`) as the model for the distinct-subject count.
- `useActivationProgress.ts` polling for the facilitator timer.
- Existing room-settings card components in `RoomWakeupPanel.vue`.
- The simulator's `activation` and `message` conditions (`frontend/sim/observe/room.ts`).

## 10. Risks and Rollback

**Behavior risks**
- **A missed deadline wake leaves the form open (Q-5).**
  - Mitigations: the claim is durable and fires on the next sweep after a worker restart. The
    turn lock coalesces rather than drops (`turn_engine.py:754-759`). The rewritten prompt also
    tells TA to end an activity whose remaining time in `[Activity state]` is zero on any wake.
  - Residual risk: a TA that ignores both leaves the form open until the creator ends it.
- **Presence lag** (about 150 seconds) can count a student who just left, delaying completion
  until the deadline. It can also miss a student whose socket dropped, ending the phase while
  they are still writing. The prompt does not claim "everyone"; it says those connected have
  submitted.
- **A class that runs longer than the period.** This applies to CRAT's eight rounds (Q-6). The
  creator can end early. The adoption guide states the expected length.
- **Phase 2 regression.** Rooms on the rewritten pack lose creator-confirmed transitions. The
  pack change reaches existing installs only by manual reinstall, as today (phase 2 AC-19), so
  a teacher who keeps the old prompt keeps the old behavior.

**Rollback.** Migration 0106 is additive and reversible: the downgrade drops the new columns,
the table and the indexes. Rolling back the code with the columns in place is safe, because
nothing else reads them. Restoring the previous pack text restores phase 2 behavior.

## 11. Acceptance Criteria

- [ ] AC-1: With class start enabled (minimum 3, maximum wait 2 minutes), the third connected student other than the creator triggers exactly one `class_start` wake for each teacher binding with the trigger on; a fourth arrival, a reconnect, or the creator's arrival triggers none. With only one student connected, the wake fires within 2 minutes 30 seconds of that student's arrival. A reset allows one more.
- [ ] AC-2: A `class_start` wake in a room with no messages produces a TA turn (not `skipped/no_input`) whose input carries the count-only notice, and the wake bypasses autostop and is not counted by `every_n_messages`.
- [ ] AC-3: Starting an activity resolves its limit from the room override, then the type default, then none, and stores `time_limit_minutes` and `deadline_at`; changing either setting afterwards does not move the running deadline; editing the type default does not bump the type version.
- [ ] AC-4: An active activation past its deadline yields exactly one `activity_deadline` wake for the bindings whose grant covers its type, within 30 seconds of the deadline, across a worker restart; an activation ended before its deadline yields none; a room with no granted binding yields none.
- [ ] AC-5: In an individual activity, the valid submission that leaves no connected non-creator participant without a valid submission yields exactly one `all_submitted` wake; an invalid submission, a re-submission, unavailable presence, an empty connected set, or a group activity yields none.
- [ ] AC-6: `[Activity state]` for an active activation shows elapsed minutes, the time limit or "no time limit", remaining minutes, and for an individual activity "N of M connected participants (room creator excluded) have a valid submission", or states the count is unavailable; the preamble states the count is not attendance. Bounds and the unavailable rendering are unchanged.
- [ ] AC-7: The class-start and time-limit routes refuse non-creators (403) and non-members (404), validate bounds and project membership of type ids, and emit their audit events; `gen:api` is rerun and the frontend types match.
- [ ] AC-8: Room settings show and edit class start (including reset) and per-type time limits; the type editor shows the default; the participant and facilitator panels show the remaining time when a deadline exists. Strings in every locale; view tests cover each new state.
- [ ] AC-9: The three teacher prompts contain no step that waits for the creator except the no-grant start fallback and the override sentence; each opens on `class_start`, ends work on `activity_deadline` or `all_submitted`, advances rounds by rule until the bank is exhausted, then summarizes, ends any running activity and closes. Pack tests pin these rules and keep the ban on unbacked completion or timing promises.
- [ ] AC-10: The CRAT teacher judges a differing answer per Q-7 and names the failing word when it rejects; pack tests pin the wording; a present creator's ruling overrides it.
- [ ] AC-11: Live runs on staging, one per course, with the creator present but silent after setup: the class opens, every round of the bank runs, the class closes, and no creator message or panel action occurs. The character or object run covers both the deadline path (a connected non-submitter) and the completion path. One run includes a creator override mid-round and TA follows it.
- [ ] AC-12: The adoption guide describes the setup checklist and the platform facts, and no longer says the creator's 「時間到」 is required.

## 12. Test Plan

**Unit tests**
- AC-1, AC-2: `tests/unit/test_class_start_trigger.py` (new) uses fake presence and the claim. Turn-engine tests use the platform notice input, following `test_on_join_trigger.py` and `test_turn_join_notices.py`.
- AC-3, AC-5, AC-6: activities service and provider unit tests.

**Db-tier tests**
- AC-3: the migration, and the snapshot on start.
- AC-4: the sweep claim against a real database, including a second sweep and an activation ended before the deadline.
- AC-5: the distinct-subject count.

**Route tests**
- AC-7: following the grant route tests.

**Frontend**
- AC-8: vitest view tests, then a browser check through the `frontend:verify` skill.

**Packs**
- AC-9, AC-10: `test_agent_example_packs.py`, plus `test_agent_example_service.py` for the type default passthrough.

**Live**
- AC-11: three simulator scenarios against staging. Transcripts and audit counts are recorded in the Deviation Log.

**Documents**
- AC-12: review of the guide.

## 13. SRS Delta

Amend [R15.26] (creator exclusion, matching the code and phase 3):

- **[R15.26]** **`on_join` trigger.** A designer may enable `triggers.on_join` for an agent, and a room creator or permitted editor may override it per normal binding ([R15.25]). It is not self-toggleable and is not available to observer bindings. When an arrival other than the room creator's is recorded ([R13.79]) in a room where at least one message exists or `allow_self_open` is true, each normal binding with `on_join` effective and not `call_only` is woken once, 60 seconds later, for all arrivals in that minute. The wake is non-explicit: autostop applies and it does not reset autostop or count toward `every_n_messages`.

Add:

- **[R15.27]** **Class start.** A room creator may enable class start for a room with a minimum participant count (1 to 100) and a maximum wait (1 to 60 minutes). The class starts once, when the number of connected human participants other than the room creator reaches the minimum or the maximum wait has passed since the first such participant arrived; the start is recorded on the room and the room creator may reset it to run another class. At the start, each normal binding whose effective wake-up configuration enables `triggers.class_start` and is not `call_only` is woken with a platform notice stating only the connected count. The wake is explicit: it bypasses autostop and `allow_self_open`, is not counted by `every_n_messages`, and does not reset autostop. Unavailable presence counts as not started.
- **[R15.28]** **Activity wakes.** When an active activation reaches its deadline ([R30.46]), and when a valid submission to an active individual activation leaves no connected human participant other than the room creator without a valid submission to it, the platform wakes, once per activation and per event, each binding in the room whose activity grant ([R30.37]) allowlists the activation's type, with a platform notice stating only the activity name and counts. Both wakes are explicit: they bypass autostop, are not counted by `every_n_messages`, and do not reset autostop. Neither ends the activation. Unavailable presence produces no completion wake.
- **[R30.46]** **Time limits.** An activity type may declare a default time limit in whole minutes (1 to 180). A room creator may override it per type for a room, including setting no limit. Starting an activation resolves the room override, then the type default, then no limit, and records the limit and the resulting deadline on the activation; later setting changes do not move a running deadline. Editing a type's default time limit is a metadata edit and does not increment its `version`. Active activation reads expose the limit and deadline to participants and the facilitator.

Replace [R30.44]:

- **[R30.44]** **Activity state for agents.** Every normal and observer room turn includes a bounded activity-state block computed from server state: whether an activity is active in the room, its name, individual or group mode, consent fraction, start time, elapsed minutes, time limit or its absence and remaining minutes, or else the most recently ended activity and its end time, or that none has been run; for the current activation the groups that have submitted and the proposal facts permitted by [R30.42]; and for an active individual activation the number of human participants other than the room creator connected now and how many of them hold a valid submission to it, or that this count is unavailable. It states that the count covers connected participants only and is not attendance, is capped at 30 group rows and 1,500 estimated tokens with stated truncation, and reads as unavailable rather than "not started" when the read fails.

## 14. Open Questions

- OQ-1: Whether a canvas-only socket enters the room presence roster (the activity analysis did not verify it). If it does, the connected count includes it; the build verifies before relying on the count.
- OQ-2: Whether guests reach `evaluate_presence_change` through the same principal path as registered students (not verified); class start must count them.
- OQ-3: Default values for class start when enabled from the UI (proposed: minimum 2, maximum wait 3 minutes).

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- FU-1: One-click course rooms that bind the pack's agents, request the grant and apply class start and time limits in one creator action.
- FU-2: Per-round time limits and completion for CRAT's group activity, which spans every round in one activation (phase 3 D-6, FU-6).
- FU-3: A server-side round counter or bank progress, so a reinstalled or restarted TA does not depend on the transcript to know the round.
- FU-4: Exclude admin-bypass visitors from arrivals and the class-start count (phase 3 FU-9).
- FU-5: A platform fallback that ends an activity some minutes after its deadline if TA did not (the option declined at Q-5), should live runs show missed deadline wakes.
