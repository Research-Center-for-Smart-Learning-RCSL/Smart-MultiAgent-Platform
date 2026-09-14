import { ref, watch, computed, onMounted, onBeforeUnmount } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { wsManager, type ChannelEvent } from '@shared/transport/ws-manager'
import { useWorkspaceStore } from '@shared/stores/workspace'
import { dashboardKeys } from '../queries'
import { useDashboardSummary } from './useDashboardSummary'

export function useProjectSocket(workspaceId: () => string) {
  const queryClient = useQueryClient()
  const workspace = useWorkspaceStore()
  const connected = ref(false)
  let currentPath: string | null = null
  let unsubEvent: (() => void) | null = null
  let unsubStatus: (() => void) | null = null
  let debounceTimer: ReturnType<typeof setTimeout> | null = null

  const { data: summary } = useDashboardSummary(workspaceId)
  const roomIds = computed<Set<string>>(() => {
    const rooms = summary.value?.rooms ?? []
    return new Set(rooms.map((r) => String(r.room_id)))
  })

  function invalidateDebounced() {
    if (debounceTimer) clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => {
      queryClient.invalidateQueries({ queryKey: dashboardKeys.all })
    }, 500)
  }

  function teardown() {
    unsubEvent?.()
    unsubStatus?.()
    unsubEvent = null
    unsubStatus = null
    if (currentPath) {
      wsManager.close(currentPath)
      currentPath = null
    }
    if (debounceTimer) clearTimeout(debounceTimer)
    connected.value = false
  }

  function setup(pid: string) {
    teardown()
    if (!pid) return

    currentPath = `/project/${pid}`
    const channel = wsManager.channel(currentPath)

    unsubEvent = channel.subscribe('*', (ev: ChannelEvent) => {
      if (
        ev.type === 'dashboard.submission.validated' ||
        ev.type === 'dashboard.activation.changed'
      ) {
        const roomId = (ev.payload as Record<string, unknown>)?.room_id
        if (roomId && !roomIds.value.has(String(roomId))) return
        invalidateDebounced()
      }
    })

    unsubStatus = channel.onStatus((isConnected: boolean) => {
      connected.value = isConnected
    })

    channel.connect()
  }

  const projectId = computed(() => workspace.projectId ?? '')

  watch(projectId, (pid) => setup(pid), { immediate: true })

  onMounted(() => {
    const pid = projectId.value
    if (pid && currentPath) {
      wsManager.channel(currentPath).connect()
    }
  })

  onBeforeUnmount(teardown)

  return { connected }
}
