---
type: bugfix
status: implemented
created: 2026-08-22
requirements: []
depends_on: []
---

# Safe-area insets on the three top-edge surfaces the cutout sweep never enumerated

## 1. Summary

`2026-08-19-mobile-viewport-and-breakpoints` opted the document into the display cutout
(`viewport-fit=cover`, `index.html`) and inset the eight surfaces its Q-5 enumerated. Under
that meta the browser stops insetting anything by itself, so a surface left out is not
merely unimproved - it is **worse than before the change**. Three top-edge surfaces were
left out. An impersonation banner renders under the status bar or cutout on a notched
device, taking the "read only" warning with it; a toast - including an error toast - does
the same; and the top bar reserves a top inset it is not entitled to whenever the banner is
present, opening a band of empty background between the two. All three are invisible to the
whole test suite, because headless Chromium emulates no display cutout
(`docs/UI/11-responsive-a11y.md:397-401`).

Found by a `/code-review` of the branch after that dossier closed, recorded as FU-12 on
`docs/tasks/2026-08-21-visual-refinement-phase1-token-adoption/spec.md`.

## 2. Observed vs Expected

**Observed**

- **F-1 (banner).** `ImpersonationBanner.vue:18-40` declares `position: sticky; top: 0` and
  `padding: var(--space-2) var(--space-4)`, with no `env(safe-area-inset-*)` anywhere in the
  file. It is the first in-flow child of `.app-root` (`App.vue:41-42`), so whenever an admin
  is impersonating it is the element at y = 0 - the exact position the status bar and the
  display cutout occupy.
- **F-2 (top bar).** `AppTopBar.vue:70-81` sets `height: var(--topbar-height-total)` and
  `padding: env(safe-area-inset-top, 0px) var(--space-4) 0`, and `AppShell.vue:156` sizes its
  first grid track from the same `--topbar-height-total`
  (`main.css:183-184`: `calc(var(--topbar-height) + env(safe-area-inset-top, 0px))`). Each
  of those three is unconditional. When the banner renders, the bar is no longer at y = 0 -
  it starts below a row whose height is `var(--space-2)` twice plus a `--font-size-sm` line
  box - yet it still grows by the top inset and pads its content down by the same amount.
  The band between the banner and the bar's content is painted `--color-bg`, and the shell's
  first grid track over-reserves by the same amount.
- **F-3 (toasts).** `toasterProps.ts:10` mounts the toaster at `position: 'top-right'` and
  passes no `offset`. vue-sonner 2.0.9 then applies its own defaults - `VIEWPORT_OFFSET =
  "24px"` and `MOBILE_VIEWPORT_OFFSET = "16px"` (`vue-sonner/lib/index.js:296-297`) - as
  plain lengths from the layout viewport edge, which under `viewport-fit=cover` is behind
  the cutout. `main.css`'s sonner override block sets colour and type only and no offset.

**Expected**

Every element that meets a viewport edge insets itself by at least that edge's
`env(safe-area-inset-*)`, and no element insets an edge it does not meet. This is not a new
rule: it is the contract `mobileViewportContract.test.ts:104-161` already encodes, whose own
comment calls its list "the complete set of present-day elements that touch a viewport edge"
and whose per-edge assertion exists because "a surface that protects half of itself is
exactly the failure this sweep exists to catch" (`:110-114`). The list is incomplete, and
that is the defect: none of these three surfaces appears in it.

