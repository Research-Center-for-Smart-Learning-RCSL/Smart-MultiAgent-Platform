// Small date/time formatters local to the conversation slice. Cross-slice
// imports of other slices' formatters are forbidden by the boundary gate, so
// these live here and are reused across the slice's views and message bubbles.

/** ISO timestamp → `YYYY-MM-DD`. Empty string for nullish input. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return ''
  return iso.slice(0, 10)
}

/** ISO timestamp → localized clock time (e.g. `10:23 AM`). */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

/** ISO timestamp → localized date + time (e.g. `Dec 15, 10:23 AM`). */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}
