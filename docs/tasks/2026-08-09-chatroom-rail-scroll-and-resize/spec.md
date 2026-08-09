---
type: feature
status: approved
created: 2026-08-09
requirements: [R20.08, R24.30, R24.31, R24.32, R24.34, R30.17, R30.18, R30.19]
depends_on: []
---

# Chatroom right rail: make it scroll, make it resizable, make plugins lay out against it

## 1. Summary

A participant who joins an activity in a chatroom cannot use it. The activity renders in
the desktop right rail, which is a hard-coded 200px track that clips anything taller than
the viewport with no way to scroll to the rest, and the bundled 3x3 Mandala grid lays itself
out against the *viewport* width rather than the 200px box it is actually in — so nine
textareas are forced into roughly 55px columns.

Three distinct defects produce the single reported symptom. The scroll failure is **not an
activities bug**: it lives in the shared `STabs` component and in `ChatroomView`'s grid, and
it affects the People and Observer tabs identically — activities is simply the first content
tall enough to expose it.

This fixes all three: a real height contract for the tabbed rail, a user-resizable rail
width that persists, and container-relative layout for activity plugins so a plugin adapts
to whatever surface hosts it.

## 2. Goals and Non-goals

**Goals**

- Content taller than the desktop right rail scrolls within the rail. Nothing is clipped
  and unreachable, in any of the three tabs.
- A user can drag the boundary between the message feed and the right rail to widen it, by
  pointer or by keyboard, and the chosen width survives a reload.
- The bundled Mandala plugin lays out against the width of its host container, not the
  viewport, so it degrades to a single column in a narrow rail and becomes a real 3x3 grid
  once the rail is widened.
- The rail keeps a floor width and the message feed keeps a floor width; neither can be
  dragged away.
- The resize affordance meets WCAG 2.1 AA ([R20.08]): keyboard operable, correctly labelled,
  and with a touch-sized hit area ([R24.34]).

**Non-goals**

- **No modal or full-screen activity mode.** Considered and rejected in Q-2; the rail stays
  the host surface.
- **No resizing of the left agents rail.** The mechanism this adds would serve it, but
  wiring a second handle is a separate change with its own layout questions.
- **No drag handle on tablet or mobile.** Side panels are drawers below 1024px ([R24.32])
  and `.s-drawer__body` already scrolls (`frontend/src/shared/ui/SDrawer.vue:224`); there is
  nothing to resize.
- **No cross-device width sync.** The width is a local display preference in
  `localStorage`, like theme and locale — not user state on the server.
- **No change to any of the other five `STabs` call sites.** The new height behaviour is
  opt-in precisely so they are untouched.
- **No backend change, no API change, no migration.**
- **Not a redesign of `SchemaForm`.** It is already a single column
  (`frontend/src/slices/activities/components/SchemaForm.vue:94,143,167` — scoped classes,
  no responsive utilities) and is unaffected by the container-query change beyond
  benefiting from the rail being wider.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | One dossier or two — the clipping is a bug, the enlarge affordance is a new capability? | **One feature dossier covering all three defects.** | User decision. They share a root cause (the rail was never given a height or width contract) and the same files; splitting them would mean two dossiers editing `ChatroomView.vue`'s style block in sequence. Cost accepted: the clipping fix ships on the whole dossier's timeline rather than immediately. |
| Q-2 | Which surface provides "enlarge"? | **A draggable, persisted rail width.** Not an `SModal`, not a takeover of the message column. | User decision. Keeps the conversation and the worksheet visible at the same time, which a modal or a feed takeover would not. Cost accepted and stated in §10: even at the maximum width a 3x3 grid of textareas is tighter than a dedicated full-width surface would be. |
| Q-3 | Should `STabs` fill its parent's height unconditionally? | **No — an opt-in `fill` prop, default `false`.** | `STabs` has six production call sites (`slices/agents/views/AgentDetailView.vue`, `KnowledgeMapConfigDetailView.vue`, `RagConfigDetailView.vue`, `slices/keys/views/ProjectKeysView.vue`, `slices/tenancy/views/ProjectListView.vue`, `slices/conversation/views/ChatroomView.vue`). Five of them sit in normal document flow inside a scrolling page; giving them `height: 100%` and an internal scroll region would change five unrelated views to fix one. |
| Q-4 | Does this depend on any unfinished dossier? | **No — `depends_on: []`.** | Open dossiers are `2026-07-07-graphrag-two-axis-redesign`, `2026-07-19-large-artifacts-silently-dropped`, and `2026-08-09-platform-example-activity-types` (drafted the same day). The first two touch neither `ChatroomView.vue` nor `shared/ui`. The third touches `frontend/src/slices/activities/` but only `views/ActivityTypesView.vue`, `api/`, `queries/`, and a new dialog — **not** `ActivityPanel.vue`, `ActivityHost.vue`, or `MandalaGrid.vue`. No file overlap, so no overlap prerequisite. |

