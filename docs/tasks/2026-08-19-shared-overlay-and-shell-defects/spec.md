---
type: bugfix
status: in-progress
created: 2026-08-19
approved: 2026-08-21
requirements: []
depends_on: [2026-08-19-transient-feedback-channels]
---

# Shared overlay components and app-shell chrome

Source: `docs/audits/2026-08-19-page-presentation-scroll-and-feedback/findings.md`
(F-5, F-6, F-7, F-8, F-9, F-22, F-30, F-33, F-34, F-41, F-43, F-50).

## 1. Summary

Twelve defects sit in two places that no single view owns: the overlay primitives in
`frontend/src/shared/ui/` and the app shell's own chrome in `frontend/src/app/`. Each is
fixed once and every consumer benefits. Three are structural failures of the shell: the
impersonation banner paints over the top bar at `z-index: 9999`
(`frontend/src/slices/admin/components/ImpersonationBanner.vue:19-23`), a caught render error
unmounts the entire layout rather than the failed view
(`frontend/src/app/App.vue:29-46`), and the 404 route carries no layout meta so a logged-in
user who mistypes a URL loses the sidebar, top bar and switcher
(`frontend/src/app/router.ts:43-47`). Four are shared-component defects that look wired and
are not: `STable`'s `stickyHeader` prop can never engage
(`frontend/src/shared/ui/STable.vue:465-468`), `SDropdown` renders menus past the viewport
bottom with no flip and no height cap (`frontend/src/shared/ui/SDropdown.vue:112-125`),
`SAlert` announces every static informational panel assertively
(`frontend/src/shared/ui/SAlert.vue:67`), and `SEmptyState` pins its content to the top of
any container that stretches it (`frontend/src/shared/ui/SEmptyState.vue:46-55`). The
remainder are smaller: a table with no loading state, a prop name that does not exist, an
unbounded modal panel, and a hardcoded tooltip z-index.

One finding in the audit's list, F-43, is not fixed here. It is a feature that was never
built, and verification found it cannot manifest today; see Q-12.

### 1.1 Freshness re-verification (2026-08-21)

Every citation below was re-checked against the tree. Four things moved; the rest hold.

1. **F-5 is now half fixed, and the half that remains is the one that matters.**
   `ImpersonationBanner.vue:27` already reads `z-index: var(--z-banner, 350)` - the completed
   `2026-08-19-transient-feedback-channels` went further than Q-1 anticipated and tokenised it,
   with a comment recording that sonner's toasts were being clipped. So the "9999 outranks
   `--z-modal`, `--z-toast` and `--z-tooltip`" arm of F-5 **no longer reproduces**, §7 item 2's
   z-index edit is **already done**, and **T-2 passes today** rather than failing first. What is
   untouched is `position: fixed; top: 0; left: 0; right: 0` (`:19-22`): nothing reserves the
   banner's height, so it still paints over the top 33px of the top bar. Q-2's fix - taking the
   banner out of `fixed` - is unchanged and is now the whole of F-5.
2. **The repository-wide numeric-`z-index` sweep now returns six sites, not seven.**
   `ImpersonationBanner.vue` dropped off it. §6's "two confirmed and four cleared" becomes **one
   confirmed** (`STooltip.vue:73`) and five cleared, and AC-11's grep expectation is unaffected.
3. **F-23 in the chatroom has landed**, which Q-8 wrote about in the future tense and under the
   wrong slug. See the corrected Q-8: the assumption that this dossier's F-30 fix would leave
   F-23 with only a stretch to add is no longer true, and the two are now independent.
4. **Line drift only**, from `2026-08-19-chatroom-scroll-and-composer` landing in files this
   dossier cites: `App.vue` `<ErrorBoundary>` `:29` -> `:32` (still outside the layout - F-6
   reproduces), `ChatroomHeader.vue` `<SDropdown` `:95` -> `:99`, `ChatroomComposer.vue`
   `z-index: 20` `:303` -> `:327`. `App.vue:30` also now passes
   `<SNetworkBanner :below-topbar="..." />`, which makes FU-4 a live interaction rather than a
   prediction.

Re-confirmed unchanged and still reproducing: F-6 (`App.vue:32` wraps the layout), F-7 (the
catch-all record at `router.ts:43-47` still declares no `meta`), F-8 (`STable.vue:465`), F-9
(`SDropdown.vue:112`), F-22 (`SAlert.vue:67`), F-30 (`.s-empty-state` at `:46-55` still has no
`justify-content`; the `justify-content: center` now visible at `:62` belongs to
`.s-empty-state__halo`, a different rule - do not mistake it for a fix), F-33
(`ProjectKeysView.vue:38` still destructures only `keys`), F-34 (`SkillWorkbench.vue:173` still
passes `:description`), F-41 (`SModal.vue:128`), F-50 (`STooltip.vue:73` still `z-index: 50`).

**New downstream consumer.** `2026-08-21-visual-refinement-phase1-token-adoption` (approved,
Blocked) lists this dossier as an **overlap prerequisite**: it rewrites the scoped style blocks
of `STable`, `SDropdown`, `SAlert`, `SEmptyState`, `SModal` and `STooltip` - the same six files
and, in places, the same rules this dossier edits. This dossier goes first; that one rebases.

## 2. Observed vs Expected

### F-5 (major) - the impersonation banner is painted over the top bar

- **Observed** - **amended 2026-08-21, see §1.1.** `ImpersonationBanner.vue:18-37` is
  `position: fixed; top: 0; left: 0; right: 0` with `z-index: var(--z-banner, 350)` at `:27`.
  The z-index was `9999` when this was written; `2026-08-19-transient-feedback-channels`
  tokenised it, so **the final sentence of this paragraph no longer holds** and the banner no
  longer outranks modals, toasts or tooltips. Everything else below still reproduces, because
  the defect is the positioning, not the layer. Mounted as a sibling of the
  layout at `frontend/src/app/App.vue:31`. Nothing reserves space for it: `.app-shell`
  (`frontend/src/app/layouts/AppShell.vue:137-146`) sets no offset, no `z-index`, no
  `transform` and no `filter`, so the banner and the top bar share the root stacking context
  and 9999 beats `AppTopBar.vue:79`'s `var(--z-topbar)` (200,
  `frontend/src/shared/styles/main.css:80`). The banner is roughly 33px tall (0.5rem padding
  top and bottom at `:28` plus a `0.875rem` line box at `:32`) against a 56px top bar
  (`--topbar-height`, `main.css:66`), so it covers the upper 33px of the 40x40 sidebar toggle
  (`AppTopBar.vue:89-94`) and most of the wordmark. `.app-shell` is `overflow: hidden`
  (`:142`), so no scroll can move the top bar clear. 9999 also outranks `--z-modal` (400),
  `--z-toast` (500) and `--z-tooltip` (600) (`main.css:85-87`).
- **Expected** - `docs/UI/02-layout-shell.md` §4.3 places the top bar at 56px, `--z-topbar`,
  sticky top 0, and the §1 integration diagram places the banner above it without
  reconciling the two. `docs/UI/01-design-system.md:53-59` is the authoritative z-index
  scale and has no entry above 600. `SNetworkBanner.vue:46` already models the correct
  pattern with `var(--z-banner, 350)`, and `main.css:82-84` states the rule verbatim: "above
  chrome, below modals".

### F-6 (major) - a caught render error replaces the entire shell

- **Observed** - `App.vue:29-46` places `<ErrorBoundary>` outside
  `<component :is="layoutComponent">`, so the boundary's `<slot v-else />`
  (`frontend/src/app/ErrorBoundary.vue:64`) is the whole layout. On `failed = true` the
  layout is swapped for the block at `ErrorBoundary.vue:50-63`, styled at `:68-73` as
  `max-width: 32rem; margin: 4rem auto; padding: 1.5rem; text-align: center`, with no shell
  around it. The `route.fullPath` watch at `:35-40` cannot rescue the user because the
  navigation chrome needed to change route is exactly what was unmounted.
