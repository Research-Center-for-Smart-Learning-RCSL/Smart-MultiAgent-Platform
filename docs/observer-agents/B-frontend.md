# Phase B — Observer Agents Frontend

The creator-facing observation panel, the release dialog, room settings
(binding role + disclosure), the neutral disclosure indicator, and the
hygiene work that keeps observers out of every shared surface. Depends on
Phase A's OpenAPI surface.

Design language: Design D (hybrid SaaS + chat-first), light blue/grey,
`@heroicons/vue`, no emojis, all copy through `$t()`. Follow
`docs/UI/01-design-system.md`, `07-conversation.md`, `11-responsive-a11y.md`,
`12-shared-patterns.md`. All new code lives in `src/slices/conversation/`
(plus the tenancy-query reuse noted in B.2); cross-slice imports only via
`index.ts` re-exports (eslint-plugin-boundaries enforced).

A load-bearing principle inherited from Phase A: **the frontend is never the
enforcement point.** Every gate below is a UI affordance; the server already
filtered/authorized. Where the client receives data, it renders it; where it
must not see data, the server never sent it.

Component inventory note (verified): the shared library is
`src/shared/ui/` with 39 S-prefixed components. Relevant here: `SDrawer`,
`SToggle`, `SSelect`, `SModal`, `STextarea`, `STooltip`, `SRelativeTime`,
`SAlert`, `SBadge`, `SCard`, `SEmptyState`, `SSkeleton`, `SCharCount`.
There is **no `SConfirm`** — confirm-before-delete is the
`useConfirmDialog()` composable (`shared/composables/useConfirmDialog.ts`,
`const ok = await confirm({title, message, variant})`) backed by the global
`SConfirmDialog`; `window.confirm` is lint-banned. There is **no
`SCallout`** — use `SAlert` (has variants and an `#actions` slot).

## B.1 API client, slice API, and types

1. Regenerate the client: `pnpm run gen:api` (Phase A froze the OpenAPI).
2. **`src/slices/conversation/types/index.ts`:**
   - `Chatroom` gains `created_by_user_id: string | null`,
     `disclose_observers: boolean`, `observers_present: boolean`.
   - New `ChatroomAgentRole = 'normal' | 'observer'`.
   - New `Observation` interface: `id`, `chatroom_id`, `agent_id`,
     `content_md`, `metadata`, `trigger` (`'every_n_messages' |
     'silence_minutes'`), `created_at`, `released_at: string | null`,
     `release_target: ReleaseTarget | null` with
     `ReleaseTarget = { kind: 'room'; message_id: string } | { kind:
     'agents'; agent_ids: string[]; woken: boolean }`.
   - New event-name union next to `ChatroomEventType`:
     `ObservationEventType = 'observation.started' | 'observation.created'
     | 'observation.failed' | 'observation.released'`.
3. **`src/slices/conversation/api/index.ts`** (verified current shapes:
   `listChatroomAgents` fetches `{agent_id}[]` and maps to `string[]`;
   `addChatroomAgent(chatroomId, agentId)` posts `{agent_id}`;
   `sendMessage` body carries `mention_agent_ids?: string[]`):
   - Change `listChatroomAgents` to return
     `{ agent_id: string; role?: ChatroomAgentRole }[]` (stop mapping to
     bare ids; `role` is present only for the creator — type it optional,
     never default it client-side). Update its callers
     (`useChatroomBindings.loadBindings`, `boundAgentsQuery` in
     `ChatroomView.vue`).
   - `addChatroomAgent(chatroomId, agentId, role?)`.
   - New `setChatroomAgentRole(chatroomId, agentId, role)` →
     `PATCH /chatrooms/{id}/agents/{agentId}`.
   - New `listObservations(chatroomId, { before?, limit? })`,
     `releaseObservation(chatroomId, observationId, body)`,
     `deleteObservation(chatroomId, observationId)`.
   - Note the chatroom PATCH already sends `If-Match` via the existing
     settings save path — `disclose_observers` rides it unchanged.
4. **`useChatroomBindings.ts`:** `BoundAgent` (verified: `{ id: string;
   name?: string; wakeup_config?: WakeupConfig }`) gains
   `role?: ChatroomAgentRole`. Existing consumers keep working because
   non-creator payloads are shape-identical.
