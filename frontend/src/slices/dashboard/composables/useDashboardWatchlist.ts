import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { DashboardService } from '@shared/api-client'
import { dashboardKeys } from '../queries'

export function useDashboardWatchlist(projectId: () => string) {
  return useQuery({
    queryKey: computed(() => dashboardKeys.watchlist(projectId())),
    queryFn: () =>
      DashboardService.dashboardWatchlistApiV1ProjectsProjectIdDashboardWatchlistGet({
        projectId: projectId(),
      }),
    enabled: computed(() => !!projectId()),
  })
}
