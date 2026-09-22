---
type: feature
status: in-progress
created: 2026-09-22
requirements: [R11.10]
depends_on: []
---

# Sidebar redesign: frequency-layered navigation

## 1. Summary

Restructure the left sidebar from a category-based layout (6 collapsible groups, 17 nav
items, chatrooms at position #10) to a frequency-layered layout (1 collapsible group,
chat-first, 3 tiers). Adds a "New Chat" CTA, moves chatrooms to the top, renames
Knowledge items from implementation terminology to user-task language, merges Project
Keys + Key Groups into a single entry with internal tabs, moves My Keys and
Notifications out of the sidebar, and collapses all infrequent settings into one
"Project Settings" group. Frontend-only; no backend, API, or migration changes.

## 2. Goals and Non-goals

**Goals**

- Reorder sidebar to place the core loop (chat, agents) at the top and infrequent
  settings at the bottom, following frequency + context (global vs project) as dual axes.
- Add a prominent "New Chat" button immediately below the Org/Project Switcher as the
  primary call-to-action.
- Move Recent Chatrooms from position #10 to position #2 (right after the CTA).
- Move Workspaces up from Infrastructure into the project core area (it is a
  conversation organizer, not infrastructure).
- Rename Knowledge items: RAG Configs -> Documents, Concept Maps -> Chat Graph,
  Knowledge Maps -> Doc Graph.
- Merge Project Keys + Key Groups sidebar entries into a single "Keys" entry; the
  destination view uses tabs to switch between them.
- Move Search Keys into the unified "Project Settings" collapsible group.
- Move MCP Allowlist from the deleted Infrastructure group into "Project Settings" under
  the Manage sub-section.
- Move My Keys from the sidebar "Personal" group into UserMenu.
- Remove the sidebar "Notifications" entry (NotificationBell in the top bar already
  navigates to `/notifications`).
- Reduce collapsible groups from 4-5 to 1 ("Project Settings").
- Fix existing tech debt: duplicated CSS between `SidebarChatroomList` and
  `SidebarNavItem`, inconsistent active indicators, `<a href="#">` instead of
  `<RouterLink>`, missing `aria-label` on `<nav>`.
- Update en.json and zh-TW.json i18n keys.

**Non-goals**

- **No backend changes.** Routes, API endpoints, auth, and data models are untouched.
- **No Keys page merge.** The combined Project Keys + Key Groups view is a separate
  follow-up; this dossier only changes the sidebar entry to point to the existing
  ProjectKeysView, which already has STabs (`ProjectKeysView.vue`). The Key Groups tab
  integration into that view is FU-1.
- **No chatroom creation flow redesign.** The "New Chat" button opens the existing
  creation modal (extracted from `ChatroomListView`); it does not introduce a
  quick-create or simplified flow.
- **No dynamic/adaptive sidebar ordering.** Static frequency-based layout only; no
  analytics-driven reordering.
- **No route changes.** All existing URLs remain valid; only sidebar nav entries and
  their i18n labels change.
- **No removal of the "Personal" group concept.** Invites remains in the global section.
  Organizations and Projects remain as sidebar entries in the global section.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Should chatrooms be at the top, bottom, or in a fixed (non-scrolling) panel? | Top, scrolling with the rest of the sidebar | User chose this option. Fixed panel adds implementation complexity for marginal benefit; mobile drawer would not benefit. |
