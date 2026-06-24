# 02 — Layout Shell

> Design D — Hybrid SaaS + Chat-first layout.
> Two layout modes unified under a single shell.

---

## 1. Layout Architecture

The application uses two layout wrappers, selected by route metadata:

| Layout | Routes | Description |
|--------|--------|-------------|
| `AuthLayout` | `/login`, `/register`, `/verify-email`, `/password-reset`, `/password-reset/confirm`, `/g/:chatroomId/:guestToken`, `/` (unauthenticated) | Centered card on plain background |
| `AppShell` | All authenticated routes | Sidebar + top bar + content area |

### Route Meta Convention

```ts
// In route definition:
meta: { layout: 'auth' }   // uses AuthLayout
meta: { layout: 'app' }    // uses AppShell (default for requiresAuth routes)
```

### App.vue Integration

`App.vue` reads `$route.meta.layout` and renders the appropriate wrapper around `<router-view>`. The switch uses a `<component :is="...">` pattern:

```
App.vue
  ├── ImpersonationBanner (fixed top, only when impersonating)
  ├── <component :is="layoutComponent">
  │     └── <router-view :key="$route.path" />
  │   </component>
  ├── Toaster
  └── SConfirmDialog
```

---

## 2. AuthLayout

**File**: `src/app/layouts/AuthLayout.vue`

**Visual spec**:
- Full viewport height, centered both axes
- Background: `--color-surface` (light grey)
- Content card: max-width 420px, white bg, `--shadow-md`, `--radius-lg`, padding 32px
- SMAP logo above the card: text-only "SMAP" in 24px 700 weight `--color-accent`, 24px margin-bottom
- Footer: links row below card (Login / Register toggle), 16px margin-top, centered

**Responsive**: card becomes full-width below 480px with 16px horizontal padding, no shadow.

```
┌─────────────────────────────────────────┐
│                                         │
│               SMAP                      │
│         ┌──────────────┐                │
│         │              │                │
│         │   Auth Form  │                │
│         │              │                │
│         └──────────────┘                │
│          Login | Register               │
│                                         │
└─────────────────────────────────────────┘
```

---

## 3. AppShell

**File**: `src/app/layouts/AppShell.vue`

### 3.1 Overall Grid

```
┌──────────────────────────────────────────────────┐
│  Top Bar (56px)                      [U] [N] [T] │
├────────────┬─────────────────────────────────────┤
│            │                                     │
│  Sidebar   │         Content Area                │
│  (260px)   │         (flex: 1)                   │
│            │                                     │
│  ┌──────┐  │                                     │
│  │ Nav  │  │                                     │
│  │      │  │                                     │
│  ├──────┤  │                                     │
│  │Rooms │  │                                     │
│  │      │  │                                     │
│  └──────┘  │                                     │
│            │                                     │
├────────────┴─────────────────────────────────────┤
```

**CSS Grid definition**:
```css
.app-shell {
  display: grid;
  grid-template-columns: var(--sidebar-width) 1fr;
  grid-template-rows: var(--topbar-height) 1fr;
  height: 100vh;
  overflow: hidden;
}

.app-shell--sidebar-collapsed {
  grid-template-columns: 0 1fr;
}
```

### 3.2 Sidebar Auto-Collapse

The sidebar collapses (width -> 0, hidden) in these contexts:
1. **Chatroom focus**: when route matches `/chatrooms/:chatroomId`
2. **Workflow editor fullscreen**: when route matches `*/workflows/:workflowId/edit`
3. **Mobile (< 1024px)**: always collapsed; accessible via hamburger menu -> drawer

A toggle button in the top bar allows manual collapse/expand. When collapsed, the toggle shows a hamburger icon; when expanded, it shows an X or left-arrow icon.

**Collapse behavior**:
- Desktop: sidebar width animates to 0 (300ms ease), content area expands
- Mobile: sidebar renders as an `SDrawer` from the left, overlay on content