5. **Query keys:** add `observations: (chatroomId) => ['conversation',
   'observations', chatroomId]` to the `convKeys` factory
   (`src/slices/conversation/queries/index.ts`). (Verified: several
   sibling queries use raw key arrays instead of the factory — new code
   uses the factory; don't refactor the strays in this phase.)

## B.2 State: `useObservations` composable

**New file:** `src/slices/conversation/composables/useObservations.ts`.
Pattern references: `useChatroomMessages.ts` (TanStack cache + cursor
pages) and `slices/notifications/composables/useNotificationsSocket.ts`
(user-channel consumption).

- **Query:** key `convKeys.observations(chatroomId)`, newest-first pages
  via `before` cursor, `enabled: isCreator` (never fire the request for
  non-creators — it would 403 and pollute error toasts).
- **Live updates — verified channel semantics, follow them exactly:**
  the user channel is `wsManager.channel(`/user/${userId}`)`. Channels are
  **singletons keyed by path with no refcounting**; `close()` wipes ALL
  handlers, and `useBanKickGuard` owns the `/user/{id}` channel lifecycle.
  Therefore this composable is an **additive subscriber**: call
  `channel.subscribe('observation.started', h)` etc. (named events, the
  `useNotificationsSocket` style — not `'*'`), call `connect()`
  (idempotent), and on teardown call only its own `unsub()` functions —
  **never `channel.close()`**. Handlers:
  - `observation.started` → set `observerAnalyzing[roomId].add(agentId)`.
  - `observation.created` → clear analyzing, `qc.invalidateQueries({
    queryKey: convKeys.observations(roomId) })` (payload is ids-only; the
    body comes from REST, mirroring the `message.created` → refetch
    discipline), bump the unread counter if the panel is closed.
  - `observation.failed` → clear analyzing, set
    `observerErrors[roomId][agentId] = kind`.
  - `observation.released` → patch the cached observation's
    `released_at`/`release_target` via `qc.setQueryData` — replace the
    object immutably, never mutate one in place (in-place mutation of a
    pushed object does not retrigger computeds).
- **Store fields:** add `observerAnalyzing` (model:
  `agentThinking` — `Record<roomId, Set<agentId>>` with immutable-spread
  set/clear) and `observerErrors` (model: `agentErrors` — nested
  `Record<roomId, Record<agentId, kind>>`) to
  `stores/conversation.ts`. Verified checklist for that store: add the
  refs to the returned object, and reset them in BOTH `resetRoom(roomId)`
  and `clearAll()` (which `registerCleanup` wires into logout).
- **`isCreator`:** computed once here and exported —
  `session.me?.is_admin === true || (room.created_by_user_id !== null
  ? room.created_by_user_id === session.me?.id : isModerator)`.
  `session.me` comes from `useSessionStore` (re-exported at
  `@shared/stores/session`; shape `{id, email, email_verified, is_admin,
  status, display_name}`). `isModerator` reuses the verified pattern from
  `McpEgressAllowlistView.vue` (~L40-52): `useQuery({ queryKey:
  tenancyKeys.projectMembers(projectId), queryFn: () =>
  projectsApi.listMembers(projectId)… })` then
  `membership?.role === 'owner'`, with `projectsApi`/`tenancyKeys`
  imported from `@slices/tenancy`.
- **Derived:** `observations` (flattened pages), `unreadCount`,
  `observerAgents` (bound agents with `role === 'observer'`, names
  resolved through the same project-agents query `ChatroomView.vue`
  already runs).

## B.3 The observation panel

**New files:**
`src/slices/conversation/components/ObserverPanel.vue`,
`ObservationCard.vue`.

**Placement.** Verified layout: `ChatroomView.vue` is a 3-column grid
(`220px 1fr 200px`); the right rail is a single `ChatroomPresence` mounted
`v-if="isDesktop"` (~L132-137); tablet/mobile render presence in an
`SDrawer side="right"` toggled by the `ChatroomHeader` `toggle-people`
event, and agents in `SDrawer side="left"` via `toggle-agents`. There is
**no tab structure today** — this phase introduces one: wrap the right-rail
content in a two-tab segmented control (`STabs`), `People` and `Observer`,
where the Observer tab renders when the viewer is the creator and the room
has either an observer binding or at least one non-deleted observation
(`useObservations.hasObserverSurface`) — observations outlive the binding
that produced them, and the panel is the only route to the
release/soft-delete affordances, so gating on the roster alone strands them
(see `docs/tasks/2026-07-22-observation-binding-cleanup/spec.md`). Icon
`EyeIcon`, badge = `unreadCount` via `SBadge`. Apply the same gating inside
the mobile people-drawer. Opening the panel zeroes `unreadCount`.

```
+- right rail ----------------------+
| [ People ] [ Observer (2) ]       |
+-----------------------------------+
| Analyst-A          analyzing...   |   <- observer roster w/ status dot
| Analyst-B          idle           |      (reuse ChatroomAgentStatusItem)
+-----------------------------------+
| +- ObservationCard -------------+ |
| | Analyst-A * 14:32 * every_n   | |
| | <rendered markdown body>      | |
| | [Released to room 14:40]      | |   <- state chip when released
| |           [Release] [Delete]  | |
| +-------------------------------+ |
| +- ObservationCard (older) -----+ |
| [Load earlier]                    |
+-----------------------------------+
```

**`ObservationCard.vue`** — props `{ observation, agentName }`, emits
`release`, `delete`:

- Header row: agent name, `SRelativeTime` timestamp, trigger chip
  (`every_n_messages` / `silence_minutes`, i18n-labelled, muted grey).
- Body: markdown through the **existing** pipeline — import
  `renderMarkdown` from `slices/conversation/utils/renderMarkdown.ts`
  (markdown-it + DOMPurify; `enhanceRenderedMarkdown`/`useMarkdownEnhance`
  for the post-mount hljs/KaTeX/Mermaid pass). Do not add a second
  renderer. **Lint gate (verified):** `vue/no-v-html` is `error` globally
  with an allowlist override in `frontend/eslint.config.js` (~L220-235,
  currently 4 files) — add `ObservationCard.vue` to that `files` array or
  lint fails. Long bodies clamp at ~14 lines with an expand toggle.
- Footer: release-state chip — unreleased: none; `kind:'room'`: "Released
  to room"; `kind:'agents'`: "Sent to N agents" (+ "and woken" when
  `woken`). Actions: `Release` (primary ghost, hidden once released),
  `Delete` (icon button → `useConfirmDialog().confirm({variant:
  'warning'…})`, the verified house pattern; see
  `useChatroomMessages.confirmDelete`).
