---
type: feature
status: draft
created: 2026-08-24
requirements: [R30.28, R30.29, R30.35]
depends_on: []
---

# The example agents may quote unit 2 answers, and still may not quote unit 4

## 1. Summary

All three room-facing agents in the `creative-thinking-room` pack are currently forbidden
from quoting, paraphrasing or repeating any participant's submission, in either unit. That
rule was written to protect a privacy decision (`echo_includes_content: false`), but it also
prevents the thing the course is for: a facilitator agent that can build on what a student
actually wrote. This task **splits the rule by activity type**. Unit 2's answers
(`mandala-9grid`, `time-traveler-next-steps`) become quotable in response to the room, but
never volunteered. Unit 4's answers (`emotion-desk-three-emotions`, `six-hats-emotion-desk`)
stay unquotable, unchanged.

Example content only: prompts, one test, and the guide. **No SRS change and no platform
code** — the prohibition never existed as a requirement (§4.1).

## 2. Goals and Non-goals

**Goals**

- TA, SA and AA may quote, paraphrase and build on a unit 2 submission.
- No agent volunteers a submission's content unprompted; quoting is a response, not a
  broadcast.
- The unit 4 prohibition survives verbatim, and is now stated as a per-type rule the model
  can actually evaluate against its own context (§5.2).
- Every agent still admits it can see the content when asked — the rule the packs learned the
  hard way (`test_agent_example_packs.py:215-222`) is untouched.
- The example guide states the consequence plainly: with quoting allowed for unit 2,
  `echo_includes_content: false` no longer means the class cannot hear an answer.

**Non-goals**

- **No platform change.** No `ActivityType` field, no policy, no runtime gate. The relaxation
  is a prompt change, and a prompt is not an enforcement boundary — §8 says so rather than
  implying otherwise.
- **`echo_includes_content` stays `false` on all four types.** The system-stamped room echo
  continues to carry no answer text; only the agents' own speech changes.
- **`expose_payload_to_agent` is unchanged**, as is the platform governance policy
  ([R30.29]). What agents *see* is not what this task touches.
- **AA's code-not-name rule is untouched.** Quoting an answer and naming its author are
  different acts; only the first is relaxed.
- **No change to the unit 4 safety boundary** (`不要追問` / `誘導` / `諮商`).
- **No change to the DA pack's inability to write back into an agent's configuration.**

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How far does the relaxation go? | Unit 2 relaxed, unit 4 unchanged. | Unit 2 is imagining one's life at 30 — collaborative material, and the discussion is better when an agent can refer to what someone wrote. Unit 4 collects "一件最近或曾經讓自己困擾的事" (`creative-thinking.json:160-166`) from 13-year-olds; an agent repeating that into a class-visible channel is the harm the rule was written for, and "they are learning collaboratively" does not change it. |
| Q-2 | Does "may quote" mean "may volunteer"? | No. Quoting is a response to the room; no agent raises a submission's content unprompted. | The user's framing: the agents need not hold the line, but need not push either. It also keeps the failure mode bounded — a student who says nothing is not narrated to the class. |
| Q-3 | How does a model know which unit it is in? | The prompts name the four **type keys**, not just "unit 2" and "unit 4". | The activity context block puts `type_key` on every row (`activity_context_provider.py:207`), so a rule keyed on it is one the model can evaluate against what it actually holds. "單元二" appears nowhere in its structured input. |
| Q-4 | Does AA relax too? | Yes for unit 2, and its notes go only to the teacher anyway. Its code-not-name rule is untouched. | Splitting AA from TA/SA was considered and rejected: AA's output has the *smallest* audience of the three, so a stricter rule for it is the hardest of the three to justify. |
| Q-5 | Does this need an SRS change? | No. | §4.1: no `[Rxx.yy]` states the prohibition. [R30.28] and [R30.35] both say example content is repository data, not platform behaviour. |
| Q-6 | Does anything depend on this? | `2026-08-24-agent-readable-live-drafts` gains it as a **logical** prerequisite. | That dossier's AC-16 writes the draft rule alongside a submission rule it describes as absolute. After this task that description is false for unit 2, and the draft rule has to be written against the new baseline — drafts stay unquotable in *both* units, which is a sharper and more teachable distinction than the flat rule it replaces. |