### 3.3 Content Area

- Padding: 24px on desktop, 16px on mobile
- Max-width: none (full available width)
- Overflow-y: auto (scrollable)
- Background: `--color-bg` (white)

For chatroom routes, content padding is reduced to 0 (the chatroom view manages its own padding for full-height experience).

---

## 4. Top Bar

**File**: `src/app/components/AppTopBar.vue`

### 4.1 Layout

```
┌──────────────────────────────────────────────────────────────┐
│ [=] SMAP    OrgName / ProjectName [v]     [?] [N] [A] [T]   │
└──────────────────────────────────────────────────────────────┘
```

| Zone | Content | Alignment |
|------|---------|-----------|
| Left | Sidebar toggle button + SMAP wordmark | `flex-start` |
| Center | Org/Project context switcher | `flex-start` (after logo, gap 24px) |
| Right | Help link, Notification bell, User avatar menu, Theme toggle | `flex-end` |

### 4.2 Component Breakdown

**Sidebar toggle**: `Bars3Icon` (hamburger) when collapsed, `XMarkIcon` when expanded. 40x40px ghost button.

**SMAP wordmark**: "SMAP" text, 18px 700 weight `--color-accent`. Acts as link to `/orgs` (authenticated) or `/` (unauthenticated).

**Org/Project switcher** (`OrgProjectSwitcher.vue`):
- Displays: `OrgName / ProjectName` with `ChevronDownIcon`
- Click opens dropdown with two columns:
  - Left: list of user's orgs (clicking selects org, shows its projects on right)
  - Right: list of projects in selected org
- Selected org/project stored in session (Pinia) and persisted to `localStorage`
- If no org/project selected, shows "Select workspace..." placeholder
- Org list includes "Create Organization" action at bottom
- Project list includes "Create Project" action at bottom

**Notification bell** (`NotificationBell.vue` — already exists):
- `BellIcon` (24/outline) with unread count badge (red dot + number)
- Click navigates to `/notifications`
- Badge visible when `unreadCount > 0`

**User avatar menu** (`UserMenu.vue`):
- `SAvatar` with user's display name
- Click opens `SDropdown` with items:
  - Profile header: user email (non-clickable, muted)
  - "Account Settings" -> `/account/password`
  - "Sessions" -> `/account/sessions`
  - Divider
  - "Admin Console" -> `/admin` (visible only if `is_admin`)
  - Divider
  - "Log Out" (danger) -> calls logout

**Theme toggle** (`ThemeToggle.vue` — restyle):
- Icon-only ghost button: `SunIcon` (light), `MoonIcon` (dark), `ComputerDesktopIcon` (system)
- Click cycles: light -> dark -> system -> light

### 4.3 Visual Spec

- Height: 56px (`--topbar-height`)
- Background: `--color-bg` (white)
- Bottom border: 1px `--color-border`
- Z-index: `--z-topbar` (200)
- Position: sticky top 0 (part of grid, but sticks during content scroll)
- Horizontal padding: 16px
- Items vertically centered

---

## 5. Sidebar

**File**: `src/app/components/AppSidebar.vue`

### 5.1 Structure

```
┌────────────────────────┐
│ Navigation             │
│                        │
│ [icon] Organizations   │
│ [icon] Projects        │
│ [icon] My Keys         │
│ [icon] Notifications   │
│ ─────────────────────  │
│ PROJECT CONTEXT        │
│ [icon] Agents          │
│ [icon] RAG Configs     │
│ [icon] Key Groups      │
│ [icon] Search Keys     │
│ [icon] Workspaces      │
│ ─────────────────────  │
│ RECENT CHATROOMS       │
│ [icon] #general        │
│ [icon] #dev-agents     │
│ [icon] #testing        │
│                        │
│                        │
│ ─────────────────────  │
│ [icon] Admin Console   │
└────────────────────────┘
```

### 5.2 Sections

**Section 1 — Global Navigation** (always visible):

