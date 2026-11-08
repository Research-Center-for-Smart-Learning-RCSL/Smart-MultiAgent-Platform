<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import STable from '@shared/ui/STable.vue'
import SBadge from '@shared/ui/SBadge.vue'
import SRelativeTime from '@shared/ui/SRelativeTime.vue'
import STooltip from '@shared/ui/STooltip.vue'
import type { Column } from '@shared/ui/STable.vue'
import type { RoomSummary, WatchlistEntry } from '../types'

const props = defineProps<{
  entries: WatchlistEntry[]
  rooms: Pick<RoomSummary, 'room_id' | 'room_name'>[]
  idleMinutes: number
}>()

const { t } = useI18n()

const CREATIVITY_DIMENSIONS = ['fluency', 'flexibility', 'originality', 'convergence'] as const
type CreativityDimension = (typeof CREATIVITY_DIMENSIONS)[number]

// The server fills creativity only for a project owner or admin, and only for a
// participant of a scored type, so the columns appear exactly when there is
// something the caller may see.
const showCreativity = computed(() => props.entries.some((entry) => entry.creativity))

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('dashboard.participant'), cellType: 'user' },
  { key: 'room_name', label: t('dashboard.room') },
  { key: 'valid_count', label: t('dashboard.validCount'), align: 'right', cellType: 'number' },
  { key: 'last_valid_submission_at', label: t('dashboard.lastValidSubmission') },
  ...(showCreativity.value
    ? CREATIVITY_DIMENSIONS.map((d) => ({
        key: d,
        label: t(`dashboard.creativity.${d}`),
        align: 'right' as const,
      }))
    : []),
  { key: 'reason', label: t('dashboard.reasonColumn') },
])

const rows = computed(() =>
  props.entries.map((entry) => {
    const room = props.rooms.find((candidate) => candidate.room_id === entry.room_id)
    return {
      ...entry,
      row_key: `${entry.room_id}:${entry.subject_code}`,
      name: entry.display_name,
      room_name: room?.room_name ?? entry.room_id,
    }
  }),
)

function creativityValue(entry: WatchlistEntry, dimension: CreativityDimension): string | null {
  const value = entry.creativity?.[dimension]
  return typeof value === 'number' ? value.toFixed(1) : null
}

// Why a participant has no mean: still being scored, or the latest attempt was
// skipped or failed. The reason codes are a closed set from the backend.
function creativityMissing(entry: WatchlistEntry): string {
  const c = entry.creativity
  if (!c || !c.last_status) return t('dashboard.creativity.none')
  if (c.last_status === 'pending') return t('dashboard.creativity.pending')
  if (c.last_status === 'scored') return t('dashboard.creativity.none')
  return c.last_reason
    ? t(`dashboard.creativity.reason.${c.last_reason}`)
    : t(`dashboard.creativity.${c.last_status}`)
}

const reasonLabel = (reason: WatchlistEntry['reason']) =>
  reason
    ? t(`dashboard.reason.${reason}`, reason === 'idle' ? { minutes: props.idleMinutes } : {})
    : ''
</script>

<template>
  <STable
    :columns="columns"
    :data="rows"
    row-key="row_key"
  >
    <template #cell-name="{ row }">
      <span v-if="row.name">{{ row.name }}</span>
      <code
        v-else
        class="font-mono text-[0.8125rem]"
      >{{ row.subject_code }}</code>
    </template>
    <template #cell-room_name="{ value }">
      {{ value }}
    </template>
    <template #cell-valid_count="{ value }">
      <span class="tabular-nums">{{ value }}</span>
    </template>
    <template #cell-last_valid_submission_at="{ value }">
      <SRelativeTime
        v-if="value"
        :value="value as string"
      />
      <span v-else>{{ t('dashboard.never') }}</span>
    </template>
    <template
      v-for="dimension in CREATIVITY_DIMENSIONS"
      :key="dimension"
      #[`cell-${dimension}`]="{ row }"
    >
      <span
        v-if="creativityValue(row as WatchlistEntry, dimension) !== null"
        class="tabular-nums"
        :data-testid="`creativity-${dimension}`"
      >{{ creativityValue(row as WatchlistEntry, dimension) }}</span>
      <STooltip
        v-else
        :content="creativityMissing(row as WatchlistEntry)"
      >
        <span
          class="text-[var(--color-muted)]"
          :data-testid="`creativity-${dimension}-missing`"
        >
          <span aria-hidden="true">{{ t('dashboard.unavailableShort') }}</span>
          <!-- The tooltip is hover-only, so the reason is also given as text. -->
          <span class="visually-hidden">{{ creativityMissing(row as WatchlistEntry) }}</span>
        </span>
      </STooltip>
    </template>
    <template #cell-reason="{ value }">
      <SBadge
        v-if="value"
        variant="warning"
        size="sm"
      >
        {{ reasonLabel(value as WatchlistEntry['reason']) }}
      </SBadge>
    </template>
  </STable>
</template>
