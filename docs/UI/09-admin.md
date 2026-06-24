# 09 -- Admin

> Platform administration console -- user governance, audit log, metrics, rate limits, impersonation, IP bans, and resource recovery.
> All views are admin-only (`meta: { requiredRoles: ['admin'] }`) and use `AppShell` layout with 24px content padding.

---

## 1. AdminHomeView (Dashboard)

**File**: `src/slices/admin/views/AdminHomeView.vue`
**Route**: `/admin` (name: `admin.home`)
**Layout**: `AppShell` (standard 24px content padding)
**Auth**: admin only

### 1.1 Wireframe

```
+--------------------------------------------------------------+
| SPageHeader: "Admin Console"                                 |
+--------------------------------------------------------------+
|                                                              |
| [Users]  [Admins]  [IP Bans]  [Organisations]  [Projects]   |
| [Audit Log]  [Operations]  [Rate Limits]  [Metrics]          |
|                                                              |
+-------------------------------+------------------------------+
|                               |                              |
|  +------------+  +----------+ | +----------+  +------------+ |
|  | total_users|  |total_orgs| | |total_proj|  |total_audit | |
|  |   1,247    |  |   89     | | |   312    |  |  58,421    | |
|  |Total Users |  |Total Orgs| | |Total Proj|  |Audit Logs  | |
|  +------------+  +----------+ | +----------+  +------------+ |
|                               |                              |
+--------------------------------------------------------------+
```

### 1.2 Navigation Links

Nine `<router-link>` elements, each rendered as a bordered pill-style link.

| Label (i18n key) | Route Name | Icon |
|---|---|---|
| `admin.nav.users` | `admin.users` | `UsersIcon` |
| `admin.nav.admins` | `admin.admins` | `ShieldCheckIcon` |
| `admin.nav.ipBans` | `admin.ipBans` | `NoSymbolIcon` |
| `admin.nav.orgs` | `admin.orgs` | `BuildingOffice2Icon` |
| `admin.nav.projects` | `admin.projects` | `FolderIcon` |
| `admin.nav.audit` | `admin.audit` | `ClipboardDocumentListIcon` |
| `admin.nav.ops` | `admin.ops` | `WrenchScrewdriverIcon` |
| `admin.nav.rateLimits` | `admin.rateLimits` | `AdjustmentsHorizontalIcon` |
| `admin.nav.metrics` | `admin.metrics` | `ChartBarIcon` |

**Visual spec**: flex row, `flex-wrap: wrap`, gap 12px, each link has 8px 16px padding, 1px `--color-border` border, `--radius-md` border-radius, no text decoration, `--color-fg` text, hover `--color-sidebar-hover` background.

### 1.3 Metrics Summary

Data source: `GET /api/admin/metrics` via `adminApi.getMetrics()`, query key `adminKeys.metrics()`.

| Metric | i18n Key | Format |
|---|---|---|
| `total_users` | `admin.metrics.totalUsers` | Locale-formatted integer |
| `total_orgs` | `admin.metrics.totalOrgs` | Locale-formatted integer |
| `total_projects` | `admin.metrics.totalProjects` | Locale-formatted integer |
| `total_audit_entries` | `admin.metrics.totalAuditEntries` | Locale-formatted integer |

**Card layout**: CSS grid `grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr))`, gap 16px, margin-top 16px. Each card is a `SCard` with centered content: value in 32px / 700 weight, label below in 14px `--color-muted`.

### 1.4 Loading & Error States

| State | Visual |
|---|---|
| Loading | `SLoadingSpinner` centered, `class="my-4"` |
| Error | `<p role="alert">` with `$t('admin.home.metricsError')` in `--color-danger` |
| Success | 4 metric cards rendered in grid |
| Empty | N/A (metrics endpoint always returns counts, even if zero) |

### 1.5 Components Used

| Component | Usage |
|---|---|
| `SPageHeader` | Page title "Admin Console" |
| `SLoadingSpinner` | Loading state while metrics fetch |
| `SCard` | Each metric summary card (target design) |

---

## 2. AdminUsersView

**File**: `src/slices/admin/views/AdminUsersView.vue`
**Route**: `/admin/users` (name: `admin.users`)
**Layout**: `AppShell`
**Auth**: admin only

### 2.1 Wireframe

```
+--------------------------------------------------------------+
| SPageHeader: "Users"                                         |
+--------------------------------------------------------------+
|                                                              |
| [ Search by email...    ] [Status v] [Search]                |
|                                                              |
+--------------------------------------------------------------+
| Email           | Status  | Verified | Created    | Actions  |
+--------------------------------------------------------------+
| alice@ex.com    | active  | Yes      | 2025-01-15 | [Ban]    |
| bob@ex.com      | banned  | Yes      | 2025-02-03 | [Unban]  |
| carol@ex.com    | deleted | No       | 2025-03-22 |          |
+--------------------------------------------------------------+
```

### 2.2 Filter / Search

| Control | Component | Model | Type | i18n |
|---|---|---|---|---|
| Text search | `SSearchInput` | `searchQuery` | `text` | placeholder: `admin.users.searchPlaceholder` |
| Status | `SSelect` | `statusFilter` | `select` | label: `admin.users.status` |
| Submit | `SButton` | -- | `submit` | `admin.users.search` |

**Status options**: `""` (All statuses), `active`, `pending`, `banned`, `deleted`.

**Behavior**: Filters are local state. On form submit (`applySearch`), `appliedQ` and `appliedStatus` are set, triggering a reactive query key change. The query refetches with new parameters.

**API call**: `GET /api/admin/users` with optional query params `q` (email search) and `status`.

### 2.3 Table Columns

| Column | Field | i18n Key | Format | Notes |
|---|---|---|---|---|
| Email | `email` | `admin.users.email` | `<router-link>` to `admin.userDetail` | Links to user detail view |
| Status | `status` | `admin.users.status` | `SStatusBadge` | Color-coded: active=success, pending=neutral, banned=danger, deleted=neutral |
| Verified | `email_verified` | `admin.users.verified` | `$t('admin.common.yes')` / `$t('admin.common.no')` | -- |
| Created | `created_at` | `admin.users.created` | `toLocaleDateString()` | Browser locale date |
| Actions | -- | `admin.users.actions` | Action buttons | Contextual per status |

### 2.4 Row Actions

| Action | Visible When | Button Variant | Behavior |
|---|---|---|---|
| Ban | `status === 'active'` | `SButton` variant `danger`, size `sm` | Calls `actions.promptBan(user.id)` -- opens prompt dialog requesting ban reason |
| Unban | `status === 'banned'` | `SButton` variant `secondary`, size `sm` | Calls `actions.unbanUser.mutate(user.id)` |

**Ban prompt dialog** (via `useConfirmDialog().prompt()`):
- Title: `$t('admin.users.banDialogTitle')` -- "Ban User"
- Message: `$t('admin.users.banDialogMessage')` -- "Provide a reason for banning this user:"
- Confirm label: `$t('admin.users.banDialogConfirm')` -- "Ban"
- Input validation: `/\S+/` pattern, error: `$t('admin.users.banDialogReasonRequired')`
- On confirm: calls `actions.banUser.mutate({ userId, reason })`

### 2.5 Loading, Error & Empty States

| State | Visual |
|---|---|
| Loading | `<p role="status">` with `$t('admin.users.loading')` -- "Loading users..." |
| Error | `<p role="alert">` with `$t('admin.users.loadError')` + `SButton` "Retry" calling `query.refetch()` |
| Empty (no filter) | `SEmptyState` text: `$t('admin.users.empty')` -- "No users yet." |
| Empty (filtered) | `SEmptyState` text: `$t('admin.users.emptyFiltered')` -- "No users match your filters." |

### 2.6 Components Used

| Component | Usage |
|---|---|
| `SPageHeader` | Page title "Users" |
| `SSearchInput` | Email search field (target design) |
| `SSelect` | Status filter dropdown (target design) |
| `SButton` | Search submit, Ban, Unban, Retry |
| `STable` | User listing table (target design) |
| `SStatusBadge` | Status column display (target design) |
| `SEmptyState` | No results state |

