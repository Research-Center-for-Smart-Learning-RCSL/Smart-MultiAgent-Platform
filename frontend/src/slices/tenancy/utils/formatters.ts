export function formatDateTime(d: string | undefined | null): string {
  if (!d) return ''
  return d.replace('T', ' ').slice(0, 16)
}

export function formatDate(d: string): string {
  return d.slice(0, 10)
}

export function formatRelative(d: string): string {
  const diff = Date.now() - new Date(d).getTime()
  const days = Math.floor(diff / 86400000)
  if (days < 1) return '<1d'
  if (days < 30) return `${days}d`
  const months = Math.floor(days / 30)
  return `${months}mo`
}