| Q-2 | Should all 4 key entries merge into one, partially merge, or stay separate? | Partial: Project Keys + Key Groups merge; Search Keys stays separate; My Keys moves to UserMenu | Personal keys (user-level, `/keys`) and project keys (project-level) have different permission models. Search Keys use different provider ecosystems (Brave/Tavily vs Claude/OpenAI). |
| Q-3 | Should Knowledge items be renamed only, merged into one entry, or both? | Rename only | The three features are fundamentally different knowledge-augmentation strategies (vector retrieval, conversation graph extraction, document graph extraction) with different CRUD flows and state models. Merging adds clicks without reducing complexity. |
| Q-4 | Should the sidebar use 3 groups, preserve current groups with reordering, or use 2 groups (core + all settings)? | Three-layer model: project core (flat) / project settings (1 collapsible) / global (flat) | Reduces collapsible groups from 4-5 to 1, preserves the project vs global boundary, and keeps high-frequency items always visible. |
| Q-5 | What should the "New Chat" button do? | Open the chatroom creation modal (extracted from ChatroomListView) | The existing modal already handles workspace selection, name, and access flags. Extracting it as a standalone component is lower risk than designing a new quick-create flow. |
| Q-6 | Should the default chatroom list show 5 or 10 items? | 5 items + "Show more" link | 10 items x ~40px = 400px, which on small screens pushes agents and settings too far down. 5 items balances visibility with vertical space. "Show more" navigates to the full chatroom list. |
| Q-7 | Where should My Keys go after leaving the sidebar? | New entry in UserMenu, between "Sessions" and "Prompt Assistant" | UserMenu already hosts personal items (Profile, Account, Sessions). My Keys is a personal setting used infrequently after initial setup. |

## 4. Current State

The sidebar (`AppSidebar.vue:117-252`) renders 9 structural blocks top-to-bottom:

1. `OrgProjectSwitcher` (desktop only, `AppSidebar.vue:126-130`)
2. Flat workspace section: Orgs + Projects (`AppSidebar.vue:133-142`)
3. `SidebarGroup` "Personal": My Keys, Notifications, Invites (`AppSidebar.vue:145-156`)
4. Project context divider + "PROJECT" section header (`AppSidebar.vue:160-164`)
5. Flat agents section: AI Agents, Agent Groups (`AppSidebar.vue:167-175`)
6. `SidebarGroup` "Knowledge": RAG Configs, Concept Maps, Knowledge Maps
   (`AppSidebar.vue:178-189`)
7. `SidebarGroup` "Keys": Project Keys, Key Groups, Search Keys
   (`AppSidebar.vue:192-203`)
8. `SidebarGroup` "Infrastructure" (default collapsed): Workspaces, MCP Allowlist
   (`AppSidebar.vue:206-218`)
9. `SidebarGroup` "Manage" (admin-gated): Members, Skills, Activity Types
   (`AppSidebar.vue:221-233`)
10. Divider + `SidebarChatroomList` (`AppSidebar.vue:236-237`)
11. Admin Console (platform admin, `AppSidebar.vue:241-250`)

Total: 4-5 collapsible groups, 17 nav items, up to 10 chatroom entries.

Key technical debt identified:

- `SidebarChatroomList.vue` duplicates `.section-header`, `.nav-item`, `.nav-icon`,
  `.nav-label` CSS from `SidebarNavItem.vue` and `AppSidebar.vue`
  (`SidebarChatroomList.vue:79-124`).
- Chatroom active indicator uses `border-left: 3px solid`
  (`SidebarChatroomList.vue:109-111`) while `SidebarNavItem` uses `::before`
  pseudo-element with `scaleY` animation (`SidebarNavItem.vue:62-73`).
- Chatroom items use `<a href="#" @click.prevent>` (`SidebarChatroomList.vue:54-59`)
  instead of `<RouterLink>`.
- `<nav>` lacks `aria-label` (`AppSidebar.vue:122`).
- Unused i18n key `app.sidebar.graphrag` exists in both locale files.

## 5. Design

### Options considered

**Option A -- Frequency-layered with single collapsible group (chosen)**

Three visual tiers: project core (always visible), project settings (one collapsible
group), global (always visible at bottom). Chatrooms and New Chat at the top.
Sub-sections within "Project Settings" use non-collapsible visual labels (the existing
`.section-header` pattern) to maintain scannability without nested collapse.

Trade-offs: Optimal for daily use; settings require one expand action; sub-sections
within the collapsed group may feel long (11 items including admin-gated ones).

**Option B -- Keep current groups, reorder by frequency**

Maintain separate Knowledge/Keys/Infrastructure/Manage groups but move chatrooms to top
and reorder groups by frequency.