| Icon | Label | Route | Guard |
|------|-------|-------|-------|
| `BuildingOffice2Icon` | Organizations | `/orgs` | verified email |
| `FolderIcon` | Projects | `/projects` | verified email |
| `KeyIcon` | My Keys | `/keys` | verified email |
| `BellIcon` | Notifications | `/notifications` | verified email |
| `InboxArrowDownIcon` | Invites | `/invites` | authenticated |

**Section 2 — Project Context** (visible when a project is selected in the switcher):

| Icon | Label | Route | Guard |
|------|-------|-------|-------|
| `CpuChipIcon` | Agents | `/projects/:pid/agents` | verified email |
| `DocumentTextIcon` | RAG Configs | `/projects/:pid/rag-configs` | verified email |
| `CircleStackIcon` | GraphRAG | `/projects/:pid/graphrag-configs` | verified email |
| `RectangleGroupIcon` | Key Groups | `/projects/:pid/key-groups` | verified email |
| `MagnifyingGlassIcon` | Search Keys | `/projects/:pid/search-keys` | verified email |
| `Square3Stack3DIcon` | Workspaces | `/projects/:pid/workspaces` | verified email |
| `ShieldCheckIcon` | MCP Allowlist | `/projects/:pid/mcp/egress-allowlist` | verified email |

**Section 3 — Recent Chatrooms** (`SidebarChatroomList.vue`):

Visible when a project is selected. Shows the most recent 10 chatrooms across all workspaces in the selected project.

| Info | Content |
|------|---------|
| Each item | `ChatBubbleLeftIcon` + chatroom name (truncated to 20 chars) |
| Click | Navigates to `/chatrooms/:chatroomId` |
| Active | `--color-sidebar-active-bg` background, `--color-sidebar-active-text` text |
| Badge | Unread message count (if applicable in future) |
| Empty state | "No chatrooms yet" muted text |

**Section 4 — Admin** (visible only if `is_admin`):

| Icon | Label | Route |
|------|-------|-------|
| `ShieldExclamationIcon` | Admin Console | `/admin` |

### 5.3 Visual Spec

- Width: 260px (`--sidebar-width`)
- Background: `--color-sidebar-bg` (`#f1f5f9`)
- Border-right: 1px `--color-border`
- Z-index: `--z-sidebar` (100)
- Overflow-y: auto (scrollable when items exceed viewport)
- Position: fixed height within grid row 2

**Nav items**:
- Height: 40px
- Padding: 0 16px
- Gap between icon and label: 12px
- Font: 14px, 400 weight, `--color-sidebar-text`
- Hover: `--color-sidebar-hover` background
- Active (current route): `--color-sidebar-active-bg` background, `--color-sidebar-active-text` text, left 3px border `--color-sidebar-active-text`
- Icon: 20x20px, inherits text color

**Section headers**:
- Uppercase, 11px, 600 weight, `--color-sidebar-section-text`
- Padding: 16px 16px 8px
- Letter-spacing: 0.05em

**Dividers**: 1px `--color-border`, 16px horizontal margin, 8px vertical margin

---

## 6. Landing Page

**File**: `src/app/views/Landing.vue` (rewrite)

### When unauthenticated

```
┌─────────────────────────────────────────┐
│               SMAP                      │
│                                         │
│    Smart Multi-Agent Platform           │
│                                         │
│    Compose, orchestrate, and chat       │
│    with multi-LLM agent groups.         │
│                                         │
│    [Get Started]    [Log In]            │
│                                         │
└─────────────────────────────────────────┘
```

- Uses `AuthLayout` (centered)
- "Get Started" (primary button) -> `/register`
- "Log In" (secondary button) -> `/login`
- No sidebar, no top bar

### When authenticated

- Redirects to `/orgs` (the default dashboard view)

---

## 7. 404 Page

**File**: `src/app/views/NotFound.vue` (rewrite)

