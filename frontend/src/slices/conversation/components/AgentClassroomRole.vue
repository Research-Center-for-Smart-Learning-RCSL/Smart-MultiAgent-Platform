<script setup lang="ts">
// The classroom role of one bound agent ([R34.01]): teacher, peer (student
// agent), analyst, or none. Creator-only: the parent renders it only for the room
// creator, and the server omits `classroom_role` from everyone else's listing.
//
// Only the roles the binding can hold are offered: teacher and peer need a
// participant binding, analyst needs an observer one. The server and a CHECK
// refuse the rest, so offering them would only trade a choice for an error.

import { computed, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { SSelect } from '@shared/ui'
import type { ClassroomRole } from '../api'
import type { BoundAgent } from '../composables/useChatroomBindings'

const props = defineProps<{
  agent: BoundAgent
  busy: boolean
  roomHasTeacher: boolean
}>()

const emit = defineEmits<{
  save: [role: ClassroomRole]
}>()

const { t } = useI18n()
const selectId = useId()

const options = computed(() => {
  const fitting: ClassroomRole[] =
    props.agent.role === 'observer' ? ['none', 'analyst'] : ['none', 'teacher', 'peer']
  return fitting.map((value) => ({ value, label: t(`conversation.classroomRole.${value}`) }))
})
</script>

<template>
  <div class="classroom-role">
    <label
      :for="selectId"
      class="classroom-role__label"
    >
      {{ t('conversation.classroomRole.label') }}
    </label>
    <SSelect
      :id="selectId"
      :model-value="agent.classroom_role ?? 'none'"
      :options="options"
      size="sm"
      :disabled="busy"
      class="classroom-role__select"
      @update:model-value="(v) => emit('save', v as ClassroomRole)"
    />
    <p class="access-row__desc">
      {{ t('conversation.classroomRole.help') }}
    </p>
    <!-- Q-9: a peer's replies wait for a teacher agent's review, so without one they all expire unposted. -->
    <p
      v-if="agent.classroom_role === 'peer' && !roomHasTeacher"
      class="classroom-role__warning"
      role="status"
      data-testid="classroom-role-no-teacher"
    >
      {{ t('conversation.classroomRole.peerWithoutTeacher') }}
    </p>
  </div>
</template>

<style scoped>
.classroom-role {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
}
.classroom-role__label {
  font-size: var(--font-size-code);
}
.classroom-role__select {
  width: auto;
  min-width: 10rem;
}
.classroom-role .access-row__desc,
.classroom-role__warning {
  flex-basis: 100%;
}
.classroom-role__warning {
  font-size: var(--font-size-code);
  color: var(--color-warning-on);
}
</style>
