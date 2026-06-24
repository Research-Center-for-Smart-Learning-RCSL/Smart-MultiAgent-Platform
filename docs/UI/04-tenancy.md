# 04 — Tenancy

> Org and project management, membership, invites, and Original Creator transfer.
> All views render inside `AppShell` with 24px content padding.

---

## 1. OrgListView

**File**: `src/slices/tenancy/views/OrgListView.vue`
**Route**: `/orgs` (`tenancy.orgList`)
**Guards**: `requiresAuth`, `requiresVerifiedEmail`

### 1.1 Wireframe

```
┌──────────────────────────────────────────────────────────────┐
│  SPageHeader                                                 │
│  [breadcrumb: Home]                                          │
│  Organizations                           [+ Create org]     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ STable                                                 │  │
│  │ ┌──────────────────────┬───────────┬──────────────┬──┐ │  │
│  │ │ Name                 │ Role      │ Created      │  │ │  │
│  │ ├──────────────────────┼───────────┼──────────────┼──┤ │  │
│  │ │ Acme Corp            │ OC Owner  │ 2025-03-14   │ >│ │  │
│  │ │ DevTeam              │ Owner     │ 2025-04-01   │ >│ │  │
│  │ │ Research Lab          │ Member    │ 2025-05-22   │ >│ │  │
│  │ └──────────────────────┴───────────┴──────────────┴──┘ │  │
│  │                                                         │  │
│  │  Showing 1-3 of 3                                       │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Create org modal** (opens on "+ Create org" button click):

```
┌──────────────────────────────────────┐
│  Create Organization            [X]  │
├──────────────────────────────────────┤
│                                      │
│  Name *                              │
│  ┌──────────────────────────────┐    │
│  │                              │    │
│  └──────────────────────────────┘    │
│  1-200 characters                    │
│                                      │
├──────────────────────────────────────┤
│                     [Cancel] [Create]│
└──────────────────────────────────────┘
```

### 1.2 Behavior

**Data loading**:
- Query: `useQuery(tenancyKeys.orgs(), orgsApi.list)`
- Loading state: `STable` with `loading` prop (5 skeleton rows)
- Error state: `SAlert` variant `danger` with retry button

**Create organization**:
1. User clicks "+ Create org" (`SButton` variant `primary`, `icon-left: PlusIcon`)
2. `SModal` opens (size `sm`, title `$t('tenancy.org.createTitle')`)
3. Form: `SFormField` + `SInput` for name, help text `$t('tenancy.org.nameHelp')` ("1-200 characters")
4. Validation: name trimmed, 1-200 chars, non-empty
5. Submit calls `orgsApi.create({ name })`
6. On success: modal closes, query invalidated, toast success `$t('tenancy.org.created')`
7. On `NameTaken` (409): field error `$t('tenancy.org.nameTaken')`
8. On other error: `SAlert` variant `danger` inside modal

**Row click**: navigates to `tenancy.orgDetail` with `id` param.

**Empty state**: `SEmptyState` with `BuildingOffice2Icon`, text `$t('tenancy.org.empty')` ("No organizations yet"), action slot: `SButton` variant `primary` to open create modal.

### 1.3 Column Spec

| Column | Key | Sortable | Width | Align | Renderer |
|--------|-----|----------|-------|-------|----------|
| Name | `name` | yes | `auto` | left | Plain text, 14px, 500 weight |
| Role | `role` | yes | `120px` | left | `SBadge` (see role badge mapping below) |
| Created | `created_at` | yes | `140px` | left | Formatted date (`YYYY-MM-DD`) |
| Arrow | — | no | `40px` | center | `ChevronRightIcon` 16px `--color-muted` |

**Role badge mapping** (used throughout tenancy views):

| Role | Badge variant | Label |
|------|---------------|-------|
| Original Creator | `info` | `$t('tenancy.role.originalCreator')` |
| Owner | `neutral` | `$t('tenancy.role.owner')` |
| Member | `neutral` | `$t('tenancy.role.member')` |

The role column renders the user's membership role in that org. When the user is the Original Creator, the badge shows "Original Creator" (variant `info`). Otherwise it shows "Owner" or "Member".

### 1.4 Role-Based Visibility

| Element | Any authenticated user |
|---------|----------------------|
| View the page | Yes (shows only orgs user belongs to) |
| Create org button | Yes |
| Row navigation | Yes |

No role-based hiding within this view. The API returns only orgs the user is a member of.

### 1.5 Components Used

`SPageHeader`, `SButton`, `STable`, `SModal`, `SFormField`, `SInput`, `SBadge`, `SEmptyState`, `SAlert`, `ChevronRightIcon`, `PlusIcon`, `BuildingOffice2Icon`

### 1.6 Responsive Behavior

| Breakpoint | Adaptation |
|------------|------------|
| >= 768px | Full table with all columns |
| < 768px | Table hides "Created" column; name column takes remaining width |
| < 480px | Card list replaces table: each org as `SCard` showing name + role badge, full-width tap target |

---

## 2. OrgDetailView

**File**: `src/slices/tenancy/views/OrgDetailView.vue`
**Route**: `/orgs/:id` (`tenancy.orgDetail`)
**Guards**: `requiresAuth`

### 2.1 Wireframe

```
┌──────────────────────────────────────────────────────────────┐
│  SPageHeader                                                 │
│  [breadcrumb: Home > Organizations > Acme Corp]              │
│  Acme Corp  [Rename]                     [Members] [Transfer]│
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─── Settings ────────────────────────────────────────────┐ │
│  │                                                         │ │
│  │  Organization ID     5a3f8c...                          │ │
│  │  Created             2025-03-14 09:21                   │ │
│  │  Version             3                                  │ │
│  │  Default Project     Default Project (link)             │ │
│  │                                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─── Quotas ──────────────────────────────────────────────┐ │
│  │                                                         │ │
│  │  Members      3 / 50       Projects    2 / 20           │ │
│  │  Chatrooms    12 / 500     Agents      5 / 100          │ │
│  │  Workflows    1 / 50                                    │ │
│  │                                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─── Danger Zone ─────────────────────────────────────────┐ │
│  │                                                         │ │
│  │  Delete this organization                               │ │
│  │  Permanently removes the org and all its projects,      │ │
│  │  members, and data after a 60-day recovery window.      │ │
│  │                                          [Delete org]   │ │
│  │                                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Inline rename mode** (replaces org name in header):

```
┌──────────────────────────────────────────────────────────────┐
│  SPageHeader                                                 │
│  [breadcrumb: Home > Organizations > Acme Corp]              │
│  ┌────────────────────────┐  [Save] [Cancel]                 │
│  │ Acme Corp              │                                  │
│  └────────────────────────┘                                  │
├──────────────────────────────────────────────────────────────┤
```

**Soft-deleted state** (when `deleted_at` is set):

