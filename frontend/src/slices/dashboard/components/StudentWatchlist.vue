<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import STable from '@shared/ui/STable.vue'
import SBadge from '@shared/ui/SBadge.vue'
import SRelativeTime from '@shared/ui/SRelativeTime.vue'
import type { Column } from '@shared/ui/STable.vue'
import type { RoomSummary, WatchlistEntry } from '../types'

const props = defineProps<{
  entries: WatchlistEntry[]
  rooms: Pick<RoomSummary, 'room_id' | 'room_name'>[]
  idleMinutes: number
}>()

const { t } = useI18n()

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('dashboard.participant'), cellType: 'user' },
  { key: 'room_name', label: t('dashboard.room') },
  { key: 'valid_count', label: t('dashboard.validCount'), align: 'right', cellType: 'number' },
  { key: 'last_valid_submission_at', label: t('dashboard.lastValidSubmission') },
  { key: 'reason', label: t('dashboard.reason') },
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
