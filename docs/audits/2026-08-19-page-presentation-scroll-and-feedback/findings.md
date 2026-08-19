---
type: audit
status: reviewed
created: 2026-08-19
requirements: [R24.25]
---

# Audit: Page presentation - scrolling, transient feedback, and vertical space

## 1. Scope

- **Area** - the whole frontend presentation layer: `frontend/src/app/` (App.vue, layouts,
  router, shell chrome), `frontend/src/shared/ui/` and `frontend/src/shared/composables/`,
  and the view/component trees of all 12 slices (activities, admin, agent-groups, agents,
  conversation, identity, keys, notifications, prompt-studio, skills, tenancy, workflow).
  Three questions drove it: how does the page scroll, where do success/error messages
  appear, and why do pages leave empty space at the bottom.
- **Intent sources** - `docs/UI/01-design-system.md` (z-index scale, tokens),
  `docs/UI/02-layout-shell.md` (shell grid, content area, route-layout table),
  `docs/UI/06-agents.md`, `docs/UI/07-conversation.md`, `docs/UI/08-workflow.md`,
  `docs/UI/11-responsive-a11y.md` (breakpoint matrix, ARIA table, browser floor),
  `docs/UI/12-shared-patterns.md` (error hierarchy 禮4, loading 禮5, empty states 禮6, toast
  rules 禮9), plus `REQUIREMENTS.md` R24.25 for the `field_errors` wire contract. These are
  detailed and current, so most findings below are code-versus-intent, not merely internal
  inconsistency. Where the intent documents contradict each other, that is recorded as such
  rather than blamed on the code.
- **Depth** - thorough. Seven investigation lenses run in parallel (scroll-container
  topology, toast/banner channels, bottom whitespace, conversation scroll behaviour,
  workflow/agents/agent-groups/prompt-studio sweep, remaining eight slices sweep,
  responsive/overlay/viewport-units), producing 61 candidates. One adversarial verification
  round of five batches then attempted to refute every candidate. 8 were refuted, roughly
  12 were materially corrected or narrowed, and the survivors are recorded below with the
  corrected statement rather than the original claim.

## 2. Coverage

**Read in full**: `app/App.vue`, `app/router.ts`, `app/guards.ts`, `app/ErrorBoundary.vue`,
all three layouts, `app/components/AppTopBar.vue`, `app/components/AppSidebar.vue`,
`app/views/Landing.vue`, `app/views/NotFound.vue`, `shared/styles/main.css`,
`shared/composables/useToast.ts`, `useBreakpoint.ts`, `useFocusTrap.ts`,
`useVisualViewport.ts`, `useServerErrors.ts`, `shared/errors/index.ts`,
`shared/transport/problem-json.ts`, `app/errorHandler.ts`, and the shared UI overlay set
(`STable`, `STableCards`, `SModal`, `SDrawer`, `SDropdown`, `STooltip`, `SAlert`,
`SEmptyState`, `SLoadingSpinner`, `SNetworkBanner`, `SConfirmDialog`). The conversation
slice was read end to end, including every component `ChatroomView` was split into and all
scroll/markdown composables. `backend/shared_kernel/errors/handlers.py` was read for F-2.
`node_modules/vue-sonner@2.0.9` was inspected directly for F-1.

**Sampled, not read in full**: the per-slice sweeps opened every `views/*.vue` template root
(74 files) and read the ones with layout-relevant hits in full; the remainder were checked
by pattern grep (`100vh|overflow|position|min-height|p-6|p-4`, loading/empty bindings, route
meta) only. Named in the lens reports as grep-only: several admin, tenancy, keys, identity,
skills and activities components. A defect in one of those that does not match those
patterns would have been missed.

**Not applied**: no browser was launched. This audit is static code and CSS reasoning by the
user's explicit choice, so every claim about rendered pixel heights is arithmetic from the
CSS, not measurement. Findings whose visual magnitude could not be derived exactly are
marked `plausible`. No screenshots, no Playwright run, no device testing. Backend behaviour
was checked only where the frontend contract depended on it (F-2).

**Deliberately excluded**: colour contrast, typography, iconography, motion design, and
copy. Structural quality issues (duplication, layering) and pure accessibility gaps are
recorded in 禮6 and routed elsewhere rather than counted as findings.

## 3. Findings

Ordered by severity. Never renumber.

### F-1: vue-sonner's stylesheet is never imported, so every toast in the app renders unstyled in document flow below the shell