```
┌──────────────────────────────────────────────────────────────┐
│  SPageHeader                                                 │
│  [breadcrumb: Home > Organizations > Acme Corp]              │
│  Acme Corp  [SBadge: Deleted]            [Restore]           │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  SAlert variant=warning                                 │ │
│  │  This organization was deleted on 2025-06-01.           │ │
│  │  It will be permanently removed on 2025-07-31.          │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─── Settings ────────────────────────────────────────────┐ │
│  │  (read-only, all actions disabled)                      │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 Behavior

**Data loading**:
- Query: `useQuery(tenancyKeys.org(id), () => orgsApi.get(id))`
- Quotas: `useQuery(tenancyKeys.orgQuotas(id), () => orgsApi.quotas(id), { retry: false })`
- Quotas load independently; failure is silently ignored (non-critical data)
- Loading state: `SLoadingSpinner` centered with text `$t('common.loading')`
- Error state: `SAlert` variant `danger` with `$t('tenancy.org.loadError')` and retry button
- 404: redirect to `tenancy.orgList` with toast `$t('tenancy.org.notFound')`

**Rename** (uses `useInlineRename` composable):
1. User clicks "Rename" button (`SButton` variant `ghost`, size `sm`)
2. Header switches to inline form: `SInput` pre-filled with current name + Save/Cancel buttons
3. Save calls `orgsApi.rename(id, { name }, { 'If-Match': org.version })`
4. On success: inline form closes, `org` ref updated with response (bumped version), toast success
5. On `NameTaken` (409): input error `$t('tenancy.org.nameTaken')`
6. On `VersionMismatch` (412): toast warning `$t('common.versionConflict')`, query refetched
7. Cancel: reverts to display mode

**Delete** (Original Creator only):
1. User clicks "Delete org" (`SButton` variant `danger`)
2. `SConfirmDialog` opens with variant `error`:
   - Title: `$t('tenancy.org.deleteTitle')`
   - Body: `$t('tenancy.org.deleteBody')` — warns about 60-day recovery and cascade
   - Prompt mode: user must type the org name to confirm (regex validation)
   - Confirm label: `$t('tenancy.org.deleteConfirm')` ("Delete organization")
3. On confirm: calls `orgsApi.remove(id)`
4. On success: navigate to `tenancy.orgList`, toast success `$t('tenancy.org.deleted')`

**Restore** (Original Creator only, visible when `deleted_at` is set):
1. User clicks "Restore" (`SButton` variant `primary`)
2. `SConfirmDialog` opens with variant `info`:
   - Title: `$t('tenancy.org.restoreTitle')`
   - Body: `$t('tenancy.org.restoreBody')`
   - Confirm label: `$t('tenancy.org.restoreConfirm')` ("Restore organization")
3. On confirm: calls `orgsApi.restore(id)`
4. On success: query refetched, alert dismissed, toast success

**Navigation buttons**:
- "Members" (`SButton` variant `secondary`, `as: router-link`, `to: tenancy.orgMembers`)
- "Transfer" (`SButton` variant `secondary`, `as: router-link`, `to: tenancy.orgTransfer`)
- Both placed in `SPageHeader` actions slot

### 2.3 Settings Card Fields

| Field | Value | Format |
|-------|-------|--------|
| Organization ID | `org.id` | Monospace, truncated with copy button (`ClipboardIcon` 16px) |
| Created | `org.created_at` | `YYYY-MM-DD HH:mm` localized |
| Version | `org.version` | Plain number |
| Default Project | `org.default_project_id` | `router-link` to `tenancy.projectDetail` |

### 2.4 Quotas Card

Quotas display as a 2-column grid (3 rows) inside an `SCard` variant `bordered`.

| Quota | Format | Threshold coloring |
|-------|--------|-------------------|
| Members | `{count} / {target}` | `--color-warning` at >= 80%, `--color-danger` at >= 95% |
| Projects | `{count} / {target}` | Same |
| Chatrooms | `{count} / {target}` | Same |
| Agents | `{count} / {target}` | Same |
| Workflows | `{count} / {target}` | Same |

Each quota item: label (12px, `--color-muted`, uppercase) above value (20px, 600 weight). Color transitions based on usage percentage against `advisory_targets`. If quotas fail to load, the card is hidden entirely (no error shown).

### 2.5 Danger Zone Card

- `SCard` variant `bordered` with top border 2px `--color-danger`
- Section title "Danger Zone" in `--color-danger` 18px 600 weight
- Description text in `--color-muted` 14px
- Delete button aligned right within the card

### 2.6 Role-Based Visibility

| Element | Original Creator | Owner | Member |
|---------|-----------------|-------|--------|
| View page | Yes | Yes | Yes |
| Rename button | Yes | Yes | No |
| Members link | Yes | Yes | Yes (view-only) |
| Transfer link | Yes | No | No |
| Quotas card | Yes | Yes | Yes |
| Danger zone | Yes | No | No |
| Delete button | Yes | No | No |
| Restore button | Yes | No | No |

Role determination: the org member list is fetched, and the current user's role is checked. The `is_original_creator` flag distinguishes OC from regular Owner. When the org is soft-deleted, rename and delete are hidden; only restore is shown (OC only).

### 2.7 Components Used

`SPageHeader`, `SBreadcrumb`, `SCard`, `SButton`, `SInput`, `SBadge`, `SAlert`, `SConfirmDialog`, `SLoadingSpinner`, `STooltip`, `PencilIcon`, `UserGroupIcon`, `ArrowsRightLeftIcon`, `ClipboardIcon`, `TrashIcon`, `ArrowPathIcon`

### 2.8 Responsive Behavior

| Breakpoint | Adaptation |
|------------|------------|
| >= 768px | Settings and Quotas cards side by side (2 columns) |
| < 768px | Cards stack vertically, full width |
| < 480px | Quotas grid collapses to single column; header action buttons stack below title |

---

## 3. OrgMembersView

**File**: `src/slices/tenancy/views/OrgMembersView.vue`
**Route**: `/orgs/:id/members` (`tenancy.orgMembers`)
**Guards**: `requiresAuth`

### 3.1 Wireframe

```
┌──────────────────────────────────────────────────────────────┐
│  SPageHeader                                                 │
│  [breadcrumb: Home > Organizations > Acme Corp > Members]    │
│  Members                                                     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─── Invite Member (SCard) ───────────────────────────────┐ │
│  │                                                         │ │
│  │  Email *                        Role                    │ │
│  │  ┌────────────────────────┐     ┌──────────────┐        │ │
│  │  │ user@example.com       │     │ Member    [v]│        │ │
│  │  └────────────────────────┘     └──────────────┘        │ │
│  │                                         [Send invite]   │ │
│  │                                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─── Member List (STable) ────────────────────────────────┐ │
│  │ ┌────────┬──────────────────┬──────────┬────────┬─────┐ │ │
│  │ │ Avatar │ Email            │ Role     │ Joined │     │ │ │
│  │ ├────────┼──────────────────┼──────────┼────────┼─────┤ │ │
│  │ │ [JD]   │ jd@acme.com     │ OC Owner │ 03-14  │     │ │ │
│  │ │ [AS]   │ alice@acme.com  │ Owner    │ 03-20  │ ... │ │ │
│  │ │ [BK]   │ bob@acme.com   │ Member   │ 04-01  │ ... │ │ │
│  │ └────────┴──────────────────┴──────────┴────────┴─────┘ │ │
│  │                                                         │ │
│  │  Showing 1-3 of 3                                       │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Row actions dropdown** (three-dot menu per row, `SDropdown`):

```
┌──────────────────────┐
│ Promote to Owner     │
│ ──────────────────── │
│ Remove member        │   (danger)
└──────────────────────┘
```

### 3.2 Behavior

**Data loading**:
- Query: `useQuery(tenancyKeys.orgMembers(orgId), () => orgsApi.listMembers(orgId))`
- Loading: `STable` with `loading` prop
- Error: `SAlert` variant `danger` with retry

