---
type: bugfix
status: implemented
created: 2026-08-16
requirements: [R30.27, R30.28, R30.35]
depends_on: []
---

# The example walkthrough states the opposite of how `filled_count` scores booleans, and omits that the provider fallback voids the packs' temperatures

## 1. Summary

`docs/examples/creative-thinking-course.md` is the operator- and educator-facing contract for
the shipped example. Two of its claims are wrong in ways that change what a reader builds:

- It states that booleans **always count as filled**, quoting the exact premise that the code
  uses to conclude the **opposite**. An educator who authors a checkbox activity on this
  sentence will ship a threshold no participant can clear (F-13).
- It describes the install-time provider fallback purely as a provider substitution, without
  saying that on OpenAI the shipped `temperature` values are dropped entirely, so the packs'
  deliberate sampling spread (AA 0.2, TA 0.7, SA 0.9) collapses (F-17).

Both from `docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`. Neither is a
code defect: in both cases the code is right and documented at the seam, and the document
disagrees with it. Grouped because they are the same file, the same reader, and the same class
of correction.

## 2. Observed vs Expected

### F-13 - the boolean rule is inverted

**Observed.** `docs/examples/creative-thinking-course.md:289-292`:

> `filled_count` is meant for text-response schemas: booleans always count as filled, because
> the generic form submits a value for every declared boolean property whether or not the
> participant touched it.

The code does the reverse. `backend/app/plugins/activity_validators.py:85` is
`if value is None or value is False: return False`, and its docstring at `:71-77` gives the
document's own premise as the reason for the opposite conclusion: because the form submits a
boolean for every declared property, counting an unticked box "would let a submission with
nothing filled in at all score `filled == len(properties)` and pass any threshold - the metric
would report the schema's size rather than the participant's effort." `:79-80` then states the
accepted cost: "a deliberate 'no' is not counted as an answer."

**Expected.** The document describes what `_is_filled` does: `None` and `False` do not count,
a non-empty string counts, an empty or whitespace-only string does not
(`activity_validators.py:87-88`), an empty list or dict does not (`:89-90`), and numbers count
including `0` (`:82-83`, `:91`).

**Intent source**: [R30.27], and the validator's own docstring, which is the authority the
document is paraphrasing.

### F-17 - the provider fallback silently voids the shipped temperatures

**Observed.** `docs/examples/creative-thinking-course.md:228-230` describes the fallback as
choosing a provider the key group carries. It does not say what that costs.

The chain, all verified: `backend/contexts/agents/application/example_service.py:309-326` falls
back to `usable[0]`; `AgentModelHint` is ordered CLAUDE, OPENAI, GEMINI
(`backend/contexts/agents/domain/models.py:12-15`), so an OpenAI-only project resolves to
`openai`. `install_pack` sets no `model_id` (`example_service.py:243-250`), so
`_resolve_provider_and_model` resolves `DEFAULT_CHAT_MODELS["openai"] = "gpt-5.4"`
(`domain/models.py:36`, used at
`backend/contexts/agents/application/runtime/turn_engine.py:335-338`).
`_REASONING_MODEL_RE = ^(?:o\d|gpt-5)`
(`backend/contexts/keys/infrastructure/adapters/openai.py:34`, applied at `:42-43`) matches
`gpt-5.4` - and matches every OpenAI preset in the catalog, so overriding the model does not
escape it. The adapter then drops `temperature` and `top_p` rather than translating them
(`:155-160`). The Claude path is unaffected: `_NO_SAMPLING_RE`
(`backend/contexts/keys/infrastructure/adapters/anthropic.py:35`) does not match
`claude-sonnet-4-6`. Gemini forwards temperature.

**Expected.** The Limitations section states that on a provider whose default model rejects
sampling controls, the packs' temperatures do not take effect, so the orchestration transfers
but the sampling spread does not.

