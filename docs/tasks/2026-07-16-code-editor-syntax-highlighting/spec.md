---
type: feature
status: draft
created: 2026-07-16
requirements: [R24.48, R24.15]
---

# Give `SCodeEditor` real syntax highlighting, starting with the JSON fields that already report errors

## 1. Summary

Split from `2026-07-16-agent-skills`' FU-4. `SCodeEditor` is a bare `<textarea>`
(`shared/ui/SCodeEditor.vue:58-73`) whose `language` prop emits a CSS class that nothing
consumes. This task replaces it with a lazily-loaded CodeMirror 6 editor, landing JSON first.

FU-4 left two costs unmeasured and called the sizing "an open question, not a finding". **Both are
now measured** (§5), and the measurement changed the task in three ways FU-4 did not anticipate:

1. **It fits — with room.** The minimal CodeMirror build for JSON is **150 911 B gz** against
   `LAZY_LIMIT=204800` (`scripts/check-bundle-size.sh:8`) — a 26.3% margin. `pnpm audit --prod` is
   clean over the 12 packages added.
2. **FU-4's stated obstacle does not exist.** FU-4 says the exempt list "is not extensible without a
   visible policy change". The exempt list is **dead code**: `EXEMPT_PREFIXES='^(mermaid|hljs)-'`
   (`check-bundle-size.sh:15`) matches **zero of the 206 chunks** the current build emits. Nothing
   needs to be added to it, because nothing is in it. §5 explains why and §16 files it.
3. **The `language` prop is not merely unstyled — it is dead, and it lies.** `code-editor--` appears
   exactly once in the entire codebase: at `SCodeEditor.vue:61`, the line that *emits* it. Zero CSS
   rules consume it. Six call sites pass `language="json"` / `"markdown"` / `"text"` believing they
   are configuring something. The union (`:7`) does not even contain `'python'`, so the "per-file
   Python editing" FU-4 is worried about cannot be expressed today.

**The JSON fields are the reason to do this now, and Python is not.** There are zero Python call
sites. But `AgentToolsView.vue:1101` (`configJson`) and `:1209` (`fnParamsJson`) already have error
state — `configJsonError` (`:224`) and `fnParamsError` (`:475`) — and today that error is a *generic*
i18n string with no position (`:335` `t('agents.tools.mcp.invalidJson')`; `:563`
`t('agents.tools.functions.invalidParamsJson')`), raised only **on submit** (`:333`, `:561`). The
user types malformed JSON, submits, and is told "invalid JSON" with no idea where. CodeMirror's
`jsonParseLinter` marks the offending position live, in the gutter. That is the win this task buys.

## 2. Goals and Non-goals

**Goals.**
- `SCodeEditor` renders real, themed syntax highlighting via a lazily-loaded CodeMirror 6.
- The two JSON fields get live, positioned parse errors instead of a generic post-submit string.
- The `language` prop becomes truthful: every accepted value maps to a real grammar, or is not
  accepted.