**Invite member** (Owner/OC only):
1. Invite form rendered inside `SCard` variant `flat` above the member table
2. Email field: `SFormField` + `SInput` type `email`, required
3. Role select: `SFormField` + `SSelect` with options `[{ value: 'member', label: $t('tenancy.role.member') }, { value: 'owner', label: $t('tenancy.role.owner') }]`, default `member`
4. Submit button: `SButton` variant `primary`, text `$t('tenancy.member.sendInvite')`, loading state during API call
5. Calls `orgsApi.invite(orgId, { email, role })`
6. On success: form cleared, toast success `$t('tenancy.member.invited')`
7. On `InviteDuplicate` (409): field error on email `$t('tenancy.member.alreadyInvited')`
8. On rate limit (429): `SAlert` variant `warning` `$t('tenancy.member.rateLimited')`

**Change role** (Owner/OC only):
1. User opens row actions `SDropdown`
2. Clicks "Promote to Owner" or "Demote to Member"
3. `SConfirmDialog` variant `info`:
   - Title: `$t('tenancy.member.changeRoleTitle')`
   - Body: confirms the role change for the user email
4. Calls `orgsApi.setRole(orgId, userId, { role })`
5. On success: query invalidated, toast success

**Remove member** (Owner/OC only):
1. User clicks "Remove member" in row actions `SDropdown` (danger item)
2. `SConfirmDialog` variant `error`:
   - Title: `$t('tenancy.member.removeTitle')`
   - Body: `$t('tenancy.member.removeBody')` — warns about cascade (removed from all org projects, key carries revoked)
   - Confirm label: `$t('tenancy.member.removeConfirm')`
3. Calls `orgsApi.removeMember(orgId, userId)`
4. On success: query invalidated, toast success
5. On `OriginalCreatorConflict` (409): toast error `$t('tenancy.member.cannotRemoveOC')`

### 3.3 Column Spec

| Column | Key | Sortable | Width | Align | Renderer |
|--------|-----|----------|-------|-------|----------|
| Avatar | — | no | `48px` | center | `SAvatar` size `sm` with `email` as name |
| Email | `email` | yes | `auto` | left | Plain text, 14px; current user highlighted with `$t('tenancy.member.you')` suffix in `--color-muted` |
| Role | `role` | yes | `140px` | left | `SBadge` per role mapping; OC shows `info` variant with `$t('tenancy.role.originalCreator')` |
| Joined | `joined_at` | yes | `120px` | left | Relative time (e.g., "3 months ago") via `useTimeAgo` |
| Actions | — | no | `48px` | center | `SDropdown` with `EllipsisVerticalIcon` trigger (see below) |

### 3.4 Row Actions Dropdown

| Item | Condition | Danger | Action |
|------|-----------|--------|--------|
| Promote to Owner | `role === 'member'` AND viewer is Owner/OC | no | Change role |
| Demote to Member | `role === 'owner'` AND NOT OC AND viewer is OC | no | Change role |
| Remove member | NOT OC AND viewer is Owner/OC | yes | Remove member |

**Original Creator row**: no actions dropdown rendered. The row displays the OC badge and no action trigger. This is a hard rule — OC cannot be demoted or removed via this UI.

**Self row**: no actions dropdown. Users cannot modify their own role or remove themselves from this view. Self-removal is handled via account settings.

### 3.5 Role-Based Visibility

| Element | Original Creator | Owner | Member |
|---------|-----------------|-------|--------|
| View member list | Yes | Yes | Yes |
| Invite form | Yes | Yes | No |
| Row actions (other owners) | Yes (demote, remove) | No | No |
| Row actions (members) | Yes (promote, remove) | Yes (promote, remove) | No |
| OC row actions | Never shown | Never shown | Never shown |

### 3.6 Empty State

When the member list is empty (should not occur in practice since the OC is always a member), `STable` renders `SEmptyState` with `UserGroupIcon` and text `$t('tenancy.member.empty')`.

### 3.7 Components Used

`SPageHeader`, `SBreadcrumb`, `SCard`, `STable`, `SAvatar`, `SBadge`, `SButton`, `SFormField`, `SInput`, `SSelect`, `SDropdown`, `SConfirmDialog`, `SAlert`, `SEmptyState`, `UserGroupIcon`, `EllipsisVerticalIcon`, `PlusIcon`

### 3.8 Responsive Behavior

| Breakpoint | Adaptation |
|------------|------------|
| >= 768px | Full table; invite form fields in a row |
| < 768px | Table hides "Joined" column; invite form fields stack vertically |
| < 480px | Table hides Avatar column; role badge moves below email in the name cell |

---

## 4. OrgTransferView

**File**: `src/slices/tenancy/views/OrgTransferView.vue`
**Route**: `/orgs/:id/transfer` (`tenancy.orgTransfer`)
**Guards**: `requiresAuth`

### 4.1 Wireframe — No Pending Transfer (OC view)

```
┌──────────────────────────────────────────────────────────────┐
│  SPageHeader                                                 │
│  [breadcrumb: Home > Organizations > Acme Corp > Transfer]   │
│  Original Creator Transfer                                   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  SAlert variant=info                                    │ │
│  │  Transferring Original Creator status is irreversible.  │ │
│  │  The target user must already be an Owner of this org.  │ │
│  │  The transfer expires after 7 days if not accepted.     │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─── Initiate Transfer (SCard) ───────────────────────────┐ │
│  │                                                         │ │
│  │  Target user ID *                                       │ │
│  │  ┌──────────────────────────────────────────────────┐   │ │
│  │  │                                                  │   │ │
│  │  └──────────────────────────────────────────────────┘   │ │
│  │  Must be an existing Owner of this organization.        │ │
│  │                                                         │ │
│  │                              [Initiate transfer]        │ │
│  │                                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 Wireframe — Pending Transfer (OC view, initiator)

```
┌──────────────────────────────────────────────────────────────┐
│  SPageHeader                                                 │
│  Original Creator Transfer                                   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─── Pending Transfer (SCard) ────────────────────────────┐ │
│  │                                                         │ │
│  │  SBadge variant=warning: "Pending"                      │ │
│  │                                                         │ │
│  │  Target         alice@acme.com (user ID)                │ │
│  │  Initiated      2025-06-17 14:30                        │ │
│  │  Expires        2025-06-24 14:30                        │ │
│  │                                                         │ │
│  │                              [Cancel transfer]          │ │
│  │                                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 4.3 Wireframe — Pending Transfer (Target view, acceptor)

