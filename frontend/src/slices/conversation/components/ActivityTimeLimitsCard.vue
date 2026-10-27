<script setup lang="ts">
// Per-activity-type time limits for this room. Each type either follows its own
// default, runs with no limit, or takes a custom number of minutes. The limit is
// snapshotted when a round starts, so a change here never moves a running one.
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { ClockIcon } from '@heroicons/vue/24/outline'
import { SButton, SCard, SEmptyState, SInput, SQueryError, SSelect, SSkeleton } from '@shared/ui'
import { useToast } from '@shared/composables'
import { ApiError } from '@shared/errors'
import type { ActivityType } from '@slices/activities'
import { getActivityTimeLimits, putActivityTimeLimits, type ActivityTimeLimit } from '../api'
import { convKeys } from '../queries'

const props = defineProps<{
  chatroomId: string
  activityTypes: ActivityType[]
  activityTypesFailed: boolean
  activityTypesLoading: boolean
}>()

const { t } = useI18n()
const toast = useToast()
const qc = useQueryClient()
const key = computed(() => convKeys.activityTimeLimits(props.chatroomId))

const BOUNDS = { min: 1, max: 180 } as const

type Mode = 'default' | 'none' | 'custom'
interface Draft {
  mode: Mode
  minutes: number | ''
}

const query = useQuery({
  queryKey: key,
  queryFn: () => getActivityTimeLimits(props.chatroomId),
  retry: false,
})
const hidden = computed(() => {
  const err = query.error.value
  return err instanceof ApiError && (err.status === 403 || err.status === 404)
})

const drafts = ref<Record<string, Draft>>({})

function draftFrom(stored: ActivityTimeLimit | undefined, type: ActivityType): Draft {
  if (!stored) return { mode: 'default', minutes: type.default_time_limit_minutes ?? '' }
  if (stored.time_limit_minutes === null) return { mode: 'none', minutes: '' }
  return { mode: 'custom', minutes: stored.time_limit_minutes }
}

function seed(): void {
  const stored = new Map((query.data.value ?? []).map((row) => [row.activity_type_id, row]))
  drafts.value = Object.fromEntries(
    props.activityTypes.map((type) => [type.id, draftFrom(stored.get(type.id), type)]),
  )
}

// Keyed on values, not identities: the type listing and the stored set are both
// rebuilt on every refetch, which must not throw away an edit in progress.
watch(
  () => {
    const stored = (query.data.value ?? [])
      .map((row) => `${row.activity_type_id}=${row.time_limit_minutes}`)
      .sort()
      .join(',')
    const types = props.activityTypes.map((type) => type.id).join(',')
    return `${stored}|${types}`
  },
  seed,
  { immediate: true },
)

function setMode(typeId: string, mode: Mode): void {
  const prev = drafts.value[typeId] ?? { mode: 'default', minutes: '' }
  drafts.value = { ...drafts.value, [typeId]: { ...prev, mode } }
}

function setMinutes(typeId: string, raw: string | number): void {
  const prev = drafts.value[typeId] ?? { mode: 'custom', minutes: '' }
  drafts.value = { ...drafts.value, [typeId]: { ...prev, minutes: raw === '' ? '' : Number(raw) } }
}

function validMinutes(raw: number | ''): number | null {
  if (raw === '') return null
  return Number.isInteger(raw) && raw >= BOUNDS.min && raw <= BOUNDS.max ? raw : null
}

function rowInvalid(typeId: string): boolean {
  const d = drafts.value[typeId]
  return !!d && d.mode === 'custom' && validMinutes(d.minutes) === null
}

const anyInvalid = computed(() => props.activityTypes.some((type) => rowInvalid(type.id)))

// Only types this room can reach are sent: the route refuses an id the project
// cannot use, so a stored override for a since-removed type is dropped by the
// next save rather than making every save fail.
const payload = computed<ActivityTimeLimit[]>(() => {
  const rows: ActivityTimeLimit[] = []
  for (const type of props.activityTypes) {
    const d = drafts.value[type.id]
    if (!d || d.mode === 'default') continue
    if (d.mode === 'none') rows.push({ activity_type_id: type.id, time_limit_minutes: null })
    else rows.push({ activity_type_id: type.id, time_limit_minutes: validMinutes(d.minutes) })
  }
  return rows
})

function signature(rows: ActivityTimeLimit[]): string {
  return rows
    .map((row) => `${row.activity_type_id}=${row.time_limit_minutes}`)
    .sort()
    .join(',')
}

const dirty = computed(() => signature(payload.value) !== signature(query.data.value ?? []))