---

## 3. AdminUserDetailView

**File**: `src/slices/admin/views/AdminUserDetailView.vue`
**Route**: `/admin/users/:userId` (name: `admin.userDetail`)
**Layout**: `AppShell`
**Auth**: admin only
**Params**: `userId` (UUID string from route)

### 3.1 Wireframe

```
+--------------------------------------------------------------+
| alice@example.com                                            |
+--------------------------------------------------------------+
|                                                              |
| ID              | 550e8400-e29b-41d4-a716-446655440000       |
| Status          | active                                     |
| Verified        | Yes                                        |
| Is Admin        | No                                         |
| Ban Reason      | -                                          |
| Banned At       | -                                          |
| Deleted At      | -                                          |
| Last Login      | 2025-06-20T14:30:00Z                       |
| Created         | 2025-01-15T09:00:00Z                       |
| Organisations   | 3                                          |
| Projects        | 7                                          |
|                                                              |
| [Ban] [Soft Delete] [Impersonate]                            |
|                                                              |
+--------------------------------------------------------------+
```

### 3.2 Data Fields

Data source: `GET /api/admin/users/{uid}` via `adminApi.getUser(userId)`, returns `UserDetail`.

| Label (i18n key) | Field | Format |
|---|---|---|
| `admin.userDetail.id` | `id` | UUID string, monospace |
| `admin.users.status` | `status` | `SStatusBadge` |
| `admin.users.verified` | `email_verified` | Yes / No |
| `admin.userDetail.isAdmin` | `is_admin` | Yes / No |
| `admin.userDetail.bannedReason` | `banned_reason` | Text or `"-"` if null |
| `admin.userDetail.bannedAt` | `banned_at` | ISO datetime or `"-"` if null |
| `admin.userDetail.deletedAt` | `deleted_at` | ISO datetime or `"-"` if null |
| `admin.userDetail.lastLogin` | `last_login_at` | ISO datetime or `"-"` if null |
| `admin.users.created` | `created_at` | ISO datetime |
| `admin.userDetail.orgs` | `org_ids.length` | Integer count |
| `admin.userDetail.projects` | `project_ids.length` | Integer count |

**Layout**: `<dl>` with CSS grid `grid-template-columns: 12rem 1fr`, gap `4px 16px`. Labels (`<dt>`) are 600 weight. The user's email serves as the page heading (`<h1>`).

### 3.3 Actions (AdminUserActions Component)

The `AdminUserActions` component renders contextual action buttons based on user state.

| Action | Visible When | Button Variant | Confirmation |
|---|---|---|---|
| Ban | `status === 'active'` | `SButton` variant `danger` | Prompt dialog (ban reason) |
| Unban | `status === 'banned'` | `SButton` variant `secondary` | None (immediate) |
| Soft Delete | `status === 'active'` | `SButton` variant `danger` | `SConfirmDialog` variant `warning` |
| Hard Delete | `deleted_at` is set | `SButton` variant `danger` | `SConfirmDialog` variant `error` |
| Impersonate | `status === 'active'` | `SButton` variant `secondary` | `SConfirmDialog` variant `warning` |

All buttons are disabled while `actionPending` is true (any mutation in flight).

#### Confirmation Dialogs

**Soft Delete**:
- Title: `$t('admin.userDetail.softDeleteTitle')` -- "Delete User"
- Message: `$t('admin.userDetail.softDeleteMessage')` -- "This will deactivate the account. The user will not be able to log in."
- Confirm: `$t('admin.userDetail.softDeleteConfirm')` -- "Delete"
- Cancel: `$t('app.cancel')`
- Variant: `warning`
- On confirm: `actions.softDeleteUser.mutate(userId)`

**Hard Delete**:
- Title: `$t('admin.userDetail.hardDeleteTitle')` -- "Permanently Delete User"
- Message: `$t('admin.userDetail.hardDeleteMessage')` -- "This will permanently destroy all user data. This action cannot be undone."
- Confirm: `$t('admin.userDetail.hardDeleteConfirm')` -- "Delete Forever"
- Cancel: `$t('app.cancel')`
- Variant: `error`
- On confirm: `actions.hardDeleteUser.mutate(userId)`

**Impersonate**:
- Title: `$t('admin.userDetail.impersonateTitle')` -- "Impersonate User"
- Message: `$t('admin.userDetail.impersonateMessage')` -- "Start a read-only impersonation session for this user?"
- Confirm: `$t('admin.userDetail.impersonateConfirm')` -- "Start"
- Cancel: `$t('app.cancel')`
- Variant: `warning`
- On confirm: `startImpersonation.mutate(userId)` (from `useImpersonation()`)

### 3.4 Loading State

The view renders only when `query.data.value` is truthy. While the query is pending, nothing is shown (no explicit loading spinner in current implementation). Target design: show `SLoadingSpinner` while loading.

### 3.5 Components Used

| Component | Usage |
|---|---|
| `AdminUserActions` | Action buttons (Ban, Unban, Soft Delete, Hard Delete, Impersonate) |
| `SStatusBadge` | Status display in detail fields (target design) |
| `SConfirmDialog` | Destructive action confirmation (via `useConfirmDialog()`) |
| `SLoadingSpinner` | Loading state (target design) |
| `SCard` | Detail card wrapper (target design) |

---

## 4. AdminAdminsView

**File**: `src/slices/admin/views/AdminAdminsView.vue`
**Route**: `/admin/admins` (name: `admin.admins`)
**Layout**: `AppShell`
**Auth**: admin only

### 4.1 Wireframe

```
+--------------------------------------------------------------+
| SPageHeader: "Admins"                                        |
+--------------------------------------------------------------+
|                                                              |
| [ User ID to promote    ] [Promote]                          |
|                                                              |
| [error] Cannot demote the last admin.     <-- conditional    |
|                                                              |
+--------------------------------------------------------------+
| User ID                       | Promoted By | Promoted At | Actions |
+--------------------------------------------------------------+
| 550e8400-e29b-41d4-a716-44... | 7a1b2c3d... | 2025-01-10  | [Demote]|
| 8f3e4d5c-6b7a-8c9d-0e1f-23... | -           | 2025-03-05  | [Demote]|
+--------------------------------------------------------------+
```

### 4.2 Promote Form

| Control | Component | Model | Required | i18n |
|---|---|---|---|---|
| User ID | `SInput` | `promoteUserId` | yes | placeholder: `admin.admins.userIdPlaceholder` |
| Submit | `SButton` variant `primary` | -- | -- | `admin.admins.promote` |

**Behavior**: On submit (`onPromote`), calls `actions.promoteAdmin.mutateAsync(userId)`. On success, clears the input. On error, sets `promoteError` to `$t('admin.users.promotionFailed')`.

### 4.3 Table Columns

| Column | Field | i18n Key | Format |
|---|---|---|---|
| User ID | `user_id` | `admin.admins.userId` | UUID string, monospace |
| Promoted By | `promoted_by_user_id` | `admin.admins.promotedBy` | UUID or `"-"` if null |
| Promoted At | `promoted_at` | `admin.admins.promotedAt` | `toLocaleDateString()` |
| Actions | -- | `admin.users.actions` | Demote button |

### 4.4 Demote Action

- Button: `SButton` variant `danger`, size `sm`, label `$t('admin.admins.demote')`
- On click: calls `actions.demoteAdmin.mutateAsync(userId)`
- **Last-admin guard**: if the server returns problem type `admin/last-admin`, shows error `$t('admin.users.lastAdminDemote')` -- "Cannot demote the last admin." Detection uses `isProblemWithType(e, 'admin/last-admin')` from `@shared/transport`.
- Other errors: `$t('admin.users.demotionFailed')`

### 4.5 Error Display

