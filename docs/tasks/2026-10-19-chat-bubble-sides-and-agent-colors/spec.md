---
type: feature
status: in-progress
created: 2026-10-19
requirements: [R13.77, R13.78, R15.25, R28.02, R28.10]
depends_on: []
---

# Chatroom bubbles: own messages on the right, and per-agent bubble colours

## 1. Summary

Two changes to how chatroom messages are told apart, delivered together.

Part A, sides. Today every non-agent message renders right-aligned, so in a room with
several people a viewer cannot tell their own messages from another person's except by
reading the sender name. After this change the viewer's own messages, and only those, sit
on the right with a light accent tint; every other sender (another registered user, a
guest, an agent) is on the left. "Own" is resolved from the identity the server sees, the
access token, so it is also correct for an anonymous guest whose tab still holds an
earlier sign-in and for an admin who is impersonating a user.

Part B, agent colours. A room's configurer can give each participant agent (a binding
whose role is `normal`) a bubble colour from a fixed palette, so that in a room with
several agents each one's messages are recognisable at a glance. Observer agents never
post bubbles ([R28.02]), so they get no colour setting. The colour is stored per room
binding, so the same agent can be coloured differently, or not at all, in different rooms.

## 2. Goals and Non-goals

**Goals**

- A message is right-aligned if and only if the current viewer sent it. Other users,
  guests, and agents are left-aligned.
- Own bubbles carry a light accent tint in both themes.
- One definition of "the current viewer" drives both the bubble side and the room's
  existing "is this you" checks, derived from the access token first.
- Optimistic (not yet acknowledged) sends render on the right from the first frame, with
  no jump when the server echo replaces them.
- A participant binding can carry an optional bubble colour chosen from a fixed palette of
  eight keys; unset keeps today's agent style.
- The colour is settable by matrix row 29's audience (Admin, Org Owner, Project Owner, and
  the room creator), refused for observer bindings, audit-logged, and pushed live to every
  open client of the room.
- Every viewer of the room, guests included, sees the colour.
- Every text and border pair on every new fill meets its WCAG bar in both themes, pinned
  by the contrast test.
- `docs/UI/07-conversation.md` §3.3 describes both rules.

**Non-goals**

- No change to edit/delete/moderation permissions (`canEdit`, `canDelete`). They keep
  their admin/moderator arms; only the identity they compare against changes, which for
  every non-impersonating, non-stale-guest session is the identity they use today.
- No mirrored inner layout for right-hand bubbles: the avatar stays first in the meta row
  and the timestamp stays at its right edge.
- No colour on the agent itself (the agents table), no free-form hex, no auto-assignment.
- No colour for observer bindings, and no colouring of released-observation cards, system
  rows, or human bubbles.
- No colouring of the agent's avatar, the agent rail, mentions, or the streaming bubble's
  "typing" chrome beyond what §6 lists. The streaming bubble does take the colour, so a
  reply does not change colour when it finishes streaming.
- No fix to impersonation's wider identity gap (`session.me` and `isAdmin` still describe
  the admin, `useImpersonation.ts:32-52`); see FU-1.
- No RTL support or move to logical CSS properties.
- No splitting of `AgentRef`'s request and response models (existing FU-6 of the
  delegated-activity-control dossier).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Besides position, should own messages look different? | Right side plus a light accent tint. | The user's choice. Position alone is weak on a narrow feed, where 75% and 85% max-widths nearly fill the row. Costs one token per theme and three contrast pairs. |