- **Expected** - the component's own docstring at `ErrorBoundary.vue:6-7` states the intent
  verbatim: "`onErrorCaptured` lets us swap in a fallback for that subtree instead, keeping
  the rest of the shell alive." `docs/UI/12-shared-patterns.md:283` assigns ErrorBoundary the
  Global level with "Retry button + fallback UI", meaning a fallback for the failed subtree.

### F-7 (major) - the 404 route selects `AuthLayout` for an authenticated user

- **Observed** - `router.ts:43-47` defines the `/:pathMatch(.*)*` record with `path`, `name`
  and `component` and no `meta` key at all. `router.beforeEach` (`:55-75`) only reads meta
  and forwards it to `runGuards`; `guards.ts` is pure and never writes `to.meta`. `App.vue:22`
  therefore falls through to `route.meta.requiresAuth ? AppShell : AuthLayout` with
  `requiresAuth` undefined and selects `AuthLayout`. Every other route record in the tree
  declares either `layout` or `requiresAuth` (for example
  `frontend/src/slices/identity/routes.ts:8,46`), so the 404 is the only record that reaches
  this branch.
- **Expected** - `docs/UI/02-layout-shell.md:16` and §7 (`:351`): "Uses `AppShell` if
  authenticated, `AuthLayout` if not", and the §9 route table row for `/:pathMatch(.*)*`
  ("App or Auth | Depends on auth", `:405`).

### F-8 (major) - `STable`'s `stickyHeader` prop is inert

- **Observed** - `STable.vue:465-468` declares `.s-table-wrap { width: 100%; overflow-x: auto }`
  and never declares `overflow-y`. Per CSS Overflow 3 §3.1, when one axis is not `visible` the
  other computes to `auto`, so the wrapper is a scroll container on both axes and is the
  nearest scrollport for the sticky `<thead>` at `:488-492`
  (`position: sticky; top: 0; z-index: 10`). Nothing gives the wrapper a height: a grep for
  `s-table-wrap` across `frontend/src` returns only that declaration, and both `sticky-header`
  consumers drop the table into normal flow with `class="mt-6"`
  (`frontend/src/slices/agents/views/AgentListView.vue:319-329`,
  `frontend/src/slices/conversation/views/ChatroomListView.vue:269-279`). The wrapper never
  scrolls vertically, `top: 0` is permanently satisfied, and the header scrolls away with
  `main.app-shell__content` (`AppShell.vue:183-189`), which is the real scroll owner.
- **Expected** - `docs/UI/06-agents.md:61` ("**Component**: `STable` with `stickyHeader`");
  internal consistency with the prop's own name and default (`STable.vue:49,60`).

### F-9 (major) - `SDropdown` menus overflow the viewport with no flip and no height cap

- **Observed** - `SDropdown.vue:112-125` is the only positioning code and sets
  `position: 'fixed'`, `top: rect.bottom + 4` and one horizontal edge. There is no
  viewport-height read, no upward flip and no clamp. `.s-dropdown__menu` (`:243-250`) has no
  `max-height` and no `overflow`; items are 36px tall (`:263`). The menu is teleported to
  `body` (`:186-193`), which does not scroll inside the shell, and
  `onScrollWhileOpen` (`:127-129`, registered on `window` with `capture: true` at `:148`)
  repositions the menu rather than closing it. `updateMenuPosition` also runs at `:146`
  *before* the `await nextTick()` at `:150`, so today the menu is positioned before it exists
  in the DOM and can never be measured.
- **Expected** - `docs/UI/11-responsive-a11y.md:272` (Arrow Up/Down navigate, Enter selects)
  presumes every item is reachable, and `:180` sets a 40px minimum for dropdown items as a
  usable target.

### F-22 (major) - `SAlert` hardcodes `role="alert"`

- **Observed** - `SAlert.vue:67` sets `role="alert"` unconditionally on the root. The only
  variant-driven logic in the component is `iconComponent` (`:51-59`) and the CSS classes
  (`:133-155`). `role="alert"` implies `aria-live="assertive"`, so every static informational
  panel pre-empts the page heading on mount. Static `variant="info"` sites include
  `frontend/src/slices/keys/components/KeyUploadForm.vue:140`,
  `frontend/src/slices/agents/views/McpEgressAllowlistView.vue:177-178`,
  `frontend/src/slices/tenancy/views/OrgTransferView.vue:223,309,372`,
  `frontend/src/slices/conversation/views/ChatroomSettingsView.vue:574`,
  `frontend/src/slices/workflow/components/config/WaitForEventConfigForm.vue:172-173` and
  `frontend/src/slices/skills/components/SkillFiles.vue:268-270`.
- **Expected** - `docs/UI/11-responsive-a11y.md:293` verbatim: "SAlert | `role="alert"` for
  danger/warning, `role="status"` for info/success".

### F-30 (minor) - `SEmptyState` has no vertical centring

- **Observed** - `SEmptyState.vue:46-55` is
  `display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 2rem 1rem;
  max-width: 400px; margin: 0 auto` with no `justify-content`, so a stretched instance packs
  its children to the start. Two consumers stretch it:
  `frontend/src/slices/agents/views/GraphragGraphView.vue:215-221` and `:223-229`, both
  `class="flex-1"` inside the `flex flex-col h-[calc(100vh-3.5rem)]` root at `:168`. A
  repository-wide grep for `<SEmptyState` followed by a stretching class
  (`flex-1|h-full|grow|self-stretch`) returns exactly those two sites.
