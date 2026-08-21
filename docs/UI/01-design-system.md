# 01 — Design System

> Component library specification for SMAP.
> All components live in `src/shared/ui/` and are importable by any slice.

---

## 1. Design Tokens (CSS Custom Properties)

Tokens are defined in `src/shared/styles/main.css` under `@theme` and `:root`, and
[R24.29] requires component styling to consume them rather than restate their values.

**This section is the only place in `docs/UI/` where a size appears as a number.**
Everywhere else, including every component spec below, sizing is written as a token
name. A second copy of the number is a second source of truth, and the document is the
one an implementer reads, so a spec written in pixels is an instruction to hard-code
pixels. Adding a literal size to a component spec below will fail
`src/shared/styles/__tests__/no-raw-style-literals.test.ts` the moment somebody
implements it.

Four classes of size stay literal in the specs below, deliberately: **border widths**,
**icon boxes** (the glyph inside a control, an avatar, a spinner, a progress bar's
thickness), **touch-target floors** (44px, an accessibility minimum that must *not* move
when the scale is retuned), and **overlay width presets** (modal and drawer sizes). Each
is geometry local to one component rather than a shared vocabulary, and a token with a
single consumer is an indirection nobody will ever use. Any size outside those four
classes that appears as a number below is a mistake.

### Type ramp

| Token | Value | px |
|-------|-------|----|
| `--font-size-2xs` | `0.625rem` | 10 |
| `--font-size-xs` | `0.75rem` | 12 |
| `--font-size-code` | `0.8125rem` | 13 |
| `--font-size-sm` | `0.875rem` | 14 |
| `--font-size-md` | `1rem` | 16 |
| `--font-size-lg` | `1.125rem` | 18 |
| `--font-size-xl` | `1.25rem` | 20 |
| `--font-size-2xl` | `1.5rem` | 24 |
| `--font-size-3xl` | `1.875rem` | 30 |

`--line-none: 1`, `--line-tight: 1.3`, `--line-snug: 1.4`, `--line-normal: 1.5`,
`--line-relaxed: 1.6`.
`--weight-normal: 400`, `--weight-medium: 500`, `--weight-semibold: 600`,
`--weight-bold: 700`.

### Spacing scale

4px grid, with three half-steps the component library uses as inner padding against an
outer padding one whole step larger.

| Token | px | Common use |
|-------|----|------------|
| `--space-0-5` | 2 | Chip and inline-badge padding |
| `--space-1` | 4 | Tight gaps, label-to-control |
| `--space-1-5` | 6 | Compact control padding |
| `--space-2` | 8 | Input padding, icon gaps |
| `--space-2-5` | 10 | Large-control vertical padding |
| `--space-3` | 12 | Card inner padding (compact) |
| `--space-4` | 16 | Standard content padding |
| `--space-5` | 20 | Section gaps |
| `--space-6` | 24 | Card padding (standard) |
| `--space-8` | 32 | Section margins |
| `--space-10` | 40 | Page margins |
| `--space-12` | 48 | Large spacing |

### Control heights

`--control-h-sm: 32px`, `--control-h-md: 40px`, `--control-h-lg: 48px`. Every control
that can sit on a form row with a button reads these: SButton, SInput, SSelect,
SSearchInput, STabs, SPagination and the top bar's sidebar toggle. A control that does
not follow the ladder will be a different height from the button beside it the first
time the ladder moves.

### Semantic elevation

`--elevation-0: none` (flush, border only), `--elevation-1` (resting card),
`--elevation-2` (raised or hover), `--elevation-3` (overlay). Components reach for depth
by meaning; the underlying `--shadow-*` values already switch per theme.

### Colour, radius, motion and layering

