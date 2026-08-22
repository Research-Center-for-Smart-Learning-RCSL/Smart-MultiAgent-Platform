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

Every neutral sits on Tailwind's **slate** axis. That is not a preference between two
equivalent greys: the surfaces were already slate while the text and borders were gray,
so the interface was subtly out of tune with itself in a way that reads as cheap without
being locatable. Slate is also the blue-leaning grey, which is what makes the "light blue
/ grey" identity in §1 true rather than aspirational. The accent and the four status
colours are unchanged.

### Surface roles

Three depths, not two. This is the distinction that lets an elevation token mean
anything: before it, the content area and the cards on it were both `--color-bg`, so no
shadow value could make a card read as raised.

| Token | Role |
|-------|------|
| `--color-canvas` | The floor the application sits on: content area, sidebar, auth pages |
| `--color-bg` | A sheet raised off it: card, modal, dropdown, top bar, table row |
| `--color-surface` | A fill recessed into a sheet: table header, secondary button, card footer |

The canvas-to-sheet gap is held between **3 and 5 points of CIE L\*** in both themes
(measured 3.65 light, 4.37 dark). Holding both themes to one window is what makes a
single `--elevation-*` ladder correct in both, which is the premise those tokens were
written on. `contrast.test.ts` enforces it.

### Rule weights

One token used to draw a text field's outline, a card's edge and the line between two
table rows. Three roles now, split by what a rule is *for*:

| Token | Role |
|-------|------|
| `--color-border-strong` | A form control's boundary. WCAG 2.1 1.4.11 applies here and only here: the outline is the sole indicator that a field is present, so it must clear 3:1 |
| `--color-border` | A container's decorative edge: card, sidebar edge, top bar, fieldset |
| `--color-border-subtle` | An interior separator: table rows, card header/footer rules, accordion and dropdown dividers |

Buttons are deliberately not control boundaries: a button carries a label and a fill, so
its border identifies nothing on its own.

### Light Theme (default)

| Token | Hex | Usage |
|-------|-----|-------|
| `--color-canvas` | `#f1f5f9` | Application background (slate-100) |
| `--color-bg` | `#ffffff` | Raised sheet |
| `--color-surface` | `#f8fafc` | Recessed fill inside a sheet (slate-50) |
| `--color-fg` | `#0f172a` | Primary text (slate-900) |
| `--color-muted` | `#475569` | Secondary text, placeholders (slate-600) |
| `--color-accent` | `#2563eb` | Primary actions, links, focus ring |
| `--color-accent-hover` | `#1d4ed8` | Hovered primary actions |
| `--color-on-accent` | `#ffffff` | Content on a filled accent surface |
| `--color-danger` | `#dc2626` | Destructive actions, errors |
| `--color-on-danger` | `#ffffff` | Content on a filled danger surface |
| `--color-success` | `#16a34a` | Success states, online indicators |
| `--color-warning` | `#d97706` | Warnings, threshold alerts |
| `--color-border-strong` | `#64748b` | Form control boundary (slate-500) |
| `--color-border` | `#cbd5e1` | Container boundary (slate-300) |
| `--color-border-subtle` | `#e2e8f0` | Interior separator (slate-200) |
| `--color-sidebar-bg` | `#f1f5f9` | Sidebar background (the canvas) |
| `--color-sidebar-text` | `#334155` | Sidebar item label (slate-700) |
| `--color-sidebar-section-text` | `#475569` | Sidebar section label (slate-600) |
| `--color-sidebar-hover` | `#dbeafe` | Sidebar item hover |
| `--color-accent-tint-hover` | `#bfdbfe` | Hover on an already-active sidebar item |

### Dark Theme

| Token | Hex | Usage |
|-------|-----|-------|
| `--color-canvas` | `#080d16` | Application background |
| `--color-bg` | `#0f172a` | Raised sheet (slate-900) |
| `--color-surface` | `#1e293b` | Recessed fill inside a sheet (slate-800) |
| `--color-fg` | `#e2e8f0` | Primary text (slate-200) |
| `--color-muted` | `#94a3b8` | Secondary text (slate-400) |
| `--color-accent` | `#60a5fa` | Primary actions |
| `--color-accent-hover` | `#93c5fd` | Hovered primary actions |
| `--color-on-accent` | `#0f172a` | Content on a filled accent surface |
| `--color-danger` | `#f87171` | Destructive actions, errors |
| `--color-on-danger` | `#0f172a` | Content on a filled danger surface |
| `--color-border-strong` | `#64748b` | Form control boundary (not themed: 3.90:1 here, 4.76:1 in light) |
| `--color-border` | `#334155` | Container boundary (slate-700) |
| `--color-border-subtle` | `#1e293b` | Interior separator (slate-800) |
| `--color-sidebar-bg` | `#080d16` | Sidebar background (the canvas) |
| `--color-sidebar-text` | `#cbd5e1` | Sidebar item label (slate-300) |
| `--color-sidebar-section-text` | `#94a3b8` | Sidebar section label (slate-400) |

`--color-on-accent` and `--color-on-danger` are theme-aware because the accent is. The
dark theme's accent and danger are *light* colours, so what reads on them is the sheet
colour, not white: white measured 2.54:1 and 2.77:1 there, which is below AA on the most
used control in the product.

