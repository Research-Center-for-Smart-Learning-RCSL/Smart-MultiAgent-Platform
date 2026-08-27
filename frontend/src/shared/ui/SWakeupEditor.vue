<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  AtSymbolIcon,
  BoltIcon,
  ChatBubbleLeftRightIcon,
  ChevronDownIcon,
  ClockIcon,
  Cog6ToothIcon,
} from '@heroicons/vue/24/outline'
import { SInput, SToggle } from '@shared/ui'
import { useCloneModel } from '@shared/composables'
import type { WakeupConfig } from '@shared/types/workflow'

const { t } = useI18n()

const props = defineProps<{
  modelValue: WakeupConfig
  readonly?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: WakeupConfig): void
}>()

const { local, emitUpdate } = useCloneModel(props, emit)

const disabled = computed(() => props.readonly ?? false)

// Unique per-instance control ids so <label for> associations stay correct when
// the editor renders once per bound agent (ChatroomSettingsView v-for).
const uid = useId()
function fid(key: string): string {
  return `${uid}-${key}`
}

// A wake mode is either "auto" (message/silence triggers drive it) or "call"
// (responds only when @mentioned). call_only is mutually exclusive with the two
// autonomous triggers, so entering call mode clears their enabled flags — this
// keeps the stored config self-consistent rather than leaving a contradictory
// "call_only + every_n both on" state the backend would silently ignore.
const mode = computed<'auto' | 'call'>({
  get: () => (local.triggers.call_only.enabled ? 'call' : 'auto'),
  set: (m) => {
    local.triggers.call_only.enabled = m === 'call'
    if (m === 'call') {
      local.triggers.every_n_messages.enabled = false
      local.triggers.silence_minutes.enabled = false
    }
    emitUpdate()
  },
})

function selectMode(m: 'auto' | 'call'): void {
  if (disabled.value) return
  mode.value = m
}

// Mirrors the backend WakeupConfig.is_inert(): in auto mode with neither trigger
// enabled the agent never speaks up on its own (it can still be @mentioned).
const isInert = computed(
  () =>
    mode.value === 'auto'
    && !local.triggers.every_n_messages.enabled
    && !local.triggers.silence_minutes.enabled,
)

const showAdvanced = ref(false)

// Ceilings matching the backend's own bounds for these fields (contexts/
// orchestration/domain/models.py) so the client can't produce a value the
// server would otherwise have to silently clamp or reject.
const EVERY_N_MAX = 1000
const SILENCE_MINUTES_MAX = 1440
const AUTOSTOP_MAX_DEFAULT_MAX = 100
const REFRESH_HOURS_MAX = 720

// Clamp numeric inputs into [min, max]; the backend still validates, but a
// blank/zero/negative/oversized value from the field should never round-trip
// as-is. SInput(type=number) emits a number, but guard the string path
// defensively.
function clampRange(raw: string | number, min: number, max: number): number {
  const n = typeof raw === 'number' ? raw : Number(raw)
  if (!Number.isFinite(n)) return min
  return Math.min(Math.max(Math.floor(n), min), max)
}

function onEveryNEnabled(v: boolean): void {
  local.triggers.every_n_messages.enabled = v
  emitUpdate()
}
function onEveryN(v: string | number): void {
  local.triggers.every_n_messages.n = clampRange(v, 1, EVERY_N_MAX)
  emitUpdate()
}
function onSilenceEnabled(v: boolean): void {
  local.triggers.silence_minutes.enabled = v
  emitUpdate()
}
function onSilenceMinutes(v: string | number): void {
  local.triggers.silence_minutes.t_minutes = clampRange(v, 1, SILENCE_MINUTES_MAX)
  emitUpdate()
}
function onAutostopRounds(v: string | number): void {
  local.triggers.silence_minutes.autostop_rounds = clampRange(
    v, 1, local.triggers.silence_minutes.autostop_max_default,
  )
  emitUpdate()
}
function onAutostopMax(v: string | number): void {
  local.triggers.silence_minutes.autostop_max_default = clampRange(v, 1, AUTOSTOP_MAX_DEFAULT_MAX)
  emitUpdate()
}
function onAllowSelfOpen(v: boolean): void {
  local.allow_self_open = v
  emitUpdate()
}
function onRefreshHours(v: string | number): void {
  local.refresh_every_hours = clampRange(v, 1, REFRESH_HOURS_MAX)
  emitUpdate()
}
</script>

