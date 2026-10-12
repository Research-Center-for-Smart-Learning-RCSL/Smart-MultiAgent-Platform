---
type: bugfix
status: draft
created: 2026-10-12
requirements: [R13.27, R28.12, R28.13]
depends_on: []
---

# Let an agent re-check its reply against messages that arrived while it was writing

## 1. Summary

When several people post in quick succession, an agent's turn starts on the first message, the
rest arrive while it generates, and those triggers coalesce into one follow-up turn. The first
turn posts its reply anyway, written from a snapshot that no longer reflects the room, and the
follow-up turn then posts a second reply. In a classroom test this read as the teacher agent
answering one student after the discussion had moved on, followed immediately by another
message. This task gives the decision to the agent: before persisting a reply, the platform
detects that new messages arrived, shows them to the agent together with its unposted draft,
and the agent posts the draft, posts a revision, or stays silent. That one re-check absorbs the
queued follow-up, so the room gets one reply that accounts for every message.

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
    dispatched to other agents' wake-ups (`turn_engine.py:3322-3347`), and then the follow-up
    turn replies again.
- **Expected** — no SRS requirement covers this; the expected behavior was agreed with the user
  (Q-1 to Q-7): whether a reply is still worth posting after the room moved on is the agent's
  judgment, and the platform's job is to give it the information to make that judgment, then
  enforce the limits the agent cannot see.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Who decides whether a stale reply is posted: the platform or the agent? | The agent. The platform detects new messages and shows them, together with the unposted draft, in one extra model call; the agent posts the draft, posts a revision, or replies with nothing. | Professor's guidance, adopted by the user. Staleness is a pedagogical judgment (an answer to a direct question can still be right after three more messages); a platform rule that always discards makes that judgment for the agent. Consistent with the existing rule that an empty reply is how an agent chooses silence (`turn_engine.py:3140-3207`). This replaces an earlier draft of this dossier that discarded automatically. |
| Q-2 | What stays with the platform? | Detection, the side-effect boundary (Q-4), and the one-re-check cap (Q-5). | The agent cannot see messages that arrived after its snapshot; it cannot un-commit a tool's side effects; and an uncapped loop spends the project's own key. |
| Q-3 | Apply to observer turns? | Yes. | User's choice. Observer turns share the lock and coalescing ([R28.12]). |
| Q-4 | Re-check a turn that already ran a state-changing tool? | No: post it as today, and let the queued follow-up run. | User's choice. Activity start/end, canvas writes, `update_wakeup` and code execution are committed by the turn's own transaction (`turn_engine.py:3107-3111`) regardless of the reply, and code-exec artifacts are persisted against the reply's `message_id` (`turn_engine.py:3310-3311`). Letting the agent withdraw that reply would leave a visible state change with no explanation. |
| Q-5 | How many re-checks per turn? | One. Messages that arrive during the re-check park a fresh mark and get a normal follow-up turn. | Each turn still ends with the agent's own decision, so no room can starve the agent, and the cost is bounded to one extra call per turn. |
| Q-6 | Does the re-check offer tools? | Room turns: none. Observer turns: `present_observation` only, with the first pass's blocks cleared. | The re-check is a decision about what to say, not a new tool loop; a tool call there would reopen Q-4. An observer delivers its output through `present_observation`, so it needs that one tool to revise. |
| Q-7 | What happens to the queued follow-up? | The re-check consumes it: the mark is popped before the re-check, because the re-check has seen everything that mark stood for. On a re-check failure the popped trigger is parked again and the draft is posted. | Without consuming it the room would still get a second reply, which is the symptom. Re-parking on failure keeps today's guarantee that a parked message is answered. |
| Q-8 | Should this depend on `2026-07-19-large-artifacts-silently-dropped` (in progress, edits `turn_engine.py`)? | No. | User's choice. That dossier edits artifact persistence (`turn_engine.py:1124-1171`); this one inserts a branch between `:3207` and `:3212`. Whoever builds second rebases. |

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
(`backend/tests/unit/test_turn_lock_and_coalescing.py:28-81`); assert `send_agent` is called with
the first-pass text and a follow-up is enqueued.

## 5. Root Cause Analysis

1. `run_turn` serializes turns per (agent, room) and coalesces late triggers into one parked
   mark (`turn_engine.py:1203,1240`).
2. `_run_locked` snapshots history before streaming (`turn_engine.py:2835`).
3. **Root cause:** `_run_locked` treats the end of streaming as the point of no return and
   persists unconditionally (`turn_engine.py:3290`). The parked mark, readable at that moment,
   says the room has moved on, but neither the platform nor the agent acts on it, and the agent
   is never shown what changed.
