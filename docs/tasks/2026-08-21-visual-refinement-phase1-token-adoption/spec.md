---
type: refactor
status: implemented
created: 2026-08-21
requirements: [R24.28, R24.29, R24.30]
depends_on: [2026-08-19-shared-overlay-and-shell-defects]
---

# Visual refinement phase 1: make the design tokens load-bearing

## 1. Summary

`2026-07-05-sitewide-ui-enhancement` added a full token vocabulary to
`frontend/src/shared/styles/main.css` (spacing, typography, weight, line-height, semantic
elevation) and [R24.28] declares it authoritative. Almost nothing consumes it: three of the
46 components in `frontend/src/shared/ui/` reference a `--font-size-*`, `--space-*`,
`--weight-*`, `--line-*` or `--elevation-*` token, while 33 of them declare a raw
`font-size`/`font-weight`/`line-height` and 37 declare a raw `padding`/`margin`/`gap`. The
same size is frequently written two ways in two files, and four sizes exist that no token
covers. The result is that editing a token changes nothing visible, so the token layer
cannot be used to change the product's appearance. This dossier makes the tokens
load-bearing and reconciles `docs/UI/01-design-system.md`, which today specifies component
sizing in literal pixels and is therefore the reason each new component is written that
way. It changes **no rendered pixel**. The appearance changes are
`2026-08-21-visual-refinement-phase2-identity-and-depth`, which this dossier exists to make
possible.

## 2. Motivation

The `check-quality` dimension is **abstraction leak / duplicated knowledge**: a value that
one layer declares as the single source of truth is re-declared literally in 37 other
places, so the declaration is decorative and the literals are the real contract.

### 2.1 The token layer has almost no consumers

`main.css:98-136` defines `--space-1..12`, `--font-size-xs..3xl`, `--line-tight/normal/relaxed`,
`--weight-medium/semibold/bold` and `--elevation-0..3`. Across all of `frontend/src`, a
`var(--font-size-*|--space-*|--weight-*|--line-*|--elevation-*)` reference appears 64 times
in 17 files, and inside `shared/ui/` only three components account for any of them:
`SCard.vue` (13), `SAuthCard.vue` (9), `SEmptyState.vue` (3). The other 43 components use
none.

Against that, inside `shared/ui/` alone:

- 109 raw `font-size` / `font-weight` / `line-height` declarations across 33 of 46 files.
- 168 raw `padding` / `margin` / `gap` declarations across 37 of 46 files.

`--radius-*`, `--color-*`, `--transition-*`, `--z-*` and `--focus-ring` are **not** part of
this problem: those are consumed normally throughout (`SButton.vue:121`, `SInput.vue:158-161`,
`STable.vue:478`, `SBadge.vue:62` and so on) and are out of scope.

### 2.2 The same size is written two ways

Enumerating every literal `font-size` in `shared/ui/`, four sizes appear in both `rem` and
`px` notation in different components, each pair being the identical computed size:

| Size | `rem` form, in | `px` form, in |
|---|---|---|
| 12px | `SButton.vue:143`, `SFormField.vue`, `SInput.vue:214`, `SSelect.vue`, `SCharCount.vue`, `SConfirmDialog.vue`, `SFileUpload.vue` | `SBadge.vue:77`, `SDivider.vue`, `STable.vue:499`, `STableCards.vue`, `STooltip.vue` |
| 14px | `SButton.vue:148`, `SInput.vue:199`, `SPageHeader.vue:128`, `STabs.vue`, `STextarea.vue`, and 12 more | `SAlert.vue`, `SAccordion.vue`, `SPagination.vue`, `STable.vue:485`, `STableCards.vue` |
| 16px | `SButton.vue:154` | `STable.vue:626` |
| 13px | `SFileUpload.vue`, `SWakeupEditor.vue` | `SCodeEditor.vue` |

A grep for a size therefore finds only half its call sites, which is how `STable`'s body
text (`14px`) and `SButton`'s `md` label (`0.875rem`) came to be maintained as if they were
unrelated numbers.

### 2.3 Four sizes have no token, and two of them differ by 0.2px

| Value | Sites | Token? |
|---|---|---|
| 13px / `0.8125rem` | `SCodeEditor.vue`, `SFileUpload.vue`, `SWakeupEditor.vue` (10 declarations) | No token, but **is** specified: `docs/UI/00-overview.md:97` gives "Code / mono 0.8125rem (13px)" |
| 11px / `0.6875rem` | `LocaleToggle.vue`, `AppSidebar.vue:289` | No token, undocumented |
| `0.7rem` (11.2px) | `STabs.vue` | No token, undocumented, and 0.2px from the line above |
| `0.9375rem` (15px) | `SIdleDialog.vue` | No token, undocumented |

### 2.4 The design-system document is written in literal pixels

This is the reason the pattern reproduces itself rather than an accident.
`docs/UI/01-design-system.md` specifies component internals as literal values, not as
tokens: `:126-132` gives SButton's three sizes as `32px / 6px 12px / 0.75rem`,
`40px / 8px 16px / 0.875rem`, `48px / 10px 24px / 1rem`; `:249` gives SBadge as
"`sm`: 20px height, 10px font. `md`: 24px height, 12px font"; `:400` gives STable's header
as "600 weight, 12px uppercase text"; `:313`, `:359`, `:476`, `:490`, `:514`, `:528` and
`:538` do the same for SFormField, SBreadcrumb, STabs, SDropdown, SAccordion, SAlert and
SSkeleton. Every component in §2.1 that hard-codes a size is **conforming** to this
document. Rewriting the components without rewriting the document leaves the next
implementer with a specification that instructs them to undo the work.

