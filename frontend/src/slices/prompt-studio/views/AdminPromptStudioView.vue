<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import { SPageHeader } from '@shared/ui'

import PromptStudioSettings from '../components/PromptStudioSettings.vue'

const { t } = useI18n()
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <SPageHeader
      :title="t('promptStudio.admin.title')"
      :subtitle="t('promptStudio.admin.subtitle')"
    />
    <PromptStudioSettings
      :scope="{ kind: 'platform' }"
      class="mt-6"
    />
  </div>
</template>
