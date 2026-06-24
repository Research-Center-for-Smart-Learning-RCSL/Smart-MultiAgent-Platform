# SMAP UI Architecture — Master Plan

> Production-grade UI specification for the Smart Multi-Agent Platform.
> All screens target production quality directly — no staging-only shortcuts.

## Document Index

| # | Document | Scope |
|---|----------|-------|
| 00 | **This file** | Master plan, phases, palette, typography, file map |
| 01 | [Design System](01-design-system.md) | Tokens, atoms, molecules, organisms — the full component library |
| 02 | [Layout Shell](02-layout-shell.md) | Design D hybrid layout: sidebar, top bar, content area, responsive |
| 03 | [Identity](03-identity.md) | Auth pages: login, register, verify, reset, sessions, account |
| 04 | [Tenancy](04-tenancy.md) | Orgs, projects, members, invites, Original Creator transfer |
| 05 | [Keys](05-keys.md) | API keys, key groups, rotation, search keys, usage dashboard |
| 06 | [Agents](06-agents.md) | Agent CRUD, prompt editor, RAG, GraphRAG, MCP, wake-up, sub-agents |
| 07 | [Conversation](07-conversation.md) | Workspaces, chatrooms, messages, streaming, presence, export, guest |
| 08 | [Workflow](08-workflow.md) | Visual DAG editor, node panels, runs, backstage trace, orchestration |
| 09 | [Admin](09-admin.md) | Users, audit, metrics, rate limits, impersonation, IP bans, restore |
| 10 | [Notifications](10-notifications.md) | Bell, notification list, real-time delivery, mark-read |
| 11 | [Responsive & A11y](11-responsive-a11y.md) | Breakpoints, mobile layouts, touch targets, WCAG 2.1 AA |
| 12 | [Shared Patterns](12-shared-patterns.md) | Forms, tables, modals, errors, loading/empty states |

---

## 1. Design Direction

**Design D — Hybrid SaaS + Chat-first.**

The application presents two modes unified under a single shell:
- **Management mode**: standard SaaS card/table layouts for Orgs, Projects, Keys, Agents, Admin.
- **Chat mode**: full-height chatroom with real-time messaging, agent streaming, presence.

The sidebar bridges both modes: top half for navigation, bottom half for chatroom quick-access. The layout auto-collapses the sidebar when the user enters a chatroom or the workflow editor fullscreen.

**Visual identity**: professional, polished, subtle. Light blue / grey palette. No AI aesthetic cliches (no glowing gradients, no robot imagery). Icons from @heroicons/vue are welcome. Emojis are strictly forbidden everywhere.

---

## 2. Color Palette

### Light Theme (default)

| Token | Hex | Usage |
|-------|-----|-------|
| `--color-bg` | `#ffffff` | Page background |
| `--color-fg` | `#1f2328` | Primary text |
| `--color-muted` | `#6b7280` | Secondary text, placeholders |
| `--color-accent` | `#2563eb` | Primary actions, links, focus rings |
| `--color-accent-hover` | `#1d4ed8` | Hovered primary actions |
| `--color-danger` | `#dc2626` | Destructive actions, errors |
| `--color-success` | `#16a34a` | Success states, online indicators |
| `--color-warning` | `#d97706` | Warnings, threshold alerts |
| `--color-surface` | `#f8fafc` | Card backgrounds, elevated surfaces |
| `--color-border` | `#e5e7eb` | Dividers, input borders |
| `--color-sidebar-bg` | `#f1f5f9` | Sidebar background |
| `--color-sidebar-hover` | `#dbeafe` | Sidebar item hover |
| `--color-sidebar-active` | `#2563eb` | Active sidebar item accent |

### Dark Theme

| Token | Hex | Usage |
|-------|-----|-------|
| `--color-bg` | `#0b0f14` | Page background |
| `--color-fg` | `#e5e7eb` | Primary text |
| `--color-muted` | `#9ca3af` | Secondary text |
| `--color-accent` | `#60a5fa` | Primary actions |
| `--color-accent-hover` | `#93c5fd` | Hovered primary actions |
| `--color-surface` | `#1f2937` | Card backgrounds |
| `--color-border` | `#374151` | Dividers |
| `--color-sidebar-bg` | `#111827` | Sidebar background |
| `--color-sidebar-hover` | `#1e3a5f` | Sidebar item hover |
| `--color-sidebar-active` | `#60a5fa` | Active sidebar item accent |

### Semantic Tint Pairs (status badges, alerts)

| Status | Tint (bg) | On (text) |
|--------|-----------|-----------|
| Info | `#dbeafe` / `#1e3a5f` | `#1d4ed8` / `#93c5fd` |
| Success | `#dcfce7` / `#14532d` | `#15803d` / `#86efac` |
| Warning | `#fef3c7` / `#78350f` | `#92400e` / `#fcd34d` |
| Danger | `#fee2e2` / `#7f1d1d` | `#b91c1c` / `#fca5a5` |
| Neutral | `#f3f4f6` / `#374151` | `#4b5563` / `#d1d5db` |