- The per-chunk bundle budget (gate #9) stays green, with the margin stated rather than discovered.

**Non-goals.**
- **`basicSetup`.** Measured at **200 003 B gz** — it passes by **4 797 B (2.3%)**. That is not a
  margin, it is a tripwire: the next extension anyone adds reddens CI, and the failure will look
  unrelated to whoever adds it. Q-2 rejects it explicitly.
- **Migrating all six call sites in this task.** Q-3 stages it. The three `markdown` sites
  (`SPromptAssistantConfigForm.vue:90`, `SPromptTemplateManager.vue:173`, `AgentDetailView.vue:919`)
  gain little from highlighting and carry the most regression risk (they are the system-prompt
  editors).
- **Python.** Zero call sites. The grammar is measured (§5) so adding it later is a decision, not a
  research project, but shipping an unused 160 KB grammar is not a goal.
- **Replacing the `SCharCount` / validation architecture.** The linter *adds* positioned feedback; it
  does not remove the existing submit-time validation, which is the server contract's mirror.
- **`docs/tasks/2026-07-16-agent-skills`' own scope.** Skills' editor needs are that dossier's.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Adopt an editor library at all, given FU-4 called the cost unknown? | **Yes — minimal CodeMirror 6, lazily imported.** | The cost is no longer unknown: 150 911 B gz for the JSON build, 26.3% under the per-chunk budget, `pnpm audit --prod` clean, 12 packages all from the `@codemirror`/`@lezer` orgs. FU-4's blocker (the exempt list) turned out to be dead code. With both stated costs cleared and a real UX defect (positionless JSON errors) waiting on it, deferring further would be deferring on a question that has been answered. |
| Q-2 | `basicSetup` or hand-assembled extensions? | **Hand-assembled. `basicSetup` is forbidden, and the AC enforces it.** | Measured: `basicSetup` + one grammar = 200 003 B gz vs the 204 800 B limit — 2.3% headroom. Hand-assembled (`lineNumbers`, `history`, `syntaxHighlighting`, `defaultKeymap`) + `lang-json` + `lint` = 150 911 B, 26.3% headroom. `basicSetup` is a convenience re-export that pulls autocomplete, search, lint, fold, and bracket-matching whether or not they are used. Paying 49 KB for features we did not ask for *and* surrendering the margin is the worst of both. |
| Q-3 | Which call sites migrate now? | **The two JSON fields only** (`AgentToolsView.vue:1101`, `:1209`). | They are where the defect is: real error state that carries no position. The three `markdown` sites are system-prompt editors — highest blast radius, least benefit (markdown highlighting in a prompt box is decoration). The one `text` readonly site (`:1142`) has nothing to highlight. Staging keeps the diff reviewable and the rollback cheap. |
| Q-4 | What happens to the `language` prop? | **The union narrows to what is real.** `'json'` maps to a grammar; the other members stay accepted but map to no grammar *and the dead class binding at `:61` is deleted.* | Deleting the prop outright would churn six call sites for no user-visible gain and is a separate concern from adopting the library. Deleting the *class binding* is free and removes the lie. The union grows back as grammars land (Q-3, §16). |
| Q-5 | How is the grammar loaded, given four grammars will eventually exist? | **One dynamic `import()` per grammar, never a static import of all of them.** | Measured: all four grammars + lint in one chunk = **258 515 B gz, which FAILS the gate by 53 715 B (26%)**. This is not an optimisation — a future implementer who adds `lang-yaml` and `lang-markdown` beside `lang-json` with static imports *will* redden CI. The dynamic-import-per-grammar shape is a hard constraint and AC-7 pins it. |
| Q-6 | Does this touch the initial bundle? | **No — it must not.** | `INITIAL_LIMIT=256000` covers `index-*`/`vendor-*` (`check-bundle-size.sh:24`). `SCodeEditor` is imported statically by its call sites, so the *component* is in their chunks; only the CodeMirror payload is dynamic. The editor must render its textarea fallback synchronously and swap in CodeMirror after the import resolves, or the component becomes async and drags its importers. |

## 4. Current State

**The component.** `shared/ui/SCodeEditor.vue` is 109 lines. A `<textarea>` (`:58-73`) with:
- `language?: 'json' | 'yaml' | 'markdown' | 'text'` (`:7`), default `'text'` (`:12`). **No `'python'`.**
- `:class="`code-editor--${language}`"` (`:61`) — the dead binding. Grep for `code-editor--` returns
  exactly one hit, this line.
- A hand-rolled Tab handler (`:40-54`) that inserts two spaces and restores the caret via
  `requestAnimationFrame`. This is the only editor behaviour the component has, and CodeMirror's
  `defaultKeymap` supersedes it.
- `idAttr`/`placeholderAttr` casts (`:32-33`) with a substantial comment (`:23-31`) explaining an
  `exactOptionalPropertyTypes` interaction and a deliberate choice to keep `:id` a literal binding so
  `vuejs-accessibility/form-control-has-label` can see it. **Read that comment before touching the
  template** — it encodes two gate interactions that are not obvious.
- The scoped style block (`:76-108`) styles `.code-editor`, `::placeholder`, `:focus`, `[readonly]`.
  All colours come from CSS custom properties (`--color-border`, `--color-surface`, `--color-fg`,
  `--font-mono`, `--focus-ring`).

**The six call sites** — `markdown` ×3, `json` ×2, `text` ×1, `python` ×0:

| Site | `language` | Notes |
|---|---|---|
| `AgentToolsView.vue:1101` | `json` | `v-model="configJson"`, error at `:1107-1110` |
| `AgentToolsView.vue:1209` | `json` | `v-model="fnParamsJson"`, error at `:1215-1218` |
| `AgentToolsView.vue:1142` | `text` | `readonly`, renders `failedResult.error` |
| `AgentDetailView.vue:919` | `markdown` | system prompt, `SCharCount` below |
| `SPromptAssistantConfigForm.vue:90` | `markdown` | system prompt |
| `SPromptTemplateManager.vue:173` | `markdown` | template body, `SCharCount` below |

**The JSON error path today.** `configJson` initialises to `'{}'` (`:245`) and is re-serialised from
the server on load (`:279`). On submit, `parseConfig(configJson.value)` (`:333`) fails and sets a
generic string (`:335`). `fnParamsJson` (`:474`) is the same shape via `JSON.parse` (`:561`), plus a
*schema* check (`:572`, `invalidParamsSchema`) that is distinct from the parse check and must survive.

**The budget gate.** `scripts/check-bundle-size.sh` gzips every `dist/assets/*.js` and compares:
`index-*`/`vendor-*` against `INITIAL_LIMIT=256000`; everything else against `LAZY_LIMIT=204800`
(`:7-8`, `:24-40`). `EXEMPT_PREFIXES='^(mermaid|hljs)-'` (`:15`) is checked at `:33`.

**The lazy-import exemplar.** `slices/conversation/utils/renderMarkdown.ts:113` —
`const hljs = (await import('highlight.js/lib/common')).default`, with `katex` at `:127` and
`mermaid` at `:150`. `vite.config.ts:37-43` carries a comment explaining that these are deliberately
*not* given manual chunks, because forcing them into named chunks made Rollup hoist the side-effectful
hljs chunk into a static entry import. **That comment is the reason the exempt list is dead** (§5) and
it is the pattern this task must copy: leave chunking automatic.

## 5. Design

### The measurement

Every number below is from an actual build, not an estimate. Method: a throwaway Vite project pinned
to the repo's `vite@6` (pnpm resolved `vite@8` first, whose esbuild-transpile split changes minifier
output — the number would not have been ours), `minify: 'esbuild'`, `target: 'es2022'`, gzipped and
byte-counted. The repo tree was not touched.

