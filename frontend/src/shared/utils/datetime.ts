type DateInput = string | number | Date

/** Browser-locale date (no time). Centralizes the `new Date(x).toLocaleDateString()`
 *  pattern so table date columns format consistently. */
export function formatDate(value: DateInput): string {
  return new Date(value).toLocaleDateString()
}

/** Browser-locale date + time. */
export function formatDateTime(value: DateInput): string {
  return new Date(value).toLocaleString()
}