| Q-2 | While an admin is impersonating a user, whose messages are "own"? | The impersonated user's: the token's `sub`. | The user's choice. `useImpersonation` swaps only the access token (`useImpersonation.ts:39`) and never refreshes `session.me`, so a `session.me` rule is backwards while impersonating. |
| Q-3 | Should "own" resolve the guest identity first? | Yes, as `viewerId` does. | The user's choice. `session.me` can survive an earlier sign-in while a guest token is held (`ChatroomView.vue:559-562`); `isOwnMessage` checks the two branches independently (`useChatroomMessages.ts:67-71`). |
| Q-4 | Should the room's other "is this you" checks use the same definition? | Yes, one shared definition. | Decided during analysis. `viewerId` (`ChatroomView.vue:563`) has Q-2's defect and feeds the roster "you" marker and moderation exclusions (`:1389`, `:1408`, `:1411`). Two rules would disagree while impersonating. |
| Q-5 | Same dossier as the agent colours, or separate? | One dossier. | The user's choice. Both parts edit `ChatroomBubbleShell.vue`, `ChatroomMessageBubble.vue`, `main.css` and `contrast.test.ts`, and the own tint and the agent palette must be judged against each other. Built as two milestones (Part A, then Part B) so each still reverts cleanly. |
| Q-6 | What does "chatroom participants only" mean? | The setting exists only for agents bound with role `normal`; observer bindings are refused. | The user's answer: observers post no bubbles, so a colour for them would be dead configuration. |
| Q-7 | Where is the colour stored? | On the room binding (`chatroom_agents`). | Follows from Q-6: participant versus observer is a per-binding property (`tables.py:81-86`), and agents are project-scoped and shared across rooms (`agents/infrastructure/tables.py:21-23`). |
| Q-8 | How is the colour chosen? | On/off plus one of a fixed palette of eight keys. | The user's choice. Each key is a token pair per theme, contrast-tested; a hex input cannot guarantee contrast in both themes and needs server-side colour validation. |
| Q-9 | Who may set it? | Matrix row 29's audience, through the same predicate as wake-up settings. | The user's choice. `can_manage_room_wakeup` (`access.py:566-578`) already admits the creator for any binding and a row-15 editor for a normal binding, so no new authority rule is invented; observer bindings are refused separately (Q-6). |
| Q-10 | What happens to a colour when a binding becomes an observer? | Cleared in the same write as the role change. | Decided during analysis. Keeping it would leave stored state no viewer can see and that reappears unexpectedly on demotion; clearing keeps "observer has no colour" true at rest, which the `CHECK` constraint in §6 can then enforce. |
| Q-11 | Does any active dossier block this? | No. `depends_on: []`. | The four active dossiers (`2026-10-19-peer-deliberate-errors`, `2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence`, `2026-10-12-graphrag-phase5a2-evidence-refs-contract`, `2026-07-19-large-artifacts-silently-dropped`) name none of the touched files. |

## 4. Current State

### 4.1 Bubble sides