| Variant | Bytes gz | vs `LAZY_LIMIT=204800` |
|---|---|---|
| core + `lang-json` + `lint` — **what ships** | **150 911** | PASS, margin 53 889 B (**26.3%**) |
| core + `lang-python` | 160 483 | PASS, margin 44 317 B (21.6%) |
| **core + all 4 grammars + lint** | **258 515** | **FAIL, over by 53 715 B** |
| `basicSetup` + one grammar | 200 003 | PASS, margin 4 797 B (**2.3%**) |

"core" = `@codemirror/{view,state,commands,language}` with `lineNumbers`, `history`,
`syntaxHighlighting(defaultHighlightStyle)`, `keymap.of([...defaultKeymap, ...historyKeymap])`.

`pnpm audit --prod`: **no known vulnerabilities**. Packages added for the JSON build: `@codemirror/`
`{view,state,commands,language,lang-json,lint}` plus transitives `@lezer/{common,highlight,lr}`,
`style-mod`, `w3c-keyname`, `crelt` — 12, all from two orgs, no unrelated vendors.

Two of these rows are the point of the whole measurement, and neither is in FU-4:
- **`basicSetup` passes.** By 2.3%. A spec that said "it fits" and stopped there would have shipped a
  tripwire.
- **All four grammars together fail.** By 26%. The obvious implementation — import the grammars
  beside each other and switch on the prop — reddens CI, and only once the *fourth* one lands.

### Why the exempt list is dead, and why it matters here

`EXEMPT_PREFIXES='^(mermaid|hljs)-'` matches zero of the 206 emitted chunks:
- **mermaid** emits `mermaid.core-<hash>.js`. The regex requires a literal `-` immediately after
  `mermaid`; the real name has `.core` in between. No match.
- **hljs** never appears in a chunk name at all. `highlight.js/lib/common` lands in
  `common-<hash>.js` (verified: it is the only chunk containing the hljs Python grammar keyword
  `nonlocal` and the `highlightAuto` export; 50.9 KB gz). It is named after the *entry module*
  (`common`), and `vite.config.ts:38-43` deliberately declines to give it a manual chunk.

