---
type: feature
status: implemented
created: 2026-07-23
requirements: [R11.10]
depends_on: []
---

# Sidebar project management nav + in-sidebar project switcher

## 1. Summary

The three owner-only project management surfaces — Members, Skills, and Activity types —
are reachable only from buttons in the `ProjectDetailView` header
(`frontend/src/slices/tenancy/views/ProjectDetailView.vue:152-186`). Every *other*
project-scoped surface (agents, knowledge, keys, infrastructure, chatrooms) already lives
in the `AppSidebar`'s "Project Context" block as collapsible groups
(`frontend/src/app/components/AppSidebar.vue:137-226`), so those three are IA orphans and
were hard to find (the trigger for this task). This feature adds a role-gated collapsible
"Manage" group to the sidebar's Project Context holding Members / Skills / Activity types,
and relocates the existing `OrgProjectSwitcher` from the top bar into the top of the
sidebar on desktop (kept in the top bar on mobile, where the sidebar is a drawer). The
`ProjectDetailView` header buttons stay as a second entry point. Frontend-only, additive.

## 2. Goals and Non-goals

**Goals**
- A collapsible "Manage" `SidebarGroup` inside the sidebar's Project Context, listing
  Members, Skills, and Activity types, using the established sidebar nav idiom.
- The Manage group is shown only to a project Owner or a platform Admin; a non-owner
  member does not see it at all (whole group hidden).
- The `OrgProjectSwitcher` is presented at the top of the sidebar on desktop so project
  switching lives with the project nav; the same single `useWorkspaceStore`-backed
  component drives selection (no second switcher, no duplicate state).
- On mobile the switcher stays in the top bar (the sidebar is a drawer that is hidden by
  default), so quick switching does not require opening the drawer.
- The `ProjectDetailView` header buttons for Members/Skills/Activities are retained.
- New sidebar label string in en + zh-TW.

**Non-goals**
- **Role-gating the other sidebar project items.** Agents, knowledge, keys, and infra are
  shown today to any project member regardless of role
  (`AppSidebar.vue:137-226`, no role check); this task does not add gating to them. The
  new Manage group is the first role-gated sidebar group; the asymmetry is recorded, not
  resolved here (FU-1).
- **Removing or restyling the `ProjectDetailView` header buttons** (Q-2: keep them).
- **Adding Rename / Delete project to the sidebar.** Those are dialog actions, not routes
  (`ProjectDetailView.vue:140-149,176-198`); the sidebar lists navigable pages only.
- **Any backend, API, migration, or auth change.** The sidebar gate is a UX affordance;
  the pages and their backend routes remain the authoritative authorization boundary.
- Changing the switcher's own internals (org/personal/project selection, create actions).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Where does the project switcher live? | Move `OrgProjectSwitcher` to the **top of `AppSidebar`** on desktop; **keep it in the top bar on mobile**. Reuse the one component. | User chose (b): switching should sit with the project nav. A second independent switcher would split the source of truth; both instances drive the same `useWorkspaceStore`. Mobile keeps the top-bar switcher because the sidebar is an `SDrawer` hidden by default (`AppShell.vue:113-121`) — a sidebar-only switcher there would force a drawer-open just to switch. |
| Q-2 | Keep the `ProjectDetailView` header buttons? | **Keep** all three. | User chose redundancy for discoverability; the sidebar is additive, lowering the risk of the change (no existing entry point is removed). |
| Q-3 | Group label + membership? | New collapsible group labeled **"Manage / 管理"**, holding Members, Skills, Activity types. Rename/Delete excluded (actions, not routes). | Delegated to the implementer. "Manage" spans access control (Members) + capabilities (Skills, Activities) better than "Settings", which does not fit Members. Matches the noun-category labels of the existing groups (Knowledge, Keys, Infrastructure). |
| Q-4 | Non-owner behavior? | **Hide the entire group** for non-owners. Gate on `useProjectRole(workspace.projectId)` `decided && isAuthorized` (Admin OR Owner). | Consistent with the `ActivityTypesView` page gate and the header-button `isOwner` gate. `decided` prevents a flash-then-hide of an owner control (R11.10). |

