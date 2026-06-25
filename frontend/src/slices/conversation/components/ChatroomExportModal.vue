<template>
  <SModal
    :open="open"
    :title="t('conversation.chatroom.exportTitle')"
    size="sm"
    @close="emit('close')"
  >
    <!-- Job in flight or resolved. -->
    <div
      v-if="job"
      class="export-state"
      data-testid="export-status"
    >
      <template v-if="job.status === 'ready'">
        <CheckCircleIcon class="export-state__icon export-state__icon--ok" />
        <p class="export-state__title">
          {{ t('conversation.chatroom.exportReady') }}
        </p>
        <SButton
          variant="primary"
          :as="'a'"
          :to="job.url ?? undefined"
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

      <template v-else-if="job.status === 'failed'">
        <ExclamationCircleIcon class="export-state__icon export-state__icon--err" />
        <p class="export-state__title">
          {{ t('conversation.chatroom.exportFailed') }}
        </p>
        <SButton
          variant="primary"
          @click="submit"
        >
          {{ t('conversation.chatroom.retry') }}
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
        >
          <SInput
            v-model="start"
            type="date"
          />
        </SFormField>
        <SFormField
          :label="t('conversation.chatroom.exportEnd')"
          name="export-end"
        >
          <SInput
            v-model="end"
            type="date"
          />
        </SFormField>
      </div>
    </form>

    <template
      v-if="!job"
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
        :disabled="dateRange === 'custom' && (!start || !end)"
        @click="submit"
      >
        {{ t('conversation.chatroom.export') }}
      </SButton>
    </template>
  </SModal>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  CheckCircleIcon,
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
} from '@shared/ui'
import type { ExportOptions } from '../api'

defineProps<{
  open: boolean
  job: { status: string; url: string | null } | null
}>()

const emit = defineEmits<{
  close: []
  submit: [opts: ExportOptions]
}>()

const { t } = useI18n()

const format = ref<NonNullable<ExportOptions['format']>>('markdown')
const dateRange = ref<NonNullable<ExportOptions['date_range']>>('all')
const start = ref('')
const end = ref('')

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

function submit(): void {
  const opts: ExportOptions = { format: format.value, date_range: dateRange.value }
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
  margin: 0 0 16px;
}

.export-form__legend {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-fg);
  margin-bottom: 8px;
}

.export-form__radio {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 0.875rem;
  color: var(--color-fg);
}

.export-form__dates {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.export-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 12px;
  padding: 16px 0;
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
  font-size: 14px;
  color: var(--color-fg);
}

.export-state__hint {
  font-size: 12px;
  color: var(--color-muted);
}
</style>