So every chunk in the build — including `mermaid.core` at 135.1 KB gz, `wardley` at 144.5, and
`cytoscape.esm` at 138 — passes the 200 KB lazy budget **on merit**. The exemption has never fired.
FU-4's concern that adding to it "requires a visible policy change" is moot: this task adds nothing to
it, and the dead list is filed as §16 FU-1 rather than fixed here (fixing it could redden CI for
mermaid, which is a separate decision with a separate owner).

### Options considered

1. **Minimal CodeMirror 6, dynamic grammar import — chosen.** 150 911 B gz, 26.3% margin, audit
   clean, live positioned lint. Cost: 12 prod packages; a real integration (theme, i18n, v-model,
   readonly, the `:id` accessibility binding at `:32`).
2. **`basicSetup` CodeMirror.** Rejected by Q-2: 2.3% margin for features we did not ask for.
3. **Reuse `highlight.js`, already a prod dependency.** Zero new packages; the `common-*` chunk
   (50.9 KB gz) already ships and already contains a Python grammar. But highlight.js highlights
   *static* markup — an editable version means the transparent-textarea-over-`<pre>` overlay trick,
   which must keep scroll, caret, selection, IME composition, and wrapping in sync by hand, and gives
   **no linting**, which is the actual defect being fixed. Rejected: cheaper in bytes, far more
   expensive in the bugs it invites, and it does not solve the problem.
4. **Do nothing; delete the dead prop.** Honest and free, and it was the pre-measurement
   recommendation. Rejected once the numbers came in: the JSON positioning defect is real and the
   blocker was imaginary.
5. **Monaco.** Not measured. An order of magnitude over budget and a known-bad fit for a small
   embedded field; ruled out without spending a build on it.

### Decision

Adopt **minimal CodeMirror 6**, one dynamic `import()` per grammar, JSON only for now, `basicSetup`
forbidden, textarea kept as the synchronous pre-hydration fallback.

## 6. Detailed Changes

1. **`package.json`** — add `@codemirror/{view,state,commands,language,lang-json,lint}` as prod
   dependencies. Pin exactly (the repo pins: `highlight.js@11.10.0`, `dompurify@3.4.11`). **Do not
   add the `codemirror` meta-package** — it is the `basicSetup` re-export Q-2 forbids, and having it
   in the tree is how someone imports it by accident.

2. **`shared/ui/SCodeEditor.vue`** — the substance:
   - Delete the dead class binding (`:61`).
   - Add `'python'` to the union only when a grammar backs it (not this task, per Q-3/§16).
   - Keep the `<textarea>` as the initial render (Q-6). Mount CodeMirror in `onMounted` after
     `await import('@codemirror/lang-json')` resolves, replacing the textarea. A field that renders
     nothing until a 150 KB chunk lands is a regression for the four sites that gain nothing from it.
   - `v-model` bridge: emit `update:modelValue` from an `EditorView.updateListener`; guard the
     inbound watch against echoing the editor's own change back into `dispatch` (the standard
     CodeMirror/Vue loop — compare against `view.state.doc.toString()` before dispatching).
   - `readonly` maps to `EditorState.readOnly` + `EditorView.editable.of(false)`; the `[readonly]`
     style (`:105-108`) must keep applying.
   - Delete the hand-rolled Tab handler (`:40-54`) **only** for CodeMirror-backed instances —
     `defaultKeymap` supersedes it. It stays for the textarea fallback path.
   - **Do not disturb `:23-33`.** That comment documents an `exactOptionalPropertyTypes` cast and a
     deliberate literal `:id` binding that `vuejs-accessibility/form-control-has-label` depends on.
     CodeMirror renders its own `contenteditable`, not an `<input>` — so the accessibility story
     changes and gate #11 must be re-verified, not assumed (AC-8).
   - Theme: a `Compartment` + `EditorView.theme` reading the same CSS custom properties the style
     block already uses (`--color-surface`, `--color-fg`, `--font-mono`, `--color-border`). The app
     switches theme via `data-theme` on `:root` (`useTheme()`), so the editor must follow without a
     remount.
   - `syntaxHighlighting(defaultHighlightStyle)` initially. A bespoke `HighlightStyle` mapped to the
     design tokens is §16 FU-3.

