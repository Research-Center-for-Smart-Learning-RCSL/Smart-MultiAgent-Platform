<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { SAlert, SCard, SLoadingSpinner, SPageHeader, SPromptAssistantConfigForm } from '@shared/ui'

import { usePresetEditor } from '../composables/usePresetEditor'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const presetId = computed(() => {
  const raw = route.params.presetId
  return typeof raw === 'string' ? raw : null
})

const {
  presetsQuery,
  preset,
  form,
  dirty,
  keyOptions,
  modelOptions,
  keyRevoked,
  files,
  isNew,
  saving,
  uploading,
  save,
  uploadFile,
  deleteFile,
} = usePresetEditor(() => presetId.value)

// A preset id in the URL that isn't in the list once it has loaded is a bad
// link, not a loading state -- distinct from "still fetching" (isNew is the
// create route, which never resolves to a preset by design).
const notFound = computed(
  () => !isNew.value && !presetsQuery.isPending.value && !presetsQuery.isError.value && preset.value === null,
)

async function onSave(): Promise<void> {
  const createdId = await save()
  if (createdId) {
    await router.replace({ name: 'admin.promptPresetEdit', params: { presetId: createdId } })
  }
}
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <SPageHeader
      :title="isNew ? t('promptStudio.presets.newPreset') : t('promptStudio.presets.editTitle')"
      :breadcrumbs="[
        { label: t('promptStudio.presets.title'), to: { name: 'admin.promptPresets' } },
        { label: isNew ? t('promptStudio.presets.newPreset') : (preset?.name ?? '') },
      ]"
    />

    <div
      v-if="presetsQuery.isLoading.value && !isNew"
      class="flex justify-center p-6"
    >
      <SLoadingSpinner size="sm" />
    </div>
    <SAlert
      v-else-if="presetsQuery.isError.value"
      variant="danger"
    >
      {{ t('promptStudio.config.loadFailed') }}
    </SAlert>
    <SAlert
      v-else-if="notFound"
      variant="danger"
    >
      {{ t('promptStudio.presets.notFound') }}
    </SAlert>
    <SCard
      v-else
      variant="bordered"
      class="mt-4"
    >
      <SPromptAssistantConfigForm
        :model-value="form"
        scope-kind="preset"
        :key-options="keyOptions"
        :model-options="modelOptions"
        :files="files"
        :key-revoked="keyRevoked"
        :saving="saving"
        :uploading="uploading"
        :dirty="dirty || isNew"
        @update:model-value="Object.assign(form, $event)"
        @save="onSave"
        @upload="uploadFile"
        @delete-file="deleteFile"
      />
    </SCard>
  </div>
</template>
