---
type: bugfix
status: in-progress
created: 2026-08-19
requirements: []
depends_on: []
---

# Chatroom scroll anchoring, streaming render, and composer

Source: `docs/audits/2026-08-19-page-presentation-scroll-and-feedback/findings.md`
(F-11, F-12, F-13, F-14, F-15, F-23, F-24, F-29, F-47, F-48, F-49).

## 1. Summary

The chatroom message feed does not hold its position, does not report what arrived, and
renders streaming text far more often than it needs to. Loading history throws the reader
240px or more toward older content and then tells them "100 new messages" arrived when
nothing did (F-11, F-12). A message containing a diagram, a formula, a highlighted code
block or an image scrolls into place at its pre-enhancement height and then grows several
hundred pixels underneath the fold, because nothing re-scrolls after the asynchronous
KaTeX/Mermaid/highlight pass and images carry no dimensions (F-13). Every streamed token
re-runs markdown-it, DOMPurify and a full `v-html` subtree replacement (F-15). The composer
never grows past one line (F-14). Around those, five smaller defects in the same surface:
the empty state pins to the top of the feed (F-23), history has no scroll-based auto-trigger
(F-24), there is no 1024-1279 layout (F-29 first arm), approval cards render after every
message with no pill (F-47), and the search panel double-counts the header (F-48). F-49 is
the structural defect underneath the first two: `useChatroomMessages` writes the feed
element's scroll position directly, so `useChatroomScroll` is not the only owner of that
state.

F-29's second arm - the agent rail rendering from 768px where the amended spec puts a drawer
- is **deliberately out of scope** and deferred to FU-6 per Q-8: it is the one item here
whose correction takes a surface away from users rather than restoring one.

Everything here is judged against `docs/UI/07-conversation.md`, which is detailed enough
that ten of the eleven findings are code-versus-intent rather than internal inconsistency.
One spec line is itself wrong and is corrected in §11.

## 2. Observed vs Expected

### F-11 (major) - "Load earlier" discards the reader's offset

- **Observed** - `frontend/src/slices/conversation/composables/useChatroomScroll.ts:96-98`
  captures only `scrollHeight`; `:99-104` assigns
  `el.scrollTop = el.scrollHeight - savedHeight`. The pre-prepend `scrollTop` is never read
  (the composable's only other `scrollTop` reads are `:33` and `:43`). The correct
  expression is `savedScrollTop + (newHeight - savedHeight)`. Driven from
  `frontend/src/slices/conversation/views/ChatroomView.vue:877-881`.
- **Expected** - `docs/UI/07-conversation.md:893`: "the scroll position is adjusted so the
  previously-topmost visible message remains in the same viewport position. Implementation
  uses `scrollTop` delta calculation before and after DOM update." Also the composable's own
  stated contract at `useChatroomScroll.ts:6,94`.

### F-12 (major) - the pill counts prepended history

- **Observed** - `useChatroomScroll.ts:60-67` derives `newCount` from raw deltas of
  `messageCount`, which is `messages.value.length` (`ChatroomView.vue:696`). `messages`
  (`useChatroomMessages.ts:116-149`) folds in `olderMessages`, which `loadEarlierPage`
  prepends at `:181` a page at a time with `PAGE_SIZE = 100` (`:34`). `onLoadEarlier`
  (`ChatroomView.vue:877-881`) wraps only the capture/restore pair. There is no flag, no
  paused watcher and no second counter anywhere in the slice.
- **Expected** - `docs/UI/07-conversation.md:912-914` scopes the pill to messages that
  "arrive while the user is scrolled up", and `:926` to a count of unseen messages.

### F-13 (major) - nothing re-scrolls after the async enhancement

- **Observed** - `useMarkdownEnhance.ts:53-56` calls `opts.onAfterUpdate?.()` (wired to
  `maybeStick` at `ChatroomView.vue:709`) synchronously inside `onUpdated`, while
  `schedule()` (`:44-50`) defers `run()` by `ENHANCE_DEBOUNCE_MS = 120` (`:14`) and `run()`
  awaits three dynamic imports plus `mermaid.render`
  (`utils/renderMarkdown.ts:126,140,163,171,185-187`) before mutating the DOM at `:149,174`.
  Growth is therefore strictly after the scroll, and because the enhancement mutates the DOM
  directly it triggers no further `onUpdated`. `AttachmentImage.vue:68-76` has
  `loading="lazy"`, no intrinsic `width`/`height` and no `@load` handler; its `url` ref
  resolves in `onMounted(load)` (`:65`) and re-renders only the child, so the parent's
  `maybeStick` never re-fires. The rendered image is capped at 360px
  (`AttachmentImage.vue:96`), so a late decode can add that much at once. A repository-wide
  grep for `ResizeObserver|IntersectionObserver` returns zero hits anywhere under
  `frontend/src/slices/conversation`.
- **Expected** - `docs/UI/07-conversation.md:907`: "User is at bottom (within 80px of
  scrollHeight) | New messages auto-scroll feed to bottom." The rule is about the message
  being visible, not about one scroll call having been issued.

### F-14 (major) - the composer never auto-grows

- **Observed** - `ChatroomComposer.vue:29-48` sets `rows="1"`; `onInput` (`:229-233`) emits
  the model value, a typing event and a mention refresh, and nothing else; `textareaRef` is
  handed only to `useMentionAutocomplete` (`:198-204`). No height is ever assigned, and the
  `min-height: 36px` / `max-height: 192px` pair at `:284-297` therefore fixes the box at one
  line that scrolls internally. The reserved 192px is dead CSS.
- **Expected** - `docs/UI/07-conversation.md:669-671`: "Min-height: 36px (single line) /
  Max-height: 192px (approximately 8 lines, then scrolls internally) / Auto-grows with
  content."

### F-15 (major) - streaming re-renders the full markdown pipeline per token

- **Observed** - `useChatroomSocket.ts:376-379` calls `store.appendAgentToken` on every
  `agent.token` frame; `stores/conversation.ts:97-103` reassigns `agentStreams` immutably on
  each call; `useAgentStreams.ts:26` keys its cache on `cached.source === text`, which can
  only hit for an agent whose text did not change and therefore never for the agent being
  appended to. Each frame is its own task, so each token schedules a render, a
  `renderMarkdown` (markdown-it plus DOMPurify, `useAgentStreams.ts:29`) and a full `v-html`
  subtree replacement in `ChatroomStreamingBubble.vue:16-19`. Grepping the slice for
  `requestAnimationFrame|throttle|debounce` finds only the 120ms enhancement timer at
  `useMarkdownEnhance.ts:14`, which gates the KaTeX/Mermaid/highlight pass and not the
  markdown render.
- **Expected** - `docs/UI/12-shared-patterns.md:474`: "Rendered through markdown pipeline
  (debounced at 120ms to avoid jitter)."