```css
@theme {
  /* Sidebar */
  --color-sidebar-bg: #f1f5f9;
  --color-sidebar-hover: #dbeafe;
  --color-sidebar-active-bg: #dbeafe;
  --color-sidebar-active-text: #1d4ed8;
  --color-sidebar-text: #374151;
  --color-sidebar-section-text: #6b7280;

  /* Accent hover */
  --color-accent-hover: #1d4ed8;

  /* Elevation shadows */
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.05);
  --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -4px rgba(0, 0, 0, 0.1);
  --shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);

  /* Overlay */
  --color-overlay: rgba(0, 0, 0, 0.45);

  /* Additional radii */
  --radius-sm: 4px;
  --radius-lg: 8px;
  --radius-xl: 12px;

  /* Sidebar width */
  --sidebar-width: 260px;
  --sidebar-collapsed-width: 0px;

  /* Top bar height */
  --topbar-height: 56px;

  /* Transitions */
  --transition-fast: 150ms ease;
  --transition-normal: 200ms ease;
  --transition-slow: 300ms ease;

  /* Z-index layers */
  --z-sidebar: 100;
  --z-topbar: 200;
  --z-dropdown: 300;
  --z-modal: 400;
  --z-toast: 500;
  --z-tooltip: 600;

  /* Focus ring */
  --focus-ring: 0 0 0 2px var(--color-bg), 0 0 0 4px var(--color-accent);
}
```

### Dark Theme Additions

```css
:root[data-theme="dark"] {
  --color-sidebar-bg: #111827;
  --color-sidebar-hover: #1e3a5f;
  --color-sidebar-active-bg: #1e3a5f;
  --color-sidebar-active-text: #93c5fd;
  --color-sidebar-text: #d1d5db;
  --color-sidebar-section-text: #9ca3af;
  --color-accent-hover: #93c5fd;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.2);
  --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
  --shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.3);
  --color-overlay: rgba(0, 0, 0, 0.65);
  --focus-ring: 0 0 0 2px var(--color-bg), 0 0 0 4px var(--color-accent);
}
```

---

## 2. Component Catalog

Components are organized in three tiers:

- **Atoms**: single-purpose primitives (button, input, badge)
- **Molecules**: composed atoms (form field, search input, file upload)
- **Organisms**: complex multi-atom compositions (data table, modal, drawer)

All components:
- Accept props for customization; no hardcoded text (use `$t()` or slot content)
- Emit typed events
- Support dark theme via CSS custom properties (no conditional class logic)
- Meet WCAG 2.1 AA (focus visible, ARIA labels, keyboard nav)
- Min touch target 44x44px on interactive elements

---

## 3. Atoms

### 3.1 SButton

**File**: `src/shared/ui/SButton.vue`

**Props**:

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `variant` | `'primary' \| 'secondary' \| 'danger' \| 'ghost' \| 'link'` | `'secondary'` | Visual style |
| `size` | `'sm' \| 'md' \| 'lg'` | `'md'` | Size preset |
| `disabled` | `boolean` | `false` | Disabled state |
| `loading` | `boolean` | `false` | Shows spinner, disables interaction |
| `iconOnly` | `boolean` | `false` | Square button for icon-only use |
| `type` | `'button' \| 'submit' \| 'reset'` | `'button'` | Native button type |
| `as` | `'button' \| 'a' \| 'router-link'` | `'button'` | Rendered element |
| `to` | `RouteLocationRaw` | — | Router-link target (when `as='router-link'`) |

**Slots**: `default` (label content), `icon-left`, `icon-right`

**Sizes**:

| Size | Height | Padding | Font |
|------|--------|---------|------|
| `sm` | `--control-h-sm` | `--space-1-5` `--space-3` | `--font-size-xs` |
| `md` | `--control-h-md` | `--space-2` `--space-4` | `--font-size-sm` |
| `lg` | `--control-h-lg` | `--space-2-5` `--space-6` | `--font-size-md` |

`iconOnly` makes the button square at the same rung, so its width is the same
`--control-h-*` token as its height.

**Visual spec**:
- `primary`: `--color-accent` bg, white text, `--color-accent-hover` on hover
- `secondary`: `--color-surface` bg, `--color-fg` text, `--color-border` border
- `danger`: `--color-danger` bg, white text
- `ghost`: transparent bg, `--color-fg` text, no border; hover shows `--color-surface`
- `link`: transparent bg, `--color-accent` text, underline on hover; no min-height
- Focus: `--focus-ring` box-shadow
- Loading: spinner replaces `icon-left`; text dimmed to 60% opacity

---

### 3.2 SInput

**File**: `src/shared/ui/SInput.vue`

