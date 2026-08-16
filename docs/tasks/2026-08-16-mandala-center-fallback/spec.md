---
type: bugfix
status: approved
created: 2026-08-16
requirements: [R30.18, R30.36]
depends_on: []
---

# The mandala plugin promotes the first property to the centre cell, overriding the declared render order

## 1. Summary

`MandalaGrid` resolves its centre cell as "the property named `center`, **or else the first
property". When a nine-field schema declares no property named `center`, the fallback silently
relocates the author's first field to the middle of the grid and shifts everything else, so the
rendered order is not the declared order. [R30.36] says the platform "renders declared fields in
that order"; the fallback is the one place in the activities rendering path that does not.

The behaviour is deliberate and pinned by a test citing AC-8 of
`docs/tasks/2026-08-08-creative-thinking-course-example/spec.md:571-572`, which predates
[R30.36]. The audit therefore recorded it as *plausible* rather than confirmed and raised the
rule conflict for triage; the user resolved it on 2026-08-16 in [R30.36]'s favour, which is what
makes this a bugfix. F-18 of
`docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`.

## 2. Observed vs Expected

**Observed.**

- `frontend/src/slices/activities/plugins/mandala9grid/MandalaGrid.vue:37-40`:
  ```
  const centerField = computed<SchemaField | null>(() => {
    if (!isGrid.value) return null
    return fields.value.find((f) => f.name === CENTER_PROPERTY) ?? fields.value[0] ?? null
  })
  ```
  `CENTER_PROPERTY = 'center'` (`:25`). The `?? fields.value[0]` arm is the defect.
- `:43-48` then removes that field from the ring and splices it back at `CENTER_INDEX = 4`
  (`:26`), so with properties `f1`..`f9` the rendered order becomes
  `f2, f3, f4, f5, f1, f6, f7, f8, f9`.
- `:28` derives `fields` from `fieldsFromSchema(props.schema)`, which since the 2026-08-13
  dossier sorts by `x-order`. So an author can declare an explicit order and still have its
  first element moved.
- Pinned by `frontend/src/slices/activities/__tests__/MandalaGrid.test.ts:110-118`, "treats the
  first property as the centre when none is named center (AC-8)", asserting
  `cells[4]` is `mandala-f1`.

**Expected.** A nine-field schema with no `center` property renders its nine declared fields in
declared order across the 3x3 grid, with no field promoted or displaced. A schema that *does*
declare `center` keeps today's behaviour: `center` in the middle, the rest as the ring.

**Intent sources.**

- **[R30.36]** (`REQUIREMENTS.md`, added by
  `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:727-730`): "A payload schema
  property may declare an explicit render order; the platform renders declared fields in that
  order and must not depend on the key order of the stored schema document."
- The same dossier's own description of the plugin (`:308-310`) says only that it "removes the
  property named `center`, and splices it back at index 4". It describes no fallback, so the
  fallback was never re-examined when `x-order` was introduced.
- **The superseded intent**: AC-8 of
  `docs/tasks/2026-08-08-creative-thinking-course-example/spec.md:571-572` reads "The Mandala
  plugin places the `center` property in the middle cell and the other eight around it; a
  schema that is not nine fields renders as a single column instead of a broken grid." Note it
  specifies the `center`-present case and the not-nine-fields case, and says **nothing** about
  a nine-field schema with no `center` property. The fallback was an implementation choice, not
  an acceptance criterion; the test's "(AC-8)" citation over-claims its mandate.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | [R30.36] says render in declared order; the plugin promotes the first property instead. Which rule wins? | **[R30.36] wins. Remove the fallback.** | User decision, 2026-08-16, recorded in the audit's F-18 triage note. A platform-wide rendering invariant is worth more than one plugin's convenience default, and the alternative (an exception clause in [R30.36]) would make the ordering guarantee conditional on which renderer a type happens to use, which an author cannot see from their schema. |
