---
type: feature
status: approved
created: 2026-07-05
requirements: [R20.05, R20.08, R24.26, R24.27, R24.28, R24.29, R24.30, R24.31, R24.33, R24.34, R24.39, R24.48]
---

# Site-wide UI Enhancement Program (L1-L5)

## 1. Summary

Raise the visual quality of the entire SMAP frontend (all slices; the landing page was
upgraded separately and is out of scope) from "structurally mature but flat" to a
polished, motion-consistent product. Five layers, delivered bottom-up so each layer is
independently committable and the shared-component layers propagate automatically to all
~60 views: L1 foundation tokens, L2 shared component upgrades, L3 app-shell motion,
L4 view sweep + legacy CSS removal, L5 motion language. Visual identity stays light
blue/grey; motion intensity is "restrained professional".

## 2. Goals and Non-goals

**Goals**
- Complete the token vocabulary: spacing, typography, semantic elevation, and
  interactive-state colors, in both themes.
- Make the most-used surfaces (SCard, toasts, empty/loading states) visually rich.
- Give the app shell and route changes tasteful motion.
- Bring the workflow slice and auth scaffold onto the S* component system, then delete
  the legacy `@layer components` styling path.
- Define one documented motion language so future views stay consistent.

**Non-goals**
- No redesign of the visual identity (colors, logo, typography family stay).
- No touching already-polished components: STable, SModal, SDrawer, SDropdown
  internals (except consuming new tokens).
- No landing-page changes (just upgraded).
- No functional/behavioral changes anywhere — this is visual + structural only.
- No new npm dependencies.
- No Vue Flow canvas rework: node rendering, editor logic (`useWorkflowEditor`,
  `useWorkflowLint`), and responsive gating stay as-is; only the surrounding chrome
  (toolbar buttons, config form controls) migrates.
- `.sr-only` / `.visually-hidden` / `.skip-link` in `main.css` are load-bearing a11y
  utilities used app-wide (e.g. `AppShell.vue:64`, `STable.vue:211`) — explicitly NOT
  part of the legacy removal.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Scope: full L1-L5, L1-L3+L5 only, or top-3 wins? | Full L1-L5, phased delivery with independent commits per layer | User wants the complete program; layering keeps each phase revertible |
| Q-2 | Motion intensity for the site-wide spec? | Restrained professional: route transitions 150-200ms fade+rise, hover lift 1-2px, list stagger first-load only | B2B tool used daily; showy motion fatigues heavy users |
| Q-3 | Remove legacy CSS (`.btn`/`.table`/`.auth-card`/`.wf-input`/`.form-page`)? | Yes, remove after migrating consumers (folded into L4) | Eliminates the dual styling path that lets new views drift off-brand |

## 4. Current State

Verified by two exploration passes (2026-07-05).

**Tokens** — `frontend/src/shared/styles/main.css` `@theme` block (lines 8-83): semantic
colors incl. status tint/on pairs, shadows (`--shadow-sm..xl`, dialog/overlay), radii,
two fonts, one extra font-size token (`--font-size-2xs`, line 62), transition durations
(68-70), focus ring (82), layout vars and z-index scale (72-80). Dark overrides at
119-155. Gaps: no spacing scale, no typography scale, no semantic elevation tiers, no
hover-state fills — hovers reuse `--color-border` as a fill (e.g. SButton secondary).

**Shared UI** — 43 components in `frontend/src/shared/ui/`. Polished: STable (shaped
skeletons, card fallback, sort/select/sticky), SModal/SDrawer/SDropdown (animated),
SAlert/SBadge/SStatusBadge. Flat: `SCard.vue:20-47` is a div with radius + border;
`default`/`bordered` variants render identically, `elevated` is `shadow-sm` only, no
hover/transition/header/footer. SSkeleton is pulse-only. SEmptyState is a bare
icon+title+text. Toaster is stock vue-sonner (`App.vue:34-38`, `rich-colors`).

**Shell** — `App.vue:31` renders `<router-view :key="$route.path" />` with no
Transition: routes hard-swap and force-remount. `AppShell.vue` collapse switches
`grid-template-columns` without a tween (transition on line 127 is inert because the
sidebar is `v-if`-removed at line 75). `AppSidebar.vue:297-302` active item = tinted bg
+ 3px left border, no animated indicator. `AppTopBar.vue` flat, no scroll shadow.

