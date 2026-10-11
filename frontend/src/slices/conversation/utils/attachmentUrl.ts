// Module-scoped memo so an attachment shown in several bubbles is fetched once,
// not per render. Links are short-lived tickets, so an entry is dropped a
// minute before its link expires, and a failed fetch is never remembered:
// caching it would pin the attachment to its fallback for the whole session.
import { getAttachment } from '../api'

const EXPIRY_MARGIN_MS = 60_000

interface Entry {
  url: Promise<string | null>
  freshUntil: number
}

const cache = new Map<string, Entry>()

export function fetchAttachmentUrl(id: string): Promise<string | null> {
  const hit = cache.get(id)
  if (hit && Date.now() < hit.freshUntil) return hit.url
  const entry: Entry = { url: Promise.resolve(null), freshUntil: Number.POSITIVE_INFINITY }
  entry.url = getAttachment(id).then(
    (d) => {
      entry.freshUntil = Date.now() + d.expires_in * 1000 - EXPIRY_MARGIN_MS
      return d.url
    },
    () => {
      if (cache.get(id) === entry) cache.delete(id)
      return null
    },
  )
  cache.set(id, entry)
  return entry.url
}

export function invalidateAttachmentUrl(id: string): void {
  cache.delete(id)
}

export function resetAttachmentUrlCache(): void {
  cache.clear()
}