**Props**:

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `modelValue` | `string \| number` | — | v-model binding |
| `type` | `'text' \| 'password' \| 'email' \| 'number' \| 'url'` | `'text'` | Input type |
| `placeholder` | `string` | — | Placeholder text |
| `disabled` | `boolean` | `false` | Disabled state |
| `error` | `boolean` | `false` | Error visual state |
| `size` | `'sm' \| 'md'` | `'md'` | Size preset |

**Slots**: `prefix` (icon/text before input), `suffix` (icon/text after input)

**Visual spec**:
- Height: `sm` `--control-h-sm`, `md` `--control-h-md`
- Border: 1px `--color-border`; focus: 2px `--color-accent`
- Error: border `--color-danger`, focus ring `--color-danger`
- Password type: eye toggle icon in suffix slot, sized to `--control-h-sm` so it fits
  every input size, with a 44px touch floor and negative margins that let it overhang
  the field's padding box
- Padding: `--space-2` horizontal, adjusted for prefix/suffix
- Font: `--font-size-sm`, dropping to `--font-size-xs` at `sm`

---

### 3.3 SSelect

**File**: `src/shared/ui/SSelect.vue`

**Props**:

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `modelValue` | `string \| number \| null` | — | Selected value |
| `options` | `Array<{value, label, disabled?}>` | `[]` | Options list |
| `placeholder` | `string` | — | Unselected placeholder |
| `disabled` | `boolean` | `false` | Disabled state |
| `error` | `boolean` | `false` | Error visual state |
| `size` | `'sm' \| 'md'` | `'md'` | Size preset |

**Visual spec**: same border/focus/error treatment as SInput, including the `--control-h-sm`/`--control-h-md` heights and the `--font-size-xs` drop at `sm`. Chevron-down icon on right. Native `<select>` for accessibility; custom dropdown styling via CSS.

---

### 3.4 SCheckbox

**File**: `src/shared/ui/SCheckbox.vue`

**Props**: `modelValue: boolean`, `disabled: boolean`, `indeterminate: boolean`

**Slots**: `default` (label)

**Visual spec**: 18x18 checkbox, `--color-accent` fill when checked, `--color-border` border when unchecked. Label inline to the right with a `--space-2` gap.

---

### 3.5 SRadio

**File**: `src/shared/ui/SRadio.vue`

**Props**: `modelValue: string`, `value: string`, `disabled: boolean`, `name: string`

**Slots**: `default` (label)

**Visual spec**: 18px circle, `--color-accent` fill dot when selected.

---

### 3.6 STextarea

**File**: `src/shared/ui/STextarea.vue`

**Props**: `modelValue: string`, `placeholder: string`, `rows: number` (default 3), `disabled: boolean`, `error: boolean`, `resize: 'none' | 'vertical' | 'both'` (default `'vertical'`)

**Visual spec**: same border treatment as SInput. Min-height based on rows.

---

### 3.7 SToggle

**File**: `src/shared/ui/SToggle.vue`

**Props**: `modelValue: boolean`, `disabled: boolean`, `size: 'sm' | 'md'`

**Slots**: `default` (label)

**Visual spec**: pill-shaped track (36x20 `md`, 28x16 `sm`). Off: `--color-border` track, white knob. On: `--color-accent` track. Transition 150ms.

---

### 3.8 SBadge

**File**: `src/shared/ui/SBadge.vue`

**Props**:

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `variant` | `'info' \| 'success' \| 'warning' \| 'danger' \| 'neutral'` | `'neutral'` | Color scheme |
| `size` | `'sm' \| 'md'` | `'md'` | Size |
| `dot` | `boolean` | `false` | Show colored dot before text |
| `removable` | `boolean` | `false` | Show X button, emits `remove` |

**Visual spec**: pill shape (`--radius-full`). Background from `*-tint` tokens, text from `*-on` tokens. `sm`: 20px height, `--font-size-2xs`. `md`: 24px height, `--font-size-xs`. Both at `--line-none`, so the pill hugs the glyph.

---

### 3.9 SAvatar

**File**: `src/shared/ui/SAvatar.vue`

**Props**: `name: string`, `size: 'sm' | 'md' | 'lg'` (24/32/40px), `src: string | null`

**Visual spec**: circle. If `src` provided, show image. Otherwise, show initials (first letter of name, uppercase) on `--color-accent` background with white text. Sizes: `sm` 24px, `md` 32px, `lg` 40px.

---

### 3.10 SDivider

