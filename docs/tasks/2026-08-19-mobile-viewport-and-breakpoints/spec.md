---
type: bugfix
status: draft
created: 2026-08-19
requirements: []
depends_on: [2026-08-19-content-area-spacing-and-scroll-contract]
---

# Mobile viewport units, safe areas, and breakpoint boundaries

Source: `docs/audits/2026-08-19-page-presentation-scroll-and-feedback/findings.md`
(F-18, F-25, F-39, F-42, F-45, F-46).

## 1. Summary

On a phone the app is sized against the wrong viewport, draws no distinction between the
screen and the parts of it the device owns, and disagrees with itself about where the
breakpoints are. `AppShell` is `height: 100vh` (`frontend/src/app/layouts/AppShell.vue:141`)
while the other three layouts are `100dvh`, so on first paint the shell is taller than the
visible area by the browser toolbar height and its bottom grid row is below the fold. The
keyboard-inset composable measures against `window.innerHeight`
(`frontend/src/shared/composables/useVisualViewport.ts:26`) and subtracts the result from an
element sized against that same `100vh` shell, so the chat composer ends up under the
keyboard by exactly the toolbar height. Nothing in the product reads
`env(safe-area-inset-*)` and `frontend/index.html:5` does not opt into the display cutout,
so bottom-anchored controls sit in the home-indicator strip. Seventeen media queries stop at
`480px`/`768px` inclusive where the breakpoints are exclusive, so a viewport at exactly 480
or 768 CSS px gets the smaller layout while `useBreakpoint()` reports the larger one. The
mobile sidebar drawer is 320px against a specified `min(280px, 85vw)`, and the sidebar
inside it overflows the drawer below about 362px of viewport width. And the agent detail
view's mobile action bar is `position: fixed` with nothing reserving its space, so it
permanently covers the end of the form.

None of these is the reported user complaint. They are the mobile half of the same audit,
grouped here because they are one question asked six times: what box is the app actually
being laid out against.

## 2. Observed vs Expected

### F-45 (minor) - the shell is sized in `vh` where every other layout uses `dvh`

- **Observed** - `frontend/src/app/layouts/AppShell.vue:141` is `height: 100vh` inside a
  rule (`:137-146`) that also sets `overflow: hidden` (`:142`) and the two grid tracks
  (`:139-140`). The three sibling layouts are all dynamic:
  `frontend/src/app/layouts/AuthLayout.vue:27` and `:77`,
  `frontend/src/app/layouts/PublicLayout.vue:14`, `frontend/src/app/views/Landing.vue:345`.
  A repository-wide grep for viewport-height units in `frontend/src` returns exactly seven
  hits: those five, plus `slices/agents/views/AgentDetailView.vue:964` and
  `slices/agents/views/GraphragGraphView.vue:168`, both of which are `100vh` inside views
  (see §6).
- **Expected** - `docs/UI/02-layout-shell.md:96-116` now specifies the shell as
  `height: 100dvh`, with the reason recorded verbatim at `:111-116`: `vh` resolves against
  the large viewport, so a `100vh` shell is taller than the visible area by the toolbar
  height and its bottom grid row is below the fold on first paint.
- **Status change from the audit.** The audit graded F-45 `plausible` on two grounds, one of
  which no longer holds: it recorded `docs/UI/02-layout-shell.md:101` as mandating
  `height: 100vh`, making this an intent-source conflict rather than a defect. That line has
  since been amended (it is now `:102`, reading `height: 100dvh`, followed by the rationale
  at `:111-116`). With the intent source corrected, F-45 is a plain code-versus-intent
  defect: one file deviates from a documented shell contract that three sibling layouts
  already implement. The audit's second mitigation (the document is scrollable, so the
  clipped row is reachable) survives and bounds the severity to first paint, but it does not
  make the code conformant. FU-6 of the audit, which asked for the spec half, is closed by
  that amendment; this dossier owns the code half.

### F-46 (minor) - the keyboard inset is measured against a different viewport from the element it shrinks

- **Observed** - `frontend/src/shared/composables/useVisualViewport.ts:26` computes
  `window.innerHeight - vv.height - vv.offsetTop`, i.e. against the layout viewport. The
  value is bound as `--kb-inset` on the chatroom root (`ChatroomView.vue:7`, enabled only on
  mobile via `:367`) and consumed by
  `.chatroom--mobile { height: calc(100% - var(--kb-inset, 0px)) }`
  (`ChatroomView.vue:1015-1019`). That `100%` chains through `main.app-shell__content`
  (`AppShell.vue:123-132`, grid row `1fr` of `:140`) to the `100vh` shell, i.e. the large
  viewport. The two references differ by the toolbar height.
- **Expected** - `docs/UI/11-responsive-a11y.md:136`: "Composer sticks to bottom above
  virtual keyboard (uses `visualViewport` API)". The composer being partly under the
  keyboard is the failure that line exists to prevent.

### F-25 (minor) - no safe-area handling, and the viewport meta does not opt into the cutout

- **Observed** - `frontend/index.html:5` is
  `<meta name="viewport" content="width=device-width, initial-scale=1.0" />`. A grep for
  `safe-area` or `env(` across `frontend/src` returns no CSS hit at all; every match is the
  unrelated `env()` test-fixture helper in `frontend/e2e/` (declared at
  `frontend/e2e/fixtures/seed.ts:44`). Bottom-anchored and edge-anchored UI that has no
  inset: the chat composer (`ChatroomComposer.vue:252-257`, rendered as grid row 4 flush to
  the shell bottom per `ChatroomView.vue:980-983`), the new-messages pill
  (`ChatroomView.vue:1001-1006`, `bottom: 16px`), the agent action bar
  (`AgentDetailView.vue:1274-1277`, see F-18), the drawer panel
  (`SDrawer.vue:126-135`, `top: 0; bottom: 0`), the full-screen mobile modal
  (`SModal.vue:258-276`), and the landing page's 24px gutters (`Landing.vue:342-349`).
- **Expected** - `docs/UI/11-responsive-a11y.md:171` (44x44px minimum usable hit area) and
  `:122` ("Composer | Fixed bottom"). A control whose hit area overlaps the home-indicator
  strip does not have a usable 44x44px target, because the system gesture wins.

### F-39 (minor) - media queries use inclusive breakpoint values

