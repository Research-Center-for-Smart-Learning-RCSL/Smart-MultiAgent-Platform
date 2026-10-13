---
type: audit
status: reviewed
created: 2026-10-12
requirements: [R15.01, R15.06, R15.07, R28.16, R32.03]
---

# Audit: CRAT teacher agent under parallel multi-student discussion

## 1. Scope

- **Area**: the `crat-word-puzzle-room` agent pack
  (`backend/contexts/agents/infrastructure/examples/packs/crat-word-puzzle-room.json`), chiefly
  its teacher agent `ta-crat-teacher`, and the platform paths that agent depends on: wake-up
  evaluation (`contexts/orchestration/application/wakeup_service.py`), turn coalescing and reply
  posting (`contexts/agents/application/runtime/turn_engine.py`), the `update_wakeup` tool
  (`contexts/agents/application/runtime/tool_registry.py`), and the context the model receives
  (transcript, system prompt blocks, canvas digest).
- **Trigger**: a classroom test by the user in which several students posted in parallel and
  the teacher agent failed to hold the discussion together. Three symptoms were reported:
  per-message replies flooding the room, some students' ideas being dropped, and no follow-up
  for a student who fell behind or went off topic.
- **Intent sources**: two kinds. The platform's documented behavior in `REQUIREMENTS.md` §15
  ([R15.01], [R15.06], [R15.07]) and the tool contracts; and the pack's own prompt, which
  states what the teacher agent is supposed to do (staged flow A to E, stall handling, A2A
  instruction). Several findings are mismatches between the two: the prompt assumes platform
  behavior that the SRS does not provide.
- **Depth**: thorough static read of the pack, the wake-up and turn paths, and the context
  assembly, with five investigation passes (pack and tests, wake-up evaluation, burst behavior,
  roster/timestamps/memory, canvas). No live classroom run was repeated for this audit; the
  symptom reports come from the user's test.

## 2. Coverage

Read in full: the two CRAT packs, the pack catalogue parser, the CRAT activity type in
`contexts/activities/infrastructure/examples/courses/chinese-character-creativity.json`, the
CRAT prompt-constraint tests in `tests/unit/test_agent_example_packs.py:622-687`,
`WakeupConfig` (`contexts/orchestration/domain/models.py:181-295`),
`WakeupService.on_message_created` and `update_wakeup` (`wakeup_service.py:62-150,309-379`),
and the `update_wakeup` tool (`tool_registry.py:359-398`). Traced but not read in full:
`turn_engine.py` (lock, coalescing, history assembly, reply posting), `transcript.py`,
`activity_context_provider.py`, the canvas tools and canvas context provider.

A second pass on the same day covered the peer (SA) and analyst (AA) agents' wake-up
configuration and prompts against parallel discussion (F-14 to F-16), the silence clock's
re-arm sites, and who can read agent configuration (F-13).

Not covered: the other two courses' packs (`character-building-room`, `object-reinterpretation-room`),
which share the prompt skeleton and may share F-3, F-4, F-10 and F-11 (recorded as FU-1);
frontend rendering of agent replies; provider-specific behavior.

## 3. Findings

## F-1: A reply generated from a stale snapshot is posted even when a fuller follow-up turn is already queued

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: history is loaded once before streaming (`turn_engine.py:2835`); triggers that
  arrive while the turn holds its lock are coalesced into one parked mark
  (`turn_engine.py:598-637`) and drained into exactly one follow-up turn
  (`turn_engine.py:677-711`); the reply is persisted with no comparison against messages newer
  than the snapshot (`turn_engine.py:3290-3301`).
- **Failure scenario**: four students post five messages within ten seconds. Message 1 wakes the
  teacher; messages 2 to 5 arrive during generation and coalesce. The teacher posts a reply
  addressed to message 1 only, then the follow-up turn posts a second reply. Students see the
  agent answering one person after the discussion has moved, immediately followed by another
  message.
- **Blast radius**: every agent with a message-driven trigger in a room with concurrent
  speakers, not only the CRAT pack.