| Q-2 | What should a nine-field schema with no `center` property render as: the declared order across the grid, or the single-column fallback? | **The grid, in declared order.** | Not a user question - the single-column fallback exists for a schema that is *not nine fields* (`MandalaGrid.vue:30-35`, "degrade, never drop (R30.18)"). A nine-field schema fits the grid; refusing to grid it because of a property name would be a second, worse surprise. |
| Q-3 | Does `MandalaGrid.test.ts:110-118` get deleted or rewritten? | **Rewritten, keeping its scenario and inverting its expectation.** | Not a user question, but it must be deliberate: the test is the only record that the fallback was intended. Rewriting it in place, with a comment naming the superseded AC-8 and the [R30.36] decision, keeps that history discoverable. Deleting it would erase the evidence that this was once chosen on purpose. |
| Q-4 | Does the shipped course change? | **No.** | `backend/contexts/activities/infrastructure/examples/courses/creative-thinking.json` declares a `center` property at `x-order` 5 for `mandala-9grid`, so the shipped example takes the `find` arm and is byte-identical in behaviour before and after. Verified as part of the audit's AC-1 check. |
| Q-5 | Does any unfinished dossier conflict? | **No - `depends_on: []`.** | `docs/tasks/BOARD.md` lists `2026-07-07-graphrag-two-axis-redesign` and `2026-07-19-large-artifacts-silently-dropped`; neither touches the activities slice. Among the sibling dossiers from this audit, none edits `MandalaGrid.vue` or its test. |

## 4. Reproduction

Component-level; no backend or tenancy preconditions.

1. Register an activity type whose `key` is `mandala-9grid` (the key the bundled plugin binds
   to, `frontend/src/slices/activities/plugins/mandala9grid/index.ts:15`) and whose
   `payload_schema` declares exactly nine string properties named `q1` through `q9`, each with
   `x-order` 1 through 9 and no property named `center`.
2. Activate it in a chatroom and open the participant form.

**Actual.** The grid renders `q2, q3, q4, q5, q1, q6, q7, q8, q9`: `q1` sits in the middle cell
and every other field has shifted one position earlier.

**Expected.** `q1` through `q9` in declared order, reading left to right, top to bottom.

Directly observable in the unit tier: `MandalaGrid.test.ts:110-118` already mounts exactly this
schema (`f1`..`f9`) and asserts the defective placement.

## 5. Root Cause Analysis

1. **Root cause.** The `?? fields.value[0]` arm at
   `MandalaGrid.vue:39`. It answers "which field is the centre" with a positional guess when the
   schema does not name one. Removing this one arm prevents the symptom; nothing upstream
   contributes.
2. `cells` (`:43-48`) then acts on that guess, filtering the chosen field out of the ring and
   splicing it at index 4. This is correct behaviour given a correct `centerField`, so it is a
   propagation link rather than a second cause.
3. **Why it was not caught by the `x-order` work.** The 2026-08-13 dossier reasoned that the
   plugin "inherits the fix without modification" because it derives its cells from
   `fieldsFromSchema` (`spec.md:308-310`). That is true of the sort, but the splice happens
   *after* the sort and was not re-read against the new [R30.36] guarantee it had just created.
   The fallback predates `x-order` by five days.
4. **Why no test caught it.** The test that covers this exact input asserts the defective
   output (`MandalaGrid.test.ts:110-118`). A test pinning the behaviour is the strongest
   possible reason a defect survives a review, which is why the audit marked it plausible
   rather than confirmed and escalated the rule conflict instead of the code.

**Not a cause: `isGrid`.** `:35` counts `fields.value.length === GRID_SIZE` **before** any
centre removal, and `cells` reassembles to nine, so the nine-field check is correct and the
audit's frontend lens verified AC-1 renders the shipped worksheet layout. Do not change `:35`.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Any activity type keyed `mandala-9grid` whose schema declares nine fields and
no property named `center`. Presentation only: `assemblePayload` keys by property name
(`MandalaGrid.vue:53-56`), so a submission's data is correct regardless of cell position - only
what the participant sees is wrong. No stored data is affected and no repair is needed.