## 4. Current State

### 4.1 D1 — the tabbed rail cannot scroll (desktop only)

The chain, top down:

- `.chatroom` is `display: grid; grid-template-columns: 220px 1fr 200px; height: 100%;
  overflow: hidden` (`frontend/src/slices/conversation/views/ChatroomView.vue:851-857`).
- `.chatroom__presence` is `grid-column: 3; grid-row: 2 / -1` and **nothing else**
  (`:935-938`). It has no `min-height: 0` and no overflow handling.
- Its sibling `.chatroom__feed` has both — `min-height: 0; overflow: hidden` (`:869-875`) —
  and its inner `.messages` carries `height: 100%; overflow-y: auto` (`:877-883`). That is
  the pattern the rail was never given.
- When the rail is tabbed, `STabs` sits between the grid item and the panel. It declares
  `.s-tabs { display: flex; flex-direction: column }`
  (`frontend/src/shared/ui/STabs.vue:145-148`) with **no** `height`, and
  `.s-tabs__panels { margin-top: 0 }` (`:231-233`) with **no** `flex`, `min-height`, or
  `overflow`.

The load-bearing consequence: **both sibling panels already try to scroll and are silently
defeated.** `ChatroomPresence` sets `overflow-y: auto; height: 100%`
(`frontend/src/slices/conversation/components/ChatroomPresence.vue:62-63`) and
`ObserverPanel` sets the same (`components/ObserverPanel.vue:142-143`), but a `height: 100%`
resolved against an auto-height `.s-tabs__panels` collapses to content height, so neither
rule does anything. `ActivityPanel` declares neither (`.activity-panel` is
`display: flex; flex-direction: column; gap; padding` only,
`frontend/src/slices/activities/components/ActivityPanel.vue:263-268`), so it simply grows
until `.chatroom`'s `overflow: hidden` clips it — with no scrollbar anywhere in the
ancestry, since `.chatroom` is `height: 100%` of a non-scrolling app shell.

The tabs render exactly when the Activity tab can exist:
`showRailTabs = showObserverTab || showActivityTab` (`ChatroomView.vue:481`), so the rail is
tabbed in precisely the situation where the bug bites.

**Scope of the defect:** desktop only. The mobile and tablet paths render the same `STabs`
inside `SDrawer` (`ChatroomView.vue:225-263`), whose `.s-drawer__body` sets
`overflow-y: auto` (`SDrawer.vue:224`), so the drawer scrolls and the content is reachable.

### 4.2 D2 — the plugin lays out against the viewport, not its container

- The rail track is a fixed `200px` (`ChatroomView.vue:853`).
- `MandalaGrid` chooses its layout with `isGrid ? 'grid grid-cols-1 gap-3 sm:grid-cols-3'
  : 'flex flex-col gap-3'`
  (`frontend/src/slices/activities/plugins/mandala9grid/MandalaGrid.vue:124`).
- Tailwind's `sm:` is a **viewport** breakpoint (640px). On any desktop — where the rail
  exists at all, i.e. >=1024px per `useBreakpoint`
  (`frontend/src/shared/composables/useBreakpoint.ts:53`) — `sm:` is always active, so the
  3x3 grid is unconditionally applied inside a 200px column. Each cell is roughly 55px wide
  after `p-2` padding (`:134-135`) and contains a `rows="3"` textarea (`:173-184`).
