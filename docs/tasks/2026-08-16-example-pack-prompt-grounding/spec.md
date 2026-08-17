---
type: bugfix
status: implemented
created: 2026-08-16
requirements: [R15.02, R30.27, R30.35]
depends_on: []
---

# The shipped analyst prompt asks for a report on non-submitters that its context cannot ground

## 1. Summary

The shipped AA (分析代理) prompt instructs the agent to report on 參與的分布, explicitly including
**誰還沒提交** - who has not submitted yet. AA's only structured input is the activity block, which
is a hard-capped 30-row, newest-first list of **submission events** with no participant roster and
no way to map a submission's truncated user code to a name in the transcript. In a class larger
than the window, the visible evidence positively suggests that students who submitted early never
submitted at all. Asked who has not submitted, AA can only answer by inventing participation data
about minors - the precise class of harm §8 and AC-10 of the source dossier were written to
prevent, and one that AC-10's text-only assertion cannot catch.

F-12 of `docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`. The code is
correct throughout; the defect is entirely in shipped prompt content.

## 2. Observed vs Expected

**Observed.**

- The instruction:
  `backend/contexts/agents/infrastructure/examples/packs/creative-thinking-room.json:74` contains
  `參與的分布：誰還沒提交、誰反覆嘗試、哪個活動卡住的人最多`.
- The only structured input is the activity block.
  `ActivityContextProvider.query` builds it purely from `list_recent_activity`
  (`backend/contexts/activities/application/activity_context_provider.py:38-55`, the call at
  `:47`), and `_format_row` (`:79-87`) emits **one line per submission**: a truncated user code
  (`u:` plus 8 hex characters), the attempt number, the type key, the outcome, an optional error
  class, and an optional digest. There is no roster, no expected-participant list, and no session
  list.
- The window is hard-capped at 30: `DEFAULT_ACTIVITY_WINDOW = 30`
  (`activity_context_provider.py:31`), and `TurnEngine._activity_context`
  (`backend/contexts/agents/application/runtime/turn_engine.py:3892-3898`) passes **no** limit
  override and is the sole call site (`:2458`). Observers and normal agents receive an identical
  block.
- The query does not dedupe by subject:
  `backend/contexts/activities/infrastructure/repositories/submission_repo.py:259-300` orders
  `created_at DESC, id DESC` and applies `.limit(limit)` (`:283-284`), filtered by
  `chatroom_id` only. Every retry consumes another row.
- Retries are expected, not exceptional: `mandala-9grid` ships `min_filled: 4` over nine declared
  properties, so a student who fills three cells is rejected `too_few_filled`
  (`backend/app/plugins/activity_validators.py:116`) and submits again.
- **No roster reaches any agent.** `_SystemBlocks.build` (`turn_engine.py:814-849`) enumerates
  every block: `base_system`, `observer_note`, `memory`, `summaries`, `knowledge`, `skills`,
  `activity`, `staged`, `notify`, `participant_note`. `_PARTICIPANT_LABEL_NOTE` (`:328-332`) is a
  style instruction, not a list, and `_participant_labels` (`:3239-3261`) resolves names only for
  `sender_id`s **already present in the transcript** - it enumerates speakers, never members.
- **Cross-referencing is impossible, which makes this worse rather than better.** The transcript
  labels are display names; the activity block uses truncated UUIDs. No block maps one to the
  other. The prompt is honest about the code format (`參與者以被截短的代號呈現，不是姓名`) but
  that honesty is what closes the only workaround.
- **The prompt's existing hedges do not cover this.** Reading the whole `system_prompt` string,
  the nearest are `你會收到這間討論室最近的結構化活動事件` ("recent") and
  `沒有證據就不要寫。你看到的是提交事件與討論發言，不是學生的內在狀態`. Neither states that the
  window is capped, nor that absence of a row is not absence of a submission.

**Concrete arithmetic.** A 28-student class running two activity types per unit produces roughly
56 submission rows against a 30-row window, before any retry. By the second activity, the first
activity's submissions are entirely gone from AA's context.

**Expected.** A shipped prompt asks only for what the runtime can supply, and states the limits of
what it is looking at so the agent declines rather than infers.