No `[Rxx.yy]` covers safe areas - `REQUIREMENTS.md` has no entry mentioning
`safe-area`, `viewport-fit`, `notch` or `cutout`. The intent source is that dossier's Q-5
contract plus `docs/UI/11-responsive-a11y.md:397-401`, which classifies cutout behaviour as
a device check rather than a suite gap. Recorded here because a bugfix whose "expected" has
no written source is a guess (§3, Q-1).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | There is no `[Rxx.yy]` for safe areas. What is the intent source? | **The closed dossier's Q-5 contract, as encoded in `mobileViewportContract.test.ts:115-130`.** No SRS Delta. | The rule is already written down and already enforced; it is the *enumeration* that is wrong, not the rule. Adding an SRS entry now would restate a contract the test tier already owns, and this dossier restores documented behaviour rather than defining new behaviour. |
| Q-2 | Should the top bar's inset become conditional on the banner, or should the banner overlay rather than displace? | **Conditional.** The banner stays in flow; the top inset moves from the bar to the banner when the banner is present. | The banner is deliberately in flow - `ImpersonationBanner.vue:19-23` records that fixed positioning was tried and reverted, because nothing then accounted for its height and `.app-shell`'s `overflow: hidden` left no way to scroll the top bar back into reach. Making it overlay again would reintroduce that. Whichever element is topmost must own the inset; that is the banner exactly when it renders. |
| Q-3 | How is "whichever element is topmost owns the inset" expressed without either element knowing about the other? | **A single inherited custom property on `.app-root`.** `--topbar-inset-top` defaults to `env(safe-area-inset-top, 0px)` and is redefined to `0px` while the banner is present; `--topbar-height-total` and the bar's `padding-top` both read it. | `AppTopBar.vue:75-79` already records that `--topbar-height-total` exists so the bar and the shell's grid track "cannot disagree". F-2 is that same class of bug one level up: three consumers of one number, and the number was wrong. Adding a second consumer-agnostic switch keeps the property count at one per fact instead of teaching each consumer about impersonation. |
| Q-4 | Toaster insets via the `offset` prop or via `--offset-*` in `main.css`? | **The `offset`/`mobileOffset` props in `toasterProps.ts`.** | `toasterProps.ts:1-7` exists precisely so the props the app ships are the props the a11y test mounts - its docstring records a label shipping with no button to attach to when the test supplied its own. Putting the insets in `main.css` would move a shipped behaviour back out of that file's reach. It also keeps this dossier out of `main.css`'s sonner block, which `2026-08-21-visual-refinement-phase2-identity-and-depth` retints (Q-6). |
| Q-5 | Inset only the edges `top-right` uses, or all four? | **All four, on both `offset` and `mobileOffset`.** | `position` is a prop. Insetting only the two edges today's value happens to use rebuilds the exact failure §2's Expected quotes - a surface that protects half of itself - one prop change later. vue-sonner applies each edge only for the positions that use it, so the unused ones cost nothing. |
| Q-6 | Does this depend on `2026-08-21-visual-refinement-phase2-identity-and-depth`, the one active dossier that touches a file here? | **No.** `depends_on: []`. | The only shared file is `main.css`, and the regions are disjoint: this dossier edits the `:root` block at `:183-184` that carries `--topbar-height-total` (deliberately outside `@theme`, because `env()` must reach the browser intact), while phase 2 edits `@theme`'s colour values and the `[data-sonner-toaster]` colour block. Q-4 keeps the toaster fix out of `main.css` entirely. Whoever builds second rebases; neither needs the other's output. |
| Q-7 | The banner uses `--space-2`/`--space-4` padding. Does the inset add to that padding or replace it? | **Add.** `padding-top: calc(var(--space-2) + env(safe-area-inset-top, 0px))`. | The `max()` form used elsewhere is right for a *gutter* whose only job is to keep clear of the edge (`SModal`, `AuthLayout`). The banner's `--space-2` is interior padding around text that must remain interior padding under the strip; `max()` would swallow it whenever the inset exceeded 8px, which is every notched device. This is a real difference from the sibling sites and is why §8's T-3 asserts the `calc(` form here rather than reusing the `max()` assertion. |

The two rows below were decided at approval on 2026-08-22, after `/build`'s freshness pass
found that §7's F-2 design does not work as written and that §6 undercounted the consumers
of `--topbar-height-total`. Both are recorded here rather than as deviations, because they
were agreed before any code moved; §12 carries what changed afterwards.

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-8 | §7's F-2 puts `--topbar-inset-top` on `.app-root` and expects `--topbar-height-total` to follow. It does not: a custom property substitutes its `var()`s at computed-value time **on the element that declares it**, so a descendant override cannot reach a total already resolved at `:root` - the descendant inherits the substituted value. Measured in Chromium at 100px where 56px was intended. Which correction? | **Declare the total on `:root, .app-root`** - one `calc()`, two subjects. | The alternatives were worse in ways that matter here. `:root:has(.impersonation-banner)` is pure CSS but makes a global stylesheet depend on a slice component's class name, which breaks silently on a rename; a JS attribute on `documentElement` adds a DOM side effect for a fact the cascade can already express. Both also give `SNetworkBanner` a *worse* offset than the double-subject form (Q-9). The double subject keeps §7's "one property per fact" intact - the value is still written once - and re-resolves it exactly where an override can exist. |
| Q-9 | §6 cleared `SNetworkBanner`, but it is a **fourth** consumer of `--topbar-height-total` (`:61`) and renders *outside* `.app-root` (`App.vue:36`), so it sees no override. During impersonation its below-topbar offset already ignores the banner's height entirely. Leave as a follow-up, or fix here? | **Fix here.** Move it inside `.app-root`, into a zero-height `position: relative` anchor placed after the impersonation banner, and make the below-topbar mode `absolute` rather than `fixed`. | Arithmetic over a height is what produced F-2 in the first place; adding a second number for the banner's height would repeat it, and the banner's height is not a constant (the text wraps on a narrow viewport). Measuring from a flow position removes the arithmetic instead of correcting it - neither file has to know the other's height, and nothing can drift. The one assumption it takes on is that the authenticated shell does not scroll the document, which `.app-shell`'s `overflow: hidden` plus `flex: 1 1 0px` already guarantee and which is stated in the rule. |