```
┌──────────────────────────────────────────────────────────────┐
│  SPageHeader                                                 │
│  Original Creator Transfer                                   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─── Pending Transfer (SCard) ────────────────────────────┐ │
│  │                                                         │ │
│  │  SAlert variant=info                                    │ │
│  │  You have been selected to become the Original Creator  │ │
│  │  of this organization.                                  │ │
│  │                                                         │ │
│  │  Initiated by    jd@acme.com                            │ │
│  │  Expires         2025-06-24 14:30                       │ │
│  │                                                         │ │
│  │                       [Decline]    [Accept transfer]    │ │
│  │                                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 4.4 Behavior

**Data loading**:
- Query: `useQuery(tenancyKeys.orgTransfers(orgId), () => orgsApi.listTransfers(orgId))`
- Filters for the first transfer with `state === 'PENDING'`
- Loading: `SLoadingSpinner`
- Error: `SAlert` variant `danger` with retry

**Computed flags**:
- `isInitiator`: `transfer.initiator_user_id === session.userId`
- `isTarget`: `transfer.target_user_id === session.userId`
- `isOC`: current user has `is_original_creator === true` on this org

**Initiate transfer** (OC only, no pending transfer):
1. User enters target user ID in `SInput`
2. Clicks "Initiate transfer" (`SButton` variant `primary`)
3. `SConfirmDialog` variant `warning`:
   - Title: `$t('tenancy.transfer.initiateTitle')`
   - Body: `$t('tenancy.transfer.initiateBody')` — warns about irreversibility
   - Confirm label: `$t('tenancy.transfer.initiateConfirm')`
4. Calls `orgsApi.initiateTransfer(orgId, { target_user_id })`
5. On success: query invalidated, view switches to pending state
6. On `TransferConflict` (409): toast error `$t('tenancy.transfer.alreadyPending')`
7. On `MemberNotFound` (404): field error `$t('tenancy.transfer.targetNotOwner')` ("Target must be an existing Owner")

**Cancel transfer** (initiator only):
1. User clicks "Cancel transfer" (`SButton` variant `danger`)
2. `SConfirmDialog` variant `error`:
   - Title: `$t('tenancy.transfer.cancelTitle')`
   - Confirm label: `$t('tenancy.transfer.cancelConfirm')`
3. Calls `orgsApi.cancelTransfer(orgId, transferId)`
4. On success: query invalidated, view returns to initiate form

**Accept transfer** (target only):
1. User clicks "Accept transfer" (`SButton` variant `primary`)
2. `SConfirmDialog` variant `warning`:
   - Title: `$t('tenancy.transfer.acceptTitle')`
   - Body: `$t('tenancy.transfer.acceptBody')` — confirms becoming OC
3. Calls `orgsApi.acceptTransfer(orgId, transferId)`
4. On success: query invalidated, toast success, navigate to `tenancy.orgDetail`
5. On `TransferConflict` (409): toast error (transfer may have been cancelled or expired)

**Decline** (target only):
1. User clicks "Decline" (`SButton` variant `secondary`)
2. Calls `orgsApi.cancelTransfer(orgId, transferId)` (same endpoint, different user context)
3. On success: query invalidated

### 4.5 Role-Based Visibility

| Element | Original Creator | Owner (target) | Owner (non-target) | Member |
|---------|-----------------|---------------|--------------------:|--------|
| View page | Yes | Yes | Yes (read-only) | No (redirect to orgDetail) |
| Initiate form | Yes (no pending) | No | No | No |
| Cancel button | Yes (is initiator) | No | No | No |
| Accept button | No | Yes | No | No |
| Decline button | No | Yes | No | No |

Members who navigate to this route see a "not authorized" redirect to `tenancy.orgDetail`.

### 4.6 Components Used

`SPageHeader`, `SBreadcrumb`, `SCard`, `SAlert`, `SBadge`, `SButton`, `SFormField`, `SInput`, `SConfirmDialog`, `SLoadingSpinner`, `ArrowsRightLeftIcon`

### 4.7 Responsive Behavior

| Breakpoint | Adaptation |
|------------|------------|
| >= 768px | Card max-width 600px centered |
| < 768px | Card full width |
| < 480px | Accept/Decline buttons stack vertically, full width |

---

## 5. ProjectListView

**File**: `src/slices/tenancy/views/ProjectListView.vue`
**Route**: `/projects` (`tenancy.projectList`)
**Guards**: `requiresAuth`, `requiresVerifiedEmail`

### 5.1 Wireframe

```
┌──────────────────────────────────────────────────────────────┐
│  SPageHeader                                                 │
│  [breadcrumb: Home]                                          │
│  Projects                                [+ Create project]  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  STabs: [All] [Personal] [Org: Acme Corp] [Org: DevTeam]    │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ STable                                                 │  │
│  │ ┌────────────────┬──────────┬──────────┬──────────┬──┐ │  │
│  │ │ Name           │ Owner    │ Role     │ Created  │  │ │  │
│  │ ├────────────────┼──────────┼──────────┼──────────┼──┤ │  │
│  │ │ Default Project│ Acme Corp│ Owner    │ 03-14    │ >│ │  │
│  │ │ My Sandbox     │ Personal │ Owner    │ 04-10    │ >│ │  │
│  │ │ Research       │ DevTeam  │ Member   │ 05-01    │ >│ │  │
│  │ └────────────────┴──────────┴──────────┴──────────┴──┘ │  │
│  │                                                         │  │
│  │  Showing 1-3 of 3                                       │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Create project modal**:

```
┌──────────────────────────────────────┐
│  Create Project                 [X]  │
├──────────────────────────────────────┤
│                                      │
│  Owner type *                        │
│  ┌──────────────────────────────┐    │
│  │ Personal                 [v] │    │
│  └──────────────────────────────┘    │
│                                      │
│  Organization *        (if org)      │
│  ┌──────────────────────────────┐    │
│  │ Acme Corp                [v] │    │
│  └──────────────────────────────┘    │
│                                      │
│  Name *                              │
│  ┌──────────────────────────────┐    │
│  │                              │    │
│  └──────────────────────────────┘    │
│  1-200 characters                    │
│                                      │
├──────────────────────────────────────┤
│                     [Cancel] [Create]│
└──────────────────────────────────────┘
```

### 5.2 Behavior

**Data loading**:
- Tab selection drives the query scope:
  - "All": `useQuery(tenancyKeys.projects('all', null), () => projectsApi.list())`
  - "Personal": `useQuery(tenancyKeys.projects('user', userId), () => projectsApi.list('user', userId))`
  - Per-org tab: `useQuery(tenancyKeys.projects('org', orgId), () => projectsApi.list('org', orgId))`
- Tab list is derived from the user's org list: one tab per org the user belongs to, plus "All" and "Personal"
- Loading: `STable` with `loading` prop
- Error: `SAlert` variant `danger` with retry

**Tabs construction**:
- `tabs` computed from `useQuery(tenancyKeys.orgs(), orgsApi.list)`
- Format: `[{ key: 'all', label: $t('tenancy.project.tabAll') }, { key: 'personal', label: $t('tenancy.project.tabPersonal') }, ...orgs.map(o => ({ key: o.id, label: o.name }))]`
- Active tab stored in `route.query.scope` and `route.query.ownerId` for URL persistence
- Tab change updates route query params without full navigation

**Create project**:
1. User clicks "+ Create project" (`SButton` variant `primary`, `icon-left: PlusIcon`)
2. `SModal` opens (size `sm`, title `$t('tenancy.project.createTitle')`)
3. Owner type: `SFormField` + `SSelect` options `[{ value: 'user', label: $t('tenancy.project.ownerPersonal') }, { value: 'org', label: $t('tenancy.project.ownerOrg') }]`
4. When `owner_type === 'org'`: `SFormField` + `SSelect` listing user's orgs (only orgs where user is Owner/OC)
5. Name: `SFormField` + `SInput`, 1-200 chars
6. Submit: `projectsApi.create({ owner_type, owner_id, name })`
7. On success: modal closes, query invalidated, toast success, navigate to new project detail
8. On `NameTaken` (409): field error on name
9. On `ProjectOwnerRequired` (422): field error on org select

**Row click**: navigates to `tenancy.projectDetail` with `id` param.

### 5.3 Column Spec

