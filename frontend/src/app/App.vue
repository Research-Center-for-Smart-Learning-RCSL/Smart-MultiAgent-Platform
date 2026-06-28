<script setup lang="ts">
import { computed } from 'vue'
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

const layoutComponent = computed(() => {
  const layout = route.meta.layout as string | undefined
  if (layout === 'public') return PublicLayout
  if (layout === 'auth') return AuthLayout
  if (layout === 'app') return AppShell
  return route.meta.requiresAuth ? AppShell : AuthLayout
})
</script>

<template>
  <SNetworkBanner />
  <ImpersonationBanner />
  <ErrorBoundary>
    <component :is="layoutComponent">
      <router-view :key="$route.path" />
    </component>
  </ErrorBoundary>
  <Toaster
    position="top-right"
    :duration="4000"
    rich-colors
  />
  <SConfirmDialog />
  <SIdleDialog />
</template>
