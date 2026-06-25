// Relative-time descriptor for notification timestamps. Returns a discriminated
// token rather than a formatted string so the component owns the i18n mapping
// (vue-i18n) — keeping this unit testable without a locale. Avoids pulling in
// date-fns just for this. The absolute-date fallback reuses the shared
// `formatDate` helper so date formatting stays consistent across the app.
import { formatDate } from '@shared/utils/datetime'

export type RelativeTime =
  | { unit: 'justNow' }
  | { unit: 'minutes'; n: number }
  | { unit: 'hours'; n: number }
  | { unit: 'yesterday' }
  | { unit: 'days'; n: number }
  | { unit: 'date'; value: string } // locale date string for >= 7 days / undated

export function relativeTime(iso: string, now: Date = new Date()): RelativeTime {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return { unit: 'date', value: iso }

  const seconds = Math.floor((now.getTime() - then) / 1000)
  // Future timestamps (clock skew) collapse to "just now".
  if (seconds < 60) return { unit: 'justNow' }

  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return { unit: 'minutes', n: minutes }

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return { unit: 'hours', n: hours }

  const days = Math.floor(hours / 24)
  if (days === 1) return { unit: 'yesterday' }
  if (days < 7) return { unit: 'days', n: days }

  return { unit: 'date', value: formatDate(iso) }
}