- Uses `AppShell` if authenticated, `AuthLayout` if not
- `SEmptyState` with `ExclamationTriangleIcon`, "Page Not Found" title, "The page you're looking for doesn't exist or has been moved." description
- "Go Home" primary button -> `/orgs` (authenticated) or `/` (unauthenticated)

---

## 8. Responsive Behavior

### Breakpoint Matrix

| Breakpoint | Sidebar | Top Bar | Content | Notes |
|------------|---------|---------|---------|-------|
| >= 1280px (xl) | 260px visible | Full | `calc(100vw - 260px)` | Default desktop |
| 1024-1279px (lg) | 260px visible | Full | Adapts | Narrower cards |
| 768-1023px (md) | Drawer (hidden) | Hamburger added | Full width | Tablet |
| < 768px (sm) | Drawer (hidden) | Compact | Full width | Mobile |
| < 480px (xs) | Drawer (hidden) | Minimal | Full width, 8px padding | Small phone |

### Sidebar on Mobile (< 1024px)

- Sidebar renders as `SDrawer` from left side
- Opened via hamburger button in top bar
- Closes on route navigation
- Closes on outside click / swipe
- Overlay backdrop on content

### Top Bar on Mobile

- Logo text "SMAP" stays
- Org/Project switcher collapses to icon-only (shows org initial in circle)
- User menu collapses to avatar-only
- Theme toggle hidden (accessible from user menu instead)

---

## 9. Route-Layout Mapping

| Route Pattern | Layout | Sidebar State | Content Padding |
|---------------|--------|---------------|-----------------|
| `/` | Auth or redirect | N/A | N/A |
| `/login`, `/register`, `/verify-email`, `/password-reset/*` | Auth | N/A | N/A |
| `/g/:chatroomId/:guestToken` | Auth | N/A | N/A |
| `/orgs`, `/orgs/:id/*` | App | Normal | 24px |
| `/projects`, `/projects/:id/*` | App | Normal | 24px |
| `/keys`, `/keys/:id` | App | Normal | 24px |
| `/invites`, `/invites/accept` | App | Normal | 24px |
| `/notifications` | App | Normal | 24px |
| `/account/*` | App | Normal | 24px |
| `/projects/:pid/agents`, etc. | App | Normal | 24px |
| `/chatrooms/:chatroomId` | App | Collapsed | 0 |
| `/chatrooms/:chatroomId/settings` | App | Normal | 24px |
| `*/workflows/:wid/edit` | App | Collapsed | 0 |
| `*/workflows/*` (other) | App | Normal | 24px |
| `/admin/*` | App | Normal | 24px |
| `/:pathMatch(.*)*` | App or Auth | Depends on auth | 24px |

### Implementation

Route meta for sidebar control:

```ts
meta: {
  requiresAuth: true,
  sidebarCollapsed: true,   // forces sidebar hidden
  contentPadding: 'none',   // removes content area padding
}
```

`AppShell` reads these meta values reactively via `useRoute()`.

---

## 10. Files to Create

| File | Description |
|------|-------------|
| `src/app/layouts/AppShell.vue` | Main application layout |
| `src/app/layouts/AuthLayout.vue` | Centered auth layout |
| `src/app/components/AppSidebar.vue` | Navigation sidebar |
| `src/app/components/AppTopBar.vue` | Top bar |
| `src/app/components/OrgProjectSwitcher.vue` | Context switcher dropdown |
| `src/app/components/UserMenu.vue` | User avatar dropdown |
| `src/app/components/SidebarChatroomList.vue` | Recent chatrooms in sidebar |

| File | Changes |
|------|---------|
| `src/app/App.vue` | Integrate layout system, remove `.app-chrome` fixed position |
| `src/app/router.ts` | Add `layout` and `sidebarCollapsed` meta to routes |
| `src/app/views/Landing.vue` | Redesign with hero |
| `src/app/views/NotFound.vue` | Redesign with SEmptyState |
