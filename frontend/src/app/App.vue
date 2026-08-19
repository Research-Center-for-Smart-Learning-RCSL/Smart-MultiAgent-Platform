<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { Toaster } from 'vue-sonner'
import { ImpersonationBanner } from '@slices/admin'
import { useBanKickGuard } from '@shared/composables'
import { SConfirmDialog, SIdleDialog, SNetworkBanner } from '@shared/ui'
import ErrorBoundary from './ErrorBoundary.vue'
import AuthLayout from './layouts/AuthLayout.vue'
import AppShell from './layouts/AppShell.vue'
import PublicLayout from './layouts/PublicLayout.vue'

useBanKickGuard()

const route = useRoute()
const { t } = useI18n()

const layoutComponent = computed(() => {
  const layout = route.meta.layout as string | undefined
  if (layout === 'public') return PublicLayout
  if (layout === 'auth') return AuthLayout
  if (layout === 'app') return AppShell
  return route.meta.requiresAuth ? AppShell : AuthLayout
})
</script>

<template>
  <SNetworkBanner :below-topbar="layoutComponent === AppShell" />
  <ImpersonationBanner />
  <ErrorBoundary>
    <component :is="layoutComponent">
      <!-- Keyed on path (not fullPath): param navigation remounts the view,
           query-only changes (filters, pagination) never do. The route
           transition classes live in shared/styles/main.css. -->
      <router-view v-slot="{ Component }">
        <Transition
          name="route"
          mode="out-in"
        >
          <component
            :is="Component"
            :key="$route.path"
          />
        </Transition>
      </router-view>
    </component>
  </ErrorBoundary>
  <!-- Toast visuals are token-themed in shared/styles/main.css (third-party
       overrides section) so they follow both themes; stock rich-colors is off. -->
  <Toaster
    position="top-right"
    :duration="4000"
    :container-aria-label="t('app.notifications.label')"
    :toast-options="{ closeButtonAriaLabel: t('app.notifications.close') }"
  />
  <SConfirmDialog />
  <SIdleDialog />
</template>