## 4. Reproduction

Deterministic, but only on hardware with a display cutout or a status-bar inset - iOS
Safari on a notched iPhone, or Chrome on an Android device with a cutout. Headless Chromium
resolves every `env(safe-area-inset-*)` to `0px`, which is why the whole suite is blind to
all three (`docs/UI/11-responsive-a11y.md:397-401`).

**F-1 / F-2**

1. Sign in as an Admin on a notched device, portrait.
2. Go to `/admin/users`, pick a user, start impersonation.
3. The orange banner's text renders under the status bar. Rotate to landscape: it renders
   under the cutout.
4. Below it, an empty `--color-bg` band separates the banner from the top bar's content,
   the height of the top inset.

**F-3**

1. On the same device, trigger any toast (any failing action; `toast.error` on a 4xx).
2. The first toast's top edge sits `24px` from the layout viewport top, which is behind the
   status bar or cutout rather than below it.

Simulation, for a developer without the hardware: temporarily replace the `env()` calls
under test with a constant (`44px`) and load at any viewport. This shows the geometry but
proves nothing about the real thing, and must not be committed.

## 5. Root Cause Analysis

1. `index.html`'s `viewport-fit=cover` removes the browser's own inset from **every**
   surface at once. The closed dossier's `mobileViewportContract.test.ts:99-103` states this
   coupling explicitly: "insets without the meta are inert, but the meta without insets is
   actively harmful".
2. Which surfaces need an inset was therefore decided by enumeration, in
   `INSET_SURFACES` (`mobileViewportContract.test.ts:115-130`), described in its own comment
   as "the complete set of present-day elements that touch a viewport edge".
3. **Root cause: the enumeration was derived from the layout tree, and all three misses are
   surfaces that are not in it.** `ImpersonationBanner` is rendered by `App.vue:42` as a
   conditional sibling *above* the layout component, so reading the layouts does not reveal
   it. Toasts are mounted by vue-sonner into its own teleported container, configured from a
   `.ts` file, so no `.vue` or `.css` file mentions their geometry at all. Neither is
   reachable by inspecting the surfaces the sweep was written from.
4. F-2 is the same root cause seen from the other side. `--topbar-height-total` encodes "the
   top bar is at the top of the screen" (`main.css:175-184`), which the enumeration treated
   as invariant because the enumeration did not contain the one element that falsifies it.

An aggravating factor, not the root cause: the sweep is a source scan, so it can only check
files it is told about. That is not fixable in kind - `:9-10` already concedes "a source
scan is a blunt instrument, but the alternative here is no guard at all" - which is why §7
adds an *ordering* guarantee (a surface must be listed before it can be forgotten) rather
than trying to derive the list.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Portrait and landscape on any device reporting a non-zero
`safe-area-inset-top`; every other device is unaffected because each inset resolves to
`0px`. F-1 and F-2 are scoped to an active impersonation session, which is Admin-only. F-3
affects every user, since a toast is the app's only transient-message channel ([R24.38]).
No persisted data is involved, so no repair plan.

**Sibling suspects.** Every element that could meet a viewport edge, checked against the
enumeration:

| Surface | Verdict |
|---|---|
| `SNetworkBanner.vue:45-49` | **Cleared** here, and that was wrong - see Q-9 and D-2. Its own inset is fine; what this row never asked is what its `--below-topbar` offset *assumes*. |
| `App.vue`'s `.app-root` | **Cleared.** Carries `min-height: 100dvh` only; its children own their edges. |
| `main.css`'s `skip-link` utility | **Cleared.** `top`/`left` insets present and listed. |
| `AppShell`, `AuthLayout`, `Landing`, `SDrawer`, `SModal`, `AppTopBar` | **Cleared.** All listed with their edges. |
| `PublicLayout` | **Cleared,** and deliberately absent from the list per its comment - it adds no padding and wraps only `Landing`, which carries its own gutters. |
| `SIdleDialog` | **Cleared.** Renders inside `SModal`, which insets all four edges. |
| **`ImpersonationBanner.vue`** | **Confirmed - F-1.** |
| **`toasterProps.ts`** | **Confirmed - F-3.** |
| **`AppTopBar.vue` + `AppShell.vue` + `main.css`'s `--topbar-height-total`** | **Confirmed - F-2**, the inverse defect: an inset applied where it is not owed. |

The two confirmed misses are both *outside the layout tree*, which is the pattern the fix
has to close rather than the two instances.

## 7. Fix Design

**F-1.** `ImpersonationBanner.vue` gains
`padding-top: calc(var(--space-2) + env(safe-area-inset-top, 0px))`, replacing the top half
of its `padding` shorthand. Additive rather than `max()`, per Q-7.

**F-2.** `main.css`'s `:root` block gains `--topbar-inset-top: env(safe-area-inset-top, 0px)`
and `--topbar-height-total` becomes `calc(var(--topbar-height) + var(--topbar-inset-top))`.
`AppTopBar.vue`'s `padding-top` reads `var(--topbar-inset-top)` instead of calling `env()`
itself. `App.vue` binds a class on `.app-root` from `useImpersonation().isImpersonating`,
and that class sets `--topbar-inset-top: 0px`. Both existing consumers follow through
inheritance with no further change, and `AppShell.vue:156` is untouched.

Why this is not a symptom patch: the defect is that three consumers read one number that
encoded an assumption ("the bar is topmost") which the banner falsifies. The fix moves the
assumption into a single named property that the one element able to falsify it overrides.
A consumer added later inherits the corrected value without knowing impersonation exists -
which is the property `--topbar-height-total` was introduced for in the first place, and
which its own comment claims ("this is the one place that number lives") while three
consumers had in fact drifted from it once already (`2026-08-19-mobile-viewport-and-breakpoints`
D-12, `SNetworkBanner`).

**F-3.** `toasterProps.ts` returns `offset` and `mobileOffset`, each with all four edges as
`max(<default>, env(safe-area-inset-<edge>, 0px))` - `24px` for `offset`, `16px` for
`mobileOffset`, matching vue-sonner's own `VIEWPORT_OFFSET` / `MOBILE_VIEWPORT_OFFSET`
(`vue-sonner/lib/index.js:296-297`) so geometry is byte-identical wherever the inset is
zero. `max()` here and not `calc()`, because these *are* gutters in Q-7's sense.

**The enumeration itself.** All three surfaces are added to `INSET_SURFACES`
(`mobileViewportContract.test.ts:115-130`): `ImpersonationBanner.vue` with `['top']`,
`toasterProps.ts` with all four, and a note on the `AppTopBar.vue` entry recording that its
`top` is now owned by `--topbar-inset-top`. Because the map is keyed by path and read with
`read(resolve(SRC, rel))`, a `.ts` file works unchanged.

This still leaves the list hand-maintained. §8's T-4 closes the ordering hole instead: it
asserts that every file declaring `position: fixed` or `position: sticky` with `top: 0`, and
every file naming a `Toaster` position, is either listed in `INSET_SURFACES` or in an
explicit exemption list with a reason. A future surface then has to be classified rather
than merely overlooked - which is the only part of the root cause a source scan can fix.

## 8. Regression Test Plan

All four are unit-tier source assertions in `frontend/src/app/__tests__/mobileViewportContract.test.ts`,
which is where this contract already lives. Nothing here can be a rendered check: no tier in
this repository can observe a display cutout (§4).

- **T-1** - add `'slices/admin/components/ImpersonationBanner.vue': ['top']` to
  `INSET_SURFACES`. The existing "insets every edge of every surface that meets one"
  (`:136`) then fails against current code, because the file contains no
  `env(safe-area-inset-top`. This is the failing-test-first step for F-1.
- **T-2** - add `'app/toasterProps.ts': ['top', 'right', 'bottom', 'left']`. The same test
  fails on all four edges. Failing-test-first for F-3.