### 2.5 Control heights are a fourth uncoordinated copy

`32 / 40 / 48px` is the control-height ladder. It is declared independently in
`SButton.vue:140,146,152`, `SInput.vue:185,189`, `SSelect.vue`, `STextarea.vue` and
`AppTopBar.vue:93-94`, and again in prose at `docs/UI/01-design-system.md:128-132` and
`:163`. Nothing ties them together, so `SInput` offers only `sm`/`md` while `SButton`
offers `sm`/`md`/`lg` and the two happen to agree at the shared sizes by hand.

## 3. Non-goals

- **No externally observable behavior change, and no visual change at all.** Every computed
  style after this dossier resolves to the value it resolves to today. This is stricter
  than the usual refactor bar (which permits visual identity to be preserved only in
  intent) and is what AC-1 measures.
- **No change to the token *values*.** Recolouring, re-scaling the type ramp, retuning
  elevation and changing the typeface are phase 2. This dossier only adds tokens where a
  literal has no token and the addition is exactly equal to the literal.
- **No new components, no prop changes, no API changes to any `S*` component.** A caller's
  markup is untouched.
- **`--radius-*`, `--color-*`, `--transition-*`, `--z-*`, `--focus-ring` are out of scope**
  (already consumed correctly, §2.1).
- **The 34 padded view roots and the 23 nested `<main>` elements are out of scope.** They
  belong to `2026-08-19-content-area-spacing-and-scroll-contract` (its F-3 and F-40), which
  is still `draft`. This dossier does not touch `**/views/*.vue` template roots (Q-5).
- **Component-local geometry is out of scope**: icon box sizes, the 3px sidebar active bar
  (`SidebarNavItem.vue:67`), the 18px checkbox, `44px` touch-target floors, SModal's width
  presets. These are per-component decisions, not a shared vocabulary (Q-2).
- **No `docs/UI/` content decisions.** The document edits in this dossier are notation only:
  a literal is replaced by the token that equals it. Where a literal has no token and none
  is added (§2.3, Q-3), the document keeps the literal.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | One dossier for the whole visual-refinement program, or split? | **Split.** This dossier is a `refactor` whose acceptance bar is "zero rendered difference"; `phase2` is a `feature` whose whole purpose is a rendered difference. | The two have incompatible verification. A single dossier could not assert AC-1 at all, because every computed-style comparison would be contaminated by the deliberate changes. Splitting also means a phase-2 visual regression can be bisected to a diff that changed only token *values*, not 40 component files. |