## 4. Current State

### 4.1 The prohibition is prompt-level, and only prompt-level

- **No requirement states it.** §30 defines the activity platform across [R30.01]-[R30.38]
  and never constrains what an agent may repeat. [R30.28] and [R30.35] state the opposite
  posture: example content is repository data, and "the platform operates normally when the
  catalogue is absent".
- **The three prompts state it**, each with its own wording and its own reason
  (`creative-thinking-room.json:27` TA, `:52` SA, `:77` AA). All three tie it to the same
  fact: the message box is class-visible and the room deliberately does not echo answers.
- **One test asserts it**, by substring: `引述` and `轉述` must appear in every shipped
  prompt (`test_agent_example_packs.py:205-212`). Its docstring already concedes the limit —
  "asserted by substring rather than by meaning... it catches a constraint deleted or lost in
  an edit, not a prompt that states it and then undermines it three lines later".
- **DA carries it one hop further.** The design agent must require all three constraints in
  the TA/SA prompts it drafts, asserted as `三條限制` + `缺一不算完成`
  (`test_agent_example_packs.py:246-251`).
- **The guide states it** as constraint #1 of four
  (`docs/examples/creative-thinking-course.md:181-195`).

### 4.2 What the prohibition is protecting, and what it is not

All four types set `echo_includes_content: false` and `expose_payload_to_agent: true`
(`creative-thinking.json`), so the room transcript withholds answer text while agents read a
digest of it. The prompt rule is what kept the agents from reversing that decision for the
whole class.

Two things it never protected, and still does not:

- The **teacher** already reads everything through the activity panel and AA's notes.
- A **participant** can always paste their own answer into the room themselves.

### 4.3 The rule that must not be lost in this edit

`test_agent_example_packs.py:215-222` records a real failure: stating only the output ban
produced agents that answered "can you see what I wrote?" with "I will not read it out" —
true, non-responsive, and heard as "no". Every prompt therefore also asserts what it *can*
see. Relaxing the output ban must not disturb that; if anything it makes the honest answer
easier.

## 5. Design

### Options considered

**Option A — drop the rule entirely.** One sentence removed from three prompts. Rejected on
Q-1: unit 4's content is the reason the rule exists.

**Option B — relax by unit, expressed in prose ("單元二可以，單元四不可以").** Rejected on
Q-3: the model's structured input carries type keys, not unit names, so a prose rule leaves it
to infer which unit a row belongs to.

**Option C — relax by type key.** Chosen.

### Decision

Each room-facing prompt states a two-column rule naming the four type keys, plus the
volunteer bound:

- `mandala-9grid`, `time-traveler-next-steps` — may be quoted, paraphrased and built on, **in
  response**. Never raised unprompted, never read out as a survey of the class.
- `emotion-desk-three-emotions`, `six-hats-emotion-desk` — never quoted, paraphrased or
  repeated, to anyone, including the person who wrote it and including in AA's teacher-only
  notes. The existing reason and the existing alternatives (name a tendency, ask a question
  the answer raises, invite the author to speak) stay as they are.

What was consciously given up: a single rule is easier for a model to follow than a
conditional one, and this makes the prompt's most safety-critical sentence conditional. The
mitigation is that the condition is a literal string the model can match against its own
context rows, not a judgement about topic sensitivity. §8 states what remains.

### 5.1 The volunteer bound