3. **`AgentToolsView.vue`** — `:1101` and `:1209` gain `linter(jsonParseLinter())` + `lintGutter()`
   via the component. The existing submit-time validation at `:333-338` and `:561-575` **stays**: it
   is the server contract's mirror, and `invalidParamsSchema` (`:572`) is a schema check the JSON
   linter cannot make. The linter adds positioned parse feedback; it does not replace either.

4. **i18n** — CodeMirror ships English strings (`phrases`). Any user-visible text it renders (lint
   panel, gutter tooltips) must route through `$t()` via its `EditorState.phrases` facet, or the
   feature must be configured off. Gate #12 does not lint inside a library, which is exactly why this
   needs stating: `2026-07-16-agent-skills` FU-5 was this same gap and shipped English `aria-label`s
   to a zh-TW product for months.

**Reuse inventory:**
- `renderMarkdown.ts:113` — the `await import()` lazy-library pattern, verbatim.
- `vite.config.ts:37-43` — the "leave chunking automatic" rule and *why*; do not add a manual chunk.
- `useTheme()` (`shared/composables/`) + the `--color-*` tokens — the theme source.
- `INPUT_LIMITS` / `SCharCount` — unchanged, still wrap the editor at the three markdown sites.
- `SFormField` — the existing label/error wrapper at every call site.

## 7. NFR Checklist

