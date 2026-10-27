<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ClockIcon } from '@heroicons/vue/24/outline'

const props = defineProps<{
  deadlineAt: string
}>()

const { t } = useI18n()

const TICK_MS = 1000

const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null

const deadlineMs = computed(() => Date.parse(props.deadlineAt))
const remainingMs = computed(() => deadlineMs.value - now.value)
const expired = computed(() => remainingMs.value <= 0)
// Rounded up so the label never claims less time than is left: 30 seconds reads
// as "1 min left", and "time is up" appears only once the deadline has passed.
const minutesLeft = computed(() => Math.ceil(remainingMs.value / 60_000))

function stop(): void {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

function start(): void {
  stop()
  now.value = Date.now()
  if (!Number.isFinite(deadlineMs.value) || expired.value) return
  timer = setInterval(() => {
    now.value = Date.now()
    // Nothing on screen changes after expiry, so the tick has no reason to run.
    if (expired.value) stop()
  }, TICK_MS)
}

watch(() => props.deadlineAt, start, { immediate: true })
onBeforeUnmount(stop)
</script>

<template>
  <p
    v-if="Number.isFinite(deadlineMs)"
    class="activity-countdown"
    :class="{ 'activity-countdown--expired': expired }"
    role="timer"
    data-testid="activity-countdown"
  >
    <ClockIcon
      class="activity-countdown__icon"
      aria-hidden="true"
    />
    <span>
      {{ expired ? t('activities.countdown.timeUp') : t('activities.countdown.minutesLeft', { n: minutesLeft }) }}
    </span>
  </p>
</template>

<style scoped>
.activity-countdown {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  margin: 0;
  font-size: var(--font-size-sm);
  color: var(--color-muted);
}
.activity-countdown--expired {
  color: var(--color-warning);
}
.activity-countdown__icon {
  width: 1rem;
  height: 1rem;
}
</style>
