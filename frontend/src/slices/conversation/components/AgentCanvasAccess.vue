<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SToggle } from '@shared/ui'
import type { BoundAgent } from '../composables/useChatroomBindings'

const props = defineProps<{
  agent: BoundAgent
  busy: boolean
}>()

const emit = defineEmits<{
  save: [granted: boolean]
}>()

const { t } = useI18n()

const granted = computed(() => props.agent.may_read_canvas === true)

function onToggle(next: boolean): void {
  emit('save', next)
}
</script>

<template>
  <div class="canvas-access">
    <SToggle
      :model-value="granted"
      size="sm"
      :disabled="busy"
      @update:model-value="onToggle"
    >
      {{ t('canvas.agentGrant') }}
    </SToggle>
    <p class="access-row__desc">
      {{ t('canvas.agentGrantDescription') }}
    </p>
  </div>
</template>

<style scoped>
.canvas-access {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  align-items: flex-start;
}
</style>