**Legacy `@layer components` block** — `main.css:222-436`. Consumer inventory:
- `.btn/.btn-primary/.btn-danger/.btn-sm` (224-274): workflow slice only — 7 files
  (`WorkflowListView.vue:27`, `WorkflowRunView.vue:37`, `WorkflowRunsListView.vue:15,20`,
  `WorkflowEditorView.vue:31,60,69,80,88,96,287`, `AgentOrchestrationView.vue:81`,
  `NodeConfigPanel.vue:93`). `SButton.vue` uses its own `.s-btn` (false positive).
- `.wf-input/.wf-input-code` (316-326): `WorkflowListView.vue:21`,
  `WorkflowEditorView.vue:278`, `NodeConfigPanel.vue:76`, and all 12 config forms under
  `slices/workflow/components/config/` (raw `<select>/<input>/<textarea>` inside
  SFormField wrappers; no SSelect/SInput/STextarea/SCheckbox anywhere).
- `.table` (329-344): `WorkflowRunView.vue:70`, `WorkflowRunsListView.vue:60`,
  `WorkflowListView.vue:58`, `DlqViewer.vue:88`.
- `.auth-card/.auth-heading/.auth-form/.auth-footer` (347-394): full scaffold in
  `LoginView.vue:124+`, `RegisterView.vue:112+`, `PasswordResetRequestView.vue:53+`,
  `PasswordResetConfirmView.vue:84+`, `VerifyEmailView.vue:45+` (local `.auth-heading`
  override at 117), `GuestLandingView.vue:77` (local override at 162); `.auth-form`
  alone in `ProfileView.vue:61`, `ChangeEmailView.vue:137`, `ChangePasswordView.vue:101`,
  `DeleteAccountView.vue:95`.
- `.form-page` family (277-313) and `.truncate-line` (431-435): zero consumers — dead.
- `.visually-hidden/.sr-only` (396-407) and `.skip-link` (411-429): app-wide a11y,
  keep (see Non-goals).

**Workflow slice** — 6 views. All use SPageHeader; some use SAlert/SEmptyState/
SStatusBadge/SFormField; every actual control and table is hand-rolled with legacy
classes (details above). `WorkflowBackstageView.vue:12` hand-rolls a raw `<select>`
without even `.wf-input`. Loading is `SLoadingSpinner`, not skeletons.
`WorkflowEditorView.vue` embeds Vue Flow (`:299-302` imports incl. its CSS,
`:358-360` custom node type via `markRaw`, responsive gating `:160-190`).

**Mobile duplication** — ChatroomListView and AgentListView render a manual
`v-if="isMobile"` SCard list duplicating the table, although STable ships a card-list
fallback (STableCards).

**Motion** — `<Transition>` in only 6 component files; no route/view-level motion; no
list stagger outside ChatroomView; skeleton-vs-spinner loading language is mixed.

**Tests** — every view has tests via `tests/utils/render.ts` `renderView`. No test
asserts on legacy class names (verified: button selectors target `.s-btn--*`;
`LoginView.test.ts:26-28` selects `button[type="submit"]`). E2E `.link-btn` match is
unrelated.

**SRS divergence** — R24.27 (Element Plus), R24.29 (no Tailwind), R24.28/R24.30
(`tokens.css`) contradict the shipped reality: Tailwind v4 with tokens in `main.css`
`@theme` and a custom S* library (`frontend/CLAUDE.md`; `main.css:1`). SRS Delta (§13)
corrects this.

## 5. Design

### Options considered

**Option A — Layered in-place upgrade (bottom-up L1→L5)**: extend tokens, upgrade
shared components behind backward-compatible props, add shell motion, migrate outlier
views, delete legacy CSS last. Trade-off: many small diffs across shared files; each
layer independently shippable and revertible; zero new deps.

**Option B — Design-system rebuild**: new `shared/design/` package with fresh
primitives, migrate views wholesale. Trade-off: cleanest end state but weeks of churn,
violates "no rebuild needed" — the S* system is structurally sound.

**Option C — Top-3 wins only** (route transitions, SCard, toasts): fastest perceived
improvement, but leaves the dual styling path and workflow outlier untouched.

### Decision

Option A. The survey shows the component system is already consistently adopted
(SPageHeader in 50 views), so upgrading shared layers propagates for free; the only
structural debt (legacy CSS + workflow hand-rolling) is bounded and fully inventoried.
Consciously given up: the "perfect" greenfield design system (Option B) — not worth the
churn for a solo-maintained product.

## 6. Detailed Changes