Trade-offs: Preserves existing spatial memory; still 4-5 collapsible groups; cognitive
load unchanged.

**Option C -- Two-tier (core + everything else collapsed)**

Only chatrooms + agents visible; everything else in a single "Settings" group.

Trade-offs: Most minimal sidebar; hides Knowledge items that are used moderately often;
too aggressive.

### Decision

Option A. The three-tier model respects both frequency and the global-vs-project context
boundary. A single collapsible group for all settings is the best balance: users who
need settings expand one group; users in the core loop never scroll past chat and agents.
Sub-section labels within the group prevent it from feeling like an undifferentiated
list.

## 6. Detailed Changes

### Frontend

**`AppSidebar.vue`** -- Major template restructure. New computed nav arrays:

| New computed | Items | Source |
|---|---|---|
| `globalNav` | Organizations, Projects, Invites | From current `workspaceNav` + part of `personalNav` |
| `agentNav` (unchanged) | AI Agents, Agent Groups | Same |
| `knowledgeSettingsNav` | Documents, Chat Graph, Doc Graph | Renamed from `knowledgeNav` |
| `keysSettingsNav` | Keys, Search Keys | Merged from `projectKeysNav` (drop Key Groups entry) |
| `manageSettingsNav` | Members, Skills, Activity Types, MCP Allowlist | From `manageNav` + `infraNav` (MCP Allowlist) |

Remove: `workspaceNav`, `personalNav`, `projectKeysNav`, `infraNav`.

Template order (top to bottom):
1. `OrgProjectSwitcher` (unchanged)
2. New Chat `SButton` (variant=primary, size=sm, full-width, `PlusIcon` + label)
3. `SidebarChatroomList` (moved from bottom; limit reduced to 5 + "Show more" link)
4. Workspaces `SidebarNavItem` (moved from Infrastructure)
5. Divider
6. `agentNav` items (flat, unchanged)
7. Divider
8. `SidebarGroup` "Project Settings" (storage-key: `project-settings`):
   - `.section-header` "Knowledge"
   - `knowledgeSettingsNav` items
   - `.section-header` "Keys"
   - `keysSettingsNav` items
   - `.section-header` "Manage" (admin-gated via `v-if="decided && isAuthorized"`)
   - `manageSettingsNav` items (admin-gated)
9. Divider
10. `globalNav` items (flat)
11. Admin Console (unchanged)

All items above the "Project Settings" group and below the global divider render as
flat `sidebar__section` with `SidebarNavItem`. No `SidebarGroup` except "Project
Settings".

**New component: `ChatroomCreateModal.vue`**

Extract from `ChatroomListView.vue:182-223,358-432`. Contains:
- The `showCreate`/`createName`/`createFlags` state
- The `createMutation` (calls `createChatroom` from `@slices/conversation`)
- The `SModal` template with name input and access-flag toggles

Exposed via the conversation slice barrel export (`@slices/conversation/index.ts`).

**New composable: `useChatroomCreate`**

Extract from `ChatroomListView.vue:182-223`. Returns `{ showCreate, openCreate,
submitCreate, isCreating }`. Used by both `ChatroomCreateModal` and the trimmed
`ChatroomListView`. Exported from `@slices/conversation`.

**`SidebarChatroomList.vue`** -- Refactor:
- Replace duplicated `.nav-item` CSS with `SidebarNavItem` component usage (or shared
  CSS class import).
- Replace `<a href="#">` with `<RouterLink>`.
- Unify active indicator to match `SidebarNavItem`'s `::before` pseudo-element.
- Change `limit` from 10 to 5.
- Add a "Show more" link at the bottom (navigates to chatroom list view).
- Remove JS truncation (CSS ellipsis already handles overflow via `.nav-label`).

**`UserMenu.vue`** -- Add "My Keys" entry:
- New menu item between "Sessions" and "Prompt Assistant" (after line ~83).
- Icon: `KeyIcon` from `@heroicons/vue/24/outline`.
- Route: `{ name: 'keys.list' }` (i.e., `/keys`).
- New i18n key: `app.userMenu.myKeys`.

