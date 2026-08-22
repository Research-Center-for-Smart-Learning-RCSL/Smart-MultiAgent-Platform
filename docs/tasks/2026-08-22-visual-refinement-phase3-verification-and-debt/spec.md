---
type: refactor
status: draft
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

- [ ] AC-1: **CI is green.** `frontend-e2e` passes, including `00-visual-token-parity`
      against a baseline regenerated on a pristine stack, and the run id is recorded in §15.
- [ ] AC-2: **The baseline describes the code it was captured from.** Immediately after
      capture, an unmodified re-run compares clean (phase 1's D-11 self-check), and that is
      stated in §15 rather than assumed.
- [ ] AC-3: **The visual pass is performed and recorded per surface.** All 20 C-0 surfaces,
      1440x900 and 375x812, `en` and `zh-TW`, light and dark. §15 carries one line per
      surface with the result, not a single tick. Phase 2's AC-3 is quoted as closed by this.
- [ ] AC-4: **No truncation regression**, or every regression found is listed with the
      element, the viewport, the locale and the fix. Specifically checked: sidebar nav
      labels, `STable` `nowrap` headers, `SBadge` pills.
- [ ] AC-5: **The focus ring is correct on every backdrop it can appear over** — sidebar,
      card footer, table header, modal footer, dropdown — in both themes, by keyboard, with
      no halo belonging to neither surface. Closes phase 2's AC-5's open half.
- [ ] AC-6: **Layering survives without its border.** An `SCard` at the default variant,
      border removed in devtools, still reads as a sheet in both themes. The landing page,
      the auth pages and the chatroom are opened and are not visibly broken. Closes phase
      2's AC-6's open half.
- [ ] AC-7: **The press survives reduced motion.** Under `prefers-reduced-motion: reduce`
      the pressed state is an instant change with no movement — observed, not reasoned.
- [ ] AC-8: **FU-8 is decided**, and the decision is either implemented (a CI job with
      nginx in front) or recorded with a named owner and a date. "Proven by construction"
      is not an acceptable close.
- [ ] AC-9: **FU-9, FU-10 and FU-12 are each fixed or decided in writing**, with the
      reasoning in §15. FU-10 is decided per token, not as one answer.
- [ ] AC-10: **FU-11 no longer reproduces under load**, with the measurement that shows it.
- [ ] AC-11: gates green on CI: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`,
      `check:bundle-size`, `check:type-coverage`, `check:boundaries-enforced`. Backend
      gates N/A. CI is authoritative over the local Windows host.
- [ ] AC-12: **Any defect §6.2 surfaces is a numbered entry here**, fixed with a test where
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

Appended by /build. AC-3's per-surface results, AC-1's CI run id, and the FU-9/10/12
decisions are recorded here.

## 16. Follow-ups

- **FU-1** — phase 2's FU-5: the product has no display face and no logotype; the wordmark
  is text in the accent colour. A distinctive identity needs both and neither is in scope
  in any dossier so far.
- **FU-2** — phase 2's FU-6: the two categorical palettes (`WorkflowNodeComponent.vue`,
  `GraphragGraphView.vue`) have never been checked for colour-blind safety or for contrast
  against their own canvas, in either theme.
- **FU-3** — phase 2's FU-3 and FU-4, inherited unchanged: `SBadge`/`SInput`'s
  contradictory touch-target declarations, and `AppShell`'s hard-coded `300ms`.
- **FU-4** — the page width policy is still undecided
  (`2026-08-19-content-area-spacing-and-scroll-contract`'s Q-15/Q-16/FU-4, phase 2's FU-2):
  six views cap their own width and the rest do not, with no intent source. It is a layout
  question and belongs in `docs/UI/12-shared-patterns.md`.