## 4. Current State

**Sidebar composition.** `AppSidebar.vue` renders, in order: a Workspace section (Orgs,
Projects list links, `:40-42,100-115`), a collapsible Personal group (`:44-48,117-135`),
and — only when `workspace.hasProject` (`:138`) — a "Project Context" block with an
Agents section (`:145-160`) and three collapsible `SidebarGroup`s: Knowledge
(`:162-180`), Keys (`:182-200`), Infrastructure (default-collapsed, `:202-221`), then the
recent-chatrooms list (`:223-225`). Admin section is role-gated via
`session.me?.is_admin` (`:229`). Project-scoped items are built as `NavItem` arrays keyed
off `workspace.projectId` using **path-string** routes, e.g.
`/projects/${pid}/agents` (`:50-86`) — not named routes.

**The three orphans.** Members, Skills, Activity types appear nowhere in `AppSidebar`.
They exist only as header buttons in `ProjectDetailView.vue:152-186`, each gated
`v-if="isOwner"` where `isOwner = myMembership.value?.role === 'owner'`
(`ProjectDetailView.vue:46`). Their routes:
- Members → path `/projects/:id/members`, name `tenancy.projectMembers`
  (`tenancy/routes.ts:40-45`) — note the param is `id`, not `projectId`.
- Skills → path `/projects/:projectId/skills`, name `skills.project`
  (`skills/routes.ts:6-11`).
- Activity types → path `/projects/:projectId/activity-types`, name `activities.types`
  (`activities/routes.ts:7-13`).
Using path strings in the sidebar (as the other items do) sidesteps the `id`/`projectId`
param-name difference.

**`SidebarGroup`** is a ready collapsible group: props `label`, `storageKey`,
`defaultCollapsed`, with per-key persistence in `localStorage`
(`SidebarGroup.vue:5-33`).

**`OrgProjectSwitcher`** is a self-contained org+project dropdown driving
`useWorkspaceStore.selectProject` (`OrgProjectSwitcher.vue:79-82`); it accepts a `compact`
prop (`:15-17`). It is currently mounted once, in the top bar's center zone, as
`<OrgProjectSwitcher :compact="isMobile" />` (`AppTopBar.vue:51-53`).

**Shell layout.** On desktop the sidebar is a persistent `<aside>` in a grid track that
tweens on collapse and auto-collapses on immersive routes (chatrooms, workflow edit)
(`AppShell.vue:18-33,104-111`). On mobile (`!isDesktop`) it is an `SDrawer`, opened by the
top-bar toggle (`AppShell.vue:113-121`, `AppTopBar.vue:26-40`).

**Owner gating composable.** `useProjectRole(projectId)` → `{ isAdmin, isOwner,
isAuthorized, decided }`, `isAuthorized = isAdmin || isOwner`, exported from
`@slices/tenancy` (`tenancy/composables/useProjectRole.ts:35,46`). It accepts a
`MaybeRefOrGetter<string | undefined>` (`:17`), so a reactive `() => workspace.projectId`
can be passed.

## 5. Design

### Options considered

**Sidebar entry — how the three surfaces appear.**
- **Option A (chosen)** — a new collapsible `SidebarGroup label="Manage"` in the Project
  Context block, built from a `manageNav` `NavItem[]` exactly like `agentNav`/`knowledgeNav`,
  wrapped in a single `v-if` on the owner gate. Matches the existing grouped IA and makes
  the hide-for-non-owner semantics trivial (one guard around the group).
- **Option B — flat section** (like the Agents section, no group header). Rejected: the
  other management-ish clusters are all grouped; a flat trio would read as un-categorized,
  and there is no collapse affordance.