---

## 3. Typography

| Element | Size | Weight | Line-height |
|---------|------|--------|-------------|
| Page title (h1) | 1.5rem (24px) | 600 | 1.4 |
| Section heading (h2) | 1.25rem (20px) | 600 | 1.4 |
| Subsection (h3) | 1.125rem (18px) | 600 | 1.4 |
| Body text | 0.875rem (14px) | 400 | 1.5 |
| Small / caption | 0.75rem (12px) | 400 | 1.4 |
| Extra-small (badges) | 0.625rem (10px) | 500 | 1 |
| Code / mono | 0.8125rem (13px) | 400 | 1.5 |

**Font stack**: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`
**Mono stack**: `"SF Mono", "Cascadia Code", "Fira Code", Consolas, monospace`

---

## 4. Spacing & Sizing Scale

Based on Tailwind's default `--spacing: 0.25rem` (4px base unit).

| Token | px | Common use |
|-------|----|------------|
| `1` | 4 | Tight gaps, badge padding |
| `2` | 8 | Input padding, icon gaps |
| `3` | 12 | Card inner padding (compact) |
| `4` | 16 | Standard content padding |
| `5` | 20 | Section gaps |
| `6` | 24 | Card padding (standard) |
| `8` | 32 | Section margins |
| `10` | 40 | Page margins |
| `12` | 48 | Large spacing |

**Border radius**: `--radius-md: 6px` (cards, inputs), `--radius-lg: 8px` (modals), `--radius-full: 9999px` (pills, avatars).

**Touch target minimum**: 44x44px (`--touch-min: 44px`).

---

## 5. Iconography

**Library**: `@heroicons/vue` v2.2 — three styles available:
- `24/outline` — default for navigation and actions
- `24/solid` — filled variant for active states and emphasis
- `20/solid` — compact variant for inline icons in text

**Sizing convention**:
- Navigation icons: 20x20 (`w-5 h-5`)
- Action buttons: 16x16 (`w-4 h-4`)
- Inline text: 16x16 (`w-4 h-4`)
- Empty state illustrations: 48x48 (`w-12 h-12`)

**Icon color**: inherits `currentColor` by default; override with `text-muted` for secondary icons.

---

## 6. Implementation Phases

The UI build is organized into 5 phases. Each phase produces a deployable increment.

### Phase U1 — Shell & Design System (foundation)

**Goal**: Establish the layout shell and component library so all subsequent view work has a consistent container.

**Deliverables**:
1. Design tokens expansion in `main.css` (sidebar colors, accent-hover, shadows)
2. Shared component library: all atoms and molecules listed in [01-design-system.md](01-design-system.md)
3. `AppShell.vue` layout with sidebar + top bar per [02-layout-shell.md](02-layout-shell.md)
4. Route-aware layout switching (auth pages = centered, app pages = shell)
5. Responsive sidebar collapse at `< 1024px`
6. Theme toggle integrated into top bar
7. Landing page redesign with auth-aware routing

**Exit criteria**: `pnpm build` passes, sidebar navigates all top-level routes, responsive collapse works at all breakpoints.

### Phase U2 — Identity & Tenancy (auth + org/project management)

**Goal**: Polished auth flow and org/project management pages.

**Deliverables**:
1. All identity views restyled per [03-identity.md](03-identity.md)
2. All tenancy views restyled per [04-tenancy.md](04-tenancy.md)
3. Org/Project context switcher in top bar
4. Invite accept flow with notification integration
5. Original Creator transfer UI

**Exit criteria**: Full auth flow (register -> verify -> login -> create org -> create project) works end-to-end with polished UI.

### Phase U3 — Keys & Agents (configuration management)

**Goal**: Production-ready key and agent management interfaces.

**Deliverables**:
1. Key management views per [05-keys.md](05-keys.md)
2. Agent management views per [06-agents.md](06-agents.md)
3. RAG config + document upload UI
4. GraphRAG config + build status UI
5. MCP bindings UI with test button
6. Key group builder with drag-reorder
7. Usage dashboard with charts

**Exit criteria**: User can upload keys, create agents with full config (key group, prompt, RAG, MCP, wake-up), and see usage data.

### Phase U4 — Conversation & Workflow (real-time)

**Goal**: Polished chat experience and visual workflow editor.

**Deliverables**:
1. Chatroom redesign per [07-conversation.md](07-conversation.md)
2. Message rendering pipeline (markdown, code, KaTeX, Mermaid)
3. Agent streaming with thinking indicators
4. Presence panel, typing indicators
5. Workspace/chatroom list in sidebar bottom section
6. Workflow editor canvas per [08-workflow.md](08-workflow.md)
7. All 11 node type config panels
8. Workflow runs list and backstage trace view
9. Guest landing page

**Exit criteria**: Real-time chat works with agent streaming, workflow editor can create/edit/validate/run workflows.

### Phase U5 — Admin, Notifications & Polish (governance)

**Goal**: Admin console, notification system, and final polish pass.

**Deliverables**:
1. Admin console per [09-admin.md](09-admin.md)
2. Notification system per [10-notifications.md](10-notifications.md)
3. Responsive pass per [11-responsive-a11y.md](11-responsive-a11y.md)
4. WCAG 2.1 AA audit and fixes
5. Empty state illustrations for all views
6. Loading skeleton screens
7. Error boundary polish
8. Bundle size optimization

**Exit criteria**: All 12 CI gates pass, bundle budget met, WCAG AA on core flows, all views have loading/empty/error states.

---

## 7. File Map — New and Modified Files

### New Files (Phase U1)

```
src/
  app/
    layouts/
      AppShell.vue              # Main layout with sidebar + top bar
      AuthLayout.vue            # Centered layout for auth pages
    components/
      AppSidebar.vue            # Navigation sidebar
      AppTopBar.vue             # Top bar with context switcher
      OrgProjectSwitcher.vue    # Org/Project dropdown in top bar
      UserMenu.vue              # User avatar + dropdown menu
      SidebarChatroomList.vue   # Bottom sidebar section: recent chatrooms
  shared/
    ui/
      SButton.vue               # Button atom (primary, secondary, danger, ghost, icon)
      SInput.vue                # Input atom (text, password, email, number, search)
      SSelect.vue               # Select/dropdown atom
      SCheckbox.vue             # Checkbox atom
      SRadio.vue                # Radio button atom
      STextarea.vue             # Textarea atom
      SModal.vue                # Modal dialog
      SDrawer.vue               # Slide-out drawer
      STable.vue                # Data table with sort/filter/pagination
      STabs.vue                 # Tab navigation
      SBadge.vue                # Inline badge/chip
      SAlert.vue                # Alert banner (info, success, warning, error)
      SPagination.vue           # Pagination controls
      SSkeleton.vue             # Loading skeleton placeholder
      SAvatar.vue               # User/agent avatar
      SDropdown.vue             # Dropdown menu
      STooltip.vue              # Tooltip wrapper
      SToggle.vue               # Toggle switch
      SFileUpload.vue           # File upload zone (drag-drop + click)
      SCodeEditor.vue           # Code/prompt text editor (monospace)
      SSearchInput.vue          # Search input with icon and clear button
      SBreadcrumb.vue           # Breadcrumb navigation
      SProgressBar.vue          # Progress bar (determinate/indeterminate)
      SDivider.vue              # Horizontal/vertical divider
      SAccordion.vue            # Collapsible accordion