**`ChatroomListView.vue`** -- Slim down:
- Replace inline create logic (lines 182-223) and modal template (lines 358-432) with
  `useChatroomCreate` composable + `ChatroomCreateModal` component.
- The two "Create" buttons (`SPageHeader` action + empty-state) call
  `openCreate()` from the composable.

**i18n changes** (`en.json`, `zh-TW.json`):

| Action | Key | EN | zh-TW |
|---|---|---|---|
| Rename | `app.sidebar.ragConfigs` | Documents | 文件庫 |
| Rename | `app.sidebar.conceptMaps` | Chat Graph | 對話圖譜 |
| Rename | `app.sidebar.knowledgeMaps` | Doc Graph | 文件圖譜 |
| Rename | `app.sidebar.projectKeys` | Keys | 金鑰 |
| Rename | `app.sidebar.groupManage` | Manage | 管理 |
| Add | `app.sidebar.newChat` | New Chat | 新對話 |
| Add | `app.sidebar.showMore` | Show more | 顯示更多 |
| Add | `app.sidebar.groupProjectSettings` | Project Settings | 專案設定 |
| Add | `app.sidebar.sectionKnowledge` | Knowledge | 知識庫 |
| Add | `app.sidebar.sectionKeys` | Keys | 金鑰 |
| Add | `app.sidebar.sectionManage` | Manage | 管理 |
| Add | `app.userMenu.myKeys` | My Keys | 我的金鑰 |
| Remove | `app.sidebar.keys` | _(was "My Keys")_ | -- |
| Remove | `app.sidebar.notifications` | _(was "Notifications")_ | -- |
| Remove | `app.sidebar.keyGroups` | _(was "Key Groups")_ | -- |
| Remove | `app.sidebar.groupPersonal` | _(was "Personal")_ | -- |
| Remove | `app.sidebar.groupKnowledge` | _(was "Knowledge")_ | -- |
| Remove | `app.sidebar.groupKeys` | _(was "Keys")_ | -- |
| Remove | `app.sidebar.groupInfra` | _(was "Infrastructure")_ | -- |
| Remove | `app.sidebar.graphrag` | _(unused dead key)_ | -- |

**No backend, API, migration, or deploy changes.**

## 7. NFR Checklist

- [x] i18n -- all new strings through `$t()`. Existing keys renamed/added/removed per
  table above. No hardcoded text.
- [ ] Audit log -- N/A. No domain events; sidebar navigation is client-side only.
- [ ] Tenant isolation -- N/A. No new endpoints. Existing project-scoped gating
  (`workspace.hasProject`, `useProjectRole`) preserved unchanged.
- [x] Error handling UX -- Chatroom list already handles loading/error/empty states
  (`SidebarChatroomList.vue:36-52`). "New Chat" button disables when no project is
  selected (same `workspace.hasProject` guard). No-project fallback: the entire
  project-scoped section is already gated by `v-if="workspace.hasProject"`.
- [ ] Performance -- Chatroom list query unchanged (already limits to N items via
  `useRecentChatrooms`). Reducing from 10 to 5 reduces DOM nodes. No new API calls.

## 8. Security Considerations

None -- no sensitive surface touched. The chatroom creation modal extraction moves
existing code without changing auth, input validation, or API calls. The
`createChatroom` mutation already validates through `chatroomCreateSchema` (Zod) at
`ChatroomListView.vue:215-217` before calling the API. The `useProjectRole` admin gate
for the Manage sub-section is preserved.

## 9. Quality Notes

**Existing debt in touched files**

- Duplicated CSS between `SidebarChatroomList.vue:79-124` and
  `SidebarNavItem.vue:43-111` / `AppSidebar.vue:304-311`. This task fixes it (AC-8).
- `<a href="#">` instead of `<RouterLink>` in `SidebarChatroomList.vue:54-59`. This task
  fixes it (AC-8).
- Inconsistent active indicator styles. This task unifies them (AC-8).
- Missing `aria-label` on `<nav>` (`AppSidebar.vue:122`). This task adds it (AC-9).
- Dead i18n key `app.sidebar.graphrag`. This task removes it.