- **Option C — keep header-only, add nothing to the sidebar.** Rejected: does not fix the
  discoverability gap that motivated the task.

**Switcher placement (Q-1).**
- **Chosen** — render `OrgProjectSwitcher` at the top of `AppSidebar` (above the Workspace
  section) on desktop, and render it in `AppTopBar` only on mobile
  (`v-if="isMobile"`). One component definition, two mount points that never both show on
  desktop. Trade-off: on desktop *immersive* routes the sidebar auto-collapses, so
  switching there needs a sidebar toggle first — accepted, because project switching then
  lives coherently with all other project nav (which is likewise hidden on immersive
  routes), and the toggle is always present in the top bar.
- **Rejected** — switcher in sidebar on *all* breakpoints: on mobile it would be buried
  in a closed drawer. **Rejected** — leave it in the top bar and also add one to the
  sidebar: two always-visible switchers on desktop, visually redundant.

### Decision

Option A + the chosen switcher placement. A role-gated "Manage" `SidebarGroup` (Members /
Skills / Activity types) joins the Project Context block; `OrgProjectSwitcher` moves to the
sidebar top on desktop and is retained in the top bar on mobile only. The header buttons
stay (Q-2). No existing sidebar item changes behavior; the only new *concept* is a
role-gated sidebar group, whose gate is a UX affordance layered over the still-authoritative
page/backend gates.

## 6. Detailed Changes

**Backend** — none.

**API contract** — none. `gen:api` rerun: no.

**Frontend** (`app/` shell only, plus a reused `@slices/tenancy` composable — allowed
cross-slice via the slice index):
- `AppSidebar.vue`:
  - Import `useProjectRole` from `@slices/tenancy` and `OrgProjectSwitcher`.
  - Gate: `const { decided, isAuthorized } = useProjectRole(() => workspace.projectId)`.
  - Add `manageNav` computed (`NavItem[]`, path-string routes
    `/projects/${pid}/members`, `/projects/${pid}/skills`, `/projects/${pid}/activity-types`;
    icons from `@heroicons/vue/24/outline`, e.g. `UsersIcon`/`PuzzlePieceIcon`/`ClipboardDocumentCheckIcon`).
  - Render `OrgProjectSwitcher` (compact) at the top of the `<nav>`, wrapped so it only
    shows on desktop (the mobile drawer already carries the top-bar one — see below; guard
    with `useBreakpoint().isDesktop` to avoid a double switcher inside the mobile drawer).
  - Render a `<SidebarGroup label=Manage storageKey="project-manage">` with the `manageNav`
    links, inside the `workspace.hasProject` block, wrapped in
    `v-if="decided && isAuthorized"`.
- `AppTopBar.vue`: change `<OrgProjectSwitcher :compact="isMobile" />` to render only on
  mobile (`v-if="isMobile"`); the center zone is otherwise empty on desktop.
- i18n: add `app.sidebar.groupManage` (+ the three item labels already exist:
  `app.sidebar`/tenancy keys — reuse existing `tenancy.breadcrumb.members`,
  `tenancy.project.skills`, `tenancy.project.activityTypes`, or add
  `app.sidebar.members/skills/activityTypes` for consistency with the other sidebar
  labels — implementer picks one and stays consistent) in en + zh-TW.

**Deploy/config** — none.

## 7. NFR Checklist

- [x] i18n — new group label + any new item labels via `$t()`, en + zh-TW.
- [x] Audit log — N/A (no domain events; pure navigation).
- [x] Tenant isolation — N/A at this layer; the sidebar gate is cosmetic. The pages
  (`ActivityTypesView` `useProjectRole`, Members/Skills owner checks) and their backend
  routes (`assert_project_owner`) remain the authoritative boundary. The gate must never be
  the *only* control — it isn't.
- [x] Error handling UX — the gate keys on `decided` so an owner's group is not
  flash-hidden mid role-resolution (R11.10); while undecided the group is simply absent
  (same as a non-owner), then appears — no flicker of a populated-then-removed group.