- **Intent source**: none explicit in the SRS; the pack prompt requires the agent to respond to
  group state rather than to individual messages (`crat-word-puzzle-room.json:24`, 群組互動規範).

## F-2: `every_n_messages` counts agent messages, and a firing that lands on the agent's own message is lost

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: the counter increments for every room message, user or agent
  (`wakeup_service.py:76-77,117`); when `count % n == 0` falls on the agent's own reply, the
  firing is skipped rather than deferred (`wakeup_service.py:119-121`). The tool tells the model
  the opposite: "Fire a wake-up after every N user messages" (`tool_registry.py:364`).
- **Failure scenario**: the teacher sets `n = 3` for free discussion, intending "every three
  student messages". Its own replies and the peer agent's replies advance the counter, and
  whenever the third count is the teacher's own message, that wake-up never happens. The real
  cadence is irregular and unrelated to student activity.
- **Blast radius**: every agent that tunes `n` expecting user-message semantics.
- **Intent source**: [R15.01] specifies counting all messages, so the counting itself is
  documented behavior; the tool description contradicts [R15.01], and the lost firing is not
  described anywhere.

## F-3: The pack's stage-by-stage switch to silence mode cannot take effect

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: the teacher ships with `silence_minutes.enabled: false`
  (`crat-word-puzzle-room.json:15-20`; no test pins it, the similar pin at
  `test_agent_example_packs.py:123-125` is for the creative-thinking teacher);
  `update_wakeup` writes only `n` and `t_minutes`, never `enabled`
  (`wakeup_service.py:319-321,361-364`).
- **Failure scenario**: step B1 tells the agent to "switch to silence_minutes = 1.5". The call
  at best sets `t_minutes` on a disabled trigger. Every silence branch in A1, B1, C1 and E1 is
  unreachable, and when the whole group goes quiet the teacher never wakes.
- **Blast radius**: the CRAT teacher; FU-1 covers the sibling packs.
- **Intent source**: [R15.06] restricts runtime edits to the two numeric fields, so the
  platform behaves as specified; the pack prompt assumes a capability the SRS does not grant.

## F-4: The pack asks for fractional `silence_minutes`

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: the tool schema declares `silence_minutes` as `integer` (`tool_registry.py:366-369`)
  and schema violations are refused before invoke (`tool_registry.py:324-332`); [R15.07] bounds
  `t_minutes` to `[1, 1440]`. The prompt asks for 1.5 minutes and refers to 30-second silences.
- **Failure scenario**: the call is refused as invalid arguments and the model has to guess a
  replacement value.
- **Blast radius**: the CRAT teacher.
- **Intent source**: [R15.07].

## F-5: The model sees no time information for chat messages

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `HistoryMessage` carries no timestamp (`transcript.py:88-105`); provider messages
  are rendered as `Label: body` only (`turn_engine.py:3797-3854`); no current time is injected.
  Timestamps reach the model only on activity rows, observer memory and draft ages.
- **Failure scenario**: the prompt's stall rules ("silent for 2 minutes", "discussion stalled")
  have no input to be judged from; five simultaneous messages and five messages over three
  minutes look identical.
- **Blast radius**: every agent prompt that reasons about pacing.
- **Intent source**: none; recorded as a capability gap.

## F-6: The model receives no room roster

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: presence is tracked (`contexts/conversation/infrastructure/presence.py:97`) but
  read only by the member-roster route (`app/api/v1/chatrooms.py:1251-1252`) and the wake-up
  empty-room gate (`wakeup_service.py:135`); the guest list loaded at `turn_engine.py:2794` is
  used only as a label lookup; no system prompt block (`turn_engine.py:1023-1046`) lists members.
- **Failure scenario**: a student who joined and never spoke is invisible to the teacher, which
  can only follow up on people who spoke and then went quiet.
- **Blast radius**: every facilitator-style agent.
- **Intent source**: none; recorded as a capability gap.

