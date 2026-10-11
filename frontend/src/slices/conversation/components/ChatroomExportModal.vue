<template>
  <SModal
    :open="open"
    :title="t('conversation.chatroom.exportTitle')"
    size="sm"
    @close="emit('close')"
  >
    <!-- Job in flight or resolved. -->
    <div
      v-if="!showForm"
      class="export-state"
      data-testid="export-status"
    >
      <template v-if="phase.kind === 'ready'">
        <CheckCircleIcon class="export-state__icon export-state__icon--ok" />
        <p class="export-state__title">
          {{ t('conversation.chatroom.exportReady') }}
        </p>
        <SButton
          variant="primary"
          data-testid="export-download"
          @click="emit('download')"
        >
          <template #icon-left>
            <ArrowDownTrayIcon class="w-4 h-4" />
          </template>
          {{ t('conversation.chatroom.exportDownload') }}
        </SButton>
        <p class="export-state__hint">
          {{ t('conversation.chatroom.exportExpiry') }}
        </p>
      </template>

      <template v-else-if="phase.kind === 'start-failed' || phase.kind === 'job-failed'">
        <ExclamationCircleIcon class="export-state__icon export-state__icon--err" />
        <p class="export-state__title">
          {{
            phase.kind === 'job-failed'
              ? t('conversation.chatroom.exportJobFailed')
              : t('conversation.chatroom.exportStartFailed')
          }}
        </p>
        <p
          v-if="phase.kind === 'job-failed'"
          class="export-state__hint"
        >
          {{ t('conversation.chatroom.exportJobFailedDetail') }}
        </p>
        <div class="export-state__actions">
          <SButton
            variant="secondary"
            data-testid="export-cancel"
            @click="emit('close')"
          >
            {{ t('conversation.chatroom.cancel') }}
          </SButton>
          <SButton
            variant="primary"
            data-testid="export-retry"
            @click="emit('retry')"
          >
            {{ t('conversation.chatroom.retry') }}
          </SButton>
        </div>
      </template>

      <template v-else-if="phase.kind === 'stalled'">
        <ClockIcon class="export-state__icon" />
        <p class="export-state__title">
          {{
            phase.reason === 'unreachable'
              ? t('conversation.chatroom.exportUnreachable')
              : t('conversation.chatroom.exportStalled')
          }}
        </p>
        <SButton
          variant="primary"
          data-testid="export-check-again"
          @click="emit('checkAgain')"
        >
          {{ t('conversation.chatroom.exportCheckAgain') }}
        </SButton>
      </template>

      <template v-else>
        <DocumentArrowDownIcon class="export-state__icon" />
        <p class="export-state__title">
          {{ t('conversation.chatroom.exportRunning') }}
        </p>
        <SProgressBar indeterminate />
      </template>
    </div>

    <!-- Config form. -->
    <form
      v-else
      class="export-form"
      @submit.prevent="submit"
    >
      <fieldset class="export-form__group">
        <legend class="export-form__legend">
          {{ t('conversation.chatroom.exportFormat') }}
        </legend>
        <div
          v-for="opt in formatOptions"
          :key="opt.value"
          class="export-form__radio"
        >
          <SRadio
            :id="`export-fmt-${opt.value}`"
            v-model="format"
            :value="opt.value"
            name="export-format"
          />
          <label :for="`export-fmt-${opt.value}`">{{ opt.label }}</label>
        </div>
      </fieldset>

      <SFormField
        :label="t('conversation.chatroom.exportRange')"
        name="export-range"
      >
        <SSelect
          v-model="dateRange"
          :options="rangeOptions"
        />
      </SFormField>

      <div
        v-if="dateRange === 'custom'"
        class="export-form__dates"
      >
        <SFormField
          :label="t('conversation.chatroom.exportStart')"
          name="export-start"
          :error="startError"
        >
          <SInput
            v-model="start"
            :type="dateInputType"
          />
        </SFormField>
        <SFormField
          :label="t('conversation.chatroom.exportEnd')"
          name="export-end"
          :error="endError"
        >
          <SInput
            v-model="end"
            :type="dateInputType"
          />
        </SFormField>
      </div>

      <SCheckbox
        v-model="includeAttachments"
        class="export-form__attachments"
        data-testid="export-include-attachments"
      >
        {{ t('conversation.chatroom.exportIncludeAttachments') }}
      </SCheckbox>

      <p
        v-if="formError"
        class="export-form__error"
        role="alert"
      >
        {{ formError }}
      </p>
    </form>

    <template
      v-if="showForm"
      #footer
    >
      <SButton
        variant="secondary"
        @click="emit('close')"
      >
        {{ t('conversation.chatroom.cancel') }}
      </SButton>
      <SButton
        variant="primary"
        data-testid="submit-export"
        :disabled="!canSubmit"
        @click="submit"
      >
        <template
          v-if="phase.kind === 'starting'"
          #icon-left
        >
          <SLoadingSpinner size="sm" />
        </template>
        {{ t('conversation.chatroom.export') }}
      </SButton>
    </template>
  </SModal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  CheckCircleIcon,
  ClockIcon,
  ExclamationCircleIcon,
  DocumentArrowDownIcon,
  ArrowDownTrayIcon,
} from '@heroicons/vue/24/outline'
import {
  SModal,
  SButton,
  SRadio,
  SSelect,
  SInput,
  SFormField,
  SProgressBar,
  SLoadingSpinner,
  SCheckbox,
} from '@shared/ui'
import type { ExportOptions } from '../api'
import type { ExportPhase } from '../composables/useChatroomExport'

