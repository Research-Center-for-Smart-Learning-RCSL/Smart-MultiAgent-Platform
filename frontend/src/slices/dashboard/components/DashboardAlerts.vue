<script setup lang="ts">
import { computed, toRef } from 'vue'
import { useI18n } from 'vue-i18n'
import SAlert from '@shared/ui/SAlert.vue'
import type { RoomSummary } from '../types'

const props = defineProps<{
  rooms: RoomSummary[]
}>()

const { t } = useI18n()

const roomsRef = toRef(props, 'rooms')
const stalledRooms = computed(() =>
  roomsRef.value.filter((r) => r.status === 'red'),
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
    class="text-sm text-[var(--color-muted)]"
  >
    {{ t('dashboard.noAlerts') }}
  </p>
</template>