| Column | Key | Sortable | Width | Align | Renderer |
|--------|-----|----------|-------|-------|----------|
| Name | `name` | yes | `auto` | left | Plain text, 14px, 500 weight |
| Owner | `owner` | yes | `160px` | left | Org name or `$t('tenancy.project.personal')` with `UserIcon`/`BuildingOffice2Icon` prefix (16px) |
| Role | `role` | yes | `120px` | left | `SBadge` per role mapping |
| Created | `created_at` | yes | `120px` | left | `YYYY-MM-DD` |
| Arrow | — | no | `40px` | center | `ChevronRightIcon` 16px `--color-muted` |

### 5.4 Empty State

`SEmptyState` with `FolderIcon`, text varies by active tab:
- All: `$t('tenancy.project.emptyAll')` ("No projects yet")
- Personal: `$t('tenancy.project.emptyPersonal')` ("No personal projects yet")
- Org tab: `$t('tenancy.project.emptyOrg', { org: orgName })` ("No projects in {org} yet")

Action slot: `SButton` variant `primary` to open create modal.

### 5.5 Role-Based Visibility

| Element | Any authenticated user |
|---------|----------------------|
| View page | Yes (shows only projects user has access to) |
| Create button | Yes |
| Org tabs | Only orgs user belongs to |
| Create under org | Only if user is Owner/OC in that org |

### 5.6 Components Used

`SPageHeader`, `SBreadcrumb`, `STabs`, `STable`, `SModal`, `SButton`, `SFormField`, `SInput`, `SSelect`, `SBadge`, `SEmptyState`, `SAlert`, `FolderIcon`, `PlusIcon`, `ChevronRightIcon`, `UserIcon`, `BuildingOffice2Icon`

### 5.7 Responsive Behavior

| Breakpoint | Adaptation |
|------------|------------|
| >= 768px | Full table with all columns; tabs horizontal |
| < 768px | Table hides "Owner" and "Created" columns; tabs scroll horizontally |
| < 480px | Card list replaces table: each project as `SCard` showing name + owner + role badge |

---

## 6. ProjectDetailView

**File**: `src/slices/tenancy/views/ProjectDetailView.vue`
**Route**: `/projects/:id` (`tenancy.projectDetail`)
**Guards**: `requiresAuth`

### 6.1 Wireframe

```
┌──────────────────────────────────────────────────────────────┐
│  SPageHeader                                                 │
│  [breadcrumb: Home > Projects > Default Project]             │
│  Default Project  [Rename]                        [Members]  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─── Settings ────────────────────────────────────────────┐ │
│  │                                                         │ │
│  │  Project ID       9b2c7d...                             │ │
│  │  Owner            Acme Corp (link) | Personal           │ │
│  │  Created by       jd@acme.com                           │ │
│  │  Created          2025-03-14 09:21                      │ │
│  │  Version          5                                     │ │
│  │                                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─── Danger Zone ─────────────────────────────────────────┐ │
│  │                                                         │ │
│  │  Delete this project                                    │ │
│  │  Permanently removes the project and all its data       │ │
│  │  after a 60-day recovery window.                        │ │
│  │                                          [Delete project]│ │
│  │                                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Soft-deleted state** follows the same pattern as OrgDetailView section 2.1: `SAlert` warning banner with deletion and permanent removal dates, restore button visible to authorized users, all other actions disabled.

### 6.2 Behavior

**Data loading**:
- Query: `useQuery(tenancyKeys.project(id), () => projectsApi.get(id))`
- Loading: `SLoadingSpinner`
- Error: `SAlert` variant `danger` with retry
- 404: redirect to `tenancy.projectList` with toast

**Rename** (uses `useInlineRename`, same pattern as OrgDetailView):
1. Click "Rename" -> inline `SInput` with Save/Cancel
2. PATCH with `If-Match` header
3. Handles `NameTaken` (409) and `VersionMismatch` (412)

**Delete** (Project Owner only):
1. Click "Delete project" (`SButton` variant `danger`)
2. `SConfirmDialog` variant `error` with prompt (type project name to confirm)
3. Calls `projectsApi.remove(id)`
4. On success: navigate to `tenancy.projectList`, toast success

**Restore** (Project Owner only, when soft-deleted):
1. Click "Restore" (`SButton` variant `primary`)
2. `SConfirmDialog` variant `info`
3. Calls `projectsApi.restore(id)`
4. On success: query refetched

### 6.3 Settings Card Fields

| Field | Value | Format |
|-------|-------|--------|
| Project ID | `project.id` | Monospace, truncated with copy button |
| Owner | `project.owner_type` + name | If org: `router-link` to `tenancy.orgDetail`; if personal: `$t('tenancy.project.personal')` with `UserIcon` |
| Created by | `project.created_by_user_id` | Email or user ID |
| Created | `project.created_at` | `YYYY-MM-DD HH:mm` |
| Version | `project.version` | Plain number |

### 6.4 Role-Based Visibility

| Element | Org OC | Org Owner | Project Owner (direct) | Project Member |
|---------|--------|-----------|----------------------|----------------|
| View page | Yes | Yes | Yes | Yes |
| Rename | Yes | Yes | Yes | No |
| Members link | Yes | Yes | Yes | No |
| Danger zone | Yes | Yes | Yes | No |
| Delete | Yes | Yes | Yes | No |
| Restore | Yes | Yes | Yes | No |

Org Owners are implicit Project Owners on all org-owned projects. This determines visibility without explicit project membership.

### 6.5 Components Used

`SPageHeader`, `SBreadcrumb`, `SCard`, `SButton`, `SInput`, `SBadge`, `SAlert`, `SConfirmDialog`, `SLoadingSpinner`, `STooltip`, `PencilIcon`, `UserGroupIcon`, `ClipboardIcon`, `TrashIcon`, `ArrowPathIcon`, `UserIcon`, `BuildingOffice2Icon`

### 6.6 Responsive Behavior

Same as OrgDetailView section 2.8: cards stack vertically below 768px, header action buttons stack below title at 480px.

---

## 7. ProjectMembersView

**File**: `src/slices/tenancy/views/ProjectMembersView.vue`
**Route**: `/projects/:id/members` (`tenancy.projectMembers`)
**Guards**: `requiresAuth`

### 7.1 Wireframe

Identical layout to OrgMembersView (section 3.1) with two differences:
- Breadcrumb: `Home > Projects > Default Project > Members`
- No `is_original_creator` flag on any row (projects have no OC concept)

```
┌──────────────────────────────────────────────────────────────┐
│  SPageHeader                                                 │
│  [breadcrumb: Home > Projects > Default Project > Members]   │
│  Members                                                     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─── Invite Member (SCard) ───────────────────────────────┐ │
│  │  Email *                        Role                    │ │
│  │  ┌────────────────────────┐     ┌──────────────┐        │ │
│  │  │ user@example.com       │     │ Member    [v]│        │ │
│  │  └────────────────────────┘     └──────────────┘        │ │
│  │                                         [Send invite]   │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─── Member List (STable) ────────────────────────────────┐ │
│  │ ┌────────┬──────────────────┬──────────┬────────┬─────┐ │ │
│  │ │ Avatar │ Email            │ Role     │ Joined │     │ │ │
│  │ ├────────┼──────────────────┼──────────┼────────┼─────┤ │ │
│  │ │ [AS]   │ alice@acme.com  │ Owner    │ 03-20  │ ... │ │ │
│  │ │ [BK]   │ bob@acme.com   │ Member   │ 04-01  │ ... │ │ │
│  │ └────────┴──────────────────┴──────────┴────────┴─────┘ │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 7.2 Behavior

