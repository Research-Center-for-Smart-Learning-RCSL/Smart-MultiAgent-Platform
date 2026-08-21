---
type: bugfix
status: in-progress
created: 2026-08-19
approved: 2026-08-21
requirements: []
depends_on: [2026-08-19-shared-overlay-and-shell-defects]
---

# Content-area spacing and the scroll-position contract

Source: `docs/audits/2026-08-19-page-presentation-scroll-and-feedback/findings.md`
(F-3, F-4, F-10, F-16, F-17, F-26, F-27, F-28, F-31, F-40, F-44, F-51, F-52).

## 1. Summary

`main.app-shell__content` is the one element in the authenticated app that owns page padding
and page scrolling. `docs/UI/02-layout-shell.md` §3.3 assigns it both, and §9 makes route
`meta.contentPadding` the only sanctioned opt-out. Thirteen defects come from views that
either duplicate that padding, size themselves against the viewport instead of against the
box the shell gave them, or leave the shell's scroll offset unmanaged. The two largest:
**F-3**, where 34 of 74 view roots add a second padding layer so the documented 24px gutter is
actually 48px on 26 routes and 40px on 7 more, and 32px on a small phone where the spec says
8px; and **F-4**, where nothing anywhere resets `main.scrollTop`, so the previous page's scroll
offset is inherited by the next view and a user navigating off a long admin table lands in the
middle of the next one with its page header off-screen.

Eleven smaller defects are the same mistake in other shapes: a graph canvas that recomputes
`100vh - topbar` and forgets the padding (F-10), a sticky panel whose constant counts that
padding once (F-51), two views that centre themselves inside an arbitrary `60vh` or `400px` box
instead of the box they were given (F-28), a panel whose declared scroll region only functions
above `lg` (F-16), a page that renders nothing but a select until a run is chosen (F-17), three
detail views that show a 24px spinner over a blank viewport (F-26), three skeletons taller than
the content they stand in for (F-27), a canvas that never re-fits when its own chrome shrinks it
(F-31), 23 nested `<main>` landmarks (F-40), and two admin sections padded unlike their eleven
siblings (F-44). F-52 is triaged and deferred (Q-15).

This dossier is the second of the audit's structural causes of the reported "too much empty
space at the bottom" complaint. The first, F-1's unstyled toast layer, belongs to
`2026-08-19-transient-feedback-channels`; F-3 is what remains of the symptom after it.

### 1.1 Freshness re-verification (2026-08-21)

Every citation was re-checked. **No finding changed status; all thirteen still reproduce.**
`AppShell.vue` - the file this whole dossier is written around - is byte-for-byte as cited:
`:45-51` (the watcher Q-4 extends), `:67`, `:75`, `:123`, `:183`, `:188`, `:207-209`,
`:216-218` all hold, and **`:141` is still `height: 100vh`**, so Q-14's warning about the
viewport unit is live rather than hypothetical and the sibling dossier's F-45 has not landed.

Three corrections, all caused by `2026-08-19-chatroom-scroll-and-composer` and
`2026-08-20-member-groups-and-room-visibility-isolation` landing in files this dossier cites:

1. **F-4's scroll-writer list is out of date, in this dossier's favour.** The sweep now returns
   five writes in two files, not six in three: `useChatroomScroll.ts:85,87,127` (was `:41,43,102`)
   and `PromptAssistantPanel.vue:118` (unchanged). **`useChatroomMessages.ts:281` is gone
   entirely** - the chatroom dossier's F-49 removed the raw `scrollTo` and replaced it with an
   `onSent` callback, so that composable no longer touches a DOM element at all. F-4's claim
   that nothing resets `main.scrollTop` is unaffected and now has one fewer competing writer to
   reason about.
2. **`ChatroomSettingsView.vue`'s root moved from `:227` to `:317`** (the member-groups dossier
   added an access-flag block above it). It is still `<main class="p-6 settings">`, so it is
   still in F-3's and F-40's sets and Q-3's instruction to keep the `settings` class is
   unchanged - only the line number is wrong.
3. **Line drift in `ChatroomView.vue`**: the root spans `:2-14` rather than `:2-10` (a third
   class binding was added) and still carries no padding utility, so F-3's "neither immersive
   view is in the padded set" holds. §9's `h-full` precedent citation `:905` is now `:1086`.

Unchanged and re-confirmed: `slices/conversation/routes.ts:26` still declares
`contentPadding: 'none'`; `slices/workflow/routes.ts:14` likewise; the 34-root padded set and
the 23 `<main>` roots are as enumerated.

**Sequencing note.** `depends_on` is still unmet: `2026-08-19-shared-overlay-and-shell-defects`
remains `draft`, so this dossier stays Blocked. That is also what makes Q-14 answerable - the
sibling is what moves `AppShell.vue:141` to `dvh`.

### 1.2 Second freshness re-verification (2026-08-21, after the sibling landed)

`2026-08-19-shared-overlay-and-shell-defects` reached `status: implemented` on 2026-08-21, so
**`depends_on` is now met and this dossier is unblocked**. It edited `AppShell.vue` and
`App.vue`, which this dossier cites heavily. Every citation was re-checked.

**The whole of F-4's fix site is byte-identical.** `AppShell.vue:45-51` (the watcher Q-4
extends), `:53-55` (`noPadding`), `:67` (`contentEl`), `:75` (the only read of
`contentEl.scrollTop`), `:89-92` (the skip link), `:18-23` (the immersive regexes) and `:123`
(`<main id="main-content">`) are all exactly where this dossier says they are. Only the
`<style>` block moved, because the sibling added comment lines above it.

**Line drift, rules unchanged** (the padding ladder is byte-identical as CSS; only its
position in the file moved):

| Cited | Now |
|---|---|
| `AppShell.vue:183-189` | `:195-201` |
| `AppShell.vue:188` (`padding: 24px`) | `:200` |
| `AppShell.vue:207-209` (16px below 1024) | `:219-221` |
| `AppShell.vue:216-218` (8px below 480) | `:229-231` |
| `AppShell.vue:139-141` | `:148-152` |
| `App.vue:17-23` (`layoutComponent`) | `:22-33` |
| `App.vue:30` (`<component :is>`, still no `key`) | `:43` |
| `App.vue:34-44` (`<Transition mode="out-in">`) | `:52-60` |
| `App.vue:31-41` (`:key="$route.path"` and its comment) | `:48-58` |

**Three material changes**, each corrected in place below rather than left for the builder:

1. **`AppShell.vue:141`'s `height: 100vh` no longer exists.** The sibling replaced it with
   `flex: 1 1 0px; min-height: 0` (now `:148-152`) and moved the viewport unit up to
   `App.vue:79`'s `.app-root { min-height: 100vh }`. Q-6 and Q-14 both reasoned from that
   line; both are rewritten. Q-14's premise was **already wrong** for a second, independent
   reason - see its rationale.
2. **`<ErrorBoundary>` now sits inside the layout**, wrapping only `<router-view>`
   (`App.vue:47-62`). F-4's root-cause chain is unaffected - it turns on `App.vue` returning
   the same `AppShell` object for every authenticated route and passing no `key`, which is
   still true at `:22-33` and `:43` - but any reading of that chain should not expect the
   boundary outside the layout any more.
3. **§6's viewport-unit sweep still returns seven hits, with one different member.**
   `AppShell.vue:141` is gone; `App.vue:79` takes its place. The conclusion is unchanged and
   is what matters: **only two of the seven are inside the content area**
   (`GraphragGraphView.vue:168` = F-10, `AgentDetailView.vue:964` = F-51), and both are this
   dossier's. Re-swept list: `App.vue:79`, `AuthLayout.vue:27,77`, `PublicLayout.vue:14`,
   `Landing.vue:345`, plus those two.

**One thing the sibling makes stronger rather than weaker.** Its D-10 proved in a browser that
`main.app-shell__content` is the page's scroll owner and pinned it with a test that goes red if
it stops being one: forcing the pre-fix `flex: 1` gives 3805px of shell and 3385px of *document*
scroll, and `frontend/e2e/21-overlay-and-shell-contract.spec.ts` now asserts the content area
scrolls and the document does not. This dossier's central premise is therefore measured rather
than only cited, and F-4's reset has a verified scroll container to write to.

### 1.3 Build-time re-verification (2026-08-21, at approval)

Every citation was swept a third time, immediately before implementation. **No finding changed
status.** `AppShell.vue` is byte-identical to §1.2's description: the watcher at `:45-51`,
`noPadding` at `:53-55`, `contentEl` at `:67`, the sole `scrollTop` read at `:75`, the skip link
at `:89-92`, `<main id="main-content">` at `:123-132`, and the padding ladder at `:195-201`,
`:219-221`, `:229-231`. Re-measured: **34 padded view roots** (26 `p-6`, 7 `p-4`, one
`px-4 py-4 sm:p-6`) exactly as §2 enumerates; **23 `<main>` in `src/slices`**, 25 across `src`
(the other two being `AppShell.vue:123` and `Landing.vue:183`); **7 viewport-unit hits** with the
§1.2 membership; `scrollBehavior` returns nothing repository-wide.

Four corrections, applied in place below rather than left for the builder:

1. **Q-5's testability premise is factually inverted, and the fix it specifies would throw.**
   Measured directly against this repo's jsdom: `typeof el.scrollTo` is `undefined` and
   `'scrollTo' in Element.prototype` is `false`, while `el.scrollTop = 123` reads back as `123`.
   So `scrollTop` is *not* inert and `scrollTo` is *not* present. Writing
   `contentEl.value?.scrollTo({ top: 0 })` would raise a TypeError inside the watcher on every
   unit test that navigates, starting with `AppShell.test.ts`'s existing
   "resets the manual override on navigation". The repository already records this constraint at
   `slices/conversation/composables/useChatroomScroll.ts:83` ("jsdom (tests) has no
   Element.scrollTo, so fall back to scrollTop"). Q-5 and T-1 are rewritten to
   `contentEl.value.scrollTop = 0`.
2. **The Tier 2 spec filename collides.** `frontend/e2e/19-transient-feedback.spec.ts`,
   `20-onboarding-without-smtp.spec.ts` and `21-overlay-and-shell-contract.spec.ts` already
   exist. The new spec is **`frontend/e2e/22-layout-contract.spec.ts`**; §8 and AC-16 are
   corrected.
3. **§7 item 7's "move `SPageHeader` above the loading branch" is not literally possible.** All
   three F-26 views derive the header title from the fetched entity
   (`ProjectDetailView` `project.name`, `OrgDetailView` `org.name`, `AdminUserDetailView`
   `query.data.value.email`), which does not exist during the fetch. `SPageHeader`'s `title` prop
   is a required `string` (`shared/ui/SPageHeader.vue:6-10`) and it has no title slot. Item 7 is
   restated with the fallback rule.
4. **75 views, not 74.** `slices/tenancy/views/ProjectMemberGroupsView.vue` landed with
   `2026-08-20-member-groups-and-room-visibility-isolation`. It carries no root padding, so the
   34/41 split leaves F-3's set unchanged.

Two things checked because §9 warns about them, both clear: **no unit test anywhere in
`frontend/src` selects on `main`** (`getByRole('main')`, `find('main')` and `querySelector('main')`
all return nothing), so the 23 root changes break no existing assertion; and the e2e specs' `main`
usages are either `main#main-content` (`21-overlay-and-shell-contract.spec.ts`) or descendant
selectors (`main table`, `main .s-page-header` in `09`, `10`, `11`, `16`), all of which continue to
match against the shell's own landmark.

## 2. Observed vs Expected

### F-3 (major) - 34 view roots duplicate the shell's padding

- **Observed** - the shell owns content padding at `frontend/src/app/layouts/AppShell.vue:188`
  (24px), `:207-209` (16px below 1024px) and `:216-218` (8px below 480px). Enumerating the
  template root of all 74 `**/views/*.vue` files, 34 add a second padded root and 40 do not.
  The corrected split (the audit reported 28/5/1; the tree says otherwise) is **26 with `p-6`**
  (24px), **7 with `p-4`** (16px), and one with `px-4 py-4 sm:p-6`
  (`slices/notifications/views/NotificationsView.vue:13`). `p-6` is 24px per
  `shared/styles/main.css:5-6`. Only two routes opt out of shell padding
  (`slices/workflow/routes.ts:14`, `slices/conversation/routes.ts:26`) and neither of those two
  views is in the padded set (`WorkflowEditorView.vue:2` is `flex flex-col h-full`,
  `ChatroomView.vue:2-10` carries no padding utility), so all 34 stack on top of the shell's.
  Representative padded roots: `slices/agents/views/AgentListView.vue:244`,
  `slices/keys/views/KeyListView.vue:128`, `slices/workflow/views/WorkflowRunView.vue:2`,
  `slices/prompt-studio/views/PersonalPromptStudioView.vue:12`. Unpadded controls that render
  directly into `main`: `slices/tenancy/views/OrgListView.vue:89`,
  `slices/admin/views/AdminUsersView.vue:2` through `slices/admin/views/AdminLayout.vue:24-39`,
  which is a bare grid with `gap` only.
- **Expected** - `docs/UI/02-layout-shell.md` §3.3 ("Padding: 24px on desktop, 16px on mobile")
  and the §9 route table, which lists a single Content Padding value per route pattern and names
  `meta.contentPadding` as its implementation. One owner, one value per route.

### F-4 (major) - nothing resets the scroll position on navigation

- **Observed** - `frontend/src/app/router.ts:50-53` creates the router with `history` and
  `routes` only; there is no `scrollBehavior` anywhere in `frontend/src` (repository-wide grep
  for `scrollBehavior` returns nothing). `frontend/src/app/guards.ts` contains no reference to
  `document`, `window` or `scroll`. The only writes to a `scrollTop`/`scrollTo` in the whole of
  `frontend/src` are the chatroom and prompt-studio internal scrollers
  (**re-swept 2026-08-21**: `slices/conversation/composables/useChatroomScroll.ts:85,87,127`
  and `slices/prompt-studio/components/PromptAssistantPanel.vue:118` - the third site,
  `useChatroomMessages.ts:281`, was removed by that dossier's F-49 and no longer exists);
  `AppShell.vue:75` only reads
  `contentEl.scrollTop` for the topbar shadow. `AppShell` is never remounted between
  authenticated routes: `App.vue:17-23` returns the same component object for all of them and
  `<component :is="layoutComponent">` at `:30` carries no `key`, so the `<main>` element at
  `AppShell.vue:123-132` and its `scrollTop` persist across every navigation. The audit refuted
  the hypothesis that `<Transition mode="out-in">` (`App.vue:34-44`) clamps the offset: Vue
  removes the leaving node and calls `instance.update()` in the same task, so the browser never
  lays out an empty `main`.
- **Expected** - `docs/UI/02-layout-shell.md` §3.3 designates the content area as the scroll
  owner; `docs/UI/12-shared-patterns.md` §8.3 requires detail pages to be reachable without a
  browser-back dependency. The shell defines a scroll container that the routing layer has no
  contract with. There is no SRS line covering scroll reset, so the expected behaviour is agreed
  in Q-4 and Q-5 rather than cited.

### F-10, F-16, F-17, F-26, F-27, F-28, F-31, F-40, F-44, F-51, F-52

| ID | Observed | Expected |
|---|---|---|
| F-10 | `slices/agents/views/GraphragGraphView.vue:168` is `<main class="p-6 flex flex-col h-[calc(100vh-3.5rem)]">`. `3.5rem` matches `--topbar-height` (`shared/styles/main.css:66`) but the content box is `100vh - 56 - 48`, because `AppShell.vue:188` adds 24px top and bottom and neither graph route opts out (`slices/agents/routes.ts:34-40`, `:53-59`; `AppShell.vue:53-55` zeroes padding only for `contentPadding: 'none'` or the two immersive path patterns). The view declares 48px more height than exists, so `main` scrolls 48px on a fixed-height canvas page and wheel events near the canvas edge move `main` | `docs/UI/02-layout-shell.md` §3.1 and §3.3: the shell sizes the content box; a view fills it rather than recomputing it |
| F-16 | `slices/prompt-studio/components/PromptAssistantPanel.vue:123` roots on `flex h-full flex-col` with the message list at `:145` (`flex-1 ... overflow-y-auto`) and the composer as the last flex child at `:208-228`. Its only mount site, `slices/agents/views/AgentDetailView.vue:960-966`, supplies a height only under `lg:` (`:964`, `min-h-[32rem] lg:sticky lg:top-6 lg:self-start lg:h-[calc(100vh-8rem)]`), and the parent grid at `:929` is `grid grid-cols-1 gap-6 lg:grid-cols-[1fr_22rem]`, so below 1024px the cell height is auto, `h-full` resolves against an indefinite height, and the list grows to content | Internal inconsistency: a component that declares a scroll region at `:145` must have that region function in both of its layout modes |
| F-17 | `slices/workflow/views/WorkflowBackstageView.vue:23` opens `<template v-if="selectedRunId">` wrapping every section through `:131`, with no `v-else`; `:154` initialises `ref('')` and `runOptions` (`:181-187`) deliberately leads with an empty-value entry whose label is a single em-dash character, per the comment at `:179-180`. The step-trace loading indicator at `:29-34` is a literal `…` | `docs/UI/12-shared-patterns.md` §6.1 (contextual empty state) and §5.1 (structural skeleton, not a text placeholder) |
| F-26 | `shared/ui/SLoadingSpinner.vue:53-59` is `display: flex; align-items: center; gap: .5rem` with no min-height and no centring, and a 24px icon at `:70-73`. Three views put it in place of the whole template, page header included: `slices/tenancy/views/ProjectDetailView.vue:103-106`, `slices/tenancy/views/OrgDetailView.vue:111-114`, `slices/admin/views/AdminUserDetailView.vue:3-7` | `docs/UI/12-shared-patterns.md` §5.1. The audit's own clearing criterion is the pattern at `slices/admin/views/AdminHomeView.vue:3-9` (header first, then a bounded indicator) and `slices/agent-groups/views/AgentGroupDetailView.vue:126-131` (`flex justify-center py-16`) |
| F-27 | `slices/agents/views/AgentDetailView.vue:667-680` renders one default skeleton, five 80x32px rects and two more default skeletons against a loaded General tab of two `SCard`s of form fields. `slices/tenancy/views/InboxInvitesView.vue:139-149` renders three 120px rects in a 12px-gap column (`:221-226`) against a possible `SEmptyState` at `:152-156`. `slices/identity/views/SessionsView.vue:126-135` renders three 80px rects with a 12px margin (`:217-223`) against `.session-item` rows whose first has `padding-top: 0` (`:239-242`) or against an `SEmptyState` at `:154-158` | `docs/UI/12-shared-patterns.md` §5.1 ("avoids layout shift when data loads"); `docs/UI/06-agents.md:449-452` ("Page header skeleton (text line 200px) / Tab bar skeleton (5 rectangles) / Two card skeletons with 4 field skeletons each") |
| F-28 | `frontend/src/app/views/NotFound.vue:34-40` is `display: flex; align-items: center; justify-content: center; min-height: 60vh`; `slices/tenancy/views/InviteAcceptView.vue:106-111` is the same shape with `min-height: 400px`. Neither value derives from `--topbar-height` or the content padding. A repository-wide grep confirms these are the only two: every other `min-height` in `frontend/src` is a control or touch-target size (40/44/48px and similar) | Internal inconsistency with `docs/UI/02-layout-shell.md` §3.3: a viewport-derived container already exists, and a second viewport- or pixel-relative box inside it cannot agree with it |
| F-31 | `slices/workflow/views/WorkflowEditorView.vue:2` roots on `section class="workflow-editor flex flex-col h-full"`, and the load-error (`:120-132`), conflict (`:135-147`) and lint-status (`:150-170`) bars are direct flex-column siblings of the canvas wrapper at `:205-208` (`flex flex-1 min-h-0`), so each shrinks the canvas when it appears. The file's only Vue Flow imports are the components (`:315-318`); there is no `useVueFlow`, no `fitView` call and no resize hook. The sole viewport instruction is `fit-view-on-init` at `:230` | `docs/UI/08-workflow.md` §2.1 (the bars are conditional zones in the flex column with the canvas at `flex: 1`) and §2.9 (the `fit-view-on-init` viewport contract) |
| F-40 | `AppShell.vue:123-132` renders `<main id="main-content" tabindex="-1" class="app-shell__content">` and `App.vue:17-23` routes every `requiresAuth` view into it. A `<main` grep across `src/slices/**/*.vue` returns exactly 23 files, all in agents, agent-groups, keys, conversation and activities, all `<main class="p-6">` roots except `GraphragGraphView.vue:168`. `app/views/Landing.vue:183` is the only other `<main>` and is legitimate: `PublicLayout` declares none. The skip link at `AppShell.vue:89-92` targets the outer landmark | HTML landmark semantics; internal inconsistency with the tenancy, admin and identity slices, which use `div`/`section` |
| F-44 | All 13 `slices/admin/views/*.vue` roots are `<section class="admin-*">` with no padding utility and `AdminLayout.vue:24-39` adds none, but the two `/admin/*` children mounted from other slices do: `slices/skills/views/AdminSkillsView.vue:26` (`<div class="p-6">`) and `slices/prompt-studio/views/AdminPromptStudioView.vue:12` (`<div class="mx-auto max-w-3xl p-4">`), both routed as children of `AdminLayout` (`slices/admin/routes.ts:71-80`) | `docs/UI/02-layout-shell.md` §9 route table (`/admin/*` at one padding value); internal inconsistency with the 13 sibling sections |
| F-51 | `slices/agents/views/AgentDetailView.vue:964` sizes the sticky prompt panel `lg:h-[calc(100vh-8rem)]`. The scrollport for a sticky offset is `main`'s padding box, whose top is at `--topbar-height`, so the panel sticks at `topbar + 24px` and should end at `main`'s content-box bottom, i.e. `100vh - 3.5rem - 3rem`. `8rem` is 24px larger, leaving 24px of dead band below the panel relative to the content box and 48px relative to the viewport for the whole scroll | Internal inconsistency; the constant does not agree with the shell's own padding, and today the discrepancy is masked at the document bottom by the doubled gutter F-3 removes |
| F-52 | `slices/agent-groups/views/AgentGroupDetailView.vue:143` is `mt-6 space-y-6 max-w-2xl` while its list view is full width (`AgentGroupListView.vue:162,185-191`) | The audit withdrew its spec citation during verification: `docs/UI/02-layout-shell.md` §3.3's "Max-width: none" describes `AppShell`'s own content region, not a prohibition on views constraining themselves. No intent source survives. Deferred by Q-15 |

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Why does `depends_on` list `2026-08-19-shared-overlay-and-shell-defects` when nothing here needs its code? | **Overlap prerequisite, not a logical one.** Both dossiers edit `frontend/src/app/layouts/AppShell.vue`, `frontend/src/app/router.ts` and `frontend/src/slices/agents/views/AgentDetailView.vue`; building them concurrently would produce conflicting diffs in the same files and, in `AppShell.vue`, in the same style block. | Recorded here per `docs/tasks/README.md`, "Dependencies and sequencing", which requires an overlap prerequisite's reason to live in a Clarifications rationale. Either dossier could technically go first; the sibling is sequenced first because its F-45 fix decides the viewport unit `.app-shell` is sized in, which Q-14 depends on. |
| Q-2 | F-3 and F-40: (a) strip the padding utility and the `<main>` element from all 34 roots, or (b) remove `padding` from `.app-shell__content` and standardise on per-view padding? | **(a).** The shell keeps sole ownership of content padding; the 34 view roots lose their padding utility, and the 23 that are `<main>` lose that element too. | `docs/UI/02-layout-shell.md` §3.3 names the content area as the padding owner with a breakpoint ladder (24/16/8px), and §9 gives one Content Padding value per route pattern implemented through `meta.contentPadding`. Option (b) would delete the mechanism §9 documents, would require editing all 74 views instead of 34, would have to re-implement the 1024px and 480px steps in each of them, and would leave no single place to opt a route out. It also breaks the two immersive routes, whose entire contract is "the shell contributes zero padding here". F-40 is the same edit on a 23-view subset of the same 34 files, so doing them apart would mean touching those files twice. |
| Q-3 | What element replaces the 23 `<main>` roots? | **`<div>`**, carrying only the classes that were doing something other than padding. Concretely: `ChatroomSettingsView.vue:317` (was `:227`; re-verified 2026-08-21) keeps `settings`; `GraphragGraphView.vue:168` keeps `flex flex-col` and gains the Q-6 height; the other 21 become a bare `<div>`. | `<section>` becomes a landmark only when it has an accessible name, so an unnamed `<section>` would either add nothing or add a second unnamed region to the rotor. The tenancy, admin and identity slices already use `div`/`section` for view roots with no ill effect. `<div>` is the neutral choice and none of the 21 needs a styling hook once the padding class is gone. |
| Q-4 | F-4: where does the scroll reset live, given vue-router's `scrollBehavior` resolves through `window.scrollTo` and the real scroll container is `main.app-shell__content`? | **In `AppShell`'s existing `watch(() => route.path, ...)` at `AppShell.vue:45-51`**, not in the router and not in a new composable. | The element is already referenced there: `contentEl` (`AppShell.vue:67`, bound at `:125`) is the scroll container, and the component that owns the element is the correct place to write to it. A router `afterEach` would have to reach the DOM by `document.getElementById('main-content')`, putting a DOM query for a layout element into the routing layer and coupling `app/router.ts` to a class name it does not own. A shared composable would need the same element handed to it and would add an indirection with exactly one consumer. The watcher already exists and already fires on precisely the right signal, so the change is one statement. `main` is the shell's own element inside `app/layouts/`, so no layer boundary is crossed at all. |
| Q-5 | F-4: is query-only navigation exempt, and how is the reset written? | **Exempt, by construction**, and written as `contentEl.value.scrollTop = 0` inside an existence check. **Rewritten 2026-08-21 (§1.3): the original `scrollTo({ top: 0 })` was chosen on an inverted premise and would throw.** | The watcher keys on `route.path`, so a query-only change never fires it. That matches the deliberate `:key="$route.path"` at `App.vue:48-58` and its comment, and preserves the tab and scope switches the audit judged defensible. On the *how*: the original rationale claimed jsdom's `scrollTop` setter is inert while `scrollTo` is a stubbable method. Measured, both halves are false — `Element.prototype.scrollTo` does not exist in this repo's jsdom (`'scrollTo' in Element.prototype === false`), so the specified line raises a TypeError inside the watcher on every navigating unit test, while `el.scrollTop = 123` reads back as `123`, so it is directly assertable. `useChatroomScroll.ts:83-88` already documents exactly this constraint and already branches around it. `scrollTop = 0` therefore wins on every axis the original argued: it works in a browser, it is one statement with no jsdom guard and no prototype stub, and it makes AC-2 a real unit assertion. Consequence accepted and recorded: back and forward navigation also land at the top, because no saved-position store exists today (FU-1). |
| Q-6 | F-10: give the two graph routes `contentPadding: 'none'`, or correct the view's `calc()`? | **Neither literally: drop the `calc()` and use `h-full`**, keeping the shell's padding. `GraphragGraphView.vue:168` becomes `<div class="flex flex-col h-full">`. | `contentPadding: 'none'` would push the page header, the search field and the summary line flush against the shell edges, contradicting §9's route table, which gives every non-immersive app route 24px, and the graph view is not immersive: it has a full page header (`:169-186`). Correcting the `calc()` instead would mean encoding 24/16/8px per breakpoint in the view, a third copy of the ladder that drifts the next time it changes. `main` is grid row 2 of `grid-template-rows: var(--topbar-height) 1fr` (`AppShell.vue:148-150`) on a container whose height is definite, so its content box is definite and a child's `height: 100%` resolves against it exactly, at every breakpoint, with no arithmetic. **Restated 2026-08-21 (§1.2):** the shell's height no longer comes from a `height: 100vh` declaration - the sibling dossier replaced it with `flex: 1 1 0px; min-height: 0` (`:151-152`) inside `App.vue`'s `.app-root` flex column. The height is now *resolved by flex layout* rather than *declared*, which is still definite, so the conclusion is unchanged. It is also no longer only an inference: that dossier measured `main` at `clientHeight` 364 with 3749px of internal scroll on a 420px viewport, so the row genuinely resolves. The one thing a builder must not do is reintroduce a percentage flex basis - its D-10 records that `flex: 1` (a `0%` basis against a `min-height`-only container) resolves to `content` and hands scrolling to the document. `WorkflowEditorView.vue:2` already uses `h-full` for the same job. It also avoids adding route meta, so it does not extend the duplication the audit records as FU-1 (`AppShell.vue:18-23` hardcodes an editor regex beside the meta that already declares the same thing). |
| Q-7 | F-16: how is a height supplied below 1024px? | **Change `min-h-[32rem]` to `h-[32rem]` at `AgentDetailView.vue:964`**, leaving the `lg:` overrides untouched. | A definite height on the flex container is the one thing `flex-1 ... overflow-y-auto` at `PromptAssistantPanel.vue:145` needs in order to engage; `min-height` cannot supply it because the flex item's `flex-basis: 0` resolves against the container's height, which stays indefinite. `h-[32rem]` (512px) is the same number the current `min-h-` already reserves, so the panel's size below `lg` does not change for short conversations; only unbounded growth stops. Tailwind emits responsive variants after base utilities, so `lg:h-[calc(...)]` continues to win above 1024px. No change inside `PromptAssistantPanel` itself, which is correct as written. |
| Q-8 | F-17: auto-select the most recent run, or add an empty state? | **Add an `SEmptyState` in a `v-else`, and do not auto-select.** Also replace the `…` at `:29-34` with `SSkeleton` rows. | The empty option is deliberate and documented in the code (`WorkflowBackstageView.vue:179-180`: it exists so the user can clear back to no selection), so auto-selecting would remove a state the view is written to support and would fire four queries on arrival for a run the user did not ask about. `docs/UI/12-shared-patterns.md` §6.1 asks for a contextual empty state, which is exactly the missing arm. The `…` replacement is §5.1's "structural skeleton, not a text placeholder". |
| Q-9 | F-26 and F-27: does this dossier introduce a shared page-skeleton primitive, or fix only the cited views? | **Only the cited views. No new shared primitive.** Six views change: the three F-26 spinners and the three F-27 skeletons. | §5.1 requires the skeleton to match *that page's* structure, so a generic page skeleton would render a shape matching nothing and reintroduce the same layout shift under a new name. `SSkeleton` already exists and is the right granularity; what the three F-26 views lack is not a component but the two-line composition the audit itself cleared elsewhere (`AdminHomeView.vue:3-9` renders `SPageHeader` before the spinner; `AgentGroupDetailView.vue:126-131` bounds and centres it). Building a structural skeleton for all 74 views is a separate, much larger piece of work, recorded as FU-2 rather than smuggled in here. `SLoadingSpinner` itself is not modified: adding a min-height or centring to `shared/ui/SLoadingSpinner.vue:53-59` would change every inline and button-adjacent consumer to fix three page-level ones. |
| Q-10 | F-27: a skeleton cannot match both an empty result and a populated one. Which does it match? | **Rule: a skeleton may never be taller than the shortest settled state that branch can produce.** Growing downward when data arrives is acceptable; collapsing upward is not. | The defect §5.1 names is layout shift, and the two are not symmetric: content appearing below the fold moves nothing the user is looking at, whereas a 384px skeleton collapsing to a 200px empty state (`InboxInvitesView`) or a 264px one collapsing to a 60px row (`SessionsView`) pulls the page header's neighbours up under the cursor. For `AgentDetailView` the settled shape is fixed and documented, so it is matched exactly per `docs/UI/06-agents.md:449-452`. |
| Q-11 | F-28: what replaces `min-height: 60vh` and `min-height: 400px`? | **`min-height: 100%`** in both scoped blocks, keeping the flex centring. | Against `AppShell` this resolves to `main`'s content-box height, which is definite (Q-6's reasoning), so the block centres in the actual content area at every viewport instead of in an invented one. Against `AuthLayout`, which is `min-height: 100dvh` and not a definite height (`AuthLayout.vue:23-30`), a percentage min-height resolves to `auto`, so `NotFound` simply wraps its content and `AuthLayout`'s own `align-items: center` centres it, which is correct there too. `height: 100%` would not degrade this way, which is why `min-height` is specified. This matters because `NotFound` renders under both layouts. |
| Q-12 | F-31: what triggers the re-fit, and what does it do? | **A watcher on the three dynamic bars' visibility (`loadError`, `conflictDetected`, `store.lintRan`) calling `fitView()` from `useVueFlow()` on `nextTick`.** Not a `ResizeObserver`. | `fit-view-on-init` (`WorkflowEditorView.vue:230`) already makes fit-view this view's viewport policy, and §2.9 documents it; extending it to "when the available canvas area changes because a bar appeared" applies the same policy rather than inventing one. A `ResizeObserver` on the canvas wrapper was rejected: it would also fire on window resize and sidebar collapse, discarding a manual pan or zoom every time the user drags a window edge, which is a worse regression than the 24px clip being fixed. The tablet notice (`:173-178`) is excluded because it is static at mount and cannot toggle, per the audit's narrowing. Cost accepted and stated in §9: a manual zoom is discarded at the moment of a validate, a save conflict or a load error. |
| Q-13 | F-44 also reports about 390px of blank to the right of the lower admin nav on `/admin`. Is that in scope? | **No. The padding half is in scope (it is part of Q-2's sweep); the blank column is accepted as-is.** | The blank column is the arithmetic of `AdminLayout.vue:25-30` (`grid-template-columns: 220px minmax(0, 1fr); align-items: start`) meeting a 13-item nav (`slices/admin/components/AdminNav.vue:28-42`) beside a short stat grid. No padding change affects it, and the alternatives (horizontal tabs on desktop, or stretching the content column) are a redesign of the admin console's information architecture, not a spacing fix. Recorded as FU-3 so the audit hand-off has a decision to cite. |
| Q-14 | F-51: which constant, and in which viewport unit? | **`lg:h-[calc(100vh-3.5rem-3rem)]`**, replacing `lg:h-[calc(100vh-8rem)]` at `AgentDetailView.vue:964`. The arithmetic is corrected; **the unit stays `vh`**. | The three terms are the topbar (`--topbar-height` = 56px, `shared/styles/main.css:66`), the 24px sticky offset that `lg:top-6` already sets, and the 24px the shell reserves at the bottom, giving a panel whose top and bottom gaps are both 24px. `8rem` counts 128px, which is 24px more than the content box allows. **Rewritten 2026-08-21 (§1.2). The original decision rested on two false premises and reached the wrong unit.** (a) It said "the unit must be whichever `.app-shell`'s `height` uses (`AppShell.vue:141`)" - that declaration no longer exists; the sibling dossier moved the viewport unit to `App.vue:79`'s `.app-root { min-height: 100vh }`. (b) It said "`depends_on` sequences the dossier that brings the code to `dvh`" - it does not. **F-45 belongs to `2026-08-19-mobile-viewport-and-breakpoints`** (`docs/audits/2026-08-19-page-presentation-scroll-and-feedback/findings.md:1331`), which is sequenced *after* this dossier, and `shared-overlay-and-shell-defects` deliberately kept `vh` so its own change stayed behaviour-neutral with respect to F-45 (its §7 item 1). So the shell is `vh` now and will still be `vh` when this is built. Specifying `dvh` here would mix units and reintroduce a smaller F-51 on mobile - the very thing §9 warns about - so this dossier matches the code it ships against and **`mobile-viewport-and-breakpoints` moves both to `dvh` together**, which is already its job: its §7 item 1 says to apply `100dvh` to whichever element carries the height. A one-line follow-up is recorded as FU-8 so the pairing cannot be forgotten. This fix must land together with Q-2's removal of the view's own `p-6`: today the doubled gutter happens to line up with the short panel at the document bottom, and correcting one without the other would leave a visible mismatch there. |
| Q-15 | F-52: in scope, or deferred? | **Deferred, explicitly.** `AgentGroupDetailView.vue:143` is not changed by this dossier. | The audit marks it `plausible` and its verification withdrew the spec citation: `docs/UI/02-layout-shell.md` §3.3's "Max-width: none" governs `AppShell`'s content region, not what a view may do inside it, and the audit lists five existing views that constrain their own width (`SessionsView.vue:213-215`, `InboxInvitesView.vue:225,232`, `OrgTransferView.vue:386`, `InviteAcceptView.vue:114`, `AdminPromptStudioView.vue:12`). With no intent source, a fix would be a guess, which the bugfix contract forbids. It is also not this dossier's defect class: the other twelve are violations of the shell's padding or scroll contract, and this one is a question about whether detail views should share a width policy. That question belongs in `docs/UI/12-shared-patterns.md` first. Recorded as FU-4. |
| Q-16 | Q-2's sweep touches roots that also carry `mx-auto max-w-3xl` / `max-w-6xl`. Are those removed too? | **No. Only the padding utility is removed; every `mx-auto max-w-*` stays.** | Page-level width capping is the same undecided question as Q-15, and two of these roots (`AdminPromptStudioView.vue:12`, and by extension its org and personal siblings) are among the precedents the audit cited when withdrawing F-52's spec claim. Removing them here would decide Q-15 by accident, in the opposite direction, without an intent source. |

## 4. Reproduction

**F-3** (any environment, authenticated):
1. `pnpm dev`, log in, size the window to 1440x900.
2. Navigate `/orgs`, then `/keys`, then `/workspaces/:wid/workflows`.
3. Observe the page title's left edge and the top gutter jog on each hop: 24px, then 48px, then
   40px. The bottom gutter changes by the same amounts.
4. Resize to 375px wide and repeat on `/keys`. The shell drops to 8px
   (`AppShell.vue:216-218`) but the view keeps 24px, giving 32px where §3.3 specifies 8px.

**F-4** (authenticated, admin):
1. Open `/admin/audit` at 1440x900 and click "Load more" twice.
2. Scroll to roughly y = 2400.
3. Click "Activities" in the admin nav.
4. Observe `/admin/activities` rendering with the scroll offset retained: the page header is
   off-screen above and the user lands mid-table.

**F-10**: open `/projects/:pid/graphrag-configs/:cid/graph` at 1440x900. A vertical scrollbar is
present on `main` with exactly 48px of travel, the top bar picks up its scrolled shadow
(`AppShell.vue:71-77,158-160`), and wheel events near the canvas edge move the page rather than
the canvas.

**F-16**: open `/agents/:id`, Prompt tab, at 900x1200. Send about 15 assistant turns. The panel
grows past the viewport and the Send box is pushed to the bottom of the document.

**F-17**: open `/workspaces/:wid/workflows/:wfid/backstage` at 1920x1080. The page is a header,
a subtitle and a `max-w-xs` select over roughly 860px of blank, with nothing stating that a
selection is required.

**F-26**: open `/orgs/:id` on a cold cache. For the duration of the request the content area is
blank apart from a 24px spinner row at the top left.

**F-27**: open `/invites` as a user with no pending invites, or `/account/sessions` with one
session, and watch the skeleton collapse upward when the query settles.

**F-31**: open `/workspaces/:wid/workflows/:wfid/edit` at 1366x768 with a graph fitted at init
and press Validate. The lint bar inserts above the canvas, the canvas loses about 24px, and the
bottom-most node clips with no viewport adjustment.

## 5. Root Cause Analysis

**F-3 root cause**: `AppShell.vue:188` and the two media-query steps are the only sanctioned
padding, and nothing enforces that. There is no lint gate, no test and no shared view-root
component, so each new view was written with `p-6` on its root by copying the previous one. The
earliest link whose correction prevents the symptom is the padding utility on those 34 roots;
`AppShell.vue:188` is correct and stays. Aggravating, not causal: `AppShell.vue:18-23` hardcodes
two immersive path regexes beside the `meta.contentPadding` that already declares the same
thing (the audit's FU-1), which makes the padding contract look like it lives in two places.

**F-4 root cause**: `router.ts:50-53` creates the router with no `scrollBehavior`, and no other
layer picks up the responsibility. The chain is: `AppShell.vue:183-189` makes `main` the scroll
container to `App.vue:17-23,30` keeping one `AppShell` instance alive across every authenticated
route to `main` and its `scrollTop` surviving the view swap to `guards.ts` and `router.ts`
touching no DOM to the offset being inherited. The earliest link whose correction prevents the
symptom is the missing reset, and it cannot be added at the router link, because vue-router's
return-based `scrollBehavior` resolves through `window.scrollTo` and `window` is not the scroll
container. Not causal, and worth recording because it was believed to be a mitigation: the
`<Transition mode="out-in">` at `App.vue:34-44` does not clamp the offset. Landing at the top of
a shorter page is a side effect of the new content being too short to sustain the old offset.

**F-10 and F-51**: both recompute the shell's own sizing in a view-local constant, and both
count the content padding wrongly. F-10 omits it entirely (`100vh - 3.5rem`); F-51 counts it
once (`8rem`) where the geometry needs the topbar plus a symmetric 24px pair. Same root cause,
two constants.

**F-16**: `PromptAssistantPanel` declares `h-full` at `:123` and the mount site supplies a height
only under `lg:` (`AgentDetailView.vue:964`). The panel is correct; the mount site is the
missing link.

**F-17**: a `v-if` with no `v-else` (`WorkflowBackstageView.vue:23-131`) over a ref that starts
empty (`:154`) and an option list that offers an empty entry by design (`:181-182`).

**F-26**: three views branch the entire template on `isLoading`, so the page header is inside
the branch that does not render. `SLoadingSpinner` having no min-height (`:53-59`) makes the
result a 24px row rather than a centred block, but the header omission is the root cause.

**F-27**: skeleton blocks were sized by eye rather than against the settled content, and no test
compares them.

**F-28**: two views centre themselves in a box of their own invention inside a container that is
already viewport-derived. Nothing reconciled the two.

**F-31**: the viewport is set once, at init (`WorkflowEditorView.vue:230`), and the flex column
above the canvas is conditional (`:120-178`). Nothing connects the two facts.

**F-40**: 23 views were written with `<main>` as the view root before or without regard to
`AppShell.vue:123` already being one. Same 23 files as most of F-3, same copy-forward cause.

**F-44**: two `/admin/*` children are mounted from other slices (`slices/admin/routes.ts:71-80`)
and were written against those slices' padding convention rather than the admin console's.

**F-52**: not a defect with a root cause; see Q-15.

## 6. Blast Radius and Sibling Suspects

**Blast radius**

- **F-3**: 34 of 74 views, at all three breakpoints, on every visit. This is the structural
  cause of the "too much empty space at the bottom" complaint that survives after F-1 in
  `2026-08-19-transient-feedback-channels`.
- **F-4**: every navigation between two authenticated routes whose target content is taller
  than the retained offset. Worst on the admin and audit tables, which are the longest pages in
  the product.
- **F-40**: 23 views, for every screen-reader user, and the skip link at `AppShell.vue:89-92`
  lands them on a wrapper whose only child is a second `main`.
- Everything else is bounded to the views named in §2: two graph routes (F-10), one panel below
  1024px (F-16), one view (F-17), three views on cold load (F-26), three views on cold load
  (F-27), two views (F-28), the workflow editor on every validation run (F-31), the admin
  console (F-44), one panel on one view (F-51).
- **No data impact.** No backend change, no API change, no migration, no `gen:api` rerun. The
  whole diff is `frontend/src`.

**Sibling suspects**

- **Other views that size themselves against the viewport inside the shell**: **cleared**. A
  grep for `100vh|100dvh` across `frontend/src` returns seven hits, of which only two are inside
  `AppShell` (`GraphragGraphView.vue:168` = F-10, `AgentDetailView.vue:964` = F-51). The other
  five are `AppShell.vue:141` itself (F-45, owned by the sibling dossier), `AuthLayout.vue:27,77`,
  `PublicLayout.vue:14` and `Landing.vue:345`, none of which is inside the content area.
  **Re-swept 2026-08-21 (§1.2): still seven, one member different.** `AppShell.vue:141` is gone
  and `App.vue:79` (`.app-root { min-height: 100vh }`) takes its place, because the sibling
  dossier moved the viewport unit up a level. F-45 moved with it and still belongs to
  `2026-08-19-mobile-viewport-and-breakpoints`. The clearance is unchanged: exactly two hits are
  inside the content area, and both are this dossier's.
- **Other arbitrary centring boxes (the F-28 pattern)**: **cleared**. A grep for
  `min-height: <n>px|<n>vh` and `min-h-[` across `frontend/src` returns 27 hits; only
  `NotFound.vue:39` and `InviteAcceptView.vue:110` are page-level layout boxes. Every other hit
  is a control or touch-target floor (32/36/40/44/48/60/78/120px in `shared/ui/*` and slice
  components) or `AgentDetailView.vue:964`'s panel, which Q-7 changes for a different reason.
- **Other `<main>` landmarks outside `views/`**: **cleared**. The `<main` grep across the whole
  of `frontend/src` returns 25 hits: the 23 slice view roots, `AppShell.vue:123`, and
  `Landing.vue:183`, which is correct because `PublicLayout` renders no `<main>`.
- **Other padded roots outside `views/`** (a layout or wrapper adding a third layer):
  **cleared**. `AdminLayout.vue:24-39` is a grid with `gap` only, and it is the only nested
  layout component between `main` and a view.
- **Other components declaring an internal scroll region against an indefinite parent (the F-16
  pattern)**: `h-full`/`height: 100%` in `**/views/*.vue` returns only `ChatroomView.vue` and
  `WorkflowEditorView.vue`, both on `contentPadding: 'none'` routes with definite ancestry.
  Components below view level were **not** swept, and `2026-08-09-chatroom-rail-scroll-and-resize`
  §4.1 documents two more instances of exactly this pattern in the chatroom rail, already fixed.
  A sweep of `**/components/*.vue` for `h-full` on a flex column with an `overflow-y-auto` child
  is to be run at build time and any further instance recorded, not silently fixed.
- **Other views whose whole template is behind a loading branch (the F-26 pattern)**: the audit
  checked seven candidates and cleared four (`AdminHomeView.vue:3`, `AdminMetricsView.vue:3`,
  `OrgTransferView.vue:185-188`, `AgentGroupDetailView.vue:126-131`). The remaining 67 views were
  not individually checked for this; re-run the sweep at build time.
- **Other Vue Flow hosts with conditional chrome (the F-31 pattern)**: `GraphragGraphView` is the
  only other Vue Flow consumer, and its bars (`:192-198`, `:207-213`) sit above a `flex-1`
  canvas in the same way. It is **confirmed** to share the pattern but **not fixed here**: its
  canvas is a read-only visualisation with no `fit-view-on-init` equivalent contract in
  `docs/UI/06-agents.md`, so a re-fit rule for it has no intent source. Recorded as FU-5.

## 7. Fix Design

Nothing here masks a symptom: every change moves a decision back to the layer that owns it, and
in eight of the thirteen cases the change is the deletion of a view-local duplicate of something
the shell already declares.

1. **F-3 and F-40 (one sweep, 34 files)**. Remove the padding utility from all 34 view template
   roots, and replace `<main>` with `<div>` on the 23 that use it, per Q-2 and Q-3. Two roots
   keep other classes (`ChatroomSettingsView.vue:317` keeps `settings`,
   `GraphragGraphView.vue:168` is handled by item 2); eleven non-`<main>` roots lose only the
   padding utility and keep their existing class list, including every `mx-auto max-w-*` per
   Q-16: `NotificationsView.vue:13`, `AdminSkillsView.vue:26`, `OrgSkillsView.vue:19`,
   `ProjectSkillsView.vue:19`, `AdminPromptStudioView.vue:12`, `OrgPromptStudioView.vue:16`,
   `PersonalPromptStudioView.vue:12`, `AgentOrchestrationView.vue:58`,
   `WorkflowBackstageView.vue:2`, `WorkflowRunsListView.vue:2`, `WorkflowRunView.vue:2`. The last
   two of the eleven, `AdminSkillsView` and `AdminPromptStudioView`, are F-44's fix.
   `AppShell.vue:188,207-209,216-218` is not touched.
2. **F-10**. `GraphragGraphView.vue:168` becomes `<div class="flex flex-col h-full">` per Q-6.
   The `flex-1` children at `:200-234` are unchanged and continue to absorb the height.
3. **F-4**. Add `contentEl.value?.scrollTo({ top: 0 })` to the existing `route.path` watcher at
   `AppShell.vue:45-51`, per Q-4 and Q-5. No change to `router.ts`, `guards.ts` or `App.vue`.
4. **F-16**. `AgentDetailView.vue:964`: `min-h-[32rem]` becomes `h-[32rem]`, per Q-7.
5. **F-51**. `AgentDetailView.vue:964`: `lg:h-[calc(100vh-8rem)]` becomes
   `lg:h-[calc(100vh-3.5rem-3rem)]`, per Q-14 (**corrected 2026-08-21: `vh`, not `dvh`** - the
   shell is `vh` and `mobile-viewport-and-breakpoints` moves both together; see FU-8). Items 4
   and 5 are the same attribute and land together.
6. **F-17**. `WorkflowBackstageView.vue`: add a `v-else` to the `:23-131` block rendering an
   `SEmptyState` with a contextual title and text stating that a run must be selected, and
   replace the `…` at `:29-34` with `SSkeleton` rows. New strings in
   `slices/workflow/locales/{en,zh-TW}.json` (gate #12 requires both).
7. **F-26**. In `ProjectDetailView.vue:103-106`, `OrgDetailView.vue:111-114` and
   `AdminUserDetailView.vue:3-7`, hoist `SPageHeader` above the loading branch so it renders
   during the fetch, and wrap the spinner in a bounded centred box following
   `AgentGroupDetailView.vue:126-131` (`flex justify-center py-16`). `shared/ui/SLoadingSpinner.vue`
   is not modified. **Restated 2026-08-21 (§1.3): the header cannot be hoisted unchanged**, because
   all three titles come from the fetched entity and `SPageHeader.title` is a required `string`
   with no slot. The rule is: hoist one `SPageHeader` out of the branch, bind its title to the
   entity value with a fallback for the pending state, and keep the entity-dependent slot content
   (`#prepend` badges, `#actions` buttons) inside the settled branch by rendering those slots
   conditionally. The fallback string reuses the slice's existing loading key
   (`tenancy.common.loading`, `admin.common.loading`) rather than introducing a new one, so the
   pending page announces a real heading instead of an empty `<h1>` or a bare `...`. Breadcrumbs
   already tolerate the pending state (`ProjectDetailView.vue:97`, `OrgDetailView.vue:105` both
   use `?? '...'`) and are hoisted with the header.
8. **F-27**. `AgentDetailView.vue:667-680` is rebuilt to the shape
   `docs/UI/06-agents.md:449-452` specifies: a 200px header text line, five tab-bar rects, and
   two card skeletons of four field skeletons each. `InboxInvitesView.vue:139-149` and
   `SessionsView.vue:126-135` are resized so the skeleton's total height is no greater than the
   height of the branch's own `SEmptyState` (`InboxInvitesView.vue:152-156`,
   `SessionsView.vue:154-158`), per Q-10.
9. **F-28**. `NotFound.vue:39` `min-height: 60vh` and `InviteAcceptView.vue:110`
   `min-height: 400px` both become `min-height: 100%`, per Q-11.
10. **F-31**. `WorkflowEditorView.vue`: obtain `fitView` from `useVueFlow()` in setup (the same
    store instance the `<VueFlow>` child at `:224` adopts) and watch the three bar predicates
    (`loadError`, `conflictDetected`, `store.lintRan`), calling `fitView()` on `nextTick` when
    any of them changes, per Q-12. `fit-view-on-init` at `:230` stays.
11. **F-44**. Covered by item 1's treatment of `AdminSkillsView.vue:26` and
    `AdminPromptStudioView.vue:12`. The blank-column half is out of scope per Q-13.
12. **F-52**. No change; see Q-15 and FU-4.

**Data repair**: none. Nothing was persisted incorrectly; no server state, no `localStorage`
key and no user preference is involved.

## 8. Regression Test Plan

Written first, failing against current code. This plan is deliberately explicit about what the
unit tier cannot reach, because most of what this dossier fixes is a rendered geometry and
**jsdom performs no layout**: it computes no box, applies no scoped CSS, and its `scrollTop`
setter is inert. The repository has closed a dossier on exactly this boundary before:
`docs/tasks/2026-08-09-chatroom-rail-scroll-and-resize` §11 carries a preamble marking four
boxes deliberately unchecked in an `implemented` dossier, §12 states the coverage boundary
plainly ("no automated test in this repository proves the reported symptom is gone"), and D-5
records the user's decision to close without the manual check, with an instruction to the next
reader to treat those ACs as unconfirmed. This dossier follows that precedent for the items in
the third table below, but narrows the gap first: unlike that task, most of the layout outcomes
here **are** measurable in a real browser, so the Playwright tier does the work instead of the
gap being merely declared.

### Tier 1 - unit and component tests that genuinely fail today

| ID | File | Asserts | Why it fails now |
|---|---|---|---|
| T-1 | `frontend/src/app/__tests__/AppShell.test.ts` (extend) | Seed `main.app-shell__content`'s `scrollTop` to a non-zero value, then assert it returns to `0` when `route.path` changes and is **preserved** when only `route.query` changes (**restated 2026-08-21**, see Q-5: a direct `scrollTop` assertion, not a `scrollTo` spy — jsdom has no `Element.prototype.scrollTo` but does persist `scrollTop`) | Nothing writes to the scroll container; the watcher at `AppShell.vue:45-51` only resets sidebar state (F-4) |
| T-2 | new `frontend/src/app/__tests__/viewRoots.test.ts` | Reads every `src/**/views/*.vue` from disk and asserts (a) no template root carries `p-6`, `p-4`, `px-*`, `py-*` or `sm:p-*`, (b) no file under `src/slices` contains `<main` | 34 roots are padded and 23 are `<main>` (F-3, F-40, F-44). A file-reading sweep, not an import, so no slice boundary is crossed; it lives under `app/` because `app/` owns the shell contract |
| T-3 | `frontend/src/slices/agents/__tests__/GraphragGraphView.test.ts` (extend) | The root element's class list contains `h-full` and contains neither `p-6` nor `h-[calc(100vh-3.5rem)]`, and the root is not a `main` | The root is `<main class="p-6 flex flex-col h-[calc(100vh-3.5rem)]">` (F-10) |
| T-4 | `frontend/src/slices/workflow/__tests__/WorkflowBackstageView.test.ts` (extend) | With no run selected, an `SEmptyState` renders; while `stepsQuery.isLoading` is true, `SSkeleton` renders and the literal `…` does not | There is no `v-else` and the loading placeholder is a text node (F-17) |
| T-5 | `frontend/src/slices/tenancy/__tests__/ProjectDetailView.test.ts`, `.../OrgDetailView.test.ts`, `frontend/src/slices/admin/__tests__/AdminUserDetailView.test.ts` (extend all three) | While the query is loading, `SPageHeader` is present in the render output | All three branch the whole template on the loading flag (F-26) |
| T-6 | `frontend/src/slices/agents/__tests__/AgentDetailView.test.ts` (extend) | The loading branch renders one header skeleton, five rect skeletons and two card groups of four field skeletons, matching `docs/UI/06-agents.md:449-452` | The current branch renders 1 + 5 + 2 flat skeletons (F-27) |
| T-7 | `frontend/src/slices/tenancy/__tests__/InboxInvitesView.test.ts`, `frontend/src/slices/identity/__tests__/SessionsView.test.ts` (extend both) | The skeleton branch renders the reduced count and heights decided in Q-10 (prop assertions on `SSkeleton`) | Three 120px and three 80px rects respectively (F-27). This pins the shape; the height comparison against the empty state is T-13 |
| T-8 | `frontend/src/slices/workflow/__tests__/WorkflowEditorView.test.ts` (extend) | With `useVueFlow` mocked, `fitView` is called when `store.lintRan` flips true, when `conflictDetected` flips true and when `loadError` becomes set; it is not called on an unrelated store change | Nothing calls `fitView` anywhere in the file (F-31) |
| T-9 | `frontend/src/slices/agents/__tests__/AgentDetailView.test.ts` (extend) | The `PromptAssistantPanel` mount site's class list contains `h-[32rem]` and `lg:h-[calc(100vh-3.5rem-3rem)]`, and contains neither `min-h-[32rem]` nor `lg:h-[calc(100vh-8rem)]` (**unit corrected 2026-08-21**, see Q-14) | Both old values are present (F-16, F-51). A class assertion only; the behaviour is T-14 and T-15 |

### Tier 2 - Playwright, in a real browser, where the outcome is a geometry

New spec `frontend/e2e/22-layout-contract.spec.ts` (**renumbered 2026-08-21, §1.3**: `19`, `20`
and `21` are taken), run by `pnpm run test:e2e` against the
compose stack. These are the assertions the unit tier cannot make and that the
`2026-08-09` precedent had no way to make at all.

| ID | Asserts | Why it fails now |
|---|---|---|
| T-10 | On `/projects/:pid/agents`, the horizontal distance between `main#main-content`'s bounding box and the page header's bounding box is 24px at 1440x900, 16px at 900x800 and 8px at 375x812 | It is 48px, 40px and 32px respectively (F-3) |
| T-11 | Scroll `main#main-content` on a long admin table to a non-zero offset, navigate to a sibling admin route, and assert `main.scrollTop === 0`; then change only a query parameter and assert the offset is preserved | The offset is inherited (F-4); the query half passes today and is a guard against Q-5 being over-applied |
| T-12 | On `/projects/:pid/graphrag-configs/:cid/graph`, `main.scrollHeight === main.clientHeight` | It exceeds it by 48px (F-10) |
| T-13 | On `/invites` and `/account/sessions` with an empty result, capture the page height while the skeleton is shown and after the query settles, and assert the settled height is not less than the skeleton height | The skeleton is 184px and 200px taller respectively (F-27) |
| T-14 | On `/agents/:id` Prompt tab at 900x1200, after enough assistant turns to overflow, the composer's bounding box is inside the viewport and the message list's `scrollHeight > clientHeight` | The panel grows unbounded and the composer leaves the viewport (F-16) |
| T-15 | On `/agents/:id` Prompt tab at 1440x900, mid-scroll, the sticky panel's bottom edge is within 24px of `main`'s content-box bottom | It is 48px above the viewport bottom (F-51) |
| T-16 | Exactly one `main` landmark exists on `/projects/:pid/agents` | There are two (F-40) |

### Tier 3 - browser-verification items with no automated coverage

These are stated as such rather than being claimed by a test that does not actually cover them.
Each is checked by hand against a running stack, using the `frontend:verify` skill, and the
result recorded in §12.

- **V-1 (F-26)** - on `/orgs/:id`, `/projects/:id` and `/admin/users/:id` with a throttled
  network, the page header is painted for the whole of the fetch and the spinner sits in a
  centred bounded block, not as a 24px row at the top left. T-5 proves the header renders; only
  a browser shows that the result reads as a loading page.
- **V-2 (F-28)** - `/404` while authenticated and `/invites/accept` both centre their block in
  the content area with no dead band below, at 1440x900 and at 1280x600. jsdom applies no scoped
  CSS, so no unit test can see `min-height` at all, and the outcome is a centring, not a
  measurable single number.
- **V-3 (F-31)** - in the workflow editor with a graph fitted at init, pressing Validate leaves
  every node inside the canvas. T-8 proves `fitView` is called; only a browser proves the fit is
  the right one.
- **V-4 (F-3)** - a visual pass over a sample of the 34 changed views confirming that removing
  the root padding did not leave any view whose own internal spacing depended on it.

**Coverage boundary, stated plainly**: of the thirteen findings, nine have a unit test that
fails today (T-1 to T-9), seven have a browser assertion that fails today (T-10 to T-16), and
four outcomes (V-1 to V-4) are verified only by eye. F-52 has no test because it is deferred
(Q-15). No unit test in this repository can prove any of the padding, centring or sticky-fill
outcomes, because they are all layout, and the unit tier has no layout engine.

## 9. Risks and Rollback

- **The 34-file sweep is the largest risk in this dossier.** Removing a root's padding exposes
  any view that relied on it for internal spacing rather than page gutter. The sweep is
  mechanical but not blind: V-4 exists for this, and T-2 makes a future regression impossible
  rather than merely unlikely.
- **Existing view tests may select on `main`.** Twenty-three roots stop being `<main>`, so any
  test using a `main` selector or `getByRole('main')` inside a slice will fail. That is a
  correct failure and those selectors are to be updated, not the fix reverted. `/build` must run
  the full `pnpm test` before assuming the sweep is clean.
- **F-4 changes back and forward navigation.** With the reset in place, popstate also lands at
  the top of the target page. There is no saved-position store today, so nothing is lost that
  currently works, but the behaviour is different from a conventional web page and users may
  notice. FU-1 records the proper fix.
- **Q-12's re-fit discards a manual pan or zoom** at the moment a bar toggles. Accepted: those
  moments are validate, save conflict and load error, which are exactly when the user wants the
  whole graph. If it proves disruptive in use, the fallback is to translate the viewport by the
  height delta instead of re-fitting, which preserves zoom at the cost of a measurement.
- **Q-14 depends on the shell's viewport unit.** ~~If `AppShell.vue:141` is still `100vh` when
  this is built, the panel constant and the shell must be made to agree first.~~ **Resolved
  2026-08-21 (§1.2):** the shell's unit lives at `App.vue:79` (`min-height: 100vh`) and stays
  `vh` until `2026-08-19-mobile-viewport-and-breakpoints` runs, which is sequenced after this
  dossier. Q-14 now specifies `vh` to match, so there is no disagreement to record. The risk
  it warned about is still live in the other direction and is now FU-8: whoever moves the shell
  to `dvh` must move this panel constant in the same change, or mixing `dvh` in the shell with
  `vh` in the view reintroduces a smaller F-51 on mobile.
- **`h-full` against a padded `main` has no in-repo precedent.** Both existing users
  (`WorkflowEditorView.vue:2`, `ChatroomView.vue:1086`) sit on `contentPadding: 'none'` routes.
  The CSS reasoning is stated in Q-6 and the outcome is asserted by T-12, which is a real browser
  measurement, so the absence of precedent is covered rather than assumed away.
- **Rollback**: every item is an independent revert. The thirteen findings are separable commits
  and the sweep in item 1 is one commit of its own. No migration, no API change, no persisted
  state, so a revert restores the previous rendering exactly.

## 10. Acceptance Criteria

Ticked only where the mapped test was **observed red before the fix and green after**; the
Tier 2 and Tier 3 halves that need a running stack are left unticked and named in §12a.

- [x] AC-1: **F-3** - T-2 fails before the fix and passes after (verified: 34 padded roots
      enumerated by the sweep, then zero). T-10 is written and unrun (needs the stack). No view
      template root in `src/**/views/*.vue` carries a padding utility.
- [x] AC-2: **F-4** - T-1 fails before the fix (`expected 1200 to be +0`) and passes after, in
      both halves: a path change leaves `main.scrollTop` at 0, a query-only change preserves it.
      T-11 is written and unrun.
- [x] AC-3: **F-10** - T-3 fails before the fix and passes after; the root is a `div` carrying
      `h-full` and neither `p-6` nor the `100vh` calc. T-12 is written and unrun, so the
      "no scroll travel" half is asserted structurally, not measured.
- [x] AC-4: **F-16** - T-9 fails before the fix (`expected … not to contain 'min-h-[32rem]'`) and
      passes after. T-14 is written and unrun.
- [x] AC-5: **F-17** - T-4 fails before the fix in both halves and passes after. The empty state
      and the skeleton render, and the keys exist in `en` and `zh-TW` (gate #12 green).
- [ ] AC-6: **F-26** - **wording amended by D-7**: all three detail views paint a *header-shaped
      skeleton* for the whole of the first fetch, not a page header - a header titled with the
      loading string put a status string in the `h1` and took two e2e specs down. T-5 fails
      before the fix and passes after for all three views, and now also asserts no `h1` exists
      during the fetch. **V-1 is not confirmed**: no running stack. Unticked deliberately.
- [x] AC-7: **F-27** - T-6 and T-7 fail before the fix and pass after. `AgentDetailView`'s
      loading branch matches `docs/UI/06-agents.md:449-452` (1 + 5 + 2x4). T-13 is written and
      unrun, so the "no upward jump" half is asserted by construction (skeleton height reduced
      below the settled height) rather than measured.
- [ ] AC-8: **F-28** - **V-2 is not confirmed**: no running stack, and jsdom applies no scoped
      CSS, so nothing in the unit tier can see `min-height` at all. The CSS reasoning was
      re-derived and the containing-block chain traced (see §12a), but the outcome is a centring
      and remains unverified.
- [ ] AC-9: **F-31** - T-8 fails before the fix and passes after for `store.lintRan` and
      `loadError`, and stays green on the negative case (see D-5). **V-3 is not confirmed**: no
      running stack, so nothing proves the fit is the *right* one.
- [x] AC-10: **F-40** - T-2 fails before the fix (23 files listed) and passes after. Zero `<main`
      remains under `src/slices`; the shell's landmark and `Landing.vue`'s are the only two left
      in `src`. T-16 is written and unrun.
- [x] AC-11: **F-44** - T-2 covers `AdminSkillsView.vue` and `AdminPromptStudioView.vue`; both
      lost their padding utility and all 15 `/admin/*` children now inherit one gutter from the
      shell. The "no change in inset when navigating" half follows from there being one owner.
- [x] AC-12: **F-51** - T-9 fails before the fix (`expected … not to contain
      'lg:h-[calc(100vh-8rem)]'`) and passes after. T-15 is written and unrun.
- [x] AC-13: **F-52** - deferred per Q-15 and recorded as FU-4.
      `AgentGroupDetailView.vue:143` is untouched: the file appears in the diff only for its
      root tag (`git diff` shows exactly two changed lines in it).
- [ ] AC-14: **V-4 is not confirmed** by eye. Its structural half *is* covered and came back
      clean: all 34 changed views were swept for scoped CSS that depended on the removed root
      padding, and zero rules are affected (§12a). What remains unverified is whether any view
      *looks* wrong, which only a browser shows.
- [x] AC-15: green on this host: `pnpm lint` (all 12 gates), `pnpm typecheck`, `pnpm test`
      (209 files / 1352 tests), `pnpm build`. The three bash-script gates could not run here and
      are deferred to CI, with the bundle budget approximated instead - see §12a. Backend gates
      N/A: the diff is frontend-only. Per the project's remote-CI rule, CI is authoritative.
- [ ] AC-16: **not run.** `frontend/e2e/22-layout-contract.spec.ts` is written (T-10 to T-16,
      seven assertions) but needs the compose stack, which is not up on this host.
- [x] AC-17: the shell's padding ladder is byte-identical to before the change: the three rules
      `.app-shell__content { padding: 24px }`, its `@media (max-width: 1023px)` override to 16px
      and its `@media (max-width: 479px)` override to 8px, plus the two
      `.app-shell__content--no-pad` companions, are untouched. **Stated as rules rather than
      line numbers (2026-08-21, §1.2)**: the sibling dossier shifted the whole `<style>` block
      down, so the ladder now sits at `AppShell.vue:195-201,219-225,229-235` and a
      line-number-based check would fail on a file this dossier never edited. The ladder is the
      contract this dossier restores, not something it edits.

## 11. SRS Delta

None. `REQUIREMENTS.md` carries no requirement for content-area padding or scroll-position
management; the intent sources for all thirteen findings are `docs/UI/02-layout-shell.md`,
`docs/UI/06-agents.md`, `docs/UI/08-workflow.md` and `docs/UI/12-shared-patterns.md`, and every
fix restores conformance with them rather than defining new behaviour. `docs/UI/02-layout-shell.md`
already specifies the shell as `100dvh` (`:104`) and needs no amendment for Q-14. **Note
(2026-08-21, §1.2):** the code does not match that line today - the shell's viewport unit is
`100vh` at `App.vue:79` - and closing that gap is F-45's job, owned by
`2026-08-19-mobile-viewport-and-breakpoints`. Q-14 deliberately matches the code rather than the
document so this dossier ships one consistent unit; FU-8 pairs the two edits.

## 12. Deviation Log

The four corrections made at approval time are recorded in §1.3 and folded into Q-5, T-1,
§7 item 7 and §8 rather than repeated here; they were agreed before implementation started.
The entries below are departures from the spec *as approved*.

- **D-1 (§7 item 7) - the page header is rendered per branch, not hoisted above the branches.**
  §1.3 already established that the header cannot be hoisted unchanged, and specified a single
  hoisted `SPageHeader` with a title fallback. That is wrong for a second reason found in
  implementation: **the error arm has no entity to title itself with either.** A hoisted header
  would announce `admin.common.loading` over "user not found", which is a new wrong state, not a
  fix. All three views instead render an `SPageHeader` inside the pending branch
  (`ProjectDetailView.vue:103-114`, `OrgDetailView.vue:111-122`,
  `AdminUserDetailView.vue:3-14`), leaving the settled branch's header - and every
  entity-dependent `#prepend` / `#actions` slot - exactly as it was. Cost: one extra
  `SPageHeader` usage per view, mutually exclusive with the other, and a remount of the header
  element when the query settles. Benefit: no entity-dependent slot had to be rewritten with
  optional chaining, and the error state is unchanged.
- **D-2 (§7 items 4 and 5) - Q-7's and Q-14's classes went onto a new wrapper `<div>`, not onto
  `PromptAssistantPanel`'s class attribute.** `AgentDetailView.vue:975-995`. The panel's own root
  is `flex h-full flex-col` (`PromptAssistantPanel.vue:123`), so passing `h-[32rem]` through the
  `class` attribute would have put **two `height` utilities on one element**, with the winner
  decided by Tailwind's emission order rather than by anything the code states. Above `lg` that
  is already how the old `lg:h-[calc(...)]` beat `h-full` - a responsive variant is emitted
  after base utilities - but below `lg` two base utilities have no such guarantee. The wrapper
  carries `h-[32rem] lg:sticky lg:top-6 lg:self-start lg:h-[calc(100vh-3.5rem-3rem)]` and becomes
  the grid item; the panel keeps `h-full` and resolves against a definite height. Q-7's "no
  change inside `PromptAssistantPanel`" is honoured, and both numbers are exactly as specified.
- **D-3 (§7 item 6) - the step-trace skeleton is one text line, not several rows.**
  `WorkflowBackstageView.vue:29-35`. The spec said "replace the `…` with `SSkeleton` rows"; the
  first implementation used three 26px rects. The quality gate caught that this branch also
  settles to a **one-line `noSteps` paragraph** (`:49-54`), so an 86px skeleton reintroduces
  F-27's own defect inside F-17's fix. Q-10's rule governs: one 240px text line, which grows
  downward when a real step list arrives and matches the empty case exactly.
- **D-4 (§7 item 8, `SessionsView`) - the skeleton is sized against a single session row, not
  against the `SEmptyState`.** §7 item 8 says "no greater than the height of the branch's own
  `SEmptyState`" (~176px), but Q-10's own rule is "the shortest settled state that branch can
  produce", and for `/account/sessions` that is a single ~60px row, not the empty state - which
  is also what §2's F-27 row observes. One 56px rect (`SessionsView.vue:126-131`). The now-inert
  `.skeleton-row` wrapper and its two CSS rules were removed with it. `InboxInvitesView` is
  unaffected: its shortest settled state really is the empty state, and one 120px rect sits under
  it.
- **D-5 (T-8) - `conflictDetected` is covered through the watcher it shares, not through a 409
  save flow.** `WorkflowEditorView.test.ts` asserts `fitView` on `store.lintRan`, on `loadError`,
  and **not** on an unrelated store change. Reaching `conflictDetected` needs a loaded workflow,
  a dirty store, a save click and a 409 response; the three predicates sit in one `watch` source
  array (`WorkflowEditorView.vue:437-443`), so the third adds no distinct branch to cover. Stated
  rather than silently dropped.
- **D-7 (§7 item 7, again - supersedes the second half of D-1) - the pending state renders a
  header-shaped `SSkeleton`, not an `SPageHeader` titled with the loading string.** Found by
  CI, not by review: the first implementation took **two pre-existing e2e specs down**
  (`15-tenancy-keys-mgmt.spec.ts` "rename a project" and "rename an org", both asserting
  `toHaveValue("Loading...")` against the real org name). Those specs read the entity's current
  name with `getByRole('heading', { level: 1 })` and rely on an invariant this dossier broke:
  **on a detail page the `<h1>` IS the entity's name, so an `<h1>` existing means the entity has
  loaded.** Titling the pending header `tenancy.common.loading` put a status string in the `h1`,
  which resolved `toBeVisible()` immediately and handed every downstream assertion the word
  "Loading...". The test was not at fault, and neither was it merely a timing race: a status
  string in the `h1` is also the page's accessible name for the whole fetch, which is a claim the
  page should not make. §1.3's correction 3 and D-1 both reasoned about *which* title to use and
  never questioned whether there should be a title at all; `docs/UI/12-shared-patterns.md` §5.1
  answers that directly ("structural skeleton, not a text placeholder"), and
  `AgentDetailView.vue:667` already did it this way. All three views now render a 200px x 2rem
  skeleton above the bounded spinner and no `h1`. **AC-6's wording changes with it** - "paint
  their page header" becomes "paint a header-shaped skeleton" - and T-5 was rewritten to assert
  the skeleton **and the absence of an `h1`**, which pins the invariant that broke.
- **D-6 (T-13) - the browser assertion runs at 1440x400, not at a default viewport.** `main` has
  a definite height and `overflow-y: auto`, so `scrollHeight` clamps to `clientHeight` for
  anything that fits: at 900px tall both the skeleton and the settled samples report the same
  number and the assertion **passes against the unfixed code**. A 400px viewport makes the
  pre-fix skeleton genuinely overflow. Found by the quality gate, not by a failing run.

## 12a. Verification record

**Ran and green on this host** (`frontend/`): `pnpm test` (209 files, 1352 tests), `pnpm lint`
(all 12 gates, `--max-warnings=0`), `pnpm typecheck`, `pnpm build`.

**Not runnable on this host, deferred to CI**: `pnpm run check:bundle-size`,
`check:type-coverage` and `check:boundaries-enforced` are bash scripts and this Windows host has
no usable bash. The bundle budget was approximated in PowerShell against the real `dist/assets`
output instead - **no chunk exceeds the 200 KB gzip lazy limit and the entry chunk is 25 043 B
gzip against a 256 000 B budget** - which is a sanity check, not the gate. CI is authoritative.

**CI run 32482080878** (`c7ebdd5`, push to `main`): every job green **except `frontend-e2e`**,
which reported 74 passed / 5 skipped / **3 failed**. All three trace to this dossier and none was
a pre-existing flake:

1. and 2. `15-tenancy-keys-mgmt.spec.ts` "rename a project" and "rename an org" - **a real
   regression this dossier introduced**, diagnosed and fixed as **D-7**.
3. `22-layout-contract.spec.ts` "a query-only change preserves the offset" - **a defect in this
   dossier's own new test, not in the product.** `click()`'s actionability check scrolls its
   target into view, and the tab bar is above the fold once the test has scrolled away, so
   Playwright zeroed `main.scrollTop` *before* the navigation it was measuring. Replaced with
   `dispatchEvent('click')`, which runs `STabs`'s plain `@click` handler (`STabs.vue:115`)
   without scrolling. Recorded here rather than as a D-entry because the spec's design was
   right and only the test's mechanics were wrong - but worth stating plainly: **T-11's
   query-only half never passed as originally written, and would have failed against correct
   code.**

**The other ten assertions in `22-layout-contract.spec.ts` ran and passed**, which is the first
real confirmation that the padding, landmark, graph-scroll and sticky-panel geometry are as this
dossier claims. Established by arithmetic rather than by reading test names, because Playwright's
reporter lists neither passes nor skips individually: the last green run (`e5e23ba`, run
32454040062) was **61 passed / 5 skipped / 0 failed**; this one was **74 passed / 5 skipped /
3 failed**, so 16 tests were added - 11 from this spec and 5 from
`21-overlay-and-shell-contract.spec.ts`, which the same push carried up from the sibling dossier
- and **the skip count did not move**. None of this spec's 11 self-skipped for want of seed data,
so 10 of them genuinely executed and passed. AC-16 stays unticked until a run is green end to
end.

**Audits**: `check-quality` over the whole task diff found two Introduced-Warning and one
Introduced-Info item, all three fixed in `a2e2095` rather than deferred (they are D-3, D-6 and
the dead `.skeleton-row` CSS in D-4); one Pre-existing SRP finding routed to FU-9. It also
swept all 34 changed views for scoped CSS that depended on the removed root padding and found
**zero** rules affected - every scoped rule targets a BEM child, and no view uses a `main`
element selector. `check-security` over the same diff found **no findings** across 13
dimensions, with no endpoint to trace: the diff touches no backend file, no dependency manifest
and no deploy config, adds no `v-html`, and renders no entity field outside the branch its own
authorized query gates.

**Self-audit** additionally confirmed the load-bearing assumption behind Q-6 and Q-11 that the
spec asserts but never traces: `ErrorBoundary` renders a bare `<slot v-else />`
(`app/ErrorBoundary.vue:60`) and `<Transition>` adds no node, so a view root really is a direct
child of `main.app-shell__content` and both `h-full` and `min-height: 100%` resolve against its
content box. Had the boundary rendered a wrapper, F-10's and F-28's fixes would have silently
resolved against an auto-height box instead.

## 13. Follow-ups

- **FU-1** - F-4's reset is unconditional, so browser back and forward also land at the top.
  Restoring the saved offset on popstate needs a per-history-entry position store keyed on the
  navigation, which vue-router's `scrollBehavior` cannot drive for a non-`window` scroll
  container. Worth doing if users report losing their place on back; not a regression, since no
  restore exists today.
- **FU-2** - §5.1 asks for a structural skeleton on the first load of *any* page. Q-9 limits this
  dossier to the six views the audit named. A systematic pass across the remaining views, and a
  decision on whether a per-view skeleton composition deserves a shared convention, is separate
  work.
- **FU-3** - `/admin` leaves roughly 390px of blank to the right of the lower nav, because
  `AdminLayout.vue:25-30` pairs a 220px 13-item nav with a short stat grid under
  `align-items: start`. Deliberately not fixed (Q-13); it is an information-architecture question
  for the admin console.
- **FU-4** - F-52 and Q-16 are the same open question: should detail views share a width policy,
  and if so what is it? Six views cap their own width today
  (`AgentGroupDetailView.vue:143`, `SessionsView.vue:213-215`, `InboxInvitesView.vue:225,232`,
  `OrgTransferView.vue:386`, `InviteAcceptView.vue:114`, `AdminPromptStudioView.vue:12` and its
  org and personal siblings) and the rest do not. The answer belongs in
  `docs/UI/12-shared-patterns.md` before any code changes.
- **FU-5** - `GraphragGraphView` shares F-31's pattern: conditional bars (`:192-198`, `:207-213`)
  above a `flex-1` Vue Flow canvas with no re-fit. Confirmed present, not fixed, because
  `docs/UI/06-agents.md` states no viewport contract for that view to conform to. Fix it
  alongside a decision on what its canvas viewport should do.
- **FU-6** - T-2 is a Vitest sweep standing in for a lint rule. A custom ESLint rule forbidding a
  padding utility or a `<main>` element on a view template root would fail at the point of
  writing rather than at test time, and would join the twelve gates. Worth doing if the sweep
  ever has to be exempted for a legitimate case.
- **FU-8** - **the sticky panel constant and the shell's viewport unit must move to `dvh`
  together.** Q-14 ships `lg:h-[calc(100vh-3.5rem-3rem)]` because the shell is `vh`
  (`App.vue:79`). `2026-08-19-mobile-viewport-and-breakpoints` owns F-45 and is sequenced after
  this dossier; when it moves the shell to `100dvh` it must move this one line in
  `AgentDetailView.vue:964` in the same change, and update T-9's expected class with it.
  Leaving one behind reintroduces a smaller F-51 on mobile - the exact failure §9 warns about,
  now pointed the other way. This is a one-line pairing, not a design question.
- **FU-9** - `slices/agents/views/AgentDetailView.vue` is 1327 lines carrying five tabs, the whole
  agent form's state, five queries and three mutations. Raised by `check-quality` as
  **Pre-existing** in touched code: it long predates this dossier, which added 20 lines to it.
  Splitting it per tab (a presentational component plus a composable each) is its own refactor
  dossier.
- **FU-7** - the audit's FU-1 remains open: `AppShell.vue:18-23` hardcodes the chatroom and
  workflow-editor path regexes that `slices/conversation/routes.ts:26` and
  `slices/workflow/routes.ts:14` already declare as meta. Q-6 was chosen partly so as not to
  extend that duplication, but it is still there. Route to `check-quality`.
