---
type: bugfix
status: draft
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

- [ ] AC-1: The characterization test from §8.1 exists and passes, covering at minimum `None`,
  `False`, `True`, `""`, whitespace-only, `0`, `[]`, `{}` and a non-empty string.
- [ ] AC-2: `docs/examples/creative-thinking-course.md` states that `False` does **not** count
  as filled, covers the four cases `_is_filled` distinguishes, and points at `_is_filled`'s
  docstring rather than restating its reasoning.
- [ ] AC-3: The Limitations section states that a pack's `temperature` takes effect only where
  the resolved provider accepts sampling controls, names OpenAI's current default model as a
  case where it does not, and confirms Claude and Gemini forward it; the fallback description
  at `:228-230` links to it.
- [ ] AC-4: A read-through confirms no other claim in the edited sections contradicts code, and
  the five sibling claims in §6 are left to their owning dossiers rather than half-edited here.
- [ ] AC-5: The OpenAI reasoning-model temperature-drop assertion from §8.2 exists and passes.
- [ ] AC-6: Gates green: `ruff check . && ruff format --check .`, `mypy .`, `pytest -q`.
  No frontend gates apply - this change touches no frontend file.
- [ ] AC-7: The document contains no em-dash and no emoji, per the project's documentation
  style rules.

## 11. SRS Delta

**None.** [R30.27] already defines `filled_count` as a completeness measure over non-empty
fields, which is what the code implements and what the corrected document will describe.
[R30.35] already frames pack content as repository data. Nothing new is required; two documents
are being brought into line with requirements that already exist.

## 12. Deviation Log

Appended by /build.

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
