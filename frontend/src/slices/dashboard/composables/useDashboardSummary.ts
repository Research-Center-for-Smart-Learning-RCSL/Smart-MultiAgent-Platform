import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { DashboardService } from '@shared/api-client'
import { dashboardKeys } from '../queries'

export function useDashboardSummary(workspaceId: () => string) {
  return useQuery({
    queryKey: computed(() => dashboardKeys.summary(workspaceId())),
    queryFn: () =>
      DashboardService.dashboardSummaryApiV1WorkspacesWorkspaceIdDashboardSummaryGet({
        workspaceId: workspaceId(),
      }),
    enabled: computed(() => !!workspaceId()),
  })
}
