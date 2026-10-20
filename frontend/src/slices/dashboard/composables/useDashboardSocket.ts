import { ref, watch, computed, onMounted, onBeforeUnmount } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { wsManager, type ChannelEvent } from '@shared/transport/ws-manager'
import { WorkspacesService } from '@shared/api-client'
import { dashboardKeys } from '../queries'

const PROJECT_ACCESS_REFUSED_CLOSE_CODE = 4403
const PROJECT_NOT_FOUND_CLOSE_CODE = 4404

export function useDashboardSocket(
  workspaceId: () => string,
) {
  const queryClient = useQueryClient()
  const connected = ref(false)
  let currentPath: string | null = null
  let unsubEvent: (() => void) | null = null
  let unsubStatus: (() => void) | null = null
  let unsubCloseCode: (() => void) | null = null
  let unsubDegraded: (() => void) | null = null
  let debounceTimer: ReturnType<typeof setTimeout> | null = null

  const {
    data: workspace,
    error: workspaceError,
    refetch: refetchWorkspace,
  } = useQuery({
    queryKey: computed(() => ['workspace', 'detail', workspaceId()] as const),
    queryFn: () =>
      WorkspacesService.readWorkspaceApiWorkspacesWorkspaceIdGet({
        workspaceId: workspaceId(),
      }),
    enabled: computed(() => !!workspaceId()),
    staleTime: Infinity,
  })

  const projectId = computed(() => workspace.value?.project_id ?? '')

  function invalidateDebounced() {
    if (debounceTimer) clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => {
      void queryClient.invalidateQueries({ queryKey: dashboardKeys.all })
    }, 500)
  }

  function teardown() {
    unsubEvent?.()
    unsubStatus?.()
    unsubCloseCode?.()
    unsubDegraded?.()
    unsubEvent = null
    unsubStatus = null
    unsubCloseCode = null
    unsubDegraded = null
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
      if (ev.type === 'dashboard.invalidated') invalidateDebounced()
    })

    unsubStatus = channel.onStatus((isConnected: boolean) => {
      connected.value = isConnected
    })
    unsubCloseCode = channel.onCloseCode((code) => {
      if (code === PROJECT_ACCESS_REFUSED_CLOSE_CODE || code === PROJECT_NOT_FOUND_CLOSE_CODE) {
        channel.disconnect()
      }
    })
    let wasDegraded = false
    unsubDegraded = channel.onDegraded((isDegraded) => {
      if (wasDegraded && !isDegraded) {
        void queryClient.invalidateQueries({ queryKey: dashboardKeys.all })
      }
      wasDegraded = isDegraded
    })

    channel.connect()
  }

  watch(projectId, (pid) => setup(pid), { immediate: true })

  onMounted(() => {
    const pid = projectId.value
    if (pid && currentPath) {
      wsManager.channel(currentPath).connect()
    }
  })

  onBeforeUnmount(teardown)

  return { connected, projectId, workspace, workspaceError, refetchWorkspace }
}
