# 11 — Responsive Design & Accessibility

> Breakpoint behavior, mobile layouts, touch targets, and WCAG 2.1 AA compliance.

---

## 1. Breakpoint System

Five breakpoints defined as CSS custom properties in `main.css`:

| Name | Min-width | Composable value | Typical devices |
|------|-----------|------------------|-----------------|
| `xs` | 0 | `< 480px` | Small phones (iPhone SE) |
| `sm` | 480px | `>= 480px` | Large phones |
| `md` | 768px | `>= 768px` | Tablets portrait |
| `lg` | 1024px | `>= 1024px` | Tablets landscape, small laptops |
| `xl` | 1280px | `>= 1280px` | Desktops |

### useBreakpoint() Composable

Returns reactive flags for current breakpoint:

```ts
const { isMobile, isTablet, isDesktop, breakpoint } = useBreakpoint()
// isMobile:  < 768px
// isTablet:  >= 768px && < 1024px
// isDesktop: >= 1024px
// breakpoint: 'xs' | 'sm' | 'md' | 'lg' | 'xl'
```

### CSS Usage

Use Tailwind responsive prefixes (`sm:`, `md:`, `lg:`, `xl:`) for layout changes. Custom breakpoint queries via `@media (min-width: var(--breakpoint-md))` are **not** valid in standard CSS — use the raw pixel values:

```css
@media (min-width: 768px) { /* md */ }
@media (min-width: 1024px) { /* lg */ }
@media (min-width: 1280px) { /* xl */ }
```

---

## 2. Layout Behavior Per Breakpoint

### 2.1 AppShell

| Element | xs (<480) | sm (480-767) | md (768-1023) | lg (1024-1279) | xl (>=1280) |
|---------|-----------|--------------|---------------|----------------|-------------|
| **Sidebar** | Drawer | Drawer | Drawer | 260px fixed | 260px fixed |
| **Top bar** | Minimal | Compact | Full | Full | Full |
| **Content padding** | 8px | 12px | 16px | 24px | 24px |
| **Sidebar trigger** | Hamburger | Hamburger | Hamburger | Toggle | Toggle |

**Sidebar as Drawer (< 1024px)**:
- Opens from left via hamburger button
- Overlay backdrop (--color-overlay)
- Closes on: route navigation, outside click, Escape key, swipe left
- Width: min(280px, 85vw)
- Z-index: --z-sidebar (100)

### 2.2 Top Bar

| Element | xs (<480) | sm (480-767) | md (768-1023) | lg+ (>=1024) |
|---------|-----------|--------------|---------------|--------------|
| Logo | "S" icon | "SMAP" text | "SMAP" text | "SMAP" text |
| Org/Project switcher | Hidden | Icon only | Truncated | Full |
| Theme toggle | Hidden | Hidden | Icon | Icon |
| Notification bell | Icon | Icon | Icon + badge | Icon + badge |
| User menu | Avatar | Avatar | Avatar + name | Avatar + name |

When hidden elements exist, they are accessible via user menu dropdown (theme toggle, org switcher).

### 2.3 Auth Layout

| Element | xs (<480) | sm+ (>=480) |
|---------|-----------|-------------|
| Card width | 100% - 16px | 420px |
| Card shadow | None | --shadow-md |
| Card padding | 24px 16px | 32px |
| Logo size | 20px | 24px |

---

## 3. Page-Specific Responsive Behavior

### 3.1 Management Pages (Orgs, Projects, Keys, Agents, Admin)

**Table layout**:
- `xl`: full table with all columns visible
- `lg`: table with less-important columns hidden (use `Column.hideBelow: 'lg'`)
- `md`: table with only essential columns (name, status, primary action)
- `< md`: card-list layout — each row becomes a stacked card:

```
┌──────────────────────────┐
│ Icon  Name         Badge │
│       Description        │
│       Meta1 | Meta2      │
│                  [Action]│
└──────────────────────────┘
```