<template>
  <div class="wakeup-editor">
    <div
      v-if="isInert"
      class="wakeup-editor__inert"
    >
      {{ t('app.wakeup.inert') }}
    </div>

    <!-- Wake mode ------------------------------------------------------- -->
    <div
      class="wakeup-modes"
      role="radiogroup"
      :aria-label="t('app.wakeup.modeLabel')"
    >
      <button
        type="button"
        role="radio"
        class="wakeup-mode"
        :class="{ 'wakeup-mode--active': mode === 'auto' }"
        :aria-checked="mode === 'auto'"
        :disabled="disabled"
        @click="selectMode('auto')"
      >
        <BoltIcon
          class="wakeup-mode__icon"
          aria-hidden="true"
        />
        <span class="wakeup-mode__title">{{ t('app.wakeup.modeAuto') }}</span>
        <span class="wakeup-mode__desc">{{ t('app.wakeup.modeAutoDesc') }}</span>
      </button>
      <button
        type="button"
        role="radio"
        class="wakeup-mode"
        :class="{ 'wakeup-mode--active': mode === 'call' }"
        :aria-checked="mode === 'call'"
        :disabled="disabled"
        @click="selectMode('call')"
      >
        <AtSymbolIcon
          class="wakeup-mode__icon"
          aria-hidden="true"
        />
        <span class="wakeup-mode__title">{{ t('app.wakeup.modeCall') }}</span>
        <span class="wakeup-mode__desc">{{ t('app.wakeup.modeCallDesc') }}</span>
      </button>
    </div>

    <!-- Auto triggers --------------------------------------------------- -->
    <div
      v-if="mode === 'auto'"
      class="wakeup-triggers"
    >
      <p class="wakeup-triggers__hint">
        {{ t('app.wakeup.triggersHint') }}
      </p>

      <!-- every_n_messages -->
      <section
        class="wakeup-card"
        :class="{ 'wakeup-card--on': local.triggers.every_n_messages.enabled }"
      >
        <div class="wakeup-card__head">
          <ChatBubbleLeftRightIcon
            class="wakeup-card__icon"
            aria-hidden="true"
          />
          <label
            :for="fid('everyn-toggle')"
            class="wakeup-card__title"
          >{{ t('app.wakeup.everyNTitle') }}</label>
          <SToggle
            :id="fid('everyn-toggle')"
            :model-value="local.triggers.every_n_messages.enabled"
            :disabled="disabled"
            @update:model-value="onEveryNEnabled"
          />
        </div>
        <div
          v-if="local.triggers.every_n_messages.enabled"
          class="wakeup-card__body"
        >
          <div class="wakeup-field">
            <label
              :for="fid('everyn')"
              class="wakeup-field__label"
            >{{ t('app.wakeup.everyNFieldLabel') }}</label>
            <div class="wakeup-field__control">
              <SInput
                :id="fid('everyn')"
                type="number"
                size="sm"
                :min="1"
                :max="EVERY_N_MAX"
                :model-value="local.triggers.every_n_messages.n"
                :disabled="disabled"
                @update:model-value="onEveryN"
              />
              <span class="wakeup-field__suffix">{{ t('app.wakeup.suffixMessages') }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- silence_minutes -->
      <section
        class="wakeup-card"
        :class="{ 'wakeup-card--on': local.triggers.silence_minutes.enabled }"
      >
        <div class="wakeup-card__head">
          <ClockIcon
            class="wakeup-card__icon"
            aria-hidden="true"
          />
          <label
            :for="fid('silence-toggle')"
            class="wakeup-card__title"
          >{{ t('app.wakeup.silenceTitle') }}</label>
          <SToggle
            :id="fid('silence-toggle')"
            :model-value="local.triggers.silence_minutes.enabled"
            :disabled="disabled"
            @update:model-value="onSilenceEnabled"
          />
        </div>
        <div
          v-if="local.triggers.silence_minutes.enabled"
          class="wakeup-card__body"
        >
          <div class="wakeup-field">
            <label
              :for="fid('silence')"
              class="wakeup-field__label"
            >{{ t('app.wakeup.silenceFieldLabel') }}</label>
            <div class="wakeup-field__control">
              <SInput
                :id="fid('silence')"
                type="number"
                size="sm"
                :min="1"
                :max="SILENCE_MINUTES_MAX"
                :model-value="local.triggers.silence_minutes.t_minutes"
                :disabled="disabled"
                @update:model-value="onSilenceMinutes"
              />
              <span class="wakeup-field__suffix">{{ t('app.wakeup.suffixMinutes') }}</span>
            </div>
          </div>
          <div class="wakeup-field">
            <label
              :for="fid('autostop')"
              class="wakeup-field__label"
            >{{ t('app.wakeup.autostopFieldLabel') }}</label>
            <div class="wakeup-field__control">
              <SInput
                :id="fid('autostop')"
                type="number"
                size="sm"
                :min="1"
                :max="local.triggers.silence_minutes.autostop_max_default"
                :model-value="local.triggers.silence_minutes.autostop_rounds"
                :disabled="disabled"
                @update:model-value="onAutostopRounds"
              />
              <span class="wakeup-field__suffix">{{ t('app.wakeup.suffixRounds') }}</span>
            </div>
          </div>
          <p class="wakeup-field__help">
            {{ t('app.wakeup.autostopHelp') }}
          </p>
        </div>
      </section>
    </div>

    <!-- Advanced -------------------------------------------------------- -->
    <div class="wakeup-advanced">
      <button
        type="button"
        class="wakeup-advanced__toggle"
        :aria-expanded="showAdvanced"
        @click="showAdvanced = !showAdvanced"
      >
        <Cog6ToothIcon
          class="wakeup-advanced__icon"
          aria-hidden="true"
        />
        <span>{{ t('app.wakeup.advanced') }}</span>
        <ChevronDownIcon
          class="wakeup-advanced__chevron"
          :class="{ 'wakeup-advanced__chevron--open': showAdvanced }"
          aria-hidden="true"
        />
      </button>
      <div
        v-if="showAdvanced"
        class="wakeup-advanced__body"
      >
        <div class="wakeup-field">
          <label
            :for="fid('maxcap')"
            class="wakeup-field__label"
          >{{ t('app.wakeup.maxCapLabel') }}</label>
          <div class="wakeup-field__control">
            <SInput
              :id="fid('maxcap')"
              type="number"
              size="sm"
              :min="1"
              :max="AUTOSTOP_MAX_DEFAULT_MAX"
              :model-value="local.triggers.silence_minutes.autostop_max_default"
              :disabled="disabled"
              @update:model-value="onAutostopMax"
            />
            <span class="wakeup-field__suffix">{{ t('app.wakeup.suffixRounds') }}</span>
          </div>
        </div>
        <div class="wakeup-inline-toggle">
          <SToggle
            :id="fid('selfopen')"
            :model-value="local.allow_self_open"
            :disabled="disabled"
            @update:model-value="onAllowSelfOpen"
          />
          <label
            :for="fid('selfopen')"
            class="wakeup-inline-toggle__label"
          >{{ t('app.wakeup.allowSelfOpenLabel') }}</label>
        </div>
        <!-- Without this, the label reads as an unrelated capability and nothing
             tells a designer that an otherwise-enabled trigger will still be
             suppressed in an empty room. -->
        <p class="wakeup-field__help">
          {{ t('app.wakeup.allowSelfOpenHelp') }}
        </p>
        <div class="wakeup-field">
          <label
            :for="fid('refresh')"
            class="wakeup-field__label"
          >{{ t('app.wakeup.refreshLabel') }}</label>
          <div class="wakeup-field__control">
            <SInput
              :id="fid('refresh')"
              type="number"
              size="sm"
              :min="1"
              :max="REFRESH_HOURS_MAX"
              :model-value="local.refresh_every_hours"
              :disabled="disabled"
              @update:model-value="onRefreshHours"
            />
            <span class="wakeup-field__suffix">{{ t('app.wakeup.suffixHours') }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wakeup-editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.wakeup-editor__inert {
  padding: var(--space-2) var(--space-3);
  font-size: var(--font-size-code);
  color: var(--color-warning-on);
  background: var(--color-warning-tint);
  border-radius: var(--radius-md);
}

/* ─── Mode selector ─────────────────────────────────────────────── */
.wakeup-modes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-2-5);
}