Error message `promoteError` is rendered as `<p>` with `--color-danger` text color. Cleared before each promote or demote attempt. Target design: use `SAlert` variant `error`.

### 4.6 Components Used

| Component | Usage |
|---|---|
| `SPageHeader` | Page title "Admins" |
| `SInput` | User ID input for promote form (target design) |
| `SButton` | Promote submit, Demote per row |
| `STable` | Admin listing table (target design) |
| `SAlert` | Error messages (target design) |

---

## 5. AdminIpBansView

**File**: `src/slices/admin/views/AdminIpBansView.vue`
**Route**: `/admin/ip-bans` (name: `admin.ipBans`)
**Layout**: `AppShell`
**Auth**: admin only

### 5.1 Wireframe

```
+--------------------------------------------------------------+
| SPageHeader: "IP Bans"                                       |
+--------------------------------------------------------------+
|                                                              |
| [ 192.168.1.0/24 ] [ Reason...  ] [Add Ban]                 |
|                                                              |
+--------------------------------------------------------------+
| CIDR              | Reason            | Created    | Actions |
+--------------------------------------------------------------+
| 10.0.0.0/8        | Spam network      | 2025-04-01 | [Remove]|
| 192.168.1.0/24    | Brute force       | 2025-05-15 | [Remove]|
+--------------------------------------------------------------+
```

### 5.2 Create Form

| Control | Component | Model | Required | i18n | Constraints |
|---|---|---|---|---|---|
| CIDR | `SInput` | `cidr` | yes | placeholder: `admin.ipBans.cidrPlaceholder` ("192.168.1.0/24") | 1-64 chars, valid CIDR |
| Reason | `SInput` | `reason` | yes | placeholder: `admin.ipBans.reason` | 1-1024 chars |
| Submit | `SButton` variant `primary` | -- | -- | `admin.ipBans.add` | disabled during `createIpBan.isPending` |

**Behavior**: On submit (`onCreate`), calls `actions.createIpBan.mutateAsync({ cidr, reason })`. On success, clears both fields. On error, toast handled by `useAdminActions` `onError` callback.

**API**: `POST /api/admin/ip-bans` with body `{ cidr, reason }`.

### 5.3 Table Columns

| Column | Field | i18n Key | Format |
|---|---|---|---|
| CIDR | `cidr` | `admin.ipBans.cidr` | `<code>` element, monospace |
| Reason | `reason` | `admin.ipBans.reason` | Plain text |
| Created | `banned_at` | `admin.users.created` | `toLocaleDateString()` |
| Actions | -- | `admin.users.actions` | Remove button |

### 5.4 Remove Action

- Button: `SButton` variant `danger`, size `sm`, label `$t('admin.ipBans.remove')`
- Disabled during `deleteIpBan.isPending`
- **Confirmation dialog** (variant `error`):
  - Title: `$t('admin.ipBans.removeConfirmTitle')` -- "Remove IP Ban"
  - Message: `$t('admin.ipBans.removeConfirm')` -- "Remove this IP ban? The blocked range will regain access immediately."
  - Confirm label: `$t('admin.ipBans.remove')` -- "Remove"
  - On confirm: `actions.deleteIpBan.mutate(id)`
- **API**: `DELETE /api/admin/ip-bans/{bid}`

### 5.5 Components Used

| Component | Usage |
|---|---|
| `SPageHeader` | Page title "IP Bans" |
| `SInput` | CIDR and reason fields (target design) |
| `SButton` | Add Ban submit, Remove per row |
| `STable` | IP ban listing (target design) |
| `SConfirmDialog` | Remove confirmation (via `useConfirmDialog()`) |

---

## 6. AdminOrgsView

**File**: `src/slices/admin/views/AdminOrgsView.vue`
**Route**: `/admin/orgs` (name: `admin.orgs`)
**Layout**: `AppShell`
**Auth**: admin only

### 6.1 Wireframe

```
+--------------------------------------------------------------+
| SPageHeader: "Organisations"                                 |
+--------------------------------------------------------------+
|                                                              |
+--------------------------------------------------------------+
| Name          | Creator     | Created    | Deleted | Actions |
+--------------------------------------------------------------+
| Acme Corp     | 7a1b2c3d... | 2025-01-20 | -       | [Force Delete] [Force Transfer OC] |
| Old Inc       | 8f3e4d5c... | 2024-11-01 | 2025-06-01 | [Restore] |
+--------------------------------------------------------------+
```

### 6.2 Table Columns

Data source: `GET /api/admin/orgs` via `adminApi.listOrgs()`.

| Column | Field | i18n Key | Format |
|---|---|---|---|
| Name | `name` | `admin.orgs.name` | Plain text |
| Creator | `creator_user_id` | `admin.orgs.creator` | UUID, truncated or monospace |
| Created | `created_at` | `admin.users.created` | `toLocaleDateString()` |
| Deleted | `deleted_at` | `admin.orgs.deleted` | Date or `"-"` if null |
| Actions | -- | `admin.users.actions` | Contextual buttons |

### 6.3 Row Actions

| Action | Visible When | Button Variant | Confirmation |
|---|---|---|---|
| Force Delete | `!org.deleted_at` | `SButton` variant `danger`, size `sm` | `SConfirmDialog` variant `error` |
| Restore | `org.deleted_at` is set | `SButton` variant `secondary`, size `sm` | None (immediate) |
| Force Transfer OC | `!org.deleted_at` | `SButton` variant `secondary`, size `sm` | Prompt dialog |

**Force Delete confirmation**:
- Title: `$t('admin.orgs.forceDeleteTitle')` -- "Force Delete Organisation"
- Message: `$t('admin.orgs.forceDeleteMessage', { name })` -- "Permanently delete \"{name}\" and all its projects? This cannot be undone."
- Confirm: `$t('admin.orgs.forceDeleteConfirm')` -- "Delete"
- Cancel: `$t('app.cancel')`
- Variant: `error`
- API: `POST /api/admin/orgs/{oid}/force-delete`

**Force Transfer OC prompt**:
- Title: `$t('admin.orgs.forceTransferTitle')` -- "Force Transfer OC"
- Message: `$t('admin.orgs.forceTransferMessage')` -- "Enter the target user ID for OC transfer:"
- Confirm: `$t('admin.orgs.forceTransferConfirm')` -- "Transfer"
- Cancel: `$t('app.cancel')`
- Input validation: `/\S+/`, error: `$t('admin.orgs.forceTransferUserIdRequired')`
- Variant: `warning`
- On confirm: calls `transferMutation.mutate({ orgId, targetUserId })`
- API: `POST /api/admin/orgs/{oid}/force-transfer-original-creator`
- Error toast: `$t('admin.orgs.transferFailed')`

**Restore**:
- Calls `actions.restoreResource.mutate({ type: 'org', id: org.id })` immediately (no confirmation)
- API: `POST /api/admin/restore/org/{resource_id}`

### 6.4 Components Used

| Component | Usage |
|---|---|
| `SPageHeader` | Page title "Organisations" |
| `SButton` | Force Delete, Restore, Force Transfer OC |
| `STable` | Organisation listing (target design) |
| `SConfirmDialog` | Force Delete confirmation, Force Transfer OC prompt |

---

## 7. AdminProjectsView

**File**: `src/slices/admin/views/AdminProjectsView.vue`
**Route**: `/admin/projects` (name: `admin.projects`)
**Layout**: `AppShell`
**Auth**: admin only

### 7.1 Wireframe

```
+--------------------------------------------------------------+
| SPageHeader: "Projects"                                      |
+--------------------------------------------------------------+
|                                                              |
+--------------------------------------------------------------+
| Name       | Owner User  | Owner Org   | Created    | Deleted|
+--------------------------------------------------------------+
| my-project | 550e8400... | 7a1b2c3d... | 2025-02-14 | -      |
| old-proj   | 8f3e4d5c... | -           | 2024-08-20 | 2025-05-01 |
+--------------------------------------------------------------+
```

### 7.2 Table Columns