**Alignment is keyed on sender type, not on the viewer.** `ChatroomMessageBubble.vue:310`
computes `isAgent`; the row gets `bubble-row--agent` from it (`:42`). `.bubble-row` is a
flex column with `align-items: flex-end` (`:372-381`) and `.bubble-row--agent` overrides
to `flex-start` (`:383-385`). Every `user` and `guest` message is therefore on the right.
The CSS comment at `:375` and the introducing commit `d337cb73` (2026-06-26, "align
user's own messages to the right") both state the intended rule; the implementation
equated "user" with "own".

**The bubble has no identity input.** Its props are booleans and display data
(`ChatroomMessageBubble.vue:283-296`); `ChatroomView.vue:139-162` passes no "own" flag.

**"Own" exists but is private and permission-only.** `isOwnMessage`
(`useChatroomMessages.ts:67-71`) compares `sender_id` to `session.me?.id` for `user`
messages (`:61`) and to `guestSessionId` for `guest` messages (`:63`). It is not returned
from the composable and is consumed only by `canEdit`/`canDelete` (`:73-87`), which return
true for admins and moderators and so cannot stand in for "own".

**The view has a second viewer rule.** `viewerId = guestSessionId ?? session.me?.id`
(`ChatroomView.vue:563`).

**The token carries the server's view of the viewer.** `accessTokenClaims`
(`shared/transport/axios.ts:142-146`) is reactive to `setAccessToken`. A user token has
`sub` = user id (`backend/shared_kernel/auth/jwt.py:81`), plus `impersonated_by` while
impersonating (`jwt.py:55`, `:147-163`); a guest token has `token_use: 'guest_access'` and
`sub` = guest session id (`jwt.py:197`, `axios.ts:148-162`).

**Optimistic sends** carry `sender_type: isGuestSession ? 'guest' : 'user'` and
`sender_id: myGuestId ?? myId` (`useChatroomMessages.ts:276-289`) and render through the
same bubble.

**Other bubble surfaces.** `ChatroomMessageBubble` and `ChatroomStreamingBubble` are
rendered only by `ChatroomView.vue:139` and `:175`; `ChatroomBubbleShell` only by those
two. The streaming bubble is always an agent (`ChatroomStreamingBubble.vue:6`). The search
panel renders a list, not bubbles (`ChatroomSearchPanel.vue:213`).

### 4.2 Agent bindings

**Table.** `chatroom_agents` (`backend/contexts/conversation/infrastructure/tables.py:67-145`),
primary key (`chatroom_id`, `agent_id`) (`:70-78`). Per-binding columns include `role`
(PG enum `chatroom_agent_role`, `normal`/`observer`, `:81-86`, migration 0041), the
activity, draft and canvas grants (`:92-127`), `binding_id` (fresh per bind and role
change, `:134-140`), and the wake-up layers (`:141-144`, migration 0104). There is no
appearance column. The latest migration is `backend/alembic/versions/0104_room_wakeup_layers.py`.

**Agents are project-scoped** (`backend/contexts/agents/infrastructure/tables.py:21-23`)
and carry no colour or avatar field; one agent can be bound to many rooms of its project
(`backend/app/api/v1/chatrooms.py:832-840`).

**Listing.** `GET /chatrooms/{id}/agents` (`chatrooms.py:748-788`) is gated by
`ensure_can_read`, so guests may call it. Non-creators get only normal bindings
(`:765-766`, [R28.10]); `name` goes to every reader (`:176-180`), while `role` and the
grants are filled only for the creator and dropped otherwise by
`response_model_exclude_none` (`:748`, `:773-785`). `AgentRef` (`:152-180`) doubles as the
bind request body.

**Writes and gates.** The role change is creator-only (`chatrooms.py:870-917`), runs
`ChatroomService.set_agent_role` with a CAS and the `chatroom.observer_role_changed` audit
(`chatroom_service.py:418-466`), and emits `chatroom.updated` with `room_visible=True`
(`chatrooms.py:912-917`). The grant routes are creator-only and emit
`room_visible=False` (`:920-986`), because non-creators cannot see the grant fields. Wake-up
settings (`backend/app/api/v1/room_wakeup.py:162-249`) use `can_manage_room_wakeup`
(`access.py:566-578`): creator for any binding, a row-15 editor for a normal binding.
Matrix row 29 and its note (`REQUIREMENTS.md:208`, `:212`) document that audience.

**Live refresh.** A room-visible `chatroom.updated` makes every client invalidate
`convKeys.chatroomAgents` (`useChatroomSocket.ts:522-525`), and the creator's other tabs
do the same over the user channel (`useObservations.ts:337-340`); the reconnect handler
re-reads the same key (`useChatroomSocket.ts:767-773`).

**Frontend.** Room settings render bindings in the Agent bindings card
(`ChatroomSettingsView.vue:745-890`), with the role select at `:809` and per-row controls
gated by `isCreator || (canEditAgents && agent.role !== 'observer')` (`:860`), which is
already row 29's audience. Binding writes go through `useChatroomBindings.ts` (`:51`,
listing at `:171`, grant mutations around `:253-299`). In the room, `boundAgentsQuery`
(`ChatroomView.vue:643-647`) is the only source of per-binding data.

### 4.3 Styling

`ChatroomBubbleShell.vue:23-36`: plain bubbles use `--color-bg` and 75% max-width;
`bubble--agent` uses `--color-surface`, a 3px `--color-accent` left border, and 85%. No
per-agent colour exists anywhere (`SAvatar.vue:101-102` always uses the accent). `main.css`
holds only semantic tokens (light `:62-216`, dark `:412-468`); the only accent fill is
`--color-accent-tint-hover` (`:216`, `:468`). Categorical palettes elsewhere are literal hex
in components (`GraphragGraphView.vue:42-59`), which `literal-colours.test.ts:18-33`
allowlists per file. Contrast pairs are pinned in `contrast.test.ts:85-113`.

