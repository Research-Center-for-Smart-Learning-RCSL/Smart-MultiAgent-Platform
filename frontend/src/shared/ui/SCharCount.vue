<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  CHAR_COUNT_WARN_RATIO,
  CHAR_COUNT_DANGER_RATIO,
} from '@shared/constants/inputLimits'

const props = withDefaults(
  defineProps<{
    current: number
    max: number
    /** Hide the counter until the value is within `warnRatio` of the max. */
    hideUntilNear?: boolean
  }>(),
  { hideUntilNear: false },
)

const { t, n } = useI18n()

const ratio = computed(() => (props.max > 0 ? props.current / props.max : 0))

const visible = computed(() => !props.hideUntilNear || ratio.value >= CHAR_COUNT_WARN_RATIO)

const tone = computed(() => {
  if (ratio.value >= CHAR_COUNT_DANGER_RATIO) return 's-char-count--danger'
  if (ratio.value >= CHAR_COUNT_WARN_RATIO) return 's-char-count--warn'
  return ''
})
</script>

<template>
  <p
    v-if="visible"
    class="s-char-count"
    :class="tone"
    role="status"
    aria-live="polite"
    :aria-label="t('app.charCount.label', { current, max })"
  >
    {{ `${n(current)} / ${n(max)}` }}
  </p>
</template>

<style scoped>
.s-char-count {
  margin-top: 4px;
  font-size: 0.75rem;
  line-height: 1;
  text-align: right;
  color: var(--color-muted);
}

.s-char-count--warn {
  color: var(--color-warning);
}

.s-char-count--danger {
  color: var(--color-danger);
}
</style>
