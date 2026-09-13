<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import STable from '@shared/ui/STable.vue'
import SBadge from '@shared/ui/SBadge.vue'
import SEmptyState from '@shared/ui/SEmptyState.vue'
import type { Column } from '@shared/ui/STable.vue'
import type { WatchlistEntry } from '../types'

defineProps<{
  entries: WatchlistEntry[]
}>()

const { t } = useI18n()

const columns = computed<Column[]>(() => [
  { key: 'subject_code', label: t('dashboard.subjectCode') },
  { key: 'submission_count', label: t('dashboard.submissionCount'), align: 'right' },
  { key: 'last_submission_at', label: t('dashboard.lastSubmission') },
  { key: 'needs_attention', label: t('dashboard.needsAttention') },
])
</script>

<template>
  <SEmptyState
    v-if="!entries.length"
    :text="t('dashboard.watchlistEmpty')"
  />
  <STable
    v-else
    :columns="columns"
    :data="entries"
    row-key="subject_code"
  >
    <template #cell-subject_code="{ value }">
      <code class="font-mono text-[0.8125rem]">{{ value }}</code>
    </template>
    <template #cell-last_submission_at="{ value }">
      {{ value ? new Date(value as string).toLocaleString() : '-' }}
    </template>
    <template #cell-needs_attention="{ value }">
      <SBadge
        v-if="value"
        variant="warning"
        size="sm"
        dot
      >
        {{ t('dashboard.needsAttention') }}
      </SBadge>
    </template>
  </STable>
</template>