Data source: `GET /api/admin/projects` via `adminApi.listProjects()`. This view is **read-only** -- no row actions.

| Column | Field | i18n Key | Format |
|---|---|---|---|
| Name | `name` | `admin.projects.name` | Plain text |
| Owner User | `owner_user_id` | `admin.projects.ownerUser` | UUID or `"-"` if null |
| Owner Org | `owner_org_id` | `admin.projects.ownerOrg` | UUID or `"-"` if null |
| Created | `created_at` | `admin.users.created` | `toLocaleDateString()` |
| Deleted | `deleted_at` | `admin.orgs.deleted` | Date or `"-"` if null |

### 7.3 Loading & Empty States

| State | Visual |
|---|---|
| Loading | `SLoadingSpinner` centered (target design; currently no explicit loading state) |
| Data | Table rendered |
| Empty | `SEmptyState` with "No projects." text (target design) |

### 7.4 Components Used

| Component | Usage |
|---|---|
| `SPageHeader` | Page title "Projects" |
| `STable` | Read-only project listing (target design) |
| `SEmptyState` | Empty state (target design) |

---

## 8. AdminAuditView

**File**: `src/slices/admin/views/AdminAuditView.vue`
**Route**: `/admin/audit` (name: `admin.audit`)
**Layout**: `AppShell`
**Auth**: admin only

### 8.1 Wireframe

```
+--------------------------------------------------------------+
| SPageHeader: "Audit Log"                                     |
+--------------------------------------------------------------+
|                                                              |
| [Action    ] [Actor User ID] [Resource Type] [Resource ID  ] |
| [IP Prefix ] [Session ID   ] [From (datetime)] [To (datetime)]|
| [Search] [Export CSV]                                        |
|                                                              |
+--------------------------------------------------------------+
| ID  | Action       | Actor User ID | Res Type | Res ID | IP        | Created             |
+--------------------------------------------------------------+
| 127 | user.login   | 550e8400...   | user     | 550e.. | 10.0.0.1  | 2025-06-20 14:30:00 |
| 126 | org.created  | 7a1b2c3d...   | org      | 9f8e.. | 10.0.0.2  | 2025-06-20 14:28:00 |
| ... | ...          | ...           | ...      | ...    | ...       | ...                 |
+--------------------------------------------------------------+
|                                                              |
|                   [Load More]                                |
|                                                              |
+--------------------------------------------------------------+
```

### 8.2 Filter Fields

Eight filter fields in a flex-wrap form, each capped at `max-width: 14rem`.

| Control | Component | Model | Type | i18n |
|---|---|---|---|---|
| Action | `SInput` | `filters.action` | `text` | placeholder: `admin.audit.action` |
| Actor User ID | `SInput` | `filters.actor_user_id` | `text` | placeholder: `admin.audit.actorUserId` |
| Resource Type | `SInput` | `filters.resource_type` | `text` | placeholder: `admin.audit.resourceType` |
| Resource ID | `SInput` | `filters.resource_id` | `text` | placeholder: `admin.audit.resourceId` |
| IP Prefix | `SInput` | `filters.ip_prefix` | `text` | placeholder: `admin.audit.ipPrefix` |
| Session ID | `SInput` | `filters.session_id` | `text` | placeholder: `admin.audit.sessionId` |
| From | `SInput` | `filters.from` | `datetime-local` | aria-label: `admin.audit.from` |
| To | `SInput` | `filters.to` | `datetime-local` | aria-label: `admin.audit.to` |

**Filter behavior**: The `filters` reactive object holds draft values. On form submit (`applyFilters`), only non-empty values are copied into `appliedFilters`, the accumulated items are cleared, cursor is reset, and TanStack Query refetches.

### 8.3 Audit Categories

The `action` field follows a dotted namespace convention. Eleven categories exist in the system:

| Category | Example Actions |
|---|---|
| Auth | `user.login`, `user.logout`, `user.login_failed` |
| User lifecycle | `user.registered`, `user.verified`, `user.banned`, `user.deleted` |
| Org | `org.created`, `org.deleted`, `org.member_added` |
| Project | `project.created`, `project.deleted` |
| Keys | `key.created`, `key.rotated`, `key.deleted` |
| Search keys | `search_key.created`, `search_key.deleted` |
| Agents/RAG/GraphRAG/MCP | `agent.created`, `rag.uploaded`, `graphrag.built`, `mcp.bound` |
| Chat | `chatroom.created`, `message.sent` |
| Workflow | `workflow.created`, `workflow.run_started` |
| Admin | `admin.user_banned`, `admin.impersonation_started`, `admin.rate_limit_changed` |
| System | `system.startup`, `system.migration` |

### 8.4 Table Columns

| Column | Field | i18n Key | Format |
|---|---|---|---|
| ID | `id` | `admin.audit.id` | Integer |
| Action | `action` | `admin.audit.action` | Dotted string |
| Actor User ID | `actor_user_id` | `admin.audit.actorUserId` | UUID or `"-"` |
| Resource Type | `resource_type` | `admin.audit.resourceType` | String or `"-"` |
| Resource ID | `resource_id` | `admin.audit.resourceId` | UUID or `"-"` |
| IP | `actor_ip` | `admin.audit.ipPrefix` | IP address or `"-"` |
| Created | `created_at` | `admin.users.created` | `toLocaleString()` (date + time) |

### 8.5 Pagination

Uses **cursor-based pagination** (not offset). The API returns `AuditPage { items, next_cursor }`.

- Items accumulate across pages in `allItems` ref (append-only).
- When `nextCursor` is non-null, a "Load More" button is shown.
- On click (`loadMore`), the cursor is merged into `appliedFilters`, triggering a refetch.
- A watcher on `query.data` appends new items (if cursor was set) or replaces all items (fresh query).
- `refetchOnWindowFocus: false` prevents unwanted resets.

### 8.6 CSV Export

- Button: `SButton` variant `secondary`, label `$t('admin.audit.export')`
- **Prerequisite**: both `from` and `to` dates must be set in applied filters. If missing, shows toast warning `$t('admin.audit.exportDateRequired')`.
- API: `POST /api/admin/audit/export` with current filters. Returns `{ url, job_id }`.
- On success: opens `url` in a new tab (`window.open(data.url, '_blank')`).
- On error: toast error `$t('admin.audit.exportFailed')`.

### 8.7 Real-Time Tail (Future)

WebSocket endpoint `ws/admin/tail` provides a live audit feed. Authenticated via JWT subprotocol. Target design: optional toggle to enable live tail mode that prepends new entries to the top of the list in real time with a subtle slide-in animation.

### 8.8 Components Used

| Component | Usage |
|---|---|
| `SPageHeader` | Page title "Audit Log" |
| `SInput` | All 8 filter fields (target design) |
| `SButton` | Search, Export CSV, Load More |
| `STable` | Audit entry listing (target design) |

---

## 9. AdminOpsView

**File**: `src/slices/admin/views/AdminOpsView.vue`
**Route**: `/admin/ops` (name: `admin.ops`)
**Layout**: `AppShell`
**Auth**: admin only

### 9.1 Wireframe

```
+--------------------------------------------------------------+
| SPageHeader: "Operations"                                    |
+--------------------------------------------------------------+
|                                                              |
|  GraphRAG Reset                                              |
|  +--------------------------------------------------------+  |
|  | [ GraphRAG config ID         ] [Reset]                 |  |
|  | GraphRAG config reset to idle.      <-- success msg    |  |
|  +--------------------------------------------------------+  |
|                                                              |
|  Restore Soft-Deleted Resource                               |
|  +--------------------------------------------------------+  |
|  | [Resource type v] [ Resource UUID   ] [Restore]        |  |
|  | Resource restored.                  <-- success msg    |  |
|  +--------------------------------------------------------+  |
|                                                              |
+--------------------------------------------------------------+
```

### 9.2 GraphRAG Reset Section

