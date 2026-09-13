import { watch, onUnmounted } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { wsManager, type ChannelEvent } from '@shared/transport/ws-manager'
import { dashboardKeys } from '../queries'

export function useProjectSocket(projectId: () => string) {
  const queryClient = useQueryClient()
  const unsubs: Array<() => void> = []

  function subscribe(pid: string) {
    cleanup()
    if (!pid) return

    const channel = wsManager.channel(`/project/${pid}`)

    unsubs.push(
      channel.subscribe('dashboard.submission.validated', (_event: ChannelEvent) => {
        queryClient.invalidateQueries({ queryKey: dashboardKeys.all })
      }),
    )

    unsubs.push(
      channel.subscribe('dashboard.activation.changed', (_event: ChannelEvent) => {
        queryClient.invalidateQueries({ queryKey: dashboardKeys.all })
      }),
    )
  }

  function cleanup() {
    unsubs.forEach((fn) => fn())
    unsubs.length = 0
  }

  watch(projectId, (id) => subscribe(id), { immediate: true })
  onUnmounted(cleanup)
}
