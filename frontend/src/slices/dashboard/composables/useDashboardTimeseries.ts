import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { DashboardService } from '@shared/api-client'
import type { TimeWindow, BucketSize } from '@shared/api-client'
import { dashboardKeys } from '../queries'

export function useDashboardTimeseries(
  workspaceId: () => string,
  window: () => TimeWindow,
  bucket: () => BucketSize,
) {
  return useQuery({
    queryKey: computed(() => dashboardKeys.timeseries(workspaceId(), window(), bucket())),
    queryFn: () =>
      DashboardService.dashboardTimeseriesApiV1WorkspacesWorkspaceIdDashboardTimeseriesGet({
        workspaceId: workspaceId(),
        window: window(),
        bucket: bucket(),
      }),
    enabled: computed(() => !!workspaceId()),
  })
}