**File**: `src/shared/ui/SDivider.vue`

**Props**: `orientation: 'horizontal' | 'vertical'` (default `'horizontal'`), `label: string | undefined`

**Visual spec**: 1px `--color-border` line. If label provided, centered text with line on both sides, `--color-muted` at `--font-size-xs`.

---

### 3.11 SProgressBar

**File**: `src/shared/ui/SProgressBar.vue`

**Props**: `value: number` (0-100), `variant: 'info' | 'success' | 'warning' | 'danger'` (default `'info'`), `indeterminate: boolean` (default `false`), `size: 'sm' | 'md'` (4px / 8px height)

**Visual spec**: rounded track on `--color-neutral-tint`. Fill uses variant accent color. Indeterminate: sliding animation.

---

### 3.12 STooltip

**File**: `src/shared/ui/STooltip.vue`

**Props**: `content: string`, `placement: 'top' | 'bottom' | 'left' | 'right'` (default `'top'`), `delay: number` (default 300ms)

**Slots**: `default` (trigger element)

**Visual spec**: dark tooltip (`--color-fg` bg for light theme, `--color-surface` bg for dark theme), white text, `--font-size-xs`, `--space-1` `--space-2` padding, `--radius-sm` corners, `--shadow-md`. Arrow pointing to trigger. Appears on hover after delay, on focus immediately.

---

## 4. Molecules

### 4.1 SFormField

**File**: `src/shared/ui/SFormField.vue` (already exists — extend)

**Props**:

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `label` | `string` | — | Field label |
| `name` | `string` | — | Field identifier — maps to label `for`, and generates ARIA IDs (`{name}-error`, `{name}-help`) |
| `error` | `string \| undefined` | — | Error message |
| `help` | `string \| undefined` | — | Help text below input |
| `required` | `boolean` | `false` | Shows required indicator |

**Slots**: `default` (the input component)

**Visual spec**: label (`--font-size-sm`, `--weight-medium`) above input. Required: red asterisk after label. Error: `--color-danger` message below input, input border turns danger. Help: `--color-muted` at `--font-size-xs` below input (hidden when error shown). Vertical gap: `--space-1` between label and input, `--space-1` between input and error/help.

---

### 4.2 SSearchInput

**File**: `src/shared/ui/SSearchInput.vue`

**Props**: `modelValue: string`, `placeholder: string`, `loading: boolean`

**Emits**: `update:modelValue`, `search` (on Enter or debounced 300ms), `clear`

**Visual spec**: SInput with `MagnifyingGlassIcon` prefix, at `--control-h-md` so it lines up with a `md` button beside it. When non-empty, `XMarkIcon` suffix button to clear. When loading, spinner replaces search icon.

---

### 4.3 SFileUpload

**File**: `src/shared/ui/SFileUpload.vue`

**Props**: `accept: string` (MIME types), `maxSize: number` (bytes), `multiple: boolean`, `disabled: boolean`

**Emits**: `files(File[])`, `error(string)`

**Slots**: `default` (custom dropzone content)

**Visual spec**: dashed border 2px `--color-border` rounded box, 120px min-height. `ArrowUpTrayIcon` centered, "Drop files here or click to browse" text. Drag-over: border turns `--color-accent`, background `--color-sidebar-hover`. Error state: border `--color-danger`. Accepted files listed below with name, size, remove button.

---

### 4.4 SCodeEditor

**File**: `src/shared/ui/SCodeEditor.vue`

**Props**: `modelValue: string`, `placeholder: string`, `language: 'json' | 'yaml' | 'markdown' | 'text'`, `rows: number` (default 8), `readonly: boolean`

**Visual spec**: monospace textarea at `--font-size-code`, with line numbers gutter (optional). `--color-surface` background. Tab key inserts 2 spaces. Wrapping: soft wrap by default.

---

### 4.5 SBreadcrumb

**File**: `src/shared/ui/SBreadcrumb.vue`

**Props**: `items: Array<{label: string, to?: RouteLocationRaw}>`

**Visual spec**: inline flex with `ChevronRightIcon` (12px) separators. Last item is plain text (current page). Previous items are `--color-accent` links. Font: `--font-size-sm`. Truncates middle items on overflow with "..." item.

---

## 5. Organisms

### 5.1 STable