Same interaction model as OrgMembersView (section 3.2) with project-scoped API calls:

| Operation | API call |
|-----------|----------|
| List members | `projectsApi.listMembers(projectId)` |
| Invite | `projectsApi.invite(projectId, { email, role })` |
| Change role | `projectsApi.setRole(projectId, userId, { role })` |
| Remove | `projectsApi.removeMember(projectId, userId)` |

**Key difference from org members**: there is no Original Creator concept at the project level. All Owners can be demoted or removed. Org Owners who inherit project-level access appear in the member list with a `SBadge` variant `neutral` labeled `$t('tenancy.member.inherited')` ("Inherited") next to their role badge. Inherited members cannot be removed or demoted via project-level actions.

### 7.3 Column Spec

| Column | Key | Sortable | Width | Align | Renderer |
|--------|-----|----------|-------|-------|----------|
| Avatar | — | no | `48px` | center | `SAvatar` size `sm` |
| Email | `email` | yes | `auto` | left | Same as OrgMembersView |
| Role | `role` | yes | `140px` | left | `SBadge`; inherited members show additional `SBadge` variant `neutral` "Inherited" |
| Joined | `joined_at` | yes | `120px` | left | Relative time |
| Actions | — | no | `48px` | center | `SDropdown` |

### 7.4 Row Actions Dropdown

| Item | Condition | Danger | Action |
|------|-----------|--------|--------|
| Promote to Owner | `role === 'member'` AND not inherited AND viewer is Owner | no | Change role |
| Demote to Member | `role === 'owner'` AND not inherited AND viewer is Owner | no | Change role |
| Remove member | Not inherited AND viewer is Owner | yes | Remove member |

Inherited members (org Owners): no action dropdown rendered. A `STooltip` on hover explains `$t('tenancy.member.inheritedTooltip')` ("This user has access as an org Owner. Manage via org settings.").

### 7.5 Role-Based Visibility

| Element | Org Owner (inherited) | Project Owner | Project Member |
|---------|----------------------|---------------|----------------|
| View member list | Yes | Yes | Yes |
| Invite form | Yes | Yes | No |
| Row actions | Yes | Yes | No |

### 7.6 Components Used

Same as OrgMembersView (section 3.7), plus `STooltip` for inherited member explanation.

### 7.7 Responsive Behavior

Same as OrgMembersView (section 3.8).

---

## 8. InboxInvitesView

**File**: `src/slices/tenancy/views/InboxInvitesView.vue`
**Route**: `/invites` (`tenancy.inbox`)
**Guards**: `requiresAuth`

### 8.1 Wireframe

