---
type: bugfix
status: implemented
created: 2026-08-16
requirements: [R30.31]
depends_on: []
---

# The `common.*` translation namespace does not exist, so seventeen call sites render English inside a zh-TW UI

## 1. Summary

Seventeen `t('common.…', 'English default')` call sites across six files reference a `common`
translation namespace that exists in no locale bundle. Because each call passes a literal
default message, vue-i18n renders the English fallback rather than the raw key, so the failure
is silent: no missing-key warning reaches a user, nothing throws, and the UI simply shows
"Close", "Edit", "Delete", "Cancel" and "Save" in English amid Chinese labels. Two of the
seventeen are on surfaces the example dossiers shipped, which is how the audit found it
(F-15 of `docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`), but the
namespace is missing for all six files equally.

The fix is two JSON files. No call site changes.

## 2. Observed vs Expected

**Observed.** A zh-TW user opens 活動類型 then 內建範例; every string is Chinese except the
dialog's footer button, which reads "Close". Opening the row action menu on a project-scoped
type shows "Edit" and "Delete" in English.

The seventeen call sites, all verified:

| File | Lines | Keys |
|---|---|---|
| `frontend/src/slices/activities/components/ExampleImportDialog.vue` | `:224` | `common.close` |
| `frontend/src/slices/activities/views/ActivityTypesView.vue` | `:124-125` | `common.edit`, `common.delete` |
| `frontend/src/slices/agents/views/AgentListView.vue` | `:171`, `:174` | `common.edit`, `common.delete` |
| `frontend/src/slices/agents/views/RagConfigListView.vue` | `:265`, `:267` | `common.edit`, `common.delete` |
| `frontend/src/slices/agents/views/KnowledgeMapConfigListView.vue` | `:179`, `:181` | `common.edit`, `common.delete` |
| `frontend/src/slices/agents/views/AgentToolsView.vue` | `:421`, `:666`, `:1073`, `:1163`, `:1170`, `:1212`, `:1349`, `:1356` | `common.edit`, `common.cancel`, `common.save` |

Five distinct keys in total: `close`, `edit`, `delete`, `cancel`, `save`.

No `common` namespace exists anywhere. `frontend/src/shared/locales/en.json` and
`zh-TW.json` each carry exactly one top-level key, `shared`. Every slice bundle is namespaced
under its own slice name, and `frontend/src/shared/i18n/index.ts:47` merges bundles flat via
`mergeLocaleMessage`, so there is no other source a `common` key could arrive from.

**Expected.** [R30.31] and the project's own gate #12 (`frontend/CLAUDE.md`, "i18n: no bare
string literals in templates") require user-facing strings to be translated, and AC-14 of
`docs/tasks/2026-08-09-platform-example-activity-types/spec.md:566` states it for these
surfaces specifically: "All user-facing strings in both slices exist in `en.json` and
`zh-TW.json`." A zh-TW user should see Chinese.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Where does the `common` namespace belong: `@shared/locales`, `@app/locales`, or duplicated per slice? | **`@shared/locales`.** | Not a user question - the codebase already answers it. `frontend/src/app/main.ts:30-36` registers the shared bundle with the comment "shared/ui atoms are used by every slice and by the app shell itself, so their strings register here rather than in any one slice". `common.*` has exactly that property: five verbs used by six files across three slices. Per-slice duplication would put the same five strings in six places and is what the shared bundle exists to prevent. |
| Q-2 | Fix only the two call sites the audit surfaced, or all seventeen? | **All seventeen, at zero extra cost.** | Not a user question. The fix adds keys, it does not touch call sites: every one of the seventeen already passes the key and an English default, so the moment the namespace resolves, all seventeen render translated. Fixing "only two" is not even expressible - there is one namespace and it either exists or does not. |
| Q-3 | Should the literal English default arguments be removed from the call sites once the keys exist? | **No, leave them.** | Not a user question. They are a genuine safety net: if a bundle chunk fails to load, the default renders instead of a raw key path. Removing seventeen of them is churn with a real downside and no upside, and would widen a two-file diff into a six-file one. Recorded as FU-1 only because a reader will wonder. |
| Q-4 | Does any unfinished dossier conflict? | **No - `depends_on: []`.** | `docs/tasks/BOARD.md` lists `2026-07-07-graphrag-two-axis-redesign` (graphrag) and `2026-07-19-large-artifacts-silently-dropped` (`kernel.py`/`turn_engine.py`/`attachment_service.py`). Neither touches locale files. Among the twelve sibling dossiers from the same audit, `2026-08-16-agent-pack-install-report-fidelity` adds keys under `agents.examplePacks` and `2026-08-16-admin-platform-type-edit-unreachable` may add keys under `admin.activities`; both are different files from `shared/locales/*`, so there is no overlap prerequisite either. |

## 4. Reproduction

1. Set the browser language to Chinese, or select 繁體中文 in the app, so the active locale is
   `zh-TW`.
