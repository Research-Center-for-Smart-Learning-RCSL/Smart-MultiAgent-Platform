<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { SButton, SProgressBar, SAlert, SSkeleton, STooltip } from '@shared/ui'
import { useToast } from '@shared/composables'
import { projectKeysApi, type KeyUsage, type UsageWindow } from '../api/project-keys'
import type { Limits } from '../api/key-groups'
import { formatTokenCount } from '../lib/formatTokenCount'

const props = withDefaults(
  defineProps<{
    projectId: string
    keyId: string
    limits?: Limits | null
    compact?: boolean
  }>(),
  { limits: null, compact: false },
)

const { t } = useI18n()
const toast = useToast()

const WINDOWS: { value: UsageWindow; label: string }[] = [
  { value: '1h', label: '1h' },
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
]

const selectedWindow = ref<UsageWindow>('1h')
const usage = ref<KeyUsage | null>(null)
const loading = ref(false)
const fetchError = ref(false)

async function loadUsage() {
  loading.value = true
  fetchError.value = false
  try {
    const { data } = await projectKeysApi.usage(props.projectId, props.keyId, selectedWindow.value)
    usage.value = data
  } catch {
    fetchError.value = true
    toast.error(t('keys.usage.fetchFailed'))
  } finally {
    loading.value = false
  }
}

watch(selectedWindow, () => loadUsage())
void loadUsage()

const stats = computed(() => {
  if (!usage.value) return []
  return [
    { key: 'requests', label: t('keys.usage.requests'), value: usage.value.requests.toLocaleString(), raw: usage.value.requests, danger: false },
    { key: 'input', label: t('keys.usage.inputTokens'), value: formatTokenCount(usage.value.input_tokens), raw: usage.value.input_tokens, danger: false },
    { key: 'output', label: t('keys.usage.outputTokens'), value: formatTokenCount(usage.value.output_tokens), raw: usage.value.output_tokens, danger: false },
    { key: 'errors', label: t('keys.usage.errors'), value: usage.value.errors.toLocaleString(), raw: usage.value.errors, danger: usage.value.errors > 0 },
  ]
})

interface LimitBar {
  label: string
  current: number
  limit: number
  pct: number
  variant: 'info' | 'warning' | 'danger'
  currentFormatted: string
  limitFormatted: string
}

const limitBars = computed<LimitBar[]>(() => {
  if (!props.limits || selectedWindow.value !== '1h' || !usage.value) return []
  const bars: LimitBar[] = []
  const entries: { label: string; current: number; limit: number | null }[] = [
    { label: t('keys.usage.inputTokensPerH'), current: usage.value.input_tokens, limit: props.limits.max_input_tokens_per_hour },
    { label: t('keys.usage.outputTokensPerH'), current: usage.value.output_tokens, limit: props.limits.max_output_tokens_per_hour },
    { label: t('keys.usage.requestsPerH'), current: usage.value.requests, limit: props.limits.max_requests_per_hour },
  ]
  for (const e of entries) {
    if (e.limit == null || e.limit <= 0) continue
    const pct = Math.round((e.current / e.limit) * 100)
    bars.push({
      label: e.label,
      current: e.current,
      limit: e.limit,
      pct: Math.min(pct, 100),
      variant: pct >= 80 ? 'danger' : pct >= 60 ? 'warning' : 'info',
      currentFormatted: formatTokenCount(e.current),
      limitFormatted: formatTokenCount(e.limit),
    })
  }
  return bars
})

const thresholdAlerts = computed(() =>
  limitBars.value.filter((b) => b.pct >= 80),
)
</script>

<template>
  <div class="usage-dashboard">
    <!-- Time window selector -->
    <div class="flex items-center gap-4 mb-4">
      <span
        v-if="!compact"
        class="text-sm font-medium"
      >{{ t('keys.usage.timeWindow') }}</span>
      <div class="inline-flex rounded-[var(--radius-md)] border border-[var(--color-border)] overflow-hidden">
        <SButton
          v-for="(w, idx) in WINDOWS"
          :key="w.value"
          :variant="selectedWindow === w.value ? 'primary' : 'ghost'"
          size="sm"
          :class="[
            'rounded-none border-0',
            idx > 0 && 'border-l border-l-[var(--color-border)]',
          ]"
          @click="selectedWindow = w.value"
        >
          {{ w.label }}
        </SButton>
      </div>
    </div>

    <!-- Loading state -->
    <div
      v-if="loading"
      class="flex gap-4"
    >
      <SSkeleton
        v-for="i in 4"
        :key="i"
        variant="rect"
        width="140px"
        height="60px"
      />
    </div>

    <!-- Error state -->
    <SAlert
      v-else-if="fetchError"
      variant="danger"
    >
      {{ t('keys.usage.fetchFailed') }}
      <template #actions>
        <SButton
          variant="ghost"
          size="sm"
          @click="loadUsage"
        >
          {{ t('keys.usage.retry') }}
        </SButton>
      </template>
    </SAlert>

    <!-- Stats -->
    <template v-else-if="usage">
      <div :class="compact ? 'flex flex-wrap gap-8' : 'flex flex-wrap gap-4'">
        <div
          v-for="s in stats"
          :key="s.key"
          :class="compact
            ? 'flex flex-col'
            : 'flex flex-col min-w-[140px] flex-1 bg-[var(--color-surface)] rounded-[var(--radius-md)] p-4'"
        >
          <STooltip
            :content="s.raw.toLocaleString()"
            placement="top"
          >
            <span
              :class="[
                compact ? 'text-base font-semibold' : 'text-2xl font-semibold',
                s.danger ? 'text-[var(--color-danger)]' : 'text-[var(--color-fg)]',
              ]"
            >{{ s.value }}</span>
          </STooltip>
          <span class="text-xs text-[var(--color-muted)]">{{ s.label }}</span>
        </div>
      </div>

      <!-- Hourly limit progress bars -->
      <div
        v-if="limitBars.length > 0"
        class="mt-4 flex flex-col gap-3"
      >
        <div
          v-for="bar in limitBars"
          :key="bar.label"
          class="flex items-center gap-3"
        >
          <span class="text-xs text-[var(--color-muted)] w-32 shrink-0">{{ bar.label }}</span>
          <div class="flex-1">
            <SProgressBar
              :value="bar.pct"
              :variant="bar.variant"
              size="sm"
            />
          </div>
          <span class="text-xs text-[var(--color-muted)] w-28 text-right shrink-0">
            {{ bar.pct }}% &middot; {{ bar.currentFormatted }} / {{ bar.limitFormatted }}
          </span>
        </div>
      </div>

      <!-- Threshold alerts -->
      <SAlert
        v-for="alert in thresholdAlerts"
        :key="alert.label"
        variant="warning"
        dismissible
        class="mt-3"
      >
        {{ t('keys.usage.thresholdBody', { metric: alert.label, pct: alert.pct }) }}
      </SAlert>

      <!-- Retention notice -->
      <p
        v-if="!compact"
        class="text-xs text-[var(--color-muted)] mt-3"
      >
        {{ t('keys.usage.retention') }}
      </p>
    </template>
  </div>
</template>