**File**: `src/shared/ui/STable.vue`

**Props**:

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `columns` | `Column[]` | — | Column definitions |
| `data` | `any[]` | `[]` | Row data |
| `loading` | `boolean` | `false` | Shows skeleton rows |
| `emptyTitle` | `string` | — | Empty state heading |
| `emptyDescription` | `string` | — | Empty state description |
| `sortBy` | `string` | — | Current sort column key |
| `sortOrder` | `'asc' \| 'desc'` | `'asc'` | Sort direction |
| `selectable` | `boolean` | `false` | Row checkboxes |
| `selected` | `any[]` | `[]` | Selected row keys |
| `stickyHeader` | `boolean` | `false` | Sticky thead |

**Column type**:
```ts
interface Column {
  key: string
  label: string
  sortable?: boolean
  width?: string
  align?: 'left' | 'center' | 'right'
}
```

**Slots**: `cell-{key}` (custom cell renderer), `actions` (row action buttons), `empty` (custom empty state), `bulk-actions` (toolbar when rows selected)

**Emits**: `sort(key, order)`, `select(keys[])`, `row-click(row)`

**Visual spec**:
- Header: `--color-surface` bg, `--weight-semibold`, `--font-size-xs` uppercase text, `--color-muted`; cells `--space-2` `--space-3`
- Rows: white bg at `--font-size-sm`, 1px bottom `--color-border`, hover `--color-surface`
- Sortable columns: clickable header with `ChevronUpDownIcon`, active shows `ChevronUpIcon`/`ChevronDownIcon` in `--color-accent`
- Loading: 5 skeleton rows with pulse animation
- Empty: `SEmptyState` centered in table body
- Selectable: checkbox column on left, header checkbox for select-all
- Row click: pointer cursor, subtle highlight

---

### 5.2 SModal

**File**: `src/shared/ui/SModal.vue`

**Props**:

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `open` | `boolean` | `false` | Visibility |
| `title` | `string` | — | Header title |
| `size` | `'sm' \| 'md' \| 'lg' \| 'xl' \| 'full'` | `'md'` | Width preset |
| `closable` | `boolean` | `true` | Show X button, close on Escape |
| `persistent` | `boolean` | `false` | Prevent close on backdrop click |

**Slots**: `default` (body), `footer` (action buttons), `header` (custom header)

**Emits**: `close`

**Sizes** (width presets, not on the spacing ladder): `sm` 400px, `md` 560px, `lg` 720px, `xl` 960px, `full` 100vw minus two `--space-6` gutters

**Visual spec**:
- Backdrop: `--color-overlay`, transition opacity 200ms
- Panel: white bg, `--radius-lg` corners, `--shadow-xl`
- Header: title at `--font-size-xl` `--weight-semibold`, X button top-right (24px icon, `--color-muted`, hover `--color-fg`)
- Body: padding `--space-6`, max-height 70vh, overflow-y auto
- Footer: padding `--space-4` `--space-6`, flex end, gap `--space-2`, top border `--color-border`
- Enter: scale(0.95) -> scale(1), opacity 0 -> 1, 200ms
- Escape key closes (unless `persistent`)
- Focus trap: tab cycles within modal
- Scroll lock on body when open

---

### 5.3 SDrawer

**File**: `src/shared/ui/SDrawer.vue`

**Props**:

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `open` | `boolean` | `false` | Visibility |
| `title` | `string` | — | Header title |
| `side` | `'left' \| 'right'` | `'right'` | Slide direction |
| `size` | `'sm' \| 'md' \| 'lg'` | `'md'` | Width preset |

**Slots**: `default` (body), `footer`

**Emits**: `close`

**Sizes**: `sm` 320px, `md` 420px, `lg` 560px

**Visual spec**: slides from side over backdrop. Same header/footer treatment as SModal. Transform: `translateX(100%)` -> `translateX(0)`, 300ms ease. Full height.

---

### 5.4 STabs

**File**: `src/shared/ui/STabs.vue`

**Props**: `modelValue: string`, `tabs: Array<{key: string, label: string, icon?: Component, badge?: string | number, disabled?: boolean}>`

**Slots**: `tab-{key}` (tab panel content)

**Emits**: `update:modelValue`

