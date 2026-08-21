<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import { SPageHeader } from '@shared/ui'

import PromptStudioSettings from '../components/PromptStudioSettings.vue'

const { t } = useI18n()
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <SPageHeader
      :title="t('promptStudio.personal.title')"
      :subtitle="t('promptStudio.personal.subtitle')"
    />
    <PromptStudioSettings
      :scope="{ kind: 'user' }"
      class="mt-6"
    />
  </div>
</template>