### F-23 (major) - the empty state pins to the top of the feed

- **Observed** - `ChatroomView.vue:135-141` places `SEmptyState` in a plain `<li>` of
  `<ol class="messages">`, which is `height: 100%; overflow-y: auto; padding: 16px` with no
  flex and no bottom anchoring (`:927-933`). `shared/ui/SEmptyState.vue:46-55` is
  `display: flex; flex-direction: column; align-items: center; margin: 0 auto`: horizontal
  centring only, no `justify-content`, no height.
- **Expected** - `docs/UI/07-conversation.md:1018`: "Vertically and horizontally centered in
  the message feed area."

### F-24 (minor) - history pagination has no scroll-based auto-trigger

- **Observed** - `ChatroomLoadEarlier.vue` is 48 lines containing one `SButton` that emits
  `load`: no lifecycle hooks, no observers. `useChatroomScroll.ts:49-52` recomputes
  `atBottom` from the bottom threshold only and never compares `scrollTop` against a top
  threshold. Nothing calls `loadEarlier` except the button handler
  (`ChatroomView.vue:57,877`).
- **Expected** - `docs/UI/07-conversation.md:895`: "when the user scrolls to within 100px of
  the top of the feed and `hasOlderMessages` is true, `loadEarlier()` triggers automatically
  (scroll-based pagination). The button remains as a fallback"; `:1312` names the file
  "Load-earlier button with auto-trigger on scroll".

### F-29 (minor) - no 1024-1279 layout, and the agent rail renders at `md`

- **Observed** - `ChatroomView.vue:903` is the unconditional four-track desktop grid
  (`220px 1fr 10px var(--chatroom-rail-w, 200px)`); `:1008-1011` is the two-track tablet
  grid; `:5` binds only `chatroom--mobile` and `chatroom--tablet`; the file's only `@media`
  is `prefers-reduced-motion` at `:968-973`. `useBreakpoint.ts:51-53` offers no `xl` arm and
  the view reads none. `ChatroomAgentSidebar` renders at `v-if="!isMobile"` (`:27-31`), so
  the rail is present from 768px, while the agents drawer is gated `v-if="isMobile"`
  (`:230-239`) and the comment at `:230` states the tablet-rail intent explicitly.
- **Expected** - `docs/UI/07-conversation.md:238` and the worked `@media` block at
  `:241-252`: at 1024-1279 both rails collapse to toggleable overlay panels. `:258`: below
  1024px the view is a single column with the panels as drawers.
  `docs/UI/11-responsive-a11y.md:116-133` now agrees: the `md` Layout cell reads "Single
  pane", the Agent list cell reads "Drawer", and the note at `:126-133` records that the
  previous "2-column" wording was the error and that the `lg+` column describes `xl`.
- **Scope** - only the first arm (the missing 1024-1279 band) is fixed here. The `md` rail
  deviation is left in place and deferred to FU-6 per Q-8, so after this dossier the code
  still knowingly disagrees with `11-responsive-a11y.md:118,121` at 768-1023. That is a
  recorded, deliberate divergence, not an oversight.

### F-47 (minor) - approval cards render last and raise no pill

- **Observed** - `ChatroomView.vue:117-125` renders `liveApprovals` as a flat `v-for` after
  the message `TransitionGroup` (`:94-115`), so their position is list order.
  `liveApprovals` is `orchStore.getApprovalsForRoom(chatroomId)` (`:815`), which is
  `Object.values(liveApprovals[roomId] ?? {})` (`shared/stores/orchestration.ts:84-86`):
  insertion order, not time order. `messageCount` (`:696`) counts only `messages.value`, so
  `useChatroomScroll.ts:60-67` never fires for an approval.
- **Expected** - `docs/UI/07-conversation.md:988`: "approval cards are placed in the message
  feed at the chronological position where the approval was requested, interleaved with
  regular messages." The pill arm follows from `:907-908`, which makes the pill the signal
  for anything arriving while the reader is scrolled up.

### F-48 (minor) - the search panel is offset 48px too far down

- **Observed** - `.chatroom__feed` is `grid-row: 2` of `grid-template-rows: 48px 1fr auto
  auto` (`ChatroomView.vue:904,919-925`) and is the `position: relative` containing block,
  so its top edge already sits below the 48px header. `ChatroomSearchPanel.vue:95-107` then
  adds `position: absolute; top: 48px`, landing the panel 96px from the chatroom top.
- **Expected** - `docs/UI/07-conversation.md:742`: "Panel: slides down from below header,
  absolute positioned."

### F-49 (plausible) - the send path scrolls the feed directly

- **Observed** - `useChatroomMessages.ts:278-282` calls `listRef.value.scrollTo` on the raw
  element. The composable receives `listRef` at `:38` and uses it nowhere else; it has no
  access to `scrollToBottom`, which is what resets `newCount` and `atBottom`
  (`useChatroomScroll.ts:45-46`).
- **Expected** - internal consistency: `useChatroomScroll` is documented at `:1-9` as the
  owner of feed scroll state. The audit rates the symptom cosmetic (a programmatic
  `scrollTo` fires a real `scroll` event and `onScroll` at `:49-52` resets both refs on the
  next frame) and the duplication structural.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | F-11 and F-49: correct each copy of the scroll-to-bottom logic locally, or consolidate so `useChatroomScroll` is the only writer of the feed's scroll position? | Consolidate. `useChatroomMessages`'s `listRef` parameter (`:38`) is removed and replaced with an `onSent?: () => void` callback; `ChatroomView` passes `scrollToBottom`. The raw `scrollTo` at `:278-282` goes away entirely, and `useChatroomScroll` becomes the only module in the slice that touches `scrollTop`/`scrollTo` on the feed. | The audit's own diagnosis (F-49: "this is a second copy of scroll-to-bottom logic with a different contract, which is how F-11 and F-13 drifted apart") is the argument. `listRef` is used for nothing else in that file, so the parameter is pure coupling: a composable whose job is pagination and CRUD is reaching into a DOM element the view owns. Consolidating also makes F-12's and F-13's fixes single-sited. It costs one signature change and its test updates. |
