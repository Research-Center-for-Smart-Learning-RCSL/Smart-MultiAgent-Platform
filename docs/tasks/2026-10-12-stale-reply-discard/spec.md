---
type: bugfix
status: draft
created: 2026-10-12
requirements: [R13.27, R28.12, R28.13]
depends_on: []
---

# Discard a reply that a queued follow-up turn supersedes

## 1. Summary

When several people post in quick succession, an agent's turn starts on the first message, the
rest arrive while it generates, and those triggers coalesce into one follow-up turn. The first
turn still posts its reply, which was written from a snapshot that no longer reflects the room,
and the follow-up turn then posts a second reply. In a classroom test this read as the teacher
agent answering one student after the discussion had moved on, followed immediately by another
message. This task makes a turn discard its reply, before persisting it, when a follow-up turn
for the same agent and room is already queued and the reply carries no side effects that would
be orphaned by discarding it.

Born from `docs/audits/2026-10-12-crat-ta-multi-speaker-facilitation/findings.md`, F-1.

## 2. Observed vs Expected

- **Observed**
  - History is loaded once before streaming (`backend/contexts/agents/application/runtime/turn_engine.py:2835`);
    no history is re-read after that.
  - A trigger that fails to take the per-(agent, room) lock parks a mark
    `turn:queued:{agent}:{room}` (`turn_engine.py:537-542,563-567,598-637`), and the lock
    holder's `finally` drains it into exactly one follow-up `wakeup_agent` job
    (`turn_engine.py:677-704,1253-1256`).
  - Between the end of streaming and `MessageService.send_agent` (`turn_engine.py:3290-3297`)
    nothing checks whether such a mark exists. The stale reply is persisted, broadcast, and
    dispatched to other agents' wake-ups (`turn_engine.py:3322-3347`).
- **Expected** — no SRS requirement covers this; the expected behavior was agreed with the user
  (Q-1 to Q-4): a turn whose reply is already superseded by a queued follow-up does not post it,
  provided discarding loses nothing but text. The follow-up turn, which loads the fuller
  history, replies instead.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Fix shape: discard when a follow-up is queued, add a debounce window, or both? | Discard only. | User's choice. Smallest change that removes the stale reply; a debounce window would delay every agent and needs an SRS change. Cost: one extra provider call per superseded turn. |
| Q-2 | Apply to observer turns too? | Yes. | User's choice. Observer turns share the lock and coalescing ([R28.12]); a superseded observation followed by a fresh one gives the creator two near-duplicate notes. |
| Q-3 | Discard a reply whose turn already ran a state-changing tool? | No: post it as today. | User's choice. Activity start/end, canvas writes, `update_wakeup` and code execution are committed by the turn's own transaction (`turn_engine.py:3107-3111`) regardless of the reply; discarding the text would leave a visible state change with no explanation, and code-exec artifacts are persisted against the reply's `message_id` (`turn_engine.py:3310-3311`), so discarding would lose them. |
| Q-4 | Can a busy room starve an agent by superseding every turn? | Cap at one consecutive discard per (agent, room). A turn that follows a discarded turn always posts. | Without a cap, a room whose messages keep arriving faster than one generation never hears from the agent. |
| Q-5 | Should this depend on `2026-07-19-large-artifacts-silently-dropped` (in progress, edits `turn_engine.py`)? | No. | User's choice. That dossier edits artifact persistence (`turn_engine.py:1124-1171`); this one inserts a check between `:3207` and `:3212`. Whoever builds second rebases. |
| Q-6 | Where does the check sit? | After the empty-reply guard (`turn_engine.py:3140-3207`) and before the observer branch (`:3223`), so it covers both roles. | Q-2. |

## 4. Reproduction

Preconditions: a room with one agent bound, `every_n_messages` enabled with `n = 1`, and a
provider slow enough that a turn takes several seconds.

1. User A posts a message. The agent's turn starts.
2. Within the generation window, users B, C and D each post a message. Each `wakeup_agent` job
   fails to take the lock and parks a mark.
3. Observe: the agent posts a reply addressed only to A's message, then the drained follow-up
   turn posts a second reply.

Deterministic unit reproduction: drive `_run_locked` with the shared engine fakes
(`backend/tests/unit/turn_engine_fakes.py`) and a fake Redis holding a parked mark
(`backend/tests/unit/test_turn_lock_and_coalescing.py:28-81`); assert `send_agent` is called.

## 5. Root Cause Analysis

