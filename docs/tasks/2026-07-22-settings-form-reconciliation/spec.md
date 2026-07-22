---
type: bugfix
status: draft
created: 2026-07-22
requirements: [R13.04, R13.21, R13.23, R28.09]
depends_on: []
---

# Settings form reconciliation and moderator affordances

## 1. Summary

Three defects on the chatroom settings and message-permission surfaces, all of the same
family: the frontend renders a permission state that the server does not agree with.
`ChatroomSettingsView`'s four access toggles flip optimistically and never revert when the
PATCH is rejected, so a creator can be looking at "Allow guest links: ON" for a room the
server left closed (**F-7**). The same form paints itself from an unrevalidated query cache,
so after a 409 it refreshes only the version and re-submits the operator's stale field
values, silently reverting another user's saved rename (**F-8**). Separately, project and org
owners — who the backend accepts as moderators for message edit and delete — are shown
neither affordance, because the frontend implements only the platform-admin half of
`[R13.23]` and the DTO it is given carries no signal it could use (**V-4**). Impact: a
security-relevant control displaying a state the server rejected, last-write-wins on room
settings whenever two people have the page open, and moderation that is unreachable through
the UI for every non-admin owner.

`depends_on` is empty and that is a determination, not an omission. The originating audit
groups F-8 with F-11/F-13 as "defects only because F-1 keeps focused tabs reconnecting"
(`docs/audits/2026-07-22-agent-to-user-conversation/findings.md:83-85`). That coupling does
not hold for F-8 — see **Q-1** — so no ordering constraint against
`docs/tasks/2026-07-22-chatroom-socket-lifecycle/` exists.

## 2. Observed vs Expected

### F-7 — access-mode toggles never revert on a rejected save

- **Observed** — `ChatroomSettingsView.vue:160-163` (`setFlag`) writes `flags[key] = value`
  and fires `void onSave()`. `useChatroomSettings.ts:109-111`, the non-409 catch, sets
  `saveError.value = 'conversation.settings.saveFailed'` and returns; the mirrored flag is
  never restored and no toast fires. The only error signal is an `SAlert` rendered inside the
  **General** card (`ChatroomSettingsView.vue:307-314`), far above the Access Control card
  that holds the toggle. When the rejected toggle is `allow_guest_links`, the dependent Guest
  Link card also appears, because it is gated on the local mirror
  (`v-if="flags.allow_guest_links"`, `ChatroomSettingsView.vue:425`).
- **Expected** — `docs/UI/07-conversation.md:1157-1158`: "Changes save immediately on toggle
  (optimistic update via `PATCH /api/chatrooms/{cid}`) … On error: toggle reverts,
  `useToast().error()` with failure message."

A second, narrower deviation on the same path: `onSave` unconditionally sends
`name: name.value` (`useChatroomSettings.ts:94-97`). Flipping any toggle therefore commits
whatever is currently in the name field, including a half-typed rename that the user has not
submitted — the name form has its own gated Save button
(`ChatroomSettingsView.vue:296-305`, `:disabled="saving || !nameDirty"`), whose gate this
bypasses.

### F-8 — a form painted from a stale cache launders a stale write through the 409

- **Observed** — `useChatroomSettings.ts:65-73`: `loadRoom` returns straight out of
  `findInCache()` with no background revalidation, so both the form fields and
  `room.value.version` can be arbitrarily stale. `useChatroomSettings.ts:102-108`: the 409
  branch assigns `room.value = await getChatroom(...)` but does **not** call `applyRoom`, so
  `name.value` and `flags.*` keep the operator's stale values while the version advances to
  the server's. The next save then succeeds with a fresh version carrying stale content.
  The sibling path one function down does it correctly and says why:
  `useChatroomSettings.ts:143-146` — "applyRoom resyncs flags.* … from the authoritative
  server state, not just `room.value`."
- **Expected** — `docs/UI/12-shared-patterns.md:305-312` §4.3 Optimistic Concurrency: on 409,
  "Auto-refresh the data (re-fetch via TanStack Query invalidation)" and "User re-applies
  changes on fresh data". Refreshing the version without refreshing the data inverts the
  control: the mechanism that exists to *prevent* a stale overwrite becomes the thing that
  authorises one.

### V-4 — project and org owners get no edit or delete affordance

- **Observed** — the backend honours both tiers: `backend/app/api/v1/messages.py:472` allows
  delete on `principal.is_admin or access.is_moderator or is_author`, and
  `backend/contexts/conversation/application/message_service.py:258` takes the moderator edit
  path on `authority.is_admin or authority.is_moderator`, where `is_moderator` is
  `Role.PROJECT_OWNER in roles or Role.ORG_OWNER in roles`
  (`backend/contexts/conversation/application/access.py:46-49`). The frontend implements only
  the platform-admin half: `useChatroomMessages.ts:53-61` (`canEdit`) and `:63-66`
  (`canDelete`) derive from `session.me?.is_admin` (`:45`) and own-authorship alone, and a
  repo-wide search for `isModerator` across `frontend/src` returns zero hits. The affordances
  are bound at `ChatroomView.vue:66-67` and rendered at
  `ChatroomMessageBubble.vue:195,204`.