- States: `SSkeleton` while pages load; `SEmptyState` ("No observations
  yet — observers write here after they analyze the conversation"); an
  inline `SAlert` above the divider when the roster is empty but
  observations remain, explaining that no observer is currently bound and
  that these past analyses are still releasable and deletable; a
  failed observer turn surfaces as an inline roster row from
  `observerErrors` (kinds mirror `agent.finished`: `rate_limited`,
  `provider_exhausted:*`, …), not a toast — it belongs to this panel's
  context.

## B.4 The release dialog

**New file:** `src/slices/conversation/components/ObservationReleaseDialog.vue`
(`SModal` base). Opened from `ObservationCard`.

1. **Content** — `STextarea` prefilled with `content_md`, editable
   (R28.08), `SCharCount` against the 100,000-char message max (mirrors the
   backend `_MAX_CONTENT_MD`). A "restore original" link resets it.
2. **Target** — radio group (`SRadio`):
   - *Publish to room*: helper text states everyone (humans and agents)
     will see it as a system message attributed to the creator; when
     `disclose_observers` is true, adds "the observer's name will be
     attached".
   - *Send privately to agents*: reveals a checkbox list of **normal-role**
     bound agents (names from the bindings query — observers are
     structurally absent from the eligible set, mirroring the server
     rule), plus a `Wake immediately` `SToggle` defaulting **off**. Helper
     text — verified queue semantics make this wording load-bearing:
     "Otherwise agents read it the next time they act. Undelivered notes
     expire after 24 hours."
3. **Confirm** — disabled until a target is valid (room, or ≥1 agent).
   Submits `releaseObservation`; pending state on the button; on 409
   (already released) refetch and show the state chip instead of an error
   toast; on 422 map the RFC 7807 problem detail to inline field errors.
4. No optimistic release — this is an irreversible, outward-facing act;
   wait for the 200, then let the `observation.released` event/refetch
   update the card.

## B.5 Settings: binding role and disclosure

**`src/slices/conversation/views/ChatroomSettingsView.vue`** (verified
structure: Bound Agents `SCard` at ~L369-455 — add form `SSelect` +
`SButton` → `onAddAgent`; per-agent rows with a danger remove button,
`WakeupConfigEditor`, `DlqViewer`; flag toggles save optimistically via
`setFlag(key, value)` → `onSave()` at ~L108):

1. **Bound Agents card:** each row gains a role control — an `SSelect`
   (`Participant` / `Observer`) rendered only for the creator
   (`isCreator` from B.2; non-creator moderators see a read-only
   "Participant" label — the server hides observer rows from them anyway).
   Changing it calls `setChatroomAgentRole` with the same
   optimistic-immediate-save style as `setFlag`. Under an `Observer`
   selection show persistent helper text: "Analyzes the conversation
   silently. Output is visible only to you until you release it."
2. **Add-agent flow:** the existing `SSelect`+form gains a role choice
   (default Participant); the Observer option renders only for the
   creator.
3. **Access Control card:** new `SToggle` "Disclose observers" (default
   on), creator-only, wired through `setFlag`-style save (the PATCH
   carries `If-Match` as today). Helper text for both positions — on:
   "Members see a notice that observers are enabled (never which agent or
   any output)"; off: "Members are not informed. Check your organization's
   policy before disabling." The off-position warning is deliberate
   product posture; keep it.