1. `run_turn` serializes turns per (agent, room) and coalesces late triggers into one parked
   mark (`turn_engine.py:1203,1240`).
2. `_run_locked` snapshots history before streaming (`turn_engine.py:2835`).
3. **Root cause:** `_run_locked` treats the end of streaming as the point of no return and
   persists unconditionally (`turn_engine.py:3290`), although the parked mark is positive
   evidence, readable at that moment, that a turn with a fuller snapshot is about to run.
4. The two replies are each broadcast (`turn_engine.py:3322`) and each dispatches other agents'
   wake-ups (`turn_engine.py:3347`), so the stale reply also advances peers' counters.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every room turn: `every_n_messages`, `silence_minutes`, `mention`,
  `release` and coalesced follow-ups all reach `run_turn` through `wakeup_agent`
  (`backend/app/workers/tasks/orchestration.py:38,151`). Headless A2A and approver turns use
  `run_input_turn` (`turn_engine.py:1275`), take no lock and park no mark, so they are
  unaffected. No persisted data needs repair.
- **Sibling suspects**
  - Stranded-turn reaper (`backend/app/workers/tasks/turn_reaper.py:43,95`) drains marks after a
    crashed turn: **cleared**, no reply is posted on that path.
  - Retry-acquire path that serves its own mark (`turn_engine.py:1206-1222`): **cleared**, it
    pops the mark before running, so the new check correctly sees no mark for that turn.
  - Mention turns: **confirmed in scope**. A discarded mention reply is answered by the
    follow-up, which sees the mention in its history. The follow-up's trigger name is the first
    parked one (SET NX, `turn_engine.py:563-567`), so a follow-up started by a parked
    `every_n_messages` mark answers a mention under that trigger name; recorded as FU-1.

## 7. Fix Design

### 7.1 Non-destructive mark check

Add `_queued_trigger_state(agent_id, room_id) -> TriggerPopState` next to `_pop_queued_trigger`
in `turn_engine.py`, using `EXISTS` on `_queued_trigger_key` (`turn_engine.py:537-538`). It must
not consume the mark: the `finally` drain (`turn_engine.py:1253-1256`) is what enqueues the
follow-up. Redis errors return `UNKNOWN`, which is treated as "not superseded" (post the reply),
matching the protocol's existing posture of never dropping a reply on an infrastructure fault.

### 7.2 Side-effect record

`ToolLoopOutcome` (`turn_engine.py:851`) gains `dispatched_tools: frozenset[str]`, filled at the
dispatch site (`turn_engine.py:4246`). A turn is discardable only when every dispatched name is
in a new `READ_ONLY_TOOL_NAMES` frozenset declared beside `BUILTIN_TOOL_NAMES`
(`tool_registry.py:119-147`): `read_skill`, `web_search`, `file_search`, `read_drafts`,
`present_observation`. Any other name, including every MCP and local-function tool, counts as
side-effecting. `present_observation` is in the set because its blocks are only recorded by the
observer branch after the check (`turn_engine.py:3236`). A drift test asserts the set is a subset
of `BUILTIN_TOOL_NAMES`, so a renamed tool cannot silently fall into the discardable set. The
artifact sink (`turn_engine.py:2745`) must also be empty.

### 7.3 Consecutive-discard cap

A Redis key `turn:superseded:{agent}:{room}` with the same TTL as the queued mark
(`_QUEUED_TRIGGER_TTL_S`, `turn_engine.py:534`). A discard sets it; any posted reply or committed
skip clears it; a turn that finds it set posts unconditionally.

### 7.4 The discard branch

Inserted after `turn_engine.py:3207`. When the mark is `PARKED`, the turn is discardable (§7.2)
and the cap is not set (§7.3), the turn mirrors the empty-reply branch
(`turn_engine.py:3140-3207`): audit `agent.turn_finished` with reason `superseded`, commit and
set `outcome_committed`, pop the compact flag, emit `agent.finished {reason: "superseded"}` for a
room turn or `observation.skipped {kind: "superseded"}` for an observer turn, drain activation
events, settle approvals, and return `TurnResult(status="skipped", reason="superseded")`. It does
not roll back: usage events and tool audits staged in the session are real and must persist. The
worker already counts no autostop round for `skipped` (`orchestration.py:181-183`).

### 7.5 Client

A room client already clears the streamed draft on an `agent.finished` without `message_id`
(`frontend/src/slices/conversation/__tests__/useChatroomSocket.test.ts:186`); no change. The
observer skip label needs a `superseded` key beside `no_input` in
`frontend/src/slices/conversation/locales/en.json:222` and `zh-TW.json:222`.