| Q-2 | Which declarations does the sweep cover? | **`font-size`, `font-weight`, `line-height`, `padding`, `margin`, `gap`, and the `32/40/48` control-height ladder.** Not: `width`/`height` on icons and decorative elements, `min-width`/`min-height` touch-target floors, `border-width`, or overlay/panel sizes. | The first list is a shared vocabulary that must move together when the visual language changes; the second is geometry local to one component's design. Tokenising a 3px indicator bar or an 18px checkbox adds an indirection with one consumer and no future edit that would use it, which is the failure mode [R24.28]'s vocabulary already has in `--color-accent-tint-hover` (the predecessor dossier's FU-9: defined, zero consumers). |
| Q-3 | What happens to the four sizes with no token (§2.3)? | **13px gets a token** (`--font-size-code`), because `docs/UI/00-overview.md:97` already specifies it as a design decision and it has three consumers. **11px, `0.7rem` and `0.9375rem` do not.** They keep their literal value, normalised to `px` notation, and are recorded as FU-1 for phase 2 to decide. | Adding a token asserts "this is a design decision the system makes". That is true of the mono size and provable from the design document; it is not true of a 0.2px difference between `STabs` and `LocaleToggle` that no document mentions and that nobody chose. Snapping them to the nearest existing token would change rendered pixels, which AC-1 forbids. |
| Q-4 | Does the control-height ladder become a token family? | **Yes: `--control-h-sm: 32px`, `--control-h-md: 40px`, `--control-h-lg: 48px`.** | Four components and the design document each carry their own copy (§2.5), and phase 2's density work has to change all of them together or produce controls of mismatched height sitting on the same row. This is the clearest case in the sweep where the absence of a token would make the next task incorrect rather than merely tedious. |
| Q-5 | Does this dossier touch view files under `src/slices/**/views/`? | **Only where a view declares a raw `font-size`/`font-weight`/`line-height`/spacing in its own scoped style block. Never the template root's class list.** | The template roots are `2026-08-19-content-area-spacing-and-scroll-contract`'s F-3/F-40 fix, still `draft`. Touching them here would either duplicate that work or conflict with it. A view's *scoped style block* is a different region of the same file, so both can be true; §7's file list makes the boundary explicit. |
| Q-6 | Why does `depends_on` list `2026-08-19-shared-overlay-and-shell-defects`? | **Overlap prerequisite, not a logical one.** That dossier edits the scoped style blocks of `STable.vue`, `SDropdown.vue`, `SAlert.vue`, `SEmptyState.vue`, `SModal.vue` and `STooltip.vue` (its §8 test plan names all six), plus `App.vue` and `AppShell.vue`. This dossier edits the scoped style block of every one of them. | Concurrent builds would conflict inside the same style rules, not merely the same files. Either could technically go first; sequencing behind it costs nothing here because this dossier has no logical dependency on its output, and it is already `draft` with a written plan. Recorded per `docs/tasks/README.md`'s "Dependencies and sequencing". |
| Q-7 | How is "zero visual change" actually verified, given jsdom performs no layout and applies no scoped CSS? | **Three tiers, none of which is jsdom asserting a box.** (a) A build-time equality assertion that each replacement token's declared value equals the literal it replaced. (b) A source sweep asserting no raw declaration of the Q-2 property set survives in `shared/ui/` or `app/`. (c) A Playwright `getComputedStyle` comparison over a representative surface set, captured before the change and asserted after. | The repository has already established that the unit tier cannot see layout (`2026-08-19-content-area-spacing-and-scroll-contract` §8, and `2026-08-09-chatroom-rail-scroll-and-resize` §12 before it). Tier (a) is what makes the refactor mechanically safe: if `--font-size-sm` is not exactly `0.875rem`, the test fails and no component needs checking. Tier (c) is the backstop for a substitution that is individually correct but lands in the wrong rule. |
| Q-8 | `docs/UI/01-design-system.md` and `docs/UI/00-overview.md` specify literal pixels (§2.4). Are they rewritten? | **Yes, in the same commit series, as notation-only edits.** A spec line reading "40px height, 8px 16px padding, 0.875rem font" becomes "`--control-h-md`, `--space-2` `--space-4`, `--font-size-sm`", with the pixel value retained in parentheses on the token definition table only. | Leaving them is the single change most likely to make this whole dossier revert itself: the next component author reads `01-design-system.md`, follows it exactly, and reintroduces the literals. Retaining the pixel value in *one* place (the token table) keeps the document readable without giving it a second authority. This is why AC-6 exists as a separate criterion rather than being folded into the code ACs. |

## 5. Current vs Target Structure

**Before**

```
main.css @theme          declares --space-*, --font-size-*, --weight-*, --line-*, --elevation-*
                         (consumed by 3 of 46 shared/ui components)
shared/ui/*.vue          109 raw type declarations + 168 raw spacing declarations,
                         in two notations, with 4 sizes outside the scale
app/components/*.vue     same pattern (AppSidebar.vue:287-294, SidebarNavItem.vue:43-52,
                         AppTopBar.vue:70-146)
docs/UI/00-overview.md   typography + spacing tables in literal px/rem
docs/UI/01-design-system.md  per-component visual specs in literal px/rem  <- the actual authority
```

The dependency edge that is wrong: `docs/UI/01-design-system.md` -> component literals is a
live edge, and `main.css @theme` -> component is not. The document and the token layer are
two uncoordinated sources for the same values, and the document is the one that wins,
because it is what an implementer reads.

**After**

```
main.css @theme          the single declaration of every shared type/space/weight/elevation
                         value, plus --font-size-code and --control-h-{sm,md,lg}
shared/ui/*.vue          zero raw declarations of the Q-2 property set; every one is var(--...)
app/components/*.vue     same
docs/UI/00-overview.md   tables name the token; the px value appears once, on the token row
docs/UI/01-design-system.md  per-component specs name tokens
```

One source, two readers (code and document), and the document's values are now assertions
about tokens rather than a second copy of the numbers. No layer boundary changes: `main.css`
is already in `shared/styles/` and already imported globally, and no component gains an
import. Lint gate 6 is unaffected, because `check-global-css.sh:15` only inspects
`src/slices` and `src/app` `.vue` files for non-scoped `<style>` blocks and no `<style>` tag
changes its attributes.

## 6. Characterization Test Plan

The behaviour to pin before anything moves is **rendered appearance**, and it must be
pinned by something that can see a rendered box. The plan is written in the order it must be
executed, because tier C's baseline has to be captured against unmodified code.

### C-0 (prerequisite, before any edit) - capture the computed-style baseline

New `frontend/e2e/20-visual-token-parity.spec.ts`. Against the compose stack, for each
surface in the list below, at 1440x900 in **both** themes, read
`getComputedStyle` for `font-size`, `font-weight`, `line-height`, `padding-*`, `margin-*`,
`gap`, `height` and `box-shadow` on the named elements, and write the result to a committed
JSON baseline. The surfaces, chosen to cover every component the sweep touches at least
once:

| Surface | Covers |
|---|---|
| `/projects/:pid/agents` (list, populated + empty) | SPageHeader, SButton (3 variants, 2 sizes), SSearchInput, SSelect, STable header/row/empty, SBadge, SDropdown trigger, SPagination, SEmptyState |
| `/projects/:pid/agents/:id` (General tab) | SCard header/body/footer, SFormField, SInput, STextarea, SToggle, SCheckbox, STabs, SAlert |
| `/keys` with the upload modal open | SModal header/body/footer, SFileUpload, SCharCount, SConfirmDialog |
| `/orgs` at 375x812 | STableCards, the responsive branch of STable |
| the app shell on any authenticated route | AppTopBar, AppSidebar section header, SidebarNavItem, SBreadcrumb, SAvatar, STooltip on hover |

`SWakeupEditor`, `SCodeEditor`, `SAccordion`, `SDivider`, `SRadio`, `SProgressBar`,
`SIdleDialog` and `LocaleToggle` are **not** reachable from those five surfaces. They are
covered by tier B (source sweep) and tier A (value equality) only, and that gap is stated in
§9 rather than papered over: their substitutions are mechanical and tier A proves the values
are equal, but no rendered box is compared for them.

### C-1 - token value equality

New `frontend/src/shared/styles/__tests__/tokens.test.ts`. Parse `main.css`'s `@theme`
block and assert each token used as a replacement equals the literal it replaces, as a
table in the test file itself:

```
--font-size-2xs  === 0.625rem   (10px)
--font-size-xs   === 0.75rem    (12px)
--font-size-sm   === 0.875rem   (14px)
--font-size-md   === 1rem       (16px)
--font-size-lg   === 1.125rem   (18px)
--font-size-xl   === 1.25rem    (20px)
--font-size-2xl  === 1.5rem     (24px)
--font-size-code === 0.8125rem  (13px)   [new]
--space-1..12    === 4..48px
--weight-medium/semibold/bold === 500/600/700
--control-h-sm/md/lg === 32/40/48px       [new]
```

This test fails today only for the two new token families (they do not exist); the existing
rows pass immediately and are there to make a later value edit in phase 2 fail loudly here
rather than silently everywhere.

### C-2 - existing component tests

`frontend/src/shared/ui/__tests__/` already holds component tests. None of them asserts on a
raw CSS value (the predecessor dossier established at its §4 that button selectors target
`.s-btn--*` roles and class names, not styles), so they pin markup and props, which this
dossier does not change. They must stay green unmodified; that is AC-4.

### Coverage gap, stated

No existing test asserts any rendered dimension anywhere in the repository. C-0 is
therefore new coverage created for this refactor, not a net the code already had.

## 7. Migration Steps

Each step leaves the tree green. The commits are ordered so that the safety net exists
before anything it protects moves.

1. **`test(e2e): pin computed styles across the component surface set`** - C-0's baseline
   spec and its committed JSON, against unmodified code. Nothing else changes.
2. **`test(frontend): assert every design token equals the literal it stands for`** - C-1.
   The two new token families are added to `main.css` in this step so the test passes:
   `--font-size-code: 0.8125rem` and `--control-h-sm/md/lg: 32px/40px/48px`. Adding a token
   nothing consumes yet is a no-op on rendering.
3. **`refactor(frontend): consume type tokens in the shared component library`** -
   `font-size`, `font-weight`, `line-height` across the 33 files in §2.1. Includes
   normalising the `0.6875rem`/`0.7rem`/`0.9375rem` one-offs to `px` notation without
   changing their value (Q-3).
4. **`refactor(frontend): consume spacing tokens in the shared component library`** -
   `padding`, `margin`, `gap` across the 37 files.
5. **`refactor(frontend): consume the control-height tokens`** - `SButton.vue:140,146,152`,
   `SInput.vue:185,189`, `SSelect.vue`, `STextarea.vue`, `AppTopBar.vue:93-94`.
6. **`refactor(frontend): consume type and spacing tokens in the app shell`** -
   `AppSidebar.vue`, `SidebarNavItem.vue`, `AppTopBar.vue`, `AppShell.vue`,
   `OrgProjectSwitcher.vue`, `UserMenu.vue`, `SidebarGroup.vue`, `SidebarChatroomList.vue`.
7. **`refactor(frontend): consume type and spacing tokens in slice-local styles`** - the
   scoped `<style>` blocks of slice components and views that declare the Q-2 property set.
   Template class lists are not touched (Q-5).
8. **`test(frontend): forbid raw type and spacing declarations in component styles`** -
   the tier-B source sweep (T-2 below). Added last, because it is red until step 7 lands.
9. **`docs(review): express the design system in tokens`** - `docs/UI/00-overview.md` §3/§4
   and `docs/UI/01-design-system.md` §1/§3/§4/§5/§6 per Q-8.

Steps 3 through 7 each rerun C-0 against the baseline; a diff is a defect in that step, not
an acceptable outcome to be renegotiated later.

## 8. Risks and Rollback

- **The largest risk is a substitution that is individually correct but lands in the wrong
  rule.** Replacing `padding: 8px 12px` with `padding: var(--space-2) var(--space-3)` in the
  wrong selector produces a plausible-looking diff and a wrong page. C-0 is the mitigation
  and it is the reason step 1 comes first. The residual exposure is the eight components C-0
  cannot reach (§6), which are covered by value equality and the source sweep only.
- **Tailwind v4 namespace collision.** `--font-size-*`, `--line-*`, `--weight-*` and
  `--space-*` were deliberately named to avoid Tailwind's `--text-*`, `--leading-*`,
  `--font-weight-*` and `--spacing` namespaces (`main.css:99-100,111-114` records this).
  The two new families must not reintroduce the problem: `--control-h-*` is not a Tailwind
  namespace, and `--font-size-code` sits inside the already-safe `--font-size-*` family.
  Verified by the build passing and by no new utility classes appearing in the output.
- **`0.7rem` is 11.2px and `0.6875rem` is 11px.** Anyone reading the diff will be tempted to
  unify them. Q-3 forbids it in this dossier; FU-1 carries it. A reviewer who "fixes" this
  breaks AC-1 by 0.2px in one component, which C-0 will catch, but only if C-0 covers
  `STabs` - it does, via the agent detail surface.
- **`SWakeupEditor.vue` carries 14 type and 20 spacing declarations**, the densest file in
  the sweep, and is not reachable from any C-0 surface. It is the single most likely place
  for an unobserved regression. Its substitutions should be reviewed line by line rather
  than swept.
- **The `docs/UI/` rewrite (step 9) has no automated verification.** A document that says
  `--space-3` where the code says `--space-4` is silently wrong. AC-6 requires it be checked
  by reading the code, and the step is deliberately last so the code is settled.
- **Rollback** is `git revert` per step. Steps 3 through 7 are independent of each other;
  reverting step 4 alone leaves the type tokens adopted and the spacing literals restored,
  which is a coherent state. Step 2's token additions are inert if every consumer is
  reverted.

## 9. Acceptance Criteria

- [x] AC-1: **No rendered difference.** `frontend/e2e/00-visual-token-parity.spec.ts` (D-6)
      passes against the baseline captured in step 1, in both themes, at 1440x900 and
      375x812, after every one of steps 3 to 7. Any single property that differs is a defect
      in that step and is fixed there, not recorded as an accepted deviation.
      **Verified**: 21 surfaces, 82 snapshots, 759 distinct element signatures; green after
      each of steps 3, 4, 5, 6 and 7 and again on the final tree. No step produced a
      difference, so nothing was renegotiated. See D-7, D-8 and D-11 for how the harness
      differs from §6 and what its first self-check found.
- [x] AC-2: `frontend/src/shared/styles/__tests__/tokens.test.ts` passes, asserting every
      row of the C-1 table. `--font-size-code` and `--control-h-sm/md/lg` exist in
      `main.css`'s `@theme` block with exactly the values `0.8125rem`, `32px`, `40px`, `48px`.
      **Verified**: 40 tests. The table grew to 38 rows (D-2, D-3) and gained two assertions
      the dossier did not specify: that every `--space-*`/`--font-size-*`/`--line-*`/
      `--weight-*`/`--control-h-*`/`--elevation-*` token in `main.css` appears in the table,
      and that none is redefined under `[data-theme="dark"]`.
- [x] AC-3: **The motivating violation from §2 is gone**, verified by the T-2 sweep
      (`frontend/src/shared/styles/__tests__/no-raw-style-literals.test.ts`): no file under
      `frontend/src/shared/`, `frontend/src/app/` or `frontend/src/slices/` declares a
      literal `font-size`, `font-weight`, `line-height`, `padding`, `margin` or `gap` value,
      except for the exemptions the sweep names explicitly with a reason.
      **Verified**: 220 files, 985 declarations in the property set, 811 naming a token,
      48 exempt, the rest stating `0`/`auto`/an `env()` inset. The exemption count is 48,
      not four, because §2 measured only `shared/ui/` while AC-3's sweep covers three trees
      (D-1). The sweep additionally asserts that it scanned a plausible amount of CSS and
      that every exemption still matches a live declaration.
- [x] AC-4: every existing test under `frontend/src/shared/ui/__tests__/`,
      `frontend/src/app/__tests__/` and the slice test suites passes **unmodified**. A test
      that had to be edited is a signal that behaviour changed and must be raised as a
      deviation, not edited quietly.
      **Verified**: 214 files, 1417 tests green. Exactly one test was edited and is raised
      as **D-10** per this criterion's own instruction: `SModal.test.ts` asserted the source
      text `max(24px, env(...))`, a spelling this dossier exists to change. No behaviour
      moved.
- [x] AC-5: `SButton`, `SInput`, `SSelect` and `STextarea` derive their control height from
      `--control-h-*`; `AppTopBar.vue`'s 40px toggle does too. Grep-verifiable: no literal
      `32px`, `40px` or `48px` remains as a `height`/`min-height` in those five files.
      **Verified**: grep clean across all five. `STextarea` declares no height at all - it
      sizes from `rows`, so §2.5 and this criterion are wrong about that one file (D-9).
      Three further shared controls were included for Q-4's own reason (D-9).
- [x] AC-6: `docs/UI/00-overview.md` §3 and §4 and `docs/UI/01-design-system.md` §1, §3, §4,
      §5 and §6 express sizing in token names. Every token named in those documents exists in
      `main.css` and carries the value the document's token table states, verified by
      reading both.
      **Verified**: all 37 tokens named across the two documents are declared in `main.css`;
      every value in the token tables matches its declaration; every px equivalent is the
      rem value at a 16px root; and the component specs were read against the components
      (SButton's three rungs, STable's header, SModal's footer, SAlert, SFormField,
      SPageHeader, STabs, SPagination, SCard). The final clause - no literal size outside
      the token table - holds for every size that *has* a token; four classes deliberately
      keep literals and §1 now names them (D-12).
- [ ] AC-7: gates green on CI: `pnpm lint` (all 12, notably #6 global CSS and #10 type
      coverage), `pnpm typecheck`, `pnpm test`, `pnpm build`,
      `pnpm run check:bundle-size`, `pnpm run check:type-coverage`,
      `pnpm run check:boundaries-enforced`. Backend gates N/A: the diff is frontend and docs
      only. Per the project's remote-CI rule, CI is authoritative over the local Windows host.
      **Deliberately unticked.** Every gate listed is green on this host - `pnpm lint`
      (0 warnings), `pnpm typecheck`, `pnpm test` (1417), `pnpm build`, plus
      `check:bundle-size`, `check:type-coverage` (98.61%), `check:boundaries-enforced`,
      `check:global-css` (171 scoped blocks) and `check:view-tests` (74/74) - but the branch
      has not been pushed, so **CI has not run**. This criterion is CI's to close.
- [x] AC-8: `main.css`'s existing token *values* are byte-identical to before the change.
      A changed value is a phase-2 edit that leaked in.
      **Verified** by parsing the `@theme` block at `db66167` and at HEAD: every token
      present in both carries an identical value, none was removed, and ten were added
      (`--font-size-code`, `--control-h-sm/md/lg`, `--space-0-5/1-5/2-5`, `--line-none`,
      `--line-snug`, `--weight-normal`).

## 10. SRS Delta

None. [R24.28] (`REQUIREMENTS.md:1960`) already declares the `@theme` block the location of
the design tokens and lists the vocabulary as covering "spacing, typography, radius,
shadow/elevation". This dossier makes the code conform to that requirement rather than
changing it. [R24.29] (`:1961`) already requires component styling to consume `@theme`
tokens, which is precisely the violation §2 documents.

## 11. Deviation Log

Built 2026-08-21/22 from base `db66167`. D-1 through D-5 are scope decisions taken with
the user before any code moved; the rest were forced by what the work found.

- **D-1 - the sweep is three times the size §2 states.** §2.1's counts (109 type, 168
  spacing) were measured inside `shared/ui/` only, while AC-3's sweep covers `app/` and
  `slices/` as well. Measured at build start: 334 type and 604 spacing declarations across
  ~140 files; measured at the end by the T-2 sweep: **985 declarations in the Q-2 property
  set across 220 files, 811 of them now naming a token**. The plan in §7 already covered all
  three trees, so no step was added - only the estimate was wrong. The user was asked
  whether to trim `slices/` into a phase 1b and chose to keep the approved scope.