- [x] Performance — `useProjectRole` issues one members query per project (already used
  elsewhere, cached by `tenancyKeys.projectMembers`); admins skip the fetch
  (`useProjectRole.ts:25`). No new N+1.

## 8. Security Considerations

Touches a tenant-scoped surface only as a *display* gate, so a light lens applies:
- The sidebar Manage group is hidden from non-owners for UX, **not** as an access control.
  A non-owner who navigates directly to `/projects/:id/members` etc. is still stopped by
  the page/backend owner checks. This task must not introduce any code that treats sidebar
  visibility as authorization.
- No secrets, no user-input-to-backend path, no new endpoint. `useProjectRole` reads
  membership the caller may already read.

## 9. Quality Notes

**Existing debt (do not imitate, do not silently fix):**
- `AppSidebar.vue` repeats the same `RouterLink`-with-icon block per section (`:100-243`);
  the `manageNav` group follows the same repetition rather than refactoring it now. A
  future extraction of the repeated link markup is FU-2.
- The sidebar has no role-gating today; this task adds it for one group only. Do not
  retrofit the others (Non-goal; FU-1).

**Patterns to follow:**
- `NavItem[]` computed + `v-for RouterLink` + `isActive` (`AppSidebar.vue:33-91,100-135`).
- `SidebarGroup` with `storageKey` (`AppSidebar.vue:163-166`, `SidebarGroup.vue`).
- Owner gate via `useProjectRole` `decided && isAuthorized` (as `ActivityTypesView.vue`).
- Reactive projectId into a composable: `useProjectRole(() => workspace.projectId)`
  (`useProjectRole.ts:17` accepts a getter).

**Reuse inventory:**
- `SidebarGroup`, `OrgProjectSwitcher`, `useWorkspaceStore`, `useProjectRole`
  (`@slices/tenancy`), `useBreakpoint` (`isDesktop`/`isMobile`), `@heroicons/vue/24/outline`.
- Existing i18n label keys for members/skills/activities where suitable.

## 10. Risks and Rollback

- **Immersive-route switcher reachability** (desktop): with the switcher moved into the
  sidebar, switching on a chatroom/workflow-edit route (sidebar auto-collapsed) needs a
  sidebar toggle first. Accepted trade-off (§5); mitigated by the always-present top-bar
  toggle. If it proves annoying, a compact top-bar switcher can be re-added on desktop too
  (reversible, one `v-if`).
- **Double switcher in the mobile drawer**: the mobile drawer renders `AppSidebar`; if the
  sidebar switcher is not desktop-guarded, the drawer would show it *and* the top bar shows
  its own. Mitigation — guard the sidebar switcher on `isDesktop` so mobile uses only the
  top-bar one.
- **Rollback**: entirely additive and behind `v-if`s; reverting the two files restores the
  prior shell exactly. No migration, no data.

## 11. Acceptance Criteria

