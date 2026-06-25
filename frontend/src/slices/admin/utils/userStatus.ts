/** Badge variant accepted by SBadge (mirrors its internal union). */
export type BadgeVariant = 'info' | 'success' | 'warning' | 'danger' | 'neutral'

/** Maps a user account status to its badge variant and i18n label key.
 *  Shared by AdminUsersView (list) and AdminUserDetailView (detail) so the
 *  colour/label contract stays in one place. `banned` maps to `danger`, which
 *  the generic SStatusBadge does not cover — hence the explicit map. */
const STATUS_VARIANTS: Record<string, BadgeVariant> = {
  active: 'success',
  pending: 'neutral',
  banned: 'danger',
  deleted: 'neutral',
}

const STATUS_LABELS: Record<string, string> = {
  active: 'admin.users.statusActive',
  pending: 'admin.users.statusPending',
  banned: 'admin.users.statusBanned',
  deleted: 'admin.users.statusDeleted',
}

export function userStatusVariant(status: string): BadgeVariant {
  return STATUS_VARIANTS[status] ?? 'neutral'
}

/** Returns the i18n key for a status, or the raw status if unmapped. */
export function userStatusLabelKey(status: string): string {
  return STATUS_LABELS[status] ?? status
}