- **T-3** - a new assertion that `AppTopBar.vue` no longer calls `env(safe-area-inset-top`
  directly but reads `var(--topbar-inset-top)`, that `main.css` declares
  `--topbar-inset-top` with an `env()` fallback, and that some rule sets it to `0px`. Fails
  against current code, where the property does not exist. Failing-test-first for F-2.
  Note the `max()`-with-fallback assertion at `:151` scans `INSET_SURFACES` files for
  `env(...)` calls lacking a comma; the banner's `calc(var(--space-2) + env(..., 0px))`
  satisfies it because the fallback is present, and Q-7 explains why the form differs.
- **T-4** - the ordering guard from §7: every `position: fixed`/`sticky` surface anchored at
  `top: 0`, plus any file configuring a toaster position, is either in `INSET_SURFACES` or
  in a named exemption list. Written last, since it is red until T-1 to T-3 land. It must
  open with a count assertion, as every other sweep in this file does (`:14-15`), so a glob
  that stops matching fails loudly rather than passing vacuously.

**Behavioural verification is a device check, not a test.** AC-6 requires the three
surfaces be confirmed on real hardware with a cutout, portrait and landscape, and says so
rather than implying the suite covers it - the closed dossier left its AC-3/AC-4b/AC-6
unticked for exactly this reason and that precedent should hold.

## 9. Risks and Rollback

- **The banner's `calc()` grows the banner on notched devices**, pushing the shell down by
  the inset. That is the intended behaviour and it is what the browser did before
  `viewport-fit=cover`; it is called out because a reviewer seeing the shell move may read
  it as a regression.
- **`--topbar-inset-top` is a second property describing one fact**, so a future rule that
  sets one and not the other reintroduces F-2 inverted. T-3 pins both halves together for
  that reason.
- **The toaster offsets are the riskiest edit for zero-inset devices**, because they replace
  a library default rather than adding to a value this repo controls. Matching
  `VIEWPORT_OFFSET`/`MOBILE_VIEWPORT_OFFSET` exactly is what makes the change a no-op there;
  if vue-sonner changes those defaults on upgrade, the values drift silently. Worth a
  comment naming the library constants, which is what §7 specifies.
- **Rollback** is `git revert` per fix; the three are independent. Reverting F-2 alone
  restores today's double inset without affecting F-1 or F-3.

## 10. Acceptance Criteria

- [x] AC-1: T-1, T-2 and T-3 each fail against current code for the documented reason, and
      pass after their fix. Verified by running them before the corresponding edit.
      **Observed: 5 failures, 7 passes** on the first run - the per-edge sweep against
      `ImpersonationBanner.vue` (top) and `toasterProps.ts` (four edges), plus all four
      T-3 assertions. Each failed for its documented reason, not incidentally.
- [x] AC-2: `ImpersonationBanner.vue` insets its top edge additively, so its interior
      `--space-2` survives under a non-zero inset (Q-7). **Measured** under §4's simulation
      at a 44px inset: banner 0-79px, its text at y=52, leaving exactly 8px of interior
      padding above and 8px below. See D-5 - it now insets three edges, not one.
- [x] AC-3: `AppTopBar.vue` contains no direct `env(safe-area-inset-top` call, and both it
      and `AppShell.vue:156` still derive from `--topbar-height-total` with no new consumer
      of impersonation state. `AppShell.vue` is untouched by this task's diff. **Measured**
      at a 44px inset: with the banner present the bar's `padding-top` is `0px`, its height
      exactly `56px`, and the shell's first grid track `56px` - no empty band. Without it,
      `44px` / `100px` / `100px`, identical to before the change. The only new reader of
      impersonation state is `App.vue`, which is where §7 put it.
- [x] AC-4: `toasterProps.ts` returns `offset` and `mobileOffset` covering all four edges,
      each `max()`-guarded with the vue-sonner default as the floor, so geometry on a device
      with no inset is unchanged. Pinned twice by `ToasterSafeArea.test.ts` (D-7): all eight
      custom properties reach the mounted container verbatim, and the two floors are
      compared against `VIEWPORT_OFFSET` / `MOBILE_VIEWPORT_OFFSET` read out of the
      installed package rather than against themselves.
- [x] AC-5: T-4 passes, and its exemption list is empty or every entry carries a stated
      reason. Seven candidates found across 762 scanned files: five classified in
      `INSET_SURFACES`, two exempted (`STable.vue`, `LandingIntro.vue`), each with a reason.
