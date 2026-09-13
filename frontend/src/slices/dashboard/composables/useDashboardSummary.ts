import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { DashboardService } from '@shared/api-client'
import { dashboardKeys } from '../queries'

export function useDashboardSummary(projectId: () => string) {
  return useQuery({
    queryKey: computed(() => dashboardKeys.summary(projectId())),
    queryFn: () =>
      DashboardService.dashboardSummaryApiV1ProjectsProjectIdDashboardSummaryGet({
        projectId: projectId(),
      }),
    enabled: computed(() => !!projectId()),
  })
}