.wakeup-mode {
  display: grid;
  grid-template-columns: auto 1fr;
  grid-template-rows: auto auto;
  column-gap: var(--space-2);
  row-gap: var(--space-0-5);
  align-items: center;
  text-align: left;
  padding: 12px 14px;
  border: 1.5px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg);
  cursor: pointer;
  transition:
    border-color var(--transition-fast),
    background var(--transition-fast);
}

.wakeup-mode:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--color-accent), var(--color-border) 40%);
}

.wakeup-mode:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.wakeup-mode--active {
  border-color: var(--color-accent);
  background: color-mix(in srgb, var(--color-accent), transparent 92%);
}

.wakeup-mode__icon {
  grid-row: 1 / span 2;
  width: 22px;
  height: 22px;
  color: var(--color-muted);
}

.wakeup-mode--active .wakeup-mode__icon {
  color: var(--color-accent);
}

.wakeup-mode__title {
  font-size: var(--font-size-sm);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
}

.wakeup-mode__desc {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  line-height: 1.35;
}

/* ─── Trigger cards ─────────────────────────────────────────────── */
.wakeup-triggers {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.wakeup-triggers__hint {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  margin: 0;
}

.wakeup-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg);
  transition: border-color var(--transition-fast);
}

.wakeup-card--on {
  border-color: color-mix(in srgb, var(--color-accent), var(--color-border) 55%);
}