- [x] AC-1: When a project is active and the caller is its Owner (or an Admin), the sidebar
  shows a collapsible "Manage" group containing Members, Skills, and Activity types, each
  linking to the correct project-scoped route. (`AppSidebar.test.ts` "shows members / skills
  / activity types to an owner" asserts the three hrefs.)
- [x] AC-2: When a project is active and the caller is a non-owner member, the Manage group
  is not rendered at all (no header, no items). (`AppSidebar.test.ts` "hides the group
  entirely from a non-owner" + "hidden until the role is decided".)
- [x] AC-3: The Manage group collapse/expand state persists across reloads
  (`SidebarGroup` storage-key), like the other sidebar groups. (Reuses `SidebarGroup` with
  `storage-key="project-manage"`; persistence is that component's existing, unchanged
  behavior — verified by reuse, behavioral confirm in the manual pass.)
- [x] AC-4: On desktop, `OrgProjectSwitcher` renders at the top of the sidebar and is
  absent from the top bar; selecting a project there re-scopes the sidebar's Project
  Context (drives `useWorkspaceStore`). (`AppSidebar.test.ts` "renders the switcher ... on
  desktop" + `AppTopBar.test.ts` "omits the top-bar switcher on desktop"; the switcher
  component and its `useWorkspaceStore.selectProject` wiring are unchanged.)
- [x] AC-5: On mobile, `OrgProjectSwitcher` renders in the top bar (not duplicated inside
  the drawer sidebar). (`AppTopBar.test.ts` "shows the switcher ... on mobile" +
  `AppSidebar.test.ts` "omits the sidebar switcher on mobile" — disjoint, no double render.)
- [x] AC-6: The `ProjectDetailView` header still shows the Members/Skills/Activities buttons
  for an owner (unchanged). (The file was not touched by this task — verified by diff.)
- [x] AC-7: All new user-facing strings resolve in en and zh-TW; `pnpm lint` (i18n gate)
  passes. (`app.sidebar.groupManage/members/skills/activityTypes` added to both locales;
  `pnpm lint` green.)

## 12. Test Plan

- **Frontend component** (Vitest, `app/__tests__/`): extend/adjacent to `AppShell.test.ts`.
  - `AppSidebar`: with `useWorkspaceStore` having a project + `useProjectRole` mocked to
    `isAuthorized=true, decided=true` → Manage group + 3 links present (AC-1); mocked to
    `isAuthorized=false` → group absent (AC-2); switcher present under `isDesktop=true`,
    absent under `isDesktop=false` (AC-4/AC-5 sidebar side).
  - `AppTopBar`: switcher present when `isMobile=true`, absent when `isMobile=false`
    (AC-4/AC-5 top-bar side).
  - Mock `useBreakpoint`, `useWorkspaceStore`, and `@slices/tenancy` `useProjectRole`
    (partial mock preserving `tenancyRoutes` — the render harness registers app routes).
- **Manual via `run`** (frontend-only, no backend needed for render): owner sees Manage +
  sidebar switcher on desktop; resize to mobile → switcher in top bar; collapse/expand
  persists (AC-3). Behavioral gate to confirm no double-switcher and immersive-route
  behavior.

## 13. SRS Delta

None — navigation placement is not an SRS-level behavior; existing `[R11.10]`
(flash-free owner controls) already governs the gate and is satisfied, not amended.

## 14. Open Questions

- OQ-1: The label key namespace — reuse `tenancy.*` item labels vs. add `app.sidebar.*`
  ones. Implementer's call at build time; both are acceptable, pick one and be consistent.

## 15. Deviation Log

None — the implementation matches this spec.

**Definition-of-Done notes:**
- **Gate 4 (behavioral) not run:** rendering the sidebar needs an authenticated session +
  active project, which requires the Docker stack (unavailable in this environment). Covered
  by the AppSidebar/AppTopBar component tests (owner/non-owner gating, desktop/mobile switcher
  split) and `pnpm build`; a manual pass (owner sees Manage + sidebar switcher on desktop;
  resize to mobile → switcher back in top bar; collapse persists — AC-3) remains.
- **Gate 6 (security) N/A:** no backend, endpoint, injection surface, or authorization
  *decision* was added. The sidebar gate is a cosmetic `v-if` reusing `useProjectRole`;
  the pages and their backend `assert_project_owner` remain the authoritative boundary
  (§8). "Hidden ≠ protected" is upheld — non-owners are stopped server-side regardless of
  sidebar visibility.

## 16. Follow-ups

- FU-1: The other sidebar project groups (agents/knowledge/keys/infra) are shown to any
  member regardless of role; decide per-surface whether they should be role-gated too.
- FU-2: Extract the repeated `RouterLink`-with-icon markup in `AppSidebar.vue` into a small
  presentational item component to cut the per-section duplication.