| Q-2 | F-12: suppress the count with a flag around the prepend, with a separate counter that only new-message events increment, or by watching a different source? | Watch a different source. `useChatroomScroll`'s second parameter changes from `messageCount: ComputedRef<number>` to the ordered list of feed item ids (`ComputedRef<readonly string[]>`). The composable remembers the id of the newest item the reader has acknowledged (set by `scrollToBottom` and by `onScroll` reaching bottom) and derives `newCount` as the number of ids after it. | This is the option that cannot silently regress. A flag has to be set by every current and future prepend path, and `loadEarlierPage` already has a retry arm (`useChatroomMessages.ts:195`) that would need it too. An event-fed counter puts the pill's correctness in the socket layer and would under-count anything that arrives by refetch rather than by a WS frame, which the connect-time reconcile does. Deriving from tail identity makes a prepend structurally incapable of moving the counter, because a prepend does not change the tail. It also gives F-47's pill arm for free once approvals are in the same ordered list. |
| Q-3 | F-13: a completion callback from `useMarkdownEnhance`, a `ResizeObserver` on the feed, or intrinsic image dimensions? | A `ResizeObserver` on the `<ol class="messages">` content, calling `maybeStick`. `useMarkdownEnhance` keeps `onAfterUpdate` as-is. | It is the only source-agnostic option. A completion callback covers exactly the three passes `enhanceRenderedMarkdown` drives (`renderMarkdown.ts:185-187`) and still misses image decode, web-font swap and any future async growth. Intrinsic dimensions are not available: `Attachment` (`slices/conversation/types/index.ts:105-114`) carries no width or height, so adding them is a backend change and out of this dossier's scope. The pattern is already established in the repository with a jsdom guard (`shared/composables/useResizablePanel.ts:129-133`) and a test stub (`shared/composables/__tests__/useResizablePanel.test.ts:36-54`), so this is a first use in the conversation slice but not in the codebase. The observer must call `maybeStick`, which already gates on `atBottom` (`useChatroomScroll.ts:55-57`), so a reader who scrolled up is never yanked. |
| Q-4 | F-15: throttle between the socket and the store, or inside `useAgentStreams`? | Between the socket and the store. `useChatroomSocket` buffers `agent.token` text per agent and flushes to `store.appendAgentToken` at most every 120ms, with a forced flush on `agent.finished`, on `agent.error`, on room change and on unmount. `useAgentStreams` is left as a pure computed. | Throttling at the store boundary collapses the churn at its source: one store write per 120ms instead of one per token, so every downstream consumer of `agentStreams` benefits, not just the bubble. Throttling inside `useAgentStreams` would leave `stores/conversation.ts:97-103` reassigning the whole map per token and would require turning a pure `computed` (`useAgentStreams.ts:14-38`) into a timer-driven `ref`, which is harder to reason about and harder to test. 120ms is the value `docs/UI/12-shared-patterns.md:474` specifies. The forced flushes are the load-bearing part: without them a reply's last tokens would be dropped when the bubble is cleared on `agent.finished`. |
| Q-5 | F-23: fix it here with a local override, or wait on `2026-08-19-shared-overlay-and-shell-defects` (its F-30, which gives `SEmptyState` vertical centring)? | Fix it here, and do not depend on that dossier. The empty-state `<li>` in `ChatroomView.vue:135-141` gets a rule that makes it fill the feed and centre its child. | F-30's fix alone is neither necessary nor sufficient for F-23. The `<li>` has auto height, so a stretched-and-self-centring `SEmptyState` inside it would still have no extra height to distribute and would still render flush at the top. The height has to come from the chatroom side regardless. The local rule is also forward-compatible: a self-centring `SEmptyState` inside a centring flex wrapper still centres, so F-30 landing later changes nothing here. Serialising the two dossiers would buy no correctness. |
| Q-6 | F-24: `IntersectionObserver` on the load-earlier row, or a scroll-threshold check in `onScroll`? | `IntersectionObserver` on the load-earlier `<li>`, with `root` set to the feed and `rootMargin: '100px 0px 0px 0px'` to express the spec's 100px directly. Armed only while `hasOlderMessages` is true and `loadingOlder` is false, and disarmed from the moment `captureBeforePrepend` runs until `restoreAfterPrepend`'s `nextTick` has completed. | The threshold is a geometry question, which is what the observer API answers; a check inside `onScroll` (`useChatroomScroll.ts:49-52`) would run on every scroll frame and would need its own re-entrancy guard to avoid firing a second load before the first resolves. The precedent, including the `typeof IntersectionObserver === 'undefined'` guard for jsdom and SSR, is `shared/composables/useRevealOnScroll.ts:20-33`, with a test stub at `shared/composables/__tests__/useRevealOnScroll.test.ts:24-47`. On the interaction with F-11: the correct restore leaves the reader's message at the same viewport position, which puts the sentinel a full page above the viewport, so the observer stops intersecting by construction. The explicit disarm is belt and braces for the frame between prepend and restore, where the sentinel is momentarily at the top of a taller list. |
| Q-7 | F-29 first arm: implement the `@media` block at `docs/UI/07-conversation.md:241-252` as written? | Yes, adapted to the four-track grid. Between 1024 and 1279 the grid collapses to `1fr` for the feed, the agent rail and the presence rail become absolutely positioned overlay panels at `top: 48px; bottom: 0` with `--z-dropdown` and `--shadow-lg`, and the resize handle track is removed with them. Header toggles open them, reusing the `agentsDrawerOpen` / `peopleDrawerOpen` refs (`ChatroomView.vue:822,529`). | The spec block is concrete and correct; the only thing it does not know about is the 10px handle track added by `2026-08-09-chatroom-rail-scroll-and-resize`, which has nothing to size when the rail is an overlay. Reusing the existing open-state refs avoids a third set of visibility state for the same two panels. |
| Q-8 | F-29 second arm: the agent rail currently renders from 768px up (`ChatroomView.vue:28`) where the amended spec says drawer. Move it? | **No, deferred to FU-6.** This dossier implements only F-29's first arm (Q-7's 1024-1279 overlay band). `ChatroomAgentSidebar` keeps `v-if="!isMobile"` (`:28`), the agents `SDrawer` keeps `v-if="isMobile"` (`:232`), `.chatroom--tablet`'s two-track grid (`:1008-1011`) is untouched, and the comments at `:230` and `:1008` stay as they are, because they continue to describe the shipped behaviour accurately. | The intent sources do say `md` is a single pane (`docs/UI/11-responsive-a11y.md:118,121`, `docs/UI/07-conversation.md:258`), so the deviation is real. But unlike every other item in this dossier, correcting it removes a surface tablet users have today: it is a visible capability regression from a user's point of view, not a restoration of intended behaviour, and it does not belong inside a scroll-and-composer bugfix where it would ship unannounced alongside ten genuine fixes. It is also cleanly separable: `isTablet` is 768-1023 and `isDesktop` is `>= 1024` (`useBreakpoint.ts:52-53`), so Q-7's compact band sits entirely inside `isDesktop` and never touches the tablet grid. Deferring costs nothing here and lets the removal be its own reviewable change. |
| Q-9 | F-48: fix the offset only, or also add the dimming overlay that `docs/UI/07-conversation.md:747` specifies and the code lacks? | Offset only (`top: 48px` becomes `top: 0` in `ChatroomSearchPanel.vue:97`). The dimming overlay is recorded as FU-1. | The offset is a regression against a positioning contract the containing block already satisfies, and it is a one-line correction with no new behaviour. The overlay is a new affordance: it needs a stacking decision against `--z-dropdown`, a click-to-dismiss decision, and a `prefers-reduced-motion` decision, none of which `07-conversation.md` §3.7 specifies. Adding it here would smuggle undesigned behaviour into a scroll-and-composer bugfix. |
| Q-10 | F-14: auto-grow with CSS `field-sizing: content` or with a JavaScript height assignment? | JavaScript. On input and on every `modelValue` change, reset `height` to `auto`, then set it to `min(scrollHeight, 192px)`. | `field-sizing` is not available at the documented browser floor (`docs/UI/11-responsive-a11y.md:346-347`: iOS Safari 16.2+, Chrome Android 110+); it is Chromium-only and recent. Driving it from the model change rather than only from `onInput` (`ChatroomComposer.vue:229-233`) is what makes the box shrink back after a send clears the draft (`useChatroomMessages.ts:277`) and grow after a programmatic mention insert (`:203`), both of which bypass the input event. |
| Q-11 | Is `depends_on: []` true against the other three dossiers spawned by this audit? | Yes, verified. `2026-08-19-content-area-spacing-and-scroll-contract` strips duplicate root padding from the 34 views in the audit's F-3 set. Four conversation views are in that set: `ChatroomListView.vue:213`, `WorkspaceListView.vue:139`, `WorkspaceSettingsView.vue:45` and `ChatroomSettingsView.vue:227`, each a `<main class="p-6">` root. This dossier touches none of them. `ChatroomView.vue` has no padded root (`:2-10`, `class="chatroom"`) and its route opts out of shell padding entirely (`slices/conversation/routes.ts:26`, cited in the audit's F-3), so it is not in the padded set. `2026-08-19-shared-overlay-and-shell-defects` is cleared by Q-5. `2026-08-19-transient-feedback-channels` touches `shared/composables/useToast.ts`, `app/errorHandler.ts` and the admin/workflow/tenancy slices, none of which appear in §7 below. | An empty `depends_on` is a positive claim under `docs/tasks/README.md`, so it was checked file by file rather than assumed. The only shared-tree file this dossier reads is `shared/stores/orchestration.ts:84-86`, and Q-12 keeps it read-only. |
| Q-12 | F-47's ordering: sort inside `getApprovalsForRoom` (shared store) or in the view? | In the view. `ChatroomView` merges `messages` and `liveApprovals` into one ordered feed list, keying approvals on `started_at` (`shared/types/workflow.ts:22`) against messages' `created_at`, and renders that one list. `shared/stores/orchestration.ts:84-86` is left alone. | The store is shared and has non-chatroom consumers; imposing a chatroom-specific ordering on it would be a layer violation in the direction `shared` must never take. The merged list is also what Q-2's tail-identity counter needs in order to raise the pill for an approval, so both arms of F-47 fall out of the same change. Approvals carry `started_at`, not `created_at`, so the merge reads a per-kind key rather than one field name. |

