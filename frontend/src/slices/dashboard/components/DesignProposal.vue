<script setup lang="ts">
// A finished run's proposal next to the prompts it was drafted from ([R34.08]).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SBadge, SButton } from '@shared/ui'
import type { DesignRunOut } from '@shared/api-client'

const props = defineProps<{
  run: DesignRunOut
  teacherName: string
  peerName: string
  applying: boolean
}>()
const emit = defineEmits<{
  apply: []
  startOver: []
}>()

const { t } = useI18n()

const PRE_CLASS =
  'mt-1 max-h-80 overflow-auto whitespace-pre-wrap rounded border border-[var(--color-border-subtle)] p-2 text-xs text-[var(--color-fg)] [overflow-wrap:anywhere]'

const sections = computed(() => [
  { role: 'teacher' as const, name: props.teacherName, target: props.run.teacher },
  { role: 'peer' as const, name: props.peerName, target: props.run.peer },
])
</script>

<template>
  <div data-testid="design-proposal">
    <p
      v-if="run.notes"
      class="mb-3 whitespace-pre-wrap text-sm text-[var(--color-fg)] [overflow-wrap:anywhere]"
    >
      <span class="text-[var(--color-muted)]">{{ t('dashboard.design.notes') }}</span>
      {{ run.notes }}
    </p>
    <div
      v-for="{ role, name, target } in sections"
      :key="role"
      class="mb-4"
    >
      <h3 class="mb-2 text-sm font-semibold text-[var(--color-fg)]">
        {{ t(`dashboard.design.target.${role}`, { name }) }}
      </h3>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div
          v-for="side in (['current', 'proposed'] as const)"
          :key="side"
        >
          <SBadge
            size="sm"
            :variant="side === 'current' ? 'neutral' : 'success'"
          >
            {{ t(`dashboard.design.${side}`) }}
          </SBadge>
          <pre
            :class="PRE_CLASS"
            :data-testid="`design-${side}-${role}`"
          >{{ side === 'current' ? target.current_prompt || t('dashboard.design.emptyPrompt') : target.proposed_prompt }}</pre>
        </div>
      </div>
    </div>
    <div class="flex flex-wrap justify-end gap-2">
      <SButton
        type="button"
        variant="secondary"
        data-testid="design-start-over"
        @click="emit('startOver')"
      >
        {{ t('dashboard.design.startOver') }}
      </SButton>
      <SButton
        type="button"
        variant="primary"
        :loading="applying"
        data-testid="design-apply"
        @click="emit('apply')"
      >
        {{ t('dashboard.design.apply') }}
      </SButton>
    </div>
  </div>
</template>