**UI spec is stale.** `docs/UI/07-conversation.md:323` says "All messages are left-aligned".

### 4.4 Tests that constrain the change

No test asserts alignment or `bubble-row--agent`. e2e specs select on
`.bubble-row`/`.bubble-row--pending` (`e2e/06-file-upload.spec.ts:7,62`,
`e2e/13-message-edit-delete.spec.ts:6,10,23,25,90`, `e2e/27-guest-sender-marking.spec.ts:82-83`),
so those class names stay. `useChatroomMessages.test.ts:407-439` covers
`canEdit`/`canDelete`. `no-raw-style-literals.test.ts:87,154` allowlists two literals in
`ChatroomMessageBubble.vue`.

## 5. Design

### Options considered

**Part A**

- **A1: `isOwn` prop computed from a shared viewer identity.** A token-derived viewer in
  `@shared/transport`, composed with a `session.me` fallback in the conversation slice,
  used by `isOwnMessage`, the optimistic `sender_id`, and `viewerId`. The bubble receives a
  boolean like `canEdit`.
- **A2: the bubble resolves identity itself.** Fewer props, but a presentational
  component would depend on auth state and the rule would exist a third time.
- **A3: feed `isOwn` from today's `session.me`-based `isOwnMessage`.** Smallest diff, but
  leaves Q-2 and Q-3 wrong.

**Part B storage**

- **B1: nullable PG enum column on `chatroom_agents`.** Matches how `role` is stored, the
  database refuses unknown keys, and a `CHECK` can tie it to `role = 'normal'`. Adding a
  palette key later is `ALTER TYPE ... ADD VALUE`.
- **B2: a JSONB "appearance" bag on the binding.** Room for future appearance settings,
  but untyped at the database and a second shape to validate.
- **B3: a column on `agents`.** Contradicts Q-7.

**Part B write path**

- **W1: a dedicated `PATCH /chatrooms/{id}/agents/{agent_id}/appearance` route.** Same
  shape as each grant having its own route, because the gate differs from the role
  route's.
- **W2: extend the role PATCH.** Mixes a creator-only decision with a row-29 decision in
  one body.

### Decision

**A1, B1, W1.**

Part A's viewer rule:

1. A guest token (`token_use === 'guest_access'`) gives `{ kind: 'guest', id: sub }`.
2. Any other token with a string `sub` gives `{ kind: 'user', id: sub }`, which covers
   impersonation.
3. No token gives `{ kind: 'user', id: session.me.id }` if `me` is set, else no viewer.

Rules 1-2 are a computed in `@shared/transport` next to `guestSessionId`; rule 3 is
composed in the conversation slice because the transport cannot import the session store.
`isOwnMessage(m)` is `m.sender_type === viewer.kind && m.sender_id === viewer.id`.

Part B's palette has eight keys: `cyan`, `teal`, `green`, `lime`, `amber`, `orange`,
`rose`, `violet`. Blue is deliberately absent, because the own-message tint is accent blue
and an agent in the same hue would blur Part A. Gray is absent because it reads as "no
colour". Each key is two tokens per theme in `main.css`, `--color-agent-<key>` (the 3px
border) and `--color-agent-<key>-tint` (the bubble fill), so no literal hex enters a
component. A coloured agent bubble uses its tint as background and its key as the left
border, replacing `--color-surface`/`--color-accent`; an uncoloured one is unchanged.

Given up: free colour choice, colouring avatars and the rail, a mirrored layout, and the
wider impersonation fix (FU-1).

## 6. Detailed Changes

### Backend (Part B)

- **Migration 0105** (reversible): create PG enum `chatroom_agent_bubble_color` with the
  eight keys; add nullable `chatroom_agents.bubble_color` of that type; add `CHECK
  (bubble_color IS NULL OR role = 'normal')`. Downgrade drops the constraint, column and
  type. Existing rows get NULL, so no install changes appearance.
- **`conversation/infrastructure/tables.py`**: the column, typed with the same PG enum
  (`create_type=False`), not `sa.Text`, so asyncpg binds the enum type.
