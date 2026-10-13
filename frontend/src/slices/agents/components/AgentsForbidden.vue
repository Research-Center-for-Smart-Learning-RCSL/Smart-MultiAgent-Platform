<script setup lang="ts">
// Matrix row 28: agent configuration is readable by its editors only. Shown in
// place of an agents page for a caller the server would refuse, so the page says
// why instead of rendering a failed load.
import { useI18n } from 'vue-i18n'
import { LockClosedIcon } from '@heroicons/vue/24/outline'
import { SEmptyState } from '@shared/ui'

const { t } = useI18n()
</script>

<template>
  <div
    class="mt-6"
    data-testid="agents-forbidden"
  >
    <SEmptyState
      :icon="LockClosedIcon"
      :title="t('agents.forbidden.title')"
      :text="t('agents.forbidden.text')"
    />
  </div>
</template>
