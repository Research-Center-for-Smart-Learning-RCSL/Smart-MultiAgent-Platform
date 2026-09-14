import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { DashboardService } from '@shared/api-client'
import { dashboardKeys } from '../queries'

export function useDashboardWatchlist(workspaceId: () => string) {
  return useQuery({
    queryKey: computed(() => dashboardKeys.watchlist(workspaceId())),
    queryFn: () =>
      DashboardService.dashboardWatchlistApiV1WorkspacesWorkspaceIdDashboardWatchlistGet({
        workspaceId: workspaceId(),
      }),
    enabled: computed(() => !!workspaceId()),
  })
}