```
┌──────────────────────────────────────────────────────────────┐
│  SPageHeader                                                 │
│  [breadcrumb: Home]                                          │
│  Invitations                                                 │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  SAlert variant=warning  (only if email unverified)     │ │
│  │  Your email is not verified. You must verify your       │ │
│  │  email before accepting invitations.                    │ │
│  │                                   [Verify now]          │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─── Invite Card ─────────────────────────────────────────┐ │
│  │                                                         │ │
│  │  [BuildingOffice2Icon]  Acme Corp                       │ │
│  │  Invited as: Owner                SBadge:info           │ │
│  │  Expires: 2025-07-01                                    │ │
│  │                                                         │ │
│  │                             [Reject]   [Accept]         │ │
│  │                                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─── Invite Card ─────────────────────────────────────────┐ │
│  │                                                         │ │
│  │  [FolderIcon]  Research (DevTeam)                       │ │
│  │  Invited as: Member               SBadge:neutral        │ │
│  │  Expires: 2025-07-05                                    │ │
│  │                                                         │ │
│  │                             [Reject]   [Accept]         │ │
│  │                                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 8.2 Behavior

**Data loading**:
- Query: `useQuery(tenancyKeys.invites('pending'), () => invitesApi.list({ state: 'pending' }))`
- Loading: 3 `SSkeleton` variant `rect` cards stacked
- Error: `SAlert` variant `danger` with retry

**Email verification warning**:
- Visible when `session.isVerified === false`
- `SAlert` variant `warning` with action slot linking to `/verify-email`
- Persistent across the session — not dismissible

**Accept invite**:
1. User clicks "Accept" (`SButton` variant `primary`, size `sm`)
2. Button enters loading state
3. Calls `invitesApi.accept(inviteId)`
4. On success: invite removed from list (optimistic update), toast success `$t('tenancy.invite.accepted', { name: scope_name })`
5. On `InviteExpired` (410): invite removed, toast warning `$t('tenancy.invite.expired')`
6. On unverified email (`/auth/email-unverified` problem type): set error discriminator to `'unverified'`, show the verification warning alert if not already visible
7. On other error: toast error

**Reject invite**:
1. User clicks "Reject" (`SButton` variant `secondary`, size `sm`)
2. `SConfirmDialog` variant `warning`:
   - Title: `$t('tenancy.invite.rejectTitle')`
   - Body: `$t('tenancy.invite.rejectBody', { name: scope_name })`
   - Confirm label: `$t('tenancy.invite.rejectConfirm')`
3. Calls `invitesApi.reject(inviteId)`
4. On success: invite removed from list, toast success

### 8.3 Invite Card Layout

Each invite renders as an `SCard` variant `bordered` with the following structure:

| Element | Content |
|---------|---------|
| Icon | `BuildingOffice2Icon` for org scope, `FolderIcon` for project scope (20px, `--color-accent`) |
| Title | `scope_name` (16px, 600 weight) |
| Scope label | `$t('tenancy.invite.scopeOrg')` or `$t('tenancy.invite.scopeProject')` (12px, `--color-muted`) |
| Role | `$t('tenancy.invite.invitedAs')` + `SBadge` with role |
| Expiry | `$t('tenancy.invite.expires')` + formatted date; `--color-warning` if within 48 hours, `--color-danger` if within 24 hours |
| Actions | Reject (secondary, sm) + Accept (primary, sm), right-aligned |

Cards are stacked vertically with 12px gap. Max-width 640px.

### 8.4 Empty State

`SEmptyState` with `InboxArrowDownIcon`, text `$t('tenancy.invite.empty')` ("No pending invitations"), no action button.

### 8.5 Role-Based Visibility

All authenticated users can view their own invites. No role-based hiding. The API returns only invites addressed to the current user's email.

### 8.6 Components Used

`SPageHeader`, `SBreadcrumb`, `SCard`, `SButton`, `SBadge`, `SAlert`, `SConfirmDialog`, `SEmptyState`, `SSkeleton`, `BuildingOffice2Icon`, `FolderIcon`, `InboxArrowDownIcon`

### 8.7 Responsive Behavior

| Breakpoint | Adaptation |
|------------|------------|
| >= 768px | Cards max-width 640px, centered |
| < 768px | Cards full width |
| < 480px | Accept/Reject buttons full width, stacked vertically; scope icon hidden |

---

## 9. InviteAcceptView

**File**: `src/slices/tenancy/views/InviteAcceptView.vue`
**Route**: `/invites/accept` (`tenancy.inviteAccept`)
**Guards**: `requiresAuth`, `requiresVerifiedEmail`

### 9.1 Wireframe — Accepting

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│                    Accept Invitation                         │
│                                                              │
│                    ┌──────────────────┐                      │
│                    │                  │                      │
│                    │  SLoadingSpinner │                      │
│                    │  "Accepting..."  │                      │
│                    │                  │                      │
│                    └──────────────────┘                      │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 9.2 Wireframe — Success

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│                    Accept Invitation                         │
│                                                              │
│                    ┌──────────────────┐                      │
│                    │                  │                      │
│                    │  CheckCircleIcon │                      │
│                    │  (48px, green)   │                      │
│                    │                  │                      │
│                    │  Invitation      │                      │
│                    │  accepted!       │                      │
│                    │                  │                      │
│                    │  [Go to Inbox]   │                      │
│                    │                  │                      │
│                    └──────────────────┘                      │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 9.3 Wireframe — Failure

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│                    Accept Invitation                         │
│                                                              │
│                    ┌──────────────────┐                      │
│                    │                  │                      │
│                    │  XCircleIcon     │                      │
│                    │  (48px, red)     │                      │
│                    │                  │                      │
│                    │  Invitation      │                      │
│                    │  could not be    │                      │
│                    │  accepted.       │                      │
│                    │                  │                      │
│                    │  [error detail]  │                      │
│                    │                  │                      │
│                    │  [Go to Inbox]   │                      │
│                    │                  │                      │
│                    └──────────────────┘                      │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 9.4 Behavior

**Three-state machine**: `'accepting' | 'success' | 'failure'`

**Token extraction** (SEC-8):
1. On mount, extract token from URL fragment: `new URLSearchParams(window.location.hash.slice(1)).get('token')`
2. The token is placed in the fragment (not query string) so it never appears in server logs, Referer headers, or browser history sync
3. If no token found: immediately transition to `failure` with message `$t('tenancy.invite.noToken')`

**Accept flow**:
1. State starts at `accepting`
2. Calls `invitesApi.acceptByToken(token)`
3. On success: state -> `success`
4. On `InviteExpired` (410): state -> `failure`, message `$t('tenancy.invite.expired')`
5. On `InviteNotFound` (404): state -> `failure`, message `$t('tenancy.invite.notFound')`
6. On other error: state -> `failure`, message `$t('tenancy.invite.acceptError')`

**Navigation**: "Go to Inbox" button (`SButton` variant `primary`, `as: router-link`, `to: tenancy.inbox`) always visible in success and failure states.

### 9.5 Layout

This view uses a centered layout within the AppShell content area (not AuthLayout). The content card is max-width 400px, centered horizontally and vertically within the content area, using `SCard` variant `elevated` with padding `lg`.

### 9.6 Components Used

`SCard`, `SButton`, `SLoadingSpinner`, `CheckCircleIcon`, `XCircleIcon`

### 9.7 Responsive Behavior

| Breakpoint | Adaptation |
|------------|------------|
| >= 768px | Card centered, max-width 400px |
| < 768px | Card full width with 16px horizontal margin |

---

## 10. MemberListPanel (Upgrade)

**File**: `src/slices/tenancy/components/MemberListPanel.vue`

The existing `MemberListPanel` currently uses raw HTML elements. It will be retired in favor of the inline `STable` + `SCard` pattern described in sections 3 and 7. The invite form and member table are rendered directly in each members view rather than delegated to a shared panel.

**Rationale for retirement**: The panel's abstraction creates prop/emit churn and limits view-specific customization (e.g., OC badge in org view vs. inherited badge in project view). The views share the same layout structure but differ in role logic, badge rendering, and action conditions. These differences are cleaner as direct template code than as conditional panel props.

**Migration path**:
1. Rewrite `OrgMembersView.vue` with inline `STable` + invite `SCard`
2. Rewrite `ProjectMembersView.vue` with inline `STable` + invite `SCard`
3. Delete `MemberListPanel.vue`
4. Remove from barrel export in `components/index.ts`

---

## 11. Shared Patterns

### 11.1 Role Badge Mapping

Used consistently across OrgListView, OrgMembersView, ProjectListView, ProjectMembersView.

| Role | Badge variant | Label (i18n key) |
|------|---------------|-------------------|
| Original Creator | `info` | `tenancy.role.originalCreator` |
| Owner | `neutral` | `tenancy.role.owner` |
| Member | `neutral` | `tenancy.role.member` |
| Inherited (project) | `neutral` | `tenancy.member.inherited` |

### 11.2 Breadcrumb Patterns

| View | Breadcrumb trail |
|------|-----------------|
| OrgListView | `Home` |
| OrgDetailView | `Home > Organizations > {orgName}` |
| OrgMembersView | `Home > Organizations > {orgName} > Members` |
| OrgTransferView | `Home > Organizations > {orgName} > Transfer` |
| ProjectListView | `Home` |
| ProjectDetailView | `Home > Projects > {projectName}` |
| ProjectMembersView | `Home > Projects > {projectName} > Members` |
| InboxInvitesView | `Home` |
| InviteAcceptView | (no breadcrumb — centered card layout) |

"Home" links to `/orgs`. Entity name segments link to their detail routes.

### 11.3 Confirmation Dialog Summary

| Action | Dialog variant | Prompt mode | Prompt validation |
|--------|---------------|-------------|-------------------|
| Create org | none (modal form) | — | — |
| Delete org | `error` | yes | Type org name |
| Restore org | `info` | no | — |
| Remove org member | `error` | no | — |
| Change org member role | `info` | no | — |
| Initiate OC transfer | `warning` | no | — |
| Cancel OC transfer | `error` | no | — |
| Accept OC transfer | `warning` | no | — |
| Create project | none (modal form) | — | — |
| Delete project | `error` | yes | Type project name |
| Restore project | `info` | no | — |
| Remove project member | `error` | no | — |
| Change project member role | `info` | no | — |
| Reject invite | `warning` | no | — |

### 11.4 Error Handling

All views follow this error hierarchy:

1. **Field-level errors**: rendered via `SFormField` `error` prop. Used for validation (name taken, invalid email, target not found).
2. **Inline errors**: `SAlert` variant `danger` within the view content area. Used for load failures with retry button.
3. **Toast errors**: `useToast` for transient operational errors (network failures, unexpected 500s). Auto-dismiss after 5 seconds.
4. **RFC 7807 problem mapping**: API errors include a `type` URI. The following types receive special handling:

| Problem type | Handling |
|-------------|----------|
| `/tenancy/name-taken` | Field error on name input |
| `/tenancy/version-mismatch` | Toast warning + query refetch |
| `/tenancy/original-creator-conflict` | Toast error (cannot modify OC) |
| `/tenancy/transfer-conflict` | Toast error (transfer already pending/resolved) |
| `/tenancy/invite-duplicate` | Field error on email input |
| `/tenancy/invite-expired` | Toast warning + remove from list |
| `/auth/email-unverified` | Show verification alert |

### 11.5 Loading States

| Context | Loading pattern |
|---------|----------------|
| Table views (org list, project list, member list) | `STable` with `loading` prop (5 skeleton rows) |
| Detail views (org detail, project detail) | `SLoadingSpinner` centered with label |
| Card lists (invites inbox) | 3x `SSkeleton` variant `rect`, height 120px, stacked |
| Button actions (invite, accept, save) | `SButton` with `loading` prop (spinner + disabled) |
| Token acceptance (InviteAcceptView) | `SLoadingSpinner` with "Accepting..." text |

### 11.6 Keyboard Interaction

| Context | Key | Action |
|---------|-----|--------|
| Create/rename forms | `Enter` | Submit form |
| Create/rename forms | `Escape` | Cancel (close modal / exit inline edit) |
| Table rows | `Enter` | Navigate to detail |
| Confirm dialogs | `Enter` | Confirm (when confirm button focused) |
| Confirm dialogs | `Escape` | Cancel |
| Modals | `Escape` | Close |
| Dropdowns | `ArrowDown/Up` | Navigate items |
| Dropdowns | `Enter` | Select item |
| Dropdowns | `Escape` | Close |

---

## 12. i18n Key Structure

All tenancy strings live under the `tenancy` namespace in `src/slices/tenancy/locales/{lang}.json`.

```
tenancy.
  org.
    createTitle           "Create Organization"
    nameHelp              "1-200 characters"
    nameTaken             "This name is already taken"
    created               "Organization created"
    empty                 "No organizations yet"
    loadError             "Failed to load organizations"
    notFound              "Organization not found"
    deleteTitle           "Delete Organization"
    deleteBody            "This will soft-delete the organization ..."
    deleteConfirm         "Delete organization"
    deleted               "Organization deleted"
    restoreTitle          "Restore Organization"
    restoreBody           "This will restore the organization ..."
    restoreConfirm        "Restore organization"
    deletedBanner         "This organization was deleted on {date} ..."
  project.
    createTitle           "Create Project"
    ownerPersonal         "Personal"
    ownerOrg              "Organization"
    personal              "Personal"
    tabAll                "All"
    tabPersonal           "Personal"
    emptyAll              "No projects yet"
    emptyPersonal         "No personal projects yet"
    emptyOrg              "No projects in {org} yet"
    nameTaken             "This name is already taken"
    created               "Project created"
    notFound              "Project not found"
    deleteTitle           "Delete Project"
    deleteBody            "This will soft-delete the project ..."
    deleteConfirm         "Delete project"
    deleted               "Project deleted"
    restoreTitle          "Restore Project"
    restoreBody           "This will restore the project ..."
    restoreConfirm        "Restore project"
  role.
    originalCreator       "Original Creator"
    owner                 "Owner"
    member                "Member"
  member.
    sendInvite            "Send invite"
    invited               "Invitation sent to {email}"
    alreadyInvited        "This email has already been invited"
    rateLimited           "Too many invitations. Please wait."
    changeRoleTitle       "Change Role"
    removeTitle           "Remove Member"
    removeBody            "This will remove the user from ..."
    removeConfirm         "Remove member"
    cannotRemoveOC        "The Original Creator cannot be removed"
    you                   "(you)"
    empty                 "No members"
    inherited             "Inherited"
    inheritedTooltip      "This user has access as an org Owner ..."
  transfer.
    initiateTitle         "Initiate Transfer"
    initiateBody          "This will transfer Original Creator ..."
    initiateConfirm       "Initiate transfer"
    cancelTitle           "Cancel Transfer"
    cancelConfirm         "Cancel transfer"
    acceptTitle           "Accept Transfer"
    acceptBody            "You will become the Original Creator ..."
    alreadyPending        "A transfer is already pending"
    targetNotOwner        "Target must be an existing Owner"
  invite.
    accepted              "Invitation to {name} accepted"
    expired               "This invitation has expired"
    notFound              "Invitation not found"
    acceptError           "Could not accept the invitation"
    noToken               "No invitation token found"
    rejectTitle           "Reject Invitation"
    rejectBody            "Reject the invitation to {name}?"
    rejectConfirm         "Reject"
    empty                 "No pending invitations"
    scopeOrg              "Organization"
    scopeProject          "Project"
    invitedAs             "Invited as"
    expires               "Expires"
  common.
    loading               "Loading..."
    versionConflict       "Someone else edited this. Refreshing..."