**Intent source**: `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:261-263`
treats temperature as one of the things installing reproduces, and §5.3 rejects Option C
(prompt templates) partly because a template would lose it. [R30.35] frames pack content as
repository data an installer instantiates; a reader is entitled to know which parts survive.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Is F-13 a docs bug or a code bug - should `_is_filled` change to match the document? | **Docs bug; the code is right.** | Not a user question. The code's reasoning is sound and written down: counting untouched checkboxes makes `filled_count` report the schema's size rather than the participant's effort, which would make the metric meaningless and, per [R30.27], it is a *completeness* measure. Changing the code to match the document would break the metric to preserve a sentence. |
| Q-2 | Is F-17 a docs bug or a code bug? | **Docs bug; the code is right.** | Not a user question. The drop is deliberate, centrally documented at `turn_engine.py:162-178` ("each adapter then applies its own constraint"), and restated at `openai.py:156` ("Reasoning models accept only the default temperature; a custom one 400s"). Forwarding it would 400 every turn. The audit's refuter reclassified this from a functional defect to a docs gap on exactly these grounds. |
| Q-3 | Should F-17 also surface in the install dialog, not only the docs? | **Docs only here.** | The dialog's display gaps are already owned by `2026-08-16-agent-pack-install-report-fidelity` (F-11/F-16), which is where a UI sentence belongs. Splitting it would put two dossiers in one component. Cross-referenced in §13 so that dossier's author sees it. |
| Q-4 | Does the shipped course itself need changing for F-13? | **No.** | All four shipped types are all-string schemas, which the same paragraph correctly notes (`:292`), so no shipped activity is scored wrongly. The defect is entirely in what a reader is told to do next. |
| Q-5 | Does any unfinished dossier conflict? | **No - `depends_on: []`.** | `docs/tasks/BOARD.md` lists only `2026-07-07-graphrag-two-axis-redesign` and `2026-07-19-large-artifacts-silently-dropped`; neither touches `docs/examples/`. Among the sibling dossiers from this audit, `2026-08-16-example-pack-prompt-grounding` (F-12) edits pack JSON and may add a docs paragraph; the sections differ, but both authors should note the shared file - recorded in §9. |

## 4. Reproduction

**F-13.** No running system needed; it is a contradiction between two files. To observe the
consequence: register an activity type whose `payload_schema` declares six boolean properties
and whose `validator_config` is `{"validator_id": "filled_count", "min_filled": 4}`. Submit
with three boxes ticked and three untouched. Expected by the document: `filled == 6`, valid.
Actual: `filled == 3`, `is_valid=False`, `error_class="too_few_filled"`.

**F-17.** Install `creative-thinking-room` into a project whose only key group carries an
OpenAI key. Install succeeds and reports `openai`. Inspect the outbound provider payload for
any subsequent turn: no `temperature` field is present, for any of the three agents, despite
the stored agent rows carrying 0.7 / 0.9 / 0.2.

## 5. Root Cause Analysis

**F-13.** The document's sentence and the code's docstring share a premise - "the generic form
submits a value for every declared boolean property whether or not the participant touched it"
(`activity_validators.py:72-74`, restated at `creative-thinking-course.md:290-292`). The code
draws the conclusion "therefore an unticked box must not count, or the metric measures the
schema". The document draws "therefore booleans always count". The premise was transcribed and
the inference inverted. Root cause: the document's conclusion sentence, single link, no
upstream cause.

**F-17.** Two correct local decisions compose into an unstated global consequence. The install
fallback (`example_service.py:309-326`) is correct - refusing to install because a project
holds the wrong vendor's key would make the example unusable, which is AC-12's whole point. The
adapter's temperature drop (`openai.py:155-160`) is correct - the provider rejects the
parameter. Neither layer is wrong and neither is the place to explain the interaction; the
document is. Root cause: the Limitations section was written before the fallback's sampling
consequence was traced, and the audit is the first time the two were composed.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Readers of `docs/examples/creative-thinking-course.md`, which
`docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:452-456` establishes as the
worked walkthrough for the whole example. F-13 misleads anyone authoring a boolean schema;
F-17 misleads anyone installing the packs on a non-Claude key group. No running system is
affected by either document.

**Sibling suspects** - other claims in the same document, each checked against code by the
audit:

| Claim | Verdict |
|---|---|
| `:125` "Property order drives render order in the generic form" | **already corrected** by the 2026-08-13 dossier, replaced with the `x-order` fact (its AC-15). |
| `:312-314` one plugin per activity-type key, premised on "`ActivityType.key` is unique only per project" | **confirmed stale**, but owned by `2026-08-16-activity-type-key-collision-across-scopes` (F-5), which is deciding the underlying rule. Do not edit it here - the correct wording depends on that dossier's outcome. |
| `:252-254` the delete-then-reinstall upgrade note | **confirmed incomplete** (it warns that activations end but not that every project must re-enable), and owned by `2026-08-16-platform-type-delete-optin-lifecycle` (F-9). |
| `:205-210` the CLI as the project-scoped-copy path | **confirmed correct as prose**, though the behaviour it describes is broken - owned by `2026-08-16-example-cli-seeder-scope-leak` (F-1), which fixes the code rather than the sentence. |
| `:160`, `:268-271` TA responds to every message / agents read recent activity | **confirmed contradicted by code**, owned by `2026-08-16-activity-submission-wakeup-gap` (F-2). Whether the document or the code changes is that dossier's call. |

