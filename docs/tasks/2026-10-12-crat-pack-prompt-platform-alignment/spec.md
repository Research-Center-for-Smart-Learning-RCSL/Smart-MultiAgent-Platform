---
type: bugfix
status: implemented
created: 2026-10-12
requirements: [R15.01, R15.02, R15.03, R15.06, R15.07]
depends_on: [2026-10-12-agent-message-time-context]
---

# Align the CRAT room pack with the platform it runs on

## 1. Summary

The CRAT teacher agent (`ta-crat-teacher` in
`backend/contexts/agents/infrastructure/examples/packs/crat-word-puzzle-room.json`) is written
against platform behavior that does not exist. Its silence-based stall handling can never fire,
it asks the `update_wakeup` tool for values the tool refuses, it promises an `instruct` tool no
runtime provides, it never learns that an empty reply is how to stay silent, and its rules steer
it away from following up individual students. In a classroom test with several students
posting in parallel the teacher replied to messages one by one, dropped some students' ideas and
did not follow up a student who fell behind. This task rewrites the pack so that, on the
platform as it stands today, the teacher speaks once per wave of discussion, covers every
proposal, follows up one individual per turn, and actually handles silence.

Born from `docs/audits/2026-10-12-crat-ta-multi-speaker-facilitation/findings.md`, findings F-3,
F-4, F-8, F-10, F-11, F-12 and F-13 (the prompt half of each); F-9, which the pack avoids by
never tuning its own cadence (Q-3a); F-14, mitigated in prompts until the platform fix lands;
and the analyst findings F-15 and F-16.

## 2. Observed vs Expected

- **Observed**
  - The teacher ships with `silence_minutes.enabled: false` (`crat-word-puzzle-room.json:15-20`),
    and `update_wakeup` writes only `n` and `t_minutes` (`wakeup_service.py:319-321,361-364`).
    Every silence branch in steps A1, B1, C1 and E1 is unreachable (F-3).
  - The prompt asks for `silence_minutes = 1.5` and refers to 30-second silences; the tool
    schema is `integer` (`tool_registry.py:366-369`) and refuses the call (F-4).
  - The teacher and peer prompts say a grant adds an `instruct` tool; no runtime tool of that
    name exists (`tool_registry.py:119-147`) (F-8).
  - The prompt says the teacher may choose not to respond but never says how; an empty reply is
    the mechanism (`turn_engine.py:3140-3207`) (F-10).
  - The rules forbid per-message replies, calling on silent students and speculating about
    non-speakers, and model off-topic only at group level, so nothing licenses a targeted
    follow-up (F-11).
  - Step D1 omits the required `reasoning` field (F-12).
  - The prompt tells the teacher not to generate clues and to confirm the answer, but names no
    place for the answer key except the room (F-13).
- **Expected**: the pack's own stated intent: the teacher guides rather than lectures, speaks
  at the right moment rather than continuously, responds to group state rather than to single
  messages, stays aware of quiet students, and moves the group through steps A to E with stall
  handling at each step. Platform behavior is the one specified by [R15.01] (count all room
  messages), [R15.02] (silence trigger), [R15.03] (autostop rounds), [R15.06] (runtime edits
  limited to `n` and `t_minutes`) and [R15.07] (integer bounds).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Split the audit's work into one dossier or several? | Four: this pack fix, `2026-10-12-stale-reply-recheck`, `2026-10-12-wakeup-human-count-and-trigger-toggle` and `2026-10-12-agent-message-time-context`. | User's choice. This dossier works without the re-check and wake-up dossiers; it depends on the time-context one only (Q-11). |