| Control | Component | Model | Required | i18n |
|---|---|---|---|---|
| Config ID | `SInput` | `graphragConfigId` | yes | placeholder: `admin.ops.configIdPlaceholder` |
| Submit | `SButton` variant `primary` | -- | -- | `admin.ops.reset` |

**Behavior**: Calls `actions.resetGraphrag.mutateAsync(configId)`. On success, shows `$t('admin.ops.graphragResetSuccess')` -- "GraphRAG config reset to idle." and clears the input. On error, shows `$t('admin.ops.resetFailed')`.

**API**: `POST /api/admin/graphrag/{cid}/reset`

### 9.3 Restore Soft-Deleted Resource Section

| Control | Component | Model | Required | i18n |
|---|---|---|---|---|
| Resource Type | `SSelect` | `restoreType` | yes | label: `admin.ops.resourceType` |
| Resource ID | `SInput` | `restoreId` | yes | placeholder: `admin.ops.resourceIdPlaceholder` |
| Submit | `SButton` variant `primary` | -- | -- | `admin.ops.restoreAction` |

**Resource type options**:

| Value | i18n Key |
|---|---|
| `user` | `admin.ops.typeUser` |
| `org` | `admin.ops.typeOrg` |
| `project` | `admin.ops.typeProject` |

**Behavior**: Calls `actions.restoreResource.mutateAsync({ type, id })`. On success, shows `$t('admin.ops.restoreSuccess')` -- "Resource restored." and clears the ID input. On error, shows `$t('admin.ops.restoreFailed')`.

**API**: `POST /api/admin/restore/{resource_type}/{resource_id}`. Returns `{ restored: boolean }`.

**Recovery window**: Soft-deleted resources can be restored within 60 days. After the grace period, the resource is permanently purged and restore will fail.

### 9.4 Result Messages

Both sections display result messages as inline `<p>` elements below the form. Target design: use `SAlert` variant `success` or `error` respectively.

### 9.5 Components Used

| Component | Usage |
|---|---|
| `SPageHeader` | Page title "Operations" |
| `SCard` | Section wrapper for each operation group (target design) |
| `SInput` | Config ID, Resource ID |
| `SSelect` | Resource type dropdown (target design) |
| `SButton` | Reset, Restore submit buttons |
| `SAlert` | Success/failure result messages (target design) |

---

## 10. AdminRateLimitsView

**File**: `src/slices/admin/views/AdminRateLimitsView.vue`
**Route**: `/admin/rate-limits` (name: `admin.rateLimits`)
**Layout**: `AppShell`
**Auth**: admin only

### 10.1 Wireframe

```
+--------------------------------------------------------------+
| SPageHeader: "Rate Limits"                                   |
+--------------------------------------------------------------+
|                                                              |
+--------------------------------------------------------------+
| Key                | Window (sec)| Max Count | Scope | Updated             | Actions |
+--------------------------------------------------------------+
| auth.login         | [  60  ]    | [  10  ]  | ip    | 2025-06-20 14:00:00 | [Save]  |
| auth.register      | [ 300  ]    | [   3  ]  | ip    | 2025-06-20 14:00:00 | [Save]  |
| api.general        | [  60  ]    | [ 100  ]  | user  | 2025-06-18 09:00:00 | [Save]  |
+--------------------------------------------------------------+
```

### 10.2 Table Columns

Data source: `GET /api/admin/rate-limits` via `adminApi.listRateLimits()`.

| Column | Field | i18n Key | Format | Editable |
|---|---|---|---|---|
| Key | `key` | `admin.rateLimits.key` | `<code>` element, monospace | No |
| Window (sec) | `window_sec` | `admin.rateLimits.window` | `SInput` type `number`, min 1, width 5rem | Yes |
| Max Count | `max_count` | `admin.rateLimits.maxCount` | `SInput` type `number`, min 1, width 5rem | Yes |
| Scope | `scope` | `admin.rateLimits.scope` | Plain text | No |
| Updated | `updated_at` | `admin.rateLimits.updated` | `toLocaleString()` | No |
| Actions | -- | `admin.users.actions` | Save button | -- |

### 10.3 Inline Editing

- A reactive `edits` record (`Record<string, { window_sec: number; max_count: number }>`) tracks local edits per policy key.
- On initial data load and whenever query data changes, `edits` is seeded from the server values (only if the key does not already exist in `edits`).
- The number inputs are bound to `edits[policy.key].window_sec` and `edits[policy.key].max_count` via `v-model.number`.
- Constraints (backend validation): `window_sec` 1-86400, `max_count` >= 1.

### 10.4 Save Action

- Button: `SButton` variant `primary`, size `sm`, label `$t('admin.rateLimits.save')`
- On click (`onPatch(key)`): reads current values from `edits[key]`, calls `actions.patchRateLimit.mutate({ key, patch: { window_sec, max_count } })`.
- **API**: `PATCH /api/admin/rate-limits/{key}` with body `{ window_sec, max_count }`.
- On success: query is invalidated and refetched, `updated_at` updates.
- On error: toast `$t('admin.actionErrors.rateLimitFailed')`.

### 10.5 Components Used

| Component | Usage |
|---|---|
| `SPageHeader` | Page title "Rate Limits" |
| `SInput` | Inline number inputs for Window and Max Count (target design) |
| `SButton` | Save per row |
| `STable` | Rate limit policy listing (target design) |

---

## 11. AdminMetricsView

**File**: `src/slices/admin/views/AdminMetricsView.vue`
**Route**: `/admin/metrics` (name: `admin.metrics`)
**Layout**: `AppShell`
**Auth**: admin only

### 11.1 Wireframe

```
+--------------------------------------------------------------+
| SPageHeader: "Platform Metrics"                              |
+--------------------------------------------------------------+
|                                                              |
|  +------------+  +------------------+  +------------------+  |
|  |   1,247    |  |       89         |  |       312        |  |
|  |Total Users |  |  Total Orgs      |  |  Total Projects  |  |
|  +------------+  +------------------+  +------------------+  |
|                                                              |
|  +--------------------+                                      |
|  |      58,421         |                                     |
|  | Total Audit Entries |                                     |
|  +--------------------+                                      |
|                                                              |
+--------------------------------------------------------------+
```

### 11.2 Metric Cards

Data source: `GET /api/admin/metrics` via `adminApi.getMetrics()`, query key `adminKeys.metrics()`.

| Metric | i18n Key | Icon (target design) |
|---|---|---|
| `total_users` | `admin.metrics.totalUsers` | `UsersIcon` |
| `total_orgs` | `admin.metrics.totalOrgs` | `BuildingOffice2Icon` |
| `total_projects` | `admin.metrics.totalProjects` | `FolderIcon` |
| `total_audit_entries` | `admin.metrics.totalAuditEntries` | `ClipboardDocumentListIcon` |

**Visual spec**:
- Grid: `grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr))`, gap 16px, margin-top 16px.
- Each card: `SCard` with centered flex column, padding 24px.
- Value: 32px / 700 weight, `--color-fg`.
- Label: 14px / 400 weight, `--color-muted`.
- Card border: 1px `--color-border`, `--radius-md` border-radius.

### 11.3 Loading & Empty States

| State | Visual |
|---|---|
| Loading | `SLoadingSpinner` centered (target design; currently no explicit loading state) |
| Data | 4 metric cards rendered |
| Error | `SAlert` variant `error` with retry button (target design) |

### 11.4 Components Used

| Component | Usage |
|---|---|
| `SPageHeader` | Page title "Platform Metrics" |
| `SCard` | Each metric card (target design) |
| `SLoadingSpinner` | Loading state (target design) |

---

## 12. AdminImpersonateLauncher

**File**: `src/slices/admin/views/AdminImpersonateLauncher.vue`
**Route**: `/admin/impersonate` (name: `admin.impersonate`)
**Layout**: `AppShell`
**Auth**: admin only

### 12.1 Wireframe

