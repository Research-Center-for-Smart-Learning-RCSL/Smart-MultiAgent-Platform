<script setup lang="ts">
import { computed, toRef } from 'vue'
import { useI18n } from 'vue-i18n'
import SAlert from '@shared/ui/SAlert.vue'
import type { RoomSummary } from '../types'

const props = defineProps<{
  rooms: RoomSummary[]
}>()

const { t } = useI18n()

const ALERT_MINUTES = 10

const roomsRef = toRef(props, 'rooms')
const stalledRooms = computed(() =>
  roomsRef.value.filter((r) => {
    if (!r.last_submission_at) return false
    const elapsed = (Date.now() - new Date(r.last_submission_at).getTime()) / 60000
    return elapsed > ALERT_MINUTES
  }),
)
</script>

<template>
  <div
    v-if="stalledRooms.length"
    class="space-y-2"
  >
    <SAlert
      v-for="room in stalledRooms"
      :key="room.room_id"
      variant="warning"
    >
      {{ t('dashboard.alertStalled', { room: room.room_name }) }}
    </SAlert>
  </div>
  <p
    v-else
    class="text-sm text-[var(--color-text-secondary)]"
  >
    {{ t('dashboard.noAlerts') }}
  </p>
</template>
