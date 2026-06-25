const STATUS_LABELS: Record<string, string> = {
  active: 'admin.users.statusActive',
  pending: 'admin.users.statusPending',
  banned: 'admin.users.statusBanned',
  deleted: 'admin.users.statusDeleted',
}

/** Returns the i18n key for a user account status, or the raw status if unmapped.
 *  The status -> badge colour mapping lives in SStatusBadge (active/banned/deleted). */
export function userStatusLabelKey(status: string): string {
  return STATUS_LABELS[status] ?? status
}