**Systemic reading.** Five of this document's claims were found stale or wrong by one audit.
The document is doing real work as a specification-by-example and nothing verifies it; see
FU-1.

## 7. Fix Design

**7.1 F-13.** Rewrite `docs/examples/creative-thinking-course.md:289-292` to state the rule the
code implements. It must cover the four cases `_is_filled` distinguishes
(`activity_validators.py:85-91`): `None` and `False` do not count; a string counts only if
non-empty after stripping; an empty list or dict does not count; every other value counts,
including the number `0`. Keep the existing final sentence noting that all four shipped types
are all-string so the rule does not affect them. Point the reader at
`_is_filled`'s docstring rather than restating its reasoning, so the two cannot diverge again.

**7.2 F-17.** Add one entry to the document's Limitations section (the section beginning at
`:294`) stating that a pack's `temperature` takes effect only where the resolved provider
accepts sampling controls; that the current OpenAI default model does not, so an OpenAI-only
project gets the pack's prompts, roles and wake-up configuration but the provider's default
sampling; and that Claude and Gemini forward it. Name the constraint's location
(`adapters/openai.py:155-160`) so a reader can check whether it still holds. Also amend
`:228-230` with a pointer to that Limitations entry, so the fallback description and its cost
are connected rather than pages apart.

**Why neither masks a symptom.** In both cases the code is the authority and the document is
the defect; §3 records why changing the code instead would be wrong.

**Data repair.** None.

## 8. Regression Test Plan

Documentation has no unit test, so the failing-test-first rule is honoured where it can be and
stated plainly where it cannot.

**8.1 F-13 is testable, and should be.** The document's claim is a claim about
`_is_filled`, so pin the real rule in
`backend/tests/unit/test_activities_services.py` (which already holds the `filled_count`
coverage): a parametrized test over `None`, `False`, `True`, `""`, `"  "`, `"x"`, `0`, `1`,
`[]`, `{}`, `[1]` asserting `_is_filled`'s verdict for each. Check whether such a test already
exists before adding it - if it does, extend it to cover `False` and `0` explicitly, which are
the two cases the document gets wrong and the two whose behaviour is least obvious.

This does not fail before the fix (the code is already correct), so it is a **characterization**
test rather than a regression test. Its job is to make the next contradictory doc edit fail
review by giving the rule one authoritative, executable statement. Say so in the test's module
docstring.

**8.2 F-17 is not unit-testable at the level that matters.** The claim is about a document.
What *is* testable, and worth pinning because the document will now cite it, is that the
OpenAI adapter drops temperature for a reasoning model: assert
`_is_reasoning_model("gpt-5.4")` is true and that the built payload for a reasoning model
carries no `temperature` key. Locate the existing OpenAI adapter tests and extend them; if none
covers this branch, that absence is itself worth recording.

**8.3 Verification of the documentation change** is a read, performed at approval per §10's
AC-4. This is the same standard `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md`
used for its own AC-15 ("manual - doc review at approval").

## 9. Risks and Rollback

- **Very low.** One documentation file plus two test additions. No runtime code changes.
- **File contention.** `2026-08-16-example-pack-prompt-grounding` (F-12) may also add prose to
  this document, and three other sibling dossiers own other sections of it (§6). None of them
  touches `:289-292` or the Limitations entry added here, but whoever builds second should
  rebase rather than assume. Deliberately not encoded as `depends_on`, per Q-5.
- **Getting the boolean rule wrong a second time.** The rewrite must be checked against
  `_is_filled` line by line, not against memory - the original error was precisely a
  plausible-sounding inference. AC-1's characterization test exists to make that check
  mechanical.
- **Rollback**: `git revert`. Nothing depends on the document's text.

## 10. Acceptance Criteria

