# 10 — Notifications

> In-app notification system: bell badge with real-time unread count, full
> notification list with kind-specific cards, individual and bulk mark-read,
> cursor-paginated history. Real-time delivery over the shared
> `/ws/user/{userId}` WebSocket channel. No email, webhook, or Slack
> integrations in v1.

---

## 1. NotificationBell Component

**File**: `src/slices/notifications/components/NotificationBell.vue`

The bell lives in `AppTopBar.vue` (right zone, between the help link and the
user avatar menu). It is the primary entry point to the notification system.

### 1.1 Wireframe

```
Desktop (top bar right zone):

   [?]   [ bell-icon (3) ]   [A]   [T]
           ^            ^
           |            +-- red badge, overlaps top-right
           +-- BellIcon 22x22

Badge states:

   (a) No unread        (b) 1-99 unread      (c) 100+ unread
   ┌─────────┐          ┌─────────┐          ┌─────────┐
   │         │          │      ┌─┐│          │    ┌──┐ │
   │  [bell] │          │ [bell│3││          │[bell│99+│
   │         │          │      └─┘│          │    └──┘ │
   └─────────┘          └─────────┘          └─────────┘
   40x40 ghost btn      18px min-w badge     badge stretches
```

### 1.2 Badge Logic

| Condition | Badge | Visual |
|-----------|-------|--------|
| `count === 0` | Hidden | Bell icon only, no badge |
| `1 <= count <= 99` | `String(count)` | Red circle, white text |
| `count >= 100` | `"99+"` | Red pill (wider min-width) |

