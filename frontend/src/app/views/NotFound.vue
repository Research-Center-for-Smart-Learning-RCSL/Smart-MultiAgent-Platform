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
/* 100%, not a viewport or pixel constant: this view renders under both layouts.
   Under AppShell the root is a direct child of main (ErrorBoundary renders a
   bare <slot> and Transition adds no node), and main's grid row gives it a
   definite height, so this resolves to the real content box at every
   breakpoint. Under AuthLayout the parent is an auto-height wrapper, so the
   percentage resolves to no floor at all and the block simply wraps, which
   .auth-layout then centres along with the rest of the column. `height: 100%`
   would not degrade that way, which is why this is a min-height. */
.not-found {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
}
</style>