- The single-column fallback the component was written to provide (`:30-32`, "anything else
  renders as a single column rather than a broken 3x3 — degrade, never drop (R30.18)") is
  keyed on the *field count*, not on available width, so it never engages here.
- **The repository has no container-query usage yet** — a search for `@container`,
  `container-type`, and `cqw` across `frontend/src` returns nothing. Tailwind v4.3 is in use
  (`frontend/package.json:75`, `:55`), which supports container queries natively.

This also matters for [R30.19]: the host/plugin contract is fixed so an isolating iframe
sandbox can be enabled later. Inside an iframe, `sm:` would key off the *iframe's* viewport,
so viewport-relative layout in a plugin is wrong under both present and planned hosts.

### 4.3 D3 — there is no way to enlarge the rail

`grid-template-columns` is a static declaration (`ChatroomView.vue:853`, tablet override
`:948-950`, mobile override `:954-958`). No component in the tree exposes a resize
affordance, and no `localStorage` key relates to layout — the only persisted display
preferences are theme (`shared/composables/useTheme.ts:5`), locale
(`shared/composables/useLocale.ts`), and the workspace selection
(`shared/stores/workspace.ts:15-23`).

## 5. Design

### Options considered

**Option A — give only `.chatroom__presence` an overflow.** One line. Rejected as
insufficient on its own: with `STabs` still auto-height, the rail would scroll as one long
column including the tab strip, so the tabs would scroll out of view — and it leaves D2 and
D3 untouched.

**Option B — make `STabs` fill height unconditionally.** Rejected per Q-3: five unrelated
views would change behaviour to fix one.

**Option C — opt-in `fill` on `STabs`, plus the missing grid contract, plus a resizable
track, plus container queries in the plugin (chosen).** Each defect is fixed at the layer
that owns it, and no call site outside the chatroom changes.

**Option D — replace the CSS grid with a splitter library.** Rejected: a dependency for one
handle, against a layout that is otherwise four lines of `grid-template-columns`, and every
candidate ships its own focus and ARIA behaviour that would have to be re-audited against
gate #11.

### Decision

**Option C.** The three fixes, at their owning layers:

**1. Height contract.** `STabs` gains `fill?: boolean` (default `false`). When set,
`.s-tabs` becomes `height: 100%; min-height: 0` and `.s-tabs__panels` becomes
`flex: 1 1 auto; min-height: 0`, with each `[role=tabpanel]` at `height: 100%`.
`.s-tabs__panels` deliberately does **not** get `overflow-y: auto`: each panel already owns
its scroll (§4.1), and adding a second scroll region would nest scrollbars. `ChatroomView`
adds `min-height: 0; overflow: hidden` to `.chatroom__presence`, mirroring
`.chatroom__feed:869-875`. `ActivityPanel` adds `height: 100%; overflow-y: auto`, matching
its two siblings. The net effect is that `ChatroomPresence`'s and `ObserverPanel`'s existing
rules start working as written, rather than being replaced.

**2. Resizable track.** `grid-template-columns` becomes
`220px 1fr var(--chatroom-rail-w, 200px)`, driven by a new
`useResizablePanel` composable and a new `SResizeHandle` component.

- **Bounds**: minimum `200px` (today's width, so nothing regresses), maximum
  `min(720px, 45vw)` so the message feed always keeps roughly half the width. Clamped on
  read and re-clamped on window resize, so a width persisted on a wide monitor does not
  strand the feed on a laptop.
- **Persistence**: `localStorage`, following `useTheme.ts:16-26` exactly — a module-level
  `STORAGE_KEY`, a `try/catch` read that falls back to the default, and a `try/catch` write
  with the same "quota / restricted" comment. No new dependency, no store.
- **Accessibility** ([R20.08], gate #11): the handle is a real focusable element with
  `role="separator"`, `aria-orientation="vertical"`, `aria-valuenow`/`aria-valuemin`/
  `aria-valuemax`, and an `aria-label` through `$t()`. Keyboard: Left/Right move by 16px,
  Shift+Left/Right by 64px, Home/End jump to the bounds, and Enter resets to the default.
  This is the WAI-ARIA window-splitter pattern.
- **Hit area** ([R24.34]): the visible divider is 4px; the pointer target is widened to 44px
  with a transparent `::before`, so the visual seam stays thin without an unhittable target.
- **Drag mechanics**: pointer events with `setPointerCapture`, `user-select: none` applied
  for the duration of the drag and released on `pointerup` *and* `pointercancel`, so an
  interrupted drag cannot leave the document unselectable.
- Rendered under the same `v-if="isDesktop"` that guards the rail (`ChatroomView.vue:172`).

`SResizeHandle` goes in `shared/ui/` and stays generic — it emits a delta and knows nothing
about rails, so the left agents rail can adopt it later (deliberately not wired here, §2).

**3. Container-relative plugin layout.** `.activity-host__plugin` declares
`container-type: inline-size` in `ActivityHost.vue`'s scoped block, and `MandalaGrid`'s
`sm:grid-cols-3` becomes a container variant with an explicit threshold —
`@min-[30rem]:grid-cols-3`. 30rem (480px) is chosen over Tailwind's `@sm` (24rem/384px)
because at 384px each of three cells is ~110px, still too narrow for a textarea; 480px gives
roughly 145px per cell, which is the point at which the grid reads as a grid. The threshold
is a judgement to confirm by eye during implementation (§12 records that jsdom cannot check
it).

Declaring the container on the **host**, not inside the plugin, is the load-bearing choice:
it makes "lay out against the surface you were given" a property of the plugin contract
rather than of one plugin, which is what [R30.19] needs when plugins later move into an
iframe.

### What is consciously given up

- A resized rail is still narrower than the message column, so a 3x3 worksheet at maximum
  width is workable but not spacious. Q-2 accepted this in exchange for keeping the
  conversation visible.
- The width is per-browser, not per-account. A facilitator who switches machines re-drags.
- `container-type: inline-size` establishes a new containment context on the plugin node.
  Any future plugin using `position: fixed` inside it would be positioned relative to that
  container rather than the viewport. That is a correctness improvement for a sandboxed
  plugin, but it is a behaviour change worth knowing about, so it is recorded here rather
  than discovered.

## 6. Detailed Changes

- **Backend** — none.
- **API contract** — none. `gen:api` rerun required: **no**.
- **Deploy/config** — none.

**Frontend**

| File | Change |
|---|---|
| `shared/ui/STabs.vue` | New `fill?: boolean` prop (default `false`); scoped rules for `.s-tabs--fill`, `.s-tabs--fill .s-tabs__panels`, and the fill-mode tabpanel. No change to markup, roles, or keyboard handling (`:44-74`). |
| `shared/ui/SResizeHandle.vue` | **New.** `role="separator"`, `aria-orientation`, `aria-valuenow/min/max`, `aria-label`; emits `resize` (delta px) and `reset`. Pointer + keyboard. |
| `shared/composables/useResizablePanel.ts` | **New.** `(opts: { storageKey, defaultWidth, min, max })` → `{ width, setWidth, nudge, reset }`. Clamping, `localStorage` persistence per `useTheme.ts:16-26`, re-clamp on viewport resize. |
| `shared/ui/index.ts`, `shared/composables/index.ts` | Barrel exports. |
| `slices/conversation/views/ChatroomView.vue` | `--chatroom-rail-w` in `grid-template-columns` (`:853`); `.chatroom__presence` gains `min-height: 0; overflow: hidden` (`:935-938`); `fill` on both `STabs` (`:175-179`, `:232-236` — the drawer one for consistency of the panel height contract); `SResizeHandle` rendered beside the rail under `v-if="isDesktop"`. |
| `slices/activities/components/ActivityPanel.vue` | `.activity-panel` gains `height: 100%; overflow-y: auto` (`:263-268`). |
| `slices/activities/components/ActivityHost.vue` | `.activity-host__plugin` gains `container-type: inline-size` (`:111-116` block). |
| `slices/activities/plugins/mandala9grid/MandalaGrid.vue` | `sm:grid-cols-3` → `@min-[30rem]:grid-cols-3` (`:124`). |
| `slices/conversation/locales/{en,zh-TW}.json` | Handle `aria-label` and its instruction text. Both files (gate #12). |

**Slice boundaries**: every new file lands in `shared/`, which both `conversation` and
`activities` may import (`frontend/eslint.config.js:20-49`). No cross-slice import is added,
so gate #1 and `check:boundaries-enforced` are unaffected.

**Global CSS**: none added. Every rule above is in a scoped block or in an existing
`shared/ui` component's scoped block, per [R24.30] and gate #6. The
`--chatroom-rail-w` custom property is set inline on `.chatroom` by the view, not in a
global stylesheet.

## 7. NFR Checklist

- [x] **i18n** — the handle's `aria-label` and keyboard hint go through `$t()` in both
  locale files. No other new user-facing string.
- [x] **Audit log** — N/A. Nothing here is a domain event; a rail width is a local display
  preference.
- [x] **Tenant isolation** — N/A. No new endpoint, no data access.
- [x] **Error handling UX** — N/A for new states. The one failure mode is `localStorage`
  being unavailable (private mode, quota), handled by falling back to the default width
  exactly as `useTheme.ts:17-26` does; the feature degrades to a fixed rail rather than
  throwing.
- [x] **Performance** — the drag writes a CSS custom property on one element; no component
  re-render is required for the visual update. `localStorage` is written on
  `pointerup`/keyup, not on every `pointermove`. The window `resize` listener is registered
  once and only while the composable is mounted.

## 8. Security Considerations

None — no auth surface, no tenant boundary, no provider key, no WebSocket, no file upload,
and no user input reaches the server. The one persisted value is a number in `localStorage`,
read back through a clamp, so a hand-edited value cannot push the layout outside its bounds.

## 9. Quality Notes

**Existing debt in touched files** — record, do not silently fix:

- `ChatroomPresence.vue:62-63` and `ObserverPanel.vue:142-143` both carry
  `height: 100%; overflow-y: auto` rules that have never had any effect (§4.1). After this
  change they start working. Do not delete them as "dead" during implementation — they are
  the correct rules, they were merely unreachable.
- `ActivityPanel.vue:31,61-73` fetches the activity type list with a bare `ref` + `watch`
  instead of TanStack Query, unlike every other read in the slice. Out of scope here;
  already recorded as FU-2 of `2026-08-09-platform-example-activity-types`.
- `MandalaGrid.vue:30-32` documents a single-column degrade keyed on field count. It is not
  wrong, but it is not a width fallback, and the comment reads as though it were. Clarify
  the comment when changing the adjacent line.
- `.s-tabs__panels { margin-top: 0 }` (`STabs.vue:231-233`) is a no-op rule.

**Patterns to follow** — exemplars:

- Grid item that must scroll: `.chatroom__feed` + `.messages`
  (`ChatroomView.vue:869-883`). This is the exact shape `.chatroom__presence` is missing.
- `localStorage` preference: `shared/composables/useTheme.ts:5,16-26` — module-level key,
  `try/catch` on both sides, silent fallback.
- Breakpoint-conditional rendering: `useBreakpoint`
  (`shared/composables/useBreakpoint.ts:26,51-53`) and its use at `ChatroomView.vue:172`,
  `:216`, `:226`.
- Accessible interactive primitive with roving keyboard handling: `STabs.vue:44-74` (arrow
  keys, Home/End, `preventDefault` only on handled keys) — mirror its keyboard shape in
  `SResizeHandle`.
- Screen-reader-only text: `.s-tabs__badge-live` (`STabs.vue:219-229`) and the
  `.sr-only` utility in `shared/styles/`.

**Reuse inventory** — use these, do not write new ones:

| Need | Use | Location |
|---|---|---|
| Desktop/tablet/mobile switch | `useBreakpoint()` → `isDesktop` | `shared/composables/useBreakpoint.ts:26,53` |
| Breakpoint constants | `BP` | `shared/composables/useBreakpoint.ts` |
| Preference persistence idiom | `useTheme` | `shared/composables/useTheme.ts:16-26` |
| Drawer body scroll (mobile path, already correct) | `SDrawer` | `shared/ui/SDrawer.vue:224` |
| Field/label/ARIA wiring, if the handle ever grows a labelled control | `SFormField` | `shared/ui/`, per [R24.26] |
| Existing tab semantics | `STabs` | `shared/ui/STabs.vue:77-142` |

## 10. Risks and Rollback

- **`STabs` is shared across six views.** The `fill` prop defaults to `false` and adds a
  modifier class, so the five non-chatroom call sites compile to identical CSS. The
  regression net is `shared/ui/__tests__/STabs.test.ts` plus a new assertion that the
  default render carries no fill class.
- **Re-clamping can move a user's chosen width.** Narrowing the window past
  `45vw < storedWidth` must shrink the rail rather than squeeze the feed to nothing. The
  clamp is applied on read and on `resize`; the stored value is only rewritten on an
  explicit user action, so returning to a wide window restores the user's choice rather than
  keeping the clamped one.
- **Container queries are new to this codebase.** Tailwind v4.3 supports them natively
  (`frontend/package.json:75`) and they are baseline in current evergreen browsers, but this
  is the first use — so the `@min-[30rem]` arbitrary variant must be confirmed to survive
  the production build, not only the dev server. `pnpm build` plus a look at the emitted CSS
  is the check.
- **jsdom does not perform layout.** No component test can prove the grid actually reflows
  at 480px; the unit tier can only assert the class is present. §12 states where the real
  verification happens instead of pretending coverage exists.
- **The chosen affordance is the weaker one for the widest worksheets.** At the 720px cap a
  3x3 grid gives ~145px per cell — usable, not generous. If that proves too tight in a real
  class, the fallback is the `SModal` option rejected in Q-2, which can be added later
  without undoing anything here (the container query makes the plugin correct in a modal
  too).
- Rollback: `git revert` per commit. No migration, no API change, no persisted server state
  — a revert leaves only an orphaned `localStorage` key, which the next read ignores.

## 11. Acceptance Criteria

- [ ] **AC-1** — On a desktop viewport, with an activity active whose rendered height
  exceeds the rail, the rail scrolls and every field plus the submit button is reachable.
  Nothing is clipped without a scrollbar.
- [ ] **AC-2** — The tab strip stays fixed while the panel below it scrolls; the tabs do not
  scroll out of view.
- [ ] **AC-3** — The same holds for the People and Observer tabs with overflowing content,
  and exactly one scrollbar appears in the rail (no nested scroll regions).
- [ ] **AC-4** — The five non-chatroom `STabs` call sites render byte-identical markup and
  classes to before the change.
- [ ] **AC-5** — Dragging the handle widens and narrows the rail; the message feed reflows
  and never collapses below its share.
- [ ] **AC-6** — The rail cannot be dragged below 200px or above `min(720px, 45vw)`.
- [ ] **AC-7** — The chosen width survives a page reload, and a `localStorage` value outside
  the bounds (hand-edited or persisted on a wider screen) is clamped on read rather than
  applied.
- [ ] **AC-8** — Shrinking the browser window re-clamps the rail so the feed keeps its
  minimum; widening it again restores the user's chosen width.
- [ ] **AC-9** — The handle is reachable by Tab, exposes `role="separator"` with
  `aria-orientation="vertical"` and live `aria-valuenow`/`aria-valuemin`/`aria-valuemax`,
  and resizes with Left/Right, Shift+Left/Right, Home, and End; Enter resets to 200px.
  Its pointer target is at least 44px wide ([R24.34]).
- [ ] **AC-10** — No drag handle renders below 1024px; the drawer path is unchanged.
- [ ] **AC-11** — An interrupted drag (pointer cancelled, window blurred) releases pointer
  capture and restores text selection.
- [ ] **AC-12** — With the rail at its default 200px the Mandala renders as a single
  column; widened past 480px it renders as a 3x3 grid. Verified visually, not only by class
  assertion.
- [ ] **AC-13** — Gates green: `pnpm lint` (all 12, notably #6 global CSS, #11
  accessibility, #12 i18n), `pnpm typecheck`, `pnpm test`, `pnpm build`,
  `pnpm run check:bundle-size`, `pnpm run check:type-coverage`,
  `pnpm run check:boundaries-enforced`. Backend gates N/A — no backend file changes.

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-2, AC-4 | component | `frontend/src/shared/ui/__tests__/STabs.test.ts` — extend: default render has no fill class and no height rule; `fill` render applies the modifier. |
| AC-1, AC-3 | component (structural) | `frontend/src/slices/activities/__tests__/ActivityPanel.test.ts` and a `ChatroomView` layout test asserting the rail's scroll ownership. jsdom computes no layout, so these assert **which element owns the overflow**, not that clipping stopped. The visual proof is the manual check below. |
| AC-5..AC-8, AC-11 | unit | new `frontend/src/shared/composables/__tests__/useResizablePanel.test.ts` — clamping at both bounds, persistence round-trip, out-of-range stored value, re-clamp on a simulated `resize`, and that a rejected `localStorage` write does not throw. |
| AC-9 | component | new `frontend/src/shared/ui/__tests__/SResizeHandle.test.ts` — roles and ARIA values, and each key producing the right delta. Follows `STabs.test.ts`'s keyboard-assertion style. |
| AC-10 | component | `ChatroomView` test with `useBreakpoint` mocked to tablet and mobile. |
| AC-12 | **manual, via the `run` skill** | jsdom cannot evaluate a container query and Vitest cannot measure a grid. Launch the app against the dev stack, open a room with the seeded `mandala-9grid` active, and confirm single-column at 200px and 3x3 past 480px. Record the result in the Deviation Log. |
| AC-13 | CI | the frontend gate set. Per `feedback_remote_ci_verification`, CI is authoritative over the local Windows host. |

**Coverage boundary, stated plainly:** no automated test in this repository proves the
reported symptom is gone, because the symptom is a layout outcome and the unit tier has no
layout engine. AC-1 and AC-12 are verified by hand. If that is not acceptable, the honest
fix is a Playwright spec — `frontend/e2e/` has no activities coverage at all today, recorded
as FU-2.

## 13. SRS Delta

**Amend [R24.32]** (the current text covers only the mobile collapse; the desktop rail's
contract is undocumented, which is why it was implementable without a scroll region):

> - **[R24.32]** Chat: single-pane at < 768 px; side panels (agent list, attachments) become
>   a drawer. At >= 1024 px the right rail is a persistent column whose width the user may
>   resize between a documented minimum and maximum, persisted locally per browser and
>   re-clamped so the message column always retains its minimum share. Every rail panel
>   scrolls within the rail: rail content is never clipped without a reachable scroll
>   region.

**New [R30.34]**:

> - **[R30.34]** An activity plugin lays out against the width of the host-provided
>   container, not the viewport, so the same plugin renders correctly in the chatroom rail,
>   in a widened rail, and in the isolating iframe host of [R30.19]. The host declares the
>   containment context; a plugin that cannot fit its preferred layout degrades to a single
>   column rather than overflowing ([R30.18]).

## 14. Open Questions

- **OQ-1** — Should the left agents rail (`220px`, `ChatroomView.vue:853`) get the same
  handle? The mechanism would serve it unchanged. Excluded from scope because it has no
  reported problem and a second handle raises a question this dossier does not answer: what
  happens when both are dragged wide on a 1280px screen.
- **OQ-2** — Should the rail width be remembered per room, or globally? This spec assumes
  globally, matching theme and locale. Per-room would suit a facilitator who runs
  worksheet-heavy and chat-heavy rooms differently, at the cost of a keyed store.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- **FU-1** — `.s-tabs__panels { margin-top: 0 }` (`STabs.vue:231-233`) is a no-op and should
  be removed once nothing depends on the selector existing.
- **FU-2** — `frontend/e2e/` has no activities coverage. The join → render → resize → submit
  path is the one flow where a real browser is the only honest verification (§12), and it
  would also serve `2026-08-09-platform-example-activity-types` (its FU-4 names the same
  gap).
- **FU-3** — Four other views host `STabs` inside a scrolling page
  (`AgentDetailView`, `RagConfigDetailView`, `KnowledgeMapConfigDetailView`,
  `ProjectKeysView`, `ProjectListView`). If any of them later needs a fixed tab strip with
  a scrolling panel, `fill` is now available rather than needing a second mechanism.
- **FU-4** — `MandalaGrid` mounts as a standalone Vue island via `createApp`
  (`plugins/mandala9grid/index.ts:27-33`), so it cannot use `@shared/ui` components or
  `useI18n`. Every plugin will re-hand-roll form markup for the same reason. A plugin-safe
  subset of the field primitives would remove that duplication before a third plugin
  exists.