## F-7: Room agents have no working memory across turns

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: the system prompt blocks (`turn_engine.py:1023-1046`) include observer memory
  for observer bindings only (`turn_engine.py:3493-3511`); no note or scratchpad tool exists for
  normal bindings. The canvas could hold shared state, but its digest assigns fresh ids and
  omits positions (`contexts/canvas/application/canvas_context_provider.py:51`,
  `contexts/canvas/domain/canvas_digest.py:40-73`), so an agent cannot update an object it
  created in an earlier turn.
- **Failure scenario**: each turn the teacher re-derives who proposed which character from an
  interleaved transcript; with many speakers it weights the latest messages and drops earlier
  ideas.
- **Blast radius**: every facilitator-style agent.
- **Intent source**: none; recorded as a capability gap.

## F-8: The pack promises an `instruct` tool that no agent runtime provides

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: the reserved runtime tool names contain no `instruct` (`tool_registry.py:119-147`);
  A2A instruct exists only as an API and workflow node, and
  `docs/tasks/2026-07-22-workflow-capability-enforcement/spec.md:468` records that no
  agent-facing instruct tool exists. Both the teacher and peer prompts state that a grant adds it.
- **Failure scenario**: the teacher cannot delegate the "speak first" or "follow up the
  struggling student" move to the peer agent; teachers reading the prompt look for a grant that
  does not exist. The teacher's fallback branches keep the flow from blocking.
- **Blast radius**: the CRAT teacher and peer prompts.
- **Intent source**: the pack prompt itself.

## F-9: `update_wakeup` writes agent-wide state, so rooms sharing one agent interfere

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `update_wakeup` persists through `patch_agent` on the agent row
  (`wakeup_service.py:376-379`); there is no per-binding override. Installing a pack is
  idempotent by agent name within a project (`contexts/agents/application/example_service.py:236-245`),
  so a project that installs `crat-word-puzzle-room` has exactly one teacher agent, which every
  group room binds.
- **Failure scenario**: one teacher agent bound to three group rooms; group A enters free
  discussion and sets `n = 3`; group B, still at the reveal step, inherits `n = 3`. This is the
  normal shape of a multi-group CRAT class, not an edge case, which is why the severity was
  raised from minor after the first draft of this audit.
- **Blast radius**: any agent bound to several rooms that self-tunes its cadence.
- **Intent source**: [R15.06] does not state the scope of the change.

## F-10: The prompt says the agent may stay silent but not how

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: an empty or whitespace reply is not persisted and the turn returns `skipped`
  (`turn_engine.py:3140-3207`); the prompt says only "you may choose not to respond".
- **Failure scenario**: the model, once woken, almost always produces text, so the
  no-per-message-reply rule is not honored.
- **Blast radius**: the CRAT teacher; FU-1 covers the sibling packs.
- **Intent source**: the pack prompt itself.

## F-11: The prompt steers the teacher away from individuals and models off-topic only at group level

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: the rules "do not reply to each message", "do not call on silent students" and
  "do not speculate about students who have not spoken" (`crat-word-puzzle-room.json:24`, 群組互動規範
  and 界線), with off-topic defined only as a group condition in C1.
- **Failure scenario**: a student drifts off topic or stops following; nothing in the prompt
  licenses a targeted follow-up, so the teacher does not make one.
- **Blast radius**: the CRAT teacher.
- **Intent source**: the pack's own role statement (guide, not lecturer; attentive to silent
  students).

## F-12: The submission instructions omit a required field

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: the activity requires `target_character` and `reasoning`
  (`chinese-character-creativity.json`, `crat-word-puzzle` `payload_schema.required`); step D1
  tells students to fill the target character and three words only.
- **Failure scenario**: a group submits without 推理歷程 and is stopped by form validation.
- **Blast radius**: CRAT group submissions.
- **Intent source**: the activity type schema.

## F-13: The answer key has no private path to the teacher agent

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: the validator is `filled_count_coverage` with `min_filled: 2`
  (`chinese-character-creativity.json`), which does not check correctness; the teacher is told
  not to generate clues and to confirm the answer (B1, E1); the pack names no channel for the
  teacher's answer key other than the room.