**Intent sources.** §8 of
`docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:502-505` ("A prompt that implies
otherwise manufactures assessment data about minors") and AC-10 (`:642-644`). [R30.35] makes pack
content repository data the platform ships and is therefore answerable for. [R30.27] bounds what
is actually measured.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Is this a code defect or a prompt defect? | **Prompt.** The code is correct. | Not a user question. The 30-row bound is deliberate, documented design: `docs/tasks/2026-07-13-activities-observer-context/spec.md:106-110` states "the always-on block stays bounded for token safety" and names the escape hatch ("the AA can pull more via a scoring tool"). Nothing in the runtime contradicts documented intent. What contradicts intent is a shipped prompt asking for a report the block structurally cannot ground. |
| Q-2 | Is this already covered by the source dossier's OQ-1, which concedes prompt behaviour is untestable? | **No - different claim.** | OQ-1 (`spec.md:734-737`) concedes that whether an agent *obeys* AC-9/10/11 cannot be asserted statically. This is not about obedience: an agent that obeys this prompt perfectly still fabricates, because the prompt asks for something the context does not contain. That is a defect in the prompt's text, which **is** statically assertable - which is why AC-1 below can be a real test. |
| Q-3 | Fix by amending the prompt, by adding a roster to the context, or by raising the window? | **Amend the prompt.** | A roster block is a genuine platform feature - new data in every agent's context, a new privacy surface (a list of minors' names sent to a provider), and a decision about who is "expected" to submit that the platform does not currently model. Raising the window fixes nothing: with 30 or 300 rows there is still no roster, so 誰還沒提交 remains ungroundable, and a larger always-on block costs tokens on every turn for every agent. Both are recorded as follow-ups. |
| Q-4 | Should the activity block itself announce when it has been truncated? | **Not here.** Recorded as FU-1 with a recommendation. | It is the right general fix and would protect every agent rather than this one prompt, but it changes the prompt input of **every activity type in every deployment** - the same reason FU-3 and FU-13 of the source dossier deferred the digest format. Making that change inside a prompt-content bugfix would be scope creep into a platform-wide behaviour change. |
| Q-5 | Does any unfinished dossier conflict? | **No - `depends_on: []`.** | `docs/tasks/BOARD.md` lists `2026-07-07-graphrag-two-axis-redesign` and `2026-07-19-large-artifacts-silently-dropped`; neither touches the packs. `2026-08-16-agent-pack-install-report-fidelity` edits the agents **dialog** and the install report, not the pack JSON. `2026-08-16-example-docs-corrections` edits `docs/examples/creative-thinking-course.md`, which this dossier also touches (§7.3) but in a different section; rebase rather than sequence. |

## 4. Reproduction

Not reproducible as a code failure; it is a property of shipped text plus a bounded context. The
deterministic part is fully assertable:

1. Read `backend/contexts/agents/infrastructure/examples/packs/creative-thinking-room.json:74`
   and confirm the prompt asks for 誰還沒提交.
2. Read `activity_context_provider.py:31`, `:79-87` and `turn_engine.py:814-849` and confirm no
   roster is delivered and the block is capped at 30 rows.
3. Construct the arithmetic: 28 students, two activity types, `min_filled: 4` over nine
   properties. More than 30 submission events occur; the block holds the newest 30.

**The behavioural half is a manual check**, and belongs in the classroom dry-run the source
dossier's OQ-1 already requires before any use with students: run a class of more than 30
submissions, trigger AA, and read its observation for a claim about who did not submit.

## 5. Root Cause Analysis

1. **Root cause.** The AA prompt was written against an intended capability rather than against
   the block's actual shape. `creative-thinking-room.json:74` asks for three things -
   誰還沒提交, 誰反覆嘗試, 哪個活動卡住的人最多 - of which only the second and third are derivable
   from a submission-event list. The first requires a set difference against a roster that no
   block carries. Removing or re-grounding that clause prevents the symptom.
2. **The truncation makes a soft failure into a confident wrong answer.** Without a cap, an agent
   asked to infer non-submission from an event list would at worst be guessing about students who
   genuinely appear nowhere. With a newest-first cap, early submitters are *actively absent* from
   the evidence, so the most natural inference from the visible data is exactly the false one.
3. **Retries accelerate it.** `min_filled: 4` over nine properties combined with no dedup by
   subject (`submission_repo.py:283-284`) means a struggling student consumes multiple rows,
   evicting others faster.