- **Backend** — none. **API contract** — none, no `gen:api`.
- **Deploy/config** — none.

**Frontend, per phase:**

**L1 — Foundation tokens** (`shared/styles/main.css` only)
- Spacing scale `--space-1..12` (4px base grid).
- Typography scale: `--text-xs..--text-3xl` with paired `--leading-*`, plus
  `--weight-medium/semibold/bold`.
- Semantic elevation: `--elevation-0..3` (0 = border only, 1 = card resting,
  2 = raised/hover, 3 = overlay) mapping onto existing `--shadow-*`, dark-theme tuned.
- Interactive-state tokens: `--color-surface-hover`, `--color-surface-active`,
  `--color-accent-tint-hover`. Replace `--color-border`-as-hover-fill in SButton
  (secondary/ghost), AppSidebar nav hover, AppTopBar toggle.
- Motion tokens (consumed by L3/L5): `--motion-rise` (6px), `--ease-out-soft`,
  `--ease-spring`; reuse existing `--transition-*` durations.

**L2 — Shared components** (`shared/ui/`)
- `SCard.vue`: variants become visually distinct (`default` = border,
  `elevated` = `--elevation-1` resting); new `hoverable` prop (lift -1px +
  `--elevation-2`, transition); optional `header`/`footer` slots with padded,
  bordered sections; existing call sites unchanged (all new behavior behind
  defaults/props).
- Toaster theming: token-driven styles for vue-sonner (via `toastOptions`/CSS
  overrides in a shared stylesheet), light+dark, status tint/on colors; drop
  `rich-colors` stock look.
- `SSkeleton.vue`: add shimmer sweep variant (reduced-motion falls back to pulse/static).
- `SEmptyState.vue`: icon in a tinted halo circle, tightened type hierarchy using L1
  typography tokens.

**L3 — Shell motion** (`app/`)
- `App.vue`: wrap router-view in `<Transition name="route" mode="out-in">` with
  fade + 6px rise, 150-200ms; review the `:key="$route.path"` strategy — keep remount
  where param-driven views need it but scope the key so query-only changes do not
  remount; reduced-motion collapses to opacity-only via the global freeze.
- `AppSidebar.vue`: animated active indicator (the 3px accent bar tweens between items
  via `transform` on a single indicator element or per-item transition).
- `AppTopBar.vue`: shadow + border deepen after scroll-y > 0 (scroll listener,
  rAF-throttled).
- `AppShell.vue`: sidebar collapse tweens (animate `grid-template-columns` via the
  existing CSS var, keep `v-if` content swap after the tween or use width+overflow).

**L4 — View sweep + legacy removal**
- Workflow slice: replace `.btn*` buttons with SButton (variants map: primary→primary,
  danger→danger, bare→secondary/ghost, `btn-sm`→size sm); `.wf-input(-code)` controls
  with SInput/SSelect/STextarea/SCheckbox (code variant: STextarea with mono class or
  SCodeEditor where appropriate); `.table` with STable (columns + skeleton loading,
  replacing SLoadingSpinner); `WorkflowBackstageView.vue:12` raw select → SSelect.
  Preserve Vue Flow canvas, node type registration, and `useBreakpoint` gating
  untouched.
- Auth scaffold: new `shared/ui/SAuthCard.vue` (or scoped styles in AuthLayout)
  replicating `.auth-card/.auth-heading/.auth-form/.auth-footer` with tokens; migrate
  the 6 full-scaffold views + 4 `.auth-form`-only settings views; reconcile local
  `.auth-heading` overrides in `VerifyEmailView.vue:117` and `GuestLandingView.vue:162`.
- Remove manual `v-if="isMobile"` card branches in ChatroomListView and AgentListView
  in favor of STable's built-in card mode.
- Delete from `main.css`: `.btn*`, `.form-page*`, `.wf-input*`, `.table`, `.auth-*`,
  `.truncate-line` (222-394 range minus a11y utilities); keep `.sr-only`,
  `.visually-hidden`, `.skip-link`.
- i18n: any new strings (e.g. table column headers formerly hardcoded) through `$t()`
  in both `en.json` and `zh-TW.json`.

**L5 — Motion language**
- Document the motion spec (durations, easings, distances, when-to-animate rules) as a
  commented section in `main.css` next to the motion tokens plus a short
  `docs/frontend-motion.md`.
- First-load list stagger for CRUD list views: a small shared composable/utility
  (e.g. `useListStagger` or CSS-only `animation-delay` via row index cap) applied to
  STable rows and card grids; runs once per view mount, disabled under reduced motion.