- **Expected** — `[R13.23]` (`REQUIREMENTS.md:697`): "**Admin / Project Owner** may edit any
  message in their scope"; `[R13.21]` (`:695`): beyond five minutes "only Admin/Project Owner
  can edit it". `docs/UI/07-conversation.md:377-379` states the affordance rule verbatim:
  "Edit visible: author within 5 minutes of `created_at`, or user has admin/owner role" and
  "Delete visible: author (own message), or user has admin/owner role".

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Does fixing F-1 (`docs/tasks/2026-07-22-chatroom-socket-lifecycle/`) make F-8 more likely, less likely, or unchanged — and is there an ordering constraint? | **Unchanged. No ordering constraint in either direction.** The two dossiers may be built in any order or concurrently. | The a2u audit's §3 head (`findings.md:83-85`) generalises F-11/F-13's genuine dependency on F-1 to F-8, and the generalisation does not survive tracing. (a) The settings route mounts **no socket** — `ChatroomSettingsView.vue` imports no socket composable, so F-1's reap/reconnect cycle never runs while the form is open. (b) Even in `ChatroomView`, the reconnect handler resyncs exactly three things — `replayDelta`, `resyncPresence`, `resyncActivation` (`useChatroomSocket.ts:347-356`) — and touches no `['conversation','chatrooms']` cache entry, so reconnect churn neither refreshes nor staleness the source F-8 reads from. (c) F-8's staleness has an independent, sufficient cause: `findInCache` prefix-matches `['conversation','chatrooms']` (`useChatroomSettings.ts:55-57`), which catches both `convKeys.chatrooms(workspaceId)` and `convKeys.recentChatrooms(projectId)` (`queries/index.ts:16-18`, whose comment at `:9-11` records the nesting as deliberate). The recent-chatrooms entry is owned by the app-shell sidebar (`SidebarChatroomList.vue:13-16`), stays mounted across the route change, and carries `staleTime: 60_000` (`useRecentChatrooms.ts:42`) — the only explicit staleTime in the path, since `query-client.ts:4-21` sets none by default. The workspace-scoped entry from `ChatroomListView.vue:58` survives unmount on the default gcTime. Neither has anything to do with the WebSocket. **Recorded as a correction the a2u audit's §3 head needs.** |
| Q-2 | V-4 needs a moderator signal the frontend does not have. Add one to `ChatroomOut`, or reuse the `projectsApi.listMembers` lookup that already exists at `ChatroomSettingsView.vue:98-111`? | **Add `is_moderator: bool` to `ChatroomOut`.** | Decided on correctness, not convenience — the member-lookup option is *provably* unable to express the predicate. `projects.list_members` (`backend/app/api/v1/projects.py:259-288`) returns rows from `project_members` only, but `PROJECT_OWNER` is a **computed** role: `role_resolver.py:41-48` grants it to any `OrgMemberRole.OWNER` of the parent org, with no `project_members` row required (R5.03). So an org owner moderating a room in a project she has never been added to resolves `role === 'owner'` → `false` on the client and `Role.ORG_OWNER in roles` → `true` on the server. The two halves of `access.py:49` cannot both be reconstructed from the members list. Cost comparison favours the DTO too: both routes that build a `ChatroomOut` for a room the user is in **already** resolve the caller's roles — `list_chatrooms` at `chatrooms.py:194-198`, `read_chatroom` at `:253-257` — so the field is zero additional queries, whereas the member lookup adds a `GET /projects/{id}/members` per room open. Finally the DTO keeps one predicate in one place: the frontend stops re-deriving an authorization rule it is structurally unequipped to derive. **Accepted costs**: `_to_out` (`chatrooms.py:115-119`) gains a keyword argument and all four call sites (`:208`, `:236`, `:268`, `:316`) must pass it; `pnpm run gen:api` and `pnpm run check:openapi-drift` must both run. |
| Q-3 | For the flags, revert-on-failure (the `saveDisclosure` shape) or eliminate the mirror entirely (the `WorkspaceSettingsView` shape)? | **Eliminate the mirror for the flags; keep a draft ref only for `name`.** | See §7. A revert you can forget to write is exactly the defect being fixed; a projection has nothing to forget. `SToggle` is fully controlled — `SToggle.vue:4-15` declares `modelValue` as a prop with no internal state and `:26-29` only emits — so a toggle whose `:model-value` reads server state is *physically incapable* of displaying a value the server did not accept. |
| Q-4 | Does the fix need to preserve `disclose_observers`' separate save path? | **Yes, unchanged.** | `useChatroomSettings.ts:88-92` documents why: the field is creator-only on the server (R28.09) and including it in a generic save would 403 a non-creator moderator editing the other flags. The per-flag patch shape adopted in §7 is a generalisation of `saveDisclosure`, not a replacement for it. |
| Q-5 | Should the e2e suite gain a non-admin project-owner moderation case? | **Not in this dossier.** Recorded as FU-1. | `frontend/e2e/fixtures/auth.ts:85-90` exposes exactly two authenticated fixtures, `authedPage` and `adminPage`. There is no owner fixture and no seeded non-admin owner, so the test would require a seed change. `frontend/e2e/13-message-edit-delete.spec.ts:78-96` covers only the admin half today, with a comment at `:87-88` naming the `is_admin` short-circuit — the coverage gap is real but its fix is a fixture change, not a behaviour change. |

## 4. Reproduction

