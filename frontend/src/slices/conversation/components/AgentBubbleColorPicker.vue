<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { ref, watch } from 'vue'
import { SRadio } from '@shared/ui'
import {
  AGENT_BUBBLE_COLORS,
  agentColorVars,
  isAgentBubbleColor,
  type AgentBubbleColor,
} from '../utils/agentBubbleColors'

const props = defineProps<{
  modelValue: AgentBubbleColor | null
  name: string
  disabled?: boolean
}>()
const emit = defineEmits<{ save: [color: AgentBubbleColor | null] }>()
const { t } = useI18n()
const selection = ref(props.modelValue)

// Native radios change before the request settles; keep that change reactive
// so a failed save can restore the persisted selection in the DOM.
watch(() => [props.modelValue, props.disabled] as const, () => {
  if (!props.disabled) selection.value = props.modelValue
})

function save(value: string): void {
  if (value !== '' && !isAgentBubbleColor(value)) return
  selection.value = value || null
  emit('save', selection.value)
}
</script>

<template>
  <fieldset
    class="color-picker"
    :disabled="disabled"
  >
    <legend>{{ t('conversation.appearance.label') }}</legend>
    <p class="color-picker__help">
      {{ t('conversation.appearance.help') }}
    </p>
    <div class="color-picker__options">
      <SRadio
        :name="name"
        :model-value="selection ?? ''"
        value=""
        :disabled="disabled"
        @update:model-value="save"
      >
        {{ t('conversation.appearance.none') }}
      </SRadio>
      <SRadio
        v-for="color in AGENT_BUBBLE_COLORS"
        :key="color"
        :name="name"
        :model-value="selection ?? ''"
        :value="color"
        :disabled="disabled"
        @update:model-value="save"
      >
        <span
          class="color-picker__swatch"
          :style="agentColorVars(color)"
          aria-hidden="true"
        />
        {{ t(`conversation.appearance.colors.${color}`) }}
      </SRadio>
    </div>
  </fieldset>
</template>

<style scoped>
.color-picker {
  border: 0;
  padding: 0;
  margin: var(--space-3) 0;
  font-size: var(--font-size-sm);
}

.color-picker__help {
  color: var(--color-muted);
  margin: var(--space-1) 0;
}

.color-picker__options {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
}

.color-picker__swatch {
  display: inline-block;
  width: var(--space-3);
  height: var(--space-3);
  border-radius: var(--radius-full);
  margin-right: var(--space-1);
  /* Overridden per swatch by agentColorVars(). */
  --agent-color: var(--color-border-strong);
  background: var(--agent-color);
}
</style>