**Why AC-10 did not catch it.** AC-10 (`spec.md:642-644`) asserts three things about the prompt's
*text*: that it names the three creativity dimensions it will not score, that it says 流暢力, and
that it contains the literal `filled_count`. Those are pinned by
`backend/tests/unit/test_agent_example_packs.py:164-172` and all pass. The criterion was written
to stop the prompt **over-claiming a scoring capability**, and it does that correctly. It was not
written to check that everything the prompt asks for is derivable from the context, which is a
different and harder property - see FU-3.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** The shipped AA prompt in any class large enough to exceed the window, which for
a two-activity unit is roughly fifteen students. The output is a teacher-facing observation
(`ChatroomAgentRole.OBSERVER` routes AA's output to an Observation the room creator releases
deliberately), so the fabricated claim reaches a teacher rather than the class - which limits the
immediate harm and makes it more likely to be believed, since it arrives as a considered analysis.

Bounded by the fact that AA's observations are released deliberately by the room creator, and by
OQ-1's standing instruction that no classroom use with students happens before a dry-run.

**Sibling suspects** - every other claim in the four shipped prompts, checked against what the
runtime actually delivers:

| Claim | Verdict |
|---|---|
| AA: `誰反覆嘗試` (who retried) | **cleared** - the attempt number is on every row (`activity_context_provider.py:79-87`), so this is directly derivable within the window. |
| AA: `哪個活動卡住的人最多` (which activity stalls most) | **cleared** - the type key and outcome are both on the row. Window-bounded like everything else, but derivable. |
| AA: `這個平台目前唯一自動計分的指標是「填答完整度」（filled_count）` | **cleared, and it was worth checking.** The audit raised whether AA can cite the number, since `sub_scores` never reaches the row model (`backend/contexts/activities/domain/models.py:237-251`). Refuted: the sentence sits under `# 你不評什麼` and asks AA to state a **fact about the platform**, never a per-student number. The prompt separately and accurately enumerates its inputs under `# 你看得到什麼`. |
| AA: no claim to score 變通力 / 獨創力 / 精進力 | **cleared** - asserted by `test_agent_example_packs.py:164-172`. |
| TA / SA: the unit-4 disclosure boundary | **cleared** - asserted over the pack files by the same test module (AC-11). |
| All four: never quote a participant's submission into the room | **cleared** - asserted (AC-9), and the single most important line per §8 of the source dossier. |

So the AA participation clause is the only place a shipped prompt asks for something the context
cannot supply.

## 7. Fix Design

**7.1 Re-ground the participation clause.** `creative-thinking-room.json:74` must stop asking who
has **not** submitted. Replace that element with what the block can actually support - the
distribution of what *is* visible - and keep 誰反覆嘗試 and 哪個活動卡住的人最多, which are
derivable.

**7.2 State the window's limits inside the prompt.** The existing hedges cover inference about
inner states; they do not cover inference from an incomplete list. The prompt must say, in
substance: the block holds only the most recent events, so a participant's absence from it is not
evidence they did not submit, and any statement about who is missing must be declined and left to
the teacher, who has the roster. This is the sentence that generalises - it protects against the
next well-meant question about coverage, not only against this one.

**7.3 Documentation.** `docs/examples/creative-thinking-course.md`'s description of what AA does
must match the amended prompt, and should state the window bound explicitly so a teacher reading
the walkthrough knows what AA is and is not looking at. The document already describes AA's role;
this adds the limit.

**No code change.** The context provider, the repository, the turn engine and the pack loader are
all correct and untouched (Q-1, Q-3).

**Why this does not mask the symptom.** The symptom is a fabricated claim about minors; the cause
is a prompt asking for a claim the evidence cannot support. Removing the ask removes the cause.
Raising the window or adding hedging alone would leave the ask in place - and an agent asked
directly for 誰還沒提交, with a hedge elsewhere in the prompt, is exactly the situation where
hedges lose.

**Data repair.** None. No observation already produced is corrected by this; if a deployment has
been running AA with real classes, the teacher-facing observations it produced may contain the
fabricated claim, which is why §9 flags telling the operator rather than only shipping the fix.

## 8. Regression Test Plan

The assertions run over the **parsed shipped packs**, not over hand-built fixtures - the idiom
`backend/tests/unit/test_agent_example_packs.py` already uses for AC-9, AC-10 and AC-11, and the
reason those constraints hold for the content that actually ships.

**8.1 The failing test.** In `test_agent_example_packs.py`: assert the AA prompt does **not**
instruct the agent to report who has not submitted. Fails today - `creative-thinking-room.json:74`
contains that instruction verbatim.