```
+--------------------------------------------------------------+
| Impersonation                                                |
+--------------------------------------------------------------+
| Start a read-only session as another user. All actions are   |
| audit-logged.                                                |
|                                                              |
| [ Target user UUID        ] [Start Session]                  |
|                                                              |
| +----------------------------------------------------------+|
| | [!] Impersonation session is active. All write operations ||
| |     are blocked.                                          ||
| |                                      [End Session]        ||
| +----------------------------------------------------------+|
|                                                              |
| Failed to start impersonation session.  <-- error, if any   |
|                                                              |
+--------------------------------------------------------------+
```

### 12.2 Start Form

| Control | Component | Model | Required | i18n |
|---|---|---|---|---|
| Target User | `SInput` | `targetUserId` | yes | placeholder: `admin.impersonation.targetPlaceholder`, aria-label: same |
| Submit | `SButton` variant `primary` | -- | -- | `admin.impersonation.start`, disabled during `startImpersonation.isPending` |

**Behavior**: On submit (`onStart`), calls `startImpersonation.mutateAsync(targetUserId)` from `useImpersonation()`. On error, sets `error` to `$t('admin.impersonation.startFailed')`.

**API**: `POST /api/admin/users/{uid}/impersonate`. Returns `ImpersonateResult { session_id, access_token }`.

### 12.3 Active Session Panel

Rendered when `isImpersonating` is true (computed from JWT claims).

- **Border**: 2px `--color-warning`, `--radius-md` border-radius, padding 12px.
- **Message**: `$t('admin.impersonation.activeSession')` -- "Impersonation session is active. All write operations are blocked."
- **End button**: `SButton` label `$t('admin.impersonation.end')`, disabled during `endImpersonation.isPending`.
- On click (`onEnd`): calls `endImpersonation.mutateAsync(activeSessionTarget)`.
- **API**: `POST /api/admin/impersonate/stop`

### 12.4 Impersonation Security Model

- **Read-only enforcement**: `ImpersonationPolicyMiddleware` on the backend blocks all non-GET/HEAD/OPTIONS requests when an impersonation JWT is active. This includes blocking download, export, presigned URL, and attachment endpoints.
- **JWT structure**: The impersonation token carries an `impersonated_by` claim containing the admin's user ID. Token auto-expires after 30 minutes.
- **Token storage**: The admin's original token is held in memory only (not `localStorage` or `sessionStorage`) to reduce XSS surface. A page refresh ends the impersonation session.
- **Audit logging**: Both start and end of impersonation are audit-logged under the Admin category.
- **Banner**: `ImpersonationBanner` is shown globally (see section 13).

### 12.5 Error Display

Error string rendered as `<p>` with `--color-danger`. Target design: use `SAlert` variant `error`.

### 12.6 Components Used

| Component | Usage |
|---|---|
| `SInput` | Target user UUID input (target design) |
| `SButton` | Start Session, End Session |
| `SCard` | Active session panel (target design) |
| `SAlert` | Error messages (target design) |

---

## 13. ImpersonationBanner

**File**: `src/slices/admin/components/ImpersonationBanner.vue`
**Rendered in**: `App.vue` (above all layouts, always visible when impersonating)
**Visibility**: only when `isImpersonating` returns true

### 13.1 Wireframe

```
+=====================================================================+
| You are viewing as another user (admin: 550e8400...) | Read-only... |
+=====================================================================+
```

### 13.2 Visual Spec

| Property | Value |
|---|---|
| Position | `fixed`, top 0, left 0, right 0 |
| Z-index | 9999 (above everything including modals) |
| Background | `--color-warning` |
| Text color | `--color-warning-on` |
| Font size | 14px (0.875rem) |
| Font weight | 600 |
| Padding | 8px 16px |
| Layout | flex, `align-items: center`, `justify-content: center`, gap 16px |

### 13.3 Content

| Element | i18n Key | Format |
|---|---|---|
| Main text | `admin.impersonation.banner` | "You are viewing as another user (admin: {adminId})" -- `adminId` is interpolated from `impersonatedBy` computed value |
| Warning suffix | `admin.impersonation.readOnly` | "Read-only mode -- all write operations are blocked." -- italic, opacity 0.8 |

### 13.4 Layout Impact

When the banner is visible, it pushes the `AppShell` down by 36px (banner height). The `AppShell` should account for this via a top padding or margin equal to the banner height when `isImpersonating` is true.

### 13.5 Components Used

No design system components. This is a standalone styled component using `useImpersonation()` composable for state.

---

## 14. AdminUserActions Component

**File**: `src/slices/admin/components/AdminUserActions.vue`
**Used in**: `AdminUserDetailView`

### 14.1 Props

| Prop | Type | Default | Description |
|---|---|---|---|
| `user` | `UserDetail` | required | The user being managed |
| `isPending` | `boolean` | `false` | Disables all buttons when any mutation is in flight |

### 14.2 Emits

| Event | Payload | Description |
|---|---|---|
| `ban` | none | Ban button clicked |
| `unban` | none | Unban button clicked |
| `soft-delete` | none | Soft Delete button clicked |
| `hard-delete` | none | Hard Delete button clicked |
| `impersonate` | none | Impersonate button clicked |

### 14.3 Button Visibility Matrix

| Button | `status === 'active'` | `status === 'banned'` | `status === 'pending'` | `deleted_at` set |
|---|---|---|---|---|
| Ban | visible | -- | -- | -- |
| Unban | -- | visible | -- | -- |
| Soft Delete | visible | -- | -- | -- |
| Hard Delete | -- | -- | -- | visible |
| Impersonate | visible | -- | -- | -- |

### 14.4 Visual Spec

- Layout: flex row, gap 8px, margin-top 16px.
- Ban / Soft Delete / Hard Delete: `SButton` variant `danger`.
- Unban / Impersonate: `SButton` variant `secondary`.
- All buttons: disabled when `isPending` is true.

---

## 15. Composables

### 15.1 useAdminActions

**File**: `src/slices/admin/composables/useAdminActions.ts`

Returns 13 TanStack `useMutation` instances. Each mutation invalidates relevant query keys on success and shows an error toast on failure.

| Mutation | API Call | Invalidates | Error Toast |
|---|---|---|---|
| `promptBan` | Shows prompt dialog, then `banUser` | `adminKeys.users()` | `admin.actionErrors.banFailed` |
| `banUser` | `adminApi.banUser(id, reason)` | `adminKeys.users()` | `admin.actionErrors.banFailed` |
| `unbanUser` | `adminApi.unbanUser(id)` | `adminKeys.users()` | `admin.actionErrors.unbanFailed` |
| `softDeleteUser` | `adminApi.softDeleteUser(id)` | `adminKeys.users()` | `admin.actionErrors.deleteFailed` |
| `hardDeleteUser` | `adminApi.hardDeleteUser(id)` | `adminKeys.users()` | `admin.actionErrors.hardDeleteFailed` |
| `promoteAdmin` | `adminApi.promoteAdmin(userId)` | `adminKeys.admins()` | `admin.actionErrors.promoteFailed` |
| `demoteAdmin` | `adminApi.demoteAdmin(userId)` | `adminKeys.admins()` | `admin.actionErrors.demoteFailed` |
| `forceDeleteOrg` | `adminApi.forceDeleteOrg(orgId)` | `adminKeys.orgs()` | `admin.actionErrors.deleteOrgFailed` |
| `createIpBan` | `adminApi.createIpBan(cidr, reason)` | `adminKeys.ipBans()` | `admin.actionErrors.createIpBanFailed` |
| `deleteIpBan` | `adminApi.deleteIpBan(id)` | `adminKeys.ipBans()` | `admin.actionErrors.removeIpBanFailed` |
| `patchRateLimit` | `adminApi.patchRateLimit(key, patch)` | `adminKeys.rateLimits()` | `admin.actionErrors.rateLimitFailed` |
| `restoreResource` | `adminApi.restoreResource(type, id)` | all admin keys | `admin.actionErrors.restoreFailed` |
| `resetGraphrag` | `adminApi.resetGraphrag(configId)` | none | `admin.actionErrors.resetGraphragFailed` |