4. **Guest-link interaction:** when `allow_guest_links` is on, observers
   exist, and disclosure is off, render a static informational `SAlert`
   in the Access Control card noting that external guests will be
   observed without notice.

## B.6 Shared-surface hygiene

Server-side filtering already guarantees most of this; the client work is
about not *creating* new leaks and about the creator's own view:

1. **Shared agent sidebar/presence rail stay role-blind.** `agentList`
   (`ChatroomView.vue` ~L300-307, fed by `boundAgentsQuery`) now receives
   role-annotated bindings for the creator — **filter `role ===
   'observer'` out of `agentList`**: observers live in the Observer tab
   only (locked decision 7), and the shared sidebar looks identical on the
   creator's screen and a member's screen (matters for screen-sharing).
2. **Mentions.** Verified flow: the composer's autocomplete candidates are
   `mentionables` (= `agentList` + named human members,
   `ChatroomView.vue` ~L315-322), while actual wake targets are resolved
   by `resolveMentions(text, agentList)` in `useChatroomMessages` (~L227)
   and sent as `mention_agent_ids`. Because both derive from `agentList`,
   the single filter in item 1 removes observers from autocomplete AND
   from the wake payload; the server drops smuggled ids anyway.
3. **Streaming/status maps:** room-channel `agent.thinking`/`agent.token`
   never arrive for observers (suppressed server-side). Defensively,
   streaming bubbles key off `agentList`, so an unknown agent id in
   `agentStreams` renders nothing.
