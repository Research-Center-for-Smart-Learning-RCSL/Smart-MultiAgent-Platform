<template>
  <ObsBlockFrame
    :title="block.title"
    :caveat="block.caveat"
    :basis="block.basis"
    :counted="block.submissions_counted"
  >
    <AttemptTable
      :columns="columns"
      :data="block.rows"
      row-key="subject_code"
      responsive-mode="hide-columns"
    >
      <template #cell-latest_outcome="{ row }">
        <span>{{ outcomeLabel(row.latest_outcome) }}</span>
        <span
          v-if="row.latest_error_class"
          class="obs-attempts__error"
        >{{ row.latest_error_class }}</span>
      </template>
    </AttemptTable>

    <!-- No silent caps: a table that stops at its limit with no sign of it reads
         as a complete record of the room, which is the one thing this is not. -->
    <p
      v-if="block.truncated"
      class="obs-attempts__truncated"
    >
      {{ t('conversation.observers.blocks.truncated', { n: block.rows.length }) }}
    </p>
  </ObsBlockFrame>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { typedTable } from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import ObsBlockFrame from './ObsBlockFrame.vue'
import type { ObservationAttemptRow, ObservationAttemptTableBlock } from '../../types'

defineProps<{ block: ObservationAttemptTableBlock }>()

const { t } = useI18n()
const AttemptTable = typedTable<ObservationAttemptRow>()

// The server's own four words for a submission's outcome.
const KNOWN_OUTCOMES: readonly string[] = ['valid', 'invalid', 'pending', 'error']

const columns = computed<Column[]>(() => [
  { key: 'subject_code', label: t('conversation.observers.blocks.participant') },
  {
    key: 'attempts',
    label: t('conversation.observers.blocks.attempts'),
    align: 'right',
    cellType: 'number',
  },
  {
    key: 'submissions',
    label: t('conversation.observers.blocks.submissions'),
    align: 'right',
    cellType: 'number',
    hideBelow: 'sm',
  },
  { key: 'latest_outcome', label: t('conversation.observers.blocks.latest') },
])

// An outcome this build does not know renders verbatim rather than as a
// missing-key echo, so a row written by a newer release stays readable.
function outcomeLabel(outcome: string): string {
  return KNOWN_OUTCOMES.includes(outcome)
    ? t(`conversation.observers.blocks.outcome.${outcome}`)
    : outcome
}
</script>

<style scoped>
.obs-attempts__error {
  display: block;
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}

.obs-attempts__truncated {
  margin: 0;
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}
</style>