STable component handles this internally:
- Props: `responsiveMode: 'hide-columns' | 'card-list'` (default `'card-list'`)
- `card-list` uses named slot `mobile-card` for custom card layout
- `hide-columns` progressively hides columns based on `Column.hideBelow`

**Form modals**: SModal goes full-screen (`size: 'full'`) below 768px. On mobile, modals render as full-page views with back button instead of close X.

**Action buttons**: stacked vertically on mobile instead of horizontal row.

### 3.2 Chatroom View

The most responsive-critical view:

| Element | xs (<480) | sm (480-767) | md (768-1023) | lg+ (>=1024) |
|---------|-----------|--------------|---------------|--------------|
| **Layout** | Single pane | Single pane | 2-column | 3-column |
| **Message area** | Full width | Full width | Main column | Main column |
| **Presence panel** | Drawer | Drawer | Hidden | 240px sidebar |
| **Agent list** | Drawer | Drawer | Drawer | In presence panel |
| **Composer** | Fixed bottom | Fixed bottom | Fixed bottom | Fixed bottom |
| **Search** | Full overlay | Full overlay | Top panel | Top panel |
| **Header** | Compact | Standard | Standard | Standard |

**Mobile chatroom specifics**:
- Composer sticks to bottom above virtual keyboard (uses `visualViewport` API)
- Message list: full viewport height minus header and composer
- Long-press on message for actions (edit/delete) instead of hover
- Agent thinking: inline indicator below last message
- Attachments: sheet from bottom instead of inline panel
- Swipe right from left edge: opens chatroom list drawer

### 3.3 Workflow Editor

| Breakpoint | Behavior |
|------------|----------|
| `>= 1024px` | Full interactive canvas: drag, connect, config panel, palette |
| `768-1023px` | Read-only canvas: zoom/pan only, no editing. Banner: "Open on desktop to edit" |
| `< 768px` | No canvas. Shows workflow info card + "Open on desktop to edit" message. Can still view runs/backstage. |

**Config panel (SDrawer)**:
- `xl`: 420px width drawer, pushes canvas
- `lg`: 380px width drawer, overlays canvas
- `< lg`: not applicable (read-only)

### 3.4 Key Group Detail (drag-reorder)

- `>= md`: drag handle on left of each key row for reorder
- `< md`: up/down arrow buttons instead of drag handle (touch-friendly)

### 3.5 Admin Views

- `>= lg`: sidebar tabs (vertical) for admin sections + content
- `md`: horizontal tabs at top
- `< md`: dropdown selector for admin sections

---

## 4. Touch Targets

**Minimum size**: 44x44px for all interactive elements (WCAG 2.5.5 AAA).

### Elements requiring touch-target attention

| Element | Solution |
|---------|----------|
| Table row actions | Icon buttons with 44px hit area (padding extends beyond icon) |
| Sidebar nav items | 40px height with full-width click area |
| Close buttons (modal X) | 44x44px hit area despite 20px visible icon |
| Dropdown items | 40px min-height |
| Checkbox/radio | 44x44px hit area (label clickable) |
| Breadcrumb links | 32px min-height, but acceptable (not primary action) |
| Pagination buttons | 40x40px min |
| Tab items | 40px height, 16px horizontal padding |

### Implementation

Use padding and `min-width`/`min-height` to achieve touch targets without visual bloat:

```css
.touch-target {
  position: relative;
  min-width: var(--touch-min);
  min-height: var(--touch-min);
}

/* Expand small icons to 44px hit area via pseudo-element */
.icon-button::after {
  content: '';
  position: absolute;
  inset: -8px; /* extends 8px beyond the 28px icon */
}
```

---

## 5. WCAG 2.1 AA Compliance

### 5.1 Color Contrast

**Minimum ratios**:
- Normal text (< 18px or < 14px bold): 4.5:1
- Large text (>= 18px or >= 14px bold): 3:1
- UI components and graphical objects: 3:1

