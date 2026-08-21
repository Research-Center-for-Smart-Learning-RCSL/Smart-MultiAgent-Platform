---
type: bugfix
status: implemented
created: 2026-08-19
approved: 2026-08-21
implemented: 2026-08-21
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

### 1.1 Freshness re-verification (2026-08-21)

Both of this dossier's sweeps were re-run and **all six findings still reproduce unchanged**.

- **F-45's sweep is exact.** Viewport-height units in `frontend/src` still return exactly the
  seven documented hits, at the same lines: `AppShell.vue:141` (still `height: 100vh`),
  `AuthLayout.vue:27,77`, `PublicLayout.vue:14`, `Landing.vue:345`,
  `AgentDetailView.vue:964`, `GraphragGraphView.vue:168`. Nothing has drifted.
- **F-39's count holds: 17 inclusive `@media` blocks across 14 files.** One line moved -
  `ProfileView.vue` is **`:319`**, not `:328`. The table in §2 is corrected. Every other entry
  is exact, and the 7 already-correct blocks are all still correct.
- **F-18's anchors are unchanged**: `AgentDetailView.vue:1274-1277`, `:1271`, `:665`.

Line drift only, from `2026-08-19-chatroom-scroll-and-composer` landing in the two chatroom
files this dossier cites for F-25 and F-46. Nothing about the findings changes; the numbers do:

| Cited as | Now | What it is |
|---|---|---|
| `ChatroomView.vue:7` | `:11` | the `--kb-inset` producer |
| `ChatroomView.vue:367` | `:387` | the sole `useVisualViewport` call site |
| `ChatroomView.vue:1015-1019` / `:1018` | `:1275-1279` / `:1278` | `.chatroom--mobile`'s `calc(100% - var(--kb-inset))` |
| `ChatroomView.vue:980-983` | `:1183` | `.chatroom__composer` grid row |
| `ChatroomView.vue:1001-1006` | `:1204` | `.chatroom__pill`, `bottom: 16px` |
| `ChatroomComposer.vue:252-257` | `:273` | `.composer` root rule |

**One new interaction worth knowing, because this is the breakpoints dossier.**
`2026-08-19-chatroom-scroll-and-composer` added a **fourth breakpoint band** that
`useBreakpoint` does not expose: `ChatroomView.vue` derives `isCompactDesktop`
(`width >= BP.lg && width < BP.xl`) locally from the exported `width`, for the 1024-1279
overlay layout. Its FU-2 says explicitly that a *second* consumer of that band should promote
it into `useBreakpoint` rather than copy the computed. That is not this dossier's job - F-39
is about the `sm`/`md` boundary values, not about adding a band - but FU-4 here is the entry
that owns "the thresholds are declared twice and neither is readable from a media query", and
a third declaration now exists in a view. Worth folding into whatever answers FU-4.

### 1.2 Second re-verification (2026-08-21, at build start)

§1.1 was written **before** `2026-08-19-content-area-spacing-and-scroll-contract` landed. That
dossier has since shipped, and it moved a great deal of what this one cites. This section
supersedes §1.1's line numbers wherever the two disagree; §1.1 is kept as the record of what
the tree looked like when the dossier was written.

**All six findings still reproduce.** Re-verified individually:

- **F-45**: `App.vue:79` is `min-height: 100vh`. No `dvh` anywhere on the shell path.
- **F-46**: `useVisualViewport.ts:26` is unchanged, character for character.
- **F-25**: `frontend/index.html:5` is unchanged, and a repository-wide grep for
  `safe-area` / `env(` across `frontend/` still returns **no CSS hit at all** - every match is
  the `env()` test-fixture helper in `frontend/e2e/` (`fixtures/seed.ts:44`).
- **F-39**: the enumeration is **exactly** as §2 states - 26 width-conditional `@media` blocks,
  17 inclusive, 7 correct, 2 ad-hoc. Four line numbers moved (table below); no block changed
  its value and none was added or removed.
- **F-42**: `SDrawer.vue:145-148` is still `width: 320px; max-width: 85vw`, the responsive block
  at `:160-174` still names only `--md`/`--lg`, and `AppSidebar.vue:256-264` still hard-sets
  `width: var(--sidebar-width)` at `:257` with `z-index: var(--z-sidebar)` at `:262`.
- **F-18**: `AgentDetailView.vue:1305` is still
  `fixed bottom-0 left-0 right-0 p-4 bg-bg border-t border-border flex gap-3 z-10`.

**Every `docs/UI/` citation in this dossier still resolves**, including the ones §1.1 did not
re-check: `02-layout-shell.md:102` and `:111-116`, and `11-responsive-a11y.md:11-17`, `:58-59`,
`:73-80`, `:110`, `:122`, `:136`, `:171`, `:340-347`, `:375-378`, `:385`, `:399`.

#### Six substantive changes, not line drift

1. **FU-6 is half-closed. `GraphragGraphView.vue` no longer contains `100vh` at all** - the
   content-area dossier's F-10 rewrote that root as `h-full` (`:168`), and
   `GraphragGraphView.test.ts:65-78` now asserts the class list does *not* contain `100vh`.
   The only `100vh` left in `frontend/src` besides `App.vue:79` is `AgentDetailView.vue:988`,
   which is the paired edit of item 2 rather than a sibling. FU-6 is rewritten accordingly.
2. **The paired edit's target moved and its value changed.** §7 item 1 names
   `AgentDetailView.vue:964` as `lg:h-[calc(100vh-3.5rem-3rem)]`. It is now **`:988`**, and the
   pin is `AgentDetailView.test.ts:335`. Note that the test makes **two** assertions and only
   one moves: `:335` asserts the current constant is present and must be updated to the `dvh`
   spelling; `:336` asserts the pre-F-51 constant `lg:h-[calc(100vh-8rem)]` is **absent** and
   must be left exactly as it is - it names a value that no longer exists and rewriting it to
   `dvh` would make it assert the absence of something that was never there.