- **Bundle (gate #9, `[R24.48]`):** the CodeMirror chunk is 150 911 B gz against 204 800. AC-6 pins
  the margin as an assertion, not a hope. The initial bundle is untouched (Q-6).
- **Performance:** the editor mounts after a dynamic import on a form the user has already navigated
  to; no blocking work on startup or on any hot path. Six textareas become at most two editors.
- **Accessibility (gate #11):** changes shape — see AC-8. This is the highest-risk NFR here.
- **Type coverage (gate #10, ≥95%):** CodeMirror ships its own types; no `any` should be needed.
- **i18n (gate #12):** see §6.4.

## 8. Security Considerations

Thin but not empty, and one item is real.

**The real one: CodeMirror renders into a `contenteditable`, and the content is user-controlled.** It
is *not* an HTML sink — CodeMirror builds DOM from the document text as text nodes and never parses it
as markup — so this is not a `v-html` situation and **must not** be added to the `vue/no-v-html`
allowlist (`eslint.config.js:229-247`), whose comment demands a security review for exactly that move.
FU-4 raised the markdown-preview idea, which *would* need that allowlist entry; this task
deliberately does not do it (§16 FU-4). The implementer should verify no CodeMirror extension in the
chosen set renders HTML from document content — `lint` renders diagnostic *messages*, which come from
`jsonParseLinter` (the JS engine's own parse error), not from the user.

**Supply chain (dimension 13a).** 12 new prod packages. `pnpm audit --prod`: clean. All from
`@codemirror`/`@lezer`, both maintained by the same upstream (Marijn Haverbeke), widely deployed. No
typosquat risk in the names as long as the meta-package `codemirror` is *not* added (§6.1). The
implementer should re-run `pnpm audit --prod` at implementation time — this measurement is from
2026-07-17 and advisories are not static.

**Not in scope, and not a new exposure:** the JSON these fields carry is MCP tool config and function
parameter schemas, already validated server-side; the editor changes how it is typed, not what is
accepted. No new endpoint, no AuthZ surface, no key path, no agent-visible context.

## 9. Quality Notes

**Existing debt in the touched files — do not imitate, and do not silently "fix":**
- The dead class binding (`SCodeEditor.vue:61`) — this task deletes it. That *is* the fix.
- `AgentToolsView.vue` is ~1200 lines with `configJson`, `fnParamsJson`, and their error refs spread
  across `:224-338` and `:474-581`. It is not this task's job to decompose it, and doing so would
  bury the editor change in unrelated churn. Leave it; §16 FU-5.
- The hand-rolled Tab handler (`:40-54`) is a symptom of the textarea, not a defect — it goes away
  with the textarea on CodeMirror-backed instances and stays on the fallback path.

**Patterns to follow:** `renderMarkdown.ts`'s dynamic import; `vite.config.ts`'s automatic chunking;
the `--color-*` token discipline in the existing style block; `shared/ui` imports only from `shared`
(gate #1 — CodeMirror is a package, so no boundary issue).

## 10. Risks and Rollback

- **The four-grammar cliff (Q-5) is the one that will actually bite.** Static-importing the grammars
  together is 258 515 B gz and fails by 26%. It fails only when the *fourth* lands, so the person who
  breaks CI will be the person who added `lang-markdown` and has no idea the constraint existed.
  AC-7 is the guard; the code comment demanded there is the real mitigation.
- **`basicSetup` re-entering by the back door.** Anyone reading CodeMirror's docs will reach for it
  first; it passes locally (2.3%) and reddens later. Mitigation: the meta-package is not in
  `package.json` (§6.1), so `import {basicSetup} from 'codemirror'` fails to resolve rather than
  silently costing 49 KB.
- **Accessibility regression (AC-8).** The `<textarea>` had a real label association via the literal
  `:id`. A `contenteditable` does not. This is the most likely way this task ships something worse
  than it replaced — and it is invisible to sighted review, which is exactly how FU-5 survived.
- **v-model echo loops.** Standard CodeMirror/Vue hazard; §6.2 names the guard.
- **Rollback:** the component is the single seam. Reverting `SCodeEditor.vue` restores the textarea at
  all six sites with no call-site changes, because the prop contract is unchanged. Removing the
  dependencies is a second, independent commit. Nothing persists; no migration; no data.

## 11. Acceptance Criteria

- [ ] AC-1: `AgentToolsView.vue:1101` and `:1209` render a CodeMirror editor with JSON highlighting;
      the other four call sites render exactly as they do today.
- [ ] AC-2: typing malformed JSON in either field surfaces a positioned diagnostic (gutter marker at
      the offending line) **without submitting**.
- [ ] AC-3: the existing submit-time validation is unchanged — `configJsonError` (`:335`),
      `fnParamsError` (`:563`), and the distinct `invalidParamsSchema` (`:572`) all still fire on
      their existing conditions, with their existing i18n strings.
- [ ] AC-4: `v-model` round-trips — editing updates the ref, and a programmatic write (`:251`,
      `:279`, `:485`, `:514`) updates the editor without an echo loop or a lost caret.
- [ ] AC-5: `readonly` (`:1142`) still renders non-editable and still carries the `[readonly]` style.
- [ ] AC-6: `pnpm run check:bundle-size` passes, and the CodeMirror chunk is asserted **under
      180 000 B gz** — a deliberate 25 KB below `LAZY_LIMIT` so the margin is defended, not merely
      observed. A test or script comment records the 2026-07-17 measurement (150 911 B) as the
      baseline.
- [ ] AC-7: each grammar is reached by its own dynamic `import()`; no module statically imports two
      grammars. A comment at the import site states the measured reason (258 515 B gz / FAIL by
      26% for all four together), so the next person to add one sees the constraint.
- [ ] AC-8: **accessibility is re-verified, not assumed.** `pnpm lint` (gate #11) is green, and the
      editor is confirmed reachable and labelled — the `<textarea>`'s label association came from the
      literal `:id` binding documented at `SCodeEditor.vue:23-33`, and a `contenteditable` does not
      inherit it. If gate #11 cannot see the new markup, that is a finding, not a pass.
- [ ] AC-9: no user-visible English ships from CodeMirror (§6.4) — any library-rendered string is
      routed through `$t()` or the feature is off.
- [ ] AC-10: `codemirror` (the meta-package) is absent from `package.json`; `basicSetup` appears
      nowhere in `src/`.
- [ ] AC-11: `pnpm audit --prod` clean at implementation time.
- [ ] AC-12: gates green — `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build`,
      `pnpm run check:bundle-size`, `pnpm run check:type-coverage`.

## 12. Test Plan

- **Component (`shared/ui/__tests__/SCodeEditor.spec.ts`)** — v-model round-trip (AC-4), readonly
  (AC-5), the programmatic-write-without-echo case (AC-4). CodeMirror needs a real DOM; Vitest runs
  jsdom — if `contenteditable` behaviour proves untestable there, say so in the Deviation Log and
  cover it in e2e rather than deleting the assertion.
- **View (gate #8)** — `AgentToolsView` has an existing test file; extend it for AC-1/AC-3 rather
  than starting a new one.
- **Bundle (AC-6/AC-7)** — `check:bundle-size` is the gate; the sub-180 KB assertion needs a home.
  The script is bash and CI-only; a Vitest test that reads `dist/` would only run after a build.
  The implementer should choose and record the choice — a comment in `check-bundle-size.sh` naming
  the measured baseline is the minimum.
- **Regression** — the three markdown sites and the readonly site must be exercised to prove AC-1's
  "unchanged" half. These are the sites with no upside and all the risk.

## 13. SRS Delta

**None.** `[R24.48]` (bundle budget) and `[R24.15]` (the 12 CI gates) already constrain this work and
are satisfied as-is — the budget is met with a 26.3% margin and no gate is modified, weakened, or
exempted. No requirement states what `SCodeEditor` must render, and this task does not need one:
"the editor highlights syntax" is an implementation quality of an existing UI, not a new platform
capability. The exempt-list finding (§5) is a defect in a *script*, not a requirement change — §16
FU-1.

## 14. Open Questions

- **OQ-1: does jsdom support CodeMirror well enough for gate #8?** CodeMirror needs layout
  measurement, which jsdom does not do. This determines whether AC-1/AC-2 are unit-testable or e2e
  only. It does not change the design, so it is not a blocker — but the implementer will hit it in
  the first hour and should not be surprised.
- **OQ-2: how does the lint gutter interact with `SFormField`'s error slot?** Two error affordances
  in one field (the gutter marker and the existing `<p class="text-danger">` at `:1107`) may read as
  duplication. Worth one design look before building; the answer might be that the submit-time
  message moves or shortens once the position is shown inline.

## 15. Deviation Log

_None yet._

## 16. Follow-ups

- **FU-1: the bundle-size exempt list is dead code and has never fired.**
  `EXEMPT_PREFIXES='^(mermaid|hljs)-'` (`check-bundle-size.sh:15`) matches **zero of 206 chunks**:
  mermaid emits `mermaid.core-*` (the regex needs a literal `-` after `mermaid`), and highlight.js
  emits `common-*` because `vite.config.ts:38-43` deliberately leaves it unnamed. Every chunk passes
  the 200 KB budget on merit, so the exemption is not load-bearing today — but it is a comment
  claiming a policy the code does not implement, and FU-4 built an argument on it. **Deleting it is
  not obviously safe**: it would leave mermaid (135.1 KB gz) passing only by 32%, and it is a
  deliberate escape hatch someone may want to actually work. The decision — fix the regex, delete the
  list, or document it as vestigial — belongs to whoever owns gate #9.
- **FU-2: `basicSetup` passes the gate by 2.3% and nothing says so.** Measured 200 003 B gz vs
  204 800. Q-2 forbids it *in this task*, and AC-10 enforces it *in this repo* — but the number is
  worth carrying: it means the per-chunk budget is roughly one CodeMirror-with-batteries wide, which
  is useful context for any future library decision, not just this one.
- **FU-3: `defaultHighlightStyle` is not the design system.** §6.2 ships CodeMirror's stock palette,
  which will not match the light-blue/grey token set. A bespoke `HighlightStyle` mapped to
  `--color-*` is a small, self-contained follow-up with a visible payoff.
- **FU-4: markdown preview still needs a security review, and this task did not do one.** FU-4's
  other half — rendering a preview beside the prompt editors — requires adding a file to the
  `vue/no-v-html` allowlist (`eslint.config.js:229-247`), whose comment demands exactly that review.
  Nothing here touches it; the allowlist is untouched (§8). Filed so the idea is not silently lost
  with FU-4's closure.
- **FU-5: `AgentToolsView.vue` is ~1200 lines.** `configJson`/`fnParamsJson` and their error refs span
  `:224-338` and `:474-581`, and the file holds several unrelated panels. Not this task's scope (§9),
  but the next person to add a field there will feel it.
- **FU-6: the three markdown editors gained nothing and were not migrated (Q-3).** If markdown
  highlighting in a system-prompt box turns out to be wanted, the grammar is measured and the seam
  exists — but note Q-5: `lang-markdown` must arrive by its own dynamic import, and the four-grammar
  total (258 515 B gz) fails the gate if it does not.