const props = defineProps<{
  open: boolean
  phase: ExportPhase
}>()

const emit = defineEmits<{
  close: []
  submit: [opts: ExportOptions]
  download: []
  retry: []
  checkAgain: []
}>()

const { t } = useI18n()

// SInput's `type` prop union omits 'date', but SInput forwards the value
// unmodified to the native <input type>, coercing only 'number' inputs in its
// input handler — so 'date' is genuinely runtime-compatible even though the
// prop type doesn't declare it.
const dateInputType = 'date' as unknown as 'text'

const format = ref<NonNullable<ExportOptions['format']>>('markdown')
const dateRange = ref<NonNullable<ExportOptions['date_range']>>('all')
const start = ref('')
const end = ref('')
// [R13.17]: the export carries its attachments unless the user opts out.
const includeAttachments = ref(true)

const formatOptions = computed(() => [
  { value: 'markdown', label: t('conversation.chatroom.formatMarkdown') },
  { value: 'json', label: t('conversation.chatroom.formatJson') },
  { value: 'pdf', label: t('conversation.chatroom.formatPdf') },
])

const rangeOptions = computed(() => [
  { value: 'all', label: t('conversation.chatroom.rangeAll') },
  { value: 'last_7_days', label: t('conversation.chatroom.range7') },
  { value: 'last_30_days', label: t('conversation.chatroom.range30') },
  { value: 'custom', label: t('conversation.chatroom.rangeCustom') },
])

// The form is kept mounted state-wise (its refs live here), so whenever it is
// shown again it still holds the values last submitted.
const showForm = computed(() =>
  ['idle', 'starting', 'invalid'].includes(props.phase.kind),
)

// Dates are ISO `yyyy-mm-dd`, so string order is date order.
const rangeReversed = computed(
  () => dateRange.value === 'custom' && !!start.value && !!end.value && end.value < start.value,
)

// A server 422 stays on screen until the user changes the input.
type ServerInvalid = Extract<ExportPhase, { kind: 'invalid' }>
const serverInvalid = ref<Pick<ServerInvalid, 'field' | 'pdf'> | null>(null)
watch(
  () => props.phase,
  (p) => {
    serverInvalid.value = p.kind === 'invalid' ? { field: p.field, pdf: p.pdf } : null
  },
  { immediate: true },
)
watch([format, dateRange, start, end, includeAttachments], () => {
  serverInvalid.value = null
})

const endError = computed(() => {
  if (rangeReversed.value) return t('conversation.chatroom.exportRangeInvalid')
  if (serverInvalid.value?.field === 'end') return t('conversation.chatroom.exportInvalid')
  return ''
})
const startError = computed(() =>
  serverInvalid.value?.field === 'start' ? t('conversation.chatroom.exportInvalid') : '',
)
const formError = computed(() => {
  const invalid = serverInvalid.value
  if (!invalid || invalid.field !== null) return ''
  return invalid.pdf
    ? t('conversation.chatroom.exportTooLargeForPdf', { count: invalid.pdf.count, limit: invalid.pdf.limit })
    : t('conversation.chatroom.exportInvalid')
})

// A 422 that names a date blocks submit until that input changes. One that
// names nothing the user can edit only explains itself; locking the button
// then would leave no way forward.
const canSubmit = computed(
  () =>
    props.phase.kind !== 'starting' &&
    !serverInvalid.value?.field &&
    !rangeReversed.value &&
    !(dateRange.value === 'custom' && (!start.value || !end.value)),
)

function submit(): void {
  if (!canSubmit.value) return
  const opts: ExportOptions = {
    format: format.value,
    date_range: dateRange.value,
    include_attachments: includeAttachments.value,
  }
  if (dateRange.value === 'custom') {
    opts.start = start.value
    opts.end = end.value
  }
  emit('submit', opts)
}
</script>

<style scoped>
.export-form__group {
  border: none;
  padding: 0;
  margin: 0 0 var(--space-4);
}

.export-form__legend {
  font-size: var(--font-size-sm);
  font-weight: var(--weight-medium);
  color: var(--color-fg);
  margin-bottom: var(--space-2);
}

.export-form__radio {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) 0;
  font-size: var(--font-size-sm);
  color: var(--color-fg);
}

.export-form__dates {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}

.export-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--space-3);
  padding: var(--space-4) 0;
}

.export-state__icon {
  width: 48px;
  height: 48px;
  color: var(--color-muted);
}

.export-state__icon--ok {
  color: var(--color-success);
}

.export-state__icon--err {
  color: var(--color-danger);
}

.export-state__title {
  font-size: var(--font-size-sm);
  color: var(--color-fg);
}

.export-form__attachments {
  margin-top: var(--space-3);
}

.export-form__error {
  font-size: var(--font-size-sm);
  color: var(--color-danger);
  margin-top: var(--space-3);
}

.export-state__actions {
  display: flex;
  gap: var(--space-2);
}

.export-state__hint {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}
</style>