## 4. Reproduction

All items need a chatroom with more than 100 persisted messages unless stated otherwise.
Preconditions: a logged-in user with access to a project workspace, and at least one bound
agent for the streaming items.

**F-11 and F-12** (one run reproduces both):
1. Open `/chatrooms/:id` at 1440x900 in a room with 250 or more messages.
2. Scroll up until the "Load earlier" button is visible but not flush at the top, roughly
   `scrollTop = 240`. It is the first `<li>` of the feed (`ChatroomView.vue:54-59`), so it
   only has to be in view.
3. Click it.
4. Observe: the feed jumps upward toward older content by about the amount that was scrolled
   past, and the message being read moves down out of view. Simultaneously a pill appears at
   the bottom of the feed reading "100 new messages" although nothing arrived. Clicking the
   pill discards the history that was just loaded.

**F-13**:
1. Pin to the bottom of a room and have an agent reply with a message containing a Mermaid
   fence, a `$$...$$` block, or an image attachment.
2. Observe: the feed scrolls to the bottom at the message's pre-enhancement height, then
   120ms or more later the diagram, formula or image resolves and pushes the message body
   below the fold. Manual scrolling is needed to read the reply that was being watched.

**F-14**: type five lines into the composer with Shift+Enter. The box stays at roughly 36px
and scrolls internally; only the last line is visible.

**F-15**: open DevTools Performance, have an agent stream a long reply, and record. Each
`agent.token` frame produces its own render, `renderMarkdown` call and `v-html` replacement.
Visible symptom without profiling: highlighting applied on one pass is discarded on the
next, so a streamed code block flickers between highlighted and plain.

**F-23**: create a chatroom and open it. The icon, title and text sit flush at the top of the
feed with 2rem of padding, with the rest of the feed height empty down to the composer.

**F-24**: scroll-wheel to the top of a 500-message room. Scrolling stops dead; history does
not continue, and the button must be found and clicked once per page.

**F-29**: open `/chatrooms/:id` at exactly 1024x768. Fixed chrome consumes
220 + 10 + 200 = 430px, leaving roughly 594px for the feed, with no way to collapse either
rail. (At 800x600 the agent rail is present where the spec puts a drawer. That is the second
arm, deferred to FU-6, and it should still reproduce after this dossier lands.)

**F-47**: with the reader scrolled up, have a workflow request an approval. The card appends
below every message, off-screen, with no pill. After resolution it stays pinned below all
messages rather than at the point in the conversation where it was requested.

**F-48**: press Ctrl+K. The panel opens with a bare 48px strip of feed (typically the "Load
earlier" button) visible above it.

**F-49**: scroll up until the pill shows a count, then type and send. The feed jumps to the
bottom with the stale pill still painted for one frame.

## 5. Root Cause Analysis

**F-49 is the root cause of the cluster, not a symptom of it.** The chain runs:
`useChatroomMessages` was given `listRef` (`:38`) so that its send path could scroll the
feed (`:278-282`), which created a second writer of the feed's scroll position alongside
`useChatroomScroll`. With two writers and no single owner, neither side's contract is
complete: `useChatroomScroll` owns `atBottom`/`newCount` but is not told about sends, and
`useChatroomMessages` owns sends but cannot reset those refs. The earliest link whose
correction prevents the whole class is the ownership split, which is why Q-1 consolidates
rather than patching two call sites.

**F-11 root cause**: `useChatroomScroll.ts:96-98` captures one of the two quantities the
restore needs. `savedHeight` alone is sufficient to compute the height delta but not to
place the reader inside it. Aggravating: the composable's docstring at `:6` asserts the
correct behaviour, so a reader of the file would not look for the defect at `:102`.

**F-12 root cause**: `useChatroomScroll.ts:60-67` treats "the list got longer" as "messages
arrived", which is true only if the list is append-only. It is not (`useChatroomMessages.ts:181`
prepends). The count and the position are two views of the same wrong premise, which is why
F-11 and F-12 always co-occur.

