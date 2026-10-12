---
type: bugfix
status: draft
created: 2026-10-12
requirements: [R15.01, R15.02, R15.03, R15.06, R15.07]
depends_on: []
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
F-4, F-8, F-10, F-11, F-12 and F-13 (the prompt half of each).

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
- **Expected** — the pack's own stated intent: the teacher guides rather than lectures, speaks
  at the right moment rather than continuously, responds to group state rather than to single
  messages, stays aware of quiet students, and moves the group through steps A to E with stall
  handling at each step. Platform behavior is the one specified by [R15.01] (count all room
  messages), [R15.02] (silence trigger), [R15.03] (autostop rounds), [R15.06] (runtime edits
  limited to `n` and `t_minutes`) and [R15.07] (integer bounds).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Split the audit's work into one dossier or several? | Three: this pack fix, `2026-10-12-stale-reply-recheck`, and `2026-10-12-wakeup-human-count-and-trigger-toggle`. | User's choice. This dossier must work on today's platform so it can be tested in class before either platform change lands. |
| Q-2 | Should this depend on `2026-07-19-large-artifacts-silently-dropped` (in progress, edits `turn_engine.py`)? | No. | This dossier edits pack JSON and tests only. |
| Q-3 | How does the teacher handle silence when `update_wakeup` cannot toggle triggers? | Ship both triggers enabled. `every_n_messages` stays at `n = 1`; `silence_minutes` is enabled with `t_minutes = 1` and `autostop_rounds = 2`. Stage changes adjust only `n`. | The only configuration under which every silence branch is reachable on today's platform ([R15.06]). See §7.2 for the timing chain this produces with the peer agent. |
| Q-4 | How does the prompt keep the teacher from replying to every message with `n = 1`? | State explicitly that an empty reply posts nothing, and make "output nothing" the default outcome of a wake in steps B1 and C1 unless a listed condition holds. | `turn_engine.py:3140-3207` already treats an empty reply as a skip. |
| Q-5 | What replaces the instruct-based escalation? | Remove `instruct` from both prompts. The peer agent's own silence timer takes over the "speak first" move (§7.2). | No agent-facing instruct tool exists (F-8); the timing chain gives the same escalation order without one. |
| Q-6 | How does the teacher follow up individuals without flooding? | One message per turn with a fixed shape: a summary of group state, at most one targeted follow-up of one person, then one question that moves the group forward. Rotate the person across turns. | Satisfies both "no per-message replies" and "do not pressure by name" while covering the reported symptom. |
| Q-7 | Where does the answer key go? | In the teacher's own system prompt, in a marked section the teacher edits per lesson; the design agent's deliverable says to paste it there. | The only private channel today. A dedicated channel is the audit's FU-7. |

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

- **Blast radius** — new installs of `crat-word-puzzle-room` only. Installing is idempotent by
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
| `every_n_messages` | `{"enabled": true, "n": 1}` |
| `silence_minutes` | `{"enabled": true, "t_minutes": 1, "autostop_rounds": 2}` |

The prompt's per-step cadence table is rewritten to change only `n`: `n = 1` in guidance steps
(A1, A2, C2, D1, E1, E2) and B1, `n = 3` in C1. The prompt states in one sentence that `n`
counts every room message including its own and the peer agent's ([R15.01]), so `n = 3` means
about two student messages.

### 7.2 Silence escalation without `instruct`

Every room message re-arms every bound agent's silence timer (`wakeup_service.py:111`), a firing
re-arms it again (`app/workers/tasks/orchestration.py:303-305`), and only a completed reply
counts toward autostop (`orchestration.py:181-183`), which resets on the next user message
(`wakeup_service.py:113-114`). With the teacher at `t = 1, autostop = 2` and the peer at its
existing `t = 2, autostop = 2` (`crat-word-puzzle-room.json:33-41`), a silent group gets:

| Elapsed silence | Who wakes | Prompt instruction |
|---|---|---|
| about 1 min | teacher | first-level hint (start from any clue) |
| about 2 min | teacher | second-level hint (radical or stroke-count hint) |
| about 4 min | peer | shares its own association path, not the answer |
| next user message | all timers and autostop reset | step resumes |

