import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { wsManager, type ChannelEvent } from '@shared/transport/ws-manager'
import { dashboardKeys } from '../queries'

export function useProjectSocket(projectId: () => string) {
  const queryClient = useQueryClient()
  const connected = ref(false)
  let currentPath: string | null = null
  let unsubEvent: (() => void) | null = null
  let unsubStatus: (() => void) | null = null
  let debounceTimer: ReturnType<typeof setTimeout> | null = null

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
        invalidateDebounced()
      }
    })

    unsubStatus = channel.onStatus((isConnected: boolean) => {
      connected.value = isConnected
    })

    channel.connect()
  }

  watch(projectId, (pid) => setup(pid), { immediate: true })

  onMounted(() => {
    const pid = projectId()
    if (pid && currentPath) {
      wsManager.channel(currentPath).connect()
    }
  })

  onBeforeUnmount(teardown)

  return { connected }
}
