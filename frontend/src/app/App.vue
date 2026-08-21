<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { Toaster } from 'vue-sonner'
import { ImpersonationBanner } from '@slices/admin'
import { useBanKickGuard } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { SConfirmDialog, SIdleDialog, SNetworkBanner } from '@shared/ui'
import ErrorBoundary from './ErrorBoundary.vue'
import AuthLayout from './layouts/AuthLayout.vue'
import AppShell from './layouts/AppShell.vue'
import PublicLayout from './layouts/PublicLayout.vue'
import { toasterProps } from './toasterProps'

useBanKickGuard()

const route = useRoute()
const { t } = useI18n()
const session = useSessionStore()

const layoutComponent = computed(() => {
  const layout = route.meta.layout as string | undefined
  if (layout === 'public') return PublicLayout
  if (layout === 'auth') return AuthLayout
  if (layout === 'app') return AppShell
  // 'auto' follows the session (02-layout-shell.md:351). The catch-all cannot
  // express this through requiresAuth: that would send a mistyped URL to
  // /login instead of rendering the 404 the visitor asked for.
  if (layout === 'auto') return session.isAuthenticated ? AppShell : AuthLayout
  return route.meta.requiresAuth ? AppShell : AuthLayout
})
</script>

<template>
  <SNetworkBanner :below-topbar="layoutComponent === AppShell" />
  <!-- The impersonation banner shares this flow column with the layout, so it
       reserves its own height instead of painting over the top bar. The fixed
       and teleported siblings stay outside: the wrapper would constrain them
       and none of them takes part in flow anyway. -->
  <div class="app-root">
    <ImpersonationBanner />
    <component :is="layoutComponent">
      <!-- Wraps only the routed view. A fallback rendered inside a shell that
           is itself broken is not a fallback, so a throw in the shell chrome
           deliberately propagates to app.config.errorHandler instead. -->
      <ErrorBoundary>
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
      </ErrorBoundary>
    </component>
  </div>
  <!-- Toast visuals are token-themed in shared/styles/main.css (third-party
       overrides section) so they follow both themes; stock rich-colors is off. -->
  <Toaster v-bind="toasterProps(t)" />
  <SConfirmDialog />
  <SIdleDialog />
</template>

<style scoped>
/* min-height, never height: AuthLayout and PublicLayout size themselves and
   must keep scrolling the document. AppShell is the one layout that claims the
   remaining space, which it does with `flex: 1 1 0px` in its own stylesheet -
   a length basis, not `flex: 1`. See the note there before changing either.

   dvh, not vh (02-layout-shell.md:111-116). On mobile browsers `vh` resolves
   against the LARGE viewport, so a 100vh root is taller than the visible area
   by the toolbar height and the shell's bottom grid row - notably the chatroom
   composer - is below the fold on first paint. It also silently breaks
   useVisualViewport: that composable measures the keyboard against
   window.innerHeight, so a consumer sized against the large viewport
   over-shoots the visible band by exactly the toolbar height. */
.app-root {
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
}
</style>