- [x] AC-1: The characterization test from §8.1 exists and passes, covering at minimum `None`,
  `False`, `True`, `""`, whitespace-only, `0`, `[]`, `{}` and a non-empty string.
  `TestIsFilledRule` in `backend/tests/unit/test_activities_services.py`, 13 parametrized
  cases (the nine required plus `1`, `0.0`, `[1]`, `{"k": 1}`). See D-1.
- [x] AC-2: `docs/examples/creative-thinking-course.md` states that `False` does **not** count
  as filled, covers the four cases `_is_filled` distinguishes, and points at `_is_filled`'s
  docstring rather than restating its reasoning.
- [x] AC-3: The Limitations section states that a pack's `temperature` takes effect only where
  the resolved provider accepts sampling controls, names OpenAI's current default model as a
  case where it does not, and confirms Claude and Gemini forward it; the fallback description
  at `:228-230` links to it. See D-2 on the Claude half.
- [x] AC-4: A read-through confirms no other claim in the edited sections contradicts code, and
  the five sibling claims in §6 are left to their owning dossiers rather than half-edited here.
  Each surviving claim in the rewritten section was checked against
  `activity_validators.py:85-91` and `filled_count_scorer:109-116` line by line. Note D-4: the
  em-dash sweep changed punctuation on lines inside two sibling-owned sections; no claim in
  them was touched.
- [x] AC-5: The OpenAI reasoning-model temperature-drop assertion from §8.2 exists and passes.
  See D-3.
- [x] AC-6: `ruff check .` (all checks passed), `ruff format --check .` (943 files formatted),
  `mypy .` (no issues in 938 source files). `pytest -q` was **not** run to completion; the
  `unit` tier, which holds every test this dossier touches, is green at 6721 passed / 6 skipped
  with the `graphrag` files excluded. See D-5 for what was not run and why. No frontend gates
  apply, this change touches no frontend file.
- [x] AC-7: The document contains no em-dash (verified: 0 occurrences of U+2014) and no emoji.
  Applied document-wide rather than to the edited sections only, per D-4. The three remaining
  non-ASCII symbols are `→` (U+2192) in the 事件 → 想法 → 情緒 teaching frame and `–` (U+2013,
  an en-dash) in "A–E rubric"; neither is an em-dash or an emoji.

## 11. SRS Delta

**None.** [R30.27] already defines `filled_count` as a completeness measure over non-empty
fields, which is what the code implements and what the corrected document will describe.
[R30.35] already frames pack content as repository data. Nothing new is required; two documents
are being brought into line with requirements that already exist.

## 12. Deviation Log

- **D-1**: §8.1 anticipated that the coverage might already exist, and it did.
  `TestFilledCountValidator` already asserted `False`, `0`, whitespace, empty collections and
  a ticked checkbox (`test_activities_services.py:702-728`), all of it reached *through*
  `filled_count_scorer`. §8.1's fallback was to extend those; a separate `TestIsFilledRule`
  calling `_is_filled` directly was added instead, because §8.1's stated job is to give the
  rule "one authoritative, executable statement" and coverage spread across six scorer tests
  is not one statement. The existing tests are left untouched: they pin the *scorer's*
  behaviour, which is a different claim. The overlap is deliberate and the new class's
  docstring says so, so neither gets deleted later as redundant. §8.1 also said to put the
  characterization note in the *module* docstring; it is on the class instead, because the
  module covers the whole activities service surface and the note is true only of this class.
- **D-2**: AC-3 asked the entry to "confirm Claude and Gemini forward it". Stated as written
  that would be a second wrong claim of the kind this dossier exists to fix: the rule is per
  *resolved model*, not per provider. `_NO_SAMPLING_RE`
  (`backend/contexts/keys/infrastructure/adapters/anthropic.py:35`) also drops sampling for
  `claude-*-5` and `claude-opus-4-[7-9]`. The entry therefore confirms that Claude's current
  default `claude-sonnet-4-6` forwards and that Gemini forwards on every model, while saying
  the rule is per model and naming the Anthropic constraint's location so a reader can check
  it the same way they can check OpenAI's.
- **D-3**: §8.2 said to extend the existing OpenAI adapter tests. Two already assert the
  temperature drop (`test_provider_adapters.py:653`, `:752`) and both pin `o3-mini`. A new
  test was added instead, pinned to `DEFAULT_CHAT_MODELS["openai"]` rather than to a literal
  model id, because the document's claim is about the *default* model: a test on `o3-mini`
  keeps passing if the default ever moves to a non-reasoning model, leaving the document
  silently wrong with a green suite. It imports `contexts.agents.domain.models` inside a
  `contexts.keys` test file, which is the only cross-context import there; that is the point
  of the assertion (neither context alone can state the consequence) and its docstring says
  so, so it is not copied as precedent into production code.