- [ ] AC-6: **device check, on hardware with a display cutout, portrait and landscape** -
      the impersonation banner's text clears the strip, no empty band appears between banner
      and top bar, and the first toast clears the strip. Left unticked rather than claimed if
      no such device is available; nothing in CI can close this.
      **Left unticked: no notched hardware was available.** What *was* done instead is
      §4's simulation - the shipped rules with `env(safe-area-inset-top, 0px)` replaced by a
      constant `44px`, measured in Chromium at 390x844 in both states (numbers under AC-2
      and AC-3). That proves the cascade and the geometry; it proves nothing about a real
      inset, and in particular **the landscape half of this criterion has been reasoned
      about and not seen** - D-5's side insets are the least-observed part of the change.
      The toaster's rendered offset has not been observed at any inset, real or simulated.
- [x] AC-7: `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build` green, and
      `ToasterAccessibility.test.ts` passes unmodified - it mounts these exact props, so a
      shape change there is a signal, not a formality. Final run: **220 files, 1521 tests,
      all passing**; lint, typecheck and build clean.

## 11. SRS Delta

None. `REQUIREMENTS.md` contains no safe-area entry and this dossier adds no behaviour: it
restores the contract `mobileViewportContract.test.ts:104-161` already encodes to the three
surfaces its enumeration missed (Q-1).

## 12. Deviation Log

Q-8 and Q-9 in §3 carry the two changes agreed **at approval**, before code moved. These
are what changed **after** it.

- **D-1** - `--topbar-height-total` is declared on `:root, .app-root`, not on `:root`
  alone as §7 implies. This is Q-8's correction; it is repeated here because a reader of
  §7 alone would write the version that does not work. The property substitution rule
  behind it was verified empirically in Chromium before the design was changed, not
  argued from memory: a total declared only at `:root` computed to `100px` under a
  descendant override that should have made it `56px`, and the same declaration repeated
  on the overriding element computed to `56px`.

- **D-2** - `SNetworkBanner` was taken into scope (Q-9). §6 had **cleared** it, on the
  strength of its own `top: max(var(--space-3), env(...))` rule - which is correct for
  the unauthenticated mode and says nothing about `--below-topbar`. The sibling sweep
  asked "does this surface inset itself" and the right question for this file was "what
  does this surface's offset assume". It ships as a zero-height `position: relative`
  anchor in `App.vue` with the banner's below-topbar mode switched to `absolute`.

- **D-3** - `AppTopBar.vue`'s `INSET_SURFACES` entry is `[]`, not `['top']` with an
  explanatory note as §7 specified. The note alone would not have worked: the per-edge
  sweep tests for a literal `env(safe-area-inset-top` in the file, and the whole point of
  F-2 is that the bar no longer calls `env()` itself. T-3's four assertions carry the
  contract instead, and the entry's comment says so - an empty list would otherwise be
  the one entry in the map that asserts nothing.

