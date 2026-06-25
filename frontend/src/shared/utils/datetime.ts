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

// Largest-to-smallest unit ladder for relative formatting. Each `amount` is how
// many of the current unit make up one of the next (60s = 1min, 60min = 1hr…).
const RELATIVE_DIVISIONS: Array<{ amount: number; unit: Intl.RelativeTimeFormatUnit }> = [
  { amount: 60, unit: 'second' },
  { amount: 60, unit: 'minute' },
  { amount: 24, unit: 'hour' },
  { amount: 7, unit: 'day' },
  { amount: 4.34524, unit: 'week' },
  { amount: 12, unit: 'month' },
  { amount: Number.POSITIVE_INFINITY, unit: 'year' },
]

/**
 * Locale-aware relative time ("2 hours ago", "yesterday", "in 3 days") via
 * `Intl.RelativeTimeFormat` (§11.3). Pair with `formatDateTime` for an absolute
 * tooltip. Past timestamps read in the past tense, future ones in the future;
 * an unparseable value falls back to its absolute date.
 */
export function formatRelativeTime(
  value: DateInput,
  now: DateInput = Date.now(),
  locale?: string,
): string {
  const then = new Date(value).getTime()
  if (Number.isNaN(then)) return formatDateTime(value)

  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  // Signed seconds: negative = in the past, positive = in the future.
  let duration = (then - new Date(now).getTime()) / 1000

  for (const division of RELATIVE_DIVISIONS) {
    if (Math.abs(duration) < division.amount) {
      return rtf.format(Math.round(duration), division.unit)
    }
    duration /= division.amount
  }
  // Unreachable (the final division is Infinity), but satisfies the type.
  return rtf.format(Math.round(duration), 'year')
}