function modeOptions(type: ActivityType): Array<{ value: Mode; label: string }> {
  const fallback =
    type.default_time_limit_minutes != null
      ? t('conversation.timeLimits.useDefaultMinutes', { n: type.default_time_limit_minutes })
      : t('conversation.timeLimits.useDefaultNone')
  return [
    { value: 'default', label: fallback },
    { value: 'none', label: t('conversation.timeLimits.noLimit') },
    { value: 'custom', label: t('conversation.timeLimits.custom') },
  ]
}

const mutation = useMutation({
  mutationFn: (rows: ActivityTimeLimit[]) => putActivityTimeLimits(props.chatroomId, rows),
  onSuccess: (applied) => {
    qc.setQueryData(key.value, applied)
  },
})
const busy = computed(() => mutation.isPending.value)

async function save(): Promise<void> {
  if (busy.value || anyInvalid.value || !dirty.value || props.activityTypesFailed) return
  try {
    await mutation.mutateAsync(payload.value)
    toast.success(t('conversation.timeLimits.saved'))
  } catch {
    toast.error(t('conversation.timeLimits.saveFailed'))
  }
}
</script>

<template>
  <SCard
    v-if="!hidden"
    data-testid="time-limits-card"
  >
    <h2 class="time-limits__heading">
      {{ t('conversation.timeLimits.title') }}
    </h2>
    <p class="time-limits__desc">
      {{ t('conversation.timeLimits.description') }}
    </p>

    <SSkeleton
      v-if="query.isPending.value || activityTypesLoading"
      :lines="3"
    />
    <SQueryError
      v-else-if="query.isError.value"
      :message="t('conversation.timeLimits.loadFailed')"
      :retry-label="t('conversation.timeLimits.retry')"
      @retry="query.refetch()"
    />
    <p
      v-else-if="activityTypesFailed"
      class="time-limits__error"
      role="alert"
    >
      {{ t('conversation.timeLimits.typesLoadFailed') }}
    </p>
    <SEmptyState
      v-else-if="!activityTypes.length"
      :icon="ClockIcon"
      :title="t('conversation.timeLimits.noTypes')"
      data-testid="time-limits-empty"
    />
    <template v-else>
      <ul class="time-limits__list">
        <li
          v-for="type in activityTypes"
          :key="type.id"
          class="time-limits__row"
          :data-testid="`time-limit-${type.id}`"
        >
          <span class="time-limits__name">{{ type.name }}</span>
          <SSelect
            :model-value="drafts[type.id]?.mode ?? 'default'"
            :options="modeOptions(type)"
            size="sm"
            :disabled="busy"
            :aria-label="t('conversation.timeLimits.modeLabel', { name: type.name })"
            data-testid="time-limit-mode"
            @update:model-value="(v) => setMode(type.id, v as Mode)"
          />
          <SInput
            v-if="drafts[type.id]?.mode === 'custom'"
            :model-value="drafts[type.id]?.minutes ?? ''"
            type="number"
            size="sm"
            :min="BOUNDS.min"
            :max="BOUNDS.max"
            :error="rowInvalid(type.id)"
            :disabled="busy"
            :aria-label="t('conversation.timeLimits.minutesLabel', { name: type.name })"
            class="time-limits__number"
            data-testid="time-limit-minutes"
            @update:model-value="(v) => setMinutes(type.id, v)"
          />
        </li>
      </ul>

      <p
        v-if="anyInvalid"
        class="time-limits__error"
        role="alert"
      >
        {{ t('conversation.timeLimits.invalid') }}
      </p>

      <div class="time-limits__actions">
        <SButton
          size="sm"
          variant="primary"
          :loading="busy"
          :disabled="busy || anyInvalid || !dirty"
          data-testid="time-limits-save"
          @click="save"
        >
          {{ t('conversation.timeLimits.save') }}
        </SButton>
      </div>
    </template>
  </SCard>
</template>

<style scoped>
.time-limits__heading {
  font-size: var(--font-size-lg);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
  margin-bottom: var(--space-2);
}

.time-limits__desc {
  font-size: var(--font-size-sm);
  color: var(--color-muted);
  margin-bottom: var(--space-3);
}

.time-limits__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  list-style: none;
  padding: 0;
  margin: 0;
}

.time-limits__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.time-limits__name {
  min-width: 10rem;
  flex: 1;
  font-size: var(--font-size-sm);
  color: var(--color-fg);
}

.time-limits__number {
  width: 6rem;
}

.time-limits__error {
  margin-top: var(--space-2);
  font-size: var(--font-size-xs);
  color: var(--color-danger);
}

.time-limits__actions {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--space-4);
}
</style>