- Card hover micro-interaction: SCard `hoverable` (from L2) adopted by the list views
  that render card grids.

## 7. NFR Checklist

- [ ] i18n — no new user-facing strings expected except workflow table/control labels
      already in locale files; anything new goes through `$t()` in en + zh-TW.
- [ ] Audit log — N/A, no domain events (visual only).
- [ ] Tenant isolation — N/A, no endpoints touched.
- [ ] Error handling UX — loading states improve (skeleton unification); error/empty
      states preserved through migrations.
- [ ] Performance — CSS-only where possible; scroll/stagger JS is rAF-throttled;
      bundle budget gate must stay green (no new deps; expect < 5 KB gzip growth).

## 8. Security Considerations

None — no sensitive surface touched. Purely presentational; no new user-input
processing (existing form controls swap to S* equivalents that already own validation
wiring via vee-validate/SFormField).

## 9. Quality Notes

- **Existing debt** (record, do not imitate): dual styling path (legacy block vs S*);
  hand-rolled controls inside SFormField in workflow config forms; duplicated mobile
  card branches; `WorkflowEditorView.vue:351-360` documented `as unknown as
  NodeComponent` cast (leave as-is); local `.auth-heading` scoped overrides.
- **Patterns to follow**: STable usage in `AgentListView.vue`/`KeyListView.vue`
  (columns + cell slots + skeleton + empty slot); SModal create-flow in
  `ChatroomListView.vue`; transition patterns in `SModal.vue`/`SDropdown.vue`;
  reduced-motion handling in `usePrefersReducedMotion.ts` and the global freeze
  (`main.css:209-218`); tilt/motion discipline in `AgentConstellation.vue`.
- **Reuse inventory**: SButton, SInput, SSelect, STextarea, SCheckbox, SToggle,
  SFormField, SCharCount, SCodeEditor, STable (+card mode), SPageHeader, SCard,
  SEmptyState, SSkeleton, SAlert, SStatusBadge, SLoadingSpinner, `useBreakpoint`,
  `usePrefersReducedMotion`, `useRevealOnScroll`, tokens in `@theme`. Do not write new
  buttons/inputs/tables anywhere.

## 10. Risks and Rollback

- **Workflow migration regressions** — biggest surface (6 views + 12 config forms).
  Mitigation: R24.39 integration tests already cover these views and assert on
  behavior/roles, not legacy classes (verified); migrate form-by-form with tests green
  between commits. Rollback: each phase is an independent commit series; L4 commits are
  per-view-group.
- **Route transition breaking view-local state** — changing the `:key` strategy can
  alter remount semantics. Mitigation: keep param-scoped keys; verify chat and editor
  routes manually (verify skill).
- **Vue Flow CSS interplay** — token changes must not restyle Vue Flow internals; its
  stylesheet imports stay untouched.
- **Visual regressions in dark theme** — every new token needs a dark value; behavioral
  verification includes dark-theme screenshots.
- No migrations, no API changes — rollback is `git revert` per phase.

## 11. Acceptance Criteria

- [ ] AC-1: `main.css` `@theme` defines spacing (`--space-*`), typography
      (`--text-*`/`--leading-*`/`--weight-*`), elevation (`--elevation-0..3`),
      interactive-state (`--color-surface-hover` etc.), and motion tokens, each with
      dark-theme values where color-bearing.
- [ ] AC-2: no component or shell style uses `--color-border` as a hover/active fill;
      they consume the new state tokens (grep-verifiable).
- [ ] AC-3: SCard `default` and `elevated` are visually distinct; `hoverable` prop
      lifts 1-2px with elevation change on hover; `header`/`footer` slots render
      padded, separated sections; all existing SCard call sites render unchanged
      without edits; component test covers the new API.
- [ ] AC-4: toasts are token-themed in light and dark (no stock sonner palette);
      success/error/warning/info map to the status tint/on tokens.
- [ ] AC-5: SSkeleton offers a shimmer variant that degrades to non-animated under
      reduced motion; SEmptyState renders the tinted-halo icon treatment.
- [ ] AC-6: route changes animate with a 150-200ms fade+rise via `<Transition>`
      `mode="out-in"`; query-only URL changes do not remount the view; param changes
      that require remount still remount; reduced-motion users get no movement.
