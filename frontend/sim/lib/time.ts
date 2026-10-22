export function formatOffset(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

/** Seconds to wait for a 429, from a Retry-After header in seconds or as an HTTP date. */
export function retryAfterSec(header: string | null | undefined, fallback: number, now = Date.now()): number {
  let sec = Number(header)
  if (header && Number.isNaN(sec)) {
    const at = Date.parse(header)
    sec = Number.isNaN(at) ? fallback : Math.ceil((at - now) / 1000)
  }
  if (!header || Number.isNaN(sec)) sec = fallback
  return Math.min(Math.max(sec, 1), 60)
}
