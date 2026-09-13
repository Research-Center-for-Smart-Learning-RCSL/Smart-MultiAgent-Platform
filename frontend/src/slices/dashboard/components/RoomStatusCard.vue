<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import SCard from '@shared/ui/SCard.vue'
import SBadge from '@shared/ui/SBadge.vue'
import type { RoomSummary } from '../types'

const props = defineProps<{
  room: RoomSummary
}>()

const { t } = useI18n()

const badgeVariant = computed(() => {
  switch (props.room.status) {
    case 'green': return 'success' as const
    case 'yellow': return 'warning' as const
    case 'red': return 'danger' as const
    default: return 'neutral' as const
  }
})

const statusLabel = computed(() => {
  switch (props.room.status) {
    case 'green': return t('dashboard.statusGreen')
    case 'yellow': return t('dashboard.statusYellow')
    case 'red': return t('dashboard.statusRed')
    default: return ''
  }
})

const lastActivityText = computed(() => {
  if (!props.room.last_submission_at) return t('dashboard.neverActive')
  return new Date(props.room.last_submission_at).toLocaleString()
})
</script>

<template>
  <SCard class="p-4">
    <div class="flex items-center justify-between mb-3">
      <h3 class="text-sm font-medium text-[var(--color-text)]">
        {{ room.room_name }}
      </h3>
      <SBadge
        :variant="badgeVariant"
        size="sm"
        dot
      >
        {{ statusLabel }}
      </SBadge>
    </div>
    <dl class="grid grid-cols-2 gap-2 text-xs text-[var(--color-text-secondary)]">
      <div>
        <dt>{{ t('dashboard.totalCount') }}</dt>
        <dd class="text-base font-semibold text-[var(--color-text)]">
          {{ room.total_submissions }}
        </dd>
      </div>
      <div>
        <dt>{{ t('dashboard.validCount') }}</dt>
        <dd class="text-base font-semibold text-[var(--color-text)]">
          {{ room.valid_count }}
        </dd>
      </div>
      <div class="col-span-2">
        <dt>{{ t('dashboard.lastActivity') }}</dt>
        <dd class="text-sm text-[var(--color-text)]">
          {{ lastActivityText }}
        </dd>
      </div>
    </dl>
  </SCard>
</template>