**Patterns to follow**

- `SidebarNavItem` for all nav links (icon + label + route + active state).
- `SidebarGroup` for the single collapsible "Project Settings" group.
- `.section-header` CSS class (already defined in `AppSidebar.vue:304-311`) for
  non-collapsible sub-section labels within the group.
- `sidebar__divider` for visual separation between tiers.
- Computed `NavItem[]` arrays that return `[]` when the project context is absent,
  naturally hiding project-scoped sections.
- `useProjectRole` gating pattern with `decided && isAuthorized` to prevent flash
  (`AppSidebar.vue:40-41`).

**Reuse inventory**

| Existing | Use for |
|---|---|
| `SidebarNavItem` | All nav entries including chatroom items (replacing duplicated `<a>` pattern) |
| `SidebarGroup` | The single "Project Settings" collapsible group |
| `SButton` (`@shared/ui`) | "New Chat" CTA (variant=primary, size=sm, icon-left slot) |
| `useWorkspaceStore` (`@shared/stores/workspace`) | Project context (`projectId`, `hasProject`) |
| `useRecentChatrooms` (`@slices/conversation`) | Chatroom list data (already used) |
| `createChatroom` (`@slices/conversation/api`) | Chatroom creation API call |
| `chatroomCreateSchema` (`@slices/conversation/types/schemas`) | Validation for create form |
| `useProjectRole` (`@slices/tenancy`) | Admin/owner gating for Manage sub-section |
| `SModal`, `SInput`, `SToggle`, `SFormField` (`@shared/ui`) | Chatroom creation modal |
| `PlusIcon`, `ChatBubbleLeftIcon` (`@heroicons/vue/24/outline`) | New Chat button, chatroom items |

## 10. Risks and Rollback

1. **Spatial memory disruption.** Existing users have muscle memory for current sidebar
   positions. Mitigated: product is early-stage with small user base; the new layout is
   objectively more aligned with usage patterns. No migration needed.

2. **"Project Settings" group is long.** With admin-gated items included, the group
   holds up to 11 entries (3 knowledge + 2 keys + 4 manage + MCP Allowlist +
   Activity Types). Mitigated: sub-section labels provide visual structure; admin items
   hidden for non-admin users reduces to 7; the group defaults expanded so users see
   the contents immediately.

3. **New Chat modal extraction.** Moving inline state and template to a composable +
   component could introduce regressions in the original `ChatroomListView` create flow.
   Mitigated: both locations use the same composable; the modal is a direct extraction
   with no logic changes.

4. **No-project empty state.** When no project is selected, the top section (New Chat,
   chatrooms, agents) is entirely hidden behind `v-if="workspace.hasProject"`. The
   sidebar shows only the global section (Orgs, Projects, Invites). This is already the
   current behavior for the project-context block -- the only difference is the global
   items are now at the bottom instead of the top, which might feel sparse. Mitigated:
   the Org/Project Switcher remains at the very top, prompting project selection.

Rollback: `git revert` per commit. No data, schema, or API changes, so rollback is
purely a frontend deploy.

## 11. Acceptance Criteria

- [ ] AC-1: A "New Chat" button (`SButton`, primary variant) is visible in the sidebar
  immediately below the Org/Project Switcher when a project is selected. Clicking it
  opens a chatroom creation modal. Creating a chatroom navigates to the new room.
- [ ] AC-2: Recent Chatrooms list appears immediately below the New Chat button,
  showing at most 5 items. A "Show more" link below the list navigates to the chatroom
  list view.
- [ ] AC-3: Workspaces nav item appears below the chatroom list, above the first divider.
- [ ] AC-4: AI Agents and Agent Groups appear as flat items between the two dividers,
  in the same relative order.
- [ ] AC-5: A single collapsible "Project Settings" group replaces the former Knowledge,
  Keys, Infrastructure, and Manage groups. It contains three sub-sections with
  non-collapsible visual labels: "Knowledge" (Documents, Chat Graph, Doc Graph), "Keys"
  (Keys, Search Keys), and "Manage" (Members, Skills, Activity Types, MCP Allowlist).
  The Manage sub-section is visible only to project owners and platform admins.