"In response" is defined in the prompt by example rather than by adjective: an agent may
quote when a participant or the teacher asks about an answer, or when the discussion is
already on that answer. It does not open a turn with someone's answer, does not enumerate
what several people wrote, and does not use a quote to restart a stalled discussion. SA's
prompt keeps its existing instruction to offer its own version instead.

### 5.2 The default for a type in neither column

Both columns are literal enumerations, and §8 makes the literal match the mitigation — but an
enumeration with no default says nothing about a type it does not name, and the safety-critical
sentence then falls silent exactly where nobody is looking. Two such types are already
foreseeable: `2026-08-24-group-activity-submissions` adds `six-hats-shared-case` (its §5.6),
and a Project Owner may register arbitrary types at any time ([R30.23]).

Each prompt therefore closes the enumeration: **a type key in neither column is treated as
unquotable.** One clause, and it fails safe for every type that will ever exist. A later
course that wants a new type quotable adds it to the first column deliberately, which is the
right place for that decision to be visible.

### 5.3 The test

`test_no_agent_may_quote_a_participant_submission` is replaced by two assertions, both at the
same substring tier as the ones they replace (§4.1's honest limit still applies):

- Every room-facing prompt names both unit 4 type keys **and** carries both `引述` and
  `轉述`, so a prompt that drops either the quoting or the paraphrasing half of the unit 4
  prohibition fails. Keeping `轉述` is not optional tidiness: AC-2 requires the prompts to
  forbid quoting, paraphrasing *and* repeating, and a replacement test that checked only
  `不引述` would let the paraphrase clause be deleted from the safety-critical column with the
  suite green — a net loss of coverage against the thing this task is most likely to break.
- Every room-facing prompt carries `不主動`, so a prompt that relaxes quoting without the
  volunteer bound fails.
- Every room-facing prompt carries the default clause of §5.2, so a prompt that states the two
  columns and stops fails.

The existing `test_no_agent_pretends_it_cannot_see_a_submission` (`看得到`) is untouched, as
are the unit 4 boundary test and AA's `代號` rule.

DA's `三條限制` / `缺一不算完成` assertion is retargeted: the count is unchanged, but the
first constraint DA must require in what it drafts is now the split rule, not the flat one.

## 6. Detailed Changes

**Backend — example pack**

- `backend/contexts/agents/infrastructure/examples/packs/creative-thinking-room.json`: TA
  (`:27`), SA (`:52`) and AA (`:77`) system prompts. TA and SA gain the two-column rule in
  their 界線 sections; AA's version additionally keeps its teacher-notes clause for unit 4 and
  its code-not-name rule verbatim.
- `backend/contexts/agents/infrastructure/examples/packs/creative-thinking-design.json`: DA's
  three-constraint requirement restated so a drafted TA/SA prompt carries the split rule.

**Backend — tests**

- `backend/tests/unit/test_agent_example_packs.py:205-212` replaced per §5.2.

**Docs**

- `docs/examples/creative-thinking-course.md:181-195`: constraint #1 rewritten as the split
  rule plus the §5.2 default, keeping constraint #2 (the "must admit it can see" lesson)
  exactly as it stands. A new paragraph states the consequence: for unit 2,
  `echo_includes_content: false` now bounds only the system echo, not what the class can hear,
  because an agent may repeat an answer when asked.

  **The escape hatch is not the teacher's to pull, and the guide must say so.** Turning
  `expose_payload_to_agent` off is the only structural control (§8), but the four example
  types are **platform-scoped** (`creative-thinking-course.md:313-314`), and that field is
  editable only by a platform admin (`admin_activities.py:350-361`, `:421-445`) — the Project
  Owner edit route refuses a platform-scoped target ([R30.31], `activities.py:487-514`). So
  the guide instructs a facilitator to **ask a platform admin**, and names the alternative
  that is genuinely theirs: install a project-scoped copy of the course via
  `python -m smap.examples` ([R30.28]) and edit that. Framing it as a switch the teacher can
  reach would send them to a 404.

**No platform code. No migration. No API change. `gen:api` rerun: no.**

## 7. NFR Checklist

- **i18n** — N/A. Prompts are course content in Traditional Chinese by design; no UI string
  changes.
- **Audit log** — N/A. No new domain event; installing a pack already audits `agent.created`
  ([R30.35]).
- **Tenant isolation** — N/A. No endpoint, no query.
- **Error handling UX** — N/A. No user-facing surface.
- **Performance** — N/A. Prompt length changes by a few hundred characters, well inside the
  pack loader's own oversized-prompt guard (`test_agent_example_packs.py:503`).

## 8. Security Considerations

This changes what an LLM agent is instructed to repeat into a class-visible channel.

- **A prompt is not an enforcement boundary.** It never was; the flat rule was equally
  unenforced. What changes is the direction the instruction points for unit 2, and the fact
  that a model now has to evaluate a condition rather than follow an absolute. A model that
  gets the condition wrong quotes a unit 4 answer into the room. That risk is real, it is
  new, and the only structural control against it is `expose_payload_to_agent`. **That lever
  is a platform admin's, not the facilitator's**, for the shipped platform-scoped types (§6),
  so "the teacher can turn it off" would be a false reassurance. What the facilitator actually
  controls is which units they run and whether they use the shipped types at all.
- **Keying on type keys rather than topic** is the mitigation that makes the condition
  tractable: the model matches a literal string present in its own context rows
  (`activity_context_provider.py:207`), instead of judging whether a piece of text is
  sensitive.
- **Unit 4 is unchanged in every respect** — the prohibition, the no-pressing boundary, and
  the hand-back-to-the-teacher instruction.
- **AA's notes.** Relaxed for unit 2 only. AA's code-not-name rule is untouched, so a quoted
  unit 2 answer in a teacher note is still attributed by code, and the note stays as
  transferable as it was.
- **Existing installs are unaffected**, which cuts both ways: install is idempotent by agent
  name and never rewrites an existing agent
  (`docs/examples/creative-thinking-course.md:376-385`), so a project that installed the pack
  before this change keeps the flat rule until someone edits the prompt or deletes and
  reinstalls the agent. Recorded in the guide rather than left to be discovered.

## 9. Quality Notes

**Existing debt in touched files:**

- The three prompts state the same prohibition three times in three wordings, which is why a
  change like this touches three strings that must stay consistent. Not fixed here — a shared
  prompt fragment mechanism does not exist and inventing one for the example is out of scope
  (FU-1).
- `test_agent_example_packs.py`'s substring tier is a known limit its own docstring records
  (`:196-202`). This task stays at that tier deliberately rather than pretending to assert
  meaning.

**Patterns to follow:**

- `test_agent_example_packs.py:215-222` — the shape of a constraint whose *reason* is recorded
  in the test, so a later reader knows what breaking it costs.
- The existing prompts' own structure: state the rule, state the reason, then state what the
  agent may do instead. The relaxation must keep all three parts, not just delete a clause.

**Reuse inventory:**

- `SHIPPED_AGENTS` / `AGENT_IDS` parametrisation already in the test module.
- The four type keys are already asserted to exist in the shipped course
  (`test_agent_example_packs.py:187`), so the new assertions can name them without a second
  source of truth.

## 10. Risks and Rollback

- **The central risk is a model applying the wrong column.** Mitigated by keying on literal
  type keys, and bounded by `expose_payload_to_agent` for a teacher who needs a guarantee.
  Not mitigated by anything the platform enforces, and §8 says so.
- **Rollback is a revert of three JSON strings, one test and one doc section.** No migration,
  no data, no deployed behaviour.
- **A running class does not pick the change up.** Installed agents are copies; the guide
  states this.
- **The dry run is the only place the interaction is observable.** The guide's dry-run
  checklist (`creative-thinking-course.md:527-544`) currently asserts "No agent quotes or
  paraphrases a submission". That line must be split, not deleted, or the checklist silently
  stops checking unit 4.

## 11. Acceptance Criteria

- [ ] AC-1: TA, SA and AA each state that `mandala-9grid` and `time-traveler-next-steps`
      answers may be quoted, paraphrased and built on.
- [ ] AC-2: TA, SA and AA each state that `emotion-desk-three-emotions` and
      `six-hats-emotion-desk` answers may never be quoted, paraphrased or repeated — for AA,
      including in its teacher-only notes.
- [ ] AC-3: TA, SA and AA each state that a quotable answer is never raised unprompted.
- [ ] AC-4: Every shipped prompt still admits it can see submission content
      (`test_no_agent_pretends_it_cannot_see_a_submission` passes unchanged).
- [ ] AC-5: AA still reports by code and never by name; its creativity-dimension disclaimer
      is unchanged.
- [ ] AC-6: TA and SA still carry the unit 4 boundary (`不要追問`, `誘導`, `諮商`).
- [ ] AC-7: DA still requires three constraints in what it drafts, with the first now the
      split rule.
- [ ] AC-8: `test_agent_example_packs.py` asserts AC-2 and AC-3 over the shipped files, and
      goes red when either the unit 4 prohibition or the volunteer bound is removed from any
      room-facing prompt.
- [ ] AC-9: The guide's constraint #1 states the split rule and the §5.2 default, names
      `expose_payload_to_agent` as the structural guarantee **and says it is a platform
      admin's edit for the shipped platform-scoped types**, names the project-scoped copy as
      the facilitator's own alternative, and states that existing installs keep the old
      prompt.
- [ ] AC-12: Every room-facing prompt states that a type key in neither column is unquotable,
      asserted by `test_agent_example_packs.py` and mutation-probed by deleting the clause.
- [ ] AC-10: The dry-run checklist's quoting line is split into a unit 2 line and a unit 4
      line rather than removed.
- [ ] AC-11: The full Definition of Done passes — `pytest -q`, `ruff`, `mypy`.

## 12. Test Plan

- **AC-1 to AC-3, AC-8, AC-12** — unit, `backend/tests/unit/test_agent_example_packs.py`.
  AC-8 is mutation-probed four ways, one per assertion the replacement carries: delete the
  unit 4 type keys, delete `引述`, delete `轉述`, delete `不主動` — each must turn it red on
  its own. AC-12 adds a fifth probe on the default clause. A substring test that has never
  been seen to fail is not evidence, and the `轉述` probe is the one that would have caught
  the coverage gap this dossier shipped with in its first draft.
- **AC-4 to AC-7** — the existing tests, run unchanged; AC-7's assertion is retargeted, not
  relaxed.
- **AC-9, AC-10** — doc-diff review.
- **No browser pass.** Nothing renders differently. The behavioural question — does a model
  actually apply the right column — is answerable only by the guide's dry run, and OQ-1
  records that.

## 13. SRS Delta

None. The prohibition was never a requirement (§4.1), and [R30.28] / [R30.35] already state
that example content is repository data rather than platform behaviour.

## 14. Open Questions

- **OQ-1.** Whether a model reliably applies the per-type rule is not answerable by any test
  in this repository. It belongs to the guide's dry run, against a real key, with both units
  run in one session. Until that happens the split rule is written and unobserved.
- **OQ-2.** SA is a peer agent at `temperature: 0.9`. It is the agent most likely to quote
  conversationally and the least likely to check a type key first. If the dry run shows the
  rule slipping anywhere, this is where to look before blaming the wording.

## 15. Deviation Log

Empty. Appended by `/build`.

## 16. Follow-ups

- **FU-1.** The same prohibition is stated three times in three wordings across the room pack,
  and DA restates it a fourth. There is no shared prompt fragment mechanism for packs, so
  every change like this one is a four-place edit that a test can only check by substring.
