export function safeRedirect(raw: string, fallback = '/orgs'): string {
  if (!raw) return fallback
  try {
    const url = new URL(raw, window.location.origin)
    if (url.origin !== window.location.origin) return fallback
    return url.pathname + url.search + url.hash
  } catch {
    return fallback
  }
}