**Visual spec**: horizontal tab bar with bottom border. Active tab: `--color-accent` text, 2px bottom border `--color-accent`. Inactive: `--color-muted` text. Hover: `--color-fg` text. Badge: `SBadge` inline after label. Icon: 16px before label. Tab height: `--control-h-md`, so a tab strip lines up with a `md` control beside it. Padding: 0 `--space-4` per tab.

---

### 5.5 SDropdown

**File**: `src/shared/ui/SDropdown.vue`

**Props**: `items: Array<{key, label, icon?, danger?, disabled?, divider?}>`, `placement: 'bottom-start' | 'bottom-end'` (default `'bottom-end'`), `width: string` (default `'auto'`, min 180px)

**Slots**: `trigger` (button that opens dropdown)

**Emits**: `select(key)`

**Visual spec**: white bg, `--shadow-lg`, `--radius-md`, 1px `--color-border`. Items: 36px height, `--space-3` `--space-4` padding at `--font-size-sm`, hover `--color-surface`. Danger items: `--color-danger` text. Divider: 1px `--color-border` horizontal line. Icons: 16px, `--color-muted`. Opens on click, closes on outside click, Escape, or item select. Keyboard: arrow keys navigate, Enter selects.

---

### 5.6 SPagination

**File**: `src/shared/ui/SPagination.vue`

**Props**: `page: number`, `totalPages: number`, `totalItems: number`, `pageSize: number`

**Emits**: `update:page`

**Visual spec**: "Showing X-Y of Z" text on left at `--font-size-sm`. Page buttons on right, each `--control-h-sm` square so the row lines up with a `sm` button: Previous, page numbers (collapsed with ... for >7 pages), Next. Active page: `--color-accent` bg, white text. Disabled prev/next: 50% opacity.

---

### 5.7 SAccordion

**File**: `src/shared/ui/SAccordion.vue`

**Props**: `items: Array<{key, title, defaultOpen?}>`, `multiple: boolean` (default `false`)

**Slots**: `item-{key}` (panel content), `header-{key}` (custom header)

**Visual spec**: each item has 1px bottom `--color-border`. Header: 44px touch-floor height at `--font-size-sm` `--weight-medium`, `ChevronRightIcon` that rotates 90deg when open, 200ms transition. Panel: padding `--space-4`, slide-down animation.

---

### 5.8 SAlert

**File**: `src/shared/ui/SAlert.vue`

**Props**: `variant: 'info' | 'success' | 'warning' | 'danger'`, `title: string`, `dismissible: boolean` (default `false`)

**Slots**: `default` (description text), `actions`

**Emits**: `dismiss`

**Visual spec**: full-width banner. Background from `*-tint` tokens, left border 4px from variant color, text from `*-on` tokens. Icon: variant-specific Heroicon (InformationCircle, CheckCircle, ExclamationTriangle, XCircle). Padding: `--space-3` `--space-4`; title at `--weight-semibold`, body at `--weight-normal`. Dismiss: X button top-right.

---

### 5.9 SSkeleton

**File**: `src/shared/ui/SSkeleton.vue`

**Props**: `variant: 'text' | 'circle' | 'rect'`, `width: string`, `height: string`, `lines: number` (for `text` variant, default 1)

**Visual spec**: `--color-neutral-tint` background with pulse animation (opacity 0.4 -> 1 -> 0.4, 1.5s). Text: rounded rect 60-100% width per line. Circle: full border-radius. Rect: `--radius-md`.

---

## 6. Existing Components — Upgrade Notes

### SCard (exists)
- Add `variant` prop: `'default' | 'elevated' | 'bordered' | 'flat'`
- Add `padding` prop: `'none' | 'sm' | 'md' | 'lg'` (default `'md'`): `--space-3` / `--space-4` / `--space-6`
- `elevated`: `--elevation-1` + white bg
- `bordered`: 1px `--color-border` + white bg
- `flat`: `--color-surface` bg, no border

### SPageHeader (exists)
- Add breadcrumb support: `breadcrumbs` prop
- Add action buttons slot: `actions`
- Add description slot: `description`
- Layout: title + description on left, action buttons on right, breadcrumbs above title. Title: `--font-size-2xl` `--weight-semibold` at `--line-snug`; description `--font-size-sm`

