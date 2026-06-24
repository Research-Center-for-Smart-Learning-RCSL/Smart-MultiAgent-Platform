<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ExclamationTriangleIcon } from '@heroicons/vue/24/outline'
import { SEmptyState, SButton } from '@shared/ui'
import { useSessionStore } from '@shared/stores/session'

const { t } = useI18n()
const session = useSessionStore()

const homeRoute = computed(() => session.isAuthenticated ? '/orgs' : '/')
</script>

<template>
  <div class="not-found">
    <SEmptyState
      :title="t('app.notFoundTitle')"
      :text="t('app.notFoundText')"
      :icon="ExclamationTriangleIcon"
    >
      <template #action>
        <SButton
          variant="primary"
          as="router-link"
          :to="homeRoute"
        >
          {{ t('app.backToHome') }}
        </SButton>
      </template>
    </SEmptyState>
  </div>
</template>

<style scoped>
.not-found {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
}
</style>