3. **F-18's containing block is not what §2 says.** The view root is no longer
   `<main class="p-6">`; the content-area dossier made it a bare `<div>` (`:665`) and moved
   content padding onto the shell. Two consequences: §5's occlusion arithmetic still holds
   (the shell recovers 16px at `sm` / 8px at `xs`, now at `AppShell.vue:230-232` and
   `:239-247`), but §7 item 6's instruction to *move* the bar "inside the scrolled content so
   it is the last flow child rather than a sibling after `</form>`" is now a **no-op**: the bar
   at `:1303-1324` already is the last flow child of the root `<div>`, which is already inside
   `main.app-shell__content`. Nothing but `position: fixed` takes it out of flow, so the fix
   reduces to the class change alone. T-4 is reworded to match.
4. **A sticky bottom bar will not sit flush against the scrollport's bottom edge.**
   `shared-overlay-and-shell-defects`'s D-11 measured that Chromium constrains a sticky child
   against the scrollport's **content** box, so its sticky header pinned one content gutter
   below the top bar rather than flush at 56px. The mirror image applies here: with
   `.app-shell__content` carrying `padding: 16px` at `sm` and `8px` at `xs`
   (`AppShell.vue:230-232`, `:239-247`), a `sticky bottom-0` bar pins that many pixels **above**
   the bottom edge. This is correct behaviour and reads as a floating bar, but AC-12 must not
   assert flushness, and Q-12's "Accepted consequence" now covers it.
5. **FU-3 loses half its subject.** `ImpersonationBanner.vue` is **no longer `position: fixed`**
   - the overlay dossier put it in flow inside `App.vue`'s `.app-root` column (`App.vue:36-41`
   records why), so it needs no top inset and never will. `SNetworkBanner.vue:45` is the only
   remaining top-anchored fixed banner. §6's inventory still totals 10 hits, but the membership
   changed: `ImpersonationBanner` left and `SDropdown.vue:284` (new, added by the overlay
   dossier) joined. `AgentDetailView.vue:1305` remains the only bottom-anchored one, which is
   the claim F-18 actually rests on.
6. **T-1 no longer has to invent a mechanism.** `frontend/src/app/__tests__/viewRoots.test.ts`
   is a working source-scan sweep shipped by the content-area dossier: it walks `src` from disk
   with `node:fs`, so it crosses no slice boundary, and it opens with a
   "finds the files it is meant to sweep" assertion so a glob that stops matching fails loudly
   instead of passing vacuously. T-1 follows that file's shape and carries the same guard.

#### Line drift (numbers only, no claim changes)

| Cited as | Now | What it is |
|---|---|---|
| `AppShell.vue:137-146` | `:148-169` | the `.app-shell` rule |
| `AppShell.vue:140` | `:151` | `grid-template-rows` |
| `AppShell.vue:141` | `App.vue:79` | the viewport-height declaration (see §7 item 1) |
| `AppShell.vue:142` | `:165` | `overflow: hidden` |
| `AppShell.vue:123-132` | `:206-212` | `.app-shell__content` |
| `AppShell.vue:202` / `:216` | `:225` / `:239` | the two already-correct `@media` blocks |
| `AppShell.vue:207-209` / `:216-218` | `:230-232` / `:239-247` | the `sm` / `xs` content padding |
| `AppShell.vue:113-121` | `:124-132` | the `<SDrawer size="sm">` sidebar |
| `AppShell.vue:104-121` | `:115-132` | aside / drawer mutual exclusion |
| `AppTopBar.vue:71-79` | `:70-80` | the `.topbar` rule (`z-index` is at `:79`) |
| `SModal.vue:258-276` / `:260` | `:271-292` / `:271` | the mobile full-screen block |
| `AgentDetailView.vue:964` | `:988` | the `lg:` prompt-assistant height |
| `AgentDetailView.vue:665` | `:665` | still `:665`, but now `<div>`, not `<main class="p-6">` |
| `AgentDetailView.vue:1274-1277` / `:1276` | `:1303-1324` / `:1305` | the mobile action bar |
| `AgentDetailView.vue:1271` | `:1300` | the form's closing tag |
| `AgentDetailView.vue:1278-1285` | `:1307-1323` | the bar's one-or-two buttons |
| `ImpersonationBanner.vue:19` | (gone) | no longer `position: fixed`; see change 5 |
| `SNetworkBanner.vue:42` | `:45` | `position: fixed` |
| §2's F-39 table: `SessionsView.vue:279` | `:268` | 768 block |
| §2's F-39 table: `InviteAcceptView.vue:155` | `:158` | 768 block |
| §2's F-39 table: `OrgDetailView.vue:440, 446` | `:454, :460` | 768 / 480 blocks |