### 15.2 useImpersonation

**File**: `src/slices/admin/composables/useImpersonation.ts`

Manages impersonation session lifecycle.

| Export | Type | Description |
|---|---|---|
| `impersonatedBy` | `ComputedRef<string \| null>` | Admin user ID from JWT `impersonated_by` claim |
| `activeSessionTarget` | `Ref<string \| null>` | The user currently being impersonated |
| `isImpersonating` | `ComputedRef<boolean>` | `true` when `impersonatedBy` is non-null |
| `startImpersonation` | `UseMutationReturnType` | Saves admin token, swaps to impersonated token |
| `endImpersonation` | `UseMutationReturnType` | Restores admin token |
| `blockMutatingAction()` | `() => void` | Throws if impersonation is active (called by write actions) |

---

## 16. Query Keys

**File**: `src/slices/admin/queries/index.ts`

All keys are namespaced under `['admin', ...]` for scoped cache invalidation.

| Factory Method | Key Shape | Used By |
|---|---|---|
| `adminKeys.users(params?)` | `['admin', 'users', params]` | AdminUsersView |
| `adminKeys.user(id)` | `['admin', 'user', id]` | AdminUserDetailView |
| `adminKeys.admins()` | `['admin', 'admins']` | AdminAdminsView |
| `adminKeys.ipBans()` | `['admin', 'ip-bans']` | AdminIpBansView |
| `adminKeys.orgs(params?)` | `['admin', 'orgs', params]` | AdminOrgsView |
| `adminKeys.projects(params?)` | `['admin', 'projects', params]` | AdminProjectsView |
| `adminKeys.audit(filters)` | `['admin', 'audit', filters]` | AdminAuditView |
| `adminKeys.rateLimits()` | `['admin', 'rate-limits']` | AdminRateLimitsView |
| `adminKeys.metrics()` | `['admin', 'metrics']` | AdminHomeView, AdminMetricsView |

---

## 17. Route Configuration

**File**: `src/slices/admin/routes.ts`

All admin routes share `meta: { requiresAuth: true, requiredRoles: ['admin'] }`, which enforces authentication and admin role. Non-admin users who navigate to any `/admin/*` path are redirected to the root (`/`).

| Route Name | Path | View | Params |
|---|---|---|---|
| `admin.home` | `/admin` | AdminHomeView | -- |
| `admin.users` | `/admin/users` | AdminUsersView | -- |
| `admin.userDetail` | `/admin/users/:userId` | AdminUserDetailView | `userId: string` |
| `admin.admins` | `/admin/admins` | AdminAdminsView | -- |
| `admin.ipBans` | `/admin/ip-bans` | AdminIpBansView | -- |
| `admin.orgs` | `/admin/orgs` | AdminOrgsView | -- |
| `admin.projects` | `/admin/projects` | AdminProjectsView | -- |
| `admin.audit` | `/admin/audit` | AdminAuditView | -- |
| `admin.ops` | `/admin/ops` | AdminOpsView | -- |
| `admin.rateLimits` | `/admin/rate-limits` | AdminRateLimitsView | -- |
| `admin.metrics` | `/admin/metrics` | AdminMetricsView | -- |
| `admin.impersonate` | `/admin/impersonate` | AdminImpersonateLauncher | -- |

---

## 18. Backend API Reference

All endpoints require `require_admin` dependency (checks `principal.is_admin`, returns 403 if false).

### 18.1 User Management

| Method | Path | Request | Response | Description |
|---|---|---|---|---|
| GET | `/api/admin/users` | `?q=&status=` | `UserSummaryOut[]` | List users with optional search/filter |
| GET | `/api/admin/users/{uid}` | -- | `UserDetailOut` | Get single user detail |
| POST | `/api/admin/users/{uid}/ban` | `BanIn { reason: str }` | 204 | Ban user (reason 1-2000 chars) |
| POST | `/api/admin/users/{uid}/unban` | -- | 204 | Unban user |
| POST | `/api/admin/users/{uid}/delete` | -- | 204 | Soft delete (60-day grace) |
| POST | `/api/admin/users/{uid}/hard-delete` | -- | 204 | Permanent delete (after grace period) |
| POST | `/api/admin/users/{uid}/impersonate` | -- | `ImpersonateOut { session_id, access_token }` | Start impersonation |
| POST | `/api/admin/impersonate/stop` | -- | 204 | End impersonation |

### 18.2 Admin Management

| Method | Path | Request | Response | Description |
|---|---|---|---|---|
| GET | `/api/admin/admins` | -- | `AdminEntryOut[]` | List all admins |
| POST | `/api/admin/admins` | `AdminPromoteIn { user_id: UUID }` | 201 | Promote user to admin |
| DELETE | `/api/admin/admins/{uid}` | -- | 204 | Demote admin (fails with `admin/last-admin` if last) |

### 18.3 Organisation Management

| Method | Path | Request | Response | Description |
|---|---|---|---|---|
| GET | `/api/admin/orgs` | `?q=` | `OrgSummaryOut[]` | List all orgs |
| POST | `/api/admin/orgs/{oid}/force-delete` | -- | 204 | Force delete org + all projects |
| POST | `/api/admin/orgs/{oid}/force-transfer-original-creator` | `ForceTransferIn { target_user_id: UUID }` | 204 | Transfer Original Creator role |

### 18.4 Project & Resource Management

| Method | Path | Request | Response | Description |
|---|---|---|---|---|
| GET | `/api/admin/projects` | `?q=` | `ProjectSummaryOut[]` | List all projects |
| POST | `/api/admin/restore/{resource_type}/{resource_id}` | -- | `RestoreOut { restored: bool }` | Restore soft-deleted resource |

### 18.5 Audit

| Method | Path | Request | Response | Description |
|---|---|---|---|---|
| GET | `/api/admin/audit` | `AuditFilter` params | `AuditPageOut { items, next_cursor }` | Query audit log (cursor pagination) |
| POST | `/api/admin/audit/export` | `AuditFilter` body | `{ url, job_id }` | Export filtered audit to CSV |
| WS | `/ws/admin/tail` | JWT subprotocol | stream of `AuditEntryOut` | Live audit feed |

### 18.6 Rate Limits

| Method | Path | Request | Response | Description |
|---|---|---|---|---|
| GET | `/api/admin/rate-limits` | -- | `RateLimitPolicyOut[]` | List all rate limit policies |
| PATCH | `/api/admin/rate-limits/{key}` | `RateLimitPatchIn { window_sec: 1-86400, max_count: >=1 }` | 204 | Update rate limit policy |

### 18.7 Metrics & Operations

| Method | Path | Request | Response | Description |
|---|---|---|---|---|
| GET | `/api/admin/metrics` | -- | `MetricsOut` | Platform summary counts |
| POST | `/api/admin/graphrag/{cid}/reset` | -- | 204 | Reset GraphRAG config to idle |

### 18.8 IP Bans

| Method | Path | Request | Response | Description |
|---|---|---|---|---|
| GET | `/api/admin/ip-bans` | -- | `IpBanOut[]` | List all IP bans |
| POST | `/api/admin/ip-bans` | `IpBanIn { cidr: 1-64, reason: 1-1024 }` | 201 | Create IP ban |
| DELETE | `/api/admin/ip-bans/{bid}` | -- | 204 | Remove IP ban |

---

## 19. Type Definitions

**File**: `src/slices/admin/types/index.ts`

