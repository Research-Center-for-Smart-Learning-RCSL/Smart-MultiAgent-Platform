// Owns the notifications list: cursor-paginated fetch plus individual / bulk
// mark-read with optimistic cache patching. Extracted from NotificationsView so
// the mutation orchestration (page-loading loop, id batching, in-place cache
// patch) is testable in isolation and the view stays presentational.
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useInfiniteQuery, useQueryClient, type InfiniteData } from '@tanstack/vue-query'
import { useToast } from '@shared/composables'
import { notificationsApi, type Notification } from '../api'
import { notificationKeys } from '../queries'

const PAGE_SIZE = 50
// Backend MarkReadIn caps `ids` at 1000 per request — chunk to stay under it.
const MARK_BATCH = 1000
// Bound the "mark all" page-load loop so a pathological backlog can't spin.
const MAX_LOAD_PAGES = 40

export function useNotificationsList() {
  const qc = useQueryClient()
  const { t } = useI18n()
  const toast = useToast()

  const query = useInfiniteQuery({
    queryKey: notificationKeys.list(),
    queryFn: ({ pageParam }) => notificationsApi.list(pageParam as string | undefined, PAGE_SIZE),
    initialPageParam: undefined as string | undefined,
    // A full page implies there may be more — page back from the last item's id.
    getNextPageParam: (lastPage: Notification[]) =>
      lastPage.length === PAGE_SIZE ? lastPage[lastPage.length - 1]!.id : undefined,
  })

  const items = computed<Notification[]>(() => (query.data.value?.pages ?? []).flat())
  const hasUnread = computed(() => items.value.some((n) => !n.read_at))
  const isEmpty = computed(
    () => !query.isLoading.value && !query.isError.value && items.value.length === 0,
  )
  const marking = ref(false)

  // Patch read_at on the loaded pages in place instead of invalidating the whole
  // infinite query (which would refetch every loaded page); only the server-side
  // unread count needs a refetch for the bell badge.
  function patchRead(ids: string[]): void {
    const set = new Set(ids)
    const now = new Date().toISOString()
    qc.setQueryData<InfiniteData<Notification[]>>(notificationKeys.list(), (old) =>
      old
        ? {
            ...old,
            pages: old.pages.map((page) =>
              page.map((n) => (set.has(n.id) ? { ...n, read_at: now } : n)),
            ),
          }
        : old,
    )
    qc.invalidateQueries({ queryKey: notificationKeys.unreadCount() })
  }

  async function markOne(id: string): Promise<void> {
    const n = items.value.find((it) => it.id === id)
    if (!n || n.read_at) return
    try {
      await notificationsApi.markRead([id])
      patchRead([id])
    } catch {
      toast.error(t('notifications.markFailed'))
    }
  }

  // "Mark all" loads the remaining pages first so it genuinely clears the backlog
  // (and the bell badge), then marks in <=1000-id batches to respect the API cap.
  async function markAll(): Promise<void> {
    marking.value = true
    try {
      let guard = 0
      while (query.hasNextPage.value && guard < MAX_LOAD_PAGES) {
        await query.fetchNextPage()
        guard += 1
      }
      const ids = items.value.filter((n) => !n.read_at).map((n) => n.id)
      const marked: string[] = []
      try {
        for (let i = 0; i < ids.length; i += MARK_BATCH) {
          const batch = ids.slice(i, i + MARK_BATCH)
          await notificationsApi.markRead(batch)
          marked.push(...batch)
        }
      } finally {
        // Patch whatever the server already accepted — even if a later batch
        // threw — so the cache and bell badge never lag behind confirmed
        // server state on a partial failure.
        if (marked.length) patchRead(marked)
      }
    } catch {
      toast.error(t('notifications.markFailed'))
    } finally {
      marking.value = false
    }
  }

  return { query, items, isEmpty, hasUnread, marking, markOne, markAll }
}