The sweep runs every 30 seconds (`orchestration.py:251`), so each time is accurate to about
30 seconds. The prompt describes stalls by "first, second time you are woken by silence in this
step", never by a duration, because the model has no clock (audit F-5). An empty reply on a
silence wake does not count toward autostop, so the teacher may decline a silence wake without
consuming an escalation level.

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
- **Integer minutes** (F-4): remove every fractional or seconds-based duration; the cadence
  table uses only `n`.
- **instruct** (F-8): delete the A2A 指令規則 section and every "if you have instruct" branch.
- **D1** (F-12): name all required fields, including 推理歷程.
- **Answer key** (F-13, Q-7): add a marked 本課題組（教師填寫） section at the end of the prompt
  with an empty template (round, three clues, target character, three words). B1 reads clues
  from it; when it is empty the teacher tells the room the teacher must prepare the set, as it
  does today.
- **Group-state scope**: no change to the quoting, draft and activity-control sections, which
  the audit found consistent with the platform.

### 7.4 Peer prompt

Delete the A2A 規則 section and the "when instructed by TA" strategy. Keep its silence-driven
role; add that it speaks at most once per silence and replies with nothing when the room is
already active.

### 7.5 Design agent

In `crat-word-puzzle-design.json`, add to 提示詞規範 that drafted prompts must use integer minutes,
must not reference `instruct`, and must state the empty-reply rule; and that the answer key goes
in the teacher's 本課題組 section.

## 8. Regression Test Plan

All in `backend/tests/unit/test_agent_example_packs.py`, written first and failing against the
current pack:

- `test_crat_teacher_ships_both_triggers` asserts §7.1's values.
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

## 9. Risks and Rollback

- **Silence wakes cost model calls.** With `t = 1` the teacher can wake every minute of silence;
  autostop bounds completed replies to two per silence, but empty replies do not count, so a
  model that keeps declining keeps being woken. Accepted: one call per minute per room, and it
  ends when a student speaks.
- **Existing installs are unchanged** (§6). The pack's README-level guidance and the release
  note say to reinstall or edit by hand.
- **Prompt length** stays far under `_MAX_SYSTEM_PROMPT` (`catalogue.py:66`).
- **Rollback**: revert the pack JSON and tests; no data written.

## 10. Acceptance Criteria

- [ ] AC-1: the §8 tests fail against the current pack and pass after the change.
- [ ] AC-2: the teacher ships with both triggers enabled as in §7.1, and the prompt's cadence
  table changes only `n`.
- [ ] AC-3: no CRAT prompt references `instruct` or a non-integer or seconds-based duration.
- [ ] AC-4: the teacher prompt states that an empty reply posts nothing, and makes it the
  default outcome of B1 and C1 wakes.
- [ ] AC-5: the teacher prompt specifies the three-part turn shape with at most one targeted
  participant per turn, and an individual-level off-topic condition in C1.
- [ ] AC-6: step D1 names every required field of `crat-word-puzzle`.
- [ ] AC-7: the teacher prompt carries an empty 本課題組 section and B1 reads clues from it.
- [ ] AC-8: the design agent's 提示詞規範 carries the integer-minute, no-instruct, empty-reply and
  answer-key rules.
- [ ] AC-9: the two sibling room packs are checked for F-3, F-4, F-8 and F-10 and each result is
  recorded in §12 as confirmed or cleared.
- [ ] AC-10: manual check on a running stack with four participants: a burst of parallel
  candidates produces one teacher message per wave that mentions every candidate, and three
  minutes of silence produce the §7.2 sequence. Recorded with the observed timings.
- [ ] AC-11: backend `pytest -q`, `ruff check .`, `ruff format --check .` and `mypy .` pass.

## 11. SRS Delta

None. This change conforms the pack to the existing §15 behavior.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — fix the sibling room packs if AC-9 finds defects that differ in shape from the CRAT
  ones.
- **FU-2** — once `2026-10-12-wakeup-human-count-and-trigger-toggle` lands, revisit §7.1 and
  §7.2: human-only counting makes `n` mean student messages, and a runtime toggle lets the
  teacher disable silence wakes in steps that do not need them.