```

---

## 13. Files Summary

### Views to rewrite

| File | Section | Key changes |
|------|---------|-------------|
| `src/slices/tenancy/views/OrgListView.vue` | 1 | Replace `<ul>` with `STable`, replace raw `<input>` with `SModal`+`SFormField`+`SInput`, add `SPageHeader` with breadcrumbs, add `SEmptyState`, add role badges |
| `src/slices/tenancy/views/OrgDetailView.vue` | 2 | Add breadcrumbs, quotas card with threshold coloring, danger zone card, soft-delete/restore UI, copy-to-clipboard on ID |
| `src/slices/tenancy/views/OrgMembersView.vue` | 3 | Replace `MemberListPanel` with inline `STable`+`SCard`, add `SAvatar`, `SBadge`, `SDropdown` row actions, OC protection |
| `src/slices/tenancy/views/OrgTransferView.vue` | 4 | Add info alert, pending transfer card with state badges, confirmation dialogs, role-based button visibility |
| `src/slices/tenancy/views/ProjectListView.vue` | 5 | Add `STabs` for scope filtering, replace `<ul>` with `STable`, create modal with owner type select, org select |
| `src/slices/tenancy/views/ProjectDetailView.vue` | 6 | Same pattern as OrgDetailView minus quotas and transfer, add owner link |
| `src/slices/tenancy/views/ProjectMembersView.vue` | 7 | Same pattern as OrgMembersView, add inherited member badge + tooltip |
| `src/slices/tenancy/views/InboxInvitesView.vue` | 8 | Replace `<ul>` with `SCard` list, add scope icons, role badges, expiry coloring, verification warning |
| `src/slices/tenancy/views/InviteAcceptView.vue` | 9 | Replace raw elements with `SCard`+`SLoadingSpinner`+result icons |

### Components to delete

| File | Reason |
|------|--------|
| `src/slices/tenancy/components/MemberListPanel.vue` | Retired; logic inlined into OrgMembersView and ProjectMembersView (section 10) |

### Shared components required (from 01-design-system.md)

All of the following must be implemented before tenancy views can be rewritten:

| Component | Used in views |
|-----------|--------------|
| `SButton` | All views |
| `SInput` | 1, 2, 3, 4, 5, 6, 7 |
| `SSelect` | 3, 5, 7 |
| `SFormField` | 1, 3, 4, 5, 7 |
| `SCard` | 2, 3, 4, 6, 7, 8, 9 |
| `STable` | 1, 3, 5, 7 |
| `SModal` | 1, 5 |
| `SBadge` | 1, 2, 3, 4, 5, 6, 7, 8 |
| `SAvatar` | 3, 7 |
| `SAlert` | 1, 2, 3, 4, 5, 6, 8 |
| `SEmptyState` | 1, 3, 5, 8 |
| `STabs` | 5 |
| `SPageHeader` | 1, 2, 3, 4, 5, 6, 7, 8 |
| `SBreadcrumb` | 2, 3, 4, 6, 7, 8 |
| `SPagination` | (via STable internally) |
| `SDropdown` | 3, 7 |
| `SSkeleton` | 8 (via STable loading for others) |
| `SConfirmDialog` | 2, 3, 4, 6, 7, 8 |
| `SLoadingSpinner` | 2, 4, 6, 9 |
| `STooltip` | 2, 6, 7 |

### Locales to update

| File | Changes |
|------|---------|
| `src/slices/tenancy/locales/en.json` | Add all keys listed in section 12 |
| `src/slices/tenancy/locales/zh-TW.json` | Add corresponding zh-TW translations |

### No new routes

All 9 routes already exist in `src/slices/tenancy/routes.ts`. No route changes required.