```ts
interface UserSummary {
  id: string
  email: string
  status: 'active' | 'pending' | 'banned' | 'deleted'
  email_verified: boolean
  created_at: string
}

interface UserDetail extends Omit<UserSummary, never> {
  is_admin: boolean
  banned_reason: string | null
  banned_at: string | null
  deleted_at: string | null
  last_login_at: string | null
  org_ids: string[]
  project_ids: string[]
}

interface AdminEntry {
  user_id: string
  promoted_by_user_id: string | null
  promoted_at: string
}

interface AuditEntry {
  id: number
  actor_user_id: string | null
  actor_ip: string | null
  action: string
  resource_type: string | null
  resource_id: string | null
  metadata: Record<string, unknown>
  session_id: string | null
  request_id: string | null
  created_at: string
}

interface AuditPage {
  items: AuditEntry[]
  next_cursor: number | null
}

interface AuditFilter {
  actor_user_id?: string
  resource_type?: string
  resource_id?: string
  action?: string
  from?: string
  to?: string
  ip_prefix?: string
  session_id?: string
  request_id?: string
  cursor?: number
  limit?: number
}

interface OrgSummary {
  id: string
  name: string
  creator_user_id: string
  deleted_at: string | null
  created_at: string
}

interface ProjectSummary {
  id: string
  name: string
  owner_user_id: string | null
  owner_org_id: string | null
  deleted_at: string | null
  created_at: string
}

interface RateLimitPolicy {
  key: string
  window_sec: number
  max_count: number
  scope: string
  updated_at: string
}

interface Metrics {
  total_users: number
  total_orgs: number
  total_projects: number
  total_audit_entries: number
}

interface ImpersonateResult {
  session_id: string
  access_token: string
}

interface IpBan {
  id: string
  cidr: string
  reason: string
  created_by_user_id: string
  banned_at: string
}
```

---

## 20. Accessibility

### 20.1 General

- All tables use `<th scope="col">` for column headers.
- Loading states use `role="status"` for screen reader announcements.
- Error states use `role="alert"` with `aria-live="assertive"` for immediate announcement.
- All form inputs have explicit `aria-label` attributes (provided via i18n keys).
- Destructive actions require confirmation dialogs, preventing accidental operations.
- Focus ring (`--focus-ring`) on all interactive elements.

### 20.2 Keyboard Navigation

- All views are fully keyboard-navigable.
- Tab order follows visual reading order (top to bottom, left to right).
- Confirmation dialogs trap focus while open.
- Tables support row-level focus for action button access.
- Forms: Enter key submits the form.

### 20.3 ARIA Labels

| Element | aria-label / aria-labelledby |
|---|---|
| Search input (users) | `$t('admin.users.search')` |
| Status select (users) | `$t('admin.users.status')` |
| Datetime inputs (audit) | `$t('admin.audit.from')`, `$t('admin.audit.to')` |
| Number inputs (rate limits) | `$t('admin.rateLimits.window')`, `$t('admin.rateLimits.maxCount')` |
| Target user input (impersonate) | `$t('admin.impersonation.targetPlaceholder')` |
| Resource type select (ops) | `$t('admin.ops.resourceType')` |

---

## 21. Responsive Behavior

All admin views follow the standard `AppShell` responsive behavior defined in `02-layout-shell.md`.

| Breakpoint | Layout Adjustments |
|---|---|
| >= 1280px (xl) | Full sidebar visible, tables at full width |
| 1024-1279px (lg) | Sidebar visible, tables may scroll horizontally |
| 768-1023px (md) | Sidebar collapsed to drawer, tables scroll horizontally (`overflow-x: auto`) |
| < 768px (sm) | Sidebar drawer, metric cards stack to 1 column, filter forms stack vertically |
| < 480px (xs) | Content padding 8px, inputs go full-width |

### Admin Home Navigation

- Desktop (>= 768px): flex-wrap row of nav links
- Mobile (< 768px): nav links stack vertically, full-width

### Tables on Mobile

All tables use `overflow-x: auto` wrapper for horizontal scrolling on narrow viewports. No column hiding -- all columns remain visible to preserve administrative data completeness.

### Audit Filters on Mobile

The 8-field filter form wraps naturally via `flex-wrap`. At small viewports, each input takes full width.

---

## 22. Error Handling Patterns

### 22.1 Network Errors

All API errors follow RFC 7807 Problem format. The `useAdminActions` composable handles errors uniformly via `onError` callbacks that call `useToast().error(message)`.

### 22.2 Error Toast Messages

| Error Key | Message |
|---|---|
| `admin.actionErrors.banFailed` | "Failed to ban user." |
| `admin.actionErrors.unbanFailed` | "Failed to unban user." |
| `admin.actionErrors.deleteFailed` | "Failed to delete user." |
| `admin.actionErrors.hardDeleteFailed` | "Failed to permanently delete user." |
| `admin.actionErrors.promoteFailed` | "Failed to promote user to admin." |
| `admin.actionErrors.demoteFailed` | "Failed to demote admin." |
| `admin.actionErrors.deleteOrgFailed` | "Failed to delete organisation." |
| `admin.actionErrors.createIpBanFailed` | "Failed to create IP ban." |
| `admin.actionErrors.removeIpBanFailed` | "Failed to remove IP ban." |
| `admin.actionErrors.rateLimitFailed` | "Failed to update rate limit." |
| `admin.actionErrors.restoreFailed` | "Failed to restore resource." |
| `admin.actionErrors.resetGraphragFailed` | "Failed to reset GraphRAG index." |

### 22.3 Special Error Handling

| Scenario | Detection | Display |
|---|---|---|
| Last admin demote | `isProblemWithType(e, 'admin/last-admin')` | `$t('admin.users.lastAdminDemote')` inline error |
| Audit export missing dates | Client-side check | `toast.warning($t('admin.audit.exportDateRequired'))` |
| Audit export failure | Catch block | `toast.error($t('admin.audit.exportFailed'))` |
| Impersonation start/end failure | Catch block | Inline error text below form |

---

## 23. Files Summary

### Views (12)

| File | Description |
|---|---|
| `src/slices/admin/views/AdminHomeView.vue` | Dashboard with nav links + metric summary cards |
| `src/slices/admin/views/AdminUsersView.vue` | User search, filter, list with ban/unban actions |
| `src/slices/admin/views/AdminUserDetailView.vue` | User detail with all management actions |
| `src/slices/admin/views/AdminAdminsView.vue` | Admin role promote/demote management |
| `src/slices/admin/views/AdminIpBansView.vue` | IP ban CIDR creation and removal |
| `src/slices/admin/views/AdminOrgsView.vue` | Organisation list with force-delete, restore, OC transfer |
| `src/slices/admin/views/AdminProjectsView.vue` | Read-only project listing |
| `src/slices/admin/views/AdminAuditView.vue` | Audit log search, filter, cursor pagination, CSV export |
| `src/slices/admin/views/AdminOpsView.vue` | GraphRAG reset + soft-delete resource restore |
| `src/slices/admin/views/AdminRateLimitsView.vue` | Rate limit inline editor |
| `src/slices/admin/views/AdminMetricsView.vue` | Platform metric cards |
| `src/slices/admin/views/AdminImpersonateLauncher.vue` | Impersonation session start/end |

### Components (2)

| File | Description |
|---|---|
| `src/slices/admin/components/AdminUserActions.vue` | Contextual user action buttons (ban, unban, soft/hard delete, impersonate) |
| `src/slices/admin/components/ImpersonationBanner.vue` | Fixed-top warning banner during impersonation |

### Composables (2)

| File | Description |
|---|---|
| `src/slices/admin/composables/useAdminActions.ts` | 13 shared TanStack mutations with error handling |
| `src/slices/admin/composables/useImpersonation.ts` | Impersonation session lifecycle management |

### Supporting Files

| File | Description |
|---|---|
| `src/slices/admin/api/admin.ts` | API client (25 endpoints) |
| `src/slices/admin/queries/index.ts` | TanStack Query key factory (9 key sets) |
| `src/slices/admin/types/index.ts` | 12 TypeScript interfaces |
| `src/slices/admin/stores/admin.ts` | Pinia store (currentTab + impersonatedBy) |
| `src/slices/admin/routes.ts` | 12 route definitions |
| `src/slices/admin/locales/en.json` | English i18n strings |
| `src/slices/admin/locales/zh-TW.json` | Traditional Chinese i18n strings |
| `src/slices/admin/index.ts` | Slice entry point + i18n registration |
