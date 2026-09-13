import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { DashboardService } from '@shared/api-client'
import type { TimeWindow, BucketSize } from '@shared/api-client'
import { dashboardKeys } from '../queries'

export function useDashboardTimeseries(
  projectId: () => string,
  window: () => TimeWindow,
  bucket: () => BucketSize,
) {
  return useQuery({
    queryKey: computed(() => dashboardKeys.timeseries(projectId(), window(), bucket())),
    queryFn: () =>
      DashboardService.dashboardTimeseriesApiV1ProjectsProjectIdDashboardTimeseriesGet({
        projectId: projectId(),
        window: window(),
        bucket: bucket(),
      }),
    enabled: computed(() => !!projectId()),
  })
}