- **D-2 - five token families were added beyond the two specified.** `--space-0-5: 2px`,
  `--space-1-5: 6px`, `--space-2-5: 10px`, `--line-none: 1` and `--line-snug: 1.4`.
  Q-3's conservatism was written against `shared/ui/`, where the only un-tokened values were
  four one-off font sizes. Across three trees, 55 declarations sit on the 2/6/10px
  half-steps and 12 more on line-heights of 1 and 1.4. Without tokens for them the AC-3
  exemption list would have run to roughly 115 entries and the sweep would have asserted
  nothing. Each addition is exactly equal to the literal it replaces, which §3 permits.
  Decided with the user.
- **D-3 - `--weight-normal: 400` was added.** Not in C-1's table. Three rules reset an
  inherited bold explicitly (`SAlert`'s body, two sidebar rows) and would otherwise have
  been the only literal font-weights left in the codebase.
- **D-4 - `main.css`'s own `@layer base` and `@utility` blocks were tokenised.** AC-3's
  wording covers `<style>` blocks in `.vue` files, which does not reach them, so the file
  that declares the vocabulary was also the file ignoring it: `h1`/`h2`/`h3`, the Preflight
  form-chrome restore, `fieldset`, `legend` and the skip link. The three heading rules are
  the ones that matter - a phase-2 change to the ramp that skipped them would leave every
  heading in the product at the old scale. Decided with the user.
- **D-5 - `slices/tenancy/styles/detail-cards.css` and `member-form.css` were included.**
  24 declarations of the same literals, missed by AC-3's wording for the same reason as D-4.
- **D-6 - the parity spec is `00-visual-token-parity.spec.ts`,** not §6's `20-`: that number
  was taken by `20-onboarding-without-smtp.spec.ts`.
- **D-7 - C-0's property set drops measured `height`/`width` and adds `min-height`.**
  §6 lists `height`. A used height varies with text and with seeded data, so it cannot serve
  a byte-equality baseline; the control ladder is *declared* as `min-height` in every one of
  the five files AC-5 names, so comparing that pins it exactly. `box-shadow` is included for
  semantic elevation. Margins and `min-height` whose used value is fractional are recorded
  as a sentinel: every literal in this diff is a whole number of pixels, so a fractional one
  can only be `margin: auto` centring against a content width.
- **D-8 - C-0 covers 21 surfaces and names no selectors.** §6 lists five surfaces and a
  selector inventory. A hand-written selector list only protects what the author remembered,
  and the failure C-0 exists to catch is by definition somewhere the author was not looking,
  so every classed element is keyed by `tag|sorted-class-list` and the *set* of distinct
  computed-style records under that key is recorded. Two surfaces are driven into a state
  navigation cannot reach (an open modal, an open drawer) and three were added purely to
  reach SCheckbox, STextarea and SAccordion. This shrinks FU-4's blind spot from eight
  components to six - see FU-8.
- **D-9 - AC-5's file list was extended, and is wrong about one file.** `STextarea` declares
  no height at all; it sizes from `rows`. `SSearchInput`'s field, `STabs`' tab row and
  `SPagination`'s page buttons were added, for Q-4's own reason rather than despite it: each
  sits on the same row as an SButton, so a density change that moved the ladder without them
  would produce exactly the mismatched-height row Q-4 exists to prevent.
- **D-10 - one existing test was edited, raised here as AC-4 requires.**
  `src/shared/ui/__tests__/SModal.test.ts` asserted the source text
  `max(24px, env(safe-area-inset-<side>, 0px))` per edge. No behaviour moved - `--space-6`
  is 24px and `tokens.test.ts` pins that - but the assertion was written against a spelling
  this dossier exists to change. It now asserts the token form, which is stronger: it pins
  both that every edge is inset and that the gutter comes from the design system. **§6's C-2
  claim that no existing test asserts a raw CSS value is false by exactly one test**; the
  other five source-reading tests assert `overflow`, `position`, `justify-content` and
  `z-index` and were untouched.
- **D-11 - the baseline was captured, then immediately compared against unmodified code,
  and that self-check found three harness defects.** Each would have shipped as an
  intermittent CI failure. (a) `margin: auto` centres against the content width, so a text
  change read as a spacing regression. (b) `span.sr-only` is a utility, not a component - it
  renders at 12px/600 inside a button and 16px/400 beside one, and which came first in DOM
  order moved with the data; recording the set rather than the first occurrence made the key
  order-independent. (c) A visible `<main>` is not a settled page, so the capture now waits
  for the element count to stop moving. A fourth was found later: the landing surface was
  recording the intro curtain and none of `Landing.vue`, and the curtain's skip hint is a
  timed state - the surface now dismisses the intro first, which both stabilises it and adds
  the whole landing page to the covered set. The baseline was recaptured at HEAD with the
  step-3 edits stashed, so it still describes unmodified rendering.
- **D-12 - AC-6's last clause is stricter than the dossier's own scope.** "No component spec
  states a literal size outside the token definition table" would forbid the literals §3 and
  Q-2 explicitly keep. Four classes stay literal - border widths, icon boxes, the 44px touch
  floor, and modal/drawer width presets - and `01-design-system.md` §1 now names those four
  classes, so a reader can tell an out-of-scope literal from a missed one. Without that line
  the rule reads as "no numbers", which would invite somebody to tokenise a 3px indicator bar.
- **D-14 - the parity spec is numbered `00-`, and that is load-bearing.** It was `24-`
  until the full suite was run end to end, which is the only thing that could have found
  this: running last, it reported 48 vanished signatures and 10 value differences, and
  **none of them was a CSS change**. The suite posts messages, so the chatroom's empty state
  stops rendering; it creates an invite, so the invites empty state goes; and
  `.s-empty-state` declares no font-size of its own, so where it lands decides what it
  inherits - it read 14px in a panel and 16px elsewhere. A parity baseline can only be
  compared against the data state it was captured in, so the spec now runs before anything
  mutates the seed. CI creates a fresh stack and bootstraps it per run
  (`ci.yml:948-1005`), so `00-` there means the pristine seeded state, which is what the
  baseline describes.
- **D-15 - two suite failures were investigated and are not this diff.** The full run also
  failed `02-org-project-flow` ("invite a member to org") and
  `18-delegated-activity-control` ("granting with nothing selected is refused"). Playwright
  reported `element(s) not found` for both, and CSS cannot remove an element from the DOM.
  The first is conclusive from its own artifact: the captured page shows the server's
  rate-limit toast, "Too many invitations. Please wait." - `global-setup.ts` raises the
  `auth`, `auth-recovery` and `other` buckets but nothing raises the invite limit, so a
  stack that has served several suite runs trips it. The second is shared-room state between
  the two tests in that file. Both are recorded as FU-10 rather than fixed here.
- **D-13 - a behaviour does change, in a case AC-1's baseline cannot see.** Ninety-odd
  spacing declarations were written in `rem` and now resolve through `px` tokens, because
  `--space-*` has been px since `2026-07-05-sitewide-ui-enhancement` and AC-8 forbids
  changing it. They are identical at the default 16px root font size, which is what the
  parity baseline measures, but a reader who raises their browser font size no longer scales
  that spacing. The type ramp moves the other way and strictly improves: `14px` and `12px`
  literals now resolve to `0.875rem` and `0.75rem`, so text that previously ignored the
  setting now honours it. Recorded as FU-7 for phase 2, which owns the token values.

## 11a. Verification

- **AC-1 was rerun after every one of steps 3 to 7**, not only at the end, and passed each
  time, against a live compose stack.
- **The full e2e suite was run twice**, which is what found D-14 and D-15. First run:
  103 passed, 3 failed, 29 skipped - the parity failure was the spec's own ordering (D-14).
  Second run, after the rename: **104 passed**, parity green in suite order, and the same
  two pre-existing failures reproducing identically, which is itself evidence they are
  environmental rather than an order-dependent flake this diff introduced (D-15, FU-10).
- **Every changed line in every changed `.vue` file was proven to sit inside a `<style>`
  block** - 118 files, checked mechanically against the diff hunks rather than assumed. A
  substitution that landed in a template or a script block would not necessarily fail any
  test.
- **Every `var(--token)` reference in `src/` resolves to a declaration.** An unresolvable
  `var()` falls back to the initial value silently, and the parity baseline cannot see that
  on the six components it does not reach. Nothing this dossier introduced is unresolved;
  the check did surface five pre-existing colour tokens that are referenced and never
  declared (FU-5).
- **Tailwind v4 emits only referenced `@theme` variables** - `--weight-bold` and
  `--font-size-3xl` were absent from `dist` before this change - so the two new namespaces
  generate no utility classes and add nothing to the bundle. That closes §8's collision risk
  by measurement rather than by reasoning.
- **`check-security` is N/A.** The diff changes no logic: 118 `.vue` files changed only
  inside `<style>` blocks, plus three test files, a docs rewrite and a committed baseline.
  The baseline was checked for sensitive content and holds no UUID, address, token or
  credential - only tag names, class names already public in the source, and CSS values.

## 12. Follow-ups

- **FU-1** - the three undocumented one-off sizes (11px in `LocaleToggle.vue` and
  `AppSidebar.vue:289`, `0.7rem` in `STabs.vue`, `0.9375rem` in `SIdleDialog.vue`) keep their
  literal values per Q-3. Phase 2 should decide whether each snaps to `--font-size-2xs`
  (10px) or `--font-size-xs` (12px), or whether an 11px step is a real part of the ramp.
  The `0.7rem`/`0.6875rem` pair being 0.2px apart is the strongest evidence that nobody
  chose either.
- **FU-2** - the T-2 sweep is a Vitest test standing in for a lint rule, exactly as
  `2026-08-19-content-area-spacing-and-scroll-contract`'s FU-6 records for its own view-root
  sweep. A single custom ESLint rule could enforce both and would fail at the point of
  writing. Worth doing once there are two such sweeps to justify it, which there will be
  after this dossier lands.
- **FU-3** - `--color-accent-tint-hover` (`main.css:96`, dark at `:217`) still has zero
  consumers, carried from `2026-07-05-sitewide-ui-enhancement`'s FU-9. Phase 2's hover and
  active-state work is the natural place to either adopt or delete it; this dossier changes
  no colour and leaves it alone.
- **FU-4** - eight components (`SWakeupEditor`, `SCodeEditor`, `SAccordion`, `SDivider`,
  `SRadio`, `SProgressBar`, `SIdleDialog`, `LocaleToggle`) are not reachable from any C-0
  surface, so their substitutions are proven equal by token value but never compared as a
  rendered box. Extending the e2e surface set to reach them is worth doing if phase 2's
  density work touches them again. **Superseded by FU-8**, which measures the gap that
  actually shipped.
- **FU-5** - five colour custom properties are referenced and never declared anywhere, so
  each falls back to the initial value: `--color-primary-600`
  (`ProjectMemberGroupsView.vue:350`), `--color-surface-2`
  (`PromptAssistantPanel.vue:162,186`), `--color-primary-soft` (`PromptAssistantPanel.vue:162`),
  `--color-text` (`GraphragGraphView.vue:128`) and `--color-surface-sunken`
  (`SchemaBuilder.vue:155`). Pre-existing and outside this dossier's scope (colour is §2.1's
  explicit exclusion), found by the resolvability check in §11a. Phase 2 owns the palette
  and is where these should be either mapped onto a real token or deleted.