**F-13 root cause**: `useMarkdownEnhance.ts:53-56` fires `onAfterUpdate` at the start of the
debounce window rather than at the end of the work. The scroll is therefore issued against a
height that is known to be provisional. Contributing independently: `AttachmentImage.vue:68-76`
gives the layout no way to reserve space for an image before it decodes, and there is no
observer anywhere in the slice that could notice either kind of growth. Two separate causes,
one symptom, which is why Q-3 chose the mechanism that covers both rather than the one that
covers the first.

**F-14 root cause**: the CSS was written to the spec (`ChatroomComposer.vue:284-297` has both
the min and the max) and the behaviour that would move the box between them was never
written. There is nothing to correct, only something to add.

**F-15 root cause**: `useChatroomSocket.ts:376-379` propagates every frame to reactive state
synchronously. The memoisation at `useAgentStreams.ts:26` was intended as the mitigation and
cannot be one, because during streaming the text changes on every evaluation by definition.
Aggravating: `docs/UI/07-conversation.md:513` states that the cache solves this, so the
defect is documented as already fixed. That line is corrected in §11.

**F-23 root cause**: the empty state is rendered as a list item in a list whose items size to
content. Contributing: `SEmptyState.vue:46-55` has no vertical centring, which is F-30 in a
sibling dossier and is neither necessary nor sufficient here (Q-5).

**F-24**: unimplemented. The button exists, the auto-trigger half of
`docs/UI/07-conversation.md:895` was never built, and `ChatroomLoadEarlier.vue` has no
lifecycle at all.

**F-29**: `useBreakpoint` exposes three bands (`:51-53`) and the chatroom's layout needs four.
The view consumed the vocabulary it was given, which is why the 1024-1279 band has no
expression at all. The second arm is a straightforward code-versus-spec deviation whose
ambiguity is now resolved in the documents but whose correction is deferred by Q-8, because
resolving the ambiguity settles what the layout *should* be without settling when it is
acceptable to take the tablet rail away.

**F-47**: the approval list was rendered as a second, independent `v-for` (`ChatroomView.vue:117-125`)
rather than merged into the feed's ordering, and `messageCount` (`:696`) was defined over
messages only. Both follow from there being no single "feed items" concept in the view.

**F-48**: the panel was written to be positioned against the chatroom root and mounted
against the feed. The 48px is correct for the container it was designed for and wrong for
the container it has (`ChatroomView.vue:919-925`).

## 6. Blast Radius and Sibling Suspects

**Blast radius**

- **F-11, F-12, F-49**: every history load in every chatroom, for every user. No data impact;
  nothing is persisted from any of the three.
- **F-13**: every message containing a diagram, formula, highlighted code block or image
  attachment, which is the product's core surface.
- **F-14**: every multi-line message in every chatroom.
- **F-15**: every streamed agent reply. The cost is main-thread time and battery, and it
  scales with reply length, so the worst case is the longest and most valuable replies.
  Q-4's fix changes the timing of store writes, which is observable to
  `useChatroomSocket.test.ts` and to anything else watching `agentStreams`.
- **F-23**: every newly created chatroom, which is the first thing a new user sees.
- **F-24**: reading any conversation longer than one page.
- **F-29**: as scoped, every viewport between 1024 and 1279 (small laptops, iPad landscape at
  full width). The 768-1023 band is untouched by this dossier and keeps today's behaviour.
- **F-47**: every approval-gated workflow run.
- **F-48**: chatroom search, every invocation.

**Sibling suspects**