- **Failure scenario**: a teacher pastes the answer key into the room and every student sees it,
  or provides none and the agent cannot confirm the answer. Putting it in the teacher agent's
  settings instead also leaks it: any project member can list every agent with its full
  `system_prompt` (`backend/app/api/v1/agents.py:446-459`, membership-only gate at
  `shared_kernel/auth/dependencies.py:168-170`), and every CRAT student is a project member,
  because the activity is a group submission, groups are project Member Groups, and a guest
  cannot join one (`docs/tasks/2026-08-24-group-activity-submissions/spec.md`, OQ-1).
- **Blast radius**: CRAT rounds; more generally, any agent prompt holding material students
  should not read (FU-9).
- **Intent source**: the pack prompt itself.

## F-14: Silence triggers fire while participants are still composing

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: [R15.02] defines room activity as a message, a join or an activity submission;
  the silence clock is touched only on those (`wakeup_service.py:111,287`,
  `contexts/conversation/application/triggers.py:138-162`) and after a firing
  (`app/workers/tasks/orchestration.py:303-305`). `typing.start` updates presence and emits the
  indicator but never touches the clock (`app/api/ws/chatroom.py:231-248`).
- **Failure scenario**: at step B1 four students each spend two minutes writing their first
  association. No message is sent, so the room reads as silent; the peer agent (`t = 2`) fires
  and posts its own association path while every student is mid-sentence, which is the
  opposite of B1's "give quiet thinking space". Any shorter teacher silence timer fires
  earlier.
- **Blast radius**: every silence-triggered agent in a room where participants compose long
  messages; worst in individual-thinking steps.
- **Intent source**: the pack's B1 step (quiet individual thinking first) and the peer agent's
  role (speak first only in a genuine lull).

## F-15: The analyst agent produces nothing during an active lesson

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `aa-crat-analyst` has `every_n_messages` disabled and `silence_minutes` at
  `t = 10` (`crat-word-puzzle-room.json:55-63`), so it wakes only after ten minutes with no
  message, join or submission. There is no on-demand route for the teacher: the observation API
  offers listing and release only (`app/api/v1/observations.py:145,161`).
- **Failure scenario**: a 40-minute lesson with steady discussion never has a ten-minute lull;
  the teacher receives the first observation after the class ends, when it can no longer
  inform teaching decisions, which is the stated purpose of the analyst ("你的觀察是給教學決策用的").
- **Blast radius**: the CRAT analyst; the creative-thinking analyst shares the silence-only
  pattern (`test_agent_example_packs.py:131-134`).
- **Intent source**: the analyst prompt's own purpose statement.

## F-16: The analyst must write codes, but chat-only participants have none

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: the analyst prompt requires codes, never names, in every note
  (`crat-word-puzzle-room.json:68`, 觀察注意事項 1). An observer reads the room transcript
  ([R28.05]), which labels participants by display name (`turn_engine.py:3845-3854`); the code
  legend maps codes to names only for participants who appear in the recent activity rows
  (`activities/application/activity_context_provider.py:234-275`).
- **Failure scenario**: in a multi-speaker discussion the most informative participation signal
  is in the chat, but a student who spoke and has not submitted has no code. The analyst either
  writes the name, breaking its rule, or leaves that student out of the participation
  description.
- **Blast radius**: every analyst prompt with a codes-only rule.
- **Intent source**: the analyst prompt itself.

## 4. Refuted Candidates

- **"Every message runs its own turn."** Refuted: turns are serialized by a per-(agent, room)
  Redis lock and concurrent triggers coalesce into one follow-up (`turn_engine.py:1203,598-637`).
  The defect is the stale first reply (F-1), not unbounded turns.
- **"The agent has no way to stay silent."** Refuted: an empty reply is a supported skip
  (`turn_engine.py:3140-3207`). The defect is that the prompt never says so (F-10).
