<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'

import { SPageHeader } from '@shared/ui'

import PromptStudioSettings from '../components/PromptStudioSettings.vue'

const { t } = useI18n()
const route = useRoute()
const orgId = computed(() => String(route.params.orgId))
</script>

<template>
  <div class="mx-auto max-w-3xl p-4">
    <SPageHeader
      :title="t('promptStudio.org.title')"
      :subtitle="t('promptStudio.org.subtitle')"
    />
    <PromptStudioSettings
      :key="orgId"
      :scope="{ kind: 'org', orgId }"
      class="mt-6"
    />
  </div>
</template>