4. The stale reply is broadcast (`turn_engine.py:3322`) and dispatches other agents' wake-ups
   (`turn_engine.py:3347`), then the follow-up turn posts again.

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
    pops its mark before running, so the new check sees only marks parked during this turn.
  - Mention turns: **confirmed in scope**. A mention that arrives during generation is shown in
    the re-check, so the agent can answer it in the same reply.

## 7. Fix Design

### 7.1 Non-destructive mark check

Add `_queued_trigger_state(agent_id, room_id) -> TriggerPopState` next to `_pop_queued_trigger`
(`turn_engine.py:640-674`), using `EXISTS` on `_queued_trigger_key` (`turn_engine.py:537-538`).
Redis errors return `UNKNOWN`, which means "post the draft", matching the protocol's posture of
never dropping a reply on an infrastructure fault.

### 7.2 Side-effect boundary

`ToolLoopOutcome` (`turn_engine.py:851`) gains `dispatched_tools: frozenset[str]`, filled at the
dispatch site (`turn_engine.py:4246`). A turn is eligible for a re-check only when every
dispatched name is in a new `READ_ONLY_TOOL_NAMES` frozenset declared beside
`BUILTIN_TOOL_NAMES` (`tool_registry.py:119-147`): `read_skill`, `web_search`, `file_search`,
`read_drafts`, `present_observation`. Any other name, including every MCP and local-function
tool, makes the turn ineligible. The artifact sink (`turn_engine.py:2745`) must also be empty. A
drift test asserts `READ_ONLY_TOOL_NAMES <= BUILTIN_TOOL_NAMES`.

### 7.3 The re-check

Inserted after the empty-reply guard (`turn_engine.py:3207`) and before the observer branch
(`turn_engine.py:3223`), so it covers both roles. When the mark is `PARKED` and the turn is
eligible:

1. Pop the mark with `_pop_queued_trigger`, keeping the trigger name and message id (Q-7). If
   the pop does not return `PARKED`, post the draft.
2. Load the messages newer than the snapshot: reload with `tx.load_model_history`
   (`transcript.py:179-224`) and keep the rows after the last `HistoryMessage.id`
   (`transcript.py:91`) of the snapshot from step 2 of §5.
3. Append to the turn's provider messages the draft as an assistant turn, then one
   `[Room notice]` user turn (the existing notice convention, `turn_engine.py:3823-3835`) that
   lists the new messages in the transcript's `Label: body` form and says, in substance: "Your
   draft above has not been posted. These messages arrived while you were writing it. Reply
   with the message to post now: the draft unchanged, a revision, or nothing to stay silent."
4. Emit the tool-round progress beacon (`turn_engine.py:4198`) so the client clears the streamed
   draft, then run one more streaming round with no tools for a room turn, or with only
   `present_observation` for an observer turn after clearing `observation_blocks`
   (`turn_engine.py:2754`).
5. The re-check's output replaces `final_text` (and, for an observer, the blocks), and the turn
   continues on its normal path: a non-empty result is posted as the reply; an empty result
   commits a skip with reason `withdrawn`, mirroring the empty-reply branch
   (`turn_engine.py:3140-3207`).
6. If the re-check fails (provider error, cancellation, a tool call on a room turn), post the
   draft and re-park the popped trigger with `_mark_trigger_queued` (`turn_engine.py:598-637`)
   so the follow-up still runs.

Messages arriving during the re-check park a fresh mark (`SET NX` succeeds because step 1
deleted the key), which the `finally` drain turns into a normal follow-up turn (Q-5).

### 7.4 Bookkeeping

- `reply_meta` (`turn_engine.py:3212`) and the `agent.turn_finished` audit record
  `recheck: unchanged | revised | withdrawn` (unchanged means the re-check text equals the draft
  after whitespace trimming), so the decision is visible in the audit trail.
- The worker counts an autostop round only for `completed` (`orchestration.py:181-183`), so a
  withdrawn turn counts none.
- The re-check is charged to the same turn's usage events.

### 7.5 Client

The room client already resets the streamed draft on the tool-round beacon and on an
`agent.finished` without `message_id` (`frontend/src/slices/conversation/__tests__/useChatroomSocket.test.ts:186`);
no change. The observer skip label needs a `withdrawn` key beside `no_input` in
`frontend/src/slices/conversation/locales/en.json:222` and `zh-TW.json:222`.

## 8. Regression Test Plan

Written first, failing against current code:

- `backend/tests/unit/test_turn_lock_and_coalescing.py`: extend `_FakeRedis` (`:28-81`) with the
  `EXISTS` path; `test_queued_state_does_not_consume_the_mark`.