**Badge styling**:
- Position: `absolute`, top -2px, right -2px
- Background: `--color-danger` (#dc2626)
- Text: white, 0.75rem (12px), font-weight 600, `line-height: 18px`
- Shape: `--radius-full`, min-width 18px, height 18px, padding 0 4px
- Vertically and horizontally centered text

**Bell container**:
- Size: 40x40px (meets 44px touch target via padding)
- Background: `--color-surface`, border 1px `--color-border`, `--radius-full`
- Hover: background `--color-border`
- Focus: `--focus-ring` box-shadow

### 1.3 Real-time Updates

The bell fetches and maintains the unread count through two complementary
mechanisms:

**Primary: WebSocket** (`useNotificationsSocket` composable)

The composable subscribes to `notification.created` events on the shared
`/ws/user/{userId}` presence channel. On each event it invalidates both
`notifications.list` and `notifications.unreadCount` TanStack Query keys,
which triggers a refetch of the unread count displayed in the badge.

The composable is an additive subscriber — it must never `close()` the
channel because `useBanKickGuard` owns the channel lifecycle. On scope
disposal it only unsubscribes its own handler.

```
  ┌──────────┐  notification.created  ┌────────────────────┐
  │ Backend  │ =====================> │ useNotificationsSocket │
  │ WS push  │                        │   invalidateQueries    │
  └──────────┘                        └─────────┬──────────────┘
                                                │
                                       ┌────────v────────┐
                                       │ TanStack Query   │
                                       │ refetch unread   │
                                       │ count + list     │
                                       └────────┬────────┘
                                                │
                                       ┌────────v────────┐
                                       │ NotificationBell │
                                       │ badge updates    │
                                       └─────────────────┘
```

**Fallback: Polling**

`refetchInterval: 300_000` (5 minutes) as a slow fallback for silently
dropped sockets. Not a tight poll — the WebSocket handles the normal path,
and `refetchOnWindowFocus` (TanStack Query default) covers tab returns.

**Data flow**:

| Source | Query Key | Triggers |
|--------|-----------|----------|
| `GET /api/notifications/unread-count` | `['notifications', 'unreadCount']` | Initial load, WS invalidation, polling, window focus |
| WebSocket `notification.created` | Invalidates both keys | New notification pushed |
| `POST /api/notifications/read` | Invalidates `unreadCount` | User marks read |

### 1.4 Click Behavior

The bell is a `<RouterLink>` navigating to `{ name: 'notifications.list' }`
(`/notifications`). No dropdown or popover — clicking always navigates to the
full notifications page.

### 1.5 Design System Components Used

| Component | Usage |
|-----------|-------|
| `BellIcon` (`@heroicons/vue/24/outline`) | Bell icon, 22x22px |

The bell is a custom `<RouterLink>` rather than an `SButton` because it
requires absolute badge positioning and link semantics. It follows the same
ghost-button visual treatment (surface bg, border, hover).

---

## 2. NotificationsView

**File**: `src/slices/notifications/views/NotificationsView.vue`
**Route**: `/notifications` (name: `notifications.list`)
**Layout**: AppShell, sidebar normal, content padding 24px
**Auth**: `requiresAuth: true`, `requiresVerifiedEmail: true`

### 2.1 Wireframe

```
┌──────────────────────────────────────────────────────────────────┐
│ SPageHeader                                                      │
│                                                                  │
│ Notifications                              [ Mark all read ]     │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│ ┌────────────────────────────────────────────────────────────┐   │
│ │ [!] Key usage threshold                      2 min ago    │   │
│ │     API key "prod-gpt4" hit 80% of hourly   [ Mark read ] │   │
│ │     rate limit.                                            │   │
│ │ (unread: accent left border + surface bg)                  │   │
│ └────────────────────────────────────────────────────────────┘   │
│                                                                  │
│ ┌────────────────────────────────────────────────────────────┐   │
│ │ [envelope] Invitation received               1 hour ago   │   │
│ │     Owner invited you to the Acme            [ Mark read ] │   │
│ │     organization.                                          │   │
│ └────────────────────────────────────────────────────────────┘   │
│                                                                  │
│ ┌────────────────────────────────────────────────────────────┐   │
│ │ [key] Key test failed                        Yesterday    │   │
│ │     Validation test for "staging-claude"                   │   │
│ │     returned HTTP 401.                                     │   │
│ │ (read: no left border, white bg, muted title)              │   │
│ └────────────────────────────────────────────────────────────┘   │
│                                                                  │
│                      [ Load more ]                               │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 Notification Card Design

Each notification renders as a card-like `<li>` element with flexbox layout.

```
Card anatomy (unread):

┌─ 3px accent border ─────────────────────────────────────────┐
│                                                              │
│  [icon]   Title (600 weight)              Relative time      │
│           Body text (400 weight, --color-fg)   [ Mark read ] │
│           kind-label  ·  absolute timestamp                  │
│                                                              │
└──────────────────────────────────────────────────────────────┘

Card anatomy (read):

┌─ 1px normal border ─────────────────────────────────────────┐
│                                                              │
│  [icon]   Title (600 weight, --color-muted)   Relative time  │
│           Body text (400 weight, --color-muted)              │
│           kind-label  ·  absolute timestamp                  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Card layout CSS**:

| Property | Value |
|----------|-------|
| Display | `flex`, `align-items: flex-start`, `justify-content: space-between` |
| Gap | 12px (0.75rem) between icon zone and action zone |
| Padding | 12px 16px (0.75rem 1rem) |
| Border | 1px solid `--color-border` |
| Border-radius | `--radius-md` (6px) |
| Margin-bottom | 8px (0.5rem) |

**Unread state additions**:
- `border-left`: 3px solid `--color-accent` (#2563eb)
- `background`: `--color-surface` (#f8fafc)
- Title and body: full `--color-fg` text

**Read state**:
- `border-left`: standard 1px `--color-border`
- `background`: `--color-bg` (white)
- Title: `--color-muted` text
- Body: `--color-muted` text

**Icon zone**: 32x32px area on the left of the main content. The icon is
20x20px within, using the kind-specific icon and tint color
(see section 2.3).

**Content zone** (`.notifications__main`):
- Flex column, gap 4px (0.25rem)
- Title: 14px, 600 weight
- Body: 14px, 400 weight (optional — may be null)
- Meta line: 12px (0.75rem), `--color-muted`, shows `kind` label and
  `created_at` separated by a centered dot

**Action zone**: right-aligned, vertically top-aligned.
- Relative time: 12px, `--color-muted`, no-wrap
- "Mark read" button: `SButton` variant `ghost`, size `sm`, visible only
  when `read_at` is null

### 2.3 Kind-Specific Icons and Colors

Each notification kind maps to a Heroicon and a semantic tint pair for the
icon background circle.

| Kind | Icon | Tint BG | Icon Color | Meaning |
|------|------|---------|------------|---------|
| `key_usage_threshold` | `ExclamationTriangleIcon` (24/outline) | `--color-warning-tint` (#fef3c7) | `--color-warning` (#d97706) | Key approaching rate limit |
| `key_test_failed` | `XCircleIcon` (24/outline) | `--color-danger-tint` (#fee2e2) | `--color-danger` (#dc2626) | Key validation failure |
| `invite_received` | `EnvelopeIcon` (24/outline) | `--color-info-tint` (#dbeafe) | `--color-accent` (#2563eb) | Org/Project invitation |
| `user_banned` | `NoSymbolIcon` (24/outline) | `--color-danger-tint` (#fee2e2) | `--color-danger` (#dc2626) | Admin banned user |
| `approval_requested` | `ClipboardDocumentCheckIcon` (24/outline) | `--color-info-tint` (#dbeafe) | `--color-accent` (#2563eb) | Agent approval (v2) |
| (unknown/fallback) | `BellIcon` (24/outline) | `--color-neutral-tint` (#f3f4f6) | `--color-muted` (#6b7280) | Unrecognized kind |

**Icon container**: 32x32px circle (`--radius-full`), background uses the
tint color, icon centered at 20x20px using the icon color. This provides a
soft colored backdrop that distinguishes kinds at a glance.

```
Icon container:

  ┌────────────┐
  │  ┌──────┐  │   32x32 circle
  │  │ icon │  │   tint background
  │  │20x20 │  │   icon in semantic color
  │  └──────┘  │
  └────────────┘
```

### 2.4 Mark-Read Behavior

#### Individual Mark-Read

- Each unread notification card shows a "Mark read" `SButton` (ghost, sm)
  on the right side.
- On click:
  1. Calls `POST /api/notifications/read` with `{ ids: [n.id] }`.
  2. On success: optimistically patches `read_at` in the TanStack Query cache
     (sets `read_at` to `new Date().toISOString()` on matching items in the
     infinite query pages). Invalidates `unreadCount` query key so the bell
     badge refreshes.
  3. On failure: shows toast error via `useToast` with
     `$t('notifications.markFailed')`.
- The button disappears once `read_at` is set (the card transitions to
  read styling).

#### Bulk Mark-All-Read

- "Mark all read" button in `SPageHeader` actions slot.
- Enabled only when `hasUnread` is true and no mark operation is in progress.
- On click:
  1. Loads all remaining infinite-query pages (up to `MAX_LOAD_PAGES = 40`
     guard to prevent pathological loops).
  2. Collects all unread notification IDs.
  3. Sends IDs in batches of `MARK_BATCH = 1000` per request (backend
     `MarkReadIn` caps `ids` at 1000).
  4. On success: patches all loaded pages in the TanStack cache, invalidates
     `unreadCount`.
  5. On failure: shows toast error.
- While running: button shows disabled state. Consider adding `loading`
  prop to show spinner during the operation.

#### Optimistic Cache Patching

The view patches loaded infinite-query pages in place via
`queryClient.setQueryData` rather than invalidating the list query (which
would refetch every loaded page). Only the `unreadCount` key is
invalidated to refresh the bell badge.

```
User clicks "Mark read"
        │
        v
  POST /notifications/read { ids: [n.id] }
        │
        ├── success ──> patchRead(ids)
        │                 ├── setQueryData: update read_at in pages
        │                 └── invalidateQueries: unreadCount
        │
        └── failure ──> toast.error($t('notifications.markFailed'))
```

### 2.5 Empty State

When `items.length === 0` and loading is complete, display an `SEmptyState`
component.

```
┌──────────────────────────────────────────┐
│                                          │
│             [BellSlashIcon]              │
│              48x48, muted                │
│                                          │
│       You have no notifications.         │
│                                          │
│   Notifications about key usage,         │
│   invitations, and account activity      │
│   will appear here.                      │
│                                          │
└──────────────────────────────────────────┘
```

| Property | Value |
|----------|-------|
| Icon | `BellSlashIcon` (24/outline), 48x48px, `--color-muted` |
| Title | `$t('notifications.empty')` — "You have no notifications." |
| Description | `$t('notifications.emptyDescription')` — supplemental guidance |
| Action | None (no CTA button needed) |
| Max-width | 400px, centered |

### 2.6 Pagination

The view uses **cursor-based infinite scroll** (not offset pagination).

| Parameter | Value |
|-----------|-------|
| Strategy | Cursor pagination via TanStack `useInfiniteQuery` |
| Page size | 50 items per request |
| Cursor | `id` of the last item in the previous page |
| Next page detection | If `lastPage.length === PAGE_SIZE`, more pages exist |
| Load trigger | Manual "Load more" `SButton` at bottom of list |

**"Load more" button**:
- Variant: `secondary`, full-width (or centered, max-width 200px)
- Visible only when `hasNextPage` is true
- Disabled while `isFetchingNextPage` is true
- Label: `$t('notifications.loadMore')` — "Load more"

**Why not infinite scroll with IntersectionObserver**: manual "Load more"
gives users explicit control and avoids accidental mass-loading on scroll.
The notification list is a utility page, not a feed — users typically scan
recent items and rarely scroll deep.

### 2.7 Loading State

While `query.isLoading` is true (initial fetch), display a text indicator:
`$t('notifications.loading')` — "Loading notifications..."

**Future enhancement**: replace the plain text with 3-5 `SSkeleton` cards
mimicking the notification card layout (icon circle + two text lines +
timestamp).

```
Skeleton card:

┌──────────────────────────────────────────────────────────────┐
│  [circle]   [========= 60% ====]              [==== 20% ==] │
│             [================ 80% ================]         │
│             [===== 30% ====]                                 │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. Responsive Behavior

### 3.1 Breakpoint Adaptations

| Breakpoint | Layout Changes |
|------------|----------------|
| >= 1024px (desktop) | Full card layout, "Mark read" button visible inline, relative time on same row as title |
| 768-1023px (tablet) | Same card layout, slightly tighter padding (12px instead of 16px horizontal) |
| < 768px (mobile) | Card stacks vertically: icon + content full width, action button below content, relative time moves to meta line |

### 3.2 Mobile Card Layout

```
Mobile (< 768px):

┌──────────────────────────────────┐
│ [icon]  Title text               │
│         Body text wraps to       │
│         multiple lines here      │
│         kind  ·  2 min ago       │
│                                  │
│         [ Mark read ]            │
└──────────────────────────────────┘
```

On mobile, the card shifts from `flex-row` (side-by-side) to `flex-column`
for the action zone. The "Mark read" button becomes a full-width ghost
button below the content, maintaining the 44px touch target minimum.

### 3.3 Page Header on Mobile

`SPageHeader` stacks: title on top, "Mark all read" button below, both
full-width. This is the standard `SPageHeader` responsive behavior.

---

## 4. Accessibility

### 4.1 ARIA Live Region

New notifications pushed via WebSocket must be announced to screen readers.
Add an `aria-live="polite"` region that receives a visually-hidden
announcement when the unread count changes upward (new notification
arrived).

```html
<div
  aria-live="polite"
  aria-atomic="true"
  class="visually-hidden"
>
  {{ announceText }}
</div>
```

When the unread count increases (detected via a `watch` on the query data),
set `announceText` to `$t('notifications.newNotification')` — e.g.,
"New notification received." The text is cleared after a short delay so
repeated announcements work.

### 4.2 Bell Component Accessibility

| Attribute | Value |
|-----------|-------|
| `aria-label` | `$t('notifications.bellLabel', { count })` — "Notifications (3 unread)" |
| Role | Implicit `<a>` role via `<RouterLink>` |
| Icon | `aria-hidden="true"` on `BellIcon` (decorative) |
| Badge | Not independently focusable; count is conveyed via `aria-label` |
| Focus | `--focus-ring` box-shadow on `:focus-visible` |

### 4.3 Notification List Accessibility

| Element | Attributes |
|---------|------------|
| List container | `<ul role="list">` |
| List items | `<li>` — natural list semantics |
| Unread indicator | Conveyed via `aria-label` on the `<li>`: e.g., "Unread: Key usage threshold — API key prod-gpt4 hit 80% of hourly rate limit. 2 minutes ago." |
| "Mark read" button | `aria-label="$t('notifications.markRead')"` — screen readers get "Mark read" |
| "Mark all read" button | Standard button, label is visible text |
| "Load more" button | Standard button, `aria-disabled` mirrors `disabled` prop |
| Empty state | `role="status"` so screen readers announce "You have no notifications" |
| Loading text | `aria-live="polite"` on the loading paragraph |

### 4.4 Keyboard Navigation

| Key | Context | Action |
|-----|---------|--------|
| Tab | Page | Cycles through: page header actions, then each notification card's "Mark read" button, then "Load more" |
| Enter | Bell icon | Navigates to notifications page |
| Enter / Space | "Mark read" button | Marks notification as read |
| Enter / Space | "Mark all read" button | Triggers bulk mark-read |
| Enter / Space | "Load more" button | Fetches next page |

### 4.5 Color Contrast

All text meets WCAG 2.1 AA contrast ratios against their backgrounds:

| Element | Foreground | Background | Ratio |
|---------|------------|------------|-------|
| Unread title | `--color-fg` (#1f2328) | `--color-surface` (#f8fafc) | 15.2:1 |
| Read title | `--color-muted` (#6b7280) | `--color-bg` (#ffffff) | 5.0:1 |
| Meta text | `--color-muted` (#6b7280) | `--color-surface` (#f8fafc) | 4.8:1 |
| Badge text | #ffffff | `--color-danger` (#dc2626) | 4.6:1 |
| Accent border | `--color-accent` (#2563eb) | N/A (decorative) | N/A |

---

## 5. Interaction Patterns

### 5.1 Notification Kind Actions

Some notification kinds may benefit from a contextual action link in the
card body or metadata area. These are navigational — clicking leads to the
relevant resource.

| Kind | Action Link | Target Route |
|------|-------------|--------------|
| `key_usage_threshold` | "View key" | `/keys/:keyId` (extracted from `metadata.key_id`) |
| `key_test_failed` | "View key" | `/keys/:keyId` (extracted from `metadata.key_id`) |
| `invite_received` | "View invites" | `/invites` |
| `user_banned` | None | Shown once on login; no further action |
| `approval_requested` | "Review" (v2) | Agent detail page |

These links render as `SButton` variant `link`, size `sm`, appended after
the body text. They are optional and only render when the notification's
`metadata` contains the required IDs.

### 5.2 Timestamp Display

| Context | Format | Example |
|---------|--------|---------|
| Card right side (desktop) | Relative time | "2 min ago", "1 hour ago", "Yesterday" |
| Card meta line | Locale-formatted absolute | `new Date(created_at).toLocaleString()` |
| Tooltip on relative time (future) | Full ISO timestamp | "2026-06-24T14:30:00Z" |

Relative time thresholds:
- < 1 minute: "Just now"
- 1-59 minutes: "X min ago"
- 1-23 hours: "X hours ago"
- 1 day: "Yesterday"
- 2-6 days: "X days ago"
- >= 7 days: locale date string

### 5.3 Toast Notifications

| Event | Toast | Variant |
|-------|-------|---------|
| Mark-read failure | `$t('notifications.markFailed')` — "Failed to mark as read." | `error` |
| Mark-all-read failure | `$t('notifications.markFailed')` — "Failed to mark as read." | `error` |

No success toast for mark-read — the visual state change (card transitions
from unread to read styling) provides sufficient feedback.

---

## 6. State Management

### 6.1 Query Keys

```ts
export const notificationKeys = {
  list: () => ['notifications', 'list'] as const,
  unreadCount: () => ['notifications', 'unreadCount'] as const,
}
```

### 6.2 Query Configuration

| Query | Stale Time | Refetch Interval | Refetch on Focus | Cache Time |
|-------|-----------|------------------|------------------|------------|
| `unreadCount` | Default (0) | 300,000ms (5 min) | Yes (default) | Default (5 min) |
| `list` (infinite) | Default (0) | None | Yes (default) | Default (5 min) |

### 6.3 Invalidation Map

| Trigger | Keys Invalidated |
|---------|------------------|
| WebSocket `notification.created` | `list`, `unreadCount` |
| `markRead()` success | `unreadCount` (list patched in place) |
| `markAll()` success | `unreadCount` (list patched in place) |
| Window regains focus | Both (TanStack default behavior) |

---

## 7. Localization Keys

All user-facing strings use `$t()` with keys registered via
`installNotificationsSlice()`.

| Key | English | Usage |
|-----|---------|-------|
| `notifications.title` | "Notifications" | Page header |
| `notifications.bellLabel` | "Notifications ({count} unread)" | Bell aria-label |
| `notifications.loading` | "Loading notifications..." | Initial load text |
| `notifications.empty` | "You have no notifications." | Empty state title |
| `notifications.emptyDescription` | "Notifications about key usage, invitations, and account activity will appear here." | Empty state body (new key) |
| `notifications.markAll` | "Mark all read" | Bulk action button |
| `notifications.markRead` | "Mark read" | Individual action button |
| `notifications.markFailed` | "Failed to mark as read." | Error toast |
| `notifications.loadMore` | "Load more" | Pagination button |
| `notifications.newNotification` | "New notification received." | Screen reader announcement (new key) |
| `notifications.justNow` | "Just now" | Relative time (new key) |
| `notifications.minutesAgo` | "{n} min ago" | Relative time (new key) |
| `notifications.hoursAgo` | "{n} hours ago" | Relative time (new key) |
| `notifications.yesterday` | "Yesterday" | Relative time (new key) |
| `notifications.daysAgo` | "{n} days ago" | Relative time (new key) |
| `notifications.viewKey` | "View key" | Kind action link (new key) |
| `notifications.viewInvites` | "View invites" | Kind action link (new key) |

Keys marked "(new key)" are additions to the current locale files.

---

## 8. Design System Components Used

| Component | Where | Purpose |
|-----------|-------|---------|
| `SPageHeader` | NotificationsView header | Page title with "Mark all read" action button in the actions slot |
| `SButton` | "Mark all read", "Mark read", "Load more", kind action links | All interactive actions; variants: `secondary` (mark all, load more), `ghost` (mark read), `link` (kind actions) |
| `SEmptyState` | NotificationsView empty state | Centered icon + message when no notifications exist |
| `SBadge` | (optional) Kind label in meta line | Pill-shaped kind indicator; variant maps to kind severity |
| `STooltip` | (future) Relative time hover | Shows absolute timestamp on hover |
| `SSkeleton` | (future) Loading state | Placeholder cards during initial fetch |

Components NOT used (and why):
- `SCard`: notification items use a custom `<li>` with card-like styling
  rather than `SCard` because list items need `<ul>/<li>` semantics and the
  unread left-border treatment is unique to notifications.
- `SPagination`: cursor-based infinite loading does not use page numbers.
- `STable`: notifications are a feed, not tabular data.

---

## 9. API Contract Summary

### 9.1 Endpoints

| Method | Path | Request | Response | Notes |
|--------|------|---------|----------|-------|
| GET | `/api/notifications` | `?cursor={id}&limit={n}` | `Notification[]` | Cursor = last item's id; limit capped at 200 by backend |
| POST | `/api/notifications/read` | `{ ids: string[] }` | `{ marked: number }` | Max 1000 ids per request |
| GET | `/api/notifications/unread-count` | — | `{ count: number }` | Used by bell badge |

### 9.2 Notification Model

```ts
interface Notification {
  id: string
  kind: string           // e.g., "key_usage_threshold"
  title: string          // localized by backend
  body: string | null    // optional detail text
  metadata: Record<string, unknown>  // kind-specific data (key_id, org_id, etc.)
  read_at: string | null // ISO timestamp or null if unread
  created_at: string     // ISO timestamp
}
```

### 9.3 WebSocket Event

Channel: `/ws/user/{userId}`
Event type: `notification.created`
Payload: not consumed directly — the composable invalidates queries to
refetch from the REST API, ensuring consistency.

---

## 10. Domain Rules

| Rule | Implementation |
|------|----------------|
| In-app only (v1) | No email, webhook, or Slack delivery. Notifications are persisted server-side and pushed over WebSocket. |
| Unread badge | Red dot with count on bell icon. Max display: "99+". |
| Mark individual | `POST /api/notifications/read` with single id. Optimistic cache patch. |
| Mark all | Loads all pages (up to 40 page guard), batches ids in chunks of 1000. |
| Purge retention | Read notifications are purged by a background Arq worker after the configured retention period. No UI for purge — it is automatic and transparent. |
| `user_banned` | Shown once on next login. The notification appears in the list but the user's session is terminated by `useBanKickGuard`, so in practice the user sees it briefly or on re-authentication if the ban is later lifted. |

---

## 11. Files Summary

### Existing Files (view/modify)

| File | Status | Notes |
|------|--------|-------|
| `src/slices/notifications/components/NotificationBell.vue` | Exists | Add `aria-live` announcement region; current implementation is production-ready |
| `src/slices/notifications/views/NotificationsView.vue` | Exists | Add kind-specific icons, icon tint circles, `SEmptyState`, `SButton` migration, responsive mobile layout |
| `src/slices/notifications/api/index.ts` | Exists | No changes needed |
| `src/slices/notifications/queries/index.ts` | Exists | No changes needed |
| `src/slices/notifications/routes.ts` | Exists | No changes needed |
| `src/slices/notifications/composables/useNotificationsSocket.ts` | Exists | No changes needed |
| `src/slices/notifications/index.ts` | Exists | No changes needed |
| `src/slices/notifications/locales/en.json` | Exists | Add new keys: `emptyDescription`, `newNotification`, relative time keys, kind action link keys |
| `src/slices/notifications/locales/zh-TW.json` | Exists | Add corresponding zh-TW translations for new keys |
| `src/slices/notifications/__tests__/NotificationsView.test.ts` | Exists | Extend: test kind-specific icon rendering, empty state with `SEmptyState`, mark-read interactions |

### New Files (create)

| File | Purpose |
|------|---------|
| `src/slices/notifications/components/NotificationCard.vue` | Extract card rendering into a dedicated component for kind-specific icon logic and responsive layout |
| `src/slices/notifications/lib/kindConfig.ts` | Map of `kind` -> `{ icon, tintBg, iconColor, actionRoute? }` for centralized kind configuration |
| `src/slices/notifications/lib/relativeTime.ts` | Pure function for relative time formatting; avoids adding a dependency like `date-fns` |

### Component Dependency Graph

```
NotificationBell.vue
  imports: BellIcon, useNotificationsSocket, notificationKeys, notificationsApi
  used by: AppTopBar.vue (app shell)

NotificationsView.vue
  imports: SPageHeader, SButton, SEmptyState, NotificationCard, notificationsApi, notificationKeys
  used by: router (notifications.list route)

NotificationCard.vue (new)
  imports: SButton, kindConfig, relativeTime
  used by: NotificationsView.vue

useNotificationsSocket.ts
  imports: wsManager, useSessionStore, notificationKeys
  used by: NotificationBell.vue

kindConfig.ts (new)
  imports: Heroicons
  used by: NotificationCard.vue
```