```

### Modified Files

```
src/app/App.vue                 # Wrap router-view in layout system
src/app/router.ts               # Add layout meta to routes
src/shared/styles/main.css      # Expand tokens (sidebar, accent-hover, shadows)
src/app/views/Landing.vue       # Redesign with hero + auth-aware CTA
src/app/views/NotFound.vue      # Styled 404 with illustration
```

### Per-Slice View Rewrites (Phases U2–U5)

Every existing view file will be rewritten to use the design system components. No new view routes are added — all 68 routes already exist. The work is purely visual: replacing raw HTML with `SButton`, `SCard`, `STable`, `SModal`, etc., and applying the layout shell.

---

## 8. Constraints

1. **SoC boundaries**: all new shared components live in `src/shared/ui/`. Layout components live in `src/app/layouts/` and `src/app/components/`. Slices only import from shared.
2. **i18n**: every user-facing string via `$t()`. No hardcoded text.
3. **Icons**: `@heroicons/vue` only. No emoji. No icon fonts.
4. **Bundle budget**: initial <= 250 KB gzip, per-view lazy <= 200 KB gzip.
5. **Type coverage**: >= 95%.
6. **Accessibility**: WCAG 2.1 AA on core flows (login, chat, agent list).
7. **Touch targets**: >= 44x44px on all interactive elements.
8. **Theme**: light + dark via CSS custom properties. No theme-specific component logic.
9. **No new dependencies** for Phase U1 beyond what is already installed.

---

## 9. Cross-References

- **REQUIREMENTS.md**: `[R24.xx]` — Frontend requirements
- **docs/implement/J-frontend-release.md**: Construction plan Phase J
- **frontend/CLAUDE.md**: Stack, patterns, CI gates
- **Memory: frontend-ui-direction**: Design D decision, color palette, strict visual requirements
