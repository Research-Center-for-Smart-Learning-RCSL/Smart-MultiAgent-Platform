// The collaboration log for the rooms this caller created ([R34.03]): analyst
// notices to the teacher agent and the teacher agent's answers.

import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { DashboardService } from '@shared/api-client'
import { dashboardKeys } from '../queries'
import type { CollaborationEntry } from '../types'

export function useDashboardCollaboration(workspaceId: () => string) {
  const query = useQuery({
    queryKey: computed(() => dashboardKeys.collaboration(workspaceId())),
    queryFn: () =>
      DashboardService.dashboardCollaborationApiV1WorkspacesWorkspaceIdDashboardCollaborationGet({
        workspaceId: workspaceId(),
      }),
    enabled: computed(() => !!workspaceId()),
    refetchInterval: 30_000,
  })

  return {
    entries: computed<CollaborationEntry[]>(() => query.data.value?.entries ?? []),
    isLoading: computed(() => query.isLoading.value),
    error: computed(() => query.error.value),
    refetch: () => query.refetch(),
  }
}