**Verified pairs (light theme)**:

| Foreground | Background | Ratio | Pass |
|------------|------------|-------|------|
| `#1f2328` (fg) | `#ffffff` (bg) | 15.4:1 | AA |
| `#6b7280` (muted) | `#ffffff` (bg) | 5.0:1 | AA |
| `#2563eb` (accent) | `#ffffff` (bg) | 4.6:1 | AA |
| `#ffffff` (white) | `#2563eb` (accent btn) | 4.6:1 | AA |
| `#dc2626` (danger) | `#ffffff` (bg) | 4.6:1 | AA |
| `#374151` (sidebar text) | `#f1f5f9` (sidebar bg) | 7.1:1 | AA |
| `#1d4ed8` (active) | `#dbeafe` (active bg) | 4.9:1 | AA |

**Dark theme pairs**: verified separately; `--color-muted` (#9ca3af) on `--color-bg` (#0b0f14) = 8.1:1.

**Badge tint pairs**: all tint/on combinations achieve >= 4.5:1 by design.

### 5.2 Focus Management

**Focus visible**: every interactive element shows a visible focus indicator.

```css
/* Applied globally via focus-visible (keyboard only, not mouse) */
:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

/* High-contrast mode override */
@media (forced-colors: active) {
  :focus-visible {
    outline: 2px solid LinkText;
    outline-offset: 2px;
  }
}
```

**Focus ring**: 2px white gap + 2px accent color ring. Visible on all backgrounds.

**Focus trap**: modals and drawers trap focus within their content. Tab cycles through focusable elements. Shift+Tab cycles backward. First focusable element focused on open. Focus returns to trigger element on close.

**Skip link**: hidden "Skip to main content" link at the top of AppShell, visible on focus, jumps to content area.

### 5.3 Keyboard Navigation

**Global shortcuts**:

| Key | Action |
|-----|--------|
| `Tab` / `Shift+Tab` | Navigate focusable elements |
| `Escape` | Close modal/drawer/dropdown, cancel edit |
| `/` | Focus search input (if visible on page) |

**Component-specific keyboard**:

| Component | Keys |
|-----------|------|
| SDropdown | `Enter`/`Space` open, `Arrow Up`/`Down` navigate, `Enter` select, `Escape` close |
| STabs | `Arrow Left`/`Right` switch tabs |
| SModal | `Escape` close, `Tab` cycles within |
| STable | `Arrow Up`/`Down` navigate rows (when selectable) |
| SAccordion | `Enter`/`Space` toggle, `Arrow Up`/`Down` navigate headers |
| SSelect | `Arrow Up`/`Down` navigate options, `Enter` select |
| Workflow editor | See 08-workflow.md section 2.8 |

### 5.4 ARIA Attributes

**Required ARIA on components**:

| Component | ARIA |
|-----------|------|
| SButton (loading) | `aria-disabled="true"`, `aria-busy="true"` |
| SInput | `aria-invalid` when error, `aria-describedby` pointing to error/help text |
| SModal | `role="dialog"`, `aria-modal="true"`, `aria-labelledby` (title) |
| SDrawer | `role="dialog"`, `aria-modal="true"`, `aria-labelledby` (title) |
| SDropdown | `role="menu"`, items `role="menuitem"`, trigger `aria-haspopup="true"`, `aria-expanded` |
| STabs | `role="tablist"`, tabs `role="tab"`, panels `role="tabpanel"`, `aria-selected` |
| STable | Implicit via `<table>` semantics; `aria-sort` on sortable headers |
| SAlert | `role="alert"` for danger/warning, `role="status"` for info/success |
| NotificationBell | `aria-label` with unread count, badge `aria-live="polite"` |
| SProgressBar | `role="progressbar"`, `aria-valuenow`, `aria-valuemin="0"`, `aria-valuemax="100"` |
| SToggle | `role="switch"`, `aria-checked` |
| Chat message list | `aria-live="polite"` on new message region |
| Typing indicator | `aria-live="polite"`, `aria-atomic="true"` |
| SBreadcrumb | `nav` with `aria-label="Breadcrumb"`, list `role="list"` |

### 5.5 Screen Reader Considerations

- **Loading states**: `aria-busy="true"` on container, `role="status"` on spinner with `aria-label`
- **Empty states**: descriptive text (not just icon)
- **Toast notifications**: `role="status"` with `aria-live="polite"`; danger toasts use `aria-live="assertive"`
- **Agent streaming**: `aria-live="polite"` on streaming container (batched to avoid excessive announcements — update every 2 seconds)
- **Route changes**: announce page title on navigation via `document.title` update and `aria-live` region
- **Form errors**: `aria-describedby` links input to error message, `aria-invalid="true"` on errored field
- **Confirmation dialogs**: focus moves to confirm button on open, auto-read title and message

### 5.6 Motion & Reduced Motion

Respect `prefers-reduced-motion`:

```css
@media (prefers-reduced-motion: reduce) {
  * {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

Affected animations:
- Sidebar slide
- Modal enter/exit
- Drawer slide
- Dropdown open
- Skeleton pulse
- Progress bar animation
- Agent streaming cursor blink
- Workflow edge flow animation
- Toast enter/exit

---

## 6. Browser Support

| Browser | Minimum Version |
|---------|-----------------|
| Chrome | Last 2 stable |
| Edge | Last 2 stable |
| Firefox | Last 2 stable |
| Safari | Last 2 stable |
| iOS Safari | 16.2+ |
| Chrome Android | 110+ |

**Feature requirements** (all supported in target range):
- CSS custom properties
- CSS Grid, Flexbox
- `color-mix()` (Chrome 111+, Safari 16.2+)
- `@layer` (Chrome 99+, Safari 15.4+)
- `focus-visible` pseudo-class
- `visualViewport` API (for mobile keyboard handling)
- ResizeObserver
- IntersectionObserver

---

## 7. Testing Strategy

### 7.1 Automated

- **ESLint gate #11**: `eslint-plugin-vuejs-accessibility` enforced in CI. Rules:
  - `label-has-associated-control`
  - `no-autofocus` (except modals)
  - `click-events-have-key-events`
  - `interactive-supports-focus`
  - `no-noninteractive-element-interactions`
  - `heading-order`

- **Axe-core smoke**: per top-level view in Vitest. Renders the view with MSW mocks and runs `axe(container)`. Fail on any violation with impact >= "serious".

- **Playwright viewport tests**: E2E golden-path specs run at 3 viewports:
  - Desktop: 1440x900
  - Tablet: 768x1024
  - Mobile: 375x812 (iPhone)

### 7.2 Manual Checklist (per view)

- [ ] Tab through all interactive elements in order
- [ ] Verify focus ring visible on every focusable element
- [ ] Test with screen reader (NVDA or VoiceOver)
- [ ] Verify at 200% zoom (no content overflow or overlap)
- [ ] Test with `prefers-reduced-motion: reduce`
- [ ] Test with high-contrast mode (Windows)
- [ ] Verify touch targets >= 44px on mobile
- [ ] Check color contrast with browser DevTools audit

---

## 8. Responsive Component Quick Reference

| Component | xs | sm | md | lg | xl |
|-----------|----|----|----|----|----|
| STable | Card list | Card list | Table (reduced) | Table (full) | Table (full) |
| SModal | Full screen | Full screen | Centered | Centered | Centered |
| SDrawer | Full width | 85vw | 420px | 420px | 420px |
| SPageHeader | Stacked | Stacked | Inline | Inline | Inline |
| STabs | Scrollable | Scrollable | Fixed | Fixed | Fixed |
| SPagination | Compact | Compact | Full | Full | Full |
| STable bulk actions | Bottom sheet | Bottom sheet | Inline toolbar | Inline toolbar | Inline toolbar |
