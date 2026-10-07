import type { Message } from '../types'

/**
 * Union two message arrays by id, preferring rows from `next` (they carry
 * fresh `version`/`edited_at`). The result is sorted ascending by
 * `created_at` then `id` for a stable tiebreak.
 *
 * Deletion handling: any cached message whose `created_at >= windowStart`
 * (the oldest row in `next`) that is absent from `next` was hard-deleted
 * during the fetch window — it is dropped. Messages older than `windowStart`
 * are kept: their deletion is not always delivered live (a disconnect can
 * drop the `message.deleted` frame, and the retention purge publishes none
 * at all), so a stale out-of-window row can persist until it is next paged
 * past as a `before` anchor, where the 422-on-a-dead-anchor fallback in
 * `useChatroomMessages.loadEarlier` degrades it to a refetch (V-2).
 *
 * `knownAtRequest` is the set of cached ids when `next` was requested. A
 * cached row outside it arrived while the request was in flight, so `next`
 * not containing it says nothing about whether it was deleted — it is kept.
 * Without the set every cached row is treated as known.
 */
export function mergeMessages(
  prev: Message[],
  next: Message[],
  knownAtRequest?: ReadonlySet<string>,
): Message[] {
  const nextById = new Map<string, Message>()
  for (const m of next) nextById.set(m.id, m)

  let windowStart: string | null = null
  for (const m of next) {
    if (windowStart === null || m.created_at < windowStart) {
      windowStart = m.created_at
    }
  }

  const merged = new Map<string, Message>()

  for (const m of prev) {
    if (
      windowStart !== null &&
      m.created_at >= windowStart &&
      !nextById.has(m.id) &&
      (knownAtRequest === undefined || knownAtRequest.has(m.id))
    ) {
      continue
    }
    merged.set(m.id, m)
  }

  for (const m of next) {
    merged.set(m.id, m)
  }

  return Array.from(merged.values()).sort((a, b) =>
    a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : a.id < b.id ? -1 : 1,
  )
}