4. **Disclosure indicator:** when `observers_present` is true, render a
   neutral chip — new component `ObserverDisclosureChip.vue` (`EyeIcon` +
   `$t('conversation.observers.disclosureChip')` = "Observers enabled",
   `STooltip`: "The room owner receives private analyses of this
   conversation."). Mount it in
   `src/slices/conversation/components/ChatroomHeader.vue` (add a prop;
   the header already spans the grid and carries the connection state
   pill). Same chip for creator and members — the creator's affordances
   are in the panel, not here.
5. **Released messages:** `ChatroomMessageBubble.vue` renders
   `sender_type === 'system'` as a centered divider (verified branch at
   L2-15, body via the allowlisted `v-html`). Add a sibling `v-else-if`
   for `metadata?.type === 'released_observation'` (defensive metadata
   guard, mirroring the `ragSources` pattern at ~L250-254): a full-width
   flat card (not a chat bubble) with a small header —
   `$t('conversation.observers.releasedByOwner')`, plus the observer's
   name when `metadata.observer_agent_id` is present — and the markdown
   body through the standard `renderMarkdown` path (the file is already
   on the v-html allowlist). Use the collapsible `bubble__sources` block
   (~L130-167) as the styling reference.

## B.7 i18n

Verified layout: per-slice locale files —
`src/slices/conversation/locales/en.json` and `zh-TW.json`, nested keys
(style reference: `conversation.settings.agentBindings`,
`conversation.chatroom.sources` with `{count}`). Add a
`conversation.observers.*` block to **both** files: tab label, badge aria,
empty state, analyzing status, failed status, card actions, trigger
labels, release dialog (title, target labels + helper texts, wake toggle +
helper incl. the 24h-expiry sentence, confirm, restore-original,
already-released), settings (role labels, observer helper, disclosure
toggle + both helpers, guest alert), disclosure chip + tooltip,
released-message header.

Two verified gotchas:

- `vue/no-bare-strings-in-template` is `error` — every template string
  must be `$t()`. But there is **no missing-key lint** (no @intlify
  plugin): a key present in `en.json` but forgotten in `zh-TW.json` fails
  only at runtime — add "both locale files touched" to the PR checklist.
- Any copy containing a literal `@` must escape it as `{'@'}` — vue-i18n
  parses bare `@` as a linked message and it crashes only in the
  production build (dev/test just warn).

## B.8 Responsive and accessibility

Per `docs/UI/11-responsive-a11y.md`:

- Rail-tab ↔ drawer breakpoint behavior matches the presence rail exactly
  (`isDesktop`/`isMobile` flags already drive it); drawers get focus trap
  and `Esc` close from `SDrawer` built-ins.
- The Observer tab button carries
  `:aria-label="$t('conversation.observers.badgeAria', { n: unreadCount })"`;
  the unread badge is `aria-live="polite"` — analyses arriving mid-meeting
  should not steal focus.
- `ObservationCard` expand toggle is a real `<button>` with
  `aria-expanded`; release-state chips have text, never color-only.
- Release dialog: radio group is a `fieldset` + `legend`; delete confirm
  goes through the shared `useConfirmDialog` (focus handling built in).
- Color: status dots reuse `shared/ui/statusColors.ts`; check the
  released/unreleased chip contrast against the light blue/grey theme
  (AA on the surface tokens).

## B.9 Test plan

Vitest, mirroring existing suites (`McpEgressAllowlistView.test.ts` is the
gating-pattern reference):

- `useObservations.test.ts`: query disabled for non-creators; named-event
  handlers (started/created/failed/released) mutate store and cache
  correctly; released patch is immutable (regression: ref-array in-place
  mutation); teardown unsubscribes its own handlers without closing the
  shared `/user` channel; unread counter lifecycle; `isCreator` truth
  table incl. NULL-creator moderator fallback and admin.
- `conversation store` (extend existing store test): `observerAnalyzing` /
  `observerErrors` set/clear, and both are wiped by `resetRoom` and
  `clearAll`.
- `ObserverPanel.test.ts`: tab hidden for non-creator and for creator with
  zero observers; roster status states; "load earlier" pagination; empty
  and error states.
- `ObservationCard.test.ts`: markdown rendered through `renderMarkdown`
  (sanitized); release-state chips for all three states; actions hidden
  once released; delete goes through `useConfirmDialog`.
- `ObservationReleaseDialog.test.ts`: target validation matrix; observer
  agents absent from the checkbox list; wake toggle only under the agents
  target; 409 path refetches instead of toasting; content restore;
  char-count cap.
- `ChatroomSettingsView.test.ts` (extend): role select creator-only;
  disclosure toggle creator-only; guest alert renders exactly when
  `allow_guest_links && observers && !disclose`.
- `ChatroomMessageBubble.test.ts` (extend): released-observation variant
  with and without `observer_agent_id`; malformed metadata falls back to
  the plain system divider.
- `ChatroomView.test.ts` (extend): `agentList` excludes observers for the
  creator; `mentionables` and `resolveMentions` never carry an observer.

## B.10 Deliverables and exit criteria

Deliverables: regenerated api-client; type/api/composable additions +
`convKeys.observations`; store fields; four new components
(`ObserverPanel`, `ObservationCard`, `ObservationReleaseDialog`,
`ObserverDisclosureChip`); settings, header, and message-bubble
extensions; eslint v-html allowlist entry; i18n keys in `en.json` AND
`zh-TW.json`; the test suite above.

Exit criteria:

1. `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build` clean;
   boundaries plugin reports no cross-slice violations.
2. Manual dual-session smoke on the dev stack (creator + member browsers):
   member sees only the disclosure chip (when enabled) and, after a room
   release, the system card; the member's network tab and WS frames
   contain no observation ids or content at any point — reconfirming A's
   guarantee from the client side.
3. Release round-trip verified both ways: room release renders the system
   card for both sessions; private release with wake produces a normal
   agent reply referencing the analysis, with no intermediate UI trace.
4. i18n: production build renders every new string in both `en` and
   `zh-TW` (missing keys have no lint — walk the surfaces in both
   locales; watch for the `{'@'}` linked-message crash).
5. A11y pass per B.8 on the panel, dialog, and chip (keyboard-only walk +
   axe scan of the three surfaces).
6. Regression check on the `/user/{id}` channel: notifications bell and
   ban-kick guard still receive events with the observer subscriber active
   (shared-channel, no-close rule).