- **FU-6** - `@layer base` restores a bare `h1`/`h2`/`h3` one ramp step below the page-level
  roles `00-overview.md` §3 documents (`--font-size-xl`/`lg`/`md` against
  `--font-size-2xl`/`xl`/`lg`). The two have always disagreed; making both read tokens is
  what made it visible. Correcting it changes rendered pixels, so it belongs to phase 2. The
  document now states the discrepancy rather than hiding it.
- **FU-7** - the spacing scale is px and the type ramp is rem, so spacing does not follow the
  reader's browser font size while type does. D-13 explains why this dossier could not
  resolve it (AC-8 forbids changing an existing token value). Phase 2 should decide whether
  `--space-*` becomes rem; if it does, every consumer this dossier created follows
  automatically, which is the whole point of the exercise.
- **FU-8** - six components are still not reachable from any C-0 surface: `SAccordion`,
  `SProgressBar`, `SSkeleton`, `SLoadingSpinner`, `SNetworkBanner` and `STextarea`. All six
  are conditional or transient states (a loading skeleton, a spinner, a connection banner)
  rather than components nobody thought of, which is why the surface set cannot reach them by
  navigating. Their substitutions are proven equal by token value (AC-2) and by the source
  sweep (AC-3), but no rendered box is compared.
- **FU-10** - two pre-existing e2e fragilities, found by running the full suite (D-15).
  `global-setup.ts` raises the `auth`, `auth-recovery` and `other` rate-limit buckets for the
  run but nothing raises the invite limit, so `02-org-project-flow`'s invite fails with
  "Too many invitations. Please wait." on a stack that has served several runs - invisible in
  CI, where the stack is new every time, and a reliable local trap. And
  `18-delegated-activity-control`'s two tests share one seeded room, so the second depends on
  what the first left behind. Neither is caused by this diff.
- **FU-11** - the parity baseline is only meaningful against a freshly seeded stack (D-14).
  Nothing enforces that beyond the `00-` filename, and a future spec numbered lower would
  silently break it. If a third such ordering constraint appears, it wants a Playwright
  project or a dependency rather than a naming convention.
- **FU-9** - four tokens now have zero consumers: `--font-size-3xl` (30px), `--line-tight`
  (1.3), `--space-10` (40px) and the pre-existing `--color-accent-tint-hover` carried from
  FU-3. Before this dossier a token with no `var()` reference might still have been "in use"
  as a literal somewhere; now that the ramp is fully consumed, zero consumers is a real
  statement about the vocabulary. Phase 2 should adopt or delete each.