- **Severity**: critical
- **Verdict**: confirmed
- **Evidence**: `frontend/package.json:50` pins `vue-sonner@^2.0.9`;
  `node_modules/vue-sonner/package.json` exports the CSS separately as
  `"./style.css": "./lib/index.css"`; `node_modules/vue-sonner/lib/index.js` contains zero
  occurrences of the substring `css` (verified by count) and injects no style element, so
  2.x does not self-install its CSS. Nothing imports it: the only `vue-sonner` references in
  `frontend/` outside node_modules are `src/app/App.vue:4`, `src/app/errorHandler.ts:2`,
  `src/shared/composables/useToast.ts:1`, `src/shared/styles/main.css:396`, `vite.config.ts:55`,
  `package.json:50` and three test mocks. Everything that makes a toast a toast lives only in
  the unimported file: `lib/index.css:19-21` (`[data-sonner-toaster] { position: fixed; ... }`),
  `:43` (`z-index: 999999999`), `:51-64` (the `data-x-position`/`data-y-position` offsets),
  `:385-397` (the mobile full-width rules). The project knows the pattern and applies it
  elsewhere: `src/slices/workflow/views/WorkflowEditorView.vue:345,347` and
  `src/slices/agents/views/GraphragGraphView.vue:22,24` both import
  `@vue-flow/*/dist/style.css`. `src/shared/styles/main.css:395-398` carries a comment that
  assumes the opposite ("The double-attribute selectors outrank sonner's runtime-injected
  base styles"), which was true of vue-sonner 1.x and is false for 2.x; the override block
  at `:399-440` therefore themes the colours of an element that has no positioning at all.
- **Failure scenario**: any route, any viewport, any toast. `<Toaster>` (`src/app/App.vue:49-52`)
  is a plain flow sibling rendered after `<component :is="layoutComponent">`. With no
  `position: fixed`, its `<section>/<ol>` lays out in normal document flow immediately after
  the layout, and `AppShell` is exactly `height: 100vh` (`src/app/layouts/AppShell.vue:141`),
  so the toast list begins at y = 100vh: one full viewport below the fold. On a chatroom
  delete failure (`src/slices/conversation/composables/useChatroomMessages.ts:353`) the user
  sees the message reappear with no explanation whatsoever. Because the toast adds height
  past the shell, the document also gains a scrollbar for the toast's lifetime and loses it
  when the toast expires.
- **Blast radius**: every success and error toast in the product, on every route, for every
  user. This single defect accounts for three of the four symptoms that prompted this audit:
  messages appearing at the bottom of the page, blank space at the bottom of the page, and a
  scrollbar that appears and disappears for no visible reason.
- **Intent source**: `docs/UI/12-shared-patterns.md` 禮4.1 (the Toast row of the error
  hierarchy) and 禮9 (the whole toast-pattern section), both of which presuppose a visible,
  auto-dismissing, corner-anchored toast channel.
- **Why tests miss it**: e2e assertions match text only, and Playwright's `toBeVisible()` is
  satisfied by any non-empty box regardless of viewport position, e.g.
  `frontend/e2e/16-knowmap.spec.ts:121`. Unit tests mock `vue-sonner` entirely.

### F-2: 422 validation errors reach the user as nothing at all, because backend and frontend disagree on the `field_errors` shape

- **Severity**: critical
- **Verdict**: confirmed
- **Evidence**: `backend/shared_kernel/errors/handlers.py:63` emits raw Pydantic v2
  `exc.errors()` (keys `ctx`/`input`/`loc`/`msg`/`type`) as `field_errors`, and is the only
  producer of that key in the repository. `REQUIREMENTS.md:1942` (R24.25) specifies the shape
  as `{path, message}`, so the backend, not the frontend, deviates from the SRS. The
  generated client independently records the real wire shape at
  `frontend/src/shared/api-client/models/ValidationError.ts:5-11`. The frontend consumes the
  SRS shape: `frontend/src/shared/errors/index.ts:47-53` types it `{path, message}` and
  `frontend/src/shared/composables/useServerErrors.ts:32-40` reads `fe.path`/`fe.message`.
  No normalisation exists anywhere in between (`frontend/src/shared/transport/problem-json.ts:16,47-49`
  re-declares the same wrong type and passes the body through).
- **Failure scenario**: `/agents/:id/tools`, submit the "Add MCP server" dialog with a value
  the backend rejects with 422. `fieldErrors.length > 0`, so the guard at `useServerErrors.ts:33`
  passes; the loop builds `mapped["undefined"] = undefined`, which vee-validate's `setErrors`
  silently drops because no rendered field owns that path; the function nonetheless returns
  `true`. Every call site is written `if (!applyServerErrors(err)) toast.error(...)`, so the
  fallback toast is suppressed. Result: no inline field error, no toast, and the dialog stays
  open because it only closes in `onSuccess`. The user clicks Add repeatedly with no
  indication of what is wrong. Roughly 10 call sites behave this way, including
  `slices/agents/views/AgentToolsView.vue:330,343`, `RagConfigDetailView.vue:207`,
  `RagConfigListView.vue:217`, `KnowledgeMapConfigDetailView.vue:223`,
  `GraphragConfigListView.vue:266`, `McpEgressAllowlistView.vue:105`,
  `ConceptMapPanel.vue:176`, `AgentDetailView.vue:502`, `ActivityTypeForm.vue:369,396`.
- **Secondary symptom**: `frontend/src/shared/errors/index.ts:91-93` joins
  `` `${fe.path}: ${fe.message}` `` into page banners, rendering the literal string
  `undefined: undefined`. Reached through the keys query composables (`useMyKeys.ts:16`,
  `useKeyGroups.ts:22`, `useSearchKeys.ts:21`, `useProjectKeys.ts:18`, `useKeyProjects.ts:25`),
  which are GETs, so this needs a 422 on a GET: real but rarer.
- **Blast radius**: every form in the product that can receive a request-validation 422.
- **Intent source**: `REQUIREMENTS.md:1942` (R24.25); `docs/UI/12-shared-patterns.md` 禮4.2
  (`validation-error` maps `detail.field_errors` to form fields) and 禮4.1 (Field level).
- **Why tests miss it**: `frontend/src/shared/composables/__tests__/useServerErrors.test.ts:42`
  hand-writes a `{path, message}` fixture, so the unit test pins the contract the backend
  never sends. `frontend/e2e/11-mcp.spec.ts:57` records the symptom as accepted behaviour in
  a comment ("a server field error that maps to no rendered field suppresses the toast") and
  its assertion at `:70` expects a 201, so no 422 is ever exercised.

### F-3: 34 views add their own root padding on top of the shell's, so the documented gutter is 40px or 48px and differs per slice

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: the shell owns content padding at `frontend/src/app/layouts/AppShell.vue:188`
  (24px), `:207-209` (16px below 1024px), `:216-218` (8px below 480px). Enumerating all 74
  `**/views/*.vue` template roots: 34 add a second padded root (26 use `p-6` = 24px, 7 use
  `p-4` = 16px, and `slices/notifications/views/NotificationsView.vue:13` uses
  `px-4 py-4 sm:p-6`), 40 do not. The 26/7/1 split supersedes the 28/5/1 first reported; it
  was re-counted while writing `docs/tasks/2026-08-19-content-area-spacing-and-scroll-contract/`.
  F-40's 23 nested `<main>` roots are a subset of these 34. `p-6` = 24px is explicit in `shared/styles/main.css:5-6`.
  Only two routes opt out of shell padding (`slices/workflow/routes.ts:14`,
  `slices/conversation/routes.ts:26`) and neither view is in the padded set, so all 34 stack.
  Representative padded roots: `slices/agents/views/AgentListView.vue:244`,
  `slices/keys/views/KeyListView.vue:128`, `slices/workflow/views/WorkflowRunView.vue:2`,
  `slices/prompt-studio/views/PersonalPromptStudioView.vue:12`. Unpadded controls verified to
  render directly in `main`: `slices/tenancy/views/OrgListView.vue:89`, and
  `slices/admin/views/AdminUsersView.vue:2` via `slices/admin/views/AdminLayout.vue:24-39`,
  which is a bare grid with `gap` only.
- **Failure scenario**: at 1440x900, navigate `/orgs` (24px inset) to `/keys` (48px inset) to
  `/workspaces/:wid/workflows` (40px inset). The page title's left edge and the top gutter
  visibly jog on every cross-slice navigation. The bottom is affected identically: the last
  card ends 48px above the fold instead of 24px. It is worst on small phones, where the shell
  correctly drops to 8px but the view keeps its 24px, giving 32px where the spec says 8px:
  four times the documented gutter, on the device with the least room.
- **Blast radius**: 34 of 74 views, at every breakpoint. This is the main structural cause of
  the "too much empty space at the bottom" complaint that survives after F-1.
- **Intent source**: `docs/UI/02-layout-shell.md` 禮3.3 ("Padding: 24px on desktop, 16px on
  mobile") and the 禮9 route table, which lists 24px for every non-immersive app route;
  `docs/UI/11-responsive-a11y.md` 禮2.1 breakpoint matrix.

### F-4: Nothing resets the scroll position on navigation, so the previous page's scroll offset carries into the next view

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/app/router.ts:50-53` creates the router with only `history` and
  `routes`, no `scrollBehavior`. `frontend/src/app/guards.ts:21-70` is pure and touches no
  DOM. A repository-wide grep finds no write to `main.scrollTop` outside the chatroom and
  prompt-studio internal scrollers; `AppShell.vue:75` only reads it for the topbar shadow.
  `AppShell` is never remounted between authenticated routes: `App.vue:17-23` returns the same
  component object for every one of them and carries no `key`, so `<component :is>` patches
  rather than remounts and the `<main>` element and its `scrollTop` persist. Adding a
  conventional `scrollBehavior` would not fix this, because vue-router's return-based API
  resolves through `window.scrollTo` while the real scroll container is
  `main.app-shell__content` (`AppShell.vue:183-189`).
- **Failure scenario**: `/admin/audit` at 1440x900, click "Load more" twice (about 5000px of
  rows), scroll to y = 2400, then click "Activities" in the admin nav. `/admin/activities`
  renders with `main.scrollTop` still 2400: the user lands in the middle of the table with
  the page header off-screen above. The `<Transition mode="out-in">` at `App.vue:34-44` does
  not rescue this, because Vue removes the leaving node and calls `instance.update()`
  synchronously in the same task, so the browser never lays out an empty `main` and never
  clamps `scrollTop` to 0. Landing at the top of a shorter page is therefore only a side
  effect of the new content being too short to sustain the old offset, not a designed reset.
- **Blast radius**: every navigation between two authenticated routes whose target content is
  taller than the retained offset. Most visible on the long admin and audit tables.
- **Intent source**: `docs/UI/02-layout-shell.md` 禮3.3 designates the content area as the
  scroll owner without a corresponding reset rule, and `docs/UI/12-shared-patterns.md` 禮8.3
  requires detail pages to be reachable without browser-back dependency. Internal
  inconsistency: the shell defines a scroll container that the routing layer has no contract
  with.
- **Note**: query-only navigation deliberately preserves scroll (`App.vue:31-33` keys on
  `path`, not `fullPath`), and the query-only navigations that exist are tab and scope
  switches where preserving scroll is defensible. That half is not a defect.

### F-5: The impersonation banner is painted over the top bar and nothing reserves space for it

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/admin/components/ImpersonationBanner.vue:18-33` is
  `position: fixed; top: 0; left: 0; right: 0; z-index: 9999`, rendered as a sibling of the
  layout at `frontend/src/app/App.vue:28`. Grepping all of `src/` for impersonation touches
  only 14 files (api, stores, locales, admin views, `shared/transport/axios.ts`); no layout
  or CSS file offsets anything. `.app-shell` sets no `z-index`, `transform` or `filter`, so
  the banner and `AppTopBar.vue:79` (`z-index: var(--z-topbar)` = 200,
  `shared/styles/main.css:80`) share the root stacking context and 9999 wins. The hardcoded
  9999 also outranks `--z-modal` (400), `--z-toast` (500) and `--z-tooltip` (600); the sibling
  `shared/ui/SNetworkBanner.vue:46` shows the correct pattern with `var(--z-banner, 350)`, and
  `main.css:82-84` states the rule it should follow ("above chrome, below modals").
- **Failure scenario**: an admin impersonates a user and lands on any authenticated route at
  1280x800. The banner is about 33px tall (8px + 8px padding plus a roughly 17px line box at
  `font-size: .875rem`) against a 56px top bar. The 40px sidebar toggle at `AppTopBar.vue:93-94`
  is centred at y 8 to 48, so its top 25px is covered and only the bottom 15px remains
  clickable; the wordmark at y 19 to 37 is almost entirely covered. `.app-shell` is
  `overflow: hidden`, so no scroll can move the top bar out from under it. The banner also
  paints over an open modal's top edge and over any toast.
- **Blast radius**: the whole impersonation session, which is exactly the flow the banner
  exists to support.
- **Intent source**: `docs/UI/02-layout-shell.md` 禮4.3 (top bar at 56px, `--z-topbar`, sticky
  top 0) versus 禮1, whose integration diagram places the banner "fixed top" over the same
  strip with no reconciliation; `docs/UI/01-design-system.md` z-index scale.

### F-6: A caught render error replaces the entire shell, not the failed view

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/app/App.vue:29-46` places `<ErrorBoundary>` outside
  `<component :is="layoutComponent">`, so the boundary's `<slot v-else />`
  (`frontend/src/app/ErrorBoundary.vue:64`) is the entire layout. On `failed = true` it is
  swapped for the block at `:67-73`: `max-width: 32rem; margin: 4rem auto; padding: 1.5rem;
  text-align: center`, with no min-height and no shell. The file's own comment at `:6-7`
  states the opposite intent verbatim: "`onErrorCaptured` lets us swap in a fallback for that
  subtree instead, keeping the rest of the shell alive."
- **Failure scenario**: any authenticated route at 1440x900 where a view throws during
  render. The top bar, sidebar and content background all disappear; the page becomes a
  roughly 130px tall centred text block starting 64px down, with about 700px of bare body
  background beneath it. The `route.fullPath` watch at `:35-40` cannot rescue the user,
  because the navigation chrome needed to change routes is precisely what was unmounted. Only
  the retry button remains.
- **Blast radius**: every uncaught render error, plus every unhandled rejection from a native
  event handler (see F-21), which routes through the same boundary.
- **Intent source**: `docs/UI/12-shared-patterns.md` 禮4.1 assigns ErrorBoundary the Global
  level with "Retry button + fallback UI", meaning a fallback for the failed subtree;
  contradicted by the component's own docstring.

### F-7: The 404 route carries no `meta`, so an authenticated user hitting a bad URL loses the entire shell

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/app/router.ts:43-47` defines the `/:pathMatch(.*)*` record with
  `path`, `name` and `component` and no `meta` key at all. `router.beforeEach` (`:55-75`)
  only reads meta and forwards it to `runGuards`; `guards.ts:21-70` is pure and never writes
  `to.meta`. `App.vue:22` therefore falls through to
  `route.meta.requiresAuth ? AppShell : AuthLayout` with `requiresAuth` undefined, selecting
  `AuthLayout`.
- **Failure scenario**: a logged-in user opens `/orgs/does-not-exist-typo` at 1440x900. The
  sidebar, top bar, org/project switcher and notification bell all vanish and the page becomes
  a 420px-wide centred column on the auth background (`AuthLayout.vue:32-35`). Combined with
  F-28's `min-height: 60vh`, the result is a small card floating in an otherwise blank browser
  window, with the only navigation being the "Go Home" button and the logo link.
- **Blast radius**: every 404 reached by an authenticated user, including mistyped deep links
  and stale bookmarks.
- **Intent source**: `docs/UI/02-layout-shell.md` 禮7 ("Uses `AppShell` if authenticated,
  `AuthLayout` if not") and the 禮9 route table row for `/:pathMatch(.*)*`.
- **Why tests miss it**: `frontend/src/app/__tests__/NotFound.test.ts` mounts the view in
  isolation and pins no layout.

### F-8: `STable`'s `sticky-header` prop is inert

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/shared/ui/STable.vue:465-468` declares
  `.s-table-wrap { width: 100%; overflow-x: auto }` and never declares `overflow-y`. Per CSS
  Overflow 3 禮3.1, when one axis is not `visible` the other computes to `auto`, so the wrapper
  is a scroll container in both axes and becomes the nearest scrollport for the sticky
  `<thead>` at `:488-492` (`position: sticky; top: 0; z-index: 10`). Nothing ever gives the
  wrapper a height: grepping `s-table-wrap` across `src/slices` and `src/shared/styles`
  returns nothing, and both consumers drop the table into normal flow with only
  `class="mt-6"` (`slices/agents/views/AgentListView.vue:319-329`,
  `slices/conversation/views/ChatroomListView.vue:269-279`). The wrapper therefore never
  scrolls vertically, `top: 0` is permanently satisfied, and the header scrolls away with
  `main`.
- **Failure scenario**: `/projects/:pid/agents` at 1440x900 with 40 agents. Scrolling `main`
  carries the column headers off the top of the screen and they never pin, so past roughly row
  15 the user is reading unlabelled columns and cannot reach the sortable headers without
  scrolling back up.
- **Blast radius**: both `sticky-header` consumers today, and any future one: the prop looks
  wired and does nothing.
- **Intent source**: `docs/UI/06-agents.md` 禮1.4 ("**Component**: `STable` with
  `stickyHeader`"); internal inconsistency with the prop's own name and default
  (`STable.vue:49,60`).

### F-9: `SDropdown` menus overflow the viewport with no flip and no height cap, and the last row's items are unreachable

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/shared/ui/SDropdown.vue:112-125` is the only positioning code
  and sets nothing but `position: 'fixed'`, `top: rect.bottom + 4` and one horizontal edge:
  no viewport-height read, no upward flip, no clamp. `.s-dropdown__menu` (`:243-250`) has no
  `max-height` and no `overflow`. Items are 36px (`:263`). The menu is teleported to `body`
  (`:186-193`), which does not scroll inside the shell. `onScrollWhileOpen` (`:127-129`,
  registered on `window` with `capture: true` at `:148`) repositions the menu and never closes
  it, so it stays glued to a trigger that is itself moving.
- **Failure scenario**: `/keys` at 1366x768 with enough rows to fill the page. Scroll `main`
  to the bottom, then open the row-action dropdown on the last row (trigger at roughly
  y = 700). A five-item menu renders from y = 704 to y = 892, 124px past the viewport bottom.
  Because the content region is already at its scroll end, there is no further scroll that can
  bring the overflowing items into view, so the last items, conventionally the destructive
  ones, are unreachable by mouse. Row-action dropdowns exist at
  `slices/agents/views/AgentListView.vue:378`, `slices/keys/views/KeyListView.vue:206`,
  `slices/conversation/views/ChatroomListView.vue:307` and elsewhere.
- **Blast radius**: every list view with row actions, worst on short viewports and long lists.
- **Deeper cause found during triage**: `updateMenuPosition` runs *before* `nextTick`, so at
  the moment it measures, the menu element does not yet exist. Any flip-or-clamp logic added
  without reordering that call would have nothing to measure against. Recorded in
  `docs/tasks/2026-08-19-shared-overlay-and-shell-defects/`.
- **Intent source**: `docs/UI/11-responsive-a11y.md` 禮5.3 (arrow-key navigation presumes every
  item is reachable) and 禮4 (40px minimum dropdown item as a usable touch target).
- **Secondary**: `--z-dropdown` (300) is below `--z-modal` (400), so a dropdown opened inside
  a modal paints under the panel. Latent today, since no such nesting currently exists.

### F-10: `GraphragGraphView` sizes itself with `100vh` inside the padded content area, forcing a permanent 48px scroll

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/agents/views/GraphragGraphView.vue:168` is
  `<main class="p-6 flex flex-col h-[calc(100vh-3.5rem)]">`. `3.5rem` equals the 56px
  `--topbar-height` (`shared/styles/main.css:66`), but the content box is
  `100vh - 56 - 48` because `AppShell.vue:188` adds 24px top and bottom, and neither route
  opts out (`slices/agents/routes.ts:34-40` for graphrag, `:53-59` for knowmap; `AppShell.vue:53-55`
  zeroes padding only for `contentPadding: 'none'` or the two immersive path patterns).
- **Failure scenario**: `/projects/:pid/graphrag-configs/:cid/graph` at 1440x900. The content
  box is 796px and the view declares 844px, so `main` scrolls exactly 48px on a page that is
  meant to be a fixed-height canvas. Dragging or wheel-zooming the Vue Flow canvas near the
  edge nudges `main` instead, and the top bar picks up its scrolled shadow
  (`AppShell.vue:71-77`) on a page with nothing to scroll to. The excess is 32px below 1024px
  and 16px below 480px. Same on the knowmap graph route.
- **Correction to the original claim**: there is no 48px blank strip. The canvas and empty
  states are `flex-1` (`:220,228,233`) and absorb the extra height. The defect is the
  permanent scrollbar and the hijacked wheel, not dead space. The `p-6` also double-pads per
  F-3, and the `3.5rem` literal silently assumes a 16px root font size instead of reading
  `--topbar-height`.
- **Blast radius**: the two graph routes.
- **Intent source**: `docs/UI/02-layout-shell.md` 禮3.1 and 禮3.3; the view double-counts the
  shell's own sizing contract.

### F-11: "Load earlier" restores the wrong scroll position, discarding the user's offset

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/composables/useChatroomScroll.ts:95-104`.
  `captureBeforePrepend` saves only `scrollHeight`; `restoreAfterPrepend` assigns
  `el.scrollTop = el.scrollHeight - savedHeight`. The pre-prepend `scrollTop` is never
  captured and never added back (the only other `scrollTop` reads in the composable are at
  `:33` and `:43`). The correct expression is
  `savedScrollTop + (newHeight - savedHeight)`. Driven from
  `slices/conversation/views/ChatroomView.vue:877-881`.
- **Failure scenario**: `/chatrooms/:id` at 1440x900. The "Load earlier" control is the first
  `<li>` of the feed (`ChatroomView.vue:54-59`), so it only has to be in view, not flush at
  the top: a user can click it at `scrollTop = 240`. After the older page prepends, the
  restore sets `scrollTop = ?H` instead of `?H + 240`, so the feed jumps 240px upward toward
  older content and the message the user was reading moves down out of view. The error is
  bounded by roughly one viewport height and compounds across successive clicks.
- **Blast radius**: every history load in every chatroom.
- **Intent source**: `docs/UI/07-conversation.md:893` ("the scroll position is adjusted so the
  previously-topmost visible message remains in the same viewport position. Implementation
  uses `scrollTop` delta calculation before and after DOM update"); also the composable's own
  contract at `useChatroomScroll.ts:6,94`.

### F-12: The "new messages" pill counts messages loaded from history, reporting up to 100 new messages when none arrived

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `useChatroomScroll.ts:60-67` increments `newCount` from raw `messageCount`
  deltas; `messageCount` is `messages.value.length`
  (`slices/conversation/views/ChatroomView.vue:696`), and `messages`
  (`composables/useChatroomMessages.ts:116-122`) folds in `olderMessages`, which
  `loadEarlierPage` prepends at `:181` with `PAGE_SIZE = 100` (`:34`). `onLoadEarlier`
  (`ChatroomView.vue:877-881`) only wraps the capture/restore pair; there is no flag, no
  paused watcher and no separate counter anywhere in the slice.
- **Failure scenario**: the user scrolls up in a busy room and clicks "Load earlier". They are
  by definition not `atBottom`, so `newCount += 100`. A pill reading "100 new messages"
  appears at the bottom of the feed although nothing new arrived, and clicking it throws them
  to the bottom of the history they were about to read.
- **Blast radius**: every history load in every chatroom, compounding with F-11.
- **Intent source**: `docs/UI/07-conversation.md:908` scopes the pill to genuinely new
  messages.

### F-13: Nothing re-scrolls after the async markdown enhancement, so diagrams, maths and images push the newest message below the fold

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `slices/conversation/composables/useMarkdownEnhance.ts:52-56` calls
  `opts.onAfterUpdate?.()` (wired to `maybeStick` at `ChatroomView.vue:709`) synchronously
  inside `onUpdated`, while `schedule()` (`:44-50`) defers `run()` by
  `ENHANCE_DEBOUNCE_MS = 120` (`:14`) and `run()` awaits three dynamic imports plus
  `mermaid.render` (`slices/conversation/utils/renderMarkdown.ts:126,139,163,171,185-187`)
  before mutating the DOM at `:150,177`. The growth is therefore strictly after the scroll,
  and because the enhancement mutates the DOM directly it triggers no further `onUpdated`.
  There is no `ResizeObserver` or `IntersectionObserver` anywhere in the conversation slice,
  and `slices/conversation/components/AttachmentImage.vue:69-76` has `loading="lazy"`, no
  intrinsic `width`/`height` and no `@load` handler; its `url` ref change re-renders only the
  child, so the parent's `maybeStick` never re-fires.
- **Failure scenario**: the user is pinned to the bottom and an agent finishes a message
  containing a Mermaid diagram, a `$$...$$` block, a highlighted code block or an image
  attachment (`max-height: 360px`, `AttachmentImage.vue:96`). `maybeStick` scrolls to the
  pre-enhancement height; 120ms or more later the content resolves and adds several hundred
  pixels, pushing the message body below the fold. The user must scroll manually to read the
  reply they were watching arrive. The same pass also shifts the viewport a second time after
  F-11's restore.
- **Blast radius**: every rich message in every chatroom, which is the product's core surface.
- **Intent source**: `docs/UI/07-conversation.md:907` ("User is at bottom (within 80px of
  scrollHeight) | New messages auto-scroll feed to bottom").

### F-14: The chat composer never auto-grows, so multi-line drafts are typed through a one-line window

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `slices/conversation/components/ChatroomComposer.vue:29-48` sets `rows="1"`;
  `onInput` (`:229-233`) only emits the model value and a typing event; `textareaRef` is
  passed only to `useMentionAutocomplete` (`:198`). A repository-wide grep for `scrollHeight`
  returns only the two scroll composables and `PromptAssistantPanel.vue:118`: there is no
  height assignment, no `field-sizing` CSS and no directive. So the `min-height: 36px` /
  `max-height: 192px` pair at `:284-297` fixes the box at one line that scrolls internally,
  and the reserved 192px is dead CSS.
- **Failure scenario**: the user writes a five-line message with Shift+Enter. The box stays
  about 36px tall and scrolls internally, so only the last line is visible while composing and
  they cannot review what they wrote before sending.
- **Blast radius**: every multi-line message, in every chatroom.
- **Intent source**: `docs/UI/07-conversation.md:670-671` ("Max-height: 192px (approximately 8
  lines, then scrolls internally) / Auto-grows with content").
- **Note**: the composer is a real grid row (`ChatroomView.vue:904,980-983`), not an overlay,
  so it does not occlude the feed. The defect is the inverse of the occlusion this lens
  expected.

### F-15: Streaming re-renders the full markdown pipeline on every token

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `slices/conversation/composables/useChatroomSocket.ts:376-379` calls
  `store.appendAgentToken` per frame; `slices/conversation/stores/conversation.ts:97-103`
  reassigns `agentStreams` immutably on each token; `composables/useAgentStreams.ts:26` keys
  its cache on `cached.source === text`, which can only hit for agents whose text did not
  change and therefore never for the agent being appended to. Each WebSocket message is its
  own task, so each token schedules its own render, its own `renderMarkdown` (markdown-it plus
  DOMPurify) and its own full `v-html` subtree replacement in
  `components/ChatroomStreamingBubble.vue:16-19`. Grepping the slice for
  `requestAnimationFrame|throttle|debounce` finds only the 120ms enhancement timer in
  `useMarkdownEnhance.ts:14`, which gates the KaTeX/Mermaid/highlight pass, not the markdown
  render.
- **Failure scenario**: an agent emitting roughly 30 tokens per second forces 30 full markdown
  parses, sanitisations and DOM subtree replacements per second. Each replacement re-runs
  `maybeStick` and discards any highlighting applied on the previous pass, producing visible
  thrash and continuous main-thread work on a long reply.
- **Blast radius**: every streamed agent reply.
- **Intent source**: `docs/UI/12-shared-patterns.md:474` ("Rendered through markdown pipeline
  (debounced at 120ms to avoid jitter)") and `docs/UI/07-conversation.md:513`, which claims
  the cache "avoids calling `renderMarkdown()` on every token at high frequency". The cache
  exists but cannot hit during streaming, which is the only time it matters.

### F-16: `PromptAssistantPanel`'s internal scroll region never engages below 1024px, so the chat grows unbounded and pushes its own composer off the page

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `slices/prompt-studio/components/PromptAssistantPanel.vue:123` roots on
  `flex h-full flex-col`, with the message list at `:145` (`flex-1 ... overflow-y-auto`) and
  the composer as the last flex child at `:208-228`. Its mount site,
  `slices/agents/views/AgentDetailView.vue:960-966`, carries
  `min-h-[32rem] lg:sticky lg:top-6 lg:self-start lg:h-[calc(100vh-8rem)]`: a height only
  under `lg:`. The parent grid at `:929` is `grid grid-cols-1 gap-6 lg:grid-cols-[1fr_22rem]`,
  so below 1024px the cell height is auto, `h-full` resolves against an indefinite height, and
  `flex: 1 1 0%` with `min-height: auto` lets the list grow to content.
- **Failure scenario**: `/agents/:id` on the Prompt tab at 900x1200 (tablet portrait). After
  about 15 assistant turns the panel is several thousand pixels tall. The Send box is pushed
  to the bottom of the document, so the user must scroll the whole page down to type and back
  up to see the prompt editor. It is not unreachable, since `main` still scrolls, but the
  panel's declared scroll region is dead in one of its two layout modes.
- **Blast radius**: the prompt assistant on every viewport below 1024px.
- **Intent source**: internal inconsistency; the component declares a scroll region at `:145`
  that only functions under `lg`.

### F-17: `WorkflowBackstageView` renders an empty page until a run is chosen, with no empty state

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `slices/workflow/views/WorkflowBackstageView.vue:23` opens a
  `<template v-if="selectedRunId">` that wraps every section through `:131`, with no `v-else`.
  `:154` initialises `const selectedRunId = ref('')`, and `runOptions` (`:181-187`)
  deliberately leads with a `{ value: '', label: '-' }` entry. No auto-select watcher exists;
  the only watches are the auth redirect (`:161`), chain resolution (`:225`) and agent names
  (`:268`). The step-trace loading indicator is a bare `...` at `:29-34`.
- **Failure scenario**: `/workspaces/:wid/workflows/:wfid/backstage` at 1920x1080. On arrival
  the page shows a header, a subtitle and a `max-w-xs` select, roughly 160px of content in a
  1024px-tall scrollport, with about 860px of blank white below and nothing telling the user a
  selection is required. Picking a run then swaps about 20px of text for a multi-hundred-pixel
  trace, jumping four sections at once.
- **Blast radius**: every visit to the backstage view.
- **Intent source**: `docs/UI/12-shared-patterns.md` 禮6.1 (contextual empty state) and 禮5.1
  (structural skeleton, not a text placeholder).

### F-18: `AgentDetailView`'s mobile action bar is `fixed` with no reserved space and permanently covers the end of the form

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `slices/agents/views/AgentDetailView.vue:1274-1277` is
  `fixed bottom-0 left-0 right-0 p-4 bg-bg border-t border-border flex gap-3 z-10`: genuinely
  `fixed`, not `sticky`. Grepping `pb-|padding-bottom` across the file yields only `pb-2` at
  `:1060`, an unrelated list divider, so neither the form (`:755-1271`) nor the view root
  (`:665`) compensates. A `fixed` child is out of flow and contributes nothing to `main`'s
  scroll height, so no amount of scrolling can reveal what it covers.
- **Failure scenario**: `/agents/:id` at 375x812 on the Prompt tab. Scrolled fully to the
  bottom of `main`, the `SCharCount` under the system-prompt editor (`:954-957`) and the
  bottom edge of its `SCard` sit exactly where the bar is painted. Net occluded content is
  about 56px (the bar is roughly 72px, minus the shell's 16px bottom padding at this
  breakpoint). On the Knowledge tab the final select's help text is unreachable.
- **Blast radius**: the agent detail view on every viewport below 768px.
- **Intent source**: `docs/UI/11-responsive-a11y.md` 禮3.1 ("Action buttons: stacked vertically
  on mobile instead of horizontal row") describes an in-flow bar, and 禮7.2's manual checklist
  requires no content overlap.

### F-19: Two admin views report the same failure twice, on two channels, with two different messages, and the banner never clears

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `slices/admin/composables/useAdminActions.ts:59,65,116,121` give
  `promoteAdmin`, `demoteAdmin`, `restoreResource` and `resetGraphrag` an
  `onError: () => toast.error(...)`. The views then `await ...mutateAsync(...)` and catch the
  same rejection to set a second, differently worded string:
  `slices/admin/views/AdminAdminsView.vue:137-139` (`admin.users.promotionFailed` against the
  composable's `admin.actionErrors.promoteFailed`), `:154-160`, and
  `slices/admin/views/AdminOpsView.vue:125-127,142-144`. The banner is an `SAlert` with
  `dismissible` defaulting to false (`shared/ui/SAlert.vue:28`) and no timer; it resets only
  at the start of the next attempt (`AdminAdminsView.vue:133,143`, `AdminOpsView.vue:112,137`).
  `slices/admin/views/AdminIpBansView.vue:139-141` is the correct contrast: an empty catch
  with a comment deferring to `onError`.
- **Failure scenario**: `/admin/admins`, promote a user id that does not exist. A red toast
  says "Failed to promote admin" while a focus-stealing danger banner under the form says
  "Promotion failed". The toast expires; the banner stays indefinitely, so a later successful
  promotion still shows a standing error.
- **Blast radius**: four admin actions across two views.
- **Intent source**: `docs/UI/12-shared-patterns.md:550` ("One toast per action") and 禮4.1,
  which assigns exactly one level per error.

### F-20: The global error handler emits hardcoded English and raw backend `detail` into toasts

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/app/errorHandler.ts` is installed unconditionally in production
  (`app/main.ts:8,52`) and the toast at `:39` fires in every environment; only `reportError`
  branches on `import.meta.env.PROD`. `:21` hardcodes
  `` `Rate limited. Please retry in ${seconds}s.` `` although the exact translated equivalent
  already exists and is used at `shared/composables/useServerErrors.ts:28`
  (`shared/locales/en.json:35`, `shared/locales/zh-TW.json:35`). `:39` hardcodes "An
  unexpected error occurred. Please try again." with no key anywhere in `shared/locales/`.
  `:13` pipes backend-authored `err.detail` verbatim. All four call sites also use the raw
  `toast` API rather than `useToast()`.
- **Failure scenario**: a zh-TW user hits a rate limit or an uncaught error and sees an
  English-only sentence in an otherwise Chinese UI; a 403 surfaces whatever English sentence
  the API author wrote in `detail`.
- **Blast radius**: every rate limit, every permission error and every uncaught error.
- **Intent source**: project rule "All user-facing strings go through `$t()` (vue-i18n)";
  `docs/UI/12-shared-patterns.md` 禮4.2 specifies a fixed UI string for `forbidden`, not the
  problem's `detail`, and 禮9 requires a brief description rather than raw server text.

### F-21: An unguarded `mutateAsync` in `WorkflowListView` trips the error boundary and replaces the whole list

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `slices/workflow/views/WorkflowListView.vue:175-180` is
  `async function onCreate() { ... await createMutation.mutateAsync(name); ... }` with no
  `try`/`catch`, bound at `:8` as `@submit.prevent="onCreate"`. Vue routes native-event-handler
  rejections through `callWithAsyncErrorHandling` into the ancestor `errorCaptured` chain, and
  `frontend/src/app/ErrorBoundary.vue:17-30` returns `false` for everything except `AuthError`,
  explicitly stopping propagation. Every other `mutateAsync` call in the codebase is inside a
  `try`.
- **Failure scenario**: a member without create rights submits the "new workflow" form. The
  mutation's own `onError` toast fires (`:172`), and the rejection then trips the boundary,
  replacing the entire workflow list with F-6's fallback screen.
- **Blast radius**: one view, but the failure mode is severe and the pattern is a one-line
  omission that could recur.
- **Intent source**: internal inconsistency with every other mutation call site.
- **Note**: this candidate was originally reported as a duplicate toast via
  `window.onunhandledrejection`. That mechanism was refuted (see 禮4); the real consequence is
  worse.

### F-22: `SAlert` hardcodes `role="alert"`, so static informational panels interrupt screen readers on page load

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/shared/ui/SAlert.vue:67` sets `role="alert"` unconditionally on
  the root; the component has no variant branch for it (the only variant-driven logic is
  `iconComponent` at `:51-59` and the CSS classes). `role="alert"` implies
  `aria-live="assertive"`. Static, non-event panels using it include
  `slices/keys/components/KeyUploadForm.vue:140`,
  `slices/agents/views/McpEgressAllowlistView.vue:177-178`,
  `slices/tenancy/views/OrgTransferView.vue:223,309,372`,
  `slices/conversation/views/ChatroomSettingsView.vue:574`,
  `slices/workflow/components/config/WaitForEventConfigForm.vue:172-173` and
  `slices/skills/components/SkillFiles.vue:268-270`; 15 or more further `variant="info"` sites
  exist across the slices.
- **Failure scenario**: `/orgs/:id/transfer` with a screen reader. Three informational alerts
  mount with the view and each fires an assertive announcement that pre-empts the page heading
  and form labels, so the user hears the footnotes before the page identity.
- **Blast radius**: every static `variant="info"` and `variant="success"` alert in the
  product.
- **Intent source**: `docs/UI/11-responsive-a11y.md:284` verbatim: "SAlert | `role="alert"`
  for danger/warning, `role="status"` for info/success".

### F-23: An empty chatroom renders its empty state flush at the top, leaving the feed blank down to the composer

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `slices/conversation/views/ChatroomView.vue:135-141` places `SEmptyState` in a
  plain `<li>` of an `<ol class="messages">` that is `height: 100%; overflow-y: auto` with no
  flex and no bottom anchoring (`:927-933`), and `shared/ui/SEmptyState.vue:46-55` is
  `flex-direction: column; align-items: center; margin: 0 auto`: horizontal centring only.
- **Failure scenario**: open a chatroom with zero messages. The icon, title and text block sit
  flush at the top of the feed with about 2rem of padding, and the remaining several hundred
  pixels down to the composer are empty. It is not floating in the vertical middle; it is
  pinned to the top.
- **Blast radius**: every newly created chatroom, which is the first thing a new user sees.
- **Intent source**: `docs/UI/07-conversation.md:1018` ("Vertically and horizontally centered
  in the message feed area").
- **Narrowed during verification**: the two-message case is *not* a defect. No spec line
  requires bottom-anchoring a short feed, and `scrollToBottom` being a no-op without overflow
  is correct behaviour.

### F-24: History pagination has no scroll-based auto-trigger

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `slices/conversation/components/ChatroomLoadEarlier.vue` is 48 lines
  containing one `SButton` that emits `load`: no lifecycle hooks, no observers.
  `useChatroomScroll.ts:49-52` recomputes `atBottom` from the bottom threshold only and never
  reads `scrollTop` against a top threshold; nothing calls `loadEarlier` except the button
  handler (`ChatroomView.vue:877`). No `IntersectionObserver` exists anywhere under
  `slices/conversation`.
- **Failure scenario**: the user scroll-wheels to the top of a 500-message room expecting
  history to keep arriving, as in every other chat product. Scrolling stops dead and they must
  locate and click the button once per 100 messages.
- **Blast radius**: reading any long conversation history.
- **Intent source**: `docs/UI/07-conversation.md:895` ("Auto-trigger: when the user scrolls to
  within 100px of the top of the feed and `hasOlderMessages` is true, `loadEarlier()` triggers
  automatically ... The button remains as a fallback") and `:1312`, which names the file
  "Load-earlier button with auto-trigger on scroll".

### F-25: No safe-area handling anywhere, and the viewport meta does not opt into the display cutout

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/index.html:5` is
  `<meta name="viewport" content="width=device-width, initial-scale=1.0" />` with no
  `viewport-fit=cover`, so `env(safe-area-inset-*)` would resolve to 0 even if used. Grepping
  `safe-area|env\(` across `frontend/src` returns zero matches. Bottom-anchored UI that needs
  it: the chat composer (`ChatroomView.vue:980-983`, grid row 4, flush to the shell bottom)
  and the new-messages pill (`:1001-1006`, `bottom: 16px`).
- **Failure scenario**: `/chatrooms/:id` on a notched iPhone in portrait. The composer's send
  and attach buttons occupy the bottom of the shell, and the bottom 34px is the home-indicator
  strip where iOS intercepts the swipe gesture, so taps near the send button either miss or
  trigger the system gesture.
- **Blast radius**: notched mobile devices, chatroom and any future bottom-anchored UI.
- **Intent source**: `docs/UI/11-responsive-a11y.md` 禮4 (44x44px minimum usable hit area) and
  禮3.2 ("Composer | Fixed bottom").

### F-26: Three detail views gate the entire page behind a bare inline spinner, so first load shows a thin line over a blank viewport

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `shared/ui/SLoadingSpinner.vue:52-59` is `display: flex; align-items: center;
  gap: .5rem` with no min-height and no centring, and a 24px icon at `:70-73`. Three views put
  it in place of the whole template, page header included:
  `slices/tenancy/views/ProjectDetailView.vue:103-106`,
  `slices/tenancy/views/OrgDetailView.vue:111-114`,
  `slices/admin/views/AdminUserDetailView.vue:3-7`.
- **Failure scenario**: `/orgs/:id` at 1440x900 on a cold cache with a 400ms response. For
  those 400ms the entire 796px content area is blank except a 24px spinner row at the top
  left; then the header and cards appear at once.
- **Blast radius**: three detail views on cold load.
- **Intent source**: `docs/UI/12-shared-patterns.md` 禮5.1 ("First load of any page shows
  skeleton layout matching the page structure ... This avoids layout shift when data loads").
- **Corrected during verification**: four of the seven originally cited views do not match.
  `AdminHomeView.vue:3`, `AdminMetricsView.vue:3` and `OrgTransferView.vue:185-188` render
  `SPageHeader` before the spinner, so the page is not blank; `AgentGroupDetailView.vue:127-130`
  is `flex justify-center py-16`, horizontally centred with 128px of vertical space.

### F-27: Where skeletons exist, their height does not match the loaded height, so the page jumps

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `slices/agents/views/AgentDetailView.vue:667-680` renders one default skeleton,
  five 32x80px rects and two more default skeletons, against a loaded General tab of two
  `SCard`s of form fields; `docs/UI/06-agents.md:449-452` requires "Two card skeletons with 4
  field skeletons each". `slices/tenancy/views/InboxInvitesView.vue:139-149` renders three
  120px rects with a 12px gap (`:221-226`) totalling 384px, against a roughly 200px
  `SEmptyState` at `:152-156`. `slices/identity/views/SessionsView.vue:126-135` renders three
  80px rects with a 12px margin (`:217-223`) totalling 264px, against a roughly 60px first
  session row (`:239-242`).
- **Failure scenario**: `/invites` for a user with no pending invites: 384px of skeleton
  collapses to a 200px empty state, a 184px upward jump. `/account/sessions` with one session:
  264px collapses to about 60px, a 200px jump that pulls the page header's neighbours up under
  the cursor.
- **Blast radius**: three views on every cold load.
- **Intent source**: `docs/UI/12-shared-patterns.md` 禮5.1; `docs/UI/06-agents.md` 禮2.10.

### F-28: Two views centre their content inside an arbitrary box, parking it in the upper part of the page

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/app/views/NotFound.vue:34-40` is
  `display: flex; align-items: center; justify-content: center; min-height: 60vh`;
  `slices/tenancy/views/InviteAcceptView.vue:105-111` is the same shape with
  `min-height: 400px`. Neither value is derived from `--topbar-height` or the content padding.
- **Failure scenario**: at 1440x900 the content box is `900 - 56 - 48 = 796px`. NotFound's box
  is 540px, so there is about 256px of dead space below the box plus about 140px inside it,
  roughly 396px of blank beneath the "Go Home" button, with the block visibly sitting above
  centre. InviteAccept's 400px box leaves about 396px of blank below it; during the `accepting`
  state (`:53-58`) the visible page is a single spinner floating in the upper third.
- **Blast radius**: the 404 page and the invite-acceptance flow.
- **Intent source**: internal inconsistency; both are viewport- or pixel-relative heights
  inside a container that is already viewport-derived, contradicting
  `docs/UI/02-layout-shell.md` 禮3.3.

### F-29: The chatroom has no 1024-1279px layout and shows the agent rail where the spec calls for a drawer

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `useBreakpoint.ts:51-53` makes `isDesktop` `>= 1024` with no `xl` branch in
  the view. `ChatroomView.vue:5` binds only `chatroom--mobile` and `chatroom--tablet`; `:903`
  is the unconditional four-track desktop grid
  (`220px 1fr 10px var(--chatroom-rail-w, 200px)`); `:1009-1011` is the two-track tablet grid;
  the file's only `@media` is `prefers-reduced-motion` at `:968`. `ChatroomAgentSidebar`
  renders at `v-if="!isMobile"` (`:28`), i.e. from 768px up.
- **Failure scenario**: `/chatrooms/:id` at exactly 1024x768. Fixed chrome consumes
  220 + 10 + 200 = 430px, leaving about 594px for the message feed, so bubbles with code
  blocks and the composer's mention popover (`ChatroomComposer.vue:299-307`,
  `min-width: 180px`) crowd into a column narrower than the two rails combined, with no way to
  collapse either.
- **Blast radius**: iPad landscape and small laptops.
- **Intent source**: `docs/UI/07-conversation.md:238` (rails collapse to toggleable overlays
  at 1024-1279, with a worked `@media` block at `:241-252`) and `:258`; the second arm is
  supported by `docs/UI/11-responsive-a11y.md:121` but contradicted by `:118`, which is
  itself an intent-source conflict (see FU-4).

### F-30: `SEmptyState` has no vertical centring, so a stretched instance pins its content to the top

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `shared/ui/SEmptyState.vue:46-55` is
  `display: flex; flex-direction: column; align-items: center; margin: 0 auto` with no
  `justify-content`, so a stretched instance packs to the start.
  `slices/agents/views/GraphragGraphView.vue:167` roots on
  `flex flex-col h-[calc(100vh-3.5rem)]` and passes `class="flex-1"` at `:215-221` and
  `:223-229`.
- **Failure scenario**: `/projects/:pid/graphrag-configs/:cid/graph` for a config whose graph
  has zero nodes. `flex-1` grows the empty state to fill the column, but its children stack
  from the top, so the halo and "No entities yet" sit immediately under the summary line with
  roughly 600px of empty white beneath, reading as a broken page rather than an intentional
  empty state.
- **Blast radius**: any consumer that stretches the component; the same root cause as F-23.
- **Intent source**: `docs/UI/07-conversation.md:1018` is explicit about vertical centring;
  `docs/UI/12-shared-patterns.md` 禮6.1 only says "contextual empty state" and does not, so the
  intent is strong for the chatroom case and weaker elsewhere.

### F-31: The workflow editor never re-fits its canvas when the conditional bars above it appear

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `slices/workflow/views/WorkflowEditorView.vue:2` roots on
  `section class="workflow-editor flex flex-col h-full"`, and the load-error (`:120-132`),
  conflict (`:135-147`), lint-status (`:150-170`) and tablet-notice (`:173-178`) bars are all
  direct flex-column siblings of the canvas wrapper at `:205-208` (`flex flex-1 min-h-0`), so
  each shrinks the canvas when it appears. Grepping
  `fitView|fit-view|onResize|resize|updateNodeInternals|onPaneReady|useVueFlow` across the file
  yields exactly one hit: `fit-view-on-init` at `:230`. No re-fit hook exists.
- **Failure scenario**: `/workspaces/:wid/workflows/:wfid/edit` at 1366x768 with a graph fitted
  at init. Press Validate: the lint bar (`py-1`, about 24px) inserts above the canvas, the
  canvas shrinks by 24px with no viewport adjustment, and the bottom-most node clips below the
  fold. A save conflict adds a further 36px.
- **Blast radius**: the workflow editor, on every validation run.
- **Intent source**: `docs/UI/08-workflow.md` 禮2.1 (the bars are conditional zones in the flex
  column with the canvas at `flex: 1`) and 禮2.9 (`fit-view-on-init` viewport contract).
- **Narrowed during verification**: the tablet notice is static at mount and cannot crop. The
  three dynamic bars are the real triggers, and lint is the common one.

### F-32: `SNetworkBanner` is anchored to the viewport rather than the content area, so it covers the top bar

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `shared/ui/SNetworkBanner.vue:41-49` is `position: fixed; top: 0; left: 50%;
  transform: translateX(-50%); z-index: var(--z-banner, 350); width: min(640px, calc(100vw - 32px));
  margin-top: 12px`, rendered outside the layout at `App.vue:27`. `--z-banner` (350) exceeds
  `--z-topbar` (200) (`shared/styles/main.css:80,84`), and the top bar occupies y 0 to 56
  (`AppTopBar.vue:70-80`).
- **Failure scenario**: any authenticated route with the connection dropped. The 640px alert
  paints from y = 12 across the horizontal centre of the viewport, over the top bar's centre
  zone, for as long as the connection is down.
- **Blast radius**: every offline episode. Narrowed during verification: on desktop the
  org/project switcher lives in the sidebar and only renders in the top bar's centre zone
  under `v-if="isMobile"` (`AppTopBar.vue:52-57`), so the switcher occlusion is a mobile-only
  symptom. The spec deviation itself is unconditional.
- **Intent source**: `docs/UI/12-shared-patterns.md:323` verbatim: "SAlert variant='warning'
  fixed at top of **content area**".

### F-33: `ProjectKeysView`'s available-keys table has no loading state, so it reports zero keys while the fetch is in flight

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `slices/keys/views/ProjectKeysView.vue:221-225` renders the available-keys
  `STable` with no `:loading` binding while the sibling carried-keys table at `:136-141` does
  pass it. `carriable` (`:48-50`) derives from `useMyKeys()`, whose `data` starts `undefined`
  so `keys` is `[]` (`useMyKeys.ts:10-15`), which renders the empty state plus an Upload CTA
  at `:248-263`.
- **Failure scenario**: `/projects/:projectId/keys`, switch to the "Available" tab while the
  key list is still loading: the user is told they have no keys when they have several, then
  the table silently populates.
- **Why plausible rather than confirmed**: `STabs.vue:150` uses `v-if="modelValue === tab.key"`,
  and the default active tab is `carried` (`:43`), so the available panel is not mounted on
  first load; the flash only appears if the user switches tabs mid-flight. The always-visible
  symptom is the tab badge at `:54` reading 0 during load. Note also that the fix is not
  `:loading="loading"`: `loading` at `:39` belongs to `useProjectKeys` (the carried query), and
  `useMyKeys`'s own `loading` is never destructured at `:38`.
- **Blast radius**: one tab of one view.
- **Intent source**: `docs/UI/12-shared-patterns.md` 禮2.4 (loading table shows skeleton rows)
  and 禮6.1 (the empty state is for a settled empty result).

### F-34: `SkillWorkbench` passes a prop `SEmptyState` does not declare, so the empty-state body copy never renders

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `shared/ui/SEmptyState.vue:4-8` declares exactly `title`, `text` and `icon`,
  and renders the body from `text` at `:29-34`. `slices/skills/components/SkillWorkbench.vue:173`
  passes `:description`, which with default `inheritAttrs` falls through onto the root `div`
  at `:12` as a stray DOM attribute. A repository-wide grep for `:description` on a Vue
  component returns this single hit. The strings exist and are translated
  (`slices/skills/locales/en.json:115`, `zh-TW.json:115`).
- **Failure scenario**: `/projects/:projectId/skills` with skills present but none selected.
  The right-hand pane shows only "No skill selected"; the guidance "Pick a skill from the list,
  or create one." is dropped. The same call also passes no `:icon`, so the halo is skipped too,
  leaving one line of text in an otherwise empty bordered panel.
- **Blast radius**: the skills workbench empty state, in all three of its mounts.
- **Intent source**: `docs/UI/12-shared-patterns.md` 禮2.3 and 禮6.1 (empty state carries icon,
  title and description).

### F-35: `--z-toast` is declared and never consumed

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `shared/styles/main.css:86` declares `--z-toast: 500`, and a grep for
  `--z-toast` across `frontend/src` returns exactly that one hit, while every sibling token
  (`--z-sidebar`, `--z-topbar`, `--z-dropdown`, `--z-banner`, `--z-modal`, `--z-tooltip`) has
  a consumer. The sonner override block at `:399-440` styles colours only. The real value is
  whatever sonner hardcodes, `z-index: 999999999` at `node_modules/vue-sonner/lib/index.css:43`,
  once F-1 is fixed.
- **Failure scenario**: nothing in the codebase can ever place UI above a toast, because no
  project token can compete with a nine-digit value; and once the stylesheet is restored, a
  mobile toast (full-width top, `lib/index.css:385-397`) will paint over `SNetworkBanner` and
  its "Retry Now" button, which is deliberately layered "above chrome, below modals"
  (`main.css:82-84`).
- **Blast radius**: the whole z-index contract; latent until F-1 is fixed, at which point it
  becomes live.
- **Intent source**: `docs/UI/01-design-system.md` 禮z-index scale.

### F-36: The toast live region announces itself in English regardless of locale

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `App.vue:49-52` passes only `position` and `duration`. vue-sonner 2.0.9
  defaults `containerAriaLabel` to `"Notifications"` (`lib/index.js:944`) and renders
  `aria-label="${containerAriaLabel} ${hotkeyLabel}"` (`:1151`) where `hotkeyLabel` derives
  from the default `["altKey", "KeyT"]` (`:920,980`), producing exactly `"Notifications alt+T"`.
  The close button falls back to `"Close toast"` the same way.
- **Failure scenario**: a zh-TW user with a screen reader hears the region announced in English
  inside an otherwise Chinese page.
- **Blast radius**: screen-reader users on non-English locales. Unlike F-35 this is live today,
  because the attribute is set by JavaScript and does not depend on the missing stylesheet.
- **Intent source**: project rule "All user-facing strings go through `$t()`".

### F-37: Version conflicts are `warning` in half the app and `error` in the other half

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: spec-conformant `toast.warning`:
  `slices/tenancy/composables/useEntityLifecycle.ts:47,68`,
  `slices/conversation/composables/useChatroomSettings.ts:98-101`. Divergent `toast.error`:
  `slices/prompt-studio/composables/useTemplateEditor.ts:53-54`,
  `slices/prompt-studio/composables/useConfigEditor.ts:104-105`,
  `slices/skills/composables/useSkillEditor.ts:94-101,139`.
- **Failure scenario**: two editors open on a prompt template; the second save conflicts and
  the user gets a red six-second failure toast implying the save is broken, where the same
  event on `/orgs/:id` yields an amber five-second "someone else edited this, refreshing"
  message. Different diagnosis for identical mechanics.
- **Blast radius**: the prompt-studio and skills editors.
- **Intent source**: `docs/UI/12-shared-patterns.md:546` (warning, 5s, "version conflict") and
  禮4.3.
- **Tightening**: not all of these are 409. `useSkillEditor.ts:95` documents a 412, and
  prompt-studio keys on a typed `*/version-mismatch` problem rather than a status code. The
  severity inconsistency for the same semantic class holds regardless.

### F-38: Transient success is rendered as a persistent, focus-stealing banner

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `slices/identity/views/ProfileView.vue:153-159` renders
  `<SAlert v-if="saved" variant="success" focus-on-mount>`; `saved` is set at `:111` and
  cleared only at `:101` (next submit) or by `@input="saved = false"` at `:148`: no timer, not
  dismissible. `slices/admin/views/AdminOpsView.vue:27-34,66-73` follow the same shape, cleared
  only at `:112`/`:137`, and both carry `focus-on-mount`.
- **Failure scenario**: save a display name on `/profile`. The green "Saved" banner remains for
  the rest of the session, so a later visit still asserts a save that happened minutes ago; and
  because it is `focus-on-mount`, a transient success steals focus.
- **Blast radius**: two views.
- **Intent source**: `docs/UI/12-shared-patterns.md:544` (success is a 4s toast) and `:554`
  ("Never use toast for persistent states"), whose inverse applies here.

### F-39: Seventeen media-query blocks use an inclusive breakpoint value, applying the smaller layout one pixel early

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `sm` starts at 480 and `md` at 768 (`shared/composables/useBreakpoint.ts:5`), so
  mobile rules must stop at 479 and 767. Correct form:
  `shared/ui/SModal.vue:260`, `shared/ui/SDrawer.vue:162,169`, `app/layouts/AppShell.vue:202,216`,
  `slices/keys/views/KeyDetailView.vue:345`, `slices/notifications/components/NotificationCard.vue:181`.
  Inclusive form: `app/layouts/AuthLayout.vue:69`, `shared/ui/SAuthCard.vue:63`,
  `slices/tenancy/styles/detail-cards.css:99`, `slices/tenancy/views/OrgDetailView.vue:440,446`,
  `slices/tenancy/views/OrgTransferView.vue:430,436`,
  `slices/tenancy/views/InboxInvitesView.vue:300,306`,
  `slices/tenancy/views/InviteAcceptView.vue:155`, `slices/tenancy/styles/member-form.css:29`,
  `slices/identity/views/DeleteAccountView.vue:191`,
  `slices/identity/views/ChangePasswordView.vue:207`,
  `slices/identity/views/ChangeEmailView.vue:260`, `slices/identity/views/ProfileView.vue:328`,
  `slices/identity/views/SessionsView.vue:279`, and `app/views/Landing.vue:791`.
- **Failure scenario**: `/login` at exactly 480 CSS px, a common tablet split-view and DevTools
  preset. `AuthLayout.vue:69-79` applies the xs treatment (padding 0, `align-items: flex-start`,
  wrapper `max-width: none`) and `SAuthCard.vue:63-68` strips the border radius and shadow, so
  the user gets the edge-to-edge phone card at the width where the spec says the 420px shadowed
  card should already apply, while `useBreakpoint()` reports `sm`.
- **Blast radius**: a one-pixel band at two breakpoints. Re-counted during dossier writing as
  17 blocks across 14 files, superseding the "15 files" first reported.
- **Intent source**: `docs/UI/11-responsive-a11y.md` 禮1 breakpoint table and 禮2.3 (card is
  420px at `sm+`).

### F-40: Twenty-three slice views nest a second `<main>` landmark inside the shell's

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `app/layouts/AppShell.vue:123-132` renders
  `<main id="main-content" tabindex="-1" class="app-shell__content">`, and `App.vue:17-23`
  routes every `requiresAuth` view into it. A `<main` grep across `src/slices/**/*.vue` returns
  23 files, all in the agents, agent-groups, keys, conversation and activities slices, all
  `<main class="p-6">` roots except `GraphragGraphView.vue:168`. The tenancy, admin and
  identity slices have zero `<main>` hits and use `div`/`section`.
- **Failure scenario**: any of those routes with a screen reader. The landmark rotor lists two
  `main` regions, and the skip link at `AppShell.vue:89-92` targets the outer one, landing the
  user on a wrapper whose only child is a second main landmark.
- **Blast radius**: 23 views; also the source of the F-3 padding, since these are the same
  `p-6` roots.
- **Intent source**: internal inconsistency with the tenancy/admin/identity slices; HTML
  landmark semantics.

### F-41: `SModal` caps only its body height, so on wide-and-short viewports the title is clipped above the fold

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `shared/ui/SModal.vue:128-135` is
  `position: fixed; inset: 0; display: flex; align-items: center` with no `overflow` and no
  padding; only `.s-modal__body` is capped, at `max-height: 70vh` (`:218-222`). Header (`:174-179`)
  and footer (`:224-230`) are unbounded. No consumer overrides `.s-modal__panel`. A centred flex
  item that exceeds its container overflows equally in both directions, and the part above y = 0
  is unreachable because neither `.s-modal` nor `body` scrolls.
- **Failure scenario**: measured header is 52px (20px top padding plus a 28px title) and footer
  73px (32px padding, 1px border, 40px `SButton` min-height), so overflow requires
  `52 + 0.7H + 73 > H`, i.e. `H < about 417px`, plus a footer, plus body content long enough to
  hit the cap. At 844x390 (landscape phone) the width exceeds 767px so the mobile full-screen
  branch at `:260` does not apply, giving a 398px panel in a 390px viewport: about 4px of the
  title clipped and unreachable.
- **Why plausible**: the trigger window is narrow and the clipped amount at the most realistic
  viewport is small. 1920x1080 at 200% zoom does not overflow, and 1280x800 at 200% falls under
  767px into the full-screen branch.
- **Blast radius**: landscape phones and tablets, and roughly 250-300% zoom on wide short
  displays. The `aria-labelledby` target (the title) is what goes off-screen.
- **Intent source**: `docs/UI/11-responsive-a11y.md` 禮8 (the centred modal variant is required
  to remain fully visible) and 禮7.2 ("Verify at 200% zoom (no content overflow or overlap)").

### F-42: The mobile sidebar drawer is 320px where the spec says 280px, and overflows below 362px viewport width

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `AppShell.vue:113-121` renders the sidebar as `SDrawer size="sm"`;
  `shared/ui/SDrawer.vue:145-148` is `.s-drawer__panel--sm { width: 320px; max-width: 85vw }`
  with the responsive overrides at `:162-174` deliberately excluding `--sm`, so 320/85vw holds
  at every width; `:116` is `z-index: var(--z-modal)` (400). `docs/UI/11-responsive-a11y.md:58-59`
  specifies `min(280px, 85vw)` at `--z-sidebar` (100). Separately, `AppSidebar.vue:256-264`
  hard-sets `width: var(--sidebar-width)` (260px) and the drawer body adds 24px padding each
  side (`SDrawer.vue:221-225`), so with border-box sizing the fit threshold is
  `0.85W >= 308`, i.e. `W >= about 362px`.
- **Failure scenario**: at 375px the drawer fits. At 360px the sidebar overflows by 2px and
  `overflow-y: auto` with `overflow-x: visible` computes overflow-x to `auto`, adding a
  horizontal scrollbar sliver. At 320px it overflows by 36px and clips the right edge of every
  nav row.
- **Blast radius**: 320px-class devices for the clipping; the width deviation is unconditional.
- **Intent source**: `docs/UI/11-responsive-a11y.md:58-59`. The z-index deviation is arguably
  the better behaviour, since the drawer is modal with a backdrop and a focus trap, so only the
  width is unambiguously wrong.
- **Correction found during triage**: adopting the spec's `min(280px, 85vw)` on its own would
  make this *worse*, not better. The 260px sidebar plus 48px of drawer padding needs 308px of
  panel, which 280px never provides, so the overflow would become unconditional instead of
  appearing below about 362px. The width change must ship together with a `max-width: 100%`
  on the sidebar. Recorded in `docs/tasks/2026-08-19-mobile-viewport-and-breakpoints/`.

### F-43: The mobile bulk-action bottom sheet does not exist

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `shared/ui/STable.vue:219-228` renders `.s-table-bulk` on
  `selectable && selected.length > 0` with no breakpoint guard, positioned before both the
  `<table>` and the `<STableCards>` branch, and styled statically at `:470-479` with
  top-rounded corners and `border-bottom: none`, visually welded to a table that is not there
  in card mode. `isMobile` is consumed at `:74,80` only for the card-list switch. A
  case-insensitive grep for `bottom.?sheet|BottomSheet|s-sheet` across `frontend/src` returns
  zero hits.
- **Failure scenario**: none reachable today. **Corrected after triage**: writing
  `docs/tasks/2026-08-19-shared-overlay-and-shell-defects/` established that no `STable`
  consumer anywhere in the tree passes `selectable`, so the bulk bar has never rendered and
  the originally reported scenario (selecting keys on a phone and losing the action bar
  off-screen) cannot occur. What remains is a spec'd affordance that does not exist, not a
  broken one.
- **Blast radius**: none today; it becomes live for every selectable table on mobile the
  moment any view opts into `selectable`.
- **Intent source**: `docs/UI/12-shared-patterns.md:194` and `docs/UI/11-responsive-a11y.md:394`.

### F-44: Two admin sections add padding no sibling has, and short sections leave a tall blank column

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: all 13 `slices/admin/views/*.vue` roots are `<section class="admin-*">` with no
  padding utility, and `AdminLayout.vue:24-39` adds none. The only two `/admin/*` children with
  their own box are `slices/skills/views/AdminSkillsView.vue:26` (`<div class="p-6">`) and
  `slices/prompt-studio/views/AdminPromptStudioView.vue:12`
  (`<div class="mx-auto max-w-3xl p-4">`). Separately, `AdminLayout.vue:25-30` is
  `grid-template-columns: 220px minmax(0, 1fr); align-items: start` with a roughly 568px nav
  (`components/AdminNav.vue:28-42`, 13 items at `min-height: 40px` with a `.25rem` gap).
- **Failure scenario**: `/admin/users` to "Skills" at 1440x900: the section title jumps 24px
  right and 24px down; "Prompt Studio" instead jumps 16px and re-centres the content in a 48rem
  column while the rest of the console is full width. Separately, `/admin` renders a page header
  plus an `auto-fit` stat grid of roughly 175px against a 568px nav, leaving about 390px of
  blank to the right of the lower nav.
- **Blast radius**: the admin console.
- **Intent source**: `docs/UI/02-layout-shell.md` 禮9 route table (`/admin/*` at one padding
  value); internal inconsistency with the 13 sibling sections.

### F-45: The app shell is sized in `vh` where every other layout uses `dvh`

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `app/layouts/AppShell.vue:141` is `height: 100vh` while
  `app/layouts/AuthLayout.vue:27,77`, `app/layouts/PublicLayout.vue:14` and
  `app/views/Landing.vue:345` all use `100dvh`. `dvh` is available at the stated browser floor
  (`docs/UI/11-responsive-a11y.md:337-338`: iOS Safari 16.2+, Chrome Android 110+; `dvh`
  shipped in Safari 15.4 and Chrome 108).
- **Failure scenario**: on iOS Safari or Chrome Android with the URL bar expanded, the shell is
  taller than the visible viewport by the toolbar height, so the grid's bottom row (the chat
  composer, any bottom action row) is below the fold on first paint.
- **Why plausible, not confirmed**: two mitigations were found during verification. First,
  `docs/UI/02-layout-shell.md:101` itself specifies `height: 100vh`, so this is an
  intent-source conflict rather than a code-versus-intent defect. Second, there is no
  `html { overflow: hidden }` or `body { overflow: hidden }` anywhere
  (`shared/styles/main.css:147-153` sets only margin, background, colour and font; the single
  `overflow: hidden` at `:303` is inside `@utility visually-hidden`; `index.html` carries no
  styles), so the document is scrollable and the bottom row is reachable, and scrolling
  collapses the toolbar and self-corrects. The residue is first-paint clipping plus a spec that
  needs updating.
- **Blast radius**: mobile first paint on every authenticated route.
- **Intent source**: internal inconsistency with the three sibling layouts; see FU-6 for the
  spec half.

### F-46: The keyboard-inset calculation is measured against a different viewport from the element it shrinks

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `shared/composables/useVisualViewport.ts:26` computes
  `const overlap = window.innerHeight - vv.height - vv.offsetTop`, i.e. against the layout
  viewport, while the element it shrinks resolves `height: calc(100% - var(--kb-inset, 0px))`
  (`ChatroomView.vue:1015-1019`) against the `100vh` shell (`AppShell.vue:141`), the large
  viewport. The two differ by the URL-bar height.
- **Failure scenario**: with `100vh = 800`, `innerHeight = 750` (URL bar shown) and
  `vv.height = 400`, the inset computes to 350 and the chatroom becomes 450px tall inside a
  400px visible band, leaving about 50px of composer under the keyboard.
- **Why plausible**: the under-shoot is zero whenever the toolbar is already collapsed at
  keyboard-open time, which is common on focus, and `index.html:5` sets no `interactive-widget`,
  so the magnitude is browser- and state-dependent rather than guaranteed. Fixing F-45 also
  fixes this.
- **Blast radius**: mobile chatroom typing.
- **Intent source**: `docs/UI/11-responsive-a11y.md:127` ("Composer sticks to bottom above
  virtual keyboard (uses `visualViewport` API)").

### F-47: Approval cards render after every message instead of chronologically, and never raise the new-messages pill

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `ChatroomView.vue:117-125` renders `liveApprovals` as a flat `v-for` after the
  message `TransitionGroup` (`:94-115`), so their position is list order, not `created_at`.
  `messageCount` (`:696`) counts only `messages.value`, so `useChatroomScroll.ts:60-67` never
  fires for an approval.
- **Failure scenario**: the user scrolls up to re-read something while a workflow requests
  approval. The pending Approve/Reject card is appended at the very bottom, off-screen, with no
  pill telling them it exists. Once resolved, the card stays pinned below all messages instead
  of at the point in the conversation where it was requested.
- **Blast radius**: every approval-gated workflow run.
- **Intent source**: `docs/UI/07-conversation.md:988` ("approval cards are placed in the message
  feed at the chronological position where the approval was requested, interleaved with regular
  messages").
- **Corrected during verification**: an approval *does* auto-scroll a pinned user, because
  `useMarkdownEnhance`'s `onUpdated` hook runs `maybeStick` on that render. The defects are the
  ordering and the missing pill, not the auto-scroll.

### F-48: The chatroom search panel is offset 48px too far down, double-counting the header

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `.chatroom__feed` is `grid-row: 2` of a
  `grid-template-rows: 48px 1fr auto auto` (`ChatroomView.vue:903-925`) and is the
  `position: relative` containing block, so its top edge already sits below the 48px header.
  `slices/conversation/components/ChatroomSearchPanel.vue:95-99` then adds
  `position: absolute; top: 48px`, landing the panel 96px from the chatroom top.
- **Failure scenario**: Ctrl+K opens the panel with a bare 48px strip of messages (typically the
  "Load earlier" button) visible above it, instead of sliding down from directly below the
  header.
- **Blast radius**: chatroom search.
- **Intent source**: `docs/UI/07-conversation.md:742` ("Panel: slides down from below header,
  absolute positioned"); `:747`'s dimming overlay on the feed behind is also absent.
- **Narrowed during verification**: the clipping consequence is much weaker than first reported.
  The panel carries its own `max-height: 50vh; overflow-y: auto`, so its content stays
  scrollable; only the bottom band of the box is cut, and only when the feed height is less
  than `48px + 50vh`.

### F-49: The send path scrolls the feed directly instead of through the scroll composable

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `slices/conversation/composables/useChatroomMessages.ts:278-282` calls
  `listRef.value.scrollTo` on the raw element. The composable receives only
  `(chatroomId, listRef, mentionAgents, isModerator)` (`:36-47`) and has no access to
  `scrollToBottom`, which is what resets `newCount` and `atBottom`
  (`useChatroomScroll.ts:45-46`).
- **Failure scenario**: the user is scrolled up with the pill showing "3 new messages", then
  types and sends. The feed jumps to the bottom but the pill is stale until the browser
  dispatches the resulting `scroll` event.
- **Why plausible**: the failure self-heals. A programmatic `scrollTo` fires a real `scroll`
  event and `onScroll` (`:49-52`, bound at `:107`) resets both refs on the next frame, so the
  observable impact is at most a one-frame stale pill. The real cost is that this is a second
  copy of scroll-to-bottom logic with a different contract, which is how F-11 and F-13 drifted
  apart.
- **Blast radius**: cosmetic; the structural duplication matters more than the symptom.
- **Intent source**: internal inconsistency with `useChatroomScroll` owning this state.

### F-50: `STooltip` hardcodes `z-index: 50` instead of the `--z-tooltip` token

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `shared/ui/STooltip.vue:71-73` sets `position: absolute; z-index: 50` against
  the token `--z-tooltip: 600` at `shared/styles/main.css:87`.
- **Failure scenario**: no current manifestation. `main.app-shell__content` clips the tooltip
  long before it could reach the top bar (200) or the network banner (350), and nothing else
  with z 200 to 600 shares its stacking context.
- **Blast radius**: latent token-consistency defect; it becomes live the moment a tooltip is
  placed in a container that does not clip it.
- **Intent source**: `docs/UI/01-design-system.md` z-index scale.
- **Note**: the stronger claim that a first-row table tooltip is clipped to nothing was
  refuted; see 禮4.

### F-51: `AgentDetailView`'s sticky prompt panel is sized with a constant that under-fills the viewport

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `slices/agents/views/AgentDetailView.vue:964` uses
  `lg:sticky lg:top-6 lg:self-start lg:h-[calc(100vh-8rem)]`. The `8rem` encodes
  56px topbar + 24px shell padding + 24px sticky offset + 24px bottom gap = 128px, but the view
  root at `:665` adds a further `p-6` (see F-3), so the arithmetic is short by 48px.
- **Failure scenario**: `/agents/:id` on the Prompt tab at 1440x900. The scrollport is 844px and
  the panel is 772px sticking at `top: 24px`, so its bottom edge sits about 48px above the
  scrollport bottom: a dead band under the panel for the entire scroll. The correct fill is
  `calc(100vh - 3.5rem - 3rem)`.
- **Blast radius**: one panel on one view.
- **Intent source**: internal inconsistency; the `8rem` constant counts the content padding once
  while the DOM applies it twice.

### F-52: `AgentGroupDetailView` is the only detail view with a page-level max-width, so drilling in collapses the page

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `slices/agent-groups/views/AgentGroupDetailView.vue:143` is
  `mt-6 space-y-6 max-w-2xl` (672px) while its list view is full width
  (`AgentGroupListView.vue:162,185-191`).
- **Failure scenario**: `/projects/:pid/agent-groups` at 1920x1080, click a group. The table
  spans roughly 1830px; the detail page's cards collapse to 672px hugging the left edge,
  leaving about 1150px of empty white to the right, including a member table that now wraps.
- **Why plausible rather than confirmed**: the original spec citation was misapplied.
  `docs/UI/02-layout-shell.md` 禮3.3's "Max-width: none" describes `AppShell`'s own content
  region, not a prohibition on views constraining themselves, and constrained content is
  common here (`SessionsView.vue:213-215`, `InboxInvitesView.vue:225,232`,
  `OrgTransferView.vue:386`, `InviteAcceptView.vue:114`, `AdminPromptStudioView.vue:12`).
  Among `*DetailView.vue` files, however, this is the only page-level cap, so the finding is an
  inconsistency across detail views rather than a spec violation.
- **Blast radius**: one view on wide displays.
- **Intent source**: internal inconsistency across detail views.

## 4. Refuted Candidates

Kept because each refutation is itself worth not re-discovering.

- **`useFocusTrap`'s body scroll lock is broken.** It really does lock `document.body`
  (`shared/composables/useFocusTrap.ts:11-19`) and body really is not the AppShell scroller,
  but both consumers Teleport to `body` and cover the viewport
  (`SModal.vue:62,128-141`, `SDrawer.vue:48,113-124`, both `position: fixed; inset: 0`), so the
  dialog sits outside `main`'s subtree: wheel and touch over the backdrop target a
  non-scrollable fixed element and chain to `body`/`html`, never to `main`. Over-scroll out of
  `.s-modal__body` chains the same way. The page behind an open dialog is not scrollable today.
  The lock is redundant on app routes and load-bearing on the auth/public layouts.
- **`<Transition mode="out-in">` clamps `main.scrollTop` to 0 on navigation.** It does not.
  Vue removes the leaving node and calls `instance.update()` synchronously in the same
  `afterLeave` task, so the browser never lays out an empty `main`. This refutation made F-4
  worse, not better: scroll offset genuinely persists into the new view.
- **`WorkflowListView` produces two toasts for one failure via `window.onunhandledrejection`.**
  Vue routes native-event-handler rejections through `callWithAsyncErrorHandling` into the
  `errorCaptured` chain, and `ErrorBoundary.vue:17-30` returns `false` for everything except
  `AuthError`, stopping propagation before `errorHandler.ts:50-54` can see it. The real
  consequence is recorded as F-21.
- **A first-row table tooltip is clipped to nothing by `.s-table-wrap`.** The clipping
  mechanism is real, but the geometry fails: a first-row trigger has 8px of cell padding plus
  the whole `<thead>` above it inside the same wrapper (about 39-41px of space) against a
  tooltip needing about 31px. It fits, in both cited consumers. The residual token defect is
  recorded as F-50.
- **`AppSidebar` creates two nested scrollports inside `AppShell`.** The inner element uses
  `var(--sidebar-width)` and `height: 100%` against a definite grid row, so it is exactly the
  aside's height and clips its own content; the outer can never overflow. The desktop aside and
  the mobile drawer are also mutually exclusive (`v-if="isDesktop"` / `v-if="!isDesktop"`), so
  the nesting never coexists with the drawer case. The drawer-width half survives as F-42.
- **`SModal` decides "mobile" twice and the two can disagree.** The facts hold, but the
  divergence needs a space-consuming document scrollbar, and there is none where SModal lives:
  `AppShell` is `overflow: hidden` and `main.css:147-153` gives `html, body` only `margin: 0`.
  The only document-scrolling layouts are the unauthenticated ones, which render no SModal.
- **A chatroom with two messages leaves wrong blank space at the bottom.** No spec line requires
  bottom-anchoring a short feed, and `scrollToBottom` being a no-op without overflow is correct.
  Only the zero-message case is a defect (F-23).
- **An approval card arriving while scrolled up produces no auto-scroll.** It does auto-scroll a
  pinned user, via `useMarkdownEnhance`'s `onUpdated` to `maybeStick`. The ordering and pill
  halves survive as F-47.

## 5. Hand-off

Triaged 2026-08-19. The user elected to act on all findings, so every one is assigned rather
than sampled. The five dossiers are grouped by blast radius so that concurrent builds cannot
produce conflicting diffs; their `depends_on` chain
(feedback-channels, then shared-overlay, then content-area-spacing, then mobile-viewport)
encodes **file overlap**, not logical sequencing. `chatroom-scroll-and-composer` is
independent and can be built in parallel with the whole chain (its Q-11 records the file-by-file
overlap check that justifies `depends_on: []`).

Two findings are not fixed by a bugfix dossier and say so explicitly rather than leaving a
blank row: **F-43** is a spec'd affordance that was never built and whose failure scenario is
unreachable today, so it is routed out to a feature dossier that does not yet exist;
**F-52** is deferred, because verification withdrew its intent source and it is a width-policy
question for `docs/UI/12-shared-patterns.md` rather than a shell-contract violation. This audit
therefore stays at `reviewed` rather than `closed`: F-43 has a decision but no linked dossier.

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | fix | `docs/tasks/2026-08-19-transient-feedback-channels/` |
| F-2 | fix | `docs/tasks/2026-08-19-transient-feedback-channels/` |
| F-3 | fix | `docs/tasks/2026-08-19-content-area-spacing-and-scroll-contract/` |
| F-4 | fix | `docs/tasks/2026-08-19-content-area-spacing-and-scroll-contract/` |
| F-5 | fix | `docs/tasks/2026-08-19-shared-overlay-and-shell-defects/` |
| F-6 | fix | `docs/tasks/2026-08-19-shared-overlay-and-shell-defects/` |
| F-7 | fix | `docs/tasks/2026-08-19-shared-overlay-and-shell-defects/` |
| F-8 | fix | `docs/tasks/2026-08-19-shared-overlay-and-shell-defects/` |
| F-9 | fix | `docs/tasks/2026-08-19-shared-overlay-and-shell-defects/` |
| F-10 | fix | `docs/tasks/2026-08-19-content-area-spacing-and-scroll-contract/` |
| F-11 | fix | `docs/tasks/2026-08-19-chatroom-scroll-and-composer/` |
| F-12 | fix | `docs/tasks/2026-08-19-chatroom-scroll-and-composer/` |
| F-13 | fix | `docs/tasks/2026-08-19-chatroom-scroll-and-composer/` |
| F-14 | fix | `docs/tasks/2026-08-19-chatroom-scroll-and-composer/` |
| F-15 | fix | `docs/tasks/2026-08-19-chatroom-scroll-and-composer/` |
| F-16 | fix | `docs/tasks/2026-08-19-content-area-spacing-and-scroll-contract/` |
| F-17 | fix | `docs/tasks/2026-08-19-content-area-spacing-and-scroll-contract/` |
| F-18 | fix | `docs/tasks/2026-08-19-mobile-viewport-and-breakpoints/` |
| F-19 | fix | `docs/tasks/2026-08-19-transient-feedback-channels/` |
| F-20 | fix | `docs/tasks/2026-08-19-transient-feedback-channels/` |
| F-21 | fix | `docs/tasks/2026-08-19-transient-feedback-channels/` |
| F-22 | fix | `docs/tasks/2026-08-19-shared-overlay-and-shell-defects/` |
| F-23 | fix | `docs/tasks/2026-08-19-chatroom-scroll-and-composer/` |
| F-24 | fix | `docs/tasks/2026-08-19-chatroom-scroll-and-composer/` |
| F-25 | fix | `docs/tasks/2026-08-19-mobile-viewport-and-breakpoints/` |
| F-26 | fix | `docs/tasks/2026-08-19-content-area-spacing-and-scroll-contract/` |
| F-27 | fix | `docs/tasks/2026-08-19-content-area-spacing-and-scroll-contract/` |
| F-28 | fix | `docs/tasks/2026-08-19-content-area-spacing-and-scroll-contract/` |
| F-29 | fix | `docs/tasks/2026-08-19-chatroom-scroll-and-composer/` |
| F-30 | fix | `docs/tasks/2026-08-19-shared-overlay-and-shell-defects/` |
| F-31 | fix | `docs/tasks/2026-08-19-content-area-spacing-and-scroll-contract/` |
| F-32 | fix | `docs/tasks/2026-08-19-transient-feedback-channels/` |
| F-33 | fix | `docs/tasks/2026-08-19-shared-overlay-and-shell-defects/` |
| F-34 | fix | `docs/tasks/2026-08-19-shared-overlay-and-shell-defects/` |
| F-35 | fix | `docs/tasks/2026-08-19-transient-feedback-channels/` |
| F-36 | fix | `docs/tasks/2026-08-19-transient-feedback-channels/` |
| F-37 | fix | `docs/tasks/2026-08-19-transient-feedback-channels/` |
| F-38 | fix | `docs/tasks/2026-08-19-transient-feedback-channels/` |
| F-39 | fix | `docs/tasks/2026-08-19-mobile-viewport-and-breakpoints/` |
| F-40 | fix | `docs/tasks/2026-08-19-content-area-spacing-and-scroll-contract/` |
| F-41 | fix | `docs/tasks/2026-08-19-shared-overlay-and-shell-defects/` |
| F-42 | fix | `docs/tasks/2026-08-19-mobile-viewport-and-breakpoints/` |
| F-43 | route out as a feature; failure scenario unreachable today (no `STable` consumer passes `selectable`) | none yet; see `docs/tasks/2026-08-19-shared-overlay-and-shell-defects/` Q-row and AC-12 |
| F-44 | fix | `docs/tasks/2026-08-19-content-area-spacing-and-scroll-contract/` |
| F-45 | fix | `docs/tasks/2026-08-19-mobile-viewport-and-breakpoints/` |
| F-46 | fix | `docs/tasks/2026-08-19-mobile-viewport-and-breakpoints/` |
| F-47 | fix | `docs/tasks/2026-08-19-chatroom-scroll-and-composer/` |
| F-48 | fix | `docs/tasks/2026-08-19-chatroom-scroll-and-composer/` |
| F-49 | fix | `docs/tasks/2026-08-19-chatroom-scroll-and-composer/` |
| F-50 | fix | `docs/tasks/2026-08-19-shared-overlay-and-shell-defects/` |
| F-51 | fix | `docs/tasks/2026-08-19-content-area-spacing-and-scroll-contract/` |
| F-52 | defer; intent source withdrawn on verification | deferral recorded in `docs/tasks/2026-08-19-content-area-spacing-and-scroll-contract/` (Q-15, AC-13, FU-4) |

## 6. Out-of-scope Observations

- **FU-1** - `AppShell.vue:18-23` hardcodes `/\/workflows\/[^/]+\/edit$/` and the chatroom path
  regex, duplicating the `sidebarCollapsed` / `contentPadding` meta that
  `slices/workflow/routes.ts:14` and `slices/conversation/routes.ts:26` already declare, against
  `docs/UI/02-layout-shell.md` 禮9's statement that meta is the single source. Not a present-day
  visual defect, but a divergence hazard and the reason the two graph routes (F-10) got neither
  treatment. Route to `check-quality`.
- **FU-2** - `docs/UI/02-layout-shell.md` 禮6 and 禮9 say `/` redirects an authenticated user to
  `/orgs`, but `app/router.ts:26-30` gives it `layout: 'public'` and `app/views/Landing.vue:190-215`
  renders an authenticated hero. Verification concluded the code is the better intent: the hero
  offers a resume-last-chatroom deep link (`:199-206`) that a blind redirect would destroy, and
  `app/__tests__/Landing.test.ts:58` deliberately pins it. File as a spec update, not a code fix.
- **FU-3** - `docs/UI/11-responsive-a11y.md` 禮2.1 specifies four content-padding tiers
  (24/24/16/12/8) while `docs/UI/02-layout-shell.md` 禮3.3 and 禮8 specify three (24/16/8). The
  implementation faithfully follows the latter; the two documents disagree only in the 480-767px
  band. Doc fix, not a code defect.
- **FU-4** - `docs/UI/11-responsive-a11y.md:118` calls the `md` chatroom layout "2-column" while
  `docs/UI/07-conversation.md:258` says panels become drawers below 1024px, and `:121` of the
  same table says the agent list is a drawer at `md`. The table contradicts itself and the
  conversation spec. Resolve before acting on F-29.
- **FU-5** - `frontend/src/shared/composables/__tests__/useServerErrors.test.ts:42` pins the
  wrong wire contract with a hand-written fixture (see F-2), and
  `frontend/e2e/11-mcp.spec.ts:57` documents the resulting symptom as accepted behaviour. Both
  should be rewritten against a real backend 422 payload when F-2 is fixed. Route to
  `check-quality`.
- **FU-6** - `docs/UI/02-layout-shell.md:101` mandates `height: 100vh` for the shell. Given the
  stated browser floor supports `dvh` and three of the four layouts already use it, the spec
  should be updated alongside any F-45 fix rather than the code silently diverging.
- **FU-7** - `frontend/e2e` asserts toast text with `toBeVisible()`, which passes for any
  non-empty box regardless of viewport position. That is why F-1 survived. Consider an assertion
  on the toast container's computed `position` or its bounding box relative to the viewport.
  Route to `check-quality`.