- **`conversation/domain/models.py`**: a `ChatroomAgentBubbleColor` enum beside
  `ChatroomAgentRole`, and the field on the binding model.
- **Repository**: `set_bubble_color(chatroom_id, agent_id, color | None)` that updates only
  a `normal` row and reports whether a row was written; `set_role` sets
  `bubble_color = NULL` in the same `UPDATE` when the new role is `observer` (Q-10).
- **`ChatroomService.set_agent_bubble_color`** returning one of written / not bound /
  observer binding, and emitting audit `chatroom.agent_appearance_changed`
  (`resource_type="chatroom_agent"`, metadata `agent_id`, `old_color`, `new_color`) only
  when the value changed. Exposed on `ConversationFacade`.
- **`conversation/application/access.py`**: extract the shared body of
  `can_manage_room_wakeup` into `can_manage_normal_binding_settings(access, principal,
  observer_binding)`; `can_manage_room_wakeup` delegates to it, and the new route uses it.
  One predicate, two named callers, no change to the wake-up behaviour.
- **`app/api/v1/chatrooms.py`**:
  - `AgentAppearanceIn` with `bubble_color: Literal[<eight keys>] | None`, validated by
    Pydantic at the boundary.
  - `PATCH /{chatroom_id}/agents/{agent_id}/appearance`, 204: `resolve_room_access`, then
    `ensure_can_read`, then the predicate above; 403 when it refuses, 404 when not bound,
    409 (problem+json) for an observer binding. On a write, `_emit_chatroom_updated(...,
    room_visible=True)`, because every reader sees the colour.
  - `AgentRef.bubble_color` (response-side only, same comment convention as `name`),
    filled for every reader in `list_chatroom_agents`. Non-creators still see only normal
    bindings, so this discloses nothing about observers.
- **SRS**: §13 delta applied at approval (§13 below).

### API contract

Changed: `AgentRef` gains optional `bubble_color`; new PATCH route. `gen:api` rerun:
yes.

### Frontend

Part A:

- `shared/transport/axios.ts` (and its barrel): export the token-derived viewer (rules
  1-2).
- `slices/conversation/composables/useChatroomMessages.ts`: compose the viewer with the
  `session.me` fallback; rewrite `isOwnMessage` on it; build the optimistic
  `sender_type`/`sender_id` from it; return `isOwnMessage` and the viewer.
- `slices/conversation/views/ChatroomView.vue`: derive `viewerId` from the same viewer;
  pass `:is-own="isOwnMessage(item.message)"`.
- `ChatroomMessageBubble.vue`: `isOwn` prop (default `false`); `.bubble-row` defaults to
  `align-items: flex-start`; `.bubble-row--own` aligns `flex-end`; forward `own` to the
  shell. Keep `bubble-row` and `bubble-row--pending`.
- `ChatroomBubbleShell.vue`: `own` prop adding `bubble--own` with `--color-bubble-own`.

Part B:

- `ChatroomBubbleShell.vue`: `agentColor` prop (palette key or null) adding
  `bubble--agent-<key>`, which sets the tint background and key border.
- `ChatroomMessageBubble.vue` and `ChatroomStreamingBubble.vue`: `agentColor` prop
  forwarded to the shell.
- `ChatroomView.vue`: a `agentColors` map built from `boundAgentsQuery` (beside
  `agentNames`, `:702-710`), passed per message and to the streaming bubble. An agent no
  longer bound, or bound as observer, has no entry and renders uncoloured.
