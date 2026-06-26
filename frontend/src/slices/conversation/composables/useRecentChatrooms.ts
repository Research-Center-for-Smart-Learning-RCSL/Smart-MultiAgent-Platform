import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { convKeys } from '../queries'
import { listWorkspaces, listChatrooms } from '../api'
import type { Chatroom } from '../types'

/**
 * Project-wide recent chatrooms for "jump back in" rails (sidebar + home).
 *
 * Fans out across the project's workspaces, gathers every chatroom, and sorts
 * newest-created first. The full sorted list is cached under one project-scoped
 * key; callers pass a `limit` to slice locally, so the sidebar (10) and the
 * landing rail (4) share a single cache entry and a single network fan-out.
 *
 * Gated on a selected project — a freshly-registered user (or one who has not
 * picked a project) has none, so the query stays idle and `rooms` is empty.
 *
 * Note: `Chatroom` carries only `created_at` (no last-activity timestamp), so
 * "recent" here means most-recently-created, not most-recently-active.
 */
export function useRecentChatrooms(
  projectId: MaybeRefOrGetter<string | null>,
  options: { limit?: number; enabled?: MaybeRefOrGetter<boolean> } = {},
) {
  const pid = computed(() => toValue(projectId))

  const query = useQuery({
    queryKey: computed(() => convKeys.recentChatrooms(pid.value ?? '')),
    queryFn: async () => {
      const workspaces = await listWorkspaces(pid.value!)
      const settled = await Promise.allSettled(
        workspaces.map((ws) => listChatrooms(ws.id)),
      )
      return settled
        .filter((r): r is PromiseFulfilledResult<Chatroom[]> => r.status === 'fulfilled')
        .flatMap((r) => r.value)
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
    },
    enabled: computed(
      () => (options.enabled ? toValue(options.enabled) : true) && !!pid.value,
    ),
    staleTime: 60_000,
  })

  const rooms = computed<Chatroom[]>(() => {
    const data = query.data.value ?? []
    return options.limit ? data.slice(0, options.limit) : data
  })

  return { query, rooms, isLoading: query.isLoading }
}