- **D-4**: AC-7's em-dash rule was applied to the **whole document**, on the user's explicit
  instruction when asked. 23 occurrences, of which roughly 20 sit outside the sections this
  dossier owns, including lines inside the delete-then-reinstall note owned by
  `2026-08-16-platform-type-delete-optin-lifecycle` and the Limitations rubric bullet. Every
  one is punctuation only; no claim, citation, or emphasis was changed. The consequence is
  that the sibling dossiers listed in §6 and §9 will hit conflicts on lines they expected to
  merge cleanly, and must rebase rather than assume.
- **D-5**: `pytest -q` did not complete on this host, and the reason is worth writing down
  because it is not the usual one. The `integration`/`wiring`/`db` tiers need a live PostgreSQL
  and Docker is unavailable (`docker info` fails at the named pipe), which prior dossiers in
  this series already record. The new one is that **`tests/unit` itself stalls in the `graphrag`
  files**: a whole-tier run reached 24% in 25 minutes and was still crawling, and the 22-27%
  band is `test_graphrag_builder.py`, `test_graphrag_retrieve.py`, `test_graphrag_reset.py` and
  `test_graphrag_vector_store.py`, which appear to block on Neo4j/Qdrant connections that fail
  slowly on Windows rather than fast. With `--ignore-glob="*graphrag*"` the same tier finishes
  in 7m50s at **6721 passed, 6 skipped** (all six are host-capability skips for symlinks and
  Windows zipfile semantics, all pre-existing). Nothing in this dossier touches graphrag, and
  no production Python changed at all, so the exclusion does not shadow this work. It is
  recorded because a future `/build` on this host will hit the same wall and should not spend
  half an hour rediscovering it; CI is the authority per the project's standing rule.
- **D-6**: A post-close `/code-review` caught the new Limitations entry enumerating the affected
  temperatures as "AA 0.2, TA 0.7, SA 0.9" while saying "the packs" plural. **Two** packs ship,
  and `packs/creative-thinking-design.json:13` gives `da-lesson-designer` a `temperature` of 0.6
  that is dropped on OpenAI identically. A reader auditing which agents lose their configured
  sampling would have concluded DA was unaffected. Corrected to name both packs and all four
  agents. Worth noting that §1 of this dossier carries the same three-value enumeration, which
  is where the omission came from: the spec described the room pack and the document generalized
  it to both.

## 13. Follow-ups

- **FU-1**: **Nothing verifies `docs/examples/creative-thinking-course.md` against the code it
  describes.** One audit found five stale or wrong claims in it (§6). It functions as a
  specification-by-example, and a document in that role wants at least the treatment the pack
  files get: `backend/tests/unit/test_agent_example_packs.py` asserts properties of shipped
  content, and a similar test could assert that the document's stated key names, thresholds and
  type counts match the shipped course JSON. Not this task's scope, but this task is the second
  time the document has needed correcting.
- **FU-2**: For the author of `2026-08-16-agent-pack-install-report-fidelity`: the F-17 fact
  belongs in the install dialog too (Q-3). When the dialog learns to show the resolved provider,
  it should say in the same breath that the pack's temperatures will not apply on that provider.
- **FU-3**: `_is_filled`'s "numbers count, including `0`" rule (`activity_validators.py:82-83`)
  is defensible but asymmetric with the boolean rule: an untouched numeric field is absent from
  the payload while an untouched checkbox is present as `False`, which is why they differ. That
  asymmetry is a property of the generic form's `assemblePayload`, not of the validator, and it
  would disappear if the form omitted untouched booleans. Worth considering if the form is ever
  reworked; it would let `False` count as a deliberate answer.
- **FU-4**: `backend/tests/unit/test_provider_adapters.py`'s module docstring claims "every
  test asserts the secret never leaks into the normalised body". It is not true of the test
  added here, nor of its immediate siblings around `:653` and `:752`. Raised by the security
  gate as hardening rather than a vulnerability (the credential rides in the `Authorization`
  header, not the body, and nothing in this change touches header assembly). Either the
  docstring should be narrowed to the tests that do assert it, or a shared
  `assert _SECRET not in body` helper should be applied across the file.