- New `backend/tests/unit/test_reply_recheck.py` on `turn_engine_fakes.py`, with a scripted
  provider that answers the re-check:
  - parked mark, tool-free turn, re-check returns a revision: one `send_agent` with the
    revision, the re-check request contains the draft and the new messages, no follow-up
    enqueued (fails today: the draft is posted and a follow-up runs);
  - re-check returns the draft unchanged: posted, `reply_meta.recheck = unchanged`;
  - re-check returns empty: no message, result `skipped/withdrawn`;
  - parked mark plus a dispatched `start_activity`: no re-check, draft posted, follow-up
    enqueued;
  - parked mark plus a non-empty artifact sink: same as above;
  - re-check provider failure: draft posted and the trigger re-parked;
  - a message arriving during the re-check: a fresh mark is drained into one follow-up;
  - observer turn: the re-check offers only `present_observation` and the first-pass blocks are
    not recorded;
  - Redis error on the check: draft posted, no re-check.
- `backend/tests/unit/test_builtin_tools_wiring.py`: `READ_ONLY_TOOL_NAMES <= BUILTIN_TOOL_NAMES`.
- Frontend: a `useObservations` case that renders the `withdrawn` skip label.

## 9. Risks and Rollback

- **The agent may keep a stale draft.** Accepted by design (Q-1); the notice states plainly that
  new messages arrived, and the CRAT pack's prompt (`2026-10-12-crat-pack-prompt-platform-alignment`)
  tells its teacher to answer the group rather than the first message.
- **Visible retyping**: the streamed draft is cleared and the re-check streams again. FU-1.
- **Cost**: one extra call per eligible turn with a parked mark; it replaces the follow-up turn
  that would otherwise have run, so the total is about the same.
- **Latency**: the reply lands one generation later than today's first reply, and about when
  today's follow-up reply lands.
- **Rollback**: revert; no data or key schema change beyond the read-only `EXISTS`.

## 10. Acceptance Criteria

- [ ] AC-1: the §8 regression tests fail before the fix and pass after.
- [ ] AC-2: with a parked mark, an eligible room turn makes exactly one re-check call that
  contains its draft and every message newer than its snapshot, and posts exactly what the
  re-check returns.
- [ ] AC-3: an empty re-check result persists no message and returns `skipped/withdrawn`.
- [ ] AC-4: a re-checked turn consumes the parked mark; no follow-up runs unless a message
  arrived during the re-check.
- [ ] AC-5: a turn that dispatched any tool outside `READ_ONLY_TOOL_NAMES`, or produced
  artifacts, is not re-checked, posts its draft, and the follow-up runs.
- [ ] AC-6: a re-check failure posts the draft and re-parks the trigger.
- [ ] AC-7: an observer turn is re-checked with only `present_observation` available, and a
  withdrawn observation emits `observation.skipped {kind: withdrawn}`, labeled in both locales.
- [ ] AC-8: the mark check never consumes the mark, and a Redis failure on it posts the draft.
- [ ] AC-9: manual check on a running stack: four participants posting within one generation
  produce exactly one agent reply that reflects all four messages.
- [ ] AC-10: backend `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .`; frontend
  `pnpm test`, `pnpm lint`, `pnpm typecheck` pass.

## 11. SRS Delta

Amend [R13.27] (`REQUIREMENTS.md:757`), last sentence:

> The same holds for a committed skip (`empty_reply`, `no_input`, `knowledge_starved`,
> `withdrawn`), which reports `skipped`.

Add after [R13.27]:

> - **[R13.72]** **Re-check before posting.** Before a room or observer turn persists its reply,
>   it checks whether a coalesced follow-up trigger for the same agent and room is queued. If one
>   is, and the turn dispatched no tool outside the read-only set (`read_skill`, `web_search`,
>   `file_search`, `read_drafts`, `present_observation`) and produced no artifacts, the agent is
>   shown its unposted draft together with every message that arrived after its snapshot, and
>   decides once whether to post the draft, post a revision, or post nothing (`withdrawn`). The
>   re-check consumes the queued trigger; messages arriving during the re-check get a normal
>   follow-up turn. A turn with side effects posts its draft and the follow-up runs. If the
>   re-check cannot run, the draft is posted and the trigger stays queued.

Amend [R28.13] (`REQUIREMENTS.md:2208`): `observation.skipped` carries benign kinds
(`no_input`, `empty_reply`, `withdrawn`).

`docs/traceability.csv` is regenerated with `python scripts/traceability.py`.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — streaming could be held back for a turn that already sees a parked mark early,
  removing the visible retyping in §9.