Unchanged and re-verified exactly as cited: `useVisualViewport.ts:25-27`, `useBreakpoint.ts:5`,
`main.css:57-60`, `:64`, `:66`, `:312`, `index.html:5`, `AuthLayout.vue:23-30`, `:27`, `:29`,
`:69-80`, `:77`, `:78`, `Landing.vue:342-349`, `:345`, `:348`, `:752`, `:791`, `:798`,
`AppSidebar.vue:256-264`, `:257`, `:261`, `:262`, `SDrawer.vue:75-77`, `:96-98`, `:113-118`,
`:116`, `:120-124`, `:126-135`, `:145-148`, `:160-174`, `:162`, `:169`, `:221-225`, `:222`,
every `ChatroomView.vue` and `ChatroomComposer.vue` number in §1.1's table, and
`SButton.vue:146`.

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
  value is bound as `--kb-inset` on the chatroom root (`ChatroomView.vue:11`, enabled only on
  mobile via `:387`) and consumed by
  `.chatroom--mobile { height: calc(100% - var(--kb-inset, 0px)) }`
  (`ChatroomView.vue:1275-1279`; line numbers re-verified 2026-08-21, see §1.1). That `100%`
  chains through `main.app-shell__content`
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
  inset (line numbers re-verified 2026-08-21, see §1.1): the chat composer
  (`ChatroomComposer.vue:273`, rendered as grid row 4 flush to
  the shell bottom per `ChatroomView.vue:1183`), the new-messages pill
  (`ChatroomView.vue:1204`, `bottom: 16px`), the agent action bar
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
  | `slices/identity/views/ProfileView.vue` | 319 | 768 |
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
  `SNetworkBanner.vue:45` which is top-anchored and owned by another dossier, `SDropdown.vue:284`
  which is a positioned menu, and four `SModal`/`SDrawer` overlay roots). Nothing reserves its
  height: a `fixed` child is out of flow, so it contributes nothing to
  `main.app-shell__content`'s scroll height and the scroll range ends before the content it
  covers. **Two corrections, 2026-08-21** (§1.2 changes 3 and 5): this paragraph originally
  read the view root as `<main class="p-6">` (`:665`) and counted `ImpersonationBanner.vue:19`
  among the ten. The root is now a bare `<div>` at `:665` with content padding on the shell,
  and the banner is no longer `position: fixed` at all; `SDropdown.vue:284` takes its place in
  the count. Neither correction touches the finding - `AgentDetailView.vue:1305` is still the
  only bottom-anchored `fixed` element in flow-bearing content, which is the whole of F-18.
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
| Q-12 | F-18: reserve bottom padding on the scroll container, or make the bar in-flow/sticky? | Sticky and in-flow: `position: sticky; bottom: 0` as the last child of the scrolled content, replacing `fixed bottom-0 left-0 right-0`. | A sticky box participates in normal flow, so it reserves exactly its own height with no constant to maintain, and it still pins to the bottom of the scrollport while there is content below it. Reserved padding would have to encode the bar's rendered height as a literal, and that height is not constant: create mode renders one button and edit mode renders two (`AgentDetailView.vue:1278-1285`), and the sibling dossier is changing the padding the constant would have to net against. Sticky also satisfies `docs/UI/11-responsive-a11y.md:110`'s in-flow description without giving up the always-reachable primary action, which making it plainly in-flow would. Accepted consequence: the bar is inset by the shell's content padding instead of bleeding to the viewport edges - **on all four sides, not just the two horizontal ones**. Chromium constrains a sticky child against the scrollport's *content* box (measured in `shared-overlay-and-shell-defects`'s D-11 for a sticky header), so the bar pins 16px above the bottom edge at `sm` and 8px at `xs` rather than flush against it (§1.2 change 4). It already carries `bg-bg` and `border-t`, which is what keeps it legible over content scrolling beneath. |
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

- **Other `100vh` in the tree**: **re-swept 2026-08-21; both subjects overtaken, FU-6 closed.**
  This entry originally deferred `AgentDetailView.vue:964` (`lg:h-[calc(100vh-8rem)]`) and
  `GraphragGraphView.vue:168` (`h-[calc(100vh-3.5rem)]`) to FU-6, because F-51 and F-10 of the
  same audit owned the arithmetic in those constants and splitting the unit change from the
  arithmetic change would mean editing both lines twice. Both have since been built by
  `2026-08-19-content-area-spacing-and-scroll-contract`: `GraphragGraphView` no longer carries
  a viewport unit (`:168` is `h-full`, pinned by `GraphragGraphView.test.ts:65-78`), and
  `AgentDetailView`'s constant moved to `:988` and became `lg:h-[calc(100vh-3.5rem-3rem)]`,
  which that dossier's Q-14 shipped in `vh` **deliberately** to match the shell it landed
  against. So there is nothing left to defer: the one remaining line is this dossier's paired
  edit (§7 item 1, AC-16), not a sibling. Its `lg:` gate still means the present-day impact is
  nil; the reason to move it is that leaving two spellings of viewport height in the tree is
  exactly what produced F-45.
- **Other consumers of `useVisualViewport`**: **cleared, re-swept 2026-08-21.** A
  repository-wide grep returns the composable itself, its barrel export
  (`shared/composables/index.ts:20`) and exactly one call site (`ChatroomView.vue:387`).
  `--kb-inset` still has exactly one producer (`ChatroomView.vue:11`) and one consumer
  (`:1278`).
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

   **Confirmed 2026-08-21: the overlay dossier has landed, so the first branch applies.** The
   target is `App.vue:79`'s `.app-root { min-height: 100vh }`. Two corrections to the paragraph
   above, both from that dossier's deviation log:
   - `.app-shell` is `flex: 1 1 0px`, **not** `flex: 1`. Its **D-10** records why the basis must
     be a length: `flex: 1` expands to a `0%` basis, and against a `min-height`-only container a
     percentage basis resolves to `content`, so the shell gets sized by `main`'s content and
     hands page scrolling to the document (measured: 3805px shell, 3385px document scroll).
     **Do not "simplify" it back to `flex: 1` while editing this rule.**
     `frontend/e2e/21-overlay-and-shell-contract.spec.ts` fails if you do.
   - **This edit is paired.** `2026-08-19-content-area-spacing-and-scroll-contract` ships
     `AgentDetailView.vue:988` as `lg:h-[calc(100vh-3.5rem-3rem)]` deliberately, to match the
     shell's `vh` (its Q-14 and FU-8). When this dossier moves the shell to `dvh`, that line and
     its T-9 class assertion must move in the same change, or the mismatch reintroduces a
     smaller F-51 on mobile. Per §1.2 change 2: the line is **`:988`**, not `:964`, and only
     `AgentDetailView.test.ts:335` moves - `:336` asserts the *absence* of the superseded
     `lg:h-[calc(100vh-8rem)]` and must be left byte-identical.
   - **`GraphragGraphView.vue` is not part of this pairing any more.** §1.2 change 1: it no
     longer contains a viewport unit at all, so `AgentDetailView.vue:988` is the whole of the
     paired work and FU-6 shrinks to nothing.
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
6. **F-18** - `AgentDetailView.vue:1305`: `fixed bottom-0 left-0 right-0` becomes
   `sticky bottom-0`. `z-10`, `bg-bg`, `border-t` and the `flex gap-3` row are all kept
   (Q-12, Q-13). **This is now the whole of the fix** - §1.2 change 3: the approved text also
   asked to move the element "inside the scrolled content", which was written against a view
   root of `<main class="p-6">`. That root is now a bare `<div>` (`:665`) and the bar at
   `:1303-1324` is already its last flow child, inside `main.app-shell__content`. Nothing but
   `position: fixed` takes it out of flow, so no element moves.

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
  Fails today on both halves. (c) the element carrying the app shell's viewport height
  declares `100dvh` and no `100vh`, and no `100vh` remains anywhere under `frontend/src/app/`.
  Fails today on `App.vue:79`. A source scan is a blunt instrument, but it is the only tier
  that can observe a CSS declaration in this repository, and the alternative is no guard at
  all.

  **Follow `frontend/src/app/__tests__/viewRoots.test.ts`** (§1.2 change 6) rather than
  inventing a mechanism: it is the same tier doing the same thing, shipped by the sibling
  dossier. Two of its properties are load-bearing and T-1 carries both - it walks `src` from
  disk with `node:fs` so it crosses no slice boundary, and it opens with a "finds the files it
  is meant to sweep" count assertion so a glob that silently stops matching fails loudly
  instead of passing vacuously. T-1 lives beside it, under `src/app/__tests__/`, for the same
  reason that file gives: `app/` owns the shell contract being enforced.
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
  that on a mobile breakpoint the action bar's class list contains `sticky bottom-0` and
  neither `fixed` nor `bottom-0 left-0 right-0`, and that it renders as the last flow child of
  the view root. This is a structural assertion, not a layout one, and its first half fails
  today. Per §1.2 change 3 the second half already passes - it is kept as a characterization
  pin, so that a later edit cannot move the bar back out of the scrolled subtree and silently
  restore the occlusion by a different route. That it does not fail today is stated rather
  than hidden.

**What only Playwright at a set viewport can close.** The suite currently runs a single
project named `chromium` at `Desktop Chrome` (`frontend/playwright.config.ts:23-28`), so
`docs/UI/11-responsive-a11y.md:375-378`'s claim that "E2E golden-path specs run at 3
viewports" is aspirational and unimplemented. The fix adds three more projects, plus one new
spec, `frontend/e2e/23-mobile-viewport.spec.ts`, that carries every viewport-conditional
assertion below:

| Project | Viewport | Runs | Closes |
|---|---|---|---|
| `desktop` | 1280x720 (`devices['Desktop Chrome']`, no override - see D-12) | the whole suite | The existing 22 specs, unchanged behaviour |
| `tablet` | 768x1024 | `23-mobile-viewport` only | F-39 at the `md` boundary: at exactly 768 the tablet layout applies, not the mobile one |
| `mobile` | 375x812 | `23-mobile-viewport` only | F-18 (bar does not intersect the last control, and is reachable at every scroll position); F-42 (drawer fits) |
| `mobile-xs` | 320x568 | `23-mobile-viewport` only | F-42's overflow: `sidebar.scrollWidth === sidebar.clientWidth` inside the open drawer, and every nav row's right edge inside the panel. 320px is below the 362px threshold derived in §5, so this project is what makes the finding observable at all |

**The three new projects are `testMatch`-scoped to the new spec, deliberately** (decided with
the user at build start, superseding the approved table's implicit "all four run everything").
`playwright.config.ts:6-9` sets `fullyParallel: false` with `workers: 1`, and `ci.yml:1040`
runs a bare `pnpm run test:e2e` with no `--project`, so an unscoped fourth project multiplies
the serial e2e job by four - 88 spec runs to answer three viewport questions. Scoping keeps
the CI cost at 22 desktop specs plus three runs of one spec. The accepted cost, stated so it
is not discovered later: the existing 22 golden paths are still exercised at one width only,
so responsive breakage in them stays invisible. That is the status quo, not a regression, and
FU-8 records it as the thing to widen if the mobile surface ever justifies the minutes.

The `chromium` project is renamed `desktop` in the same edit. Nothing references it by name:
`ci.yml:1040` passes no `--project`, and `package.json:22` is a bare `playwright test`.

One further check runs at exactly 480x800 inside `23-mobile-viewport` via
`page.setViewportSize` rather than a fifth project, to close F-39's `sm` boundary: at exactly
480 CSS px `/login` must render the 420px shadowed card, not the edge-to-edge xs card.

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

**Thirteen of sixteen are ticked.** The three that are not are the device checks that **no
test in this repository can close**: headless Chromium has no collapsing URL bar, opens no
virtual keyboard and emulates no display cutout, so `dvh`, the `visualViewport` inset and
`env(safe-area-inset-*)` are identically inert there. AC-3, AC-4b and AC-6 need real hardware
and are stated as such in §8.

Everything else was **executed**, not reasoned about: the Playwright criteria were closed by
`/verify` against a live stack and mutation-probed one fix at a time (§12a). An AC that
cannot be closed is left unticked rather than redefined into something weaker that can be.

- [x] AC-1: T-1 fails before the fix and passes after, on all three of its assertions.
      Observed red first: 5 failing assertions against the pre-fix tree (14 files with
      inclusive queries, 2 files with `100vh`, no `dvh`, no meta, 6 un-inset surfaces).
      The two assertions whose final form was never seen red - the narrowed prelude sweep
      (D-3) and the `env()` fallback check - were **mutation-probed**: reverting one boundary
      to 768 and stripping one `0px` fallback each turned exactly the intended test red.
- [x] AC-2 (F-45): the element carrying the app shell's viewport height declares a `dvh` unit
      with no `100vh` fallback, matching `docs/UI/02-layout-shell.md:102`, and no `100vh`
      remains anywhere in `frontend/src/app/`. Which element that is depends on whether
      `2026-08-19-shared-overlay-and-shell-defects` has landed; see §7 item 1.
- [ ] AC-3 (F-45): **device check.** On iOS Safari 16.2+ and on Chrome Android 110+, a fresh
      load of `/chatrooms/:id` with the toolbar expanded shows the composer fully within the
      visible area, without scrolling. Cannot be closed by any test in this repository (§8).
- [x] AC-4a (F-46): T-2 passes, pinning the inset formula including the `offsetTop` term and
      the clamp at `useVisualViewport.ts:27`. Seven cases; passed before the fix too, as §8
      said it would.
- [ ] AC-4b (F-46): **device check.** On a real phone with the toolbar expanded, focusing the
      chatroom composer leaves its bottom edge flush above the keyboard, with no part of it
      obscured. Cannot be closed by any test in this repository (§8).
- [x] AC-5 (F-25): `frontend/index.html:5` carries `viewport-fit=cover` **and** all seven
      surfaces named in Q-5 reference `env(safe-area-inset-*)`, asserted together by T-1(b)
      so neither can land without the other. **Widened after a post-close review** (D-12,
      D-13): Q-5's seven were not the complete set of elements that meet a viewport edge -
      `Landing`'s top, the skip link and the network banner were all exposed by the meta and
      insetted none of themselves. The list is now eight surfaces asserted **per edge**, not
      per file, because the per-file form let a half-protected surface pass.
- [ ] AC-6 (F-25): **device check.** On a notched iPhone, portrait and landscape: the
      composer's send and attach buttons clear the home-indicator strip; the top bar's
      background paints into the status-bar strip rather than leaving a bare band; the open
      drawer and a full-screen modal clear both the cutout and the indicator; the landing
      page's content clears the landscape cutout. Cannot be closed by any test in this
      repository (§8).
- [x] AC-7 (F-39): no width-conditional `@media` under `frontend/src` uses
      `max-width: 480px` or `max-width: 768px`; the 7 already-correct blocks listed in §2 are
      byte-identical to before; `Landing.vue:752` and `:798` are unchanged. Re-enumerated
      after the edit: 26 blocks, 24 exclusive and 2 ad-hoc, and the diff is exactly 17
      one-line changes across 14 files. The five element-level `max-width: 480px` caps in the
      identity views were correctly **not** touched (D-3).
- [x] AC-8 (F-39): **Playwright at 480x800.** `/login` renders the `sm` treatment (420px
      wrapper, card shadow and radius present) at exactly 480 CSS px, and `useBreakpoint()`
      and the CSS agree. Strengthened to walk **both** sides of the boundary (D-11): measured
      `0px`/`none`/`none` at 479 and `8px`/present/`420px` at 480. Mutation-probed via the
      `md` boundary in the same family.
- [x] AC-9 (F-42): `.s-drawer__panel--sm` resolves to `min(280px, 85vw)`, matching
      `docs/UI/11-responsive-a11y.md:58`. The declaration is exactly that, with the redundant
      separate `max-width` removed, and T-3 pins that `size="sm"` still reaches the panel as
      the modifier class the rule targets. The *rendered* width at 375 and 320 is AC-10's
      pending e2e half.
- [x] AC-10 (F-42): **Playwright at 320x568.** With the sidebar drawer open,
      `scrollWidth === clientWidth` on the sidebar element and every nav row's right edge
      lies inside the drawer panel's box. Passes at both `mobile` and `mobile-xs`, and
      **mutation-probed**: removing `.sidebar { max-width: 100% }` turns both red. Also
      observed directly - the 320px panel settles at 272px with all six nav rows inside it
      and no horizontal scrollbar (§12a).
- [x] AC-11 (F-42): `docs/UI/11-responsive-a11y.md:59` states the drawer's real z-index and
      the reason; `SDrawer.vue:116` is unchanged and every other drawer consumer is
      unaffected. The doc entry also now records the narrower in-drawer nav width that Q-10
      accepted, so it is documented rather than discovered. T-3 pins the modal properties
      (`role`, `aria-modal`, backdrop) that are the reason for the z-index.
- [x] AC-12 (F-18): **Playwright at 375x812.** On `/agents/:id`, scrolled fully to the bottom
      of the Prompt tab, the last form control's bounding box does not intersect the action
      bar's, and the action bar is visible at every scroll position from top to bottom. The
      assertion is non-intersection, **not** flushness against the viewport bottom: per §1.2
      change 4 the bar pins one content gutter above it (16px at `sm`, 8px at `xs`) - which
      the captured screenshot shows as a visible gap below the bar. **Mutation-probed**:
      restoring `fixed bottom-0 left-0 right-0` turns both action-bar tests red.
- [x] AC-13 (F-18): the bar reserves its own height rather than a hardcoded constant,
      verified by T-4's structural assertion plus AC-12. **Partial on one point, stated
      rather than glossed**: the e2e half was exercised in **edit mode only** (two buttons) -
      the seeded fixture is an existing agent, and `/agents/new` needs a `projectId` query
      plus a key group to render its form. The create-mode half (one button) rests on T-4's
      unit-tier assertion that the bar's height follows its rendered content, plus the fact
      that a sticky box reserves its own box height with no constant to be wrong. No
      hardcoded height exists anywhere in the change, which is the claim AC-13 actually makes.
- [x] AC-14: `frontend/playwright.config.ts` declares the four projects tabulated in §8, with
      `tablet`/`mobile`/`mobile-xs` `testMatch`-scoped to `23-mobile-viewport.spec.ts`; the
      existing **22** specs still pass under the renamed `desktop` project unchanged; and
      `pnpm run test:e2e` with no `--project` runs 22 + 3 spec instances, not 88.
      `22-layout-contract.spec.ts` re-run under the renamed project: 10 passed, 1 skipped.
      The scoping was confirmed incidentally during verification - a scratch spec added to
      `e2e/` was picked up by `desktop` only, exactly as intended. **One caveat**: only
      `22-layout-contract` was re-run under `desktop`, not all 22 specs; the rename is a
      config-key change with no per-spec behaviour, and CI runs the full suite.
- [x] AC-16 (paired edit, from `content-area-spacing-and-scroll-contract`'s FU-8):
      `AgentDetailView.vue:988` uses the same viewport unit as the shell, and
      `AgentDetailView.test.ts:335` is updated to match while `:336` stays byte-identical
      (§1.2 change 2). No `100vh` remains in `frontend/src` at all - asserted by T-1 over
      every `.vue` and `.css` outside `__tests__/`, and confirmed in the emitted bundle,
      where the only surviving `100vh` utility is the dead one FU-10 explains.
- [x] AC-15: gates green: `pnpm lint` (all 12), `pnpm typecheck`, `pnpm test`
      (212 files, 1374 tests), `pnpm build`. Backend untouched, so its gates are N/A.
      `check-quality` ran over the full task diff and returned 0 Critical, 2 Warning,
      3 Info, all Introduced and all fixed (D-7, D-8, and the e2e/composable test
      tightening) rather than deferred; no Pre-existing findings.
      `check-security` is **N/A**: the diff is CSS declarations, one viewport meta
      attribute, one Tailwind class list and tests. It touches no auth logic, no provider
      keys, no tenant boundary, no WebSocket, no upload, no user-input path, no agent or
      prompt surface, no dependency manifest and no deploy config. `AuthLayout.vue` is an
      auth *page layout* and the change to it is padding.

## 11. SRS Delta

None. `REQUIREMENTS.md` specifies nothing about viewport units, safe areas, breakpoint
boundaries or drawer widths; every intent source cited here is a `docs/UI/` document. Two
`docs/UI/` corrections are part of the change and are described in §7 rather than here,
because they are not SRS entries: `docs/UI/11-responsive-a11y.md:59` (drawer z-index, Q-11)
and, deferred to FU-5, `:110` (mobile action-button orientation, Q-13).

## 12. Deviation Log

- **D-1**: T-4's second assertion was written to carry the `sticky` class as well as the
  structural position, so **both** of its assertions fail before the fix. §1.2 change 3
  predicted it would already pass as a pure characterization pin. The combined form is
  strictly stronger and the structural half still guards the thing §1.2 cared about - that a
  later edit cannot move the bar out of the scrolled subtree while leaving an innocent class
  list - so it was kept rather than split.
- **D-2**: F-45's paired edit and F-18 landed in **one commit** (`b8e2b43`), because both
  edit `AgentDetailView.vue` and `AgentDetailView.test.ts` and git commits whole files.
  §9's rollback note claims the six findings "share no file except `AppShell.vue`"; that is
  false for these two, and reverting either one alone means an interactive revert rather
  than `git revert`. Every other finding is an independent commit as §9 describes.
- **D-3**: **T-1(a) had to be narrowed to the `@media` prelude.** As first written it scanned
  whole files for `max-width: 480px`, and reported five false positives:
  `.form-card`/`.warning-text` element width caps in `ChangeEmailView`, `ChangePasswordView`
  (x2), `DeleteAccountView` and `ProfileView`. Those share the literal with the breakpoint
  but are element widths; rewriting them to 479 would have resized four cards to satisfy a
  rule about media queries. The narrowed form was mutation-probed (a boundary reverted to
  768 turns it red).
- **D-4**: **T-1 strips `/* */` and `<!-- -->` comments before scanning.** Every assertion in
  it is a rule about a *declaration*, and the comment that explains why a declaration is
  written the way it is has to be free to name the spelling it rejects. Without this, writing
  `dvh, not 100vh` beside the fix failed the sweep the fix exists to satisfy - which it did,
  on the first run after the F-45 edit.
- **D-5**: `SModal.test.ts:29`'s pre-existing F-41 assertion moved from the literal
  `padding: 24px` to the four `max(24px, env(...))` floors. The guarantee it encodes - the
  panel is never flush against the viewport edge - is unchanged and now holds at `>= 24px`
  rather than `== 24px`. The literal was not weakened away; it was re-expressed because
  §7 item 3 changes the declaration it pinned.
- **D-6**: **AC-14 was rescoped before implementation, with the user's agreement.** The
  approved table implied four projects each running the whole suite; `workers: 1` plus
  `ci.yml:1040`'s bare `pnpm run test:e2e` makes that 88 serial spec runs. The three narrow
  projects are `testMatch`-scoped to the new spec. Recorded in §8, with the accepted cost as
  FU-8.
- **D-7**: **`--topbar-height-total` was added to `main.css`**, and `AppShell`'s grid track
  and `AppTopBar`'s height both read it. §7 item 3 specified the
  `calc(var(--topbar-height) + env(safe-area-inset-top, 0px))` expression inline in both
  places; the quality gate flagged that as two spellings of one number that must stay equal -
  the same shape as F-45 itself. Declared outside `@theme` deliberately: that block is
  Tailwind's token source and its values are processed at build time, while this one must
  reach the browser intact because `env()` resolves per device. Verified in the emitted CSS.
- **D-8**: `e2e/22-layout-contract.spec.ts:267`'s comment was reworded. It named the class
  literal `lg:h-[calc(100vh-3.5rem-3rem)]`, and **Tailwind scans e2e specs**, so the comment
  was emitting a real `calc(100vh - 6.5rem)` rule into the bundle - dead the moment §7 item 1
  moved the unit. Confirmed by rebuilding: the rule is gone. See FU-10 for the general case.
- **D-9**: `/build` could not perform the behavioural pass itself - the `verify` skill is
  reserved for explicit user invocation - so it handed AC-8, AC-10, AC-12, AC-13 and AC-14 to
  the user unticked. **The user ran `/verify` and all five are now closed**; see D-10 and §12a.
  This breaks a long streak of dossiers in this area closing unobserved.
- **D-10**: **The e2e spec had three defects of its own, and only running it found them.**
  It was written without a stack to run against, and every one of the three would have shipped
  as a silent CI failure:
  - `/profile` is not a route. The real one is `/account/profile`, and the tablet case was
    asserting a width against the 404 page - which renders a `<main>`, so it failed on a
    missing `.form-card` rather than on the navigation.
  - The agent detail tabs are `v-show`, not `v-if`, so **every** tab's cards are in the DOM.
    `.s-card` + `.last()` selected a card from the hidden Skills panel, whose `boundingBox()`
    is `null`. Now scoped with `:visible`.
  - **`toBeVisible()` is true from the first frame of the drawer's slide-in**, while the panel
    is still `translateX(-100%)`. Measured `x = -272` at 320px, so every geometry assertion
    after it was off by a full panel width. The helper now polls for the transform to settle.
    This one generalises: `--transition-slow` is 300ms and `SDrawer` animates on open, so any
    future spec that measures a drawer needs the same wait. It belongs in
    `frontend/.claude/skills/verify/SKILL.md`'s gotcha list beside the `click()`-scrolls note.
- **D-11**: **AC-8's test only checked one side of the boundary.** As approved and as first
  written it asserted that 480px gets the `sm` treatment - which a rule that stopped at 400
  would also satisfy. It proves the treatment applies, not that the boundary sits on the right
  pixel. Strengthened during verification to walk both sides, with the measured values in §12a.

- **D-12**: **A post-close `/code-review` found two regressions this task introduced, plus
  three surfaces `viewport-fit=cover` exposed that Q-5 had not counted.** All five are fixed;
  three further findings are routed to FU-11..FU-13. The two that were genuinely this task's
  fault are the ones worth carrying forward:
  - **`max-width: 100%` on `.sidebar` was not "inert on desktop", as its own comment claimed.**
    `AppShell` tweens `grid-template-columns` from `var(--sidebar-width)` to `0` over 300ms
    while the aside stays `visibility: visible`, and the aside carries `min-width: 0`. With an
    unscoped `max-width` the sidebar **reflows** through that tween instead of being clipped
    at a fixed 260px by the aside's `overflow-x: hidden`. Measured in the browser:
    `260 → 169 → 66 → 19 → 1 → 1` px, i.e. the nav labels squash to nothing on **every**
    collapse - which happens on every navigation into or out of a chatroom or workflow editor,
    not just on a manual toggle. Now scoped to `@media (max-width: 1023px)`, which is exactly
    the band where `AppShell` renders the drawer rather than the aside. Re-measured after the
    fix: a constant `260, 260, 260, 260, 260, 260`. Q-9 and Q-10 reasoned about the drawer
    case correctly and simply never considered the docked one.
  - **`SNetworkBanner.vue:54` was a third consumer of the topbar height and was not migrated**
    to `--topbar-height-total`. `main.css`'s comment for that token says "this is the one
    place that number lives so they cannot drift apart" - and a consumer had already drifted
    when it was written. `.s-net-banner--below-topbar` stayed at `calc(var(--topbar-height) +
    12px)` = 68px while the bar's bottom edge moved to `56px + inset`, and at `--z-banner`
    (350) against `--z-topbar` (200) the banner paints **over** the top bar rather than below
    it. D-7 created the token; it did not finish the migration.

  The three exposure findings are all the same shape - `viewport-fit=cover` is document-level,
  so it removed the browser's own inset from elements Q-5 never enumerated:
  `Landing.vue`'s nav (the first page an unauthenticated visitor sees, flush to the top edge),
  the `skip-link` (`main.css`, fixed at `top: 8px` and the first tab stop on every page), and
  `SNetworkBanner`'s unauthenticated position (`top: 12px`). **FU-3 had deferred the banner on
  the reasoning that another dossier was about to move it; that dossier
  (`2026-08-19-transient-feedback-channels`) has since landed, so the premise was stale and
  the deferral no longer applied.**
- **D-13**: **T-1(b) was asserting the wrong thing, and the review is what showed it.** It
  checked that the string `env(safe-area-inset-` appeared *somewhere* in each of the six
  files - so a surface that insets two of its four edges passed as fully protected, which is
  precisely how `Landing.vue` shipped with its nav under the status bar. The sweep is now
  **per edge**: each surface declares the edges it actually meets, and the failure names the
  missing one. Mutation-probed against the real bug - removing Landing's top inset reports
  `app/views/Landing.vue (top)`, where the old form passed. Two surfaces were added to the
  list at the same time (`main.css`, `SNetworkBanner.vue`), and `AppShell` correctly declares
  no `top` because its top inset lives in `--topbar-height-total`.
- **D-14**: `docs/UI/11-responsive-a11y.md` and §8's table both said the `desktop` project runs
  at **1440x900**. It does not: it uses `devices['Desktop Chrome']` with no override, which is
  **1280x720** (read back from the package). Both documents corrected to match the code rather
  than the reverse - setting an explicit 1440x900 would change the environment under all 22
  existing specs at close-out, which is real regression risk for no benefit, since the specs
  that care about width set their own. Recorded as FU-13 if the documented intent is
  preferred.

## 12a. Verification record (2026-08-21)

Driven against the running local compose stack (`/readyz` all-green **and** a real
`POST /api/auth/login` returning 200, because that endpoint's Vault dependency is the one
`/readyz` cannot see). `e2e/.e2e-seed.json` carried the full 11-key set before and after the
run, so the 24 skips are `onlyIn()` project gating, not the silent fixture starvation the
project verify skill warns about.

**Results.** `23-mobile-viewport.spec.ts`: **8 passed, 24 skipped** across all four projects.
`22-layout-contract.spec.ts` under the renamed `desktop` project: **10 passed, 1 skipped**,
including T-15, the geometry test whose constant this dossier moved to `dvh` - so the paired
edit did not disturb it.

**Mutation-probed, which is what makes the pass mean anything.** Three fixes were reverted
one at a time in a single run and produced **exactly five failures, each attributable**:

| Reverted | Failed |
|---|---|
| `.sidebar { max-width: 100% }` | drawer overflow at `mobile` and `mobile-xs` |
| `sticky bottom-0` back to `fixed bottom-0 left-0 right-0` | both agent action bar tests |
| `ProfileView` 767 back to 768 | the `md` boundary test |

The three that stayed green under all three mutations - the 480px auth card and both drawer
*width* tests - are correctly independent of them. All three mutations reverted.

**Boundary walked from both sides** (D-11), measured on `/login`:

| Viewport | border radius | shadow | wrapper max-width |
|---|---|---|---|
| 479px | `0px` | `none` | `none` |
| 480px | `8px` | present | `420px` |

The flip is exactly between 479 and 480. Before the fix, 480 produced the 479 row.

**Observed directly**, not only asserted: at 320x568 the drawer settles at 272px with all six
nav rows fully inside it and no horizontal scrollbar; at 375x812, scrolled fully to the
bottom of the Prompt tab, the Prompt Assistant card's bottom border is fully visible with the
action bar in flow beneath it - and a visible gutter below the bar, which is the content-box
sticky behaviour D-4/Q-12 predicted rather than a layout error.

**Not covered by any of this**: AC-3, AC-4b and AC-6. Headless Chromium has no collapsing URL
bar, no virtual keyboard and no display cutout, so `dvh`, the `visualViewport` inset and every
`env(safe-area-inset-*)` are identically inert there. They remain real-device checks.

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
- **FU-3**: safe-area insets are deliberately not applied to `SNetworkBanner.vue:45`
  (`position: fixed; top: 0`). It is being repositioned by `2026-08-19-transient-feedback-channels`
  (F-32), and adding a top inset to a `top` value that is about to change is churn. **Rewritten
  2026-08-21** per §1.2 change 5: this entry originally also covered `ImpersonationBanner.vue:19`,
  which no longer exists as a fixed element - `shared-overlay-and-shell-defects` put the banner
  in flow inside `App.vue`'s `.app-root` column, so it inherits the shell's insets and needs
  nothing of its own. `SNetworkBanner` is the only remaining subject.
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
- **FU-6**: **closed at build start, 2026-08-21** (§1.2 change 1). Both of its subjects have
  been overtaken. `GraphragGraphView.vue:168` no longer contains a viewport unit at all - the
  content-area dossier's F-10 rewrote it as `h-full` and pinned the absence at
  `GraphragGraphView.test.ts:65-78`. `AgentDetailView.vue`'s constant moved to `:988` and was
  rewritten by that dossier's F-51, which converts it from a deferred sibling into this
  dossier's own paired edit (§7 item 1, AC-16). Nothing is left to defer.
- **FU-7**: `docs/UI/11-responsive-a11y.md:375-378` states that E2E specs run at three
  viewports. Until AC-14 lands that is false, and it has been false since the line was
  written. AC-14 as rescoped makes it true only for the one spec that carries the
  viewport-conditional assertions, so the line must be amended to say that rather than left
  reading as a claim about the golden paths. If AC-14 is descoped entirely, the doc must be
  corrected instead of left aspirational.
- **FU-11**: **the top bar double-insets itself while an impersonation banner is showing.**
  `ImpersonationBanner` is in flow at the top of `App.vue`'s `.app-root` column, so when it
  renders it - not the top bar - is the element meeting the top edge. The bar still adds
  `padding-top: env(safe-area-inset-top)`, so it reserves a second inset's worth of blank
  space inside itself, while the banner's own text sits under the cutout with no inset at all.
  Deliberately not fixed here: the conditional ("inset only when nothing is above me") is not
  expressible in the CSS as structured, and the intersection of impersonating **and** a
  notched device is narrow. The principled fix is to move the top inset onto whichever element
  is first in the `.app-root` column.
- **FU-12**: **the exclusive boundaries are correct at integer widths and disagree at
  fractional ones.** `useBreakpoint` reads `window.innerWidth`, which is an integer, while a
  media query evaluates against a viewport width that is fractional under browser zoom and OS
  display scaling. At a 479.4px viewport JS reads 479 and reports `xs`, but
  `max-width: 479px` does not match, so the `sm` stylesheet applies - the same JS/CSS
  disagreement F-39 was about, relocated into a sub-pixel band. `max-width: 479.98px` /
  `767.98px` closes it in both directions (this is why Bootstrap uses `.98`). Not applied
  here because it **revisits Q-7**, which chose the plain integer form specifically to match
  the 7 blocks that were already correct, and because it would apply to those 7 too. It is a
  design decision, not a boundary correction, and wants the user's call.
- **FU-13**: two small items from the same post-close review. `AgentDetailView.vue:989`'s
  `lg:h-[calc(100dvh-3.5rem-3rem)]` still subtracts a **literal** 3.5rem for a top bar that is
  now `--topbar-height-total`, and does not account for the shell's `padding-bottom`, so on a
  large-viewport device that has insets (an iPad in landscape has a home-indicator inset) the
  panel overshoots its cell by `inset-top + inset-bottom` and is clipped by `.app-shell`'s
  `overflow: hidden`. Deriving it from the token would restore the pairing its own comment
  claims. And `playwright.config.ts`'s `desktop` project could set an explicit 1440x900 to
  match what `docs/UI/11-responsive-a11y.md` originally documented, rather than the doc being
  corrected down to the 1280x720 it actually runs at (D-14).
- **FU-9**: **the build already emits media-query range syntax, for every query in the app.**
  Measured in `frontend/dist/assets/*.css` after `pnpm build`: **30** blocks compiled to
  `@media (width<=N)`, including the two ad-hoc widths this dossier deliberately left alone
  (`Landing.vue:752`, `:798`) and the pre-existing `AppShell.vue:225` at 1023px. There is no
  `browserslist`, no `.browserslistrc`, no `build.target` and no lightningcss `targets`
  anywhere in `frontend/`, so Vite and Tailwind v4 apply their default modern CSS target and
  Lightning CSS rewrites `max-width: N` on the way out. **This makes Q-7(b)'s premise false.**
  That clarification refused to author `@media (width < 480px)` because range syntax needs
  Safari 16.4 while the floor at `11-responsive-a11y.md:346` is iOS Safari 16.2 - but the
  toolchain has been shipping exactly that syntax for every hand-written query all along. So
  either the stated floor is wrong, or **every media query in the product is inert on iOS
  Safari 16.2-16.3**, which would be a far larger defect than F-39 and would mean the mobile
  layout has never applied on those versions. Entirely pre-existing and untouched by this
  change - the boundary edits changed literals inside queries that were already being
  converted - so it does not block, but it wants a browser-targets decision rather than a
  CSS edit. It also answers the open question FU-4 parked: the emitted CSS *does* use range
  syntax, so the Tailwind-variant route is no worse than what ships today.
- **FU-10**: **Tailwind scans test and e2e files**, so naming a class literal in a test - even
  to assert its absence - emits a real CSS rule for it. D-8 removed one such stale rule.
  `AgentDetailView.test.ts:336` deliberately keeps `lg:h-[calc(100vh-8rem)]` to pin the
  absence of the superseded F-51 constant, and that one line is why the bundle still carries
  a dead `calc(100vh - 8rem)` utility. Harmless and not worth obfuscating a regression pin
  for, but a `@source not` directive or a scanner exclusion for `__tests__/` and `e2e/` would
  remove the whole class of artifact.
- **FU-8**: the 22 existing golden-path specs run at one width only, because AC-14's three new
  projects are `testMatch`-scoped to `23-mobile-viewport.spec.ts` (§8). That is the status quo
  rather than a regression, but it means no golden path is exercised at a phone width, so
  responsive breakage in the flows users actually walk stays invisible. Widening the scope is
  a wall-clock decision - `fullyParallel: false` with `workers: 1` makes it roughly linear in
  the number of projects - and should be revisited if the mobile surface justifies the minutes,
  ideally together with making the e2e job parallel.
