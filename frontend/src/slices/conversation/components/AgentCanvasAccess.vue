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
  'save-read': [granted: boolean]
  'save-write': [granted: boolean]
}>()

const { t } = useI18n()

const readGranted = computed(() => props.agent.may_read_canvas === true)
const writeGranted = computed(() => props.agent.may_write_canvas === true)

function onToggleRead(next: boolean): void {
  emit('save-read', next)
}

function onToggleWrite(next: boolean): void {
  emit('save-write', next)
}
</script>

<template>
  <div class="canvas-access">
    <SToggle
      :model-value="readGranted"
      size="sm"
      :disabled="busy"
      @update:model-value="onToggleRead"
    >
      {{ t('canvas.agentGrant') }}
    </SToggle>
    <p class="access-row__desc">
      {{ t('canvas.agentGrantDescription') }}
    </p>

    <SToggle
      :model-value="writeGranted"
      size="sm"
      :disabled="busy"
      @update:model-value="onToggleWrite"
    >
      {{ t('canvas.mayWriteCanvas') }}
    </SToggle>
    <p class="access-row__desc">
      {{ t('canvas.mayWriteCanvasDescription') }}
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
