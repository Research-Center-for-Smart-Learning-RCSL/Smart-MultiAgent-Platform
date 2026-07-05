<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import { SAlert, SCard, SLoadingSpinner, SPromptAssistantConfigForm, SPromptTemplateManager } from '@shared/ui'

import { useConfigEditor } from '../composables/useConfigEditor'
import { useTemplateEditor } from '../composables/useTemplateEditor'
import type { ConfigScopeRef } from '../types'

const props = defineProps<{
  scope: ConfigScopeRef
}>()

const { t } = useI18n()

const {
  configQuery,
  form,
  dirty,
  keyOptions,
  modelOptions,
  keyRevoked,
  files,
  saving,
  uploading,
  save,
  uploadFile,
  deleteFile,
} = useConfigEditor(props.scope)

const { templates, busy, onCreate, onUpdate, onDelete } = useTemplateEditor(props.scope)
</script>

<template>
  <div class="space-y-6">
    <SCard variant="bordered">
      <h2 class="mb-4 text-lg font-semibold">
        {{ t('promptStudio.config.heading') }}
      </h2>
      <div
        v-if="configQuery.isLoading.value"
        class="flex justify-center p-6"
      >
        <SLoadingSpinner size="sm" />
      </div>
      <SAlert
        v-else-if="configQuery.isError.value"
        variant="danger"
      >
        {{ t('promptStudio.config.loadFailed') }}
      </SAlert>
      <SPromptAssistantConfigForm
        v-else
        :model-value="form"
        :scope-kind="scope.kind"
        :key-options="keyOptions"
        :model-options="modelOptions"
        :files="files"
        :key-revoked="keyRevoked"
        :saving="saving"
        :uploading="uploading"
        :dirty="dirty"
        @update:model-value="Object.assign(form, $event)"
        @save="save"
        @upload="uploadFile"
        @delete-file="deleteFile"
      />
    </SCard>

    <SCard variant="bordered">
      <h2 class="mb-4 text-lg font-semibold">
        {{ t('promptStudio.templates.heading') }}
      </h2>
      <SPromptTemplateManager
        :templates="templates"
        :busy="busy"
        @create="onCreate"
        @update="onUpdate"
        @delete="onDelete"
      />
    </SCard>
  </div>
</template>
