<template>
  <SModal
    :open="open"
    :title="t('conversation.observers.releaseTitle')"
    size="lg"
    @close="emit('close')"
  >
    <div class="release">
      <label
        class="release__label"
        :for="contentId"
      >
        {{ t('conversation.observers.releaseContent') }}
      </label>
      <STextarea
        :id="contentId"
        v-model="content"
        :rows="8"
        :maxlength="MAX_CONTENT"
      />
      <div class="release__content-foot">
        <button
          type="button"
          class="release__restore"
          :disabled="content === original"
          @click="content = original"
        >
          {{ t('conversation.observers.restoreOriginal') }}
        </button>
        <SCharCount
          :current="content.length"
          :max="MAX_CONTENT"
          hide-until-near
        />
      </div>

      <fieldset class="release__targets">
        <legend class="release__legend">
          {{ t('conversation.observers.releaseTargetLegend') }}
        </legend>

        <div class="release__opt">
          <SRadio
            v-model="target"
            value="room"
            name="release-target"
          />
          <span>
            <span class="release__opt-title">{{ t('conversation.observers.targetRoom') }}</span>
            <span class="release__opt-help">
              {{ disclose ? t('conversation.observers.targetRoomHelpDisclosed') : t('conversation.observers.targetRoomHelp') }}
            </span>
          </span>
        </div>

        <div class="release__opt">
          <SRadio
            v-model="target"
            value="agents"
            name="release-target"
          />
          <span>
            <span class="release__opt-title">{{ t('conversation.observers.targetAgents') }}</span>
            <span class="release__opt-help">{{ t('conversation.observers.targetAgentsHelp') }}</span>
          </span>
        </div>

        <div
          v-if="target === 'agents'"
          class="release__agents"
        >
          <SEmptyState
            v-if="!normalAgents.length"
            :title="t('conversation.observers.noEligibleAgents')"
          />
          <div
            v-for="a in normalAgents"
            :key="a.id"
            class="release__agent"
          >
            <SCheckbox
              :model-value="selected.has(a.id)"
              @update:model-value="toggle(a.id, $event)"
            >
              {{ a.name }}
            </SCheckbox>
          </div>

          <div class="release__wake">
            <SToggle v-model="wake" />
            <span>
              <span class="release__opt-title">{{ t('conversation.observers.wakeNow') }}</span>
              <span class="release__opt-help">{{ t('conversation.observers.wakeHelp') }}</span>
            </span>
          </div>
        </div>
      </fieldset>

      <SAlert
        v-if="errorMessage"
        variant="danger"
        focus-on-mount
        :title="errorMessage"
      />
    </div>

    <template #footer>
      <SButton
        variant="ghost"
        @click="emit('close')"
      >
        {{ t('conversation.observers.cancel') }}
      </SButton>
      <SButton
        variant="primary"
        :disabled="!canSubmit"
        :loading="submitting"
        @click="submit"
      >
        {{ t('conversation.observers.confirmRelease') }}
      </SButton>
    </template>
  </SModal>
</template>

<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { SAlert, SButton, SCharCount, SCheckbox, SEmptyState, SModal, SRadio, STextarea, SToggle } from '@shared/ui'
import type { ReleaseBody } from '../api'
import type { Observation } from '../types'

// Mirrors the backend _MAX_CONTENT_MD.
const MAX_CONTENT = 100_000

const props = defineProps<{
  open: boolean
  observation: Observation | null
  disclose: boolean
  normalAgents: Array<{ id: string; name: string }>
}>()

const emit = defineEmits<{
  close: []
  submit: [body: ReleaseBody]
}>()

const { t } = useI18n()
const contentId = useId()

const original = computed(() => props.observation?.content_md ?? '')
const content = ref('')
const target = ref<'room' | 'agents'>('room')
const selected = ref<Set<string>>(new Set())
const wake = ref(false)
const submitting = ref(false)
const errorMessage = ref('')

// Reset every time a new observation opens the dialog.
watch(
  () => [props.open, props.observation?.id],
  () => {
    if (props.open) {
      content.value = original.value
      target.value = 'room'
      selected.value = new Set()
      wake.value = false
      submitting.value = false
      errorMessage.value = ''
    }
  },
  { immediate: true },
)

function toggle(id: string, on: boolean): void {
  const next = new Set(selected.value)
  if (on) next.add(id)
  else next.delete(id)
  selected.value = next
}

const canSubmit = computed(() => {
  if (!content.value.trim()) return false
  if (target.value === 'agents') return selected.value.size > 0
  return true
})

function submit(): void {
  if (!canSubmit.value || submitting.value) return
  // W-2 (B.4.3): the parent owns the async release and drives `submitting`
  // via setSubmitting — resetting it here would race the parent's await
  // (emits are synchronous) and drop the pending state for the whole request.
  submitting.value = true
  errorMessage.value = ''
  const override = content.value !== original.value ? content.value : undefined
  const body: ReleaseBody =
    target.value === 'room'
      ? { target: 'room', ...(override ? { content_override: override } : {}) }
      : {
          target: 'agents',
          agent_ids: [...selected.value],
          wake: wake.value,
          ...(override ? { content_override: override } : {}),
        }
  emit('submit', body)
}

// Exposed so the parent can drive submitting/error state during the release
// request without re-implementing the form.
defineExpose({
  setSubmitting: (v: boolean) => {
    submitting.value = v
  },
  setError: (msg: string) => {
    // W-8: inline only — the dialog is always open when this runs, so a
    // toast would duplicate the visible SAlert and outlive the dialog.
    errorMessage.value = msg
  },
})
</script>

<style scoped>
.release {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.release__label {
  font-size: var(--font-size-code);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
}

.release__content-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.release__restore {
  background: none;
  border: none;
  padding: 0;
  color: var(--color-accent);
  font-size: var(--font-size-xs);
  cursor: pointer;
}

.release__restore:disabled {
  color: var(--color-muted);
  cursor: default;
}

.release__targets {
  border: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2-5);
}

.release__legend {
  font-size: var(--font-size-code);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
  padding: 0;
  margin-bottom: var(--space-1);
}

.release__opt {
  display: flex;
  gap: var(--space-2);
  align-items: flex-start;
}

.release__opt-title {
  display: block;
  font-size: var(--font-size-code);
  color: var(--color-fg);
}

.release__opt-help {
  display: block;
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}

.release__agents {
  margin-left: 26px;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.release__agent {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-size-code);
}

.release__wake {
  display: flex;
  gap: var(--space-2);
  align-items: flex-start;
  margin-top: var(--space-1);
}
</style>
