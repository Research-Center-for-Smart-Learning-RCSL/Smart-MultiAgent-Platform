<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { INPUT_LIMITS } from '@shared/constants/inputLimits'

import SAlert from './SAlert.vue'
import SButton from './SButton.vue'
import SCharCount from './SCharCount.vue'
import SCodeEditor from './SCodeEditor.vue'
import SFileUpload from './SFileUpload.vue'
import SFormField from './SFormField.vue'
import SInput from './SInput.vue'
import SSelect from './SSelect.vue'

// Structural contracts ??kept local so shared/ui does not import a slice type.
export interface ConfigFormValue {
  system_prompt: string
  key_id: string | null
  model_id: string | null
  daily_request_limit_per_user: number
  enabled: boolean
  hide_platform_templates: boolean
}

export interface ConfigFormFile {
  id: string
  filename: string
  size_bytes: number
  scan_status: string
  extracted_chars: number
}

interface Option {
  value: string | number
  label: string
}

const props = withDefaults(
  defineProps<{
    modelValue: ConfigFormValue
    scopeKind: 'platform' | 'org' | 'user'
    keyOptions: Option[]
    modelOptions: Option[]
    files: ConfigFormFile[]
    keyRevoked?: boolean
    saving?: boolean
    uploading?: boolean
    dirty?: boolean
  }>(),
  {
    keyRevoked: false,
    saving: false,
    uploading: false,
    dirty: false,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: ConfigFormValue]
  save: []
  upload: [file: File]
  deleteFile: [id: string]
  uploadError: [message: string]
}>()

const { t } = useI18n()

function patch<K extends keyof ConfigFormValue>(key: K, value: ConfigFormValue[K]): void {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

const keySelectOptions = computed<Option[]>(() => [
  { value: '', label: t('promptStudio.config.noKey') },
  ...props.keyOptions,
])

function onFiles(files: File[]): void {
  if (files[0]) emit('upload', files[0])
}
</script>

<template>
  <div class="space-y-6">
    <SFormField
      :label="t('promptStudio.config.systemPromptLabel')"
      name="system_prompt"
      :help="t('promptStudio.config.systemPromptHelp')"
    >
      <SCodeEditor
        language="markdown"
        :rows="10"
        :model-value="modelValue.system_prompt"
        :placeholder="t('promptStudio.config.systemPromptPlaceholder')"
        @update:model-value="patch('system_prompt', $event)"
      />
    </SFormField>
    <SCharCount
      :current="modelValue.system_prompt.length"
      :max="INPUT_LIMITS.PROMPT_ASSISTANT_SYSTEM_PROMPT"
    />

    <SAlert
      v-if="keyRevoked"
      variant="warning"
      :title="t('promptStudio.config.keyRevokedTitle')"
    >
      {{ t('promptStudio.config.keyRevokedBody') }}
    </SAlert>

    <div class="grid gap-4 sm:grid-cols-2">
      <SFormField
        :label="t('promptStudio.config.keyLabel')"
        name="key_id"
        :help="t('promptStudio.config.keyHelp')"
      >
        <SSelect
          :model-value="modelValue.key_id ?? ''"
          :options="keySelectOptions"
          @update:model-value="patch('key_id', $event === '' ? null : String($event))"
        />
      </SFormField>

      <SFormField
        :label="t('promptStudio.config.modelLabel')"
        name="model_id"
      >
        <SSelect
          :model-value="modelValue.model_id ?? ''"
          :options="modelOptions"
          :disabled="modelOptions.length === 0"
          @update:model-value="patch('model_id', $event === '' ? null : String($event))"
        />
      </SFormField>
    </div>

    <SFormField
      :label="t('promptStudio.config.dailyCapLabel')"
      name="daily_request_limit_per_user"
      :help="t('promptStudio.config.dailyCapHelp')"
    >
      <SInput
        type="number"
        :min="1"
        :model-value="modelValue.daily_request_limit_per_user"
        @update:model-value="patch('daily_request_limit_per_user', Number($event) || 1)"
      />
    </SFormField>

    <label class="flex items-center gap-2 text-sm">
      <input
        type="checkbox"
        :checked="modelValue.enabled"
        @change="patch('enabled', ($event.target as HTMLInputElement).checked)"
      >
      {{ t('promptStudio.config.enabledLabel') }}
    </label>

    <label
      v-if="scopeKind === 'org'"
      class="flex items-center gap-2 text-sm"
    >
      <input
        type="checkbox"
        :checked="modelValue.hide_platform_templates"
        @change="patch('hide_platform_templates', ($event.target as HTMLInputElement).checked)"
      >
      {{ t('promptStudio.config.hidePlatformTemplatesLabel') }}
    </label>

    <div class="space-y-2">
      <h4 class="text-sm font-semibold">
        {{ t('promptStudio.config.filesTitle') }}
      </h4>
      <ul
        v-if="files.length"
        class="divide-y divide-[var(--color-border-subtle)] rounded border border-[var(--color-border)]"
      >
        <li
          v-for="f in files"
          :key="f.id"
          class="flex items-center justify-between gap-3 px-3 py-2 text-sm"
        >
          <span class="truncate">
            {{ f.filename }}
            <span class="text-[var(--color-muted)]">({{ f.extracted_chars }} {{ t('promptStudio.config.chars') }})</span>
          </span>
          <SButton
            variant="ghost"
            size="sm"
            type="button"
            @click="emit('deleteFile', f.id)"
          >
            {{ t('promptStudio.actions.remove') }}
          </SButton>
        </li>
      </ul>
      <SFileUpload
        accept=".pdf,.txt,.md,.docx"
        :max-size="5 * 1024 * 1024"
        :disabled="!!uploading"
        @files="onFiles"
        @error="emit('uploadError', $event)"
      >
        <p class="text-sm text-[var(--color-muted)]">
          {{ t('promptStudio.config.filesHint') }}
        </p>
      </SFileUpload>
    </div>

    <div class="flex justify-end">
      <SButton
        variant="primary"
        :loading="!!saving"
        :disabled="!dirty"
        type="button"
        @click="emit('save')"
      >
        {{ t('app.save') }}
      </SButton>
    </div>
  </div>
</template>