2. Open a project's 活動類型 page (`/projects/:projectId/activity-types`).
3. Observe the row action menu on any project-scoped type: the two entries read "Edit" and
   "Delete" rather than 編輯 / 刪除.
4. Click 匯入內建範例 to open `ExampleImportDialog` and look at the footer button: it reads
   "Close" rather than 關閉.

Deterministic; no data or tenancy preconditions beyond a project with at least one
project-scoped activity type for step 3.

## 5. Root Cause Analysis

1. **Root cause.** The `common` namespace was never added to any locale bundle.
   `frontend/src/shared/locales/en.json` and `zh-TW.json` contain a single top-level `shared`
   key. Correcting this one link removes the symptom at all seventeen sites.
2. The symptom is invisible to every automated gate because each call site supplies a default:
   `t('common.close', 'Close')`. vue-i18n's `missing` handler
   (`frontend/src/shared/i18n/index.ts:26-34`) returns the key only when no default is given;
   with a default, the default is rendered and the handler's fallback-loading path resolves
   nothing further. So there is no console warning, no thrown error, and no failed assertion.
3. Gate #12 does not catch it either: the gate forbids **bare string literals in templates**
   (`frontend/CLAUDE.md`), and `t('common.edit', 'Edit')` is a `t()` call, not a bare literal.
   The literal is an argument, which is exactly the shape the gate is designed to permit.

**Why this is a bugfix and not a feature.** No new capability is added; five strings that the
code already asks for in two languages are supplied in two languages.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every zh-TW user of the six listed files. Cosmetic only: no data, no
authorization, no state. English users see no change at all, since the defaults already match
what the keys will contain.

**Sibling suspects.** The general pattern is "a `t()` call naming a namespace that no bundle
provides, masked by a default argument". Checked:

- **`common.*` - confirmed**, the seventeen sites above.
- **Every other namespace referenced in the six files** - cleared. The audit's frontend lens
  flattened all `en.json` bundles and compared key sets: `activities` (168 keys), `agents`
  (556) and `admin` (266) are identical between `en` and `zh-TW`, with no missing key other
  than the `common` trio.
- **Literal `@` in translation values** - cleared, and worth stating because it is the one
  i18n defect class in this codebase that crashes production rather than degrading
  (`reference_i18n_literal_at`): all four occurrences repo-wide are correctly escaped as
  `{'@'}`.

**Systemic gap, not fixed here.** Nothing in CI asserts that a `t()` key resolves. That is why
this survived; see FU-2.

## 7. Fix Design

Add a `common` namespace to both shared bundles, with the five keys the call sites request:

- `frontend/src/shared/locales/en.json` - `close`, `edit`, `delete`, `cancel`, `save`, with the
  English text the call sites already pass as defaults, so no rendered string changes for an
  English user.
- `frontend/src/shared/locales/zh-TW.json` - the same five keys in 繁體中文.

Both files must stay UTF-8 without a BOM, matching their current encoding.

No component changes, no new registration (the shared bundle is already registered at
`frontend/src/app/main.ts:33-36`, and mount is gated on `ensureLocaleLoaded`, so the keys are
merged before any component can render). No API change, no `gen:api`, no migration.

**Why this does not merely mask the symptom.** The symptom is a missing translation and the
cause is a missing translation; there is no deeper link. The one thing that would be masking is
deleting the default arguments to force a visible failure, which Q-3 rejects for good reason.

**Data repair.** None - nothing was persisted.

## 8. Regression Test Plan

The failing test comes first.

**8.1** New test asserting the namespace resolves in both locales. The natural home is a
shared-level i18n test; if none exists, create
`frontend/src/shared/__tests__/i18n.common.test.ts`. It imports both shared bundles directly
and asserts each contains a `common` object carrying all five keys, and that the `en` and
`zh-TW` key sets are identical. It fails today because neither bundle has a `common` key at
all.

**8.2** A component-level assertion that the symptom is gone, in an existing file rather than a
new one: `frontend/src/slices/activities/__tests__/ExampleImportDialog.test.ts` renders the
footer button and asserts it uses the translated value under an active `zh-TW` locale rather
than the literal "Close". This is the test that would have caught the original report, and it
pins the user-visible behaviour rather than the file contents.

**8.3** Guard against recurrence in the same class: extend 8.1 to assert that **every** key
referenced as `t('common.X', …)` across `frontend/src` exists in the bundle, by scanning for
the `common.` prefix. This keeps the test honest when a sixth verb is added later, and is the
cheap half of FU-2.

## 9. Risks and Rollback

- **Very low.** Two additive JSON files; no code path changes. The worst realistic failure is a
  wrong translation, which is a text edit.
- **Encoding.** `zh-TW.json` must not gain a BOM. D-8 of
  `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:785-791` records that shell
  redirection on this project's Windows host adds one; edit the file with a tool that does not.