- [ ] AC-7: sidebar active indicator animates between nav items; topbar gains
      shadow/border depth once scrolled; sidebar collapse/expand tweens instead of
      snapping; all three inert under reduced motion.
- [ ] AC-8: workflow slice contains zero raw `<button>`, form controls, or `<table>`
      styled by legacy classes — S* equivalents throughout; workflow list/run views
      load with skeletons, not SLoadingSpinner; Vue Flow canvas behavior (desktop
      edit / tablet read-only / mobile message) unchanged.
- [ ] AC-9: the 6 auth-scaffold views and 4 `.auth-form` settings views render via the
      new shared auth-card treatment; the two local `.auth-heading` overrides are
      gone.
- [ ] AC-10: `main.css` no longer defines `.btn*`, `.form-page*`, `.wf-input*`,
      `.table`, `.auth-*`, `.truncate-line`; `.sr-only`/`.visually-hidden`/`.skip-link`
      remain; repo-wide grep finds zero consumers of the deleted classes.
- [ ] AC-11: ChatroomListView and AgentListView have no manual `isMobile` card branch;
      mobile rendering goes through STable's card mode.
- [ ] AC-12: list views show a first-load-only stagger (≤ 300ms total, disabled under
      reduced motion); the motion spec is documented (`main.css` comment block +
      `docs/frontend-motion.md`).
- [ ] AC-13: `pnpm test`, `pnpm typecheck`, `pnpm build`, bundle-size check pass; the
      12 lint gates pass on all touched files (repo-wide `pnpm lint` remains red from
      pre-existing warnings — see FU-1); every touched view keeps ≥ 1 passing test.

## 12. Test Plan

- AC-1/2/10: grep assertions + visual pass; token presence is reviewed in diff.
- AC-3/4/5: component tests in `shared/ui/__tests__/` (SCard slots/props/classes,
  SSkeleton variant class, SEmptyState structure); toast theming verified behaviorally.
- AC-6/7: unit-testable pieces (transition name on route wrapper, scroll class toggle)
  in `app/__tests__/`; full motion verified behaviorally via the `verify` skill
  (Playwright screenshots light+dark, reduced-motion run).
- AC-8/9/11: existing view integration tests (R24.39) must stay green after each
  migration; selectors already target roles/`.s-btn--*`; add assertions where a view
  gains STable (skeleton presence, card mode).
- AC-12: composable/CSS unit test for stagger cap + reduced-motion; behavioral check.
- AC-13: full gate run per phase commit and at program end.

## 13. SRS Delta

Amend §24.9 (styling reality diverged from SRS long before this task; this delta makes
the SRS truthful and adds the new token/motion requirements):

- **[R24.27]** *(amended)* Base library: the in-house `S*` component library under
  `frontend/src/shared/ui/` (43 components). No external UI kit; Element Plus was
  dropped before v1 implementation.
- **[R24.28]** *(amended)* Design tokens live in `frontend/src/shared/styles/main.css`
  in the Tailwind v4 `@theme` block as CSS custom properties, themed light/dark via the
  `data-theme` attribute on `<html>`. The token vocabulary covers color (semantic +
  status tint/on), spacing, typography, radius, shadow/elevation, motion
  (duration/easing/distance), focus ring, layout, and z-index scales.
- **[R24.29]** *(amended)* Tailwind CSS v4 is the utility layer (via
  `@tailwindcss/vite`); component styling uses scoped `<style>` blocks consuming the
  `@theme` tokens.
- **[R24.30]** *(amended)* Scoped styles only. Global CSS is restricted to
  `shared/styles/` (tokens, base, a11y utilities `.sr-only`/`.skip-link`, third-party
  overrides). Enforced by lint gate 6.
- **[R24.49]** *(new)* Motion language: interface motion follows a documented
  restrained-professional spec — route transitions 150-200 ms fade + ≤ 6 px rise,
  hover elevation lift 1-2 px, first-load-only list stagger ≤ 300 ms total, easings and
  durations from the motion tokens. All motion collapses under
  `prefers-reduced-motion: reduce`.

## 14. Open Questions

- Whether `SCodeEditor` or a mono STextarea is the right target for each
  `.wf-input-code` site — decide per-field during L4 (both are in the reuse inventory).

## 15. Deviation Log

None yet.

## 16. Follow-ups

- FU-1: repo-wide `pnpm lint` fails on `main` with 295 pre-existing warnings
  (`--max-warnings=0`; mostly unused vars in keys/other slices) — needs its own cleanup
  task; out of scope here.