.wakeup-card__head {
  display: flex;
  align-items: center;
  gap: var(--space-2-5);
  padding: var(--space-2-5) var(--space-3);
  cursor: pointer;
}

.wakeup-card__icon {
  width: 20px;
  height: 20px;
  color: var(--color-muted);
  flex-shrink: 0;
}

.wakeup-card--on .wakeup-card__icon {
  color: var(--color-accent);
}

.wakeup-card__title {
  flex: 1;
  font-size: var(--font-size-sm);
  font-weight: var(--weight-medium);
  color: var(--color-fg);
}

.wakeup-card__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2-5);
  padding: var(--space-3);
  border-top: 1px solid var(--color-border-subtle);
  background: var(--color-surface);
  border-bottom-left-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
}

/* ─── Fields ────────────────────────────────────────────────────── */
.wakeup-field {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.wakeup-field__label {
  font-size: var(--font-size-code);
  color: var(--color-fg);
}

.wakeup-field__control {
  display: flex;
  align-items: center;
  gap: var(--space-1-5);
  flex-shrink: 0;
}

.wakeup-field__control :deep(.s-input) {
  width: 80px;
}

.wakeup-field__suffix {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  min-width: 2.5em;
}

.wakeup-field__help {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  margin: 0;
}

/* ─── Advanced ──────────────────────────────────────────────────── */
.wakeup-advanced {
  border-top: 1px solid var(--color-border-subtle);
  padding-top: var(--space-2-5);
}

.wakeup-advanced__toggle {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-0-5);
  font-size: var(--font-size-code);
  font-weight: var(--weight-medium);
  color: var(--color-muted);
  background: none;
  border: none;
  cursor: pointer;
  transition: color var(--transition-fast);
}

.wakeup-advanced__toggle:hover {
  color: var(--color-fg);
}

.wakeup-advanced__toggle:focus-visible {
  border-radius: var(--radius-sm);
}

.wakeup-advanced__icon {
  width: 16px;
  height: 16px;
}

.wakeup-advanced__chevron {
  width: 16px;
  height: 16px;
  transition: transform var(--transition-fast);
}

.wakeup-advanced__chevron--open {
  transform: rotate(180deg);
}

.wakeup-advanced__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  margin-top: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
}

.wakeup-inline-toggle {
  display: flex;
  align-items: center;
  gap: var(--space-2-5);
  cursor: pointer;
}

.wakeup-inline-toggle__label {
  font-size: var(--font-size-code);
  color: var(--color-fg);
}
</style>