| Q-2 | Should this depend on `2026-07-19-large-artifacts-silently-dropped` (in progress, edits `turn_engine.py`)? | No. | This dossier edits pack JSON and tests only. |
| Q-3 | How does the teacher handle silence when `update_wakeup` cannot toggle triggers? | Ship both triggers enabled: `every_n_messages` at `n = 2`, `silence_minutes` at `t_minutes = 2`, `autostop_rounds = 2`. The peer moves to `t_minutes = 3`. | The only configuration under which every silence branch is reachable on today's platform ([R15.06]). The teacher and peer timers differ so they never fire in the same sweep. See §7.2 for the chain. |
| Q-3a | Should the teacher still change `n` per step at runtime? | No. The teacher never calls `update_wakeup`; `n` stays 2 in every step, and pacing comes from the empty-reply rule (Q-4). | Installing a pack is idempotent by agent name within a project (`example_service.py:236-245`), so a project has one teacher agent, which every group room shares; `update_wakeup` writes the agent row (`wakeup_service.py:376-379`), so one group entering free discussion would change the cadence of every other group (audit F-9). |
| Q-3b | Why `n = 2` rather than 1? | To stay well under the per-(agent, room) turn rate limit. | The limit is 30 turns per 300 s (`turn_engine.py:276-277`), and a rate-limited turn shows an error in the room (`turn_engine.py:2668-2683`). With `n = 1`, every message from four students and the peer wakes the teacher, an empty reply still spends a turn, and coalescing still allows about one turn per generation, so a busy discussion can reach 30 in five minutes. `n` counts agent messages too ([R15.01]), so `n = 2` is about every second student message. |
| Q-4 | How does the prompt keep the teacher from replying to every message it is woken by? | State explicitly that an empty reply posts nothing, and make "output nothing" the default outcome of a wake in steps B1 and C1 unless a listed condition holds. | `turn_engine.py:3140-3207` already treats an empty reply as a skip. |
| Q-5 | What replaces the instruct-based escalation? | Remove `instruct` from both prompts. The peer agent's own silence timer takes over the "speak first" move (§7.2). | No agent-facing instruct tool exists (F-8); the timing chain gives the same escalation order without one. |
| Q-6 | How does the teacher follow up individuals without flooding? | One message per turn with a fixed shape: a summary of group state, at most one targeted follow-up of one person, then one question that moves the group forward. Rotate the person across turns. | Satisfies both "no per-message replies" and "do not pressure by name" while covering the reported symptom. |
| Q-7 | Where does the answer key go? | Nowhere the agents can read. The teacher posts the three clues in the room; the teacher agent leads the group through verifying each word and says so when it is unsure a word exists; whether the answer is right is announced by the teacher at the end of the round, and the agent never claims it. | User's choice. An earlier draft put the key in the teacher's system prompt. That leaks it: CRAT is a group submission, groups are project Member Groups and guests cannot join one (`docs/tasks/2026-08-24-group-activity-submissions/spec.md`, OQ-1), so every CRAT student is a project member, and any project member can list every agent with its full `system_prompt` (`backend/app/api/v1/agents.py:446-459`, membership-only gate at `shared_kernel/auth/dependencies.py:168-170`). Restricting who can read agent configuration is recorded in the audit as FU-9. |
| Q-8 | How does the pack handle silence wakes that fire while students are still composing (audit F-14)? | The teacher and peer each call `read_drafts` first on a lull, when granted, and stay silent if anyone is drafting. The two-minute teacher timer (Q-3) leaves a first thinking window. The platform fix is in `2026-10-12-wakeup-human-count-and-trigger-toggle`. | User's choice. An earlier draft also told the teacher to decline its first B1 silence wake; that rule was dropped because the model cannot tell which wake is the first (Q-11), and a declined wake also shifts the timing chain. |
| Q-9 | Should the analyst observe during the lesson (audit F-15)? | Yes: enable `every_n_messages` at `n = 20` alongside the existing ten-minute silence trigger. | User's choice. An observer is exempt from the presence gate (`wakeup_service.py:130-134`), and its output reaches only the room creator ([R28.13]); one call per twenty room messages. |
| Q-10 | How does the analyst refer to a participant who has no code (audit F-16)? | It describes the behavior without identifying the participant and says that it did so; it never writes a name. | The codes-only rule stands; the platform fix (codes in the observer transcript) is the audit's FU-11. |
| Q-11 | How does the teacher know a wake is a lull, and which hint level applies? | From the time line that `2026-10-12-agent-message-time-context` adds: when the last room message is two minutes old or more, the wake is a lull; the hint level is one plus the number of the teacher's own messages since the last participant message. This dossier depends on that one. | User's choice. The wake reason never reaches the model: `trigger` is used only in audits and reply metadata (`turn_engine.py:2720,3212`), and a declined wake leaves nothing in the transcript. Without a clock, a lull right after a participant's message reads exactly like a message wake, so a transcript-only rule cannot give the first hint. |

## 4. Reproduction