Asserting on absence needs care: a bare substring check on 還沒提交 is brittle against a rewording
that reintroduces the same ask in different words. Pair it with 8.2, which asserts the positive
statement, so a future edit that drops the caveat fails even if it avoids the exact phrase.

**8.2 The positive assertion.** Assert the AA prompt states that the activity block is a recent
window and that absence from it is not evidence of non-submission. This is the durable half: it
pins the property that makes the removal safe rather than the exact wording of the removal.

**8.3 Must stay green unmodified.** `test_agent_example_packs.py:164-172` (AC-10's three
assertions), the AC-9 no-quoting assertion, and the AC-11 unit-4 boundary assertions. This change
touches one clause of one prompt and must not weaken any of them - in particular it must not
remove the `filled_count` sentence, which is the subject of a separate cleared suspect in §6.

**8.4 The cross-reference tripwire.** `binds_activity_types` and `for_course` are checked by the
same module (AC-6); the prompt edit does not touch them, so they must pass unmodified as a check
that the edit stayed within the `system_prompt` string.

**8.5 Behavioural verification is manual and belongs to OQ-1.** Stated here rather than implied:
whether AA actually declines to speculate is not assertable statically, and this dossier does not
claim otherwise. §4's manual check goes in the dry-run checklist.

## 9. Risks and Rollback

- **The fix does not reach an installed deployment.** Pack agents are created copy-on-import
  (§5.1 of the source dossier), so a project that already installed `creative-thinking-room` holds
  agents carrying the **old** prompt, and re-installing is idempotent by agent name and will not
  update them (`spec.md:265-270`). Anyone who has installed the pack must edit AA's system prompt
  by hand or delete and re-install that agent. **This must be stated in the docs**, not
  discovered - it is the same trap D-16 recorded for the course schemas, in a different
  subsystem.
- **Prompt quality is not testable.** §8.5. The text assertions are real; obedience is not.
- **Rewording risk.** The clause sits in a long Chinese prompt; an edit that changes more than
  intended would weaken AC-9/AC-10/AC-11. §8.3 exists to catch that, and the diff should be read
  clause by clause.
- **Already-produced observations.** A deployment that has run AA with real classes may hold
  teacher-facing observations containing the fabricated claim. The fix does not retract them; the
  operator note in §7.3 should say so.
- **Rollback**: `git revert`. No code, no schema, no API.

## 10. Acceptance Criteria

- [x] AC-1: The test from §8.1 fails before the fix and passes after: the AA prompt no longer
  instructs the agent to report who has not submitted. Verified failing first.
- [x] AC-2: The AA prompt states that the activity block is a bounded recent window and that a
  participant's absence from it is not evidence they did not submit, asserted over the parsed
  shipped pack (§8.2). Three anchors: 有限視窗, 不代表那個人沒有提交, and 名冊 for the hand-back.
- [x] AC-3: The AA prompt still asks only for what the block supports - retry counts and
  per-activity difficulty remain, since both are derivable from the row shape. Pinned by its own
  test, which passed before the edit and still passes, so it is a guard rather than a claim.
- [x] AC-4: AC-9, AC-10 and AC-11 of the source dossier still hold: no quoting of submission text,
  no claim to score 變通力 / 獨創力 / 精進力, the `filled_count` sentence intact, and the unit-4
  boundary clause intact in TA and SA. All pass unmodified; the diff is confined to three clauses
  of the one `system_prompt` string.
- [x] AC-5: `for_course` and every `binds_activity_types` key still resolve (§8.4).
- [x] AC-6: `docs/examples/creative-thinking-course.md` describes AA's amended scope, states the
  window bound, and tells an operator who already installed the pack that existing AA agents keep
  the old prompt and must be edited or re-created.
- [x] AC-7: `ruff check .`, `ruff format --check .` and `mypy .` are green. `pytest -q` is green
  for the **unit tier only** (6889 passed, 6 skipped) - see D-2 for why the other tiers did not
  run. No frontend gates apply.
- [x] AC-8: The manual check from §4 is added to the pre-deployment dry-run checklist that OQ-1
  of the source dossier requires - see D-3, which records that the checklist had to be created.

## 11. SRS Delta

**None.** [R30.35] already makes pack content repository data the platform ships; [R30.27] already
bounds what is measured; [R15.02] is cited only because the window's existence is what makes the
prompt ungroundable. This corrects shipped content against requirements that already exist.