## 8. Regression Test Plan

Written first, failing against current code:

- `backend/tests/unit/test_turn_lock_and_coalescing.py`: extend `_FakeRedis` (`:28-81`) with the
  `EXISTS` path; `test_queued_state_does_not_consume_the_mark` asserts the mark survives the
  check and the `finally` drain still enqueues one follow-up.
- New `backend/tests/unit/test_superseded_reply_discard.py` on `turn_engine_fakes.py`:
  - a parked mark and a tool-free turn: no `send_agent`, result `skipped/superseded`, one
    `agent.finished` with reason `superseded`, follow-up enqueued (fails today: `send_agent`
    is called);
  - parked mark plus a dispatched `start_activity`: reply posted;
  - parked mark plus a non-empty artifact sink: reply posted;
  - parked mark, cap key set: reply posted and cap cleared;
  - observer turn with a parked mark and only `present_observation`: no observation recorded,
    `observation.skipped {kind: superseded}`;
  - Redis error on the check: reply posted.
- `backend/tests/unit/test_builtin_tools_wiring.py` (or the module holding the existing
  reserved-name drift test): `READ_ONLY_TOOL_NAMES <= BUILTIN_TOOL_NAMES`.
- Frontend: a `useObservations` case that renders the `superseded` skip label.

## 9. Risks and Rollback

- **Visible flicker**: streamed tokens of the discarded turn were already rendered and are
  cleared by `agent.finished`. Accepted: the follow-up reply replaces them within one
  generation.
- **Cost**: one provider call wasted per discard, bounded by the cap to at most every other
  turn per (agent, room).
- **Mis-classification**: a side-effecting tool cannot be discarded by mistake, since anything
  outside `READ_ONLY_TOOL_NAMES` blocks the discard. The only possible error is a read-only tool
  left out of the set, which keeps today's behavior for that turn.
- **Rollback**: revert; the new Redis key expires on its own TTL.

## 10. Acceptance Criteria

- [ ] AC-1: the §8 regression tests fail before the fix and pass after.
- [ ] AC-2: with a parked mark, a tool-free and artifact-free room turn persists no message,
  returns `skipped/superseded`, emits `agent.finished {reason: superseded}`, and the follow-up
  turn still runs.
- [ ] AC-3: the check never consumes the mark.
- [ ] AC-4: a turn that dispatched any tool outside `READ_ONLY_TOOL_NAMES`, or produced
  artifacts, posts its reply even with a parked mark.
- [ ] AC-5: no two consecutive turns of one (agent, room) are both discarded.
- [ ] AC-6: an observer turn under the same conditions records no observation and emits
  `observation.skipped {kind: superseded}`; the frontend labels it in both locales.
- [ ] AC-7: a Redis failure on the check posts the reply.
- [ ] AC-8: manual check on a running stack: four participants posting within one generation
  produce exactly one agent reply that reflects all four messages.
- [ ] AC-9: backend `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .`; frontend
  `pnpm test`, `pnpm lint`, `pnpm typecheck` pass.

## 11. SRS Delta

Amend [R13.27] (`REQUIREMENTS.md:757`), last sentence:

> The same holds for a committed skip (`empty_reply`, `no_input`, `knowledge_starved`,
> `superseded`), which reports `skipped`.

Add after [R13.27]:

> - **[R13.72]** **Superseded replies.** Before a room or observer turn persists its reply, it
>   checks whether a coalesced follow-up trigger for the same agent and room is already queued.
>   If one is, the turn dispatched no tool outside the read-only set (`read_skill`, `web_search`,
>   `file_search`, `read_drafts`, `present_observation`), produced no artifacts, and the
>   previous turn for that agent and room was not itself discarded, the reply is discarded and
>   the turn commits a `superseded` skip. The follow-up turn replies from the fuller history. A
>   failure to read the queue state posts the reply.

Amend [R28.13] (`REQUIREMENTS.md:2208`): `observation.skipped` carries benign kinds
(`no_input`, `empty_reply`, `superseded`).

`docs/traceability.csv` is regenerated with `python scripts/traceability.py`.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — the follow-up turn keeps the first parked trigger name (SET NX), so a mention that
  arrives after an `every_n_messages` mark is answered under `every_n_messages`; mention-only
  behavior (autostop bypass, error emits) does not apply to it.
- **FU-2** — streaming could be held back for turns that start with a parked mark already
  present, removing the flicker in §9.