The shipped course is unaffected (Q-4). The population at risk is therefore projects that reuse
the `mandala-9grid` key with their own schema, which is exactly the scenario
`docs/examples/creative-thinking-course.md:312-314` describes as expected ("Any project whose
type is named `mandala-9grid` inherits the grid renderer").

**Sibling suspects** - other places a renderer could depart from declared order:

| Site | Verdict |
|---|---|
| `frontend/src/slices/activities/components/schemaFields.ts` (`fieldsFromSchema`, `orderOf`) | **cleared** by the audit: stable sort, `x-order: 0` handled correctly via a `typeof === 'number' && Number.isFinite` test rather than `\|\|`, comparator returns 0 on ties, undeclared properties keep stored order. Covered by `schemaForm.test.ts:53-105`. |
| `SchemaForm.vue` | **cleared** - renders `fieldsFromSchema` output directly with no reordering. |
| `MandalaGrid.vue:43-48` (the splice itself, for a schema that *does* declare `center`) | **confirmed intentional and retained.** Moving a field named `center` to the centre is the plugin's stated purpose and is what AC-8 actually specified. Not a defect. |
| `build_agent_digest` (`backend/contexts/activities/application/agent_digest.py:21-27`) | **cleared as out of scope** - emits `sort_keys=True`, alphabetical, deliberately. Recorded as FU-3/FU-13 of the agent-packs dossier; [R30.36] governs rendering, not the digest. |

The mandala fallback is the only renderer-side departure from declared order in the tree.

## 7. Fix Design

**7.1 Remove the positional fallback.** `MandalaGrid.vue:37-40` becomes a lookup with no
positional arm: `centerField` is the field named `center` when one exists, and `null`
otherwise.

**7.2 Make `cells` handle the no-centre case as declared order.** `:43-48` currently returns
`[]` when `centerField` is null (`:45`), which was safe only because the fallback made null
unreachable for a nine-field schema. It must now return `fields.value` unchanged when there is
no centre, so the nine declared fields render across the grid in order (Q-2). The
`displayFields` computed at `:51` then needs no change: `isGrid` is still true, and `cells`
supplies the nine fields in declared order.

The three constants (`GRID_SIZE`, `CENTER_PROPERTY`, `CENTER_INDEX`, `:24-26`) stay as they
are, as does the not-nine-fields single-column fallback at `:30-35`, which serves [R30.18] and
is a different rule.

**7.3 Document the retained special case.** The comment above `centerField` must state that
`center` is a *named opt-in* to centre placement, and that a schema declaring no such property
renders in declared order, citing [R30.36]. Without this the next reader re-adds the fallback
as a robustness improvement.

**Why this does not mask the symptom.** The symptom is a field rendered in the wrong cell and
the cause is the expression that chooses it. There is no deeper link: the sort above it is
already correct, and the splice below it is correct given a correct input.

**Data repair.** None. Payloads key by property name, so nothing was ever stored in the wrong
place.

## 8. Regression Test Plan

The failing test comes first, and here it is an inversion rather than an addition.

**8.1 Rewrite `MandalaGrid.test.ts:110-118` (Q-3).** Keep the scenario (nine properties `f1`
through `f9`, none named `center`); invert the expectation: assert the nine cells render in
declared order, i.e. `cells[0]` is `mandala-f1` and `cells[4]` is `mandala-f5`. Rename it to
state the new rule ("renders declared order when no property is named center (R30.36)") and
carry a comment naming the superseded AC-8 of
`docs/tasks/2026-08-08-creative-thinking-course-example/spec.md:571-572` and the 2026-08-16
triage decision. It fails against current code, which puts `mandala-f1` at index 4.

**8.2 Add an `x-order` interaction case**, same file. Nine properties whose object key order is
deliberately different from their `x-order` (so `jsonb`'s ordering cannot accidentally produce
the right answer), none named `center`. Assert the rendered order matches `x-order`. This is
the case that ties the plugin to [R30.36] rather than merely to object order, and no existing
test covers it.

**8.3 Guard the retained behaviour.** The existing test at `MandalaGrid.test.ts:88-108`, which
asserts the shipped nine-name schema places `center` at index 4, must pass **unmodified**. If
it needs any change, the fix has over-reached. Verify explicitly.

**8.4 Guard the not-nine-fields fallback.** `MandalaGrid.test.ts:120-124` ("falls back to a
single column when the schema is not nine fields") must also pass unmodified; §7.2 changes the
nine-field-no-centre path only.

## 9. Risks and Rollback

- **Low.** One computed expression, one guard clause, one comment, in a single component.
- **The `cells` null branch is load-bearing.** `:45` currently short-circuits to `[]`. After
  §7.1 a nine-field schema with no `center` reaches it, so §7.2 must land in the same change or
  the grid renders empty. This is the one way to get the fix wrong, and AC-2 exists to catch it.
- **Behaviour change for existing deployments.** Any project already using a nine-field
  `mandala-9grid` schema without a `center` property will see its cells move. That is the point
  of the fix, and the new order is the one the author declared, but it is a visible change to a
  live form. No shipped type is affected (Q-4).
- **The superseded AC-8 stays checked in a closed dossier.** Per the contract, dossiers are not
  rewritten retroactively; this spec's §2 records the supersession instead, which is why the
  test comment in §8.1 matters.
- **Rollback**: `git revert`. No migration, no API, no stored data.

## 10. Acceptance Criteria

- [ ] AC-1: The rewritten test from §8.1 fails before the fix and passes after.
- [ ] AC-2: A nine-field schema with no property named `center` renders all nine fields in the
  3x3 grid in declared order, with no field promoted, displaced, or dropped, and the grid is
  **not** empty.
- [ ] AC-3: A nine-field schema that *does* declare `center` places it in the middle cell with
  the other eight around it, exactly as today; `MandalaGrid.test.ts:88-108` passes unmodified.
- [ ] AC-4: Declared order is honoured via `x-order` even when the schema's object key order
  differs from it (§8.2).
- [ ] AC-5: A schema that is not nine fields still renders as a single column;
  `MandalaGrid.test.ts:120-124` passes unmodified.
- [ ] AC-6: The comment above `centerField` states that `center` is a named opt-in and cites
  [R30.36].
- [ ] AC-7: The shipped `mandala-9grid` type renders the worksheet layout unchanged
  (家/工作/具備能力 top row, 想對30歲的自己說…/自由發揮/人際關係 bottom row).
- [ ] AC-8: Gates green: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`,
  `pnpm run check:bundle-size`, `pnpm run check:type-coverage`,
  `pnpm run check:boundaries-enforced`.

## 11. SRS Delta

**None.** [R30.36] already states the rule this fix implements; the change removes a violation
rather than defining new behaviour. [R30.18]'s degrade-never-drop rule is served unchanged by
the not-nine-fields single-column fallback, which this task does not touch.

Recorded rather than drafted: the superseded AC-8 lives in a closed dossier
(`docs/tasks/2026-08-08-creative-thinking-course-example/spec.md:571-572`) and is **not**
amended, per the contract's rule that existing dossiers are never rewritten retroactively. The
supersession is recorded in §2 of this spec and in the rewritten test's comment.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1**: `MandalaGrid.vue:35`'s exact-nine-fields rule is invisible to a schema author until
  the form looks wrong - a ten-field schema silently becomes a single column with no diagnostic.
  This is FU-2 of `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:839-842`,
  still open, and this fix works within the rule rather than changing it. A validation hint in
  the authoring UI would close it.
- **FU-2**: The plugin binds by `key` (`plugins/mandala9grid/index.ts:15`), so any project's
  type named `mandala-9grid` inherits this renderer and its nine-field rule. That coupling is
  the subject of `docs/tasks/2026-08-16-activity-type-key-collision-across-scopes` (F-5); the
  wording at `docs/examples/creative-thinking-course.md:312-314` depends on that dossier's
  outcome and is deliberately not edited here.
- **FU-3**: A test citing an AC by number (`MandalaGrid.test.ts:110`, "(AC-8)") does not say
  *which* dossier's AC-8, and this audit needed a search to find it. Tests across this repo cite
  bare AC numbers the same way. Including the dossier slug in the citation would make the
  intent traceable in one step; a mechanical sweep, worth doing once.