- **Expected** - `docs/UI/07-conversation.md:1018` ("Vertically and horizontally centered in
  the message feed area") is explicit for the chatroom; `docs/UI/12-shared-patterns.md:390`
  says only "contextual empty state" and does not specify centring, so the intent is strong
  for one case and weak elsewhere. See Q-8.

### F-33 (minor, `plausible`) - `ProjectKeysView`'s available-keys table has no loading state

- **Observed** - `frontend/src/slices/keys/views/ProjectKeysView.vue:221-225` renders the
  available-keys `STable` with no `:loading` binding, while the sibling carried table at
  `:136-141` passes `:loading="loading"`. `carriable` (`:48-50`) derives from `myKeys`, which
  is `useMyKeys()`'s `keys` computed and is `[]` until the query resolves
  (`frontend/src/slices/keys/composables/useMyKeys.ts:15`). `useMyKeys`'s own `loading`
  (`:10`, `:42`) is never destructured at `ProjectKeysView.vue:38`, so the correct flag is not
  in scope in the view. The `loading` at `:39` belongs to `useProjectKeys`, the carried query.
- **Expected** - `docs/UI/12-shared-patterns.md:171-173` (§2.4: a loading `STable` shows five
  skeleton rows) and `:390` (§6.1: the empty state is for a settled empty result).

### F-34 (minor) - `SkillWorkbench` passes a prop `SEmptyState` does not declare

- **Observed** - `SEmptyState.vue:4-8` declares exactly `title`, `text` and `icon`, and
  renders the body from `text` at `:29-34`.
  `frontend/src/slices/skills/components/SkillWorkbench.vue:170-174` passes `:description`,
  which with default `inheritAttrs` falls through onto the root `div` at
  `SEmptyState.vue:12` as a stray DOM attribute. The same call passes no `:icon`, so the halo
  (`:13-22`) is skipped as well. The strings exist and are translated
  (`frontend/src/slices/skills/locales/en.json:115`, `zh-TW.json:115`).
- **Expected** - `docs/UI/12-shared-patterns.md:169` ("Uses SEmptyState with icon, title,
  description, and action button").

### F-41 (minor, `plausible`) - `SModal` caps only its body height

- **Observed** - `SModal.vue:128-135` is `position: fixed; inset: 0; display: flex;
  align-items: center; justify-content: center` with no `overflow` and no padding. Only
  `.s-modal__body` is capped, at `max-height: 70vh` (`:218-222`); the header (`:174-179`) and
  footer (`:224-230`) are unbounded. No consumer overrides `.s-modal__panel`. A centred flex
  item taller than its container overflows equally in both directions, and the part above
  y = 0 is unreachable because neither `.s-modal` nor `body` scrolls. The mobile full-screen
  branch (`:260-295`) only applies below 768px, so a wide-and-short viewport does not reach it.
- **Expected** - `docs/UI/11-responsive-a11y.md:398` (SModal is "Centered" from `md` up, which
  presumes it is fully visible) and `:385` ("Verify at 200% zoom (no content overflow or
  overlap)"). The clipped element is the `aria-labelledby` target (`SModal.vue:87-93`).

### F-50 (minor) - `STooltip` hardcodes `z-index: 50`

- **Observed** - `STooltip.vue:71-73` sets `position: absolute; z-index: 50` against the token
  `--z-tooltip: 600` (`main.css:87`). A repository-wide grep for numeric `z-index` values
  returns seven sites; inside `shared/ui/` only this one and `STable.vue:491` (a value local
  to the table wrapper's own stacking context), so this is the sole app-level scale violation
  in the shared overlay set besides F-5's banner.
- **Expected** - `docs/UI/01-design-system.md:53-59` z-index scale.
- **Bounded by the audit's §4** - the stronger claim that a first-row table tooltip is clipped
  to nothing was refuted: a first-row trigger has 8px of cell padding plus the whole `<thead>`
  above it inside the same wrapper (about 39 to 41px) against a tooltip needing about 31px.
  This dossier fixes the token only, and does not add teleporting or repositioning to
  `STooltip`.

### F-43 (minor) - the mobile bulk-action bottom sheet does not exist

- **Observed** - `STable.vue:219-228` renders `.s-table-bulk` on
  `selectable && selected.length > 0` with no breakpoint guard, positioned before both the
  `<table>` and the `STableCards` branch, and styled at `:470-479` with top-rounded corners and
  `border-bottom: none`. `isMobile` is consumed at `:74,80` only for the card-list switch. A
  case-insensitive grep for `bottom.?sheet|BottomSheet|s-sheet` across `frontend/src` returns
  zero hits.
- **Expected** - `docs/UI/12-shared-patterns.md:194` ("On mobile (< md): bulk actions render as
  a bottom sheet") and `docs/UI/11-responsive-a11y.md:403` (the "STable bulk actions" row:
  bottom sheet at `xs`/`sm`, inline toolbar from `md`).
- **Correction to the audit** - the audit's blast radius, "every selectable table on mobile",
  overstates it. A grep for `selectable`, `:selected=` and `bulk-actions` across
  `frontend/src/slices` returns no `STable` consumer passing any of them: the only hits are
  unrelated prose comments and `WorkflowEditorView.vue:233`'s `:elements-selectable`. No table
  in the product is selectable today, so the bulk bar never renders anywhere and the failure
  scenario the audit describes (`/keys` at 375x812 with five keys selected) is not reachable.
  This is the basis for Q-12.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Does this dossier depend on another? | Yes: `depends_on: [2026-08-19-transient-feedback-channels]`, an **overlap prerequisite**, not a logical one. | Both dossiers edit `frontend/src/app/App.vue`'s template: the feedback dossier changes `<Toaster>` (`App.vue:49-52`) and `SNetworkBanner` (`:27`), this one restructures `:28-46` for F-5 and F-6. Building concurrently produces conflicting diffs in one 30-line template. There is also a semantic tie: that dossier's Q-6 lowers the toaster from sonner's hardcoded `999999999` onto `--z-toast: 500`, which only holds if F-5 stops the impersonation banner sitting at 9999 above it. Either could technically go first; serial ordering avoids both problems. |
| Q-2 | F-5: reserve vertical space in the shell grid, or lower the banner into the token scale and offset the shell? | **Neither literally: take the banner out of `position: fixed` entirely.** `App.vue` wraps the banner and the layout in a flex column (`display: flex; flex-direction: column; min-height: 100vh`); the banner becomes `position: sticky; top: 0; z-index: var(--z-banner)`; `.app-shell` drops `height: 100vh` for `flex: 1; min-height: 0`. | A sticky, in-flow banner reserves its own space by construction, so no height constant is written down anywhere and no offset can drift when the copy wraps to two lines on a narrow viewport. Moving the banner into the shell grid was rejected because impersonation must stay visible on every layout, and `App.vue:19-22` can still select `PublicLayout` (route `/`) or `AuthLayout` (the 404, until Q-4 lands) for an impersonating admin; a shell-only banner would silently disappear there. `--z-banner` (350) is the documented "above chrome, below modals" slot (`main.css:82-84`) and is only load-bearing on the two document-scrolling layouts, where sticky actually pins. Consistency with Q-6 of the sibling dossier is stronger than required: at 350 the banner is below `--z-modal` (400), `--z-toast` (500) and `--z-tooltip` (600), so nothing it does can obscure a toast at 500. Cost: `.app-shell`'s height moves from the component to the new wrapper. That is also a benefit, since F-45's `vh` to `dvh` change (out of scope, owned elsewhere) becomes a one-line edit in one place. |
| Q-3 | F-6: where does the boundary move to? | **Inside the layout, wrapping only `<router-view>`**: `<component :is="layoutComponent"><ErrorBoundary><router-view .../></ErrorBoundary></component>`. | This is what `ErrorBoundary.vue:6-7` says the component is for. Consequence accepted and recorded: a throw inside `AppShell`, `AppTopBar` or `AppSidebar` is no longer caught by this boundary and falls through to `app.config.errorHandler`. That is the right trade, because a fallback rendered *inside* a shell that is itself broken is not a fallback; the shell is also a small, stable surface with no data fetching in its own render path (`AppShell.vue:1-82`). The `AuthError` re-throw at `:17-20` and the `fullPath` reset at `:35-40` are unchanged. |
| Q-4 | F-7: how does the 404 pick a layout without breaking guards? | **Add `meta: { layout: 'auto' }` to the catch-all record and handle `'auto'` in `App.vue`'s `layoutComponent` by reading `useSessionStore().isAuthenticated`.** | `meta: { layout: 'app' }` would force `AppShell` for anonymous visitors, and `meta: { requiresAuth: true }` would make `runGuards` redirect a mistyped URL to `/login` instead of showing a 404, which is worse than the present defect. An explicit third mode changes nothing for any other route and states the doc's rule (`02-layout-shell.md:351`) in code. `App.vue` lives in `app/`, which may import from slices, and `NotFound.vue:6` already imports the same store through `@shared/stores/session`; use that path for consistency. Out of scope and left alone: `NotFound.vue:34-40`'s `min-height: 60vh` box (audit F-28). |
| Q-5 | F-8: bounded wrapper height, `overflow-x` on an inner element, or stick against `main`? | **Stick against `main`: when `stickyHeader` is set, `.s-table-wrap` becomes `overflow: visible`, so the nearest scrollport for the `<thead>` is `main.app-shell__content`.** | Moving `overflow-x` to an inner element does not work: any ancestor of the `<table>` with a non-`visible` overflow becomes the scrollport, so the header would still be pinned to a container that never scrolls vertically. A bounded wrapper height does work but requires inventing a height the component cannot know, and it creates a nested vertical scroll region inside the shell's content area, which contradicts `docs/UI/02-layout-shell.md` §3.3 (the content area is the scroll owner) and is the exact shape `2026-08-09-chatroom-rail-scroll-and-resize` §5 refused ("adding a second scroll region would nest scrollbars"). **Cost, stated plainly:** a `stickyHeader` table wider than the content area now scrolls the content area horizontally instead of scrolling inside its own box, so the page header moves sideways with it. This affects only the two consumers that opt in; the other 20-plus `STable` call sites keep `overflow-x: auto` unchanged. Both opt-in consumers use `responsive-mode="card-list"`, so no table renders at all below 768px (`STable.vue:79-81`, `useBreakpoint.ts:51`). If a wide sticky table appears later, the bounded-height variant can be added behind an explicit `maxHeight` prop; recorded as FU-1. |
| Q-6 | F-9: flip, `max-height` plus internal scroll, or both? | **Both.** Measure the menu after `nextTick`, flip upward when it would overflow the bottom and there is more room above, and cap `max-height` to the available space on the chosen side with `overflow-y: auto`. Keep `onScrollWhileOpen` repositioning rather than closing, and re-run the flip and cap on every reposition. | Flip alone fails for a menu taller than both the space above and the space below, which a long row-action menu on a 768px-tall laptop reaches. A cap alone leaves a menu opened near the bottom showing one or two items with a scrollbar when a flip would have shown all of them. Order matters: pick the side first, then cap to that side. The measurement requires reordering `SDropdown.vue:146,150` so `updateMenuPosition` runs after `await nextTick()`; the enter transition starts at `opacity: 0` (`:309-313`), so the pre-measurement frame is not visible. Closing on scroll was considered and rejected: it would change documented behaviour for the shell's own menus (`UserMenu.vue:148`, `ChatroomHeader.vue:95`) where the menu is expected to track its trigger. |
| Q-7 | F-22: does `SAlert` need an escape hatch prop for an info alert that really is event-driven? | **No.** Map the role from the variant: `danger`/`warning` to `role="alert"`, `info`/`success` to `role="status"`. No override prop. | `docs/UI/11-responsive-a11y.md:293` states the mapping unconditionally. `role="status"` still announces, politely, so an event-driven info alert is not silent; it merely stops pre-empting the page heading. Adding an override would invite exactly the assertive-by-default usage the finding is about. The `focusOnMount` path (`SAlert.vue:39-49`), which exists for transient submit errors, is unaffected: those are `variant="danger"` and keep `role="alert"`. |
| Q-8 | F-30: is vertical centring the default or an opt-in prop? | **Default.** Add `justify-content: center` to `.s-empty-state`. No new prop. | The regression risk the audit warns about does not exist here, and that is checkable rather than assumed: `justify-content` on a column flex container is a no-op when the container's height is its content height, so the declaration can only change rendering where the component is stretched. A repository-wide grep for `<SEmptyState` carrying `flex-1`, `h-full`, `grow` or `self-stretch` returns exactly the two `GraphragGraphView.vue:215-229` instances, which are the defect. An opt-in prop would therefore add API surface that every future stretched consumer has to remember. **Cross-reference corrected 2026-08-21**: F-23 belongs to `2026-08-19-chatroom-scroll-and-composer` (not `-and-resize`, which is a different, older dossier), and **it has already landed**. Its Q-5 decided not to wait on this dossier, on the ground that a stretched-but-not-self-centring `SEmptyState` inside an auto-height `<li>` would still sit at the top, so the height had to come from the chatroom side regardless. It therefore added `.chatroom__empty { flex: 1; display: flex; align-items: center; justify-content: center }` in `ChatroomView.vue`. That is a wrapper that stretches and centres its child, not a second centring mechanism inside `SEmptyState`, and the two compose: a self-centring `SEmptyState` inside a centring flex wrapper still centres. So this fix is safe to land as designed, F-23 needs nothing further, and the earlier instruction here ("it must not add a second centring mechanism") is moot. |
| Q-9 | F-33: which loading flag does the available-keys table bind? | **`useMyKeys()`'s own `loading`, destructured in the view under a distinct name.** `ProjectKeysView.vue:38` becomes `const { keys: myKeys, loading: myKeysLoading } = useMyKeys()`, bound at `:221-225` and used to gate the tab badge at `:54`. | The obvious `:loading="loading"` is wrong: `loading` at `:39` is `useProjectKeys`'s flag for the *carried* query and would tie the available table to an unrelated request. The audit called this out and it is the whole content of the fix. Gating the badge matters more than the table: `STabs.vue:150` only renders the active panel, so on first load the available panel is not mounted and the badge reading "0" is the always-visible half of the symptom. |
| Q-10 | F-34: fix the call site, or teach `SEmptyState` to accept `description`? | **Fix the call site.** `SkillWorkbench.vue:173` becomes `:text=`, and the call gains an `:icon` so the halo renders like every other empty state. Do not add a `description` alias. | `text` is the declared API and 40-plus other call sites already use it; adding an alias would make two names correct and leave the next author guessing. The reason this passed review is worth recording rather than patching: Vue's default `inheritAttrs` turns an unknown prop into a DOM attribute silently, and `vue-tsc` does not reject it because no `vueCompilerOptions.strictTemplates` is configured in any `frontend/tsconfig*.json` (grep returns no match). That is a tooling gap, not a component gap; recorded as FU-2. |
| Q-11 | F-41 is `plausible` with a narrow trigger window. Is it in scope? | **Yes, in scope.** | The trigger is narrow (viewport height under about 417px *and* width at or above 768px, so landscape phones and roughly 250 to 300% zoom on wide displays), but the fix is three declarations in the file this dossier is already opening, the clipped element is the dialog's accessible name, and `docs/UI/11-responsive-a11y.md:385` makes 200%-zoom overflow an explicit manual-check item. The fix is `.s-modal { overflow-y: auto; padding: 24px; align-items: flex-start }` with `.s-modal__panel { margin: auto }`: `margin: auto` centres a flex item in a scroll container without the top-clipping that `align-items: center` produces there. `.s-modal__panel--full`'s `calc(100vw - 48px)` (`:170-172`) stays correct, since the new padding is the same 24px per side. The backdrop is separately `position: fixed; inset: 0` (`:137-141`) and is unaffected by the scroll. |
| Q-12 | F-43: build the mobile bulk-action bottom sheet here, or route it out? | **Route it out as a feature dossier. It is not fixed in this dossier.** The audit's hand-off table should cite this decision for F-43 rather than leaving it open. | Three reasons. (1) It is a capability that was never built, not behaviour that regressed: `docs/UI/12-shared-patterns.md:194` and `docs/UI/11-responsive-a11y.md:403` describe a bottom sheet, and a case-insensitive grep for `bottom.?sheet|BottomSheet|s-sheet` across `frontend/src` returns zero hits, so there is no primitive to fix. (2) It cannot manifest today: no `STable` consumer in `frontend/src/slices` passes `selectable`, `:selected` or a `bulk-actions` slot, so `STable.vue:219-228` never renders. (3) Building it means a new shared overlay primitive with a focus trap, a backdrop, an ARIA contract and safe-area insets, which the app does not handle at all (audit F-25: `frontend/index.html:5` has no `viewport-fit=cover` and `env(safe-area-inset-*)` appears nowhere). That is feature work with its own acceptance criteria, and it should land together with the first view that actually needs multi-select. |
| Q-13 | F-50: also fix the secondary `--z-dropdown` (300) below `--z-modal` (400) issue the audit records? | **No.** Change `STooltip.vue:73` to `var(--z-tooltip)` and stop there. | The dropdown-inside-modal case is latent: both are teleported to `body`, and no such nesting exists in the tree today (`SDropdown` call sites are 16 list-view and chrome locations, none inside an `SModal`). Fixing it properly means either raising `--z-dropdown` above `--z-modal`, which breaks the scale's meaning for every other consumer, or scoping the dropdown's z-index to its host, which is a design decision that needs a real consumer to reason about. Recorded as FU-3. |

## 4. Reproduction

**F-5**: log in as an admin, start an impersonation session (`/admin/impersonate`), then open
any authenticated route at 1280x800. Observe the amber banner painted across the top 33px of
the 56px top bar; the top 25px of the 40px sidebar toggle is not clickable and the wordmark is
almost entirely covered. Open any modal and observe the banner painting over its top edge.

**F-6**: force a throw during render in any view (for example, dereference a null in a
computed used by the template). Observe the sidebar, top bar and content background all
disappear, leaving a centred text block roughly 130px tall starting 64px from the top of the
page. Only the retry button remains; there is no navigation. The reproducible production path
is the F-21 defect owned by `2026-08-19-transient-feedback-channels`: submit the new-workflow
form at `/workspaces/:wid/workflows` as a member without create rights.

**F-7**: log in, then open `/orgs/does-not-exist-typo` at 1440x900. Observe the sidebar, top
bar, org/project switcher and notification bell all vanish and the page render as a centred
column on the auth background.

**F-8**: open `/projects/:pid/agents` at 1440x900 with enough agents to overflow the content
area. Scroll `main` down. Observe the column headers scroll off the top and never pin; the
sortable headers are unreachable without scrolling back up. Same at
`/workspaces/:wid/chatrooms`.

**F-9**: open `/keys` at 1366x768 with enough rows to fill the page, scroll `main` to the
bottom, and open the row-action dropdown on the last row. Observe the menu render downward past
the viewport bottom; the last items are not reachable, because the content region is already at
its scroll end.

**F-22**: open `/orgs/:id/transfer` with a screen reader. Observe three informational alerts
announced assertively on mount, pre-empting the page heading and form labels.

**F-30**: open `/projects/:pid/graphrag-configs/:cid/graph` for a config whose graph has zero
nodes. Observe the halo and "No entities yet" pinned immediately under the summary line with
roughly 600px of empty space beneath.

**F-33**: open `/projects/:projectId/keys` on a cold cache. Observe the "Available" tab badge
read 0 while the key list is in flight; switch to that tab mid-flight and observe the settled
empty state with an Upload call to action, then the table silently populating.

**F-34**: open `/projects/:projectId/skills` with skills present and none selected. Observe the
right-hand pane show only "No skill selected": no icon halo, and the guidance line "Pick a
skill from the list, or create one." is absent.

**F-41**: open any modal with a footer and enough body content to hit the 70vh cap, at a
viewport of 844x390 (landscape phone) so the width stays at or above 768px. Observe the panel
exceed the viewport height and the title clipped above y = 0, with no way to scroll to it.

**F-50**: not reproducible as a user-visible symptom today. `main.app-shell__content` clips the
tooltip long before it could reach the top bar (200) or the network banner (350), and nothing
else with a z-index between 200 and 600 shares its stacking context. This is a token-consistency
fix; the AC is a code assertion, not a behavioural one.

## 5. Root Cause Analysis

**F-5 root cause**: `ImpersonationBanner.vue:19-23` chose `position: fixed` with a
hand-written `z-index: 9999` instead of reserving layout space. The chain is: fixed positioning
removes the banner from flow, so nothing downstream can account for its height; the shell is
`height: 100vh` with `overflow: hidden` (`AppShell.vue:141-142`), so the top bar cannot be
scrolled clear; and 9999 places the banner above every layer in the scale, including modals and
toasts. The earliest link whose correction prevents all three symptoms is the positioning
choice, which is why Q-2 changes that rather than adding an offset. Aggravating, not causal:
the integration diagram in `docs/UI/02-layout-shell.md:32` sanctions "fixed top" without
reconciling it with §4.3's top bar, so the code matched a document that contradicts itself.

**F-6 root cause**: `App.vue:29` places the boundary one level too high. Everything else about
`ErrorBoundary.vue` is correct, including the propagation contract (`:17-30`) and the
navigation reset (`:35-40`); only the slot's contents are wrong. Aggravating: the fallback
block carries no min-height (`:68-73`), which is what turns "the shell is gone" into "the page
looks empty", but that is cosmetic on top of the structural fault.

**F-7 root cause**: `router.ts:43-47` omits `meta`. `App.vue:22`'s fallback is a reasonable
default for a route that declares nothing, and the guard chain is correct; the record simply
does not say which of the two documented layouts it wants. The earliest link is the missing
meta, which is why Q-4 adds an explicit mode rather than changing the fallback for every route.

**F-8 root cause**: `.s-table-wrap`'s `overflow-x: auto` (`STable.vue:467`) makes the wrapper
the sticky scrollport. This is a CSS Overflow 3 §3.1 consequence that is invisible in the
source: only one axis is written down, and the other is computed. The sticky rule at `:488-492`
is correct in isolation and would work against any scrolling ancestor; it is the wrapper that
intercepts it. Aggravating: the prop's presence, its default (`:49,60`) and its use at two call
sites all read as working configuration, so nothing in the codebase signals the dead path.

**F-9 root cause**: `updateMenuPosition` (`SDropdown.vue:112-125`) computes a position from the
trigger's rect alone and never reads the viewport height or the menu's own size. A second,
independent fault compounds it: the function is called at `:146`, before `await nextTick()` at
`:150`, so even a size-aware implementation would have nothing to measure. Both must be
corrected together, which is why Q-6 reorders the open path.

**F-22 root cause**: `SAlert.vue:67` hardcodes the role that the danger case needs onto all four
variants. Every other variant-dependent aspect of the component (icon, colours) is branched;
the role was not.

**F-30 root cause**: `.s-empty-state` (`SEmptyState.vue:46-55`) specifies cross-axis centring
(`align-items: center`) and horizontal block centring (`margin: 0 auto`) but not main-axis
distribution, so a stretched instance has no rule telling it where its content goes. The same
root cause produces audit F-23 in the chatroom, which a different dossier owns.

**F-33 root cause**: `ProjectKeysView.vue:38` destructures only `keys` from `useMyKeys()`, so
the loading flag the available table needs is not in the view's scope, and `:221-225` was
written without it. The badge at `:54` reads the same unloaded array.

**F-34 root cause**: a prop-name mismatch at one call site (`SkillWorkbench.vue:173`), made
silent by Vue's attribute fallthrough and by the absence of `strictTemplates` in the
TypeScript configuration.

**F-41 root cause**: `.s-modal` (`SModal.vue:128-135`) centres a flex item in a container that
neither scrolls nor pads, while only one of the panel's three children is height-capped
(`:218-222`). The panel can therefore exceed the viewport, and centred overflow is symmetric,
so half of the excess goes somewhere unreachable.

**F-50 root cause**: a literal written where a token exists (`STooltip.vue:73`). No mechanism,
just drift.

**F-43 root cause**: the bottom sheet was specified (`12-shared-patterns.md:194`) and never
implemented, and the desktop bulk bar was written without the breakpoint guard the spec implies
(`STable.vue:219-228`). Not fixed here; see Q-12.

## 6. Blast Radius and Sibling Suspects

**Blast radius**

- **F-5** - the entire impersonation session on every authenticated route, which is exactly the
  flow the banner exists to support. No data impact.
- **F-6** - every uncaught render error and every unhandled rejection from a native event
  handler, which routes through the same boundary (`ErrorBoundary.vue:17-30`). This is the
  amplifier that makes the sibling dossier's F-21 severe rather than cosmetic.
- **F-7** - every 404 reached by an authenticated user: mistyped deep links, stale bookmarks,
  and any link to a resource route whose path shape changed.
- **F-8** - both `sticky-header` consumers today (`AgentListView.vue:324`,
  `ChatroomListView.vue:274`) and any future one, since the prop looks wired.
- **F-9** - all 16 `SDropdown` call sites, worst on short viewports and long lists. Enumerated
  from a repository-wide grep for `<SDropdown`: `AgentListView.vue:378`,
  `KeyListView.vue:206`, `ChatroomListView.vue:307`, `KeyGroupListView.vue:169`,
  `SearchKeyView.vue:243`, `OrgMembersView.vue:227`, `ProjectMembersView.vue:241`,
  `ActivityTypesView.vue:244`, `RagConfigListView.vue:371`,
  `KnowledgeMapConfigListView.vue:291`, `GraphragConfigListView.vue:451`,
  `AgentToolsView.vue:897,997`, `WorkspaceListView.vue:224`, `ChatroomHeader.vue:95`,
  `UserMenu.vue:148`. The last two are app chrome, so the defect reaches the top bar's own
  menu.
- **F-22** - every static `variant="info"` and `variant="success"` alert in the product, for
  screen-reader users.
- **F-30** - any consumer that stretches the component; two today.
- **F-33** - one tab of one view.
- **F-34** - the skills workbench empty state in all three of its mounts (project, org, admin).
- **F-41** - landscape phones and tablets at or above 768px wide, and roughly 250 to 300% zoom
  on wide short displays; every `SModal` consumer, since the fault is in the base class.
- **F-50** - latent. It becomes live the moment a tooltip is placed in a container that does
  not clip it.

No data was persisted incorrectly by any of the twelve, so there is no repair plan.

**Sibling suspects**

- **Other components whose sticky child is trapped by an ancestor overflow (the F-8 pattern):
  cleared.** `position: sticky` appears in exactly two scoped stylesheets across `frontend/src`
  (`AppTopBar.vue:71`, `STable.vue:489`). The top bar is a grid row of `.app-shell`, which is
  `overflow: hidden` and therefore not a scrollport at all, so its sticky is inert but also
  harmless (the row never scrolls). `overflow-x: auto` appears twice in `shared/ui`
  (`STabs.vue:168`, `STable.vue:467`); the `STabs` tab strip has no sticky descendant. Tailwind
  `sticky` utilities are not covered by that grep; the one production use is
  `AgentDetailView.vue:964` (`lg:sticky lg:top-6`), whose scrollport is `main` and which works.
  Its 48px arithmetic error is audit F-51 and is out of scope here.
- **Other viewport-positioned popovers that would need the same flip and cap (the F-9 pattern):
  cleared.** `SDropdown.vue:114` is the only `getBoundingClientRect`-driven positioner in
  `shared/ui`. The other two `Teleport to="body"` consumers are `SModal.vue:62` and
  `SDrawer.vue:48`, both `position: fixed; inset: 0`, so they have no anchor to overflow from.
  `STooltip` is `position: absolute` relative to its own trigger (`:66-73`) and does not
  compute coordinates.
- **Other app-level z-index literals competing with the token scale (the F-5 and F-50
  pattern): swept, two confirmed and four cleared.** A repository-wide grep for numeric
  `z-index` returns seven sites. Confirmed and fixed here: `ImpersonationBanner.vue:23` (9999)
  and `STooltip.vue:73` (50). Cleared as legitimate local stacking within a single component's
  own context: `STable.vue:491` (10, the thead against its own rows),
  `ChatroomComposer.vue:303` (20, the mention popover inside the composer),
  `Landing.vue:386,459` (0 and 1, hero layering) and `LandingIntro.vue:326` (1000, inside the
  full-screen intro overlay on the public landing page, which shares no stacking context with
  app chrome).
- **Other hardcoded ARIA roles in `shared/ui` that should be variant-driven (the F-22
  pattern): cleared.** The other `role="alert"` is `SFormField.vue:63`, on the field-error
  message, where assertive is correct. The `role="status"` uses (`SCharCount.vue:37`,
  `SLoadingSpinner.vue:20`, `SSkeleton.vue:37,64`, `STable.vue:214`) are all polite regions and
  match `docs/UI/11-responsive-a11y.md:303-305`.
- **Other components passed a prop they do not declare (the F-34 pattern): to be re-swept at
  build time.** The audit's grep for `:description` on a Vue component returned this single
  hit. That grep only covers one prop name, so it is not a general clearance; the honest
  statement is that no systematic check exists (FU-2), and the build should re-run the audit's
  grep against the current tree before relying on the single-hit result.
- **Other tables missing a `:loading` binding (the F-33 pattern): to be enumerated during the
  build.** Nineteen `<STable` call sites exist across the slices; the audit checked only
  `ProjectKeysView`. The sweep is cheap and belongs in the build, not in a second dossier.

## 7. Fix Design

Ordered so that the two `App.vue` changes land together.

1. **F-5 and F-6 (`frontend/src/app/App.vue`)** - wrap `<ImpersonationBanner />` and
   `<component :is="layoutComponent">` in a flex-column root (`display: flex;
   flex-direction: column; min-height: 100vh`) in a new scoped `<style>` block, and move
   `<ErrorBoundary>` inside the layout so it wraps only `<router-view>`. `<SNetworkBanner />`
   (`:27`), `<Toaster>` (`:49-52`), `<SConfirmDialog />` and `<SIdleDialog />` stay outside
   the wrapper: all four are fixed or teleported and must not be constrained by it. Use
   `100vh`, not `100dvh`, so this change is behaviour-neutral with respect to audit F-45,
   which a different dossier owns.
2. **F-5 (`ImpersonationBanner.vue:18-33`)** - `position: fixed` becomes
   `position: sticky; top: 0`, and `z-index: 9999` becomes `z-index: var(--z-banner)`. The
   `left`/`right` declarations are dropped: a sticky block-level element is already full width.
3. **F-5 (`AppShell.vue:141`)** - `height: 100vh` becomes `flex: 1; min-height: 0`.
   `overflow: hidden` (`:142`) is unchanged, so the content area remains the only scroller.
4. **F-7 (`router.ts:43-47` and `App.vue:17-23`)** - add `meta: { layout: 'auto' }` to the
   catch-all record, and add an `if (layout === 'auto') return session.isAuthenticated ?
   AppShell : AuthLayout` branch to `layoutComponent`, importing `useSessionStore` from
   `@shared/stores/session` as `NotFound.vue:6` does.
5. **F-8 (`STable.vue:465-468`)** - add a `.s-table-wrap--sticky { overflow: visible }`
   modifier applied when `stickyHeader` is true, and document on the prop (`:49`) that
   `stickyHeader` and in-table horizontal scrolling are mutually exclusive, with the reason.
6. **F-9 (`SDropdown.vue:112-125,143-162,243-250`)** - move `updateMenuPosition` after
   `await nextTick()`; in it, read `menuRef`'s height and `window.innerHeight`, choose the side
   with more room when the menu does not fit below, set either `top` or `bottom` accordingly,
   and set `maxHeight` to the chosen side's space minus an 8px margin. Add
   `overflow-y: auto` to `.s-dropdown__menu`. `onScrollWhileOpen` keeps calling the same
   function, so a reposition re-evaluates the flip.
7. **F-22 (`SAlert.vue:67`)** - replace the literal with a computed that returns `'alert'` for
   `danger`/`warning` and `'status'` for `info`/`success`, alongside the existing
   `iconComponent` computed (`:51-59`).
8. **F-30 (`SEmptyState.vue:46-55`)** - add `justify-content: center`.
9. **F-33 (`ProjectKeysView.vue:38,54,221-225`)** - destructure `loading: myKeysLoading` from
   `useMyKeys()`, bind it on the available-keys table, and suppress the numeric tab badge while
   it is true.
10. **F-34 (`SkillWorkbench.vue:170-174`)** - `:description` becomes `:text`, and an `:icon` is
    added so the empty state matches every other one.
11. **F-41 (`SModal.vue:128-135,143-152`)** - `.s-modal` gains `overflow-y: auto` and
    `padding: 24px` and changes `align-items: center` to `align-items: flex-start`;
    `.s-modal__panel` gains `margin: auto`. The mobile branch at `:260-295` must zero the new
    padding so full-screen modals still reach the edges.
12. **F-50 (`STooltip.vue:73`)** - `z-index: 50` becomes `z-index: var(--z-tooltip)`.

None of these masks a symptom: each replaces the declaration or the placement that produces it.
The two that could be mistaken for masking are F-8 and F-41, and in both cases the change is to
the property that creates the containing block or the scrollport, not to a compensating offset.

## 8. Regression Test Plan

Written first, failing against current code. This section is load-bearing because most of these
findings are layout outcomes and this repository's unit tier has no layout engine.

**What jsdom can and cannot do here.** jsdom parses CSS but performs no layout: it computes no
box geometry, evaluates no media or container queries, and returns zeros from
`getBoundingClientRect`. It also does not implement `position: sticky`, `overflow` scrolling, or
viewport-relative units. So no Vitest test in this repository can assert that a sticky header
pins, that a dropdown flipped, that a modal title is on-screen, or that an empty state is
vertically centred. `2026-08-09-chatroom-rail-scroll-and-resize` hit exactly this wall and
handled it by splitting each layout AC into a *structural* half that a test pins (which element
owns the overflow, which class is applied) and a *visual* half verified by hand, stating the
boundary in its §12 ("no automated test in this repository proves the reported symptom is gone")
and recording the unverified ACs in its §11 preamble and D-5. The same split is used below.
There is no axe-core harness in `frontend/` either (a grep for `axe` across `frontend/src`,
`frontend/tests` and `frontend/e2e` returns no test usage), so `docs/UI/11-responsive-a11y.md`
§7.1's automated a11y tier does not exist to lean on.

**Unit and component tests (all must fail before the fix)**

- **T-1 (F-5, F-6, F-7)** new `frontend/src/app/__tests__/App.test.ts`. Three assertions:
  (a) the `ImpersonationBanner` element is a previous sibling of the layout inside the flex
  wrapper and its computed `position` in the scoped stylesheet is not `fixed`; (b) mounting with
  a throwing stub view leaves `.app-shell` in the DOM alongside the boundary fallback, and
  `AppTopBar` is still rendered; (c) at `/does-not-exist` with an authenticated session store,
  the rendered layout is `AppShell`, and with an anonymous one it is `AuthLayout`. Fails today
  on all three: (a) the banner is `fixed`, (b) the fallback replaces the layout entirely
  (`App.vue:29-46`), (c) `App.vue:22` selects `AuthLayout` for both session states. Follow
  `AppShell.test.ts`'s mounting shape (Pinia plus a memory router over
  `frontend/tests/utils/routes.ts`, top bar and sidebar stubbed).
- **T-2 (F-5)** new `frontend/src/slices/admin/__tests__/ImpersonationBanner.test.ts`.
  **Amended 2026-08-21**: the z-index half already passes (`:27` is
  `var(--z-banner, 350)`), so asserting it is a *guard*, not a regression test - keep it, but
  it does not fail first. The half that fails first is the positioning: assert the scoped rule
  declares `position: sticky` and not `position: fixed`. Fails today against `:19`.
- **T-3 (F-8)** new `frontend/src/shared/ui/__tests__/STable.test.ts`: with `sticky-header`, the
  wrapper carries the `s-table-wrap--sticky` modifier; without it, the modifier is absent and
  the default markup is byte-identical to today's. **Structural only.** Fails today because no
  modifier exists.
- **T-4 (F-9)** new `frontend/src/shared/ui/__tests__/SDropdown.test.ts`, with `menuRef`'s
  `getBoundingClientRect` and `window.innerHeight` stubbed the way
  `useResizablePanel.test.ts:60` stubs geometry: (a) a trigger near the viewport bottom with a
  tall menu produces a `bottom` offset rather than a `top` one; (b) a menu taller than both
  sides receives a `maxHeight` no larger than the available space; (c) the menu is positioned
  after `nextTick`, asserted by checking that the style object is non-empty on the frame the
  menu first appears. Fails today: `SDropdown.vue:115-124` only ever writes `top`, never writes
  `maxHeight`, and `:146` runs before the menu exists.
- **T-5 (F-22)** new `frontend/src/shared/ui/__tests__/SAlert.test.ts`: `role` is `alert` for
  `danger` and `warning` and `status` for `info` and `success`. Fails today on the last two
  against `:67`.
- **T-6 (F-30)** new `frontend/src/shared/ui/__tests__/SEmptyState.test.ts`: the scoped root
  rule declares `justify-content: center`, and a `description` attribute passed to the component
  does not appear on the root element (a fallthrough guard for the F-34 class of mistake).
  **Structural only for the centring half.** Fails today on both.
- **T-7 (F-33)** extend `frontend/src/slices/keys/__tests__/ProjectKeysView.test.ts`: with the
  my-keys query pending, the available-keys table receives `loading` true and the "Available"
  tab badge does not read `0`. Fails today because `ProjectKeysView.vue:38` does not destructure
  that flag at all.
- **T-8 (F-34)** new `frontend/src/slices/skills/__tests__/SkillWorkbench.test.ts` (or an
  extension of `ProjectSkillsView.test.ts`, which already mounts the workbench): with skills
  present and none selected, the guidance body text from `skills.workbench.pickBody` is in the
  DOM and no `description` attribute appears on any element. Fails today on both halves.
- **T-9 (F-41)** extend T-3's sibling, a new
  `frontend/src/shared/ui/__tests__/SModal.test.ts`: the scoped `.s-modal` rule declares
  `overflow-y: auto` and `.s-modal__panel` declares `margin: auto`. **Structural only.** Fails
  today against `:128-135,143-152`.
- **T-10 (F-50)** new `frontend/src/shared/ui/__tests__/STooltip.test.ts`: the scoped
  `.s-tooltip` rule resolves its `z-index` from `var(--z-tooltip)` and carries no numeric
  literal. Fails today against `:73`.

**End-to-end (real browser, real layout)**

- **T-11 (F-5)** extend `frontend/e2e/08-admin-impersonate.spec.ts`: after starting a session,
  assert the top bar's bounding box top is at or below the banner's bounding box bottom, and
  that the sidebar toggle is clickable. The existing spec asserts only that
  `.admin-impersonate__active` is visible (`:30`), which says nothing about overlap. Fails today
  because the banner is fixed at y = 0 over a top bar that also starts at y = 0.
- **T-12 (F-8)** new assertion in an existing agents spec: at 1440x900 with a list long enough
  to scroll, after scrolling `main.app-shell__content`, the `<thead>`'s bounding box top is
  still within the content area. Fails today because the header scrolls away.
- **T-13 (F-9)** new assertion in an existing keys spec: at 1366x768, with `main` scrolled to
  its end, opening the last row's dropdown leaves every `role="menuitem"` inside the viewport.
  Fails today because the menu extends past the viewport bottom.

**Browser-verification items, not covered by any test in this repository**

These are stated as such rather than papered over with a structural assertion that would read as
coverage:

- F-8's visual outcome beyond T-12's single scroll case (behaviour of a `stickyHeader` table
  wider than the content area, which is the cost Q-5 accepts).
- F-30's vertical centring in `GraphragGraphView`'s two empty states.
- F-41 at 844x390 and at 200% zoom on a wide display, per
  `docs/UI/11-responsive-a11y.md:385`.
- F-22's assertive-versus-polite announcement, which requires NVDA or VoiceOver
  (`docs/UI/11-responsive-a11y.md:384`); the ARIA attribute is asserted by T-5, the
  announcement behaviour is not.
- F-50 has no visual outcome to verify today, by design (see §4).

## 9. Risks and Rollback

- **The `App.vue` restructure touches every route.** Moving `.app-shell`'s height onto a new
  wrapper changes how the shell is sized for all three layouts. The specific risk is that
  `AuthLayout` and `PublicLayout` (`min-height: 100dvh` at `AuthLayout.vue:27,77` and
  `PublicLayout.vue:14`) currently let the document scroll; they must keep doing so, which means
  the wrapper uses `min-height`, never `height`, and the layout child must not be given
  `min-height: 0`. The e2e landing and login specs (`frontend/e2e/01-identity-flow.spec.ts`) are
  the net.
- **Moving the error boundary narrows what it catches.** A throw inside `AppShell`,
  `AppTopBar` or `AppSidebar` now reaches `app.config.errorHandler` instead of rendering a
  fallback. Accepted in Q-3 and stated here so it is not discovered later as a regression.
- **F-8's fix trades one overflow for another.** A `stickyHeader` table wider than the content
  area will scroll the content area horizontally. Only two call sites opt in today, and both
  render as cards below 768px, but this is a real behaviour change and is the reason Q-5 records
  the alternative and FU-1 keeps it available.
- **F-41's padding change insets every centred modal by 24px.** `.s-modal__panel--full`
  (`SModal.vue:170-172`) already subtracts 48px, so it is unaffected, but the mobile
  full-screen branch must zero the padding or every modal below 768px gains a border.
- **F-22 changes announcements, not pixels.** The risk is the inverse of the defect: an
  `info` alert that genuinely needed to interrupt now announces politely. No such site was
  found, but the change is invisible in screenshots and in unit tests other than T-5.
- **The impersonation banner and `SNetworkBanner` can now coexist awkwardly.** With the banner
  in flow, the top bar starts at y = 33 during impersonation, while the sibling dossier's Q-7
  moves `SNetworkBanner` to a viewport-fixed `top: var(--topbar-height)` (56px). During an
  impersonated offline episode the network banner would overlap the top bar's lower edge. Not
  fixed here (that component belongs to the other dossier); recorded as FU-4.
- Rollback: each of the twelve is an independent revert. The only coupled pair is F-5 and F-6,
  which share the `App.vue` template edit and should be one commit.

## 10. Acceptance Criteria

- [ ] AC-1: T-1(a) and T-2 fail before the fix and pass after; the impersonation banner
      reserves its own vertical space and T-11 shows the top bar fully visible and the sidebar
      toggle clickable during an impersonation session. (**Amended 2026-08-21**: "carries no
      numeric `z-index`" was already true before this dossier starts - see §1.1 - so it is a
      guard here, not evidence of this task's work.)
- [ ] AC-2: T-1(b) fails before the fix and passes after; a render error inside a view replaces
      only that view, with the top bar, sidebar and content background still rendered and the
      user able to navigate away without using the retry button.
- [ ] AC-3: T-1(c) fails before the fix and passes after; an authenticated user opening an
      unknown URL gets `AppShell`, an anonymous one gets `AuthLayout`, and no other route's
      layout selection changes.
- [ ] AC-4: T-3 and T-12 fail before the fix and pass after; scrolling a `sticky-header` table's
      page keeps the column headers pinned within the content area. The wide-table cost from Q-5
      is confirmed by hand and recorded in the Deviation Log.
- [ ] AC-5: T-4 and T-13 fail before the fix and pass after; a dropdown opened near the viewport
      bottom flips upward or caps its height, and every `role="menuitem"` is reachable without
      scrolling a container that has no scroll left.
- [ ] AC-6: T-5 fails before the fix and passes after; `SAlert` resolves `role` from `variant`
      per `docs/UI/11-responsive-a11y.md:293`, and no `SAlert` call site needed an override prop.
- [ ] AC-7: T-6 fails before the fix and passes after; a stretched `SEmptyState` centres its
      content vertically, and the two `GraphragGraphView` empty states are confirmed centred in
      a browser.
- [ ] AC-8: T-7 fails before the fix and passes after; the available-keys table shows skeleton
      rows while `useMyKeys` is in flight and the "Available" tab badge does not assert zero
      during load.
- [ ] AC-9: T-8 fails before the fix and passes after; the skills workbench empty state renders
      its icon, title and body copy, and no stray `description` attribute reaches the DOM.
- [ ] AC-10: T-9 fails before the fix and passes after; at 844x390 the modal title is on screen
      and the panel scrolls rather than clipping. Verified by hand per §8.
- [ ] AC-11: T-10 fails before the fix and passes after; `STooltip` resolves its `z-index` from
      `--z-tooltip`, and a repository-wide grep for numeric `z-index` in `frontend/src/shared/ui`
      returns only `STable.vue:491`.
- [ ] AC-12: F-43 is not implemented, and the audit's Hand-off table records it as routed to a
      separate feature dossier with Q-12 as the cited reason.
- [ ] AC-13: the sibling sweeps in §6 that are marked "to be re-swept at build time" (unknown
      props, missing `:loading` bindings) are re-run against the tree at build time and their
      results recorded, as findings or as an explicit clearance.
- [ ] AC-14: gates green on CI: `pnpm lint` (all 12, notably #6 global CSS, #11 accessibility,
      #12 i18n), `pnpm typecheck`, `pnpm test`, `pnpm build`,
      `pnpm run check:boundaries-enforced`, `pnpm run check:bundle-size`,
      `pnpm run check:type-coverage`. Backend gates N/A: the diff is frontend-only. Per
      `feedback_remote_ci_verification`, CI is authoritative over the local Windows host.

## 11. SRS Delta

None. Every finding here restores behaviour that `docs/UI/01-design-system.md`,
`02-layout-shell.md`, `06-agents.md`, `07-conversation.md`, `11-responsive-a11y.md` or
`12-shared-patterns.md` already specifies, and no `[Rxx.yy]` entry is added or amended. The one
documentation inconsistency this dossier touches, `02-layout-shell.md:32`'s integration diagram
placing the impersonation banner "fixed top" over the same strip as §4.3's top bar, is a
doc-level contradiction that the Q-2 fix resolves in favour of §4.3; the diagram should be
updated to show the banner as an in-flow row above the shell when that fix lands. That is a
change to a UI design document, not to the SRS.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** - Q-5 gives `STable` a sticky header at the cost of in-table horizontal scrolling.
  If a wide table later needs both, the bounded-scrollport variant (an explicit `maxHeight`
  prop on `STable`, wrapper `overflow: auto`) is the answer, and it should be added deliberately
  with its own nested-scroll justification rather than by re-adding `overflow-x` to the sticky
  branch.
- **FU-2** - no `vueCompilerOptions.strictTemplates` is configured in any
  `frontend/tsconfig*.json`, which is why F-34's undeclared prop passed `pnpm typecheck`.
  Enabling it would catch this whole class at the gate, but it will surface existing violations
  across the tree and so needs its own scoped task. Route to `check-quality`.
- **FU-3** - `--z-dropdown` (300) sits below `--z-modal` (400) (`main.css:81,85`), so a
  dropdown opened inside a modal would paint under the panel. Latent: no such nesting exists
  today, and both are teleported to `body`. Q-13 declines to fix it without a real consumer to
  design against.
- **FU-4** - after F-5, the top bar starts at y = 33 during impersonation while
  `SNetworkBanner` is viewport-fixed. **No longer a prediction (2026-08-21)**: the sibling
  dossier has landed, and `App.vue:30` now reads
  `<SNetworkBanner :below-topbar="layoutComponent === AppShell" />`. That offset is computed
  from `--topbar-height` alone and knows nothing about a banner above the shell, so the overlap
  becomes real the moment F-5's fix moves the top bar down. Worth a single shared offset;
  whoever builds F-5 should at least confirm the two banners' interaction by hand.
- **FU-5** - `.s-dropdown__item` is 36px tall (`SDropdown.vue:263`) against
  `docs/UI/11-responsive-a11y.md:180`'s 40px minimum for dropdown items. Not part of F-9's
  reachability defect, so not fixed here, but it is a four-pixel change in the same rule the
  F-9 fix touches and should be picked up next time that file is opened.
- **FU-6** - `AppTopBar.vue:71`'s `position: sticky; top: 0` is inert: the top bar is a grid row
  of `.app-shell`, which is `overflow: hidden`, so there is no scrollport for it to stick
  against. Harmless today (the row never scrolls) and it matches
  `docs/UI/02-layout-shell.md:202`, but it is dead CSS that reads as load-bearing.
- **FU-7** - `frontend/` has no axe-core harness, so `docs/UI/11-responsive-a11y.md` §7.1's
  "Axe-core smoke: per top-level view in Vitest" is unimplemented. F-22 is exactly the kind of
  defect it would have caught. Route to `check-quality`.