## 12. Deviation Log

- **D-1 — the prompt describes the window's size in words, not as a number.** §7.2 asked the
  prompt to say the block holds only the most recent events; it now says 數十筆 rather than
  naming 30. A literal 30 in the prompt would be a second copy of `DEFAULT_ACTIVITY_WINDOW`
  (`activity_context_provider.py:31`) with nothing to keep the two in step, so changing the
  constant would silently make the shipped prompt lie to the agent about its own input. The
  number is stated in the walkthrough instead, next to the constant's name and file, where a
  reader can check it. The property the fix depends on - bounded, newest-first, absence is not
  evidence - is stated exactly and is what the tests pin.
- **D-2 — `pytest -q` was not run to completion.** The `integration`, `db` and `wiring` tiers
  need a live PostgreSQL and Docker was unavailable on the implementing host; the full run
  reached 4% in 25 minutes, erroring at connect, and was stopped. The `unit` tier - which holds
  every test this dossier touches, and the entire pack-content suite - is green at 6889 passed,
  6 skipped. Nothing in this diff is reachable from the other tiers: it is one JSON string, one
  unit test module, and a Markdown file.
- **D-3 — AC-8's dry-run checklist did not exist and was created rather than extended.** OQ-1
  of the source dossier requires a dry-run, and `docs/examples/creative-thinking-course.md`
  stated that requirement in one sentence under Privacy and ethics, but no checklist existed
  anywhere to add an item to (searched `docs/` for both spellings). Adding a single free-floating
  item would have left the other three prompt constraints - the ones the same paragraph says a
  test cannot establish - still uncovered, so the section now carries all five checks. The new
  one is the third: run past the window, then ask AA who has not submitted.
- **D-4 — no behavioural verification (gate 4 of the Definition of Done).** Docker was
  unavailable, so no room was run and AA was never asked the question the fix exists to make it
  decline. This dossier states plainly in §8.5 that the behavioural half is not statically
  assertable, so the gap is the whole point of AC-8's checklist item rather than an oversight -
  but it does mean the prompt's effect is reasoned, not observed. The dry-run OQ-1 already
  mandates is what closes it.

## 13. Follow-ups

- **FU-1 (recommended)**: **The activity block should announce when it has been truncated.**
  `ActivityContextProvider.query` (`activity_context_provider.py:38-55`) knows whether it hit
  `DEFAULT_ACTIVITY_WINDOW`; emitting "showing the most recent N of M" when it does would let
  **every** agent - not only this one prompt - know that absence is not evidence. Deferred here
  only because it changes the prompt input of every activity type in every deployment (Q-4), the
  same constraint that deferred the digest format. It should be resolved together with FU-3 and
  FU-13 of the source dossier, which are blocked on the same consideration.
- **FU-2**: A **participant roster block** would make 誰還沒提交 answerable. It is a real feature
  with a real privacy surface - a list of minors' names entering an LLM context - and it needs a
  decision about who counts as "expected" to submit, which the platform does not model today
  (there is no per-activation participant set). Worth specifying properly if teachers ask for
  coverage reporting.
- **FU-3**: **No check exists that a shipped prompt asks only for what the runtime supplies.**
  AC-9/10/11 assert that prompts do not *over-claim*; nothing asserts they do not *over-ask*.
  This defect is the difference between those two properties. A reviewer checklist item - "for
  each thing this prompt asks the agent to produce, name the block that supplies the evidence" -
  would have caught it at authoring time, and is cheaper than any automated check.
- **FU-4**: The transcript labels participants by display name while the activity block uses
  truncated UUIDs, with no mapping in any block (§2). That is a deliberate privacy choice, but it
  means no agent can connect a submission to a speaker, which quietly bounds what any
  activity-aware prompt can ask. Worth stating in the pack-authoring guidance rather than leaving
  each prompt author to rediscover.
- **FU-5**: **Nothing ties the prompt's description of the window to the window.** Per D-1 the
  prompt says 數十筆 rather than a number, which is true for `DEFAULT_ACTIVITY_WINDOW = 30` and
  would still be true at 20 or 80 - but not at 5, and not at 500, where the caveat would either
  overstate or understate what AA is missing. FU-1's truncation marker would close this properly
  by making the block state its own bounds at runtime, which is the reason to prefer it over any
  test that pins prose against a constant.