- **Other direct writes to a scroll container inside the conversation slice**: swept.
  `useChatroomMessages.ts:280-281` is the only one outside `useChatroomScroll`. The audit's
  repository-wide `scrollHeight` grep (recorded under F-14) returned the two scroll
  composables and `slices/prompt-studio/components/PromptAssistantPanel.vue:118`, which is a
  different slice and a different defect (audit F-16, not in this dossier's scope).
  **Confirmed and fixed** by Q-1.
- **Other prepend paths that could move a length-derived counter**: `loadEarlierPage`
  (`useChatroomMessages.ts:165-202`) is the only prepend, and it has two entry points, the
  normal call at `:153` and the poisoned-anchor retry at `:195`. A flag-based fix would have
  to cover both; Q-2's tail-identity derivation covers any number of them. **Cleared by
  construction.**
- **Other feed content that grows asynchronously after render**: `ChatroomStreamingBubble`
  (`v-html` at `:16-19`, re-rendered per token today), `ChatroomMessageBubble`'s rendered
  markdown, `AttachmentImage` (`:68-76`), and the enhancement pass itself. All four are
  inside the observed `<ol>`, so Q-3's `ResizeObserver` covers all four. **Confirmed and
  covered.**
- **Other `SEmptyState` instances inside the conversation slice that would show F-23's
  symptom**: swept. The chatroom feed instance (`ChatroomView.vue:135-141`) is the only one
  in a full-height scroll container. The other conversation empty states sit in normal
  document flow in the four padded list/settings views, where top alignment is correct.
  **Cleared.**
- **Other absolutely positioned children of `.chatroom__feed` that could double-count the
  header**: `ChatroomSearchPanel` (`:95-107`) and `.chatroom__pill` (`ChatroomView.vue:1001-1006`).
  The pill is anchored `bottom: 16px`, which is measured from the feed's bottom edge and is
  therefore correct: `docs/UI/07-conversation.md:922` puts it "16px above the typing
  indicator", and the typing indicator is the next grid row (`:975-978`). **Pill cleared;
  panel confirmed.**
- **Other consumers of `useBreakpoint` that assume three bands where the spec has four**: out
  of scope for this dossier, but noted. `useBreakpoint.ts:5` does export `BP.xl = 1280`, so
  the vocabulary exists and only the chatroom needs it today. **Cleared for this scope**,
  recorded as FU-2.
- **Other per-frame WebSocket handlers that write reactive state without throttling**: the
  remaining `useChatroomSocket` cases are discrete events (`agent.thinking`, `agent.warning`,
  `message.created`, `message.updated`, `message.deleted`, presence), not per-token streams.
  Only `agent.token` (`:376-379`) is high-frequency. **Cleared.**

## 7. Fix Design

Files touched, all under `frontend/src/slices/conversation` except where noted:

1. **`composables/useChatroomScroll.ts`** (F-11, F-12, F-24, F-49 and the anchor for F-13):
   - Signature: second parameter becomes `feedIds: ComputedRef<readonly string[]>`.
   - Add `savedScrollTop` alongside `savedHeight` in `captureBeforePrepend` and restore with
     `savedScrollTop + (el.scrollHeight - savedHeight)`, clamped at 0 (F-11).
   - Replace the `newCount` delta watch with a tail-identity derivation: hold
     `lastSeenId: string | null`, set it to the last element of `feedIds` in `scrollToBottom`
     and whenever `onScroll` observes `atBottom`, and compute `newCount` as the number of ids
     after `lastSeenId` (all of them when `lastSeenId` is absent from the list, which is what
     a full cache replacement looks like) (F-12, F-47 pill arm).
   - Add a `ResizeObserver` on the feed element, guarded on
     `typeof ResizeObserver !== 'undefined'`, calling `maybeStick`; disconnect in
     `onBeforeUnmount` alongside the existing scroll listener teardown at `:110-113` (F-13).
   - Add an `IntersectionObserver` factory `observeTop(el, onReach)` with `root` = the feed
     and `rootMargin: '100px 0px 0px 0px'`, guarded on `typeof IntersectionObserver !==
     'undefined'`, plus an internal `armed` flag that `captureBeforePrepend` clears and
     `restoreAfterPrepend`'s `nextTick` sets (F-24).
   - Nothing else in the slice writes `scrollTop` or calls `scrollTo` on the feed after this
     change (F-49).

2. **`composables/useChatroomMessages.ts`** (F-49): drop the `listRef` parameter at `:38`,
   add `onSent?: () => void`, and replace `:278-282` with `onSent?.()` after the `nextTick`.
   The jsdom `scrollTo` guard at `:280` goes away with the raw access.

3. **`views/ChatroomView.vue`**:
   - Build one ordered `feedItems` computed merging `messages` (keyed on `created_at`) and
     `liveApprovals` (keyed on `started_at`), and render it as a single `v-for` in place of
     the `TransitionGroup` at `:94-115` plus the approvals `v-for` at `:117-125`, branching
     on item kind. `feedIds` for Q-2 is derived from it, and `messageCount` (`:696`) is
     deleted (F-47, and the input for F-12).
   - Pass `scrollToBottom` as `useChatroomMessages`' `onSent` (F-49).
   - Wrap the empty-state `<li>` (`:135-141`) so it fills the feed and centres its child:
     the `<li>` gets `flex: 1; display: flex; align-items: center; justify-content: center`
     and `.messages` gets `display: flex; flex-direction: column` so the item can grow.
     Verify against `:927-933` that the added flex context does not change the stacking of
     ordinary bubbles (F-23).
   - Attach the load-earlier `<li>` (`:54-59`) to `observeTop`, calling `onLoadEarlier` when
     it reaches the threshold and `hasOlderMessages && !loadingOlder` (F-24).
   - Add the 1024-1279 `@media` block per Q-7, and a `chatroom--compact` class bound from a
     new `isCompactDesktop` computed (`width >= BP.lg && width < BP.xl`, read from
     `useBreakpoint`'s `width`, which is already exported at `useBreakpoint.ts:47`) (F-29
     first arm). The header toggles reuse `agentsDrawerOpen` / `peopleDrawerOpen`
     (`:822,529`); at this band only the rails render (`ChatroomAgentSidebar` is
     `v-if="!isMobile"`, the drawers are `v-if="isMobile"` / `v-if="!isDesktop"`), so the two
     refs drive a class on the rail rather than an `SDrawer`, and no third visibility state
     is introduced.
   - **Not changed**: `ChatroomAgentSidebar`'s guard at `:28`, the agents drawer guard at
     `:232`, `.chatroom--tablet` (`:1008-1011`) and the comments at `:230` and `:1008` all
     stay exactly as they are (Q-8, FU-6). Q-7's band is entirely inside `isDesktop`
     (`useBreakpoint.ts:53`), so the compact rules must not leak into `.chatroom--tablet`.

4. **`composables/useChatroomSocket.ts`** (F-15): buffer `agent.token` payloads per agent in
   a module-local map and flush to `store.appendAgentToken` on a 120ms timer. Force a flush
   before `clearAgentStream` on `agent.finished`, on the error path, and in the existing
   teardown. `stores/conversation.ts:97-103` is unchanged: it still appends, just less often.

5. **`components/ChatroomComposer.vue`** (F-14): add a `resize()` that sets
   `textareaRef.value.style.height = 'auto'` then
   `= Math.min(scrollHeight, 192) + 'px'`, called from `onInput` (`:229-233`) and from a
   `watch` on `props.modelValue`. The existing `max-height: 192px` at `:287` stays as the
   internal-scroll backstop, and `min-height: 36px` at `:286` keeps the empty state at one
   line.

6. **`components/ChatroomSearchPanel.vue`** (F-48): `top: 48px` at `:97` becomes `top: 0`.

**Why this does not merely mask the symptoms.** F-11's restore is corrected to the
expression the spec names rather than being compensated elsewhere. F-12 is fixed by removing
the false premise (list length equals arrival count), not by subtracting a known page size,
which would break the moment `PAGE_SIZE` or the dedupe at `useChatroomMessages.ts:180`
changed the number actually prepended. F-13 observes the growth instead of predicting where
it will come from. F-15 removes the work rather than making it cheaper. F-49 removes the
second writer instead of synchronising two.

**Data repair**: none. No defect in this dossier writes anything to the server, to
`localStorage` or to the query cache.

## 8. Regression Test Plan

Written first, failing against current code. `frontend/src/slices/conversation/__tests__/useChatroomScroll.test.ts`
exists today and is 68 lines covering `scrollToMessage` only (`:33-68`); it is the natural
home for most of the unit tier. Note the path: the file is in the slice's top-level
`__tests__/`, not in `composables/__tests__/`.

**This is where jsdom stops.** jsdom performs no layout: `scrollHeight`, `clientHeight` and
`scrollTop` are all writable stubs that never reflect content, `IntersectionObserver` and
`ResizeObserver` do not exist, and no computed geometry is available. The split below is the
same one `docs/tasks/2026-08-09-chatroom-rail-scroll-and-resize` §12 drew for the rail work,
and it closed with four acceptance criteria deliberately unchecked (its D-5) because the
manual browser pass was skipped. That precedent is why this section names the browser items
up front rather than discovering them at the end.

| ID | Finding | Level | Location and assertion |
|---|---|---|---|
| T-1 | F-11 | unit (jsdom) | `useChatroomScroll.test.ts`: set the stub element's `scrollHeight = 2000`, `scrollTop = 240`, call `captureBeforePrepend()`, set `scrollHeight = 3000`, call `restoreAfterPrepend()`, flush `nextTick`, assert `scrollTop === 1240`. Fails today with 1000. This is pure arithmetic on writable properties, so it is genuinely testable. |
| T-2 | F-12 | unit (jsdom) | `useChatroomScroll.test.ts`: drive the composable with a mutable id list. From a scrolled-up state, prepend 100 ids and assert `newCount === 0` and `showPill === false`; then append 3 and assert `newCount === 3`. Fails today at the first assertion with 100. |
| T-3 | F-49 | unit (jsdom) | `useChatroomMessages.test.ts`: assert `onSent` is invoked once after a successful send, and that the composable never touches a DOM element (the `listRef` parameter is gone, so this is enforced by the signature and pinned by the updated mount helper). |
| T-4 | F-13 | unit (jsdom, stubbed observer) | `useChatroomScroll.test.ts` with a `ResizeObserver` stub modelled on `shared/composables/__tests__/useResizablePanel.test.ts:36-54`: assert the composable constructs and observes; assert firing the callback while `atBottom` calls `scrollToBottom`, and while scrolled up does not. Proves the wiring and the gate, not that content stopped being pushed below the fold. |
| T-5 | F-24 | unit (jsdom, stubbed observer) | `useChatroomScroll.test.ts` with an `IntersectionObserver` stub modelled on `shared/composables/__tests__/useRevealOnScroll.test.ts:24-47`: assert `rootMargin` is `'100px 0px 0px 0px'`; assert the callback fires the handler once; assert it is a no-op between `captureBeforePrepend` and the tick after `restoreAfterPrepend`. |
| T-6 | F-15 | unit (fake timers) | `useChatroomSocket.test.ts`: deliver 30 `agent.token` frames inside one 120ms window and assert `appendAgentToken` was called once with the concatenated text; assert `agent.finished` forces a flush so no trailing token is lost; assert unmount mid-window flushes. Fails today on the first assertion with 30 calls. |
| T-7 | F-14 | component | `ChatroomComposer` test: stub `scrollHeight` on the textarea, dispatch `input`, assert the inline `height` is `min(scrollHeight, 192)px`; assert clearing `modelValue` returns it to the one-line height. jsdom's `scrollHeight` is a stub, so this pins the formula and the trigger, not the rendered line count. |
| T-8 | F-47 | component | `ChatroomView.test.ts`: with one message at T+0, one approval `started_at` T+1 and one message at T+2, assert the feed's rendered item order is message, approval, message. Fails today with message, message, approval. Second assertion: an approval arriving while scrolled up raises the pill. |
| T-9 | F-29 (first arm) | component | `ChatroomView.test.ts`, using the `setViewport` helper already in that file at `:507-511`: at 1100 assert `chatroom--compact` is bound and no resize handle renders; at 1400 assert the existing three-rail assertions at `:518-537` still hold. Plus a **guard against Q-8 leaking in**: at 800 assert `ChatroomAgentSidebar` is still present and `chatroom--compact` is not bound, pinning the deferral so a later compact-band edit cannot silently take the tablet rail with it. Class and presence assertions only. |
| T-10 | F-23, F-48 | component (structural) | `ChatroomView.test.ts` / `ChatroomSearchPanel.test.ts`: assert the empty-state `<li>` carries the filling class and that `.search-panel`'s `top` is `0`. Structural only: jsdom cannot report where either box lands. |
| T-11 | all | regression guard | A slice-wide grep assertion is not added; instead T-3's signature change makes a second scroll writer a type error at the call site. Recorded here so the absence is deliberate. |

**Browser-verification items.** The following acceptance criteria cannot be closed by any
test in this repository and must be confirmed by hand against the running stack (the
`frontend:verify` skill, or a deployed build): **AC-1, AC-4, AC-7, AC-9, AC-10, AC-13**.
Each is a layout or timing outcome measured in pixels or frames. They are listed as such in
§10 so that nobody checks them off on the strength of the unit tier. Per
`docs/tasks/2026-08-09-chatroom-rail-scroll-and-resize` §12, the honest long-term fix for
this gap is Playwright coverage; `frontend/e2e/` has chatroom specs but none that assert
feed geometry, which is recorded as FU-3.

**Coverage boundary, stated plainly**: the unit tier proves the arithmetic (T-1), the
counting rule (T-2), the throttle (T-6) and every wiring decision. It proves none of the
visual outcomes. Roughly half of this dossier's user-visible value is verified by eye.

## 9. Risks and Rollback

- **The `useChatroomScroll` signature change (Q-1, Q-2) touches its only existing test file.**
  `useChatroomScroll.test.ts:20` constructs the composable with
  `computed(() => messageIds.length)` and must move to passing the ids themselves. Both
  existing cases are about `scrollToMessage` and are otherwise unaffected.
- **Throttling tokens changes observable timing.** Any test that asserts store state
  immediately after dispatching an `agent.token` frame will need a timer advance.
  `useChatroomSocket.test.ts` and `ChatroomView.test.ts:411` ("renders the streaming draft
  bubble while agent tokens accumulate") are the known sites. A dropped final token would be
  a worse defect than the one being fixed, which is why Q-4 makes the forced flushes explicit
  and T-6 asserts two of them.
- **A `ResizeObserver` that calls `maybeStick` can loop** if the scroll itself changes the
  observed box. It should observe the `<ol>`'s content size, not the scrollport, and
  `maybeStick` is already a no-op when the reader is not at the bottom
  (`useChatroomScroll.ts:55-57`). Worth watching for in the manual pass: a room with a tall
  Mermaid diagram is the stress case.
- **The auto-trigger can burn through history** if the disarm in Q-6 is wrong, loading every
  page in a room in one scroll. `loadingOlder` (`useChatroomMessages.ts:168,200`) plus the
  `armed` flag are two independent guards; `hasOlderMessages` going false
  (`:174,186`) is the terminating condition.
- **Merging approvals into the message feed changes vnode keys** in the `TransitionGroup`.
  The pending-message key swap behaviour documented at `ChatroomView.vue:960-966` must
  survive; if the merged list changes how a `pending-<uuid>` key is replaced by its persisted
  twin, the send animation regresses.
- **The compact-band CSS must not reach the tablet grid.** With Q-8 deferred, `.chatroom--tablet`
  (`ChatroomView.vue:1008-1011`) and the compact `@media` block coexist, and the failure mode
  is a selector that matches both, silently shipping the deferred behaviour. The band is
  `width >= BP.lg`, the tablet class is `width < BP.lg`, so they are disjoint by construction;
  T-9's 800px arm is what proves it stayed that way.
- **Rollback**: the six files in §7 are independently revertible, and the findings map to
  separable commits. The one coupling is Q-1/Q-2, which land together because they share a
  signature.

## 10. Acceptance Criteria

- [ ] AC-1: **(browser)** With the feed scrolled such that "Load earlier" is visible but not
      flush at the top, clicking it leaves the message that was being read at the same
      viewport position. Verified across two successive loads, since the audit records the
      error as compounding.
- [ ] AC-2: T-1 fails before the fix and passes after; `restoreAfterPrepend` yields
      `savedScrollTop + (newHeight - savedHeight)`.
- [ ] AC-3: T-2 fails before the fix and passes after; loading a page of history leaves
      `newCount` at 0 and the pill hidden, while genuinely new items still increment it.
- [ ] AC-4: **(browser)** A message containing a Mermaid diagram, a `$$...$$` block and an
      image attachment lands fully in view for a reader pinned to the bottom, after the
      enhancement pass and after the image decodes.
- [ ] AC-5: T-4 passes; the `ResizeObserver` is constructed under a `typeof` guard, observes
      the feed content, calls `maybeStick`, and is disconnected on unmount.
- [ ] AC-6: T-7 passes; the composer grows with content and stops at 192px, and returns to
      one line when the draft is cleared by a send.
- [ ] AC-7: **(browser)** Five Shift+Enter lines are all visible in the composer while
      typing; a ninth line scrolls internally rather than growing the box.
- [ ] AC-8: T-6 fails before the fix and passes after; a 30-token burst inside one 120ms
      window produces one store write, and `agent.finished` and unmount both flush.
- [ ] AC-9: **(browser)** A streamed reply containing a fenced code block no longer flickers
      between highlighted and unhighlighted as it arrives.
- [ ] AC-10: **(browser)** An empty chatroom renders its empty state vertically centred in
      the feed area, per `docs/UI/07-conversation.md:1018`.
- [ ] AC-11: T-10's empty-state half passes; the empty-state item is a filling, centring flex
      item and ordinary message bubbles are unaffected.
- [ ] AC-12: T-5 passes; the auto-trigger observes at a 100px top margin, fires once per
      reach, and is disarmed across a prepend/restore cycle.
- [ ] AC-13: **(browser)** Scroll-wheeling to the top of a 500-message room loads successive
      pages without clicking, stops when `hasOlderMessages` goes false, and never loads more
      than one page per reach.
- [ ] AC-14: T-9 passes; at 1024-1279 both rails are overlay panels driven by the header
      toggles and the feed occupies the full remaining width; at 1280+ the existing
      three-column layout and its resize handle are unchanged; and at 768-1023 the agent rail
      is **still present** and `chatroom--compact` unbound, confirming Q-8's deferral held.
- [ ] AC-15: T-8 passes; approval cards render at their `started_at` position among messages,
      and an approval arriving while the reader is scrolled up raises the pill.
- [ ] AC-16: T-10's search half passes; the search panel's top edge is flush with the top of
      the feed, with no strip of messages above it.
- [ ] AC-17: T-3 passes; `useChatroomMessages` no longer receives or touches the feed
      element, and `useChatroomScroll` is the only module in the slice that writes the feed's
      scroll position.
- [ ] AC-18: Gates green: `pnpm lint` (all 12, notably #4 v-html allowlist, which the merged
      feed `v-for` must not widen, and #12 i18n), `pnpm typecheck`, `pnpm test`,
      `pnpm build`. Per `feedback_remote_ci_verification`, CI is authoritative over the local
      Windows host.

## 11. SRS Delta

None against `REQUIREMENTS.md`. This dossier restores documented behaviour.

Two corrections to `docs/UI/`, to be applied when this dossier is approved:

1. **`docs/UI/07-conversation.md:513`** currently reads:

   > - Rendering: memoized per agent - only re-renders when accumulated text changes; the
   >   `_streamCache` map avoids calling `renderMarkdown()` on every token at high frequency

   The second clause is false and is the reason F-15 went unnoticed. The cache exists
   (`useAgentStreams.ts:12,26`) but keys on text equality, so it cannot hit while text is
   growing, which is the only time it would matter. It is also not named `_streamCache`
   anywhere in the code. Replace with:

   > - Rendering: the accumulated text is flushed from the socket layer to the store on a
   >   120ms throttle, so `renderMarkdown()` runs at most once per flush per agent rather
   >   than once per token. The per-agent memo in `useAgentStreams` then suppresses
   >   re-rendering agents whose text did not change in that flush.

2. **`docs/UI/07-conversation.md:897`** specifies "Page size: 50 messages per fetch" while
   the implementation uses `PAGE_SIZE = 100` (`useChatroomMessages.ts:34`), which is also
   the size the connect-time reconcile depends on (the comment at `:32-33` makes that
   coupling explicit). The code is the better intent, since 100 halves the number of
   round-trips for the same history, and the constant is already documented as shared.
   Change the spec line to 100. No code change.

Neither correction changes behaviour this dossier does not already change.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1**: `docs/UI/07-conversation.md:747` specifies that the message feed behind the
  search panel is dimmed with `--overlay-backdrop` at 0.2 opacity. No such overlay exists in
  `ChatroomSearchPanel.vue`. Deferred per Q-9: it is a new affordance whose stacking,
  dismissal and reduced-motion behaviour are unspecified, not a regression.
- **FU-2**: `useBreakpoint.ts:51-53` exposes three bands (`isMobile`, `isTablet`,
  `isDesktop`) against the four in `docs/UI/11-responsive-a11y.md` §1. This dossier derives
  its fourth band locally from the exported `width` (`:47`) rather than widening the shared
  composable for one consumer. If a second view needs the 1024-1279 band, promote it to
  `useBreakpoint` at that point rather than duplicating the computed.
- **FU-3**: `frontend/e2e/` has no spec that asserts chatroom feed geometry, which is why
  six of this dossier's acceptance criteria are manual. A Playwright spec that measures the
  feed's `scrollTop` across a history load, and the bounding box of the newest message after
  a Mermaid render, would convert AC-1 and AC-4 into automated checks. Same class of gap as
  `docs/tasks/2026-08-09-chatroom-rail-scroll-and-resize` FU-2.
- **FU-4**: `AttachmentImage.vue:68-76` renders without intrinsic dimensions because
  `Attachment` (`slices/conversation/types/index.ts:105-114`) carries none. Q-3's
  `ResizeObserver` handles the consequence, but reserving space up front would be better for
  cumulative layout shift and would need `width`/`height` on the attachment DTO. Backend
  change, out of scope here.
- **FU-5**: `useChatroomScroll.ts` will own four responsibilities after this change (bottom
  anchoring, the unseen counter, prepend restoration, and two observers). That is still one
  cohesive concern (feed scroll state) but the file roughly doubles. Worth a `check-quality`
  pass afterwards to decide whether the observer wiring belongs in its own composable.
- **FU-6**: F-29's second arm, deferred by Q-8. `ChatroomAgentSidebar` renders from 768px
  (`ChatroomView.vue:28`, `v-if="!isMobile"`) while `docs/UI/11-responsive-a11y.md:118,121`
  and `docs/UI/07-conversation.md:258` both put a drawer there. The correction is small and
  known: `:28` becomes `isDesktop`, the agents `SDrawer` at `:232` becomes `!isDesktop` to
  match the presence drawer at `:242`, `.chatroom--tablet` (`:1008-1011`) collapses to one
  column, and the comments at `:230` and `:1008` are updated. It is held back not because it
  is unclear but because it removes a surface tablet users have today, which deserves its own
  change and its own announcement rather than riding along with ten restorations. Until it
  lands, the code knowingly diverges from the responsive spec at 768-1023, and T-9's 800px
  assertion pins the current behaviour so the divergence stays deliberate.