### Semantic Tint Pairs (status badges, alerts)

| Status | Tint (bg) | On (text) |
|--------|-----------|-----------|
| Info | `#dbeafe` / `#1e3a5f` | `#1d4ed8` / `#93c5fd` |
| Success | `#dcfce7` / `#14532d` | `#15803d` / `#86efac` |
| Warning | `#fef3c7` / `#78350f` | `#92400e` / `#fcd34d` |
| Danger | `#fee2e2` / `#7f1d1d` | `#b91c1c` / `#fca5a5` |
| Neutral | `#f1f5f9` / `#334155` | `#334155` / `#cbd5e1` |

### Contrast

Every foreground-on-background pair above meets WCAG 2.1 AA (4.5:1) in both themes, and
`--color-border-strong` meets 1.4.11's 3:1. This is measured, not asserted:
`src/shared/styles/__tests__/contrast.test.ts` parses both theme blocks out of `main.css`
and fails the build on a pair that drops below its threshold. Before it existed the
requirement had been stated since this document was written and never checked, and two
pairs were in fact below it.

---

## 3. Typography

Sizing is written as a token name here and everywhere else in `docs/UI/`. The values
live in one place: `01-design-system.md` §1, which mirrors `main.css`'s `@theme`
block. A second copy of a number is a second source of truth, and this
document is what an implementer reads.

| Element | Size | Weight | Line-height | Tracking |
|---------|------|--------|-------------|----------|
| Page title (h1) | `--font-size-2xl` | `--weight-semibold` | `--line-tight` | `--tracking-tight` |
| Section heading (h2) | `--font-size-xl` | `--weight-semibold` | `--line-tight` | `--tracking-tight` |
| Subsection (h3) | `--font-size-lg` | `--weight-semibold` | `--line-snug` | — |
| Body text | `--font-size-sm` | `--weight-normal` | `--line-normal` | — |
| Small / caption | `--font-size-xs` | `--weight-normal` | `--line-snug` | — |
| Extra-small (badges) | `--font-size-2xs` | `--weight-medium` | `--line-none` | — |
| Code / mono | `--font-size-code` | `--weight-normal` | `--line-normal` | — |

`@layer base` in `main.css` restores exactly these sizes to a bare `h1`/`h2`/`h3` that
Preflight strips. The two used to disagree — the base layer sat one ramp step lower — and
they now do not.

Tracking is negative and applies from `--font-size-xl` upward only. A UI grotesque is
drawn for body sizes and reads loose at heading sizes; tightening body text would cost
legibility instead of buying anything. `--tracking-tighter` exists for the one
display-size heading in the product, Landing's hero, which runs fluid above the ramp.

**Font stack**: `Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", "Helvetica Neue", Arial, sans-serif`
**Mono stack**: `"SF Mono", "Cascadia Code", "Fira Code", Consolas, monospace`

Inter is self-hosted from `frontend/public/fonts/` under SIL OFL 1.1, in two
`unicode-range`-scoped subsets: Latin (47 KB, preloaded from the document head) and
Latin-Extended (83 KB, fetched only if a document needs it). Self-hosting is not a
preference — the deployed CSP is `font-src 'self' data:`, so a font CDN is blocked
outright, and an air-gapped install has none to reach.

The ranges stop at Latin deliberately. Inter has no CJK coverage, and without an explicit
`unicode-range` a browser may fetch the file only to discover that. The CJK families are
named after it in the stack and are never resolved through it; before this they were
named nowhere at all, so zh-TW fell to whatever the browser happened to default to.

---

## 4. Spacing & Sizing Scale

A 4px grid, plus three half-steps the component library uses as the inner padding of a
control whose outer padding is the whole step above. Values in `01-design-system.md` §1.

| Token | Common use |
|-------|------------|
| `--space-0-5` | Chip and inline-badge padding |
| `--space-1` | Tight gaps, label-to-control |
| `--space-1-5` | Compact control padding |
| `--space-2` | Input padding, icon gaps |
| `--space-2-5` | Large-control vertical padding |
| `--space-3` | Card inner padding (compact) |
| `--space-4` | Standard content padding |
| `--space-5` | Section gaps |
| `--space-6` | Card padding (standard) |
| `--space-8` | Section margins |
| `--space-12` | Large spacing |

The scale is declared in **rem**, each value equal to the px it replaced at the 16px
default root. It used to be px while the type ramp was rem, which meant a reader who
raised their browser's font size got larger text inside padding that stayed put — the
interface tightened around them at the moment they asked for more room.

There is no `--space-10`. The 40px rung had no consumer once every spacing declaration in
the codebase named a token, and the large-gap cases sit at 32 and 48.

**Control heights**: `--control-h-sm` / `--control-h-md` / `--control-h-lg`. Anything
that can share a form row with a button reads these, so the row cannot end up ragged.

**Border radius**: `--radius-md: 6px` (cards, inputs), `--radius-lg: 8px` (modals), `--radius-full: 9999px` (pills, avatars).

**Touch target minimum**: 44x44px (`--touch-min: 44px`). Deliberately not on the spacing
ladder, because it is an accessibility floor, not a design step, and must not move when the
scale is retuned.

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