- **D-4** - T-4 matches `inset: 0` as well as `top: 0`. §8 specified `top: 0`. `inset: 0`
  is the same statement spelled shorter and three surfaces in the tree use it
  (`SModal`, `SDrawer`'s overlay, `LandingIntro`); leaving it out would have left a known
  hole in a guard whose entire purpose is closing a known hole.

- **D-5** - `ImpersonationBanner.vue` insets **three** edges, not the one `padding-top`
  §7 specified, and its `INSET_SURFACES` entry is `['top', 'left', 'right']`. Found by a
  post-implementation `/code-review`, and it is the dossier's own reproduction: §4 step 3
  says "Rotate to landscape: it renders under the cutout", and AC-6 asks for both
  orientations, but §7's fix covers portrait only. In landscape on a notched device
  `safe-area-inset-top` is `0px` and the sensor housing becomes a left/right inset, which
  this full-bleed row never cleared. That is precisely the half-protected surface §2
  quotes the sweep as existing to catch - and with `['top']` in the map, the sweep could
  never have flagged it. The sides are `max()` and the top stays additive, which is Q-7's
  own distinction applied per edge: the side `--space-4` is a gutter, the top `--space-2`
  is interior padding. `AuthLayout` and `Landing` are the in-repo precedent.

- **D-6** - a `useImpersonationFlag` export was added to the admin slice and then
  **reverted**; the shipped code matches §7's `useImpersonation().isImpersonating`. It is
  recorded because the reasoning was wrong in an instructive way. `App.test.ts` threw
  `No 'queryClient' found in Vue context` when `App.vue` began calling the composable,
  which was read as "App.vue is mounted above the QueryClient provider" and answered with
  a narrower export. The real cause was that `App.test.ts` never installed
  `VueQueryPlugin` at all: `main.ts:56` installs it with `app.use()`, an app-level
  provide that the root component resolves like any other. Verified by mounting a root
  component that calls `useMutation()` under an app-level plugin - no throw. **A test
  harness that differs from `main.ts` is not evidence about the application.** The
  correction was to give the test the plugin the real app has always had.

- **D-7** - `ToasterSafeArea.test.ts` is a new file; §8's plan had four tests, all source
  assertions in `mobileViewportContract.test.ts`. Two things there are unreachable by a
  source scan. That vue-sonner still *reads* `offset`/`mobileOffset` is one - a rename in
  the library would leave the scan green and put every toast back behind the cutout. §9's
  named risk is the other: its first version asserted the floors against the same
  literals `toasterProps.ts` writes, comparing the file with itself, so a vue-sonner bump
  that moved `VIEWPORT_OFFSET` would have passed. It reads the constants out of the
  installed package now. That first version shipped and was caught by `/code-review`.

- **D-8** - two existing tests changed. `AppFeedback.test.ts` gains a case asserting the
  `.app-root--impersonating` class tracks the flag, which is the only part of F-2 jsdom
  can see. `App.test.ts:93` asserted `SNetworkBanner` sits **outside** `.app-root`; D-2
  reverses that contract, so the assertion was replaced - not deleted - by a stronger one
  that pins containment **and DOM order**, order being the whole mechanism. Its old
  comment justified the exclusion with "the wrapper would constrain them", which is false
  for a `position: fixed` child of a non-transformed ancestor.

## 13. Follow-ups

- **FU-1** - `mobileViewportContract.test.ts` is a source scan, and T-4 only guarantees that
  a *new* top-anchored surface is classified. It cannot see a surface whose geometry is set
  from JavaScript, a library default, or an inline style. The durable check is a rendered
  one against a browser that emulates an inset; Playwright cannot, but Chrome DevTools
  Protocol's device-metrics override can set `displayFeature`/insets. Worth costing once
  there is a second reason to drive CDP directly.
- **FU-2** - `docs/UI/11-responsive-a11y.md:397-401` lists cutout behaviour among the three
  things outside the suite's reach but gives no checklist for confirming them; §7.2's manual
  checklist has no safe-area row. AC-6 is currently the only written form of that check, and
  it will leave with this dossier. **This is now the load-bearing one**: AC-6 closed
  unticked and D-5's landscape insets are the least-observed part of the change, so the
  only durable record of what to check on a real device is a criterion in a closed
  dossier. Moving it into that document's §7.2 is what stops it evaporating.

- **FU-3** - `--topbar-height-total` is now declared twice, and a consumer rendered
  **outside** `.app-root` silently gets the `:root` value - correct on every device with
  no inset, wrong by exactly the inset during an impersonation session. There is no such
  consumer today (D-2 moved the last one in), and T-3 pins the `.app-root` half of the
  declaration, but nothing detects a *new* outside consumer. The check has the same shape
  as T-4: a sweep for `var(--topbar-height-total)` in files whose element is not a
  descendant of `.app-root`. That descendant relationship is not something a source scan
  can determine, which is why it is a follow-up rather than a fifth test here.

- **FU-4** - `e2e/baselines/visual-token-parity.json` predates D-2's
  `div|app-root__overlay-anchor` element. Nothing fails: the spec iterates baseline
  signatures and reports absent ones without failing, so a *new* element is simply never
  compared. It matters only because
  `2026-08-22-visual-refinement-phase3-verification-and-debt`'s AC-1 regenerates that
  baseline, and whoever does should know the extra div is expected rather than drift.

- **FU-5** - D-6's real finding generalises past this task. `App.test.ts` was mounting the
  application root under a plugin set that did not match `main.ts` - it had pinia, router
  and i18n but not `VueQueryPlugin` - and that gap read as a fact about the application
  for long enough to produce a wrong design. Nothing asserts that a test harness mounting
  `App.vue` installs what `main.ts` installs. A single shared mount helper, or a test that
  compares the two plugin lists, would close a class of false evidence rather than this
  one instance.