- **Observed** - the thresholds are declared twice, both as min-width values:
  `frontend/src/shared/composables/useBreakpoint.ts:5`
  (`BP = { xs: 0, sm: 480, md: 768, lg: 1024, xl: 1280 }`, described at `:3-4` as the
  "single source of truth for breakpoint pixel thresholds"), and
  `frontend/src/shared/styles/main.css:57-60` (`--breakpoint-sm: 480px` and siblings inside
  the `@theme` block opened at `:8`). Mobile-side rules must therefore stop at 479 and 767.
  Enumerating every width-conditional `@media` in `frontend/src` gives 26 blocks: 17 use the
  inclusive value, 7 use the correct exclusive value, and 2 use ad-hoc non-breakpoint widths.
- **Expected** - `docs/UI/11-responsive-a11y.md:11-17` (breakpoint table: `sm` is 480px
  min-width, `md` is 768px min-width) and `:73-80` (the auth card is 420px with a shadow at
  `sm+`, i.e. from 480px inclusive).
- **Correction to the audit's count.** The audit reported "fifteen media queries ... across
  15 files". Re-derived against the current tree it is **17 blocks across 14 files**. The
  audit's own enumeration lists 17 `path:line` pairs; the headline number and the file count
  were both wrong. The 17:

  | File | Lines | Value |
  |---|---|---|
  | `app/layouts/AuthLayout.vue` | 69 | 480 |
  | `app/views/Landing.vue` | 791 | 768 |
  | `shared/ui/SAuthCard.vue` | 63 | 480 |
  | `slices/identity/views/ChangeEmailView.vue` | 260 | 768 |
  | `slices/identity/views/ChangePasswordView.vue` | 207 | 768 |
  | `slices/identity/views/DeleteAccountView.vue` | 191 | 768 |
  | `slices/identity/views/ProfileView.vue` | 328 | 768 |
  | `slices/identity/views/SessionsView.vue` | 279 | 768 |
  | `slices/tenancy/styles/detail-cards.css` | 99 | 480 |
  | `slices/tenancy/styles/member-form.css` | 29 | 768 |
  | `slices/tenancy/views/InboxInvitesView.vue` | 300, 306 | 768, 480 |
  | `slices/tenancy/views/InviteAcceptView.vue` | 155 | 768 |
  | `slices/tenancy/views/OrgDetailView.vue` | 440, 446 | 768, 480 |
  | `slices/tenancy/views/OrgTransferView.vue` | 430, 436 | 768, 480 |

  The 7 already correct, which the fix must leave untouched: `shared/ui/SModal.vue:260`,
  `shared/ui/SDrawer.vue:162` and `:169`, `app/layouts/AppShell.vue:202` and `:216`,
  `slices/keys/views/KeyDetailView.vue:345`,
  `slices/notifications/components/NotificationCard.vue:181`. The 2 ad-hoc:
  `app/views/Landing.vue:752` (900px) and `:798` (560px), which are not breakpoint values
  and are out of scope (FU-2).

### F-42 (minor) - the mobile sidebar drawer is the wrong width, and its content overflows it

- **Observed, width half** - `AppShell.vue:113-121` renders the sidebar as
  `<SDrawer size="sm">`. `SDrawer.vue:145-148` is
  `.s-drawer__panel--sm { width: 320px; max-width: 85vw }`, and the responsive overrides at
  `:160-174` name only `--md` and `--lg`, so 320/85vw holds at every width.
- **Observed, overflow half** - `AppSidebar.vue:256-264` hard-sets
  `width: var(--sidebar-width)` (`:257`), which is 260px (`main.css:64`), with a 1px right
  border (`:261`) that falls inside that 260 because Tailwind's preflight
  (`main.css:1`, `@import "tailwindcss"`) makes every element `border-box`. The drawer body
  adds 24px of padding on each side (`SDrawer.vue:221-225`), and the sidebar is rendered
  into it through the default slot (`SDrawer.vue:96-98`). See §5 for the re-derived
  threshold.
- **Observed, z-index half** - `SDrawer.vue:113-118` sets `z-index: var(--z-modal)` (400) on
  the overlay root; `AppSidebar.vue:262` sets `--z-sidebar` (100) on the inner element.
- **Expected** - `docs/UI/11-responsive-a11y.md:58-59`: "Width: min(280px, 85vw)",
  "Z-index: --z-sidebar (100)". `:399` independently specifies `SDrawer` widths per
  breakpoint and does not cover the `sm` size at all.

### F-18 (major) - the mobile action bar is `fixed` with no reserved space

- **Observed** - `slices/agents/views/AgentDetailView.vue:1274-1277` is
  `fixed bottom-0 left-0 right-0 p-4 bg-bg border-t border-border flex gap-3 z-10`,
  rendered under `v-if="isMobile"` after the form's closing tag (`:1271`). It is the only
  bottom-anchored `position: fixed` element in the whole of `frontend/src/slices`
  (repository-wide grep for `fixed bottom-0` and `position: fixed` returns 10 hits: this
  one, `LandingIntro.vue:322` and `main.css:312` which are `inset: 0` / top-left overlays,
  `SNetworkBanner.vue:42` and `ImpersonationBanner.vue:19` which are top-anchored and owned
  by other dossiers, and four `SModal`/`SDrawer` overlay roots). Nothing reserves its
  height: the view root is `<main class="p-6">` (`:665`) with no bottom padding, and a
  `fixed` child is out of flow so it contributes nothing to `main.app-shell__content`'s
  scroll height.
