// Analyst alerts for the rooms this caller created ([R34.02]), plus the
// dashboard's half of RQ2's "teacher saw it" signal.
//
// A view is reported only while the page is visible: a dashboard left open in a
// background tab is not a teacher looking. The server keeps the first view only
// and ignores anyone but the room's creator, so a repeat or an admin's view is
// harmless; `reported` only avoids resending one per poll. Best-effort: a lost
// report costs one data point, not the dashboard.

import { computed, onScopeDispose, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { DashboardService, ObservationsService } from '@shared/api-client'
import { dashboardKeys } from '../queries'
import type { DashboardAlert } from '../types'

const VIEW_BATCH = 50

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

  const pageVisible = ref(typeof document === 'undefined' || document.visibilityState === 'visible')
  function onVisibilityChange(): void {
    pageVisible.value = document.visibilityState === 'visible'
  }
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibilityChange)
    onScopeDispose(() => document.removeEventListener('visibilitychange', onVisibilityChange))
  }

  const reported = new Set<string>()

  watch(
    [alerts, pageVisible],
    () => {
      if (!pageVisible.value) return
      const byRoom = new Map<string, string[]>()
      for (const a of alerts.value) {
        if (a.first_viewed_at || reported.has(a.observation_id)) continue
        byRoom.set(a.room_id, [...(byRoom.get(a.room_id) ?? []), a.observation_id])
      }
      for (const [roomId, ids] of byRoom) {
        for (let i = 0; i < ids.length; i += VIEW_BATCH) {
          const batch = ids.slice(i, i + VIEW_BATCH)
          for (const id of batch) reported.add(id)
          ObservationsService.recordAlertViewsApiChatroomsChatroomIdObservationsViewedPost({
            chatroomId: roomId,
            requestBody: { observation_ids: batch, via: 'dashboard' },
          }).catch(() => {
            for (const id of batch) reported.delete(id)
          })
        }
      }
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
