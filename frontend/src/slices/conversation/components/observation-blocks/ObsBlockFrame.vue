<template>
  <section class="obs-block">
    <h4
      v-if="title"
      class="obs-block__title"
    >
      {{ title }}
    </h4>

    <slot />

    <!-- R28.18: the denominator is submissions, never a participant population,
         and never a rate. Rendered here rather than per block so no computed
         kind can ship without it. -->
    <p
      v-if="counted !== undefined"
      class="obs-block__counted"
    >
      {{ t('conversation.observers.blocks.submissionsCounted', { n: counted }) }}
    </p>

    <p
      v-if="caveat"
      class="obs-block__caveat"
    >
      {{ caveat }}
    </p>
    <!-- R28.19: the basis sentence comes from the platform's own catalogue and
         is not suppressible by anything the agent supplied. An unknown basis
         renders nothing rather than echoing the raw value back at the reader. -->
    <p
      v-if="basisText"
      class="obs-block__basis"
    >
      {{ basisText }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ObservationBasis } from '../../types'

const props = defineProps<{
  title?: string | undefined
  caveat?: string | undefined
  basis?: ObservationBasis | undefined
  counted?: number | undefined
}>()

const { t } = useI18n()

// The catalogue this build ships. Checked against the value rather than against
// key existence: a missing key is a build defect and must not silently cost a
// block its label, while a basis from a newer server is data and renders nothing
// rather than echoing a raw enum at the reader.
const KNOWN_BASIS: readonly string[] = ['server_facts', 'recent_window', 'transcript']

const basisText = computed(() =>
  props.basis && KNOWN_BASIS.includes(props.basis)
    ? t(`conversation.observers.basis.${props.basis}`)
    : '',
)
</script>

<style scoped>
.obs-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-1-5);
}

.obs-block__title {
  margin: 0;
  font-size: var(--font-size-code);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
}

.obs-block__counted {
  margin: 0;
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}

.obs-block__caveat {
  margin: 0;
  font-size: var(--font-size-xs);
  color: var(--color-fg);
}

.obs-block__basis {
  margin: 0;
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  font-style: italic;
}
</style>
