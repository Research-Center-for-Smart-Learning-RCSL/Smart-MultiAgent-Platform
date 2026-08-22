---
type: refactor
status: implemented
created: 2026-08-22
requirements: [R24.28, R24.48, R24.49]
depends_on: []
---

# Visual refinement phase 3: see what phase 2 shipped, and close its debt

## 1. Summary

`2026-08-21-visual-refinement-phase2-identity-and-depth` changed the product's visual
identity — a new typeface, a new neutral axis, a third surface role, three rule weights,
a press language — and closed with **eleven of sixteen acceptance criteria ticked, three
partial and two open**. It was measured thoroughly and *seen* barely at all: no overflow
pass at any viewport in any locale, no keyboard traversal of the surface set looking for a
mismatched focus ring, and nobody opened the landing page. Its own §15 says so.

This dossier owns what it left: the parity baseline that keeps CI red, the visual
verification that was never performed, and six recorded follow-ups. It is deliberately
mostly *verification* rather than change — the largest item alters no code at all if it
finds nothing, and any defect it does surface becomes its own numbered entry here rather
than a silent fix.

Nothing lists phase 2 in `depends_on` (`2026-08-22-safe-area-uncovered-top-surfaces`'s Q-6
records the file-overlap check that justifies its empty list), so this dossier unblocks
nothing and blocks nothing. `depends_on: []` is a positive claim: phase 2 is implemented
and every predecessor is closed.

## 2. Goals and Non-goals

**Goals**

- CI is green. The one red job has a single, fully-diagnosed cause.
- A human has looked at the product phase 2 shipped, at both viewports, in both locales,
  in both themes, and the result is written down per surface rather than as a tick.
- Phase 2's FU-7 through FU-12 are each either fixed or converted into a decision.

**Non-goals**

- **No new visual identity work.** No token value moves unless the verification proves it
  wrong. This dossier does not continue phase 2's design; it audits it.
- **No new component, no new prop.**
- **No landing-page or wordmark redesign.** Phase 2's FU-5 (the product has no display
  face and no logotype) stays out of scope and stays open.
- **No rebuild of the developer's local stack as a side effect.** The parity work needs a
  pristine stack (§6.1) and that requirement is stated up front rather than discovered.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Phase 2 closed as `implemented` with AC-3 and AC-16 open. Is that being reversed here? | **No.** Phase 2 stays `implemented`; this dossier owns the remainder. | The scope call was the user's, taken with the gaps recorded rather than hidden. Re-opening a closed dossier to finish it would make its `implemented` mean "mostly", which is exactly the erosion the status lifecycle exists to prevent. The debt is visible here instead, with an owner. |