### SEmptyState (exists)
- Add `icon` prop (Heroicon component)
- Add `action` slot for CTA button
- Layout: icon (48px, `--color-muted`) above title above description above action. Centered. Max-width 400px. Title `--font-size-md` `--weight-semibold`, description `--font-size-sm` at `--line-normal`.

### SStatusBadge (exists)
- Migrate to use `SBadge` internally, keeping the semantic status mapping

### SConfirmDialog (exists)
- Migrate to use `SModal` internally
- Add `variant` support for destructive confirmation (danger style)

### SLoadingSpinner (exists)
- Add `size` prop: `'sm' | 'md' | 'lg'` (16/24/32px)
- Add `label` prop for accessible text

### ThemeToggle (exists)
- Restyle as icon button in top bar (SunIcon / MoonIcon / ComputerDesktopIcon)
- Remove from fixed bottom-right position

---

## 7. Component Dependencies

```
SFormField
  └── uses: SInput | SSelect | STextarea | SCheckbox | SRadio | SToggle

SSearchInput
  └── uses: SInput, MagnifyingGlassIcon, XMarkIcon

SFileUpload
  └── uses: SButton, SProgressBar, ArrowUpTrayIcon

STable
  └── uses: SCheckbox, SSkeleton, SEmptyState, SPagination, SDropdown

SModal
  └── uses: SButton (close), XMarkIcon

SDrawer
  └── uses: SButton (close), XMarkIcon

SConfirmDialog (upgraded)
  └── uses: SModal, SButton

SPageHeader (upgraded)
  └── uses: SBreadcrumb
```

---

## 8. Component File Inventory

### New files to create

| File | Tier | Priority |
|------|------|----------|
| `SButton.vue` | Atom | Phase U1 |
| `SInput.vue` | Atom | Phase U1 |
| `SSelect.vue` | Atom | Phase U1 |
| `SCheckbox.vue` | Atom | Phase U1 |
| `SRadio.vue` | Atom | Phase U1 |
| `STextarea.vue` | Atom | Phase U1 |
| `SToggle.vue` | Atom | Phase U1 |
| `SBadge.vue` | Atom | Phase U1 |
| `SAvatar.vue` | Atom | Phase U1 |
| `SDivider.vue` | Atom | Phase U1 |
| `SProgressBar.vue` | Atom | Phase U1 |
| `STooltip.vue` | Atom | Phase U1 |
| `SSearchInput.vue` | Molecule | Phase U1 |
| `SFileUpload.vue` | Molecule | Phase U1 |
| `SCodeEditor.vue` | Molecule | Phase U1 |
| `SBreadcrumb.vue` | Molecule | Phase U1 |
| `STable.vue` | Organism | Phase U1 |
| `SModal.vue` | Organism | Phase U1 |
| `SDrawer.vue` | Organism | Phase U1 |
| `STabs.vue` | Organism | Phase U1 |
| `SDropdown.vue` | Organism | Phase U1 |
| `SPagination.vue` | Organism | Phase U1 |
| `SAccordion.vue` | Organism | Phase U1 |
| `SAlert.vue` | Organism | Phase U1 |
| `SSkeleton.vue` | Atom | Phase U1 |

### Existing files to modify

| File | Changes |
|------|---------|
| `SCard.vue` | Add `variant`, `padding` props |
| `SPageHeader.vue` | Add `breadcrumbs`, `actions` slot, `description` slot |
| `SEmptyState.vue` | Add `icon` prop, `action` slot |
| `SStatusBadge.vue` | Refactor to use `SBadge` |
| `SConfirmDialog.vue` | Refactor to use `SModal` |
| `SLoadingSpinner.vue` | Add `size`, `label` props |
| `ThemeToggle.vue` | Restyle as icon button |
| `statusColors.ts` | Extend with new semantic tint pairs |
| `index.ts` | Re-export all new components |

---

## 9. CSS Class Strategy

All shared components use **scoped styles** exclusively. No global CSS classes for component internals.

The existing `.btn`, `.btn-primary`, `.btn-danger`, `.btn-sm`, `.form-page`, `.table` classes in `main.css` remain for backward compatibility during migration. Once all views use `SButton` and `STable`, these global classes will be removed.

New utility classes in `main.css` (Tailwind utilities handle most cases, but these semantic shortcuts are useful):

```css
@layer components {
  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  .truncate-line {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
```
