<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import { SButton, SCard, SPageHeader, SPromptTemplateManager } from '@shared/ui'

import { useTemplateEditor } from '../composables/useTemplateEditor'

const { t } = useI18n()

// Platform-scope assistant config is no longer a singleton (R29.02/R29.16):
// persona presets are managed on their own page. Only platform templates
// (unaffected by that change) live here.
const { templates, busy, onCreate, onUpdate, onDelete } = useTemplateEditor({ kind: 'platform' })
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <SPageHeader
      :title="t('promptStudio.admin.title')"
      :subtitle="t('promptStudio.admin.subtitle')"
    >
      <template #actions>
        <SButton
          variant="secondary"
          as="router-link"
          :to="{ name: 'admin.promptPresets' }"
        >
          {{ t('promptStudio.admin.managePresets') }}
        </SButton>
      </template>
    </SPageHeader>

    <SCard
      variant="bordered"
      class="mt-6"
    >
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