| Q-2 | Is the parity rebaseline a task, or just a command someone runs? | **A task, and the first one.** | It is the only thing making CI red, and phase 1's D-17/D-18 established it is only meaningful against a freshly seeded stack. Run casually against a saturated stack it produces a file that is green locally and red on CI — which is how it got here. It needs the stated preconditions, not a shell line. |
| Q-3 | Should the visual pass be automated instead? | **No, and this is not a concession.** | The judgements are "does a label truncate that did not before", "is there a halo belonging to neither surface", "does this read as layered". A screenshot-diff harness would answer a different question — *did anything change* — and phase 2 changed everything by design, so it would be noise. Phase 2 already automated everything that has a threshold (contrast, L\* separation, border ordering, the press transform). What is left is what only a person can answer. |
| Q-4 | FU-10 asks whether `--control-h-*`, `--sidebar-width` and `--topbar-height` should follow `--space-*` onto rem. Decide it here? | **Decide it; implement only if the answer is yes.** | The question is real (a 260px sidebar holding rem text truncates sooner as the reader's font grows) and it has a different answer per token — a control-height *floor* is not a track *width*. Leaving it a third time would make it permanent by default. |
| Q-5 | FU-11 is an intermittent test unrelated to phase 2's diff. In scope? | **Yes.** | An intermittent failure is a future red CI nobody can attribute, and it was observed during phase 2's build (its §16). Whoever sees it next will pay for it whether or not it is anyone's fault. |

## 4. Current State

### 4.1 CI

Run `32561272600` (phase 2's first push): 22 of 23 jobs green. `frontend-e2e` ran 145
tests and reported 21 failures, **all 21 in `e2e/00-visual-token-parity.spec.ts` and no
other spec**, each an `AC-1: no rendered difference on "<surface>"`. That spec compares a
computed-style baseline captured before phase 2 against a product phase 2 deliberately
restyled: `--shadow-*`, the `@layer base` heading ramp, `SCard`'s default elevation and
`STable`'s cell padding all moved, and all four are in the spec's property set
(`00-visual-token-parity.spec.ts` PROPS).

The baseline is `e2e/baselines/visual-token-parity.json`. It covers 20 surfaces.

### 4.2 What was never looked at

Phase 2's §15 records this in full. In summary: AC-3 not performed at all; AC-5's
traversal covered `/login` only and the halo judgement was never made; AC-6's visual half
(card border removed in devtools, landing page, auth pages, chatroom) was never made;
AC-4's reduced-motion behaviour is reasoned from the global freeze, not observed.

### 4.3 The CSP gap

`playwright.config.ts:18` sets `baseURL: 'http://localhost:5173'` — Vite, which sets no
CSP header — and CI uses the same config. `deploy/compose/nginx/conf.d/smap.conf:177`
carries `font-src 'self' data:`. Nothing in this repository runs the frontend behind that
config, so the font being admitted is proven by construction (same origin, verified) and
never observed.

### 4.4 Debt phase 2 recorded

- `isFigureColumn` is duplicated at `STable.vue:181` and `STableCards.vue:43`, three lines
  each, deliberately (the mobile branch does not import the table's type).
- `main.css:165-167` and `:279-281` keep `--sidebar-width`, `--topbar-height` and
  `--control-h-*` in px while `--space-*` and the type ramp are rem.
- `SButton.vue:150`'s `box-shadow: var(--elevation-0)` resolves to `none` and `.s-btn`
  declares no resting shadow, so the press's "one elevation step down" is currently inert;
  only the 1px translate at `:149` is expressed.
- `src/slices/agents/__tests__/AgentToolsView.test.ts` failed once under the full suite
  (~6.4s, consistent with a timeout) and passed in isolation and on two later full runs.

## 5. Design

### Options considered

**Option A — one dossier, verification first.** Rebaseline, then the visual pass, then the
debt items, in that order. Trade-off: the debt items are trivial and would ship faster on
their own, but they touch the same files the visual pass is judging, and doing them first
means judging a surface that has already moved again.

**Option B — split verification from debt.** Two dossiers. Trade-off: honest separation of
"look at it" from "change it", but the visual pass is the thing most likely to *produce*
debt items, so the second dossier's scope is unknowable until the first finishes.

### Decision

**Option A**, ordered. The verification runs against exactly what phase 2 shipped, before
anything here perturbs it; whatever it finds is appended as a numbered item and fixed in
the same dossier, which is what keeps a found defect from becoming a third phase.

Consciously given up: speed on the four small debt items, which could each land in an hour
and will instead wait behind a manual pass.

## 6. Detailed Changes

- **Backend** — none. **API contract** — none. **Migration** — none. **Deploy/config** —
  none, unless §6.3 concludes a CI job should put nginx in front, which is a workflow
  change and would be specified before being made.

### 6.1 The parity baseline (closes phase 2's AC-16)

Preconditions, all three, none optional:

1. A stack brought up per `frontend/.claude/skills/verify/SKILL.md` ("Bringing up the full
   test stack") against a **freshly created** `smap_test`, not a developer stack.
   `deploy/compose/compose.test.yml` binds `28000:8000` and reuses the base compose
   project's container names, so this displaces a running dev stack — plan for it.
2. `e2e/.e2e-seed.json` confirmed to hold the full `E2E_*` set before trusting anything.
   `global-setup.ts` is not idempotent and fails **silently** against an already-seeded
   stack, writing a three-key file that makes every fixture-gated spec skip — a green run
   with no coverage.
3. Then `UPDATE_VISUAL_BASELINE=1 pnpm exec playwright test 00-visual-token-parity
   --project=desktop`, and the regenerated file committed.

Then the harness is self-checked the way phase 1 self-checked it (its D-11): capture, then
immediately re-run the comparison against unmodified code. A baseline that does not
compare clean against the code it was captured from is describing something other than the
code.

### 6.2 The visual pass (closes phase 2's AC-3, and AC-5/AC-6's open halves)

Over the 20 surfaces of the phase-1 C-0 set, at 1440x900 and 375x812, in `en` and `zh-TW`,
in light and dark. Recorded **per surface** in §15, not as a single tick — the result of a
judgement is the judgement, and "all fine" over 20 surfaces is not a record.

What is being looked for, specifically, because "does it look right" is not a test:

- **Truncation and wrap.** Inter runs wider than Segoe UI at the same nominal size. The
  three exposures phase 2's §10 named: sidebar nav labels against `--sidebar-width: 260px`
  (`SidebarNavItem` ellipsises), `STable` headers with `white-space: nowrap`, and `SBadge`
  labels against their pill. Any label truncated that was not truncated before is a finding.
- **The focus ring on every backdrop it can appear over.** The ring was replaced precisely
  because the old one painted a `--color-bg` halo regardless of what was behind it. So:
  a control inside the sidebar, inside a card footer, inside a table header, inside a modal
  footer, and inside a dropdown (where the ring is inset). Keyboard only.
- **Layering.** An `SCard` at the default variant with its border removed in devtools —
  does it still read as a sheet on the canvas, in both themes.
- **The surfaces nobody opened**: the landing page (phase 2's §10 called it the highest
  risk and the least likely to be opened), the auth pages, the chatroom.
- **Reduced motion.** The press under `prefers-reduced-motion: reduce`: it must become an
  instant change, not disappear.

### 6.3 FU-8 — the CSP path

Decide and record: either a CI job that puts `smap.conf` in front of the built frontend and
re-runs the font assertions, or an explicit statement that this closes on a staging check
with a named owner. What is not acceptable is a third dossier inheriting "proven by
construction".

### 6.4 FU-9 — the duplicated predicate

Either accept the duplication with a comment on each side naming the other, or lift the
predicate to a shared module. The deciding question is whether a third consumer is
plausible; if not, two commented copies beat an import that re-couples the mobile branch.

### 6.5 FU-10 — px versus rem, per token

Decide each of `--control-h-*`, `--sidebar-width`, `--topbar-height`. They are not one
question: a control height is a *floor* that a growing font already overruns harmlessly,
while a sidebar width is a *track* that a growing font truncates against.

### 6.6 FU-12 — the inert half of the press

Either give the button variants a resting elevation so "one step down" means something, or
drop `box-shadow` from the press rule and from `.s-btn`'s transition list, and correct the
motion language in `main.css` and `docs/UI/01-design-system.md` to describe what ships.

### 6.7 FU-11 — the intermittent test

Reproduce under load, then fix the cause. A raised timeout is acceptable only with the
measurement that justifies it.

### 6.8 i18n

No new user-facing string. The `zh-TW` half of §6.2 is a rendering check, not a content
change.

## 7. NFR Checklist

- [ ] **i18n** — no new strings (§6.8). But `zh-TW` is half of §6.2's matrix and is the
      locale most likely to reveal a metric problem, because `text-transform` is inert on
      CJK and the CJK families were only named in the font stack by phase 2.
- [ ] **Audit log** — N/A. No domain event.
- [ ] **Tenant isolation** — N/A. No endpoint touched.
- [ ] **Error handling UX** — N/A unless §6.2 finds a broken state surface, in which case
      it becomes a numbered finding here.
- [ ] **Performance** — N/A. Nothing here changes the payload; phase 2's §15 records it.

## 8. Security Considerations

None beyond §6.3, which is a *verification* gap rather than a vulnerability: the CSP is
unmodified (`smap.conf:177`) and the font is same-origin, which is the only property that
clause turns on. No auth, no provider keys, no tenant boundary, no WebSocket, no upload,
no user-input processing, no agent or prompt surface.

## 9. Quality Notes

**Patterns to follow**

- `frontend/src/shared/styles/__tests__/contrast.test.ts` — the shape phase 2 settled on
  for anything with a threshold: parse the source of truth, assert, and report the measured
  table rather than trusting a review.
- Phase 2's D-11 for the failure mode to watch: a budget that measures a token against one
  background cannot see a collision with any other. If §6.2 finds a colour problem, the fix
  includes the assertion that would have caught it.

**Existing debt — record, do not imitate, do not silently fix**

- `SBadge.vue` sets `min-width: 44px; min-height: 44px` and then `unset` four lines later,
  with a `::before` supplying the real touch target; `SInput`'s eye toggle does the
  equivalent. Phase 2's FU-3, still open, still not this dossier's property set.
- `AppShell.vue` hard-codes `transition-delay: 300ms` where `--duration-slow` exists.
  Phase 2's FU-4, and `2026-07-05-sitewide-ui-enhancement`'s FU-10 before it. Two dossiers
  have now declined it.

**Reuse inventory**

`frontend/.claude/skills/verify/SKILL.md` is the handle for §6.2 and carries the traps that
have already cost time: `networkidle` never fires because the app holds live sockets,
`toBeVisible()` does not mean "finished animating", tab panels are `v-show` so `.last()`
can pick a hidden element, and the AUTH bucket is 10 req/min/IP.

## 10. Risks and Rollback

- **§6.1 displaces the developer's running stack.** Stated in Q-2 and §6.1 rather than
  discovered. The recovery is bringing the dev stack back up; volumes survive if it is
  torn down with `down`, never `down -v`.
- **§6.2 may find nothing, and that is a real outcome.** It should be recorded as such in
  §15 per surface. A verification dossier that finds nothing has still moved the product
  from "unverified" to "verified", which is the whole point of it existing.
- **§6.2 may find a lot.** Inter's metrics touch every screen. If the findings are broad
  rather than a handful, that is the signal to stop fixing inline and open a fourth
  dossier with the list, rather than let this one sprawl.
- **Rollback**: no migration, no API change, no persisted state. The baseline commit is
  revertible on its own; each debt item is a separate commit.

## 11. Acceptance Criteria

- [x] AC-1: **CI is green.** `frontend-e2e` passes, including `00-visual-token-parity`
      against a baseline regenerated on a pristine stack, and the run id is recorded in §15.
- [x] AC-2: **The baseline describes the code it was captured from.** Immediately after
      capture, an unmodified re-run compares clean (phase 1's D-11 self-check), and that is
      stated in §15 rather than assumed.
- [x] AC-3: **The visual pass is performed and recorded per surface.** All 20 C-0 surfaces,
      1440x900 and 375x812, `en` and `zh-TW`, light and dark. §15 carries one line per
      surface with the result, not a single tick. Phase 2's AC-3 is quoted as closed by this.
- [x] AC-4: **No truncation regression**, or every regression found is listed with the
      element, the viewport, the locale and the fix. Specifically checked: sidebar nav
      labels, `STable` `nowrap` headers, `SBadge` pills.
- [ ] AC-5: **The focus ring is correct on every backdrop it can appear over** — sidebar,
      card footer, table header, modal footer, dropdown — in both themes, by keyboard, with
      no halo belonging to neither surface. Closes phase 2's AC-5's open half.
      **Unticked on purpose**: the card footer was closed after the fact (§15 "FU-5,
      resolved"), leaving only the dropdown unobserved. See §15 AC-5 and FU-6.
- [x] AC-6: **Layering survives without its border.** An `SCard` at the default variant,
      border removed in devtools, still reads as a sheet in both themes. The landing page,
      the auth pages and the chatroom are opened and are not visibly broken. Closes phase
      2's AC-6's open half.
- [x] AC-7: **The press survives reduced motion.** Under `prefers-reduced-motion: reduce`
      the pressed state is an instant change with no movement — observed, not reasoned.
- [x] AC-8: **FU-8 is decided**, and the decision is either implemented (a CI job with
      nginx in front) or recorded with a named owner and a date. "Proven by construction"
      is not an acceptable close.
- [x] AC-9: **FU-9, FU-10 and FU-12 are each fixed or decided in writing**, with the
      reasoning in §15. FU-10 is decided per token, not as one answer.
- [ ] AC-10: **FU-11 no longer reproduces under load**, with the measurement that shows it.
      **Unticked on purpose**: it never reproduced here, so "no longer" cannot be claimed.
      The measurement exists and the change is defensive. See §15 AC-10.
- [x] AC-11: gates green on CI: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`,
      `check:bundle-size`, `check:type-coverage`, `check:boundaries-enforced`. Backend
      gates N/A. CI is authoritative over the local Windows host.
- [x] AC-12: **Any defect §6.2 surfaces is a numbered entry here**, fixed with a test where
      a test can express it, and never fixed silently.

## 12. Test Plan

- **AC-1, AC-2**: the `frontend-e2e` job, plus the local self-check of §6.1.
- **AC-3 to AC-7**: manual, via `frontend:verify`. These are judgements; §15 records the
  result per surface rather than an assertion. Where a finding *can* be expressed as a
  test — a truncation with a measurable width, a missing outline — it gets one under
  AC-12, because the next person should not have to look again.
- **AC-8**: N/A if the decision is "staging"; a CI job otherwise.
- **AC-9**: unit, extending phase 2's source sweeps where the decision is mechanical
  (a dropped `box-shadow` is assertable in `focus-and-press.test.ts`, which currently pins
  it in place).
- **AC-10**: repeated full-suite runs, not a single green.
- **AC-11, AC-12**: the full gate run per commit and at dossier end.

## 13. SRS Delta

None expected. [R24.28], [R24.48] and [R24.49] are listed because §6.5 and §6.6 could
amend them: FU-10 would change what the token vocabulary means by "control height", and
FU-12 would change [R24.49]'s pressed-state sentence to describe what actually ships. If
either lands, the amendment is drafted here verbatim before it is applied.

## 14. Open Questions

- Whether §6.2 finding a broad class of metric problems should convert this dossier into a
  findings artifact under `docs/audits/` with its own hand-off, rather than fixing inline.
  Decided when the pass runs, not before.

## 15. Deviation Log

### Deviations

**D-1 — the build order was inverted: the baseline was captured last, not first.**
§5 chose Option A "rebaseline, then the visual pass, then the debt items". FU-12's
approved close (D-3) gives the filled button variants a resting `box-shadow`, and
`box-shadow` is in the parity spec's `PROPS` — so a baseline captured first would have
been invalidated by the last step and needed a second pristine stack. Capturing once, at
the end, also makes AC-2's self-check apply to the code that ships rather than to an
intermediate state. The risk Option A was protecting against — spending the whole budget
before learning whether the harness works — was retired instead by a diagnostic dry run
of `00-visual-token-parity` in compare mode on a fresh stack before anything else began.
It reproduced CI exactly (21 failed, 1 passed, all `AC-1: no rendered difference`), which
established that the harness was healthy and the 21 failures had a single cause.

**D-2 — the six defects §6.2 found are fixed here rather than handed to a fourth
dossier.** §10 and §14 both anticipated this fork and left it to be decided when the pass
ran. It ran, and the finding was six defects with five root causes, all at 375px, all
predating phase 2. That is a coherent class — "the narrow viewport was never looked at" —
rather than the broad metric problem §10 feared, and the user's call was to close it here.
AC-12 is therefore read as written rather than amended.

**D-3 — FU-8 and FU-12 were decided at approval, not left to §6.3/§6.6.** FU-8 closes as
a CI job with nginx in front (§6.3's first option). FU-12 closes by giving the filled
variants a resting elevation (§6.6's first option) rather than by dropping the inert
`box-shadow`. The second is the one with visible consequences: every primary, secondary
and danger button now rests at `--elevation-1`. It sits close to the non-goal "no new
visual identity work", and was taken with that tension stated.

**D-4 — V-7 (a table header operable only by pointer) was fixed here although it is an
accessibility defect rather than a visual one.** It was found while trying to satisfy
AC-5: one of the five backdrops it names is a table header, and there was nothing there
to focus. Out of scope on a strict reading, taken in scope by the user's call, and it is
what made AC-5's table-header case testable at all.

**D-5 — truncation was measured as well as looked at.** Q-3 rejected a screenshot-diff
harness, correctly. It did not rule out measuring the one thing in §6.2 that has a
threshold: `scrollWidth > clientWidth` is the browser's own statement that content was
clipped, and it is exact where 168 eyeballed screenshots are not. The judgements Q-3
reserved for a person — halo, layering, "does this read as a sheet" — were made from
screenshots. The mechanical half found V-1, V-2, V-3 and V-5; the visual half found V-4
and V-6, which wrap rather than clip and are therefore invisible to the measurement. Both
halves were needed, which is the argument for doing both.

**D-6 — four post-close fixes from the code review of this batch, and one decision to
leave something alone.** Recorded here rather than folded into the narrative above,
because three of them are corrections to this dossier's own work.

- **V-7 shrank the sort target and I shipped it.** Moving `@click` from the `th` onto the
  button left the cell's `--space-4` gutters dead, so a click aimed just left of a short
  header stopped sorting. Keyboard access did not require giving up the pointer target:
  the cell keeps its handler and the button stops the event rather than bubbling into a
  second sort, which would toggle the order twice and read as the header doing nothing.
  Both paths and the no-double-sort guarantee are asserted now. Sizing the button to fill
  the cell was the alternative and was rejected — an inline-flex box at `width: 100%`
  stops following the cell's `text-align`, which would left-align every right-aligned
  column's header.
- **The CSP job ran the wrong nginx.** `1.31.3-alpine` is what `frontend/Dockerfile` uses
  to serve static files *behind* the edge; the edge that emits the header ships
  `1.27.3-alpine`. Observing the header under a server two mainline releases newer
  weakened the claim the job's first assertion is built on. Corrected and re-run locally
  under 1.27.3: identical header, spec green.
- **`PLAYWRIGHT_CSP_BASE_URL` was documented as set by CI and was not.** The run depended
  on the config's fallback happening to match a port chosen in the workflow. The job sets
  it explicitly now.
- **The landing clip bounds more than its comment claimed.** `overflow-x: clip` on `.hero`
  also clips `.hero__bg` (`inset: -40px -24px 0`) at every width, not only the
  constellation bleed. Kept rather than narrowed: its `mask-image` has already faded those
  regions to transparent, and before/after captures at 1440x900 in both themes show no
  difference at either edge. The comment now describes what the rule does.
- **`SPageHeader`'s wrapped action row stays left-aligned.** It was raised that this
  silently changes alignment at desktop widths too — any header whose actions no longer
  fit beside a 16rem title wraps, not only at 375px. True, and the user's call was to keep
  it: flush-left under the title is what the 375px capture shows reading well, and
  `margin-left: auto` would buy desktop consistency at the cost of mobile buttons floating
  right of a left-aligned title. Considered, not overlooked.

### FU-5, resolved — the card footer has no consumer, and it is correct anyway

**FU-5's own premise was wrong, and it was mine.** It said "four views pass a `#footer`
slot to `SCard`". They do not. That claim came from a grep for files containing both
`<SCard` and `#footer`, which is a co-occurrence test, not an ownership test. Checked by
line number, every one of those footers belongs to an `SModal`:
`KnowmapDocumentsTab.vue:217` inside `SModal` 189-234, `RagDocumentsTab.vue:243` inside
215-260, `AgentToolsView.vue:1162/1202/1348` inside 1076-1179 / 1182-1212 / 1215-1365, and
`WorkspaceListView.vue:280` inside 259-297.

Widened to the whole tree: **all 30 `#footer` sites in `src/` belong to a modal, and the
string `s-card__footer` appears only in `SCard.vue` itself and `SCard.test.ts`.** So the
slot has zero consumers in the application. AC-5's card-footer backdrop was not
"uncovered" — it could not occur.

That makes a second thing true that is worth writing down: `main.css:46-51` introduces the
three surface roles and names "card footer" as its example of the recessed
`--color-surface` role. **The one illustration of that role has never been on screen.**

**Rendered once before deciding.** A temporary `#footer` carrying a real button was
mounted on the Hosted Tools card of `/agents/:id/tools`, photographed in both themes, and
removed. It is correct:

| | canvas | card | footer |
|---|---|---|---|
| light | `rgb(241, 245, 249)` | `rgb(255, 255, 255)` | `rgb(248, 250, 252)` |
| dark | `rgb(8, 13, 22)` | `rgb(15, 23, 42)` | `rgb(30, 41, 59)` |

The footer is recessed into the sheet in light and lighter than it in dark, which is what
`--color-surface` means in each theme. The `border-top` divides without competing with the
card's own edge, the bottom corners follow `--radius-lg`, and the negative margins bleed it
to the card's edges with no gap and no overflow.

**AC-5's card-footer case is therefore closed by observation**: the button inside took
`outline: solid 2px` at `2px` offset — `rgb(37, 99, 235)` light, `rgb(96, 165, 250)` dark —
with no halo, reached by keyboard. Four of AC-5's five named backdrops are now observed;
only the dropdown (FU-6) remains, which is why AC-5 stays unticked.

**Decision: keep the slot.** Header/footer is the conventional API for a card primitive,
and deleting it would leave the next person to reinvent one — possibly without the surface-
role vocabulary this one already follows correctly. `SCard.vue` now carries the finding
beside the rule, so a reader cannot mistake "styled" for "exercised".

### AC-1 and AC-11 — CI

**Run `32574448737`, at `ee2693f`: green, all 23 jobs.** `frontend-e2e` passes, so
`00-visual-token-parity` now compares clean against the regenerated baseline, and
`25-narrow-viewport-layout` passes on CI's own data. The new `frontend-csp-font` job
passes there too, which is what makes AC-8 an observation rather than a local anecdote.

The run this dossier inherited (`32563605043`, at `4a0f0de`) was red on `frontend-e2e`
alone: 21 failures, every one `AC-1: no rendered difference on "<surface>"`, 95 passed.
§4.1's citation of run `32561272600` was one push stale by the time work began; the
signature was identical, so the diagnosis carried.

That push also carried the four `2026-08-22-safe-area-uncovered-top-surfaces` commits,
which had been sitting unpushed and had never been through CI. They are frontend style
changes on surfaces this baseline covers, so capturing against `origin/main` rather than
`HEAD` would have produced a baseline describing code that was not shipping.

### AC-2 — the baseline self-check

Captured on a `smap_test` that was dropped, recreated, migrated to head and seeded by the
backend alone (2 users, 0 `api_keys` — the state CI starts from), with `global-setup` run
exactly once and its 11-key `E2E_*` set verified before the run was trusted. Only spec 00
ran, so the capture saw the freshly seeded data its header requires. 22 passed, 82
snapshots written.

**The self-check passed**: the comparison was immediately re-run against unmodified code
and reported 22/22 with no differences.

Two earlier captures were discarded rather than committed. The first was refused by the
spec's own partial-baseline guard after a surface timed out — the guard working exactly as
its comment describes. The second succeeded but on a stack `global-setup` had seeded
twice, which is a data state CI never has; committing it would have been the same
casualness Q-2 exists to prevent.

### AC-3 — the visual pass, per surface

21 surfaces (the `SURFACES` list in `00-visual-token-parity.spec.ts` holds 21, not the 20
§6.2 says; `mobile-drawer` exists at one viewport only). 1440x900 and 375x812, `en` and
`zh-TW`, light and dark — 164 combinations, each captured and each swept for clipped text
and horizontal page overflow. Screenshots were read per surface; the table records what
was found, not merely that it was looked at.

| Surface | Result |
|---|---|
| agents-list | 1440 clean both themes/locales. **V-1** at 375: title "AI Agents" in a 13px box, rendering as the single letter "A". Fixed. |
| agent-detail | Clean at 1440. At 375 the title ellipsised to `e2e-agent-…` — degraded but legible; full width after V-1. |
| keys | Clean. The masked-preview column wraps `sk-e2e- … 2334` over two lines at 1440 — cramped, not a defect. Recorded, not fixed. |
| key-group-detail | **V-5** at 375: the key name clipped to a 0px box. Header also read oddly, the title wrapping right-aligned beside its action. Both resolved. |
| orgs | Clean at both viewports, both locales, both themes. |
| org-detail | **V-1** at its worst: title box 0px, so the page never named the org it was showing, and the five-button action row ran 267px past the right edge with the last two unreachable. Fixed; verified by screenshot after. |
| project-members | Clean. |
| chatroom | **V-3** and **V-4** at 375, both locales, both themes: the room name and `live` pill clipped mid-word, and the empty-state description running off the container. One cause, fixed. |
| chatroom-settings | Clean. A suspected back-arrow/title collision in `zh-TW` was **not confirmed** and is recorded as unproven rather than as a defect. |
| workflows | Clean. Lower visual polish than its neighbours (no breadcrumb, plain-text row actions); a design observation, not a defect, and not this dossier's property set. |
| profile | Clean. The focus ring on the display-name input reads correctly against the card at 375 dark. |
| notifications | Clean. |
| invites | Clean. |
| admin-metrics | Clean; the metric cards layer well at 375 dark. |
| account-delete | Clean; the danger alert reads correctly in dark. |
| prompt-assistant | Clean. |
| agent-tools | **V-6** at 375 in `zh-TW`: the upload button's label broke one CJK glyph per line. `en` wrapped to two lines — the same cause, less visible. Fixed. |
| chatroom-create-modal | The modal itself is clean; the `s-page-header__title` reported at this slot belongs to the workspace page behind it. |
| mobile-drawer | Clean. The panel reads as a raised sheet over the dimmed canvas. |
| landing | 1440 excellent in both themes and locales. **V-2** at 375: the page scrolled sideways 11px in all four combinations. Fixed. |
| login | Clean. The autofocused email field's ring reads correctly against the auth card. |

**After the fixes, the same 164-combination sweep reports zero horizontal page overflow
(was 4) and zero starved labels (was 28 entries including 0px and 13px boxes).** The eight
remaining clipped elements are long seeded names ellipsising in boxes of 147-359px, which
is what ellipsis is for.

### AC-4 — truncation

**The three exposures §6.2 named are clean.** Sidebar nav labels against
`--sidebar-width`, `STable`'s `nowrap` headers, and `SBadge` pills showed no truncation in
any of the 164 combinations. **Inter caused no truncation regression** — which is the
question AC-4 asks, and the answer is no.

Everything the sweep did find is older than the typeface work: `SPageHeader`'s geometry is
unchanged since the Phase U1 component library (phase 2 touched only its `line-height`, in
`5599303`), and `.chatroom--mobile`'s track and the constellation's bleed likewise predate
it. `zh-TW` was the more revealing locale exactly as §7 predicted, but by exposing V-6
rather than by any metric problem.

### AC-5 — the focus ring

Observed by keyboard traversal, both themes, recording the computed outline at every stop
and photographing each one. The ring is `solid 2px` at `2px` offset with
`box-shadow: none` on every control reached — `rgb(37, 99, 235)` light, `rgb(96, 165, 250)`
dark. **No control anywhere painted a `--color-bg` halo**, which is the failure phase 2
replaced the two-layer shadow to remove.

| Backdrop | Result |
|---|---|
| Top bar chrome | Observed. Skip link, sidebar toggle, wordmark, bell, user menu, locale and theme toggles. |
| `.sidebar` | Observed. Switcher, nav items, group header, active nav item. |
| `main#main-content` | Observed. |
| `.s-card` | Observed on `/agents/:id/tools` — toggles, secondary, primary and link buttons. |
| `.s-modal__panel` | Observed. Toggles and the close button. |
| `.s-modal__footer` | Observed. |
| `.s-table__th` | Observed — **only because V-7 created something to focus**. Ring correct against the recessed header fill. |
| `.s-card__footer` | **Observed after the fact** — see "FU-5, resolved" above. Nothing in the app renders one, so it had to be mounted temporarily to exist at all. Ring correct in both themes. |
| `.s-dropdown__menu` | **Not observed.** FU-6. |

`.s-input__field` reports `outline-style: none` and is not a finding: the ring is painted
on the `.s-input` wrapper via `:focus-within`, and the login and profile screenshots show
it rendering correctly.

The dropdown is the honest gap. What *is* established: with a pointer-opened menu,
arrow-key focus produces no outline at all, only the `--color-surface` highlight — and
`SDropdown.vue:332` does that deliberately, with the comment at `:324` saying a keyboard
landing should get a real ring instead. Whether the keyboard-opened path delivers that
inset ring could not be observed: three harness approaches failed to land focus on a menu
item within budget. Reasoning from Chromium's `:focus-visible` modality rules says it
works. That reasoning is exactly what this dossier exists to replace, so it is recorded as
open rather than ticked.

### AC-6 — layering

Measured and observed, both themes, with `.s-card`'s border removed via an injected style.
Light: canvas `rgb(241, 245, 249)` against card `rgb(255, 255, 255)`. Dark: canvas
`rgb(8, 13, 22)` against card `rgb(15, 23, 42)`. Both keep `--elevation-1`. **The cards
still read as sheets on the canvas with no border at all**, which is what phase 2's
three-surface-role work was for. The landing page, the auth pages and the chatroom were
all opened and none is visibly broken.

### AC-7 — the press under reduced motion

Observed, by holding the button down rather than clicking it. Normal: transition-duration
`0.15s`, pressed transform `matrix(1, 0, 0, 1, 0, 1)`. Under
`prefers-reduced-motion: reduce`: transition-duration `1e-05s`, pressed transform still
`matrix(1, 0, 0, 1, 0, 1)`. **The press becomes an instant change rather than
disappearing.** The same measurement confirmed FU-12's premise: `box-shadow` was `none`
both at rest and pressed.

### FU-8, FU-9, FU-10, FU-12 — the decisions

**FU-8 — a CI job (`frontend-csp-font`).** It serves a built `dist/` from a bare nginx
carrying the CSP **extracted from `smap.conf` at run time**, never copied — a copy drifts,
and a drifted copy holds the job green while the deployed header changes, which is the
same vacuous pass in a new costume. The extractor fails loudly if the directive is renamed
or loses its `font-src` clause, and the spec asserts the response header *first*, because
without that every other assertion passes just as happily against a server that sent no
CSP. Both directions were run locally: green against the real header, red under
`font-src 'none'` with "no Inter face reached status loaded".

**FU-9 — two commented copies.** A third consumer of `isFigureColumn` is not plausible,
and lifting it would give the mobile card branch a dependency on the table's `Column` type
— the coupling that splitting the branches removed. Each copy now names the other.

**FU-10 — decided per token, and the answers differ.** `--control-h-*` moves to rem;
`--sidebar-width` and `--topbar-height` stay px. A control height is a *floor under text*:
it exists so a 14px label has room, so it must grow when the reader enlarges their font or
the label overruns it. A sidebar is a *track* and the top bar is *chrome*: growing those
takes space away from the content the reader enlarged the font to read. `2/2.5/3rem` are
exactly `32/40/48px` at the default root size, and `tokens.test.ts` keeps the px column so
that claim stays checkable.

**FU-12 — a resting elevation on the filled variants.** `primary`, `secondary` and
`danger` now rest at `--elevation-1`, so the press's drop to `--elevation-0` is a real
step. `ghost` and `link` render as transparent text, where a shadow would be cast by
nothing; they stay flat, and the press's shadow stays a no-op for them **by design rather
than by oversight** — the same reasoning that already excludes `link` from the translate.
Declared per variant so `focus-and-press.test.ts` can ask each one separately, including
the two that must answer "none".

### AC-10 — FU-11

**It did not reproduce.** Eighteen runs of `AgentToolsView.test.ts` under six-way CPU
load, all passing. The measurement the dossier requires before any timeout may move: the
first test costs **1319ms** under that load and every other test in the file costs
**92-270ms**, so the slowest has 3.8x headroom against the 5s default — reachable on a
shared runner, not on this machine.

A cause fix was attempted first. Pre-importing the `@codemirror/*` graph in a `beforeAll`,
on the theory that the first test paid a cold start for the code-split editor, moved
1319ms to **1239ms**. It was therefore wrong, and it was removed rather than kept with a
rationale the numbers do not support. The cost is the mount and the editor construction,
which belong to the test.

The timeout moves to 15s — 11x the observed worst case — and `clickByText` now polls
instead of reading the button once after a fixed 50ms sleep, which is an independent race
worth removing on its own merits.

**This file is not special, and that is the more useful finding.** Three consecutive full
local suites during close-out failed 1, 0 and 2 tests, and the failures were different
tests each time (`mobileViewportContract`'s viewport-unit sweep at 10542ms, `SFormField`'s
CodeMirror re-sync) — never `AgentToolsView`. The thin headroom is host-wide, not
file-specific. FU-7.

### Defects found by §6.2

Numbered, each with the measurement that identified it and the commit that closed it.
None is a phase-2 regression; all six predate the typeface work.

**V-1 — a page header's actions erase its title at 375px.** `.s-page-header__row` was a
flex row with no wrap, `__actions` was `flex-shrink: 0` and `__content` could shrink to
zero, so the row resolved entirely in the actions' favour. Six surfaces; worst on
`/orgs/:id` (title box 0px, action row 267px past the edge). Fixed in `612957c` by giving
the content a floor — which is what forces the wrap — and capping the actions at `100%` —
which is what makes the wrap reachable, since `flex-shrink: 0` otherwise holds that box at
its max-content width however narrow the line. Title box 13px → 359px on the agent list,
0px → 359px on the org detail.

**V-2 — the landing page scrolls sideways 11px at 375px**, all four locale/theme
combinations. The constellation's particle canvas is deliberately 48px wider than its
figure on each side; nothing bounded that bleed. Fixed in `ab73642` with `overflow-x: clip`
on `.hero` — far larger than the figure, so the effect is preserved exactly. `clip` not
`hidden`, which would make the hero a scroll container.

**V-3 — the chatroom header is clipped at 375px**, both locales and themes. A `1fr` grid
track is floored at `min-content`, so the single mobile column measured 498px inside a
375px viewport and `.chatroom`'s `overflow: hidden` cut the pill and buttons off.
`minmax(0, 1fr)`, in `7083af9`; the desktop template took the same treatment, where it has
never bitten only because the window is wide enough to hide it.

**V-4 — the chatroom empty state overflows its container at 375px.** Same cause as V-3:
`max-width: 400px` was resolving against a 498px column. No separate fix.

**V-5 — a key group member's name is clipped to 0px at 375px.** `truncate` sets
`overflow: hidden`, which makes a flex item's automatic minimum size 0 rather than its
content — so the only part of the row identifying the key did not ellipsise, it vanished.
Fixed in `553f70e` with a floor and a wrapping row.

**V-6 — the workspace-files upload button breaks one CJK glyph per line at 375px in
`zh-TW`.** Its wrapper shrank to min-content beside a long hint. `en` wrapped to two lines:
same cause, less visible. Fixed in `553f70e`. Found by looking, not by measuring — it wraps
rather than clips, so D-5's mechanical half is blind to it.

**V-7 — a sortable table header is operable by pointer only.** It carried `@click` and
`cursor: pointer` and nothing else: no `tabindex`, no key handler, no role, while
`aria-sort` announced a control that could not be operated. Fixed in `f809d87` by making
the header content a real `<button>` for sortable columns, so activation, the focus ring
and the announced role come from the platform. Found while trying to satisfy AC-5.

**The regression test.** `e2e/25-narrow-viewport-layout.spec.ts` (`feab657`) pins the class
rather than the instances: at 375px in both locales, no page scrolls sideways and no
element that clips its text is given a box too small to show any of it. Its assertions are
about boxes, not text, because whether a seeded name ellipsises is data. Verified in both
directions — with `SPageHeader`'s floor reverted it fails with
`"agents-list" gives its page title "AI Agents" only 13px of box`, which is the defect in
the numbers that were shipping.

## 16. Follow-ups

- **FU-1** — phase 2's FU-5: the product has no display face and no logotype; the wordmark
  is text in the accent colour. A distinctive identity needs both and neither is in scope
  in any dossier so far.
- **FU-2** — phase 2's FU-6: the two categorical palettes (`WorkflowNodeComponent.vue`,
  `GraphragGraphView.vue`) have never been checked for colour-blind safety or for contrast
  against their own canvas, in either theme.
- **FU-3** — phase 2's FU-3 and FU-4, inherited unchanged: `SBadge`/`SInput`'s
  contradictory touch-target declarations, and `AppShell`'s hard-coded `300ms`.
- **FU-5** — **resolved. See §15 "FU-5, resolved" for the finding.** The premise as first
  written was wrong: `SCard`'s footer slot has no consumers at all, not four.
- **FU-6** — **the dropdown menu's keyboard focus ring is still unobserved.** With a
  pointer-opened menu, arrow-key focus paints no outline at all — only the
  `--color-surface` highlight, which `SDropdown.vue:324` itself calls "too faint to be
  one". `:332` removes the outline deliberately for that path, and `:328` promises an
  inset ring for the keyboard path. Three harness approaches failed to land focus on a
  menu item within budget, so whether that promise is kept is unknown. It is a menu:
  `role="menu"` with `tabindex="-1"` items driven by arrow keys and focused by script, so
  the answer turns on Chromium's `:focus-visible` modality rules and cannot be read off
  the CSS. Worth one deliberate manual check rather than another harness.
- **FU-7** — **the unit suite's timing headroom is thin host-wide, not in one file.**
  Phase 2's FU-11 named `AgentToolsView.test.ts`; this dossier's AC-10 raised its timeout
  with a measurement but never reproduced the failure. Three consecutive full local suites
  during close-out failed 1, 0 and 2 tests respectively, and never the same ones —
  `mobileViewportContract`'s viewport-unit sweep at 10542ms and `SFormField`'s CodeMirror
  re-sync were the two that fell over. Raising timeouts one file at a time as each is
  observed is a treadmill; the question worth answering is whether the suite's default
  timeout is right for a loaded runner at all.
- **FU-8** — the `/keys` masked-preview column wraps `sk-e2e- … 2334` over two lines at
  1440x900. Cramped rather than broken, and outside the property set this dossier's ACs
  cover. Recorded because it was seen, not fixed because it was not asked for.
- **FU-4** — the page width policy is still undecided
  (`2026-08-19-content-area-spacing-and-scroll-contract`'s Q-15/Q-16/FU-4, phase 2's FU-2):
  six views cap their own width and the rest do not, with no intent source. It is a layout
  question and belongs in `docs/UI/12-shared-patterns.md`.
