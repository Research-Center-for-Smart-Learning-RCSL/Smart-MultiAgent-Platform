import { computed, ref, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { TimeWindow, BucketSize } from '@shared/api-client'
import { useProjectRole } from '@slices/tenancy'
import { useDashboardSummary } from './useDashboardSummary'
import { useDashboardTimeseries } from './useDashboardTimeseries'
import { useDashboardWatchlist } from './useDashboardWatchlist'
import { useDashboardSocket } from './useDashboardSocket'
import type { RoomSummary } from '../types'
import { dashboardBucketForWindow } from '../chartData'

export function useDashboardViewModel(workspaceId: Ref<string>) {
  const { t, locale } = useI18n()
  const timeWindow = ref<TimeWindow>('1h')
  const bucketSize = computed<BucketSize>(() => dashboardBucketForWindow(timeWindow.value))
  const wid = () => workspaceId.value
  const {
    data: summaryData,
    isLoading: summaryLoading,
    error: summaryError,
    dataUpdatedAt: summaryUpdatedAt,
    refetch: refetchSummary,
  } = useDashboardSummary(wid)
  const {
    data: timeseriesData,
    isLoading: timeseriesLoading,
    error: timeseriesError,
    refetch: refetchTimeseries,
  } = useDashboardTimeseries(
    wid,
    () => timeWindow.value,
    () => bucketSize.value,
  )
  const {
    data: watchlistData,
    isLoading: watchlistLoading,
    error: watchlistError,
    refetch: refetchWatchlist,
  } = useDashboardWatchlist(wid)

  const rooms = computed(() => summaryData.value?.rooms ?? [])
  const statusOrder: Record<RoomSummary['status'], number> = {
    red: 0,
    yellow: 1,
    green: 2,
    inactive: 3,
  }
  const sortedRooms = computed(() =>
    [...rooms.value].sort(
      (left, right) =>
        statusOrder[left.status] - statusOrder[right.status] ||
        left.room_name.localeCompare(right.room_name, locale.value),
    ),
  )
  const {
    connected,
    projectId,
    workspace,
    workspaceError,
    refetchWorkspace,
  } = useDashboardSocket(wid)
  const buckets = computed(() => timeseriesData.value?.buckets ?? [])
  const watchlistEntries = computed(() => watchlistData.value?.entries ?? [])
  const thresholds = computed(() => summaryData.value?.thresholds)
  const roomFilter = ref('')
  const roomFilterOptions = computed(() => [
    { value: '', label: t('dashboard.allRooms') },
    ...rooms.value.map((room) => ({ value: room.room_id, label: room.room_name })),
  ])
  const filteredEntries = computed(() =>
    roomFilter.value
      ? watchlistEntries.value.filter((entry) => entry.room_id === roomFilter.value)
      : watchlistEntries.value,
  )
  const attentionEntries = computed(() => filteredEntries.value.filter((entry) => entry.needs_attention))

  const attentionCount = computed(
    () => watchlistEntries.value.filter((entry) => entry.needs_attention).length,
  )
  const { isAuthorized: canExport, decided: roleDecided } = useProjectRole(
    () => projectId.value || undefined,
  )

  return {
    timeWindow,
    bucketSize,
    summaryLoading,
    summaryError,
    summaryUpdatedAt,
    refetchSummary,
    timeseriesLoading,
    timeseriesError,
    refetchTimeseries,
    watchlistLoading,
    watchlistError,
    refetchWatchlist,
    rooms,
    sortedRooms,
    buckets,
    watchlistEntries,
    roomFilter,
    roomFilterOptions,
    filteredEntries,
    attentionEntries,
    thresholds,
    attentionCount,
    connected,
    workspace,
    workspaceError,
    refetchWorkspace,
    canExport,
    roleDecided,
  }
}
