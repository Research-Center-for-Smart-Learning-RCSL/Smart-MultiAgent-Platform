// Analyst alerts for the rooms this caller created ([R34.02]), plus the
// dashboard's half of RQ2's "teacher saw it" signal.
//
// A view is reported only while the page is visible: a dashboard left open in a
// background tab is not a teacher looking. The server keeps the first view only
// and ignores anyone but the room's creator, so a repeat or an admin's view is
// harmless; the reporter only avoids resending one per poll. Best-effort: a lost
// report costs one data point, not the dashboard.

import { computed, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { DashboardService, ObservationsService } from '@shared/api-client'
import { usePageVisible } from '@shared/composables/usePageVisible'
import { onceBatchReporter } from '@shared/composables/onceBatchReporter'
import { dashboardKeys } from '../queries'
import type { DashboardAlert } from '../types'

export function useDashboardAlerts(workspaceId: () => string) {
  const query = useQuery({
    queryKey: computed(() => dashboardKeys.alerts(workspaceId())),
    queryFn: () =>
      DashboardService.dashboardAlertsApiV1WorkspacesWorkspaceIdDashboardAlertsGet({
        workspaceId: workspaceId(),
      }),
    enabled: computed(() => !!workspaceId()),
    refetchInterval: 30_000,
  })

  const alerts = computed<DashboardAlert[]>(() => query.data.value?.alerts ?? [])
  const pageVisible = usePageVisible()
  // One reporter per room: the route is per room, and ids are unique across rooms.
  const reporters = new Map<string, (ids: string[]) => void>()
  function reporterFor(roomId: string): (ids: string[]) => void {
    let report = reporters.get(roomId)
    if (!report) {
      report = onceBatchReporter(
        (ids) =>
          ObservationsService.recordAlertViewsApiChatroomsChatroomIdObservationsViewedPost({
            chatroomId: roomId,
            requestBody: { observation_ids: ids, via: 'dashboard' },
          }),
        50,
      )
      reporters.set(roomId, report)
    }
    return report
  }

  watch(
    [alerts, pageVisible],
    () => {
      if (!pageVisible.value) return
      const byRoom = new Map<string, string[]>()
      for (const a of alerts.value) {
        if (!a.first_viewed_at) byRoom.set(a.room_id, [...(byRoom.get(a.room_id) ?? []), a.observation_id])
      }
      for (const [roomId, ids] of byRoom) reporterFor(roomId)(ids)
    },
    { immediate: true },
  )

  return {
    alerts,
    isLoading: computed(() => query.isLoading.value),
    error: computed(() => query.error.value),
    refetch: () => query.refetch(),
  }
}