- **"The quoting markers in the prompt do not match the activity block."** Refuted: `#`, `::`,
  `—` and `g:` match `activity_context_provider.py:60-102,350`.

## 5. Hand-off

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | fix (the agent re-checks its draft against new messages; the platform only detects and bounds) | `docs/tasks/2026-10-12-stale-reply-recheck/` |
| F-2 | fix (platform behavior change, SRS delta) | `docs/tasks/2026-10-12-wakeup-human-count-and-trigger-toggle/` |
| F-3 | fix in the pack now; platform toggle in the feature | `docs/tasks/2026-10-12-crat-pack-prompt-platform-alignment/`, `docs/tasks/2026-10-12-wakeup-human-count-and-trigger-toggle/` |
| F-4 | fix | `docs/tasks/2026-10-12-crat-pack-prompt-platform-alignment/` |
| F-5 | fix | `docs/tasks/2026-10-12-agent-message-time-context/` |
| F-6 | defer (product decision) | FU-3 |
| F-7 | defer (product decision) | FU-4 |
| F-8 | fix the prompt; defer the tool | `docs/tasks/2026-10-12-crat-pack-prompt-platform-alignment/`, FU-5 |
| F-9 | avoid in the pack (the teacher never tunes its own cadence); defer per-binding state | `docs/tasks/2026-10-12-crat-pack-prompt-platform-alignment/`, FU-6 |
| F-10 | fix | `docs/tasks/2026-10-12-crat-pack-prompt-platform-alignment/` |
| F-11 | fix | `docs/tasks/2026-10-12-crat-pack-prompt-platform-alignment/` |
| F-12 | fix | `docs/tasks/2026-10-12-crat-pack-prompt-platform-alignment/` |
| F-13 | fix: no agent holds the answer key, the teacher confirms answers; defer a private channel and agent-config visibility | `docs/tasks/2026-10-12-crat-pack-prompt-platform-alignment/`, FU-7, FU-9 |
| F-14 | mitigate in the pack now; fix in the platform | `docs/tasks/2026-10-12-crat-pack-prompt-platform-alignment/`, `docs/tasks/2026-10-12-wakeup-human-count-and-trigger-toggle/` |
| F-15 | fix in the pack | `docs/tasks/2026-10-12-crat-pack-prompt-platform-alignment/` |
| F-16 | fix in the pack; platform fix deferred | `docs/tasks/2026-10-12-crat-pack-prompt-platform-alignment/`, FU-11 |

## 6. Out-of-scope Observations

- **FU-1**: `character-building-room` and `object-reinterpretation-room` share the CRAT prompt
  skeleton; F-3, F-4, F-10 and F-11 are likely present there and were not checked.
- **FU-2**: closed, taken into scope as `2026-10-12-agent-message-time-context` (F-5).
- **FU-3**: a room member block listing members by label or code with a "has spoken" flag
  (F-6). Privacy review needed: it discloses presence of non-speakers to the model.
- **FU-4**: per-room working memory for normal bindings, or a canvas digest that carries
  stable element ids so an agent can maintain a shared candidate board (F-7).
- **FU-5**: an agent-facing `instruct` tool for room agents (F-8).
- **FU-6**: per-binding wake-up state so `update_wakeup` does not leak across rooms (F-9).
- **FU-7**: a private teacher-to-agent instruction channel for answer keys and steering (F-13).
- **FU-8**: the canvas `create` path does not catch `CrdtUpdateError`
  (`canvas_tools.py:143`), unlike update and delete (`:239`, `:301`); route to `check-quality`.
- **FU-9**: restrict who can read an agent's configuration, so that a project member who is a
  student sees agent names (already served separately by `GET /agents/names`,
  `agents.py:476-495`) but not system prompts (F-13). Wider than CRAT: every teaching prompt is
  readable by the students it is written about.
- **FU-10**: closed, taken into `2026-10-12-wakeup-human-count-and-trigger-toggle` (F-14).
- **FU-11**: give observer transcripts the same participant codes the activity legend uses,
  so a codes-only analyst can refer to chat-only participants (F-16).