- **Expected** - `docs/UI/11-responsive-a11y.md:110` ("Action buttons: stacked vertically on
  mobile instead of horizontal row") describes an in-flow bar, and `:385` requires no
  content overlap.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Why does this dossier depend on `2026-08-19-content-area-spacing-and-scroll-contract`? | Overlap prerequisite, not a logical one. Either could go first; they must not go concurrently. | Both edit `frontend/src/app/layouts/AppShell.vue` and `frontend/src/slices/agents/views/AgentDetailView.vue`. This dossier changes the shell's `height` (`:141`) and grid rows (`:140`) and the agent view's mobile action bar (`:1274-1277`); the sibling changes the shell's content-padding rules (`:188`, `:207-209`, `:216-218`) and the agent view's root `p-6` (`:665`). Same two style blocks, same two template regions. There is also a real ordering benefit: the sibling's removal of the view-root `p-6` changes what the action bar's containing block is, and building this second means the bar lands against the final geometry rather than being re-derived. |
| Q-2 | F-45: does `100dvh` need a `100vh` fallback declaration at the project's stated browser floor? | No fallback. Change `AppShell.vue:141` to `height: 100dvh` and declare nothing else. | Verified against caniuse `viewport-unit-variants`: the dynamic/small/large viewport units require Safari 15.4, Safari on iOS 15.4, Chrome 108, Firefox 101, Edge 108. (Its "Chrome for Android 151" cell is caniuse listing the single current Android release, not a floor; Android Chrome ships the same engine version as desktop, so 108 is the real threshold.) The project floor at `docs/UI/11-responsive-a11y.md:340-347` is iOS Safari 16.2+, Chrome Android 110+, and last-2-stable for Chrome/Edge/Firefox/Safari. Every floor clears 15.4/108. Independently, the codebase already ships `100dvh` unguarded in three layouts (`AuthLayout.vue:27,77`, `PublicLayout.vue:14`, `Landing.vue:345`); adding a fallback only in `AppShell` would be a fourth spelling of the same rule. |
| Q-3 | F-46: does fixing F-45 fully resolve it, or does the composable's arithmetic also need correcting? | Fixing F-45 fully resolves it. No numeric change to `useVisualViewport.ts`. Add a comment at `:25-26` naming the dvh coupling, and a unit test pinning the formula. | Re-derived rather than taken from the audit; see §5. With the shell in `dvh` the residual under-shoot is identically zero for every toolbar height and every keyboard height, not merely small. The load-bearing assumption is that `100dvh` and `window.innerHeight` denominate the same box (the layout viewport), which holds under the default `interactive-widget: resizes-visual`. The composable is enabled only on mobile (`ChatroomView.vue:367`), so the desktop classic-scrollbar discrepancy between `vh` and `innerHeight` never applies. |
| Q-4 | F-46: add `interactive-widget=resizes-content` to the viewport meta? | No. Record as FU-1. | Verified: `interactive-widget` is supported in Chrome 108+ and Firefox 132+ and is not implemented in WebKit, so it has no effect in any iOS browser (all of which are WebKit). Adding it would split keyboard behaviour between the two mobile engines and give iOS, the platform where the composable does the most work, nothing. It would also make the layout viewport shrink with the keyboard on Android only, so `--kb-inset` would compute to roughly 0 there and roughly `K` on iOS: two different mechanisms producing the same result, which is harder to reason about than one. |
| Q-5 | F-25: what is the scope, given that `viewport-fit=cover` changes layout on every notched device at once? | Add the meta **and** apply insets to a closed, named set of seven surfaces in the same change: `index.html:5`, `.app-shell` (`AppShell.vue:137-146` plus the topbar row at `:140`), `AppTopBar.vue:71-79`, `AuthLayout.vue:23-30` and its xs block `:69-80`, `Landing.vue:342-349`, `SDrawer.vue:126-135`, `SModal.vue:258-276`. Deferred: everything else, as FU-3. | `viewport-fit=cover` is a document-level switch with no per-surface granularity, so shipping it alone is a strict regression: today the browser insets the whole page and nothing is under the cutout, and the meta alone removes that protection from every surface simultaneously while adding it back to none. The seven surfaces are chosen because they are the complete set of present-day elements that touch a viewport edge: the shell (which protects every authenticated route with one rule), the top bar (which must grow into the status-bar strip rather than be pushed below it, so the bar's own background paints there), the two unauthenticated roots that have their own gutters, and the two overlays that Teleport to `body` and therefore sit outside the shell's padding box. `PublicLayout.vue:13-17` needs nothing: it adds no padding and only wraps `Landing`. |
| Q-6 | F-25: is `env()` safe to use unguarded at the stated floor? | Yes, but write every use as `max(<existing value>, env(safe-area-inset-*, 0px))` rather than as a bare addition. | `env()` with a fallback degrades to the fallback in any engine that does not know the variable, and to `0px` on any device without a cutout, so the non-notch rendering is unchanged. The `max()` form is what keeps the existing designed gutters (24px on `Landing`, 16px on `AuthLayout`) instead of replacing them with a smaller inset on devices whose inset is under the gutter. |
| Q-7 | F-39: fix all 17 mechanically, or introduce shared breakpoint custom media? | Fix all 17 mechanically: `max-width: 480px` becomes `479px`, `max-width: 768px` becomes `767px`. No new mechanism. Record the consolidation as FU-4. | Three reasons. (a) The repository already contains 7 correct instances of exactly this form (`SModal.vue:260`, `SDrawer.vue:162,169`, `AppShell.vue:202,216`, `KeyDetailView.vue:345`, `NotificationCard.vue:181`); the mechanical fix makes 24 blocks consistent, and introducing an eighth spelling for the other 17 would not. (b) The obviously cleaner form, `@media (width < 480px)`, is unavailable: verified against caniuse `css-media-range-syntax`, media-query range syntax requires Safari 16.4 and Safari on iOS 16.4, and the project floor is iOS Safari 16.2 (`docs/UI/11-responsive-a11y.md:346`). It would silently drop the rule on two shipped iOS versions inside the supported range. (c) `@custom-media` is a Media Queries 5 draft with no native support and would need a PostCSS stage; `frontend/` has no PostCSS config at all and builds through `@tailwindcss/vite`. |
| Q-8 | F-39: `useBreakpoint.ts:5` calls itself the single source of truth for the thresholds, and CSS cannot read it. Fix that here? | No. FU-4. | The thresholds are in fact declared twice already, in TS at `useBreakpoint.ts:5` and in CSS at `main.css:57-60`, and neither declaration is usable inside a `max-width` media query, which is why 17 hand-written literals exist. The durable fix is to stop hand-writing max-width queries and use Tailwind's `max-sm:`/`max-md:` variants, which are generated from `--breakpoint-*`; that is a rewrite of 14 files' scoped styles into template classes, and it must first be checked that the emitted variant does not use range syntax (see Q-7b). Both are out of proportion to a one-pixel boundary fix. |
| Q-9 | F-42, width half: adopt the specified `min(280px, 85vw)`? | Yes, but not alone. Change `SDrawer.vue:146-147` to `width: min(280px, 85vw)` **and** add `max-width: 100%` to `.sidebar` (`AppSidebar.vue:256-264`) in the same commit. | Re-deriving the fit threshold (§5) shows the specified width is *less* able to contain the sidebar than the current one: the fit needs a 308px panel and the spec caps the panel at 280px, so under the spec width alone the sidebar overflows at **every** viewport width, by 28px at 330px and up. Shipping the width change on its own would convert an overflow that starts below 362px into an unconditional one. `max-width: 100%` on `.sidebar` is a one-line change that is inert on desktop (the aside track is already exactly `--sidebar-width`) and makes the drawer case fluid. |
| Q-10 | F-42, overflow half: why not add a `padded` prop to `SDrawer` and render the sidebar flush instead? | Rejected. Use `max-width: 100%` on the sidebar. | A new component prop is public API added to fix one consumer's overflow, and it would have to be threaded through `AppShell.vue:113-121`. `max-width: 100%` fixes the overflow for any drawer content at any width, including future consumers, and touches one declaration. The accepted consequence, stated so it is not discovered: inside the drawer the nav renders at `min(280px, 85vw) - 48px`, i.e. 232px at a 375px viewport and 224px at 320px, rather than the desktop 260px. That is the correct reading of a `min(280px, 85vw)` drawer with 24px gutters, not a regression. |
| Q-11 | F-42, z-index half: lower the drawer to `--z-sidebar` (100) as specified? | No code change. Correct `docs/UI/11-responsive-a11y.md:59` instead. | The drawer is genuinely modal: it has a backdrop (`SDrawer.vue:120-124`), `role="dialog" aria-modal="true"` (`:75-77`) and a focus trap. At z 100 it would paint *below* the top bar (`--z-topbar` 200, `AppTopBar.vue:79`), which is precisely what its backdrop exists to cover, and below the network banner at 350. `SDrawer` is also shared with the workflow config panel, so a change there is not scoped to the sidebar. The audit reached the same conclusion ("the z-index deviation is arguably the better behaviour"); this dossier converts that from an aside into an edit. `:59` is amended to state that the mobile sidebar drawer renders through `SDrawer` at `--z-modal` because it is modal, and that `--z-sidebar` applies to the docked desktop aside, which is where `AppSidebar.vue:262` already uses it. |
| Q-12 | F-18: reserve bottom padding on the scroll container, or make the bar in-flow/sticky? | Sticky and in-flow: `position: sticky; bottom: 0` as the last child of the scrolled content, replacing `fixed bottom-0 left-0 right-0`. | A sticky box participates in normal flow, so it reserves exactly its own height with no constant to maintain, and it still pins to the bottom of the scrollport while there is content below it. Reserved padding would have to encode the bar's rendered height as a literal, and that height is not constant: create mode renders one button and edit mode renders two (`AgentDetailView.vue:1278-1285`), and the sibling dossier is changing the padding the constant would have to net against. Sticky also satisfies `docs/UI/11-responsive-a11y.md:110`'s in-flow description without giving up the always-reachable primary action, which making it plainly in-flow would. Accepted consequence: the bar is inset by the shell's content padding instead of bleeding to the viewport edges. It already carries `bg-bg` and `border-t`, which is what keeps it legible over content scrolling beneath. |
| Q-13 | F-18: also stack the two buttons vertically, as `docs/UI/11-responsive-a11y.md:110` says? | No. Keep the horizontal `flex gap-3` row of two `flex-1` buttons. Record the doc reconciliation as FU-5. | At a 320px viewport the two buttons are about 130px each (320 minus the shell's 8px gutters at `xs`, minus the bar's 16px `p-4` each side, minus the 12px `gap-3`, halved), far above the 44px minimum at `:171`, and stacking them would consume about 92px of vertical space on the device with the least of it. The `:110` sentence is a general statement about mobile action buttons across all management pages, not about this bar; correcting it properly means surveying every mobile action row in the product, which is a wider change than this dossier's scope and would need the device pass §8 says this dossier cannot perform. This dossier therefore fixes the occlusion and explicitly does not implement the stacking half. |

## 4. Reproduction

All six need a viewport a desktop browser can only approximate; two need real hardware.
Preconditions: a logged-in user, one project with at least one agent, one chatroom.

**F-45**: open any authenticated route on iOS Safari or Chrome Android with the URL bar
expanded (a fresh page load, before any scroll). The bottom of the shell, notably the
chatroom composer, is below the fold by the toolbar height. Scrolling the *document* (not
the content area) reveals it, and the toolbar collapses, which self-corrects the symptom and
is why it reads as a first-paint glitch rather than a layout bug.

**F-46**: open `/chatrooms/:id` on a real phone, scroll the feed so the toolbar is expanded,
then tap the composer. The keyboard opens and the composer's bottom edge is under it by the
toolbar height. Real device only: no desktop browser opens a virtual keyboard, and
`window.visualViewport` does not shrink without one.

**F-25**: open `/chatrooms/:id` on a notched iPhone in portrait. The send and attach buttons
sit at the bottom of the shell; the bottom 34px is the home-indicator strip, where the
system intercepts the swipe. Taps near the send button either miss or trigger the system
gesture. Real device only.

**F-39**: open `/login` at exactly 480 CSS px (a DevTools preset and a common tablet
split-view). `AuthLayout.vue:69-80` applies the xs treatment (root padding 0,
`align-items: flex-start`, wrapper `max-width: none`) and `SAuthCard.vue:63` strips the
radius and shadow, so the edge-to-edge phone card renders at the width where
`docs/UI/11-responsive-a11y.md:73-80` puts the 420px shadowed card, while `useBreakpoint()`
reports `sm`. The same at exactly 768px for the 768-valued blocks.

**F-42**: at 375px width, open the hamburger drawer; it fits. At 360px the sidebar overflows
by 2px and, because `.sidebar` is `overflow-y: auto` with `overflow-x` computing to `auto`,
a horizontal scrollbar sliver appears. At 320px it overflows by 36px and the right edge of
every nav row is clipped.

**F-18**: `/agents/:id` at 375x812 on the Prompt tab. Scroll `main` fully to the bottom. The
`SCharCount` under the system-prompt editor and the bottom edge of its card are behind the
action bar and no amount of scrolling reveals them. On the Knowledge tab the final select's
help text is unreachable the same way.

## 5. Root Cause Analysis

**F-45 root cause**: `AppShell.vue:141`. The shell was written with `100vh` and the three
other layouts were later written with `100dvh`; nothing reconciled them, and the spec
line that would have caught it said `100vh` too until it was amended. That single
declaration is the earliest link: correcting it removes the symptom and, per the next
paragraph, F-46's symptom as well. No aggravating factor beyond the absence of any test that
can observe a viewport unit.

**F-46 root cause**: the same line. The composable is correct in isolation; it is the
mismatch between its reference box and the CSS's that produces the error. Re-derived, with
`L` the large viewport height, `T` the toolbar height at keyboard-open time, `K` the
keyboard height, and the 56px top bar from `main.css:66`:

- Today. `window.innerHeight = L - T`; `vv.height = L - T - K`; `vv.offsetTop = 0`. So
  `--kb-inset = (L - T) - (L - T - K) = K`. The chatroom's height is
  `100vh - 56 - K = L - 56 - K`. The band actually visible below the top bar is
  `vv.height - 56 = L - T - K - 56`. The chatroom overshoots it by
  `(L - 56 - K) - (L - T - K - 56) = T`. The composer is under the keyboard by **exactly the
  toolbar height**, which is why the audit's worked example came out at 50px for a 50px
  toolbar and why the error vanishes when the toolbar is already collapsed.
- After the fix. `100dvh = L - T`, so the chatroom's height is `(L - T) - 56 - K`, which is
  the visible band exactly. The residual is **0**, for every `T` and every `K`.

This verifies the audit's claim that fixing F-45 also fixes F-46, and shows it is exact
rather than approximate. The one assumption it rests on is that `100dvh` and
`window.innerHeight` denominate the same box; see Q-3 and Q-4 for why that holds here and
what would change it.

**F-25 root cause**: an omission. `env(safe-area-inset-*)` resolves to 0 without
`viewport-fit=cover`, and `viewport-fit=cover` was never added, so neither half was ever
written. The two halves are mutually dependent in one direction only, which is the trap:
insets without the meta are inert, but the meta without insets is actively harmful.

**F-42 root cause, width half**: `SDrawer.vue:146` predates or ignores
`docs/UI/11-responsive-a11y.md:58`, and the responsive block at `:160-174` names `--md` and
`--lg` explicitly, so `--sm` was excluded by construction rather than by oversight.

**F-42 root cause, overflow half**: `AppSidebar.vue:257` hard-sets a width for a component
that is rendered into two different containers. Re-derived, with border-box sizing from
Tailwind's preflight (`main.css:1`):

- The drawer body's content box is `panelWidth - 2 x 24px` (`SDrawer.vue:222`).
- The sidebar's outer width is 260px (`main.css:64`); the 1px border (`AppSidebar.vue:261`)
  is inside it.
- Fitting therefore requires `panelWidth >= 260 + 48 = 308px`.
- Today `panelWidth = min(320, 0.85W)`. Since `320 >= 308`, the binding constraint is
  `0.85W >= 308`, i.e. **`W >= 362.35px`**. At 360px the panel is 306px and the overflow is
  2px; at 320px the panel is 272px and the overflow is 36px. Both match the audit.
- Under the specified `min(280, 0.85W)` the panel never reaches 308px, so the sidebar
  overflows at every width: by 28px wherever `0.85W >= 280` (i.e. `W >= 329.4px`) and by
  more below that. This is the reason Q-9 refuses to ship the width half alone.

**F-18 root cause**: `AgentDetailView.vue:1276`'s `fixed`. An out-of-flow child contributes
nothing to the scroll height of `main.app-shell__content`, so the scroll range ends before
the content the bar covers. Occluded height is the bar's own box: `p-4` gives 16px top and
bottom, `border-t` gives 1px, and `SButton`'s default `md` size is `min-height: 40px`
(`SButton.vue:146`), for 73px, against which the shell's bottom padding at this breakpoint
recovers 16px at `sm` (`AppShell.vue:207-209`) or 8px at `xs` (`:216-218`). Net occlusion is
therefore about 57px at `sm` and about 65px at `xs`, which is the audit's "about 56px" plus
the border and the narrower `xs` gutter.

## 6. Blast Radius and Sibling Suspects

**Blast radius**

- **F-45 and F-46**: mobile first paint on every authenticated route, and mobile chatroom
  typing. No data impact, nothing persisted. Both self-correct once the user scrolls, which
  is why they have survived.
- **F-25**: notched devices only, on the chatroom composer, the new-messages pill, the
  drawer, the full-screen mobile modal and the agent action bar. Applying the fix has a
  blast radius of its own that non-notched devices share: `viewport-fit=cover` changes how
  every page meets the screen edge, so the seven surfaces in Q-5 must land together.
- **F-39**: a one-CSS-pixel band at two breakpoints, in 17 blocks across 14 files, on the
  auth pages, the landing page, five identity views and five tenancy surfaces. The visible
  consequence is confined to viewports at exactly 480 or exactly 768 CSS px, which is not
  rare (DevTools presets, tablet split-view, a 1536px screen at 200% zoom).
- **F-42**: the width deviation is unconditional on every viewport under 1024px; the
  clipping affects 320px-class devices, which are inside the supported range
  (`docs/UI/11-responsive-a11y.md:13` names the iPhone SE as the `xs` device).
- **F-18**: the agent detail view on every viewport below 768px, on every tab, permanently.

**Sibling suspects**

- **Other `100vh` in the tree**: **confirmed, out of scope.**
  `AgentDetailView.vue:964` (`lg:h-[calc(100vh-8rem)]`) and `GraphragGraphView.vue:168`
  (`h-[calc(100vh-3.5rem)]`) both size against the large viewport inside what will be a
  `100dvh` shell. Neither is in this dossier's six findings: they are F-51 and F-10 of the
  same audit, which own the arithmetic errors in those same two constants, and splitting the
  unit change away from the arithmetic change would mean editing both lines twice. Recorded
  as FU-6 so whoever builds those two changes the unit at the same time. Both are also
  `lg:`-gated or desktop-only routes, where `dvh` and `vh` coincide, so the present-day
  impact is nil.
- **Other consumers of `useVisualViewport`**: **cleared.** A repository-wide grep returns the
  composable itself, its barrel export (`shared/composables/index.ts:20`) and exactly one
  call site (`ChatroomView.vue:367`). `--kb-inset` has exactly one producer
  (`ChatroomView.vue:7`) and one consumer (`:1018`).
- **Other bottom-anchored `fixed` elements (the F-18 pattern)**: **cleared.** The 10 hits for
  `fixed bottom-0` / `position: fixed` in `frontend/src` are enumerated in §2; only
  `AgentDetailView.vue:1276` is bottom-anchored in flow-bearing content. `SNetworkBanner.vue:42`
  and `ImpersonationBanner.vue:19` are top-anchored and are F-32 and F-5, owned by
  `2026-08-19-transient-feedback-channels` and the shared-overlay dossier respectively; their
  *top* safe-area inset is deliberately not added here (FU-3) because both are being moved by
  those dossiers and an inset applied to a `top: 0` they are about to change is churn.
- **Other hard-set widths rendered into two containers (the F-42 pattern)**: **cleared for
  the drawer.** `SDrawer`'s only other consumers pass `md` or `lg`, both of which the
  responsive block at `:162-174` already narrows to 85vw and then 100vw, so no fixed-width
  child can be squeezed the same way. Re-run the sweep at build time as a guard against
  drift.
- **Other breakpoint literals outside media queries**: **not swept.** Tailwind's `sm:`/`md:`
  variants are generated from `main.css:57-60` and are correct by construction; hand-written
  pixel comparisons in TypeScript all read `BP` (`useBreakpoint.ts:5`). Two ad-hoc media
  widths remain at `Landing.vue:752` and `:798` and are deliberately untouched (FU-2).
- **Refuted candidates from the audit that must not be reintroduced.** The audit's §4 records
  two refutations that touch this dossier's area and are easy to rediscover.
  `SModal` does decide "mobile" twice, but the two cannot disagree, because the divergence
  needs a space-consuming document scrollbar and there is none where `SModal` lives
  (`AppShell` is `overflow: hidden` at `:142`, and the only document-scrolling layouts render
  no `SModal`). And `AppSidebar` does not create two nested scrollports inside `AppShell`:
  the inner element is exactly the aside's height against a definite grid row, so the outer
  can never overflow, and the desktop aside and the mobile drawer are mutually exclusive
  (`AppShell.vue:104-121`). Only the drawer-width half of that second candidate survives, and
  it is F-42. Neither claim is restated as a defect anywhere in this dossier.

## 7. Fix Design

1. **F-45** - the viewport-height declaration becomes `100dvh`. No fallback (Q-2). This is
   now the fourth layout to match `docs/UI/02-layout-shell.md:102`.

   **The target line moves before this dossier builds.** `2026-08-19-shared-overlay-and-shell-defects`
   is an ancestor of this dossier through `depends_on`, and its §7 F-5 fix relocates the
   viewport height: `App.vue` gains a flex-column root declaring `min-height: 100vh`, and
   `AppShell.vue:141`'s `height: 100vh` becomes `flex: 1; min-height: 0`. That dossier states
   it kept `100vh` on the new root deliberately, so the change is behaviour-neutral with
   respect to F-45 and this dossier still owns the unit. So: apply `100dvh` to whichever
   element carries the viewport height when this task starts. If the overlay dossier has
   landed, that is `App.vue`'s new flex-column root (`min-height: 100dvh`) and `.app-shell`
   must be left on `flex: 1`. If it has not landed, that is `AppShell.vue:141`. Re-read both
   files before editing; do not assume either state.
2. **F-46** - no arithmetic change to `useVisualViewport.ts` (Q-3). Extend the comment at
   `:25-26` to record that the formula's correctness depends on the consuming element
   resolving against the layout viewport, and name `AppShell.vue`'s `100dvh` as the reason
   that holds. This is exactly the kind of cross-file coupling that has no other place to
   live.
3. **F-25** - `frontend/index.html:5` gains `viewport-fit=cover`, and insets are applied in
   the same commit to the seven surfaces of Q-5, every one written in the
   `max(<existing>, env(safe-area-inset-*, 0px))` form of Q-6:
   - `.app-shell` (`AppShell.vue:137-146`): left, right and bottom padding. Because
     preflight makes it `border-box`, the padding comes out of the `100dvh` rather than
     overflowing it, so the grid shrinks and `overflow: hidden` still holds. The top inset is
     **not** applied here: instead the first grid track at `:140` becomes
     `calc(var(--topbar-height) + env(safe-area-inset-top, 0px))`, so the top bar grows into
     the status-bar strip and paints its own background there rather than being pushed below
     a bare band.
   - `AppTopBar.vue:71-79`: `height` matches the widened track and gains
     `padding-top: env(safe-area-inset-top, 0px)`, keeping the 56px content box. Its
     horizontal padding is unchanged, because it sits inside the shell's padding box.
   - `AuthLayout.vue:29` and the xs block's wrapper padding at `:78`.
   - `Landing.vue:348` (the 24px gutters).
   - `SDrawer.vue:126-135` and `SModal.vue:258-276`: both Teleport to `body` and are
     `position: fixed`, so they sit outside the shell's padding box and need their own.
4. **F-39** - mechanical edit of the 17 blocks tabulated in §2: `480px` to `479px`, `768px`
   to `767px`. The 7 correct blocks and the 2 ad-hoc widths are not touched.
5. **F-42** - `SDrawer.vue:146-147` becomes `width: min(280px, 85vw)` with the now-redundant
   separate `max-width` removed, and `.sidebar` (`AppSidebar.vue:256-264`) gains
   `max-width: 100%`. Both in one commit (Q-9). Separately,
   `docs/UI/11-responsive-a11y.md:59` is corrected per Q-11; no z-index code changes.
6. **F-18** - `AgentDetailView.vue:1274-1277`: `fixed bottom-0 left-0 right-0` becomes
   `sticky bottom-0`, and the element moves inside the scrolled content so it is the last
   flow child rather than a sibling after `</form>` at `:1271`. `z-10`, `bg-bg`, `border-t`
   and the `flex gap-3` row are all kept (Q-12, Q-13).

No data repair: nothing was persisted incorrectly, and none of these six touches a
persistence path.

## 8. Regression Test Plan

**This is the weakest section of this dossier and saying so is part of it.** jsdom performs
no layout, has no `visualViewport`, does not implement dynamic viewport units, and resolves
`env(safe-area-inset-*)` to nothing. Four of the six findings are therefore untestable at
the unit tier by construction, and two of those are untestable in Playwright as well.
`docs/tasks/2026-08-09-chatroom-rail-scroll-and-resize` is the precedent: it shipped with
AC-1, AC-3, AC-5 and AC-12 deliberately unticked in an `implemented` dossier, with a
preamble at its §11 stating that jsdom cannot assert a layout outcome and that those four
should be read as unconfirmed rather than passed (its D-5 records that the manual check was
waived). The same discipline applies here, and the same honesty: an AC that cannot be
closed is left unticked, not redefined into something weaker that can be.

What is genuinely testable, written first and failing against current code:

- **T-1 (F-39, F-25, F-45) - source-scan guards, `frontend/src/**/__tests__/`.** These need
  no DOM at all: they read the files and assert on their text. Three assertions.
  (a) No width-conditional `@media` under `frontend/src` uses `max-width: 480px` or
  `max-width: 768px`. Fails today on 17 blocks. (b) `frontend/index.html` contains
  `viewport-fit=cover` **and** each of the seven Q-5 surfaces contains
  `env(safe-area-inset-`. Asserting both in one test is the point: it is what makes it
  impossible to ship the meta without the insets, which is the specific hazard of Q-5.
  Fails today on both halves. (c) `AppShell.vue`'s `.app-shell` rule declares `100dvh` and no
  `100vh`. Fails today. A source scan is a blunt instrument, but it is the only tier that
  can observe a CSS declaration in this repository, and the alternative is no guard at all.
- **T-2 (F-46) - `frontend/src/shared/composables/__tests__/useVisualViewport.test.ts`.**
  jsdom has no `visualViewport`, but the object can be stubbed on `window`, which makes the
  composable's arithmetic fully testable even though its consequence is not. Cases: no
  `visualViewport` gives 0; `innerHeight === vv.height` gives 0; a shrunk `vv.height` gives
  the difference; a non-zero `vv.offsetTop` is subtracted; a negative result clamps to 0
  (`useVisualViewport.ts:27`); `detach` resets to 0. This does not fail before the fix,
  because §7 changes no arithmetic. It is a characterization test that pins the formula
  Q-3 decided to keep, so that a future edit cannot silently reintroduce the mismatch. It is
  listed here rather than omitted precisely because the fix is in another file.
- **T-3 (F-42, width) - `frontend/src/shared/ui/__tests__/SDrawer.test.ts`.** Assert the
  `--sm` panel class is applied for `size="sm"`. The width value itself is covered by T-1's
  pattern class of assertion or by T-5; jsdom will not compute it.
- **T-4 (F-18) - `frontend/src/slices/agents/__tests__/AgentDetailView.test.ts`.** Assert
  that on a mobile breakpoint the action bar renders inside the scrolled content subtree
  rather than as a following sibling, and that its class list contains `sticky` and not
  `fixed`. This is a structural assertion, not a layout one, and it fails today.

**What only Playwright at a set viewport can close.** The suite currently runs a single
project, `Desktop Chrome` (`frontend/playwright.config.ts:23-28`), so
`docs/UI/11-responsive-a11y.md:375-378`'s claim that "E2E golden-path specs run at 3
viewports" is aspirational and unimplemented. The fix includes adding them, plus one more:

| Project | Viewport | Closes |
|---|---|---|
| `desktop` | 1440x900 | The existing suite, unchanged behaviour |
| `tablet` | 768x1024 | F-39 at the `md` boundary: at exactly 768 the tablet layout applies, not the mobile one |
| `mobile` | 375x812 | F-18 (bar does not intersect the last control, and is reachable at every scroll position); F-42 (drawer fits) |
| `mobile-xs` | 320x568 | F-42's overflow: `sidebar.scrollWidth === sidebar.clientWidth` inside the open drawer, and every nav row's right edge inside the panel. 320px is below the 362px threshold derived in §5, so this project is what makes the finding observable at all |

One further spec runs at exactly 480x800 without a dedicated project (`page.setViewportSize`)
to close F-39's `sm` boundary: at exactly 480 CSS px `/login` must render the 420px shadowed
card, not the edge-to-edge xs card.

**What no test in this repository can close.** Three ACs, all requiring real hardware:

- **F-45's visible symptom.** Headless Chromium has no collapsing toolbar, so `100vh` and
  `100dvh` are identical there and a Playwright assertion would pass before and after the
  fix. Requires iOS Safari 16.2+ or Chrome Android 110+ with the toolbar expanded.
- **F-46 entirely, beyond T-2's arithmetic.** No desktop browser opens a virtual keyboard,
  and `visualViewport` does not shrink without one. Requires a real phone.
- **F-25's visible symptom.** Playwright's device descriptors do not emulate display cutouts;
  `env(safe-area-inset-*)` resolves to 0 in headless Chromium regardless of the descriptor,
  so T-1(b) can prove the declarations exist but nothing automated can prove they produce the
  right insets. Requires a notched device, portrait and landscape.

These three are AC-3, AC-4b and AC-6 below. They are written as device checks with named
devices and named observations, so that a later reader can tell the difference between an AC
that was verified and an AC that was reasoned about.

## 9. Risks and Rollback

- **`dvh` reflows the shell during toolbar collapse.** With `100vh` the shell's height is
  constant for the session; with `100dvh` it changes as the toolbar retracts, which relayouts
  the grid mid-scroll on mobile. `.app-shell` is `overflow: hidden` (`AppShell.vue:142`) and
  `main` is the scroller, so `main`'s height changes underneath an active scroll. The three
  other layouts already accept this and the spec now mandates it, but it is a behaviour
  change on every authenticated route and it is the thing to watch for on the device pass.
- **`viewport-fit=cover` is the highest-risk change here** and the only one that alters
  layout on devices that have no defect today. It cannot be scoped to one surface, so a
  mistake in any of the seven Q-5 rules shows up as content under a cutout rather than as a
  test failure. Its rollback is independent of the other five fixes and is a one-line revert
  of `index.html:5`, which makes every `env()` in the change resolve to its `0px` fallback
  and restores today's rendering exactly. That property is the reason Q-6 mandates the
  fallback form.
- **The 17 breakpoint edits are one CSS pixel each and touch 14 files**, five of them tenancy
  and identity views with their own scoped styles. The risk is not the change but the
  review: a mechanical diff across 14 files invites a rubber stamp. T-1(a) is the guard that
  makes the intent checkable without reading all 17 hunks.
- **The drawer becomes narrower for every consumer of `size="sm"`.** Today that is only
  `AppShell.vue:113-121`, verified by grep, but a consumer added between now and the build
  would inherit a 40px reduction it was not designed for.
- **The sticky action bar changes the agent form's scroll height**, which the sibling dossier
  is also changing. This is the concrete reason for Q-1's ordering.
- Rollback for each of the six is an independent revert; they are separable commits and
  share no file except `AppShell.vue`, where F-45's one line and F-25's padding rules are
  adjacent but not entangled.

## 10. Acceptance Criteria

- [ ] AC-1: T-1 fails before the fix and passes after, on all three of its assertions.
- [ ] AC-2 (F-45): the element carrying the app shell's viewport height declares a `dvh` unit
      with no `100vh` fallback, matching `docs/UI/02-layout-shell.md:102`, and no `100vh`
      remains anywhere in `frontend/src/app/`. Which element that is depends on whether
      `2026-08-19-shared-overlay-and-shell-defects` has landed; see §7 item 1.
- [ ] AC-3 (F-45): **device check.** On iOS Safari 16.2+ and on Chrome Android 110+, a fresh
      load of `/chatrooms/:id` with the toolbar expanded shows the composer fully within the
      visible area, without scrolling. Cannot be closed by any test in this repository (§8).
- [ ] AC-4a (F-46): T-2 passes, pinning the inset formula including the `offsetTop` term and
      the clamp at `useVisualViewport.ts:27`.
- [ ] AC-4b (F-46): **device check.** On a real phone with the toolbar expanded, focusing the
      chatroom composer leaves its bottom edge flush above the keyboard, with no part of it
      obscured. Cannot be closed by any test in this repository (§8).
- [ ] AC-5 (F-25): `frontend/index.html:5` carries `viewport-fit=cover` **and** all seven
      surfaces named in Q-5 reference `env(safe-area-inset-*)`, asserted together by T-1(b)
      so neither can land without the other.
- [ ] AC-6 (F-25): **device check.** On a notched iPhone, portrait and landscape: the
      composer's send and attach buttons clear the home-indicator strip; the top bar's
      background paints into the status-bar strip rather than leaving a bare band; the open
      drawer and a full-screen modal clear both the cutout and the indicator; the landing
      page's content clears the landscape cutout. Cannot be closed by any test in this
      repository (§8).
- [ ] AC-7 (F-39): no width-conditional `@media` under `frontend/src` uses
      `max-width: 480px` or `max-width: 768px`; the 7 already-correct blocks listed in §2 are
      byte-identical to before; `Landing.vue:752` and `:798` are unchanged.
- [ ] AC-8 (F-39): **Playwright at 480x800.** `/login` renders the `sm` treatment (420px
      wrapper, card shadow and radius present) at exactly 480 CSS px, and `useBreakpoint()`
      and the CSS agree.
- [ ] AC-9 (F-42): `.s-drawer__panel--sm` resolves to `min(280px, 85vw)`, matching
      `docs/UI/11-responsive-a11y.md:58`.
- [ ] AC-10 (F-42): **Playwright at 320x568.** With the sidebar drawer open,
      `scrollWidth === clientWidth` on the sidebar element and every nav row's right edge
      lies inside the drawer panel's box. Fails today by 36px per §5.
- [ ] AC-11 (F-42): `docs/UI/11-responsive-a11y.md:59` states the drawer's real z-index and
      the reason; `SDrawer.vue:116` is unchanged and every other drawer consumer is
      unaffected.
- [ ] AC-12 (F-18): **Playwright at 375x812.** On `/agents/:id`, scrolled fully to the bottom
      of the Prompt tab, the last form control's bounding box does not intersect the action
      bar's, and the action bar is visible at every scroll position from top to bottom.
- [ ] AC-13 (F-18): the bar reserves its own height rather than a hardcoded constant,
      verified by T-4's structural assertion plus AC-12 holding in both create mode (one
      button) and edit mode (two buttons).
- [ ] AC-14: `frontend/playwright.config.ts` declares the four projects tabulated in §8, and
      the existing 18 specs still pass under the `desktop` project unchanged.
- [ ] AC-15: gates green: `pnpm lint` (all 12), `pnpm typecheck`, `pnpm test`, `pnpm build`.

## 11. SRS Delta

None. `REQUIREMENTS.md` specifies nothing about viewport units, safe areas, breakpoint
boundaries or drawer widths; every intent source cited here is a `docs/UI/` document. Two
`docs/UI/` corrections are part of the change and are described in §7 rather than here,
because they are not SRS entries: `docs/UI/11-responsive-a11y.md:59` (drawer z-index, Q-11)
and, deferred to FU-5, `:110` (mobile action-button orientation, Q-13).

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1**: `interactive-widget=resizes-content` in the viewport meta would let the browser
  do the keyboard arithmetic instead of `useVisualViewport`. Refused here (Q-4) because it is
  Chrome 108+ / Firefox 132+ only and unimplemented in WebKit, so it would help no iOS user.
  Revisit when it ships in Safari, at which point the composable could be retired rather than
  maintained.
- **FU-2**: `app/views/Landing.vue:752` (900px) and `:798` (560px) are ad-hoc media widths
  that correspond to no breakpoint. Left alone by Q-7's mechanical rule because changing them
  is a design decision, not a boundary correction. Worth deciding whether the landing page
  should share the app's breakpoints at all.
- **FU-3**: safe-area insets are deliberately not applied to `SNetworkBanner.vue:42` or
  `ImpersonationBanner.vue:19`, both `position: fixed; top: 0`. Both are being repositioned
  by other dossiers from this audit (F-32 and F-5), and adding a top inset to a `top` value
  that is about to change is churn. Their top insets should be added by those dossiers, or
  here if this one lands last.
- **FU-4**: the breakpoint thresholds are declared twice, in TS at `useBreakpoint.ts:5` and
  in CSS at `main.css:57-60`, and neither is readable from a `max-width` media query, which
  is why 17 hand-written literals existed. The durable fix is to replace hand-written
  max-width queries with Tailwind's `max-sm:`/`max-md:` variants, which are generated from
  `--breakpoint-*`. Before adopting it, check the emitted CSS: if the variant compiles to
  range syntax it is blocked by the same iOS Safari 16.4 floor that Q-7 found. Route to
  `check-quality`.
- **FU-5**: `docs/UI/11-responsive-a11y.md:110` ("Action buttons: stacked vertically on
  mobile instead of horizontal row") is not implemented anywhere and Q-13 deliberately does
  not implement it. Either the line is wrong or the product is; deciding needs a survey of
  every mobile action row, not a single view.
- **FU-6**: `AgentDetailView.vue:964` and `GraphragGraphView.vue:168` remain `100vh` inside
  what will be a `100dvh` shell. They are F-51 and F-10 of the same audit, both of which
  already own the arithmetic in those constants; whoever builds them should change the unit
  in the same edit rather than leave two spellings of viewport height in the tree.
- **FU-7**: `docs/UI/11-responsive-a11y.md:375-378` states that E2E specs run at three
  viewports. Until AC-14 lands that is false, and it has been false since the line was
  written. If AC-14 is descoped, the doc must be corrected instead of left aspirational.