- **English users see no change**, because the new `en` values are exactly the defaults already
  being rendered. This is deliberate and makes the change safe to ship without an English
  review.
- **Rollback**: `git revert`. The call sites keep their defaults, so reverting restores the
  current English-in-zh-TW behaviour rather than breaking anything.

## 10. Acceptance Criteria

- [x] AC-1: The test from §8.1 fails before the fix and passes after.
- [x] AC-2: `frontend/src/shared/locales/en.json` and `zh-TW.json` both contain a `common`
  namespace with `close`, `edit`, `delete`, `cancel`, `save`, and their key sets are identical.
- [x] AC-3: Under an active `zh-TW` locale, all seventeen call sites listed in §2 render
  Chinese; under `en` they render exactly the text they render today.
- [x] AC-4: No call site is modified - the diff touches only the two locale files and the
  test files.
- [x] AC-5: `zh-TW.json` is written UTF-8 with no BOM.
- [x] AC-6: Gates green: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`,
  `pnpm run check:bundle-size`, `pnpm run check:type-coverage`,
  `pnpm run check:boundaries-enforced`.

## 11. SRS Delta

**None.** [R30.31] and gate #12 already require translated user-facing strings; this supplies
strings the code already asks for.

## 12. Deviation Log

- **D-1**: §8.2 asked for a component-level assertion "under an active `zh-TW` locale", which
  the test harness does not provide. `renderView` (`frontend/tests/utils/render.ts:38`) mounts
  the shared `i18n` singleton with **no** bundle loaded at all - which is why every other
  assertion in `ExampleImportDialog.test.ts` matches a raw key string such as
  `activities.examples.enable`. The test therefore merges `@shared/locales/zh-TW.json` into the
  singleton and switches the locale itself, restoring it in a `finally`. Consequence worth
  knowing: it proves the key resolves, not that the app's boot path merges the bundle. Contained
  by vitest's default per-file fork isolation (`vite.config.ts:70-74` sets no `pool`/`isolate`
  override); if that ever changes, this merge becomes global.
- **D-2**: §8.3's recurrence guard is a second `describe` block in the same file rather than an
  extension of §8.1's assertions, and it carries two bounds the spec did not state. It excludes
  `**/__tests__/**` from the scan, so a test asserting on the literal string `common.delete`
  (`frontend/src/slices/agents/__tests__/KnowledgeMapConfigListView.test.ts:150`) is not counted
  as a call site. And it asserts the scan finds at least one reference, so a glob or regex that
  silently stops matching fails loudly instead of passing vacuously - the failure mode a
  scan-based test is most likely to develop.
- **D-3**: AC-3 is checked on this basis: one call site (`ExampleImportDialog`'s footer) is
  rendered and asserted under `zh-TW`; the other sixteen follow from the same mechanism, pinned
  by §8.3's scan asserting that every key referenced anywhere under `src/` exists in both
  bundles. The `en` half needs no render assertion because the five new English values are
  byte-identical to the default arguments already being rendered.
- **D-4**: A `check-quality` finding on this task's sibling (the backend dossier) prompted a
  second pass over this file's type handling: `en`/`zh-TW` are read through a
  `{ common?: ... }` cast in *all* assertions, not only the first. Typing `common` as required
  would turn a deleted namespace into a compile error in the test file, and a test that cannot
  run is not a test that fails.

## 13. Follow-ups

- **FU-1**: The seventeen call sites keep their literal English default arguments (Q-3). Once
  the namespace exists they are dead code in the happy path, retained as a chunk-load safety
  net. If the team later decides the safety net is not worth the duplication, removing them is
  a mechanical sweep - but do it as its own change, not as a rider on this one.
- **FU-2**: **No gate asserts that a `t()` key resolves.** This defect existed across six files
  and three slices without any of the twelve frontend gates noticing, because gate #12 checks
  for bare literals in templates rather than for unresolvable keys. §8.3 closes the `common.*`
  slice of this; a general lint rule or a build-time check that every `t('x.y')` literal
  resolves against the merged bundles would close the rest. That is a tooling task, not a
  bugfix.
- **FU-3**: `AgentToolsView.vue` accounts for eight of the seventeen sites and is over 1350
  lines. Not this task's business, but it is the file most likely to grow a ninth; recorded for
  whoever routes structural work (`check-quality`).
- **FU-4**: **The component test harness loads no locale bundles at all**, which sharpens FU-2
  by naming the second reason this survived. `renderView` mounts the shared `i18n` instance
  without running a single loader (D-1), so every component test asserts either a raw key or an
  English default argument - 182 test files, and none of them could have seen this. The hole is
  wider than `common.*`: a key deleted from `zh-TW.json` only is invisible to the entire suite
  the same way. Making `renderView` merge the real bundles would break every existing
  `toContain('slice.key')` assertion, so the cheap form is a per-slice bundle-parity test
  (en and zh-TW declare identical key sets) modelled on §8.1's third assertion.
