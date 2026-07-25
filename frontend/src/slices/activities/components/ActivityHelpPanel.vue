<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import { SDrawer } from '@shared/ui'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const { t } = useI18n()

// One entry per helpPanel.<key> namespace in the locale files — order here is
// the order rendered, matching the numbering baked into each title string.
const SECTIONS = [
  'types',
  'schema',
  'validators',
  'activation',
  'plugins',
  'visibility',
  'retention',
] as const
</script>

<template>
  <SDrawer
    :open="open"
    :title="t('activities.helpPanel.title')"
    side="right"
    size="lg"
    @close="emit('close')"
  >
    <div class="flex flex-col gap-6">
      <p class="text-sm text-[var(--color-muted)]">
        {{ t('activities.helpPanel.intro') }}
      </p>

      <section
        v-for="section in SECTIONS"
        :key="section"
        class="flex flex-col gap-1"
      >
        <h3 class="text-sm font-semibold text-[var(--color-fg)]">
          {{ t(`activities.helpPanel.${section}.title`) }}
        </h3>
        <p class="text-sm text-[var(--color-muted)]">
          {{ t(`activities.helpPanel.${section}.body`) }}
        </p>
      </section>
    </div>
  </SDrawer>
</template>