**Preconditions (all three)** — one project P with a workspace and chatroom R; users A and B
both project members with `Capability.CHAT_ROOM_MANAGE` on R; a third user O who is Project
Owner of P (or Org Owner of P's parent org) and **not** a platform admin.

**F-7** — deterministic.
1. As A, open `/chatrooms/{R}/settings`.
2. Force the PATCH to fail (stop the backend, revoke A's capability, or intercept
   `PATCH /api/chatrooms/{R}` and return 500).
3. Flip **Allow guest links** on.
4. Observed: the toggle stays ON; the "Guest Link" card
   (`ChatroomSettingsView.vue:425`) appears and begins fetching a link that does not exist;
   the only error is an `SAlert` in the General card above the fold
   (`ChatroomSettingsView.vue:307-314`); no toast. Reload the page and the toggle is OFF.

**F-7 rename bleed** — same setup, no failure needed.
1. As A, type a partial new name into the General field but do **not** click Save.
2. Flip any access toggle.
3. Observed: the partial name is persisted, because `onSave` always sends
   `name: name.value` (`useChatroomSettings.ts:94-97`).

**F-8** — deterministic given the cache precondition.
1. As A, visit the workspace chatroom list (populating `convKeys.chatrooms(ws)`) or simply
   sit on any page with the sidebar mounted (populating `convKeys.recentChatrooms(P)` —
   `useRecentChatrooms.ts:27-43`), then navigate to `/chatrooms/{R}/settings`. `loadRoom`
   paints from cache and returns without revalidating (`useChatroomSettings.ts:68-73`).
2. As B, in another browser, rename R from "Room One" to "Renamed By B" and save. This bumps
   the version; A's cached copy is now stale on both name and version. Within the sidebar
   query's 60s `staleTime` (`useRecentChatrooms.ts:42`) nothing refreshes A's copy.
3. As A, flip any access toggle → 409 → the version conflict alert renders.
4. As A, click **Save changes** (or flip the toggle again).
5. Observed: the save succeeds. R's name is back to "Room One". B's rename is gone with no
   error shown to anyone.

**V-4** — deterministic.
1. As A, post a message in R and wait more than five minutes.
2. As O, open `/chatrooms/{R}` and hover A's message.
3. Observed: neither Edit nor Delete renders. Both API calls would succeed for O
   (`messages.py:472`, `message_service.py:258`).

## 5. Root Cause Analysis

### F-7 and F-8 share a root cause

They do — at the level that matters for the fix, and the fix for one is the fix for the
other. The causal chain:

1. `useChatroomSettings` maintains a **local mirror** of server state: `name` as a `ref`
   (`:25`) and `flags` as a five-key `reactive` (`:26-34`), alongside the authoritative
   `room` ref (`:35`). `applyRoom` (`:43-51`) is the only function that reconciles the mirror
   to a server object.
2. Every path that reaches server truth must therefore remember to call `applyRoom`. Three
   of the six do: the `onSave` success path (`:93`), the `saveDisclosure` success path
   (`:132`), and `saveDisclosure`'s 409 path (`:145`).
3. Three do not. `loadRoom`'s cache hit calls `applyRoom(cached)` on data it never
   revalidated (`:69`) — a reconciliation to a *stale* object, which is the same failure as
   not reconciling. `onSave`'s 409 path assigns `room.value` directly and skips `applyRoom`
   (`:105`). `onSave`'s generic-error path reconciles nothing at all (`:109-111`).
4. Callers write the mirror *before* the round-trip: `setFlag`
   (`ChatroomSettingsView.vue:161`) mutates `flags[key]` and only then fires the save.
5. Consequence: the mirror is the sole input to what the user sees (the toggles read
   `flags.*` at `ChatroomSettingsView.vue:336,355,371,387`; the dependent Guest Link card
   reads it at `:425`) **and** the sole input to the next PATCH body (`:94-97`). Once it
   diverges from server truth it both misinforms the user and re-submits the divergence.

**Root cause, named**: *the form's local mirror of server state has no invariant tying it to
a server response — reconciliation is a per-call-site convention that three call sites do not
follow.* F-7 is the write-side symptom (mirror ahead of the server, never pulled back), F-8
the read-side symptom (mirror behind the server, never pushed forward).

Two distinct earliest links exist within that one cause, and both must be corrected:
- **F-7's earliest link**: `ChatroomSettingsView.vue:160-163` writes the mirror on a path
  whose failure branch (`useChatroomSettings.ts:109-111`) has no restore.
- **F-8's earliest link**: `useChatroomSettings.ts:68-73` paints from an unrevalidated cache.
  The 409 branch's partial assign (`:105`) is an **aggravating factor**, not the root — with
  a fresh form the 409 would be a benign retry; with a stale form the partial assign converts
  the conflict signal into a licence to overwrite.

**Aggravating factors, distinguished**: (a) the error `SAlert` is positioned in the General
card (`ChatroomSettingsView.vue:307-314`) while the failing control lives in the Access
Control card, so even the signal that does exist is easy to miss — this makes F-7 worse but
does not cause it; (b) `saving` is a single flag shared by the name form and all five
toggles, which is why the audit's "double-toggle is silently dropped" sub-claim was refuted
(`:337,356,372,388` all carry `:disabled="saving"`, and `onSave` sets `saving.value = true`
synchronously at `:85`) — worth knowing so the fix does not "solve" a problem that is not
there.

### The codebase already has a correct pattern — two of them

**Exemplar A, in-file, same shape (revert-on-failure)**: `saveDisclosure`
(`useChatroomSettings.ts:125-157`). It captures `previous` before the optimistic write
(`:127`), calls `applyRoom` on the 409 refetch (`:145`), and reverts explicitly when the
refetch itself fails (`:148`) or the error is generic (`:152`). Its docstring (`:117-124`)
states the invariant the rest of the file breaks: "every failure path must leave
`flags.disclose_observers` matching the server's actual value". It is test-pinned three ways
at `frontend/src/slices/conversation/__tests__/useChatroomSettings.test.ts:39-111`.

**Exemplar B, repo-wide, stronger (no mirror at all)**: bind the control directly to server
state and let the mutation invalidate.
- `WorkspaceSettingsView.vue:78-81` — `:model-value="workspace.concept_map_enabled"` read
  from a `useQuery`, `@update:model-value` firing a mutation whose `onSuccess` invalidates
  and whose `onError` toasts (`:37-41`). There is no local boolean, so there is nothing to
  revert: a rejected click leaves the query data untouched and the toggle re-renders in its
  server position on the next tick.
- `AgentGroupDetailView.vue:219-222` with its mutation at `:102-106` — the same shape,
  independently written.
- `AgentToolsView.vue:748,987` → `toggleSingleton` (`:111-114`) → `toggleMutation`
  (`:99-107`) — the same shape again.
- `SearchKeyView.vue:232-239` → `onActivate` (`:100-107`) — a third variant: revert by
  refetch (`await reload()` in the catch).

Exemplar B is the stronger of the two because `SToggle` is fully controlled (`SToggle.vue:4-15`
props-only, `:26-29` emit-only): with no mirror, displaying an unaccepted value is not
expressible. That is the pattern §7 adopts for the flags.

### V-4

The chain is short and is an absence rather than a mistake:

1. `useChatroomMessages` computes affordances from `session.me?.is_admin` and own-authorship
   only (`:44-45`, `:53-61`, `:63-66`). Its own header comment (`:25-28`) states the intended
   rule — "R13.21/R13.23: … Beyond that only Admin/Project Owner may" — and the code
   implements only the Admin arm.
2. It could not implement the other arm from what it is given. `ChatroomOut`
   (`backend/app/api/v1/chatrooms.py:74-89`) carries `workspace_id` but no `project_id` and
   no role flag, and the `Chatroom` the frontend caches is that DTO verbatim.
3. **Root cause, named**: *the moderator predicate is computed server-side
   (`access.py:46-49`) and never serialized, so the client has no input from which to derive
   the affordance.* Everything downstream is a consequence.
4. The pattern for resolving it out-of-band exists in the same slice and was never applied
   here: `ChatroomSettingsView.vue:97-111` resolves project-owner status via
   `projectsApi.listMembers`, built for the observer creator-gate (R28.02). Q-2 records why
   that pattern is the wrong one to copy — it cannot see org owners
   (`role_resolver.py:41-48` vs `projects.py:264-288`).

## 6. Blast Radius and Sibling Suspects

### Blast radius

- **F-7** — a security-relevant control (`allow_guest_links` governs external guest
  enrolment) displaying a state the server rejected, for the life of the page. Also the
  reverse: a creator who believes they *closed* guest access when the PATCH 500'd. The
  rename-bleed sub-path writes unintended data to a persisted field on every toggle.
- **F-8** — last-write-wins on room settings whenever two people have the page open, across
  all five fields including all four access flags. **Data already written**: yes — see §7's
  data-repair position.
- **V-4** — moderation unreachable through the UI for every non-admin project or org owner,
  in every room, since the affordance was written. No data loss and no disclosure: the
  backend capability is intact, so this is an affordance gap, not an AuthZ gap. Recovery
  today requires escalating to a platform admin or calling the API by hand.
- **Blast radius of the V-4 fix itself** — `_to_out` is the single serializer for every
  `ChatroomOut` in the system (`chatrooms.py:208,236,268,316`), so the DTO change touches
  create, read, list and patch responses, plus the generated frontend client.

### Sibling suspects — every optimistic toggle in the frontend, swept

| Site | Verdict | Evidence |
|---|---|---|
| `ChatroomSettingsView.vue:335-339,354-358,370-374,386-390` (four access flags) | **CONFIRMED** | F-7 itself. Local mirror written at `:161`, no restore at `useChatroomSettings.ts:109-111`. |
| `ChatroomSettingsView.vue:408-412` (`disclose_observers`) | **CLEARED** | `saveDisclosure` reverts on both failure branches (`useChatroomSettings.ts:148,152`) and re-applies on 409 (`:145`); pinned by `useChatroomSettings.test.ts:39-111`. This is Exemplar A. |
| `WorkspaceSettingsView.vue:78-81` (concept-map privacy) | **CLEARED** | No mirror — `:model-value` reads `workspace.concept_map_enabled` from a `useQuery` (`:22-25`); mutation invalidates on success, toasts on error (`:37-41`). Exemplar B. |
| `AgentGroupDetailView.vue:219-222` (group privacy) | **CLEARED** | Identical shape; mutation at `:102-106`. |
| `AgentToolsView.vue:748,987` (singleton tool enable) | **CLEARED** | `toggleSingleton` (`:111-114`) delegates to `toggleMutation` (`:99-107`); the toggle reads `card.tool.enabled` from the tools query, so a rejected PATCH leaves the rendered value untouched. |
| `SearchKeyView.vue:232-239` (activate search key) | **CLEARED** | `onActivate` (`:100-107`) toasts **and** `await reload()`s in the catch — explicit revert-by-refetch. |
| `ChatroomSettingsView.vue:510-517` (agent role select) | **CLEARED** | `onSetRole` (`useChatroomBindings.ts:112-124`) calls `loadBindings()` on success and sets `bindingError` on failure without mutating the roster; the select reads `agent.role` from the reloaded roster, so a rejected change never sticks. |
| `ChatroomSettingsView.vue:534-537` (`SWakeupEditor` autosave) | **CLEARED, fragile** | `saveWakeupConfig` (`useChatroomBindings.ts:149-174`) toasts on failure and deliberately does **not** write the mirror — the comment at `:140-145` records this as a decision ("that would re-render the editor and revert in-progress edits"). The consequence is that the editor keeps showing an unsaved value after a failure. That is a defensible trade for a multi-field editor and is out of scope here. → **FU-2**. |
| `RagConfigDetailView.vue:728,843` and `KnowledgeMapConfigDetailView.vue:696,807` (per-document agent allowlists) | **CLEARED** | `toggleUploadAgent`/`toggleEditAgent` (`RagConfigDetailView.vue:119-136`, `KnowledgeMapConfigDetailView.vue:179-196`) write local refs consumed by an explicit `setAgentsMutation` (`:137-148` / `:197-208`). Not optimistic — staged edits behind a Save. |
| `ObservationReleaseDialog.vue:83` | **CLEARED** | Local dialog selection state; nothing is sent until submit. |
| `ChatroomListView.vue:374-395` (create-room access flags) | **CLEARED** | `v-model="createFlags.*"` into a local form submitted by an explicit create mutation. |
| `MemberConfigPanel.vue:112,119`; `RagConfigListView.vue:553`; `SkillWorkbench.vue:84` | **CLEARED** | `v-model` into local form state or a client-only list filter, with an explicit submit or no server call at all. |
| Optimistic **non-toggle** actions in the conversation slice (send, edit, delete) | **CLEARED** | All three already roll back and are pinned: `useChatroomMessages.test.ts:160-176` (send rollback), `:205-231` (edit reopens with the user's text), `:232-241` (delete restores on rejection). |

**Conclusion of the sweep**: F-7 is the only surviving instance of the pattern. It is not
systemic — it is the one site in a slice that otherwise applies the rule, including in the
very file that defines it.

### Sibling suspect for the V-4 root cause

| Site | Verdict | Evidence |
|---|---|---|
| `ChatroomSettingsView.vue:97-111` (`isCreator` fallback for `created_by_user_id === null`) | **CONFIRMED as carrying the same org-owner gap, OUT OF SCOPE** | It resolves ownership through `projectsApi.listMembers` (`:99`) and tests `membership?.role === 'owner'` (`:109-110`), which cannot see an org owner with no `project_members` row (`role_resolver.py:41-48`). But it gates a *different* predicate (R28.02 creator fallback, not R13.23 moderator), it fails **closed** (a missed owner sees fewer controls, never more), and changing it is a behaviour change on the observer surface rather than a bugfix here. → **FU-3**. |

## 7. Fix Design

### 7.1 F-7 + F-8 — replace the mirror with a projection (frontend, conversation slice)

All changes in `frontend/src/slices/conversation/composables/useChatroomSettings.ts` and
`frontend/src/slices/conversation/views/ChatroomSettingsView.vue`.

1. **`flags` stops being a `reactive` mirror.** Derive the four access flags and
   `disclose_observers` as a `computed` projection of `room.value`, replacing the mirror at
   `useChatroomSettings.ts:26-34` and the five assignments in `applyRoom` (`:46-50`). The
   toggles at `ChatroomSettingsView.vue:336,355,371,387,409` and the Guest Link card's gate
   at `:425` then read server state directly. This is Exemplar B
   (`WorkspaceSettingsView.vue:78-81`).
2. **`setFlag` becomes a per-flag patch that always ends in a server object.** Move it out of
   the view (`ChatroomSettingsView.vue:160-163`) into the composable, next to
   `saveDisclosure`, and give it `saveDisclosure`'s body shape minus the manual revert (which
   the projection makes unnecessary): send **only the changed key**, `applyRoom` the response
   on success, `applyRoom(await getChatroom(...))` on 409, and — the new part —
   `applyRoom(await getChatroom(...))` on a generic failure too, replacing
   `useChatroomSettings.ts:109-111`'s bare `saveError` assignment. Per Q-4, `saveDisclosure`
   stays as the creator-only arm.
3. **`onSave` sends only what the name form owns.** Drop the `...patchFlags` spread from
   `useChatroomSettings.ts:92-97`, leaving `{ name }`. This removes the rename-bleed defect
   in both directions: a toggle no longer commits a half-typed name, and the name form no
   longer re-submits flags it did not change. It also makes the `chatroom.updated` audit's
   `metadata.changed` list (`chatroom_service.py:172`) truthful, which it currently is not.
4. **`onSave`'s 409 branch calls `applyRoom`, not a bare assign.** Replace
   `useChatroomSettings.ts:105` with `applyRoom(await getChatroom(chatroomId))`, matching
   `saveDisclosure:145` and its comment at `:143-144`. This is the direct F-8 correction:
   after a conflict the operator sees the other user's saved values and re-applies their edit
   on top, as `docs/UI/12-shared-patterns.md:311-312` prescribes.
5. **`loadRoom` revalidates.** Keep the cache read at `useChatroomSettings.ts:68-73` for the
   instant paint, but always fire `getChatroom(chatroomId)` and `applyRoom` its result when
   it lands — stale-while-revalidate rather than stale-forever. This closes F-8 at its
   earliest link: a form that starts fresh cannot launder a stale write through a 409, and it
   removes the 60s window that `useRecentChatrooms.ts:42` opens by design for the sidebar.
   Guard the late apply against a user edit in flight so revalidation never clobbers typing
   (only `name` can be dirty once step 1 lands; `nameDirty` already exists at
   `ChatroomSettingsView.vue:143-145`).
6. **A toast on toggle failure.** `docs/UI/07-conversation.md:1158` requires
   `useToast().error()`, and `toast` is already in scope (`useChatroomSettings.ts:20`).
   Reuse the existing keys `conversation.settings.saveFailed` and
   `conversation.settings.versionConflict`
   (`frontend/src/slices/conversation/locales/en.json:276-277`) via `$t()`; per
   `docs/UI/12-shared-patterns.md:301` a conflict is a `toast.warning`, matching
   `useEntityLifecycle.ts:46-47`. Keep the inline `SAlert` as well. No new i18n keys are
   required; if any are added they must land in **both** `en.json` and `zh-TW.json`.

**Why this corrects rather than masks.** The masking fix is "add a revert to the catch
block" — it restores the invariant at one more call site while leaving the invariant
unenforced, which is precisely how `saveDisclosure` came to be correct and `setFlag` did not.
Deleting the mirror removes the class: there is no second copy of the flags to diverge, so no
future call site can forget to reconcile one. `SToggle`'s controlled design
(`SToggle.vue:4-15,26-29`) is what makes this available at zero cost — the component holds no
state of its own to go stale. `name` legitimately remains a draft ref because it *is* a draft:
the user types into it and commits with a button.

**Note for the implementer**: `docs/UI/07-conversation.md:1156` describes R13.04
auto-correction — turning on "Restrict to project owners" dims the two sibling toggles. Since
the projection reads `room.value`, whatever the server returns for the siblings is what
renders, automatically and without a second round-trip. This is a further argument for the
projection over the mirror and should not be undone by re-introducing local coupling between
the flags.

### 7.2 V-4 — serialize the moderator bit (backend + frontend)

1. **Backend.** Add `is_moderator: bool` to `ChatroomOut`
   (`backend/app/api/v1/chatrooms.py:74-89`) and a corresponding keyword parameter to
   `_to_out` (`:115-119`), defaulting to `False` so every call site is explicit about
   granting. Compute it exactly as the enforcement path does — `principal.is_admin or
   Role.PROJECT_OWNER in roles or Role.ORG_OWNER in roles`, mirroring `access.py:46-49` — at
   the two routes that already resolve roles: `list_chatrooms` (`chatrooms.py:194-198`,
   feeding `:208`) and `read_chatroom` (`:253-257`, feeding `:268`). For `patch_chatroom`
   (`:316`) the `access` object is already in hand at `:289`, so pass
   `access.is_moderator or principal.is_admin` directly. `create_chatroom` (`:236`) may pass
   the default; the frontend invalidates the list after create.
   **Fail closed for guests**: `read_chatroom` computes `pure_guest` at `:264` and `_to_out`
   already neutralises observer fields for that viewer (`:116-119`). `is_moderator` must be
   `False` on that path for the same reason — a guest is never a moderator, and the field
   must not become an oracle.
2. **Regenerate the client.** `pnpm run gen:api`, then `pnpm run check:openapi-drift`.
3. **Frontend.** Give `useChatroomMessages` an optional `isModerator: () => boolean`
   parameter — the exact shape already used for the project-id getter passed to
   `useChatroomAttachments` (`ChatroomView.vue:587-590`) — and add it to the two predicates:
   `canEdit` (`useChatroomMessages.ts:53-61`) gains it beside the `isAdmin` short-circuit,
   and `canDelete` (`:63-66`) likewise. The `_status` guards at `:55` and `:64` (optimistic
   messages have no server id to PATCH) stay first in both. Wire it at
   `ChatroomView.vue:552` from `roomQuery.data.value?.is_moderator`.

**Why this corrects rather than masks.** The masking fix is to have the frontend re-derive
the authorization rule from tenancy data. Q-2 shows that derivation is not merely
inconvenient but *incomplete* — it cannot express the `ORG_OWNER` half of `access.py:49`, so
it would ship a fix that silently continues to fail for one of the two intended roles. Moving
the predicate into the DTO puts it in the one place it is already computed correctly.

**Cross-slice note (eslint-plugin-boundaries).** This design deliberately introduces **no new
cross-slice import**. The alternative in Q-2 would have required the conversation slice to
consume `useProjectRole` from `@slices/tenancy` — which is a legitimate public surface
(`frontend/src/slices/tenancy/index.ts:9`, already consumed by conversation at
`WorkspaceSettingsView.vue:10` and by three other slices), so it would have been allowed, but
it would have deepened a dependency that the DTO removes the need for. Flagging it explicitly
because it is a public-surface decision either way, not an incidental one: **the chosen
design reduces the conversation slice's coupling to tenancy.**

### 7.3 Data repair

**F-8 can have silently reverted another user's saved changes, and the position is: no
retroactive repair is possible or attempted. The fix is forward-only.**

The reasoning, stated so it is not revisited: a laundered write is indistinguishable from a
legitimate one. It arrives as a well-formed PATCH carrying the correct current version from
an authorized user; nothing in the row, the version counter, or the request marks it. The
audit trail cannot close the gap either — `chatroom_service.py:164-175` emits
`chatroom.updated` with `metadata={"changed": list(values.keys())}` (`:172`) and **no
old/new values**, so even a full audit replay yields "A changed `name` at T" without what it
changed from. Compounding this, the F-7 rename-bleed means historic `changed` lists name
`name` on every toggle regardless of whether the name actually differed, so the audit
over-reports the affected population and cannot be used to bound it either.

Scope of possible damage is nonetheless small and self-limiting: five fields on one row per
room, overwritten only with values a legitimate collaborator had recently seen, recoverable
by any authorized user simply re-entering them. No message, key, or tenancy data is reachable
from this path. Users are not notified, because a notification could not name what was lost.

If a specific room is later reported as having lost a setting, the `chatroom.updated` audit
rows do identify **who** wrote **which fields** and **when**, which is enough to reconstruct
the sequence by hand with the participants. That is the available remedy, and it is
sufficient for a minor, low-cardinality, user-recoverable defect.

**Improving the audit to carry old/new values is out of scope** and recorded as **FU-4** —
it is a change to the audit contract, not to the defect.

## 8. Regression Test Plan

Failing test first, in every case. `/build` implements the tests below and confirms each one
red before touching the fix.

### 8.1 `frontend/src/slices/conversation/__tests__/useChatroomSettings.test.ts` (extend)

The file already exists and its `Host` harness (`:28-36`) plus `makeChatroom` factory
(`:10-26`) are reused as-is. Add a `describe('useChatroomSettings.setFlag')` block modelled
on the existing `saveDisclosure` block (`:38-112`).

- **T-1 — "reverts an access flag when the PATCH is rejected".** Seed a room with
  `allow_guest_links: false`; MSW returns 500 on `PATCH /api/chatrooms/:id` (the shape at
  `:42-47`). `await loadRoom()`, then `await setFlag('allow_guest_links', true)`. Assert
  `flags.allow_guest_links === false` and `saveError === 'conversation.settings.saveFailed'`.
  **Fails today**: `setFlag` is not on the composable at all — it lives in the view
  (`ChatroomSettingsView.vue:160-163`) — and the code path it calls,
  `useChatroomSettings.ts:109-111`, sets `saveError` without restoring anything, so the
  mirrored flag stays `true`.
- **T-2 — "resyncs every form field from the refetched room on a 409".** Seed the form via
  `loadRoom` against a GET returning `{ name: 'Room One', version: 1 }`; then re-stub the GET
  to `{ name: 'Renamed By B', version: 2 }` and the PATCH to 409 (the shape at `:67-76`).
  Toggle a flag. Assert **`name.value === 'Renamed By B'`** and `room.version === 2` and
  `saveError === 'conversation.settings.versionConflict'`. **Fails today** on the first
  assertion: `useChatroomSettings.ts:105` assigns `room.value` only, so `name.value` keeps
  the stale `'Room One'` — this assertion *is* F-8.
- **T-3 — "does not send `name` when only an access flag changed".** Capture the PATCH
  request body in the MSW handler. Toggle a flag. Assert `'name' in body === false` and that
  the body carries exactly the one toggled key. **Fails today**: `useChatroomSettings.ts:94-97`
  always spreads `name: name.value` and all four flags.
- **T-4 — "revalidates a cache-painted room instead of trusting the cache".** Seed the query
  cache with `['conversation','chatrooms','ws_1']` holding a room named `'Stale Name'` (the
  `seededClient` helper at `ChatroomSettingsView.test.ts:64-71` is the model); stub
  `GET /api/chatrooms/:id` to return `'Fresh Name'`. `await loadRoom()`, `await
  flushPromises()`. Assert `name.value === 'Fresh Name'`. **Fails today**:
  `useChatroomSettings.ts:68-73` returns from the cache branch and never issues the GET.
- **T-5 — "applies the new flag once the server confirms it"** (guard against
  over-correcting). Toggle succeeds; assert the flag reads the server's returned value and
  `saveError === null`. Mirrors the existing `:96-111`. **Passes today** — included so a fix
  that reverts unconditionally is caught.

### 8.2 `frontend/src/slices/conversation/__tests__/ChatroomSettingsView.test.ts` (extend)

- **T-6 — "the Guest Link card does not appear when enabling guest links was rejected".**
  Render the view with the seeded client (`:64-71`), stub the PATCH to 500, click the
  "Allow guest links" toggle, `flushPromises()`. Assert the guest-link card is absent.
  **Fails today**: the card is gated on the local mirror (`ChatroomSettingsView.vue:425`),
  which the rejected toggle left `true`, so a card and a `getGuestLink` fetch
  (`:209-218`) both appear for a room the server left closed. This is the user-visible half
  of F-7 and the reason it is filed as security-relevant.

### 8.3 `frontend/src/slices/conversation/__tests__/useChatroomMessages.test.ts` (extend)

The file already stubs a session with `is_admin: false` at `:107`, which is exactly the
precondition V-4 needs. Add a `describe('moderator affordances')` block.

- **T-7 — "a project owner sees Edit on another user's message beyond the 5-minute
  window".** Session `is_admin: false`; construct the composable with the moderator signal
  true; message authored by a different user with `created_at` ten minutes in the past.
  Assert `canEdit(m) === true`. **Fails today**: `useChatroomMessages.ts:53-61` returns false
  — `isAdmin` is false and `isOwnUserMessage` is false, so neither branch grants.
- **T-8 — "a project owner sees Delete on another user's message".** Same setup; assert
  `canDelete(m) === true`. **Fails today**: `:63-66` is `isAdmin || isOwnUserMessage`.
- **T-9 — "a plain member sees neither"** (over-grant guard). Moderator signal false, another
  user's aged message. Assert both false. **Passes today** — its purpose is to fail if the
  fix grants unconditionally.
- **T-10 — "an optimistic message is never editable, moderator or not".** Moderator signal
  true, message carrying `_status`. Assert both false. **Passes today** (`:55`, `:64`) —
  guards the guard ordering during the edit.

### 8.4 `backend/tests/unit/test_chatroom_moderator_dto.py` (new)

Modelled on `backend/tests/unit/test_observer_agents.py:143-159`, which already unit-tests
`chatrooms_mod._to_out` directly for the guest-neutralisation rule — the same seam.

- **T-11 — "`_to_out` reports `is_moderator` only when told to".** Assert
  `_to_out(room).is_moderator is False` (default) and
  `_to_out(room, is_moderator=True).is_moderator is True`. **Fails today**: the field and the
  parameter do not exist — a `TypeError`/`AttributeError`.
- **T-12 — "a pure guest is never a moderator".** `_to_out(room, is_moderator=True,
  viewer_is_pure_guest=True).is_moderator is False`. **Fails today**, same reason. Pins the
  fail-closed rule §7.2 requires, alongside the existing `observers_present` neutralisation
  at `test_observer_agents.py:151-154`.
- **T-13 — "the DTO predicate matches the enforcement predicate".** Table-driven over role
  sets: `{PROJECT_OWNER}` → true; `{ORG_OWNER}` → true; `{ORG_OWNER, ORG_MEMBER}` → true;
  `{PROJECT_MEMBER}` → false; `{ORG_MEMBER}` → false; `frozenset()` → false. Assert the
  route-level expression agrees with `RoomAccess.is_moderator` (`access.py:46-49`) on every
  row. **Fails today**: no such expression exists. This test is the one that would have
  caught the members-list approach — `{ORG_OWNER}` with no project membership row is
  precisely the case Q-2 rules out.

`backend/tests/unit/test_conversation_services.py:671-676` already asserts the moderator
**edit path** works at the service layer and needs no change; it is the evidence that the
backend half of `[R13.23]` was never the defect.

## 9. Risks and Rollback

- **Risk: the projection breaks a toggle that depended on optimistic feedback.** Removing the
  mirror means a toggle does not move until the PATCH returns. Mitigated by the existing
  `:disabled="saving"` on every toggle (`ChatroomSettingsView.vue:337,356,372,388`) and
  `saving` being set synchronously before the first `await` (`useChatroomSettings.ts:85`), so
  the control is visibly busy for the round-trip rather than silently unresponsive.
  If the latency reads badly, fall back to Exemplar A (`saveDisclosure`'s explicit revert),
  which is a strictly smaller change and still closes F-7.
- **Risk: `loadRoom` revalidation clobbers an in-progress rename.** Guarded in §7.1 step 5 by
  skipping the late `applyRoom` of `name` while `nameDirty` (`ChatroomSettingsView.vue:143-145`)
  is true. T-4 must not be written in a way that permits the unguarded version to pass.
- **Risk: the per-flag patch changes server-side auto-correct behaviour.** Previously every
  toggle sent all four flags; now it sends one. `chatroom_service.patch`
  (`chatroom_service.py:141-158`) builds `values` from non-`None` fields only, so a one-key
  patch is already a supported shape — this is how `saveDisclosure` has always worked
  (`useChatroomSettings.ts:133-135`). Verified against the existing single-field tests at
  `test_observer_agents.py:313-359` (`disclosure_only_patch`, `name_only_patch`).
- **Risk: the DTO change breaks generated-client drift checks.** Mitigated by running
  `pnpm run gen:api` and `pnpm run check:openapi-drift` in the same commit as the backend
  change. If the two land in separate commits the drift check fails CI — keep them together.
- **Risk: over-granting moderation.** The DTO is additive and the backend gate is unchanged
  (`messages.py:472`, `message_service.py:258`), so a wrong `is_moderator` can only produce a
  button that 403s — never an unauthorized mutation. T-9, T-12 and T-13 pin the negative
  cases.
- **Rollback** — three independent revert points. The frontend settings change
  (§7.1) is contained to two files in one slice. The V-4 frontend change (§7.2 step 3) is two
  predicates plus one argument and reverts to the `is_admin`-only behaviour. The DTO addition
  (§7.2 steps 1-2) is additive: an older frontend ignores the extra field, so the backend may
  ship first and be left in place even if the frontend half is reverted.

## 10. Acceptance Criteria

- [ ] **AC-1** — Every test named in §8 (T-1 … T-13) fails against current `main` for the
      stated reason and passes after the fix.
- [ ] **AC-2** — A rejected access-flag PATCH leaves the toggle in the server's position,
      fires a `$t()`-sourced error toast, and does not render the dependent Guest Link card
      (T-1, T-6).
- [ ] **AC-3** — After a 409 on any settings save, every form field — `name` included — shows
      the server's current values, and `room.version` matches (T-2).
- [ ] **AC-4** — Toggling an access flag sends a body containing only that flag: no `name`,
      no untouched flags (T-3).
- [ ] **AC-5** — `loadRoom` revalidates against `GET /api/chatrooms/{id}` even on a cache hit,
      and a rename made by another user appears without a manual reload (T-4).
- [ ] **AC-6** — `ChatroomOut` carries `is_moderator`, computed identically to
      `RoomAccess.is_moderator` (`access.py:46-49`) plus the admin bypass, and `False` for a
      pure guest (T-11, T-12, T-13).
- [ ] **AC-7** — A non-admin project owner **and** a non-admin org owner with no
      `project_members` row each see Edit and Delete on another user's message; a plain
      project member sees neither (T-7, T-8, T-9, T-13).
- [ ] **AC-8** — No new cross-slice import is introduced in the conversation slice;
      `pnpm lint` passes all 12 boundary gates.
- [ ] **AC-9** — `pnpm run gen:api` has been re-run and `pnpm run check:openapi-drift` passes
      in the same commit as the backend DTO change.
- [ ] **AC-10** — No hardcoded user-facing string; any new i18n key exists in both
      `frontend/src/slices/conversation/locales/en.json` and its `zh-TW.json` counterpart.
- [ ] **AC-11** — Full Definition of Done: `pytest -q`, `ruff check . && ruff format --check .`,
      `mypy .`, `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build`.
- [ ] **AC-12** — The correction recorded in Q-1 is written back to
      `docs/audits/2026-07-22-agent-to-user-conversation/findings.md:83-85`: F-8 is **not**
      contingent on F-1; that coupling applies to F-11 and F-13 only.

## 11. SRS Delta

**None for behaviour.** All three defects restore documented intent: `[R13.23]`/`[R13.21]`
(`REQUIREMENTS.md:695,697`) and `docs/UI/07-conversation.md:377-379` for V-4;
`docs/UI/07-conversation.md:1157-1158` for F-7; `docs/UI/12-shared-patterns.md:305-312` for
F-8. The backend already implements the requirement in every case.

**One documentation correction** (not an SRS change), carried as AC-12: the a2u audit's §3
coupling note. See Q-1.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — No e2e coverage for a non-admin project owner's moderation affordances.
  `frontend/e2e/fixtures/auth.ts:85-90` provides only `authedPage` and `adminPage`, so
  `13-message-edit-delete.spec.ts:78-96` can cover only the admin half — the exact one-sided
  coverage V-4 records. Add an owner fixture and seeded owner user, then extend that spec.
  Deferred per Q-5: a seed change, not a behaviour change.
- **FU-2** — `saveWakeupConfig` (`useChatroomBindings.ts:149-174`) leaves the editor showing
  an unsaved value after a failed save, by an explicit decision documented at `:140-145`.
  Cleared in §6 as deliberate, but it is the one remaining site in this slice where the UI
  can display something the server rejected. Worth revisiting with a per-field dirty
  indicator rather than a revert.
- **FU-3** — `ChatroomSettingsView.vue:97-111`'s `isCreator` fallback carries the same
  org-owner blind spot as the rejected V-4 approach (`role_resolver.py:41-48` grants
  `PROJECT_OWNER` with no `project_members` row; `projects.py:264-288` returns only rows that
  exist). It fails closed and gates a different predicate (R28.02), so it is not a defect
  here — but once `ChatroomOut` carries a role signal, that lookup and its
  `projectsApi.listMembers` cross-slice reach could be retired.
- **FU-4** — `chatroom.updated` audits record only `metadata={"changed": [...]}`
  (`chatroom_service.py:172`), with no old/new values. This is why F-8's damage is not
  reconstructible (§7.3). Capturing before/after for settings changes would make this class
  of defect auditable in future; it is an audit-contract change, out of scope here.
- **FU-5** — `docs/UI/07-conversation.md:1157-1158` specifies a toast on toggle failure while
  the implementation surfaces an inline `SAlert` in a different card. This dossier satisfies
  both. If the inline alert is retained long-term, the shared-patterns doc should say which
  surface is canonical for an immediate-save control, since
  `docs/UI/12-shared-patterns.md:301` prescribes a toast for `conflict` and the two documents
  are not obviously reconciled.
</content>