- [ ] AC-6: Below the "Project Settings" group, a global section shows Organizations,
  Projects, and Invites as flat items.
- [ ] AC-7: My Keys is accessible from the UserMenu dropdown (between Sessions and
  Prompt Assistant). It is not in the sidebar. Notifications is not in the sidebar (the
  top-bar NotificationBell remains).
- [ ] AC-8: `SidebarChatroomList` uses `RouterLink` (not `<a href="#">`) and shares the
  same active-indicator style (`::before` pseudo-element) as `SidebarNavItem`. No
  duplicated `.nav-item` / `.section-header` CSS between the two components.
- [ ] AC-9: The sidebar `<nav>` element has `aria-label="Main navigation"` (en) /
  appropriate i18n equivalent.
- [ ] AC-10: All i18n keys are updated per the table in section 6. No hardcoded strings.
  The dead `app.sidebar.graphrag` key is removed.
- [ ] AC-11: The sidebar collapse/expand behavior on desktop and mobile is unchanged
  (auto-collapse on chatroom routes, manual toggle, `SDrawer` on mobile).
- [ ] AC-12: Existing tests in `AppSidebar.test.ts` are updated to reflect the new
  structure and pass. The Manage group admin-gate test is preserved.

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-1 | Component test + manual (`run` skill) | `app/__tests__/AppSidebar.test.ts` (new test: render New Chat when project selected; click opens modal) |
| AC-2 | Component test | `app/__tests__/SidebarChatroomList.test.ts` (assert max 5 items + "Show more" link) |
| AC-3-6 | Component test | `app/__tests__/AppSidebar.test.ts` (snapshot or structural assertions for nav item order and grouping) |
| AC-7 | Component test | `app/__tests__/UserMenu.test.ts` (assert My Keys link present) |
| AC-8 | Code review + visual inspection | No duplicated CSS; `RouterLink` usage verifiable by grep |
| AC-9 | Component test | `app/__tests__/AppSidebar.test.ts` (assert `aria-label` on `<nav>`) |
| AC-10 | Lint (`vue-i18n` unused key detection if available) + manual | Verify both locale files have matching key sets |
| AC-11 | Existing e2e tests + manual | Verify auto-collapse on chatroom routes still works |
| AC-12 | CI: `pnpm test` | Existing test suite updated and green |

## 13. SRS Delta

None. This feature restructures sidebar navigation ordering and naming; it does not
introduce new system behavior that requires a requirements amendment. The existing
[R11.10] sidebar/project-nav requirement is unaffected (the Manage group and its
admin gate are preserved).

## 14. Open Questions

- OQ-1: Should the "Project Settings" group default to expanded or collapsed? Expanded
  is recommended for discoverability, but collapsed would make the sidebar shorter. Not
  blocking -- can be adjusted post-launch via the `defaultCollapsed` prop.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- FU-1: **Merge Project Keys + Key Groups into a unified tabbed view.** This dossier
  changes the sidebar entry only; the Keys route still points to `ProjectKeysView`.
  The combined view would add a "Key Groups" tab pane to `ProjectKeysView`'s existing
  `STabs` (which already has Carried/Available tabs). This is a separate task because it
  restructures the keys slice's view layer, not just the sidebar.
- FU-2: **NotificationBell dropdown.** The bell currently only links to `/notifications`.
  Adding a dropdown with recent notifications would make the sidebar entry removal
  feel less like a regression for users who relied on the sidebar path.
- FU-3: **Account Settings hub.** UserMenu items (Profile, Password, Sessions, My Keys,
  Prompt Assistant) could be consolidated into a single Account Settings page with
  sidebar tabs, reducing the number of top-level menu items.
- FU-4: **Chatroom list "Show more" behavior.** Consider whether "Show more" should
  expand the list inline (load more items) or navigate to the full-page chatroom list.
  Current spec navigates; inline expansion may be better UX.