- `ChatroomSettingsView.vue` Agent bindings card: per normal-role row, a colour control
  (off plus eight swatches, each an accessible radio with the colour's name) shown under
  the existing `:860` condition and hidden for observer rows. Writes go through a new
  mutation in `useChatroomBindings.ts`, beside the grant mutations, invalidating
  `chatroomAgents`.
- A palette constant (the eight keys, in a single place in the conversation slice) used by
  both the settings control and the bubble, so the UI cannot offer a key the API refuses.
- i18n (`en`, `zh-TW`): the control's label and help text, "No colour", and the eight
  colour names.

Shared:

- `shared/styles/main.css`: `--color-bubble-own`, and `--color-agent-<key>` /
  `--color-agent-<key>-tint` for each key, in both theme blocks.
- `shared/styles/__tests__/contrast.test.ts`: the pairs in AC-11.
- `docs/UI/07-conversation.md` §3.3: own-right/others-left, the own tint, and the agent
  colour rule; the room settings section gains the colour control.

### Deploy/config

None.

## 7. NFR Checklist

- [x] i18n: control label, help, "No colour", and the eight colour names through `$t()`,
  in both locales.
- [x] Audit log: `chatroom.agent_appearance_changed` on every effective change; a role
  change that clears a colour is already audited as `chatroom.observer_role_changed`.
- [x] Tenant isolation: the route resolves room access and applies `ensure_can_read` and
  the row-29 predicate before reading or writing the binding; the binding is addressed by
  the room's own id, so no cross-room write is possible.
- [x] Error handling UX: the settings control disables while saving, reverts and toasts on
  failure; 403/404/409 arrive as problem+json through `@shared/errors`.
- [x] Performance: one nullable column on an already-fetched row; one string comparison
  and one map lookup per rendered message.

## 8. Security Considerations

- **AuthZ.** The new write reuses matrix row 29's predicate and adds the observer refusal,
  so it grants nothing a row-29 caller could not already change on a normal binding.
  Guests and plain members who are not the creator get 403. Covered by AC-13.
- **Disclosure.** `bubble_color` is returned to every reader, guests included, but only on
  bindings non-creators already see (`chatrooms.py:765-766`); the observer layout stays
  hidden ([R28.10]). The route emits `room_visible=True`, which is correct only because
  the field is visible to everyone; the frame carries no colour itself.
- **Input.** A closed `Literal` at the Pydantic boundary plus the PG enum and `CHECK` at
  the database; no free text reaches CSS. The class name is built from a validated key on
  the client too, so a stored value cannot inject a selector.
- **Client identity.** The Part A viewer is read from the already-decoded, unverified
  claims the client uses today (`axios.ts:128-146`). It decides layout, not access; the
  backend stays authoritative for edit, delete and moderation (`useChatroomMessages.ts:28-31`).
  The two cases where client-offered affordances change (impersonation, a stale `me` under
  a guest token) now match what the server would accept.

## 9. Quality Notes

- **Existing debt**
  - Two independent viewer rules (`useChatroomMessages.ts:61-71`, `ChatroomView.vue:563`).
    This change removes the duplication; do not add a third.
  - `AgentRef` doubles as request and response model (`chatrooms.py:162-167`); the new
    field is response-side only, documented the same way, and the split stays FU-6 of its
    own dossier.
  - `can_manage_room_wakeup`'s name is wake-up specific; §6 extracts a neutral predicate
    instead of calling a wake-up function from an appearance route.
  - Bubble CSS uses physical properties; keep that.
  - `backend/CLAUDE.md` says migrations are `0000–0079`; the head is 0104 (FU-3).
- **Patterns to follow**
  - Decisions arrive at the bubble as props (`canEdit`, `senderIsGuest`).
  - Shared reactive token state as in `guestSessionId` (`axios.ts:158-162`).
  - Per-binding writes as in `set_agent_activity_grant` (service, facade, route,
    `_emit_chatroom_updated`), and the CAS-plus-audit shape of `set_agent_role`
    (`chatroom_service.py:418-466`).
  - Column type matches the migration's PG enum, as `role` does (`tables.py:81-86`).
  - Colour tokens in both theme blocks of `main.css`, pinned in `contrast.test.ts`.
- **Reuse inventory**: `accessTokenClaims`, `isGuestSession`, `guestSessionId`
  (`@shared/transport`); `useSessionStore`; `resolve_room_access`, `ensure_can_read`,
  `is_room_creator`, the wake-up predicate body (`access.py`); `_emit_chatroom_updated`;
  `audit.emit`; `convKeys.chatroomAgents`; `useChatroomBindings`' mutation pattern;
  `ChatroomBubbleShell` for the frame; `useToast`; `SRadio` (`shared/ui/SRadio.vue`) as
  the basis of the swatch picker.

## 10. Risks and Rollback

- A pending message and its echo landing on different sides would make the bubble jump on
  ack; one viewer for both prevents it (AC-4).
- Between page load and token restore no token is held; rule 3's fallback keeps own
  messages on the right in that window.
- Eight tints must each pass contrast in two themes; AC-11 makes failure a test failure,
  and the build may shorten the palette (recorded as a D-n) if a hue cannot pass.
- A colour-blind viewer may not distinguish some pairs; the sender name and avatar remain
  the primary identification, and colour is supplementary (FU-2).
- Rollback: Part B's migration downgrade drops the column and type; reverting the frontend
  first is safe because the new field is optional. Part A is frontend only.

## 11. Acceptance Criteria

Part A

- [ ] AC-1: For a registered viewer, their own `user` messages render with
  `bubble-row--own` (right, tinted); other users', guests', and agents' messages render
  without it (left).
- [ ] AC-2: For an anonymous guest viewer, their own `guest` messages are own; other
  guests' and all users' messages are not, including an account still in `session.me` from
  an earlier sign-in (Q-3).
- [ ] AC-3: While impersonating, the impersonated user's messages are own and the admin's
  are not (Q-2); ending impersonation restores the admin's view without a reload.
- [ ] AC-4: An optimistic send is own from its first render and stays own after the echo
  replaces it.
- [ ] AC-5: The roster "you" marker and moderation exclusions (`ChatroomView.vue:1389`,
  `:1408`, `:1411`) use the same viewer as AC-1 to AC-3.
- [ ] AC-6: `canEdit`/`canDelete` are unchanged for a normal user, a guest, an admin, and a
  moderator (`useChatroomMessages.test.ts:407-439` passes).

Part B

- [ ] AC-7: Migration 0105 upgrades and downgrades cleanly on a database with existing
  bindings; after upgrade every existing binding has `bubble_color IS NULL`, and inserting
  an observer row with a colour violates the `CHECK`.
- [ ] AC-8: `PATCH .../appearance` with a palette key, by the room creator, by a Project
  Owner, and by an Org Owner, returns 204, stores the key, writes one
  `chatroom.agent_appearance_changed` audit row, and emits a room-visible
  `chatroom.updated`; a repeat with the same key writes no second audit row. `null` clears
  it.
- [ ] AC-9: The same PATCH returns 403 for a Project Member who is not the creator, an Org
  Member who is not the creator, and a guest; 404 for an unbound agent; 409 for an observer
  binding; 422 for a key outside the palette.
- [ ] AC-10: Changing a coloured binding's role to observer clears its colour in the same
  write; demoting it back to normal leaves it uncoloured.
- [ ] AC-11: `contrast.test.ts` asserts, in both themes: `--color-fg`, `--color-muted`, and
  `--color-accent` at 4.5:1 on `--color-bubble-own` and on every `--color-agent-<key>-tint`;
  every `--color-agent-<key>` at 3:1 against `--color-canvas` (the feed background behind
  the border); and passes.
- [ ] AC-12: `GET /chatrooms/{id}/agents` returns `bubble_color` for normal bindings to
  every reader (creator, member, guest), and still returns no observer binding to
  non-creators.
- [ ] AC-13: In room settings, the colour control appears for normal rows under the
  `:860` condition, never for observer rows, and a save updates another open client's
  bubbles for that agent without a reload.
- [ ] AC-14: An agent's persisted and streaming bubbles both use its colour; an agent with
  no colour, or no longer bound, renders with today's agent style.

Both

- [ ] AC-15: The e2e specs selecting `.bubble-row`/`.bubble-row--pending` pass unchanged.
- [ ] AC-16: `docs/UI/07-conversation.md` describes both rules and the settings control;
  `REQUIREMENTS.md` carries §13's delta and `docs/traceability.csv` is regenerated.
- [ ] AC-17: Manual check on a running stack, light and dark, desktop and mobile width:
  two signed-in users, one guest and two coloured agents in one room; each person sees
  only their own messages on the right, and both agents on the left in their colours.
- [ ] AC-18: Backend `ruff`, `mypy`, `pytest`; frontend `pnpm lint`, `typecheck`, `test`,
  `build`, `check:openapi-drift` pass.

## 12. Test Plan

- AC-1 to AC-4: unit tests in `useChatroomMessages.test.ts` for `isOwnMessage` across the
  viewer cases, with tokens set through `setAccessToken`; AC-3's "without a reload" is a
  second `setAccessToken` under the same computed. A component test in
  `ChatroomMessageBubble.test.ts` that `isOwn` toggles `bubble-row--own` and `bubble--own`.
- AC-5: extend the `ChatroomView` roster tests with an impersonation token.
- AC-7: a `db`-tier migration test (upgrade, downgrade, `CHECK` violation).
- AC-8, AC-9, AC-10, AC-12: route and service tests; the role-clearing `UPDATE` and the
  enum binding need a `db`-tier test, since the unit tier compiles with `literal_binds` and
  cannot see an enum bind error (`backend/CLAUDE.md`, "PostgreSQL-specific SQL").
- AC-11: `contrast.test.ts`.
- AC-13, AC-14: component tests for the settings control and for colour forwarding to
  both bubble kinds; the cross-client half rides on the existing `chatroom.updated`
  invalidation tests (`useChatroomSocket.test.ts:1209-1273`) plus AC-17.
- AC-15, AC-18: existing suites on CI.
- AC-16: doc diff review and `python scripts/traceability.py --check`.
- AC-17: manual, via the `frontend:verify` skill.

## 13. SRS Delta

Insert in §13.5 Output / rendering, after [R13.76]:

- **[R13.77]** **Message sides.** In the chatroom feed, a message is placed on the right
  if and only if the current viewer sent it, with a light accent tint; every other
  message (other users, guests, agents) is placed on the left. The current viewer is the
  identity in the access token the client holds: the guest session for a guest token,
  otherwise the token's subject, including while an admin is impersonating a user. System
  rows and released observations keep their own full-width layouts.
- **[R13.78]** **Agent bubble colour.** A room binding with role `normal` may carry one
  bubble colour from a fixed platform palette, or none. It is set per room, so one agent
  can differ between rooms, and it is shown to every reader of the room, guests included,
  on that agent's persisted and streaming bubbles. Observer bindings carry no colour: the
  server refuses to set one, and changing a binding's role to observer clears it. Who may
  set it is matrix row 29's audience; every change is audit-logged.

Amend matrix row 29 (`REQUIREMENTS.md:208`):

`| 29 | Change a room's agent wake-up settings ([R15.25]) and bubble colour ([R13.78]) | ✓ | ✓ | ∘ (room creator) | ✓ | ∘ (room creator) | ✗ |`

Amend the row 29 note (`REQUIREMENTS.md:212`), first sentence:

`**Row 29 — room wake-up settings and bubble colour.** The caller must also be able to read the room.`

(The rest of the note is unchanged; its observer clause is subsumed for colour by
[R13.78]'s refusal.)

## 14. Open Questions

None.

## 15. Deviation Log

Appended by /build.

- D-1: Implementation plan approved on 2026-10-20. The unavailable `run` and
  `frontend:verify` skills are replaced by direct browser verification, as approved
  by the user. Task base: `792b316a73ce25b2a798301ff2faa6166cfbde8c`;
  branch: `feat/chat-bubble-sides-and-agent-colors`.

## 16. Follow-ups

- FU-1: While impersonating, `session.me` and `isAdmin` still describe the admin
  (`useImpersonation.ts:32-52` never calls `session.refreshMe()`), so admin-only UI stays
  visible and `canEdit`/`canDelete` stay permissive as the impersonated user. Worth its own
  dossier.
- FU-2: The palette has not been checked for colour-vision deficiency; neither has the
  existing `GraphragGraphView.vue:42-59` palette. A shared categorical palette with a
  CVD check could serve both.
- FU-3: `backend/CLAUDE.md` states migrations `0000–0079`; the head is 0104.
- FU-4: `senderLabel` (`ChatroomView.vue:1583`) compares the guest id separately for the
  "you" label; correct today, but could read the shared viewer.