Preconditions: a project with the `chinese-character-creativity` course installed and the
`crat-word-puzzle-room` pack installed; one room with the teacher and peer agents bound; four
human participants.

1. Run step A to B1 (reveal three clues).
2. All four participants post a candidate character within ten seconds.
3. Observe: the teacher replies to the first message, then replies again (F-1 in the audit,
   fixed by `2026-10-12-stale-reply-recheck`), and the replies address individuals in turn.
4. All participants stop posting for three minutes. Observe: the teacher never speaks (F-3).

## 5. Root Cause Analysis

The root cause is that the pack prompt was written against an assumed platform rather than the
specified one. Each link:

1. The pack disables the silence trigger (`crat-word-puzzle-room.json:15-20`) and expects to
   enable it at runtime.
2. [R15.06] and `wakeup_service.py:319-321` allow only numeric edits, so the trigger stays off.
3. The pack's rule set (群組互動規範, 界線) forbids the individual follow-up the symptom needs, and
   never tells the model how to stay silent, so with `n = 1` the model speaks on every wake.
4. The pack references a tool (`instruct`) that is not in `BUILTIN_TOOL_NAMES`
   (`tool_registry.py:119-147`).

Aggravating factors outside this dossier: the stale first reply (audit F-1), agent messages
advancing `n` ([R15.01], audit F-2), and the absence of timestamps, roster and working memory
in the model's context (audit F-5 to F-7).

## 6. Blast Radius and Sibling Suspects

- **Blast radius**: new installs of `crat-word-puzzle-room` only. Installing is idempotent by
  agent name and never updates an existing agent (`example_service.py:236-245`), so projects
  that already installed the pack keep the old prompt until they reinstall under a new name or
  edit the agent by hand. §9 covers the communication.
- **Sibling suspects**
  - `character-building-room` and `object-reinterpretation-room`: same course, same prompt
    skeleton. Not checked by the audit (its FU-1). This task checks each for F-3, F-4, F-8 and
    F-10 and records the result as a confirmed or cleared line in §12; fixing them is FU-1 here
    unless the check finds a defect identical in shape, in which case it is fixed in the same
    change (AC-9).
  - `crat-word-puzzle-design` (design agent): instructs the designer to draft TA/SA prompts. It
    must stop recommending `instruct` and fractional minutes, or the next drafted prompt repeats
    the defects (AC-8).

## 7. Fix Design

All edits are in `crat-word-puzzle-room.json`, `crat-word-puzzle-design.json` and
`tests/unit/test_agent_example_packs.py`. No platform code changes.

### 7.1 Teacher wake-up configuration

`ta-crat-teacher.wakeup_config.triggers`:

| Trigger | Value |
|---|---|
| `every_n_messages` | `{"enabled": true, "n": 2}` |
| `silence_minutes` | `{"enabled": true, "t_minutes": 2, "autostop_rounds": 2}` |

`sa-crat-peer.wakeup_config.triggers.silence_minutes.t_minutes` becomes 3; its other values are
unchanged.

The prompt's 動態調頻規則 section and every per-step `[喚醒策略: ...]` tag are deleted, and the
prompt says not to call `update_wakeup` (Q-3a). In their place the prompt separates two kinds of
wake by the time line (Q-11):

- **A lull** (the last room message is two minutes old or more): apply the step's stall
  handling at the level of §7.2, unless `read_drafts` shows a participant drafting (Q-8).
- **Anything else**: apply the step's conditions, and reply with nothing unless one holds: in
  B1 until at least two participants have proposed a character, in C1 unless one of the C1
  conditions holds, and in every step when no participant has spoken since the teacher's own
  last message.

### 7.2 Silence escalation without `instruct`

Every room message re-arms every bound agent's silence timer (`wakeup_service.py:111`), a firing
re-arms it again (`app/workers/tasks/orchestration.py:303-305`), and only a completed reply
counts toward autostop (`orchestration.py:181-183`), which resets on the next user message
(`wakeup_service.py:113-114`). With the teacher at `t = 2, autostop = 2` and the peer at
`t = 3, autostop = 2`, a silent group gets, counting from the last participant message:

| Elapsed silence | Who wakes | Level (Q-11) | Prompt instruction |
|---|---|---|---|
| about 2 min | teacher | 1 (no own message since the participant's) | first-level hint (start from any clue) |
| about 4 min | teacher | 2 (one own message since) | second-level hint (radical or stroke-count hint); autostop is now reached |
| about 7 min | peer | n/a, a peer wake is always a lull or a mention | shares its own association path, not the answer |
| next user message | all timers and autostop reset | | step resumes |

Each teacher message re-arms the peer's timer, which is why the peer speaks three minutes after
the second hint rather than at three minutes. The sweep runs every 30 seconds
(`orchestration.py:251`), so each time is accurate to about 30 seconds. A teacher wake declined
because a participant is drafting (Q-8) re-arms only the teacher's own timer
(`orchestration.py:305`) and consumes no level, so the chain shifts by one teacher interval; AC-10
records the observed timings against this table with that in mind.

### 7.3 Teacher prompt rewrite

- **Silence rule** (F-10): "To stay silent, reply with nothing. An empty reply posts nothing to
  the room." Placed in 群組互動規範 and repeated as the default outcome of B1 and C1.
- **Turn shape** (F-11, Q-6): in B1 and C1 a turn that speaks produces exactly one message
  with three parts: a one or two sentence summary of the group's current candidates and paths;
  at most one targeted follow-up of one participant (an unanswered idea, a participant who
  spoke earlier and has gone quiet, or an off-topic remark brought back to a clue); one forward
  question. Rotate the participant across turns.
- **Working ledger** (audit F-7, interim): before deciding whether to speak, the teacher
  rebuilds from the transcript a private list of participant, proposed character, starting
  clue and verification status, and checks the list for unanswered proposals. It is not
  posted.
- **Rule edits** (F-11): replace "do not reply to each message" with "do not reply message by
  message; one message per turn covers the group"; keep "do not pressure by name" but
  distinguish it from inviting one participant to expand on what they already said; add an
  individual-level off-topic condition to C1.
- **Integer minutes** (F-4): remove every fractional or seconds-based duration; with the cadence
  table gone (§7.1), no duration remains in the prompt at all.
- **instruct** (F-8): delete the A2A 指令規則 section and every "if you have instruct" branch.
- **D1** (F-12): name all required fields, including 推理歷程.
- **Answer key** (F-13, Q-7): B1 takes the clues from the teacher's message in the room and,
  if none has been posted, asks the teacher to post them, without generating any. C2 verifies
  each of the three words with the group and names any word the agent is unsure exists as
  uncertain rather than judging it. E1 asks the teacher to confirm the answer and no longer
  states that it is correct; the 界線 rule against revealing the answer stays, since the agent
  no longer has one.
- **Group-state scope**: no change to the quoting, draft and activity-control sections, which
  the audit found consistent with the platform.
- **Composing is not a stall** (Q-8): on a lull, when `read_drafts` is available, call it first
  and reply with nothing if any participant is drafting.

### 7.4 Peer prompt

Delete the A2A 規則 section and the "when instructed by TA" strategy. Its silence timer moves to
three minutes (§7.1). Keep its silence-driven role; add that it speaks at most once per silence, replies with nothing when the room is
already active, and, when `read_drafts` is available, checks it first and replies with nothing
if any participant is drafting (Q-8).

### 7.4a Analyst configuration and prompt

- `aa-crat-analyst.wakeup_config.triggers.every_n_messages` becomes
  `{"enabled": true, "n": 20}`; `silence_minutes` is unchanged (Q-9).
- 觀察注意事項 gains a rule for participants who appear in the chat but have no code in the
  legend: describe what happened without identifying them, say that the participant is
  unidentified because they have no code, and never write a name (Q-10).
- 回報維度 3 (參與分布) is rewritten to draw on both the chat transcript and the activity events,
  under the same identification rule.

### 7.5 Design agent

In `crat-word-puzzle-design.json`, add to 提示詞規範 that drafted prompts must use integer minutes,
must not reference `instruct` or `update_wakeup`, and must state the empty-reply rule; and that
the teacher version of a question set, with its answers, stays with the teacher and is never
pasted into any agent's settings, because every project member can read them (Q-7).

## 8. Regression Test Plan

All in `backend/tests/unit/test_agent_example_packs.py`, written first and failing against the
current pack:

- `test_crat_teacher_ships_both_triggers` asserts §7.1's values for the teacher and the peer's
  `t_minutes = 3`.
- `test_crat_teacher_reads_lulls_from_the_time_line` asserts the teacher prompt defines a lull
  by the last message's age and the hint level by its own messages since the last participant
  message, and carries no "first or second time you are woken" wording.
- `test_crat_prompts_reference_no_unavailable_tool` asserts `instruct` does not appear in any
  `crat-word-puzzle-*` prompt (as a tool name, `instruct ` or `instruct 工具`).
- `test_crat_prompts_use_integer_minutes` asserts no `silence_minutes = <non-integer>`, no
  `秒` duration and no `\d+\.\d+ 分鐘` in the CRAT prompts.
- `test_crat_teacher_states_the_empty_reply_rule` asserts the empty-reply sentence is present.
- `test_crat_teacher_names_every_required_field` reads `payload_schema.required` from the course
  file and asserts each field's `title` appears in the teacher prompt. Reading the schema rather
  than hard-coding the titles keeps the test honest if the course changes.
- `test_crat_teacher_carries_the_turn_shape` asserts the three-part turn shape and the
  one-participant limit are stated.
- `test_crat_prompts_do_not_tune_their_own_cadence` asserts `update_wakeup` appears in the
  teacher prompt only in the sentence forbidding it, and that no `[喚醒策略` tag remains.
- `test_crat_teacher_never_claims_the_answer` asserts the E1 step asks the teacher to confirm
  the answer and that the prompt carries no answer-key section.
- `test_crat_analyst_observes_during_the_lesson` asserts §7.4a's `every_n_messages` value and
  that silence stays enabled.
- `test_crat_room_agents_check_drafts_before_breaking_silence` asserts the teacher and peer
  prompts carry the `read_drafts` check on a lull.
- `test_crat_analyst_never_names_an_uncoded_participant` asserts the uncoded-participant rule.

## 9. Risks and Rollback

- **Silence wakes cost model calls.** With `t = 2` the teacher can wake every two minutes of
  silence; autostop bounds completed replies to two per silence, but empty replies do not count,
  so a model that keeps declining keeps being woken. Accepted: one call per two minutes per room,
  ending when a student speaks.
- **Message wakes cost model calls, and the rate limit is visible.** With `n = 2` the teacher
  wakes on about every second message and usually answers with nothing. A turn refused by the
  per-(agent, room) limit (30 per 300 s, `turn_engine.py:276-277`) emits an error to the room
  (`turn_engine.py:2668-2683`), so the limit is a defect to avoid, not a ceiling to rely on
  (Q-3b, AC-10a). `2026-10-12-stale-reply-recheck`, once built, lowers the count further by
  folding a burst into one turn.
- **The teacher must close each round.** With no answer key the agent cannot confirm the
  target character (Q-7); a teacher who is not watching the room leaves the round open at E1.
  The pack's guidance says so.
- **Existing installs are unchanged** (§6). The pack's README-level guidance and the release
  note say to reinstall or edit by hand.
- **Prompt length** stays far under `_MAX_SYSTEM_PROMPT` (`catalogue.py:66`).
- **Rollback**: revert the pack JSON and tests; no data written.

## 10. Acceptance Criteria

- [x] AC-1: the §8 tests fail against the current pack and pass after the change.
- [x] AC-2: the teacher ships with both triggers enabled as in §7.1, the cadence section and
  step tags are gone, and the prompt forbids calling `update_wakeup`.
- [x] AC-3: no CRAT prompt references `instruct` or a non-integer or seconds-based duration.
- [x] AC-4: the teacher prompt states that an empty reply posts nothing, and makes it the
  default outcome of B1 and C1 wakes.
- [x] AC-5: the teacher prompt specifies the three-part turn shape with at most one targeted
  participant per turn, and an individual-level off-topic condition in C1.
- [x] AC-6: step D1 names every required field of `crat-word-puzzle`.
- [x] AC-7: no agent in either CRAT pack is told to hold an answer key; B1 takes clues from the
  teacher's room message, C2 marks uncertain words as uncertain, and E1 asks the teacher to
  confirm the answer.
- [x] AC-8: the design agent's 提示詞規範 carries the integer-minute, no-instruct,
  no-update_wakeup and empty-reply rules, and says the answer key never goes into agent
  settings.
- [x] AC-8a: the teacher prompt tells a lull from other wakes by the time line, sets the hint
  level from its own messages since the last participant message, and both teacher and peer
  reply with nothing on a lull when `read_drafts` shows a participant drafting.
- [x] AC-8b: the analyst ships with `every_n_messages` at `n = 20` and its silence trigger
  unchanged, and its prompt carries the uncoded-participant rule.
- [x] AC-9: the two sibling room packs are checked for F-3, F-4, F-8 and F-10 and each result is
  recorded in §12 as confirmed or cleared.
- [ ] AC-10: manual check on a running stack with four participants: a burst of parallel
  candidates produces one teacher message per wave that mentions every candidate, and eight
  minutes of silence produce the §7.2 sequence. Recorded with the observed timings.
- [ ] AC-10a: the same session, run as a 30-minute lesson with steady discussion, never shows a
  rate-limit error from the teacher; the audit log's `agent.turn_started` count per five-minute
  window is recorded.
- [x] AC-11: backend `pytest -q`, `ruff check .`, `ruff format --check .` and `mypy .` pass.
  (The change is pack JSON plus one test module: `test_agent_example_packs.py` and`n  `test_agent_example_service.py` 319 passed; ruff, format and mypy clean on the test module.)

## 11. SRS Delta

None. This change conforms the pack to the existing §15 behavior.

## 12. Deviation Log

- **AC-9 sibling sweep** (2026-10-12). `character-building-room` and
  `object-reinterpretation-room` were read against F-3, F-4, F-8 and F-10:
  - F-3 **confirmed** in both: the teachers ship `silence_minutes.enabled: false` and switch
    cadence through `update_wakeup` (5 and 4 calls, 8 and 11 `[喚醒策略` tags).
  - F-4 **confirmed** in both teachers: `1.5 分鐘`.
  - F-8 **confirmed** in both: `instruct` appears 9 and 10 times in the teachers, twice in each
    peer.
  - F-10 **confirmed** in both teachers: "可以選擇不回應" with no empty-reply rule.
  - The analysts carry none of the four.
- **D-1**: AC-9's sibling defects are not fixed here, although §6 said a defect identical in shape
  would be. The F-3 fix is not mechanical: each course needs its own silence chain and step
  conditions, which this dossier never analysed, and the user chose a separate dossier once the
  CRAT approach has been tried in class (FU-1).
- **D-2**: the design agent's new rules avoid the words "instruct" and "秒" so that the
  pack-wide checks of §8 hold for the designer too; the rules are stated as "no tool that lets
  the teacher agent direct the peer agent" and "no time shorter than a minute".
- **D-4**: from the security audit (three MEDIUM, all introduced by the rewrite, all fixed):
  the teacher accepts clues, answer confirmations and the end of the last round only from the
  room creator the platform names, never from a look-alike name; the second-level hints no
  longer point at the target character's radical or stroke count, which the teacher cannot know
  without an answer key, and offer an answer-free angle or ask the teacher for a hint instead
  (§7.2's "radical or stroke-count hint" row is superseded); and the analyst treats chat
  messages as data rather than instructions, with "never write names, never invent codes"
  moved into its boundaries. The teacher also declines requests to post its working notes
  (hardening).
- **D-3**: the analyst prompt also tells it to reply with nothing when nothing changed since its
  last note, because the new `n = 20` wake would otherwise ask it for a note every twenty
  messages regardless of content. Not in §7.4a; same intent as Q-9.

## 13. Follow-ups

- **FU-1**: rewrite `character-building-room` and `object-reinterpretation-room` the same way
  (D-1 lists what was confirmed), each with its own silence chain; to be opened as its own
  dossier after the CRAT pack has been observed in class (AC-10).
- **FU-3**: `update_wakeup` is registered for every agent (`tool_registry.py:819`); only the
  prompt keeps the shared teacher from calling it. A pack-level way to leave the tool out would
  make Q-3a structural. From the security audit (hardening).
- **FU-2**: once `2026-10-12-wakeup-human-count-and-trigger-toggle` lands, revisit §7.1 and
  §7.2: human-only counting makes `n` mean student messages, a runtime toggle lets the teacher
  disable silence wakes in steps that do not need them, and composing-as-activity makes the
  `read_drafts` check of Q-8 redundant.
