<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { SDrawer } from '@shared/ui'
import { useBreakpoint } from '@shared/composables/useBreakpoint'
import AppTopBar from '../components/AppTopBar.vue'
import AppSidebar from '../components/AppSidebar.vue'

const route = useRoute()
const { isDesktop } = useBreakpoint()

// null = follow the route's auto behavior; true/false = explicit user choice
// for the current route. An explicit choice must be able to override
// autoCollapsed in BOTH directions, otherwise the toggle is dead on immersive
// routes (e.g. chatrooms) where autoCollapsed is always true.
const manualOverride = ref<boolean | null>(null)

const isImmersiveRoute = computed(() => {
  const path = route.path
  if (/^\/chatrooms\/[^/]+$/.test(path)) return true
  if (/\/workflows\/[^/]+\/edit$/.test(path)) return true
  return false
})

const autoCollapsed = computed(() =>
  isImmersiveRoute.value || route.meta.sidebarCollapsed === true,
)

const sidebarCollapsed = computed(() => {
  if (!isDesktop.value) return true
  if (manualOverride.value !== null) return manualOverride.value
  return autoCollapsed.value
})

const sidebarDrawerOpen = ref(false)

function toggleSidebar(): void {
  if (isDesktop.value) {
    manualOverride.value = !sidebarCollapsed.value
  } else {
    sidebarDrawerOpen.value = !sidebarDrawerOpen.value
  }
}

watch(
  () => route.path,
  () => {
    sidebarDrawerOpen.value = false
    manualOverride.value = null
  },
)

const noPadding = computed(() =>
  route.meta.contentPadding === 'none' || isImmersiveRoute.value,
)
</script>

<template>
  <div
    class="app-shell"
    :class="{ 'app-shell--sidebar-collapsed': sidebarCollapsed }"
  >
    <a
      class="skip-link"
      href="#main-content"
    >{{ $t('app.skipToContent') }}</a>

    <AppTopBar
      class="app-shell__topbar"
      :sidebar-open="!sidebarCollapsed || sidebarDrawerOpen"
      @toggle-sidebar="toggleSidebar"
    />

    <aside
      v-if="isDesktop && !sidebarCollapsed"
      class="app-shell__sidebar"
    >
      <AppSidebar />
    </aside>

    <SDrawer
      v-if="!isDesktop"
      :open="sidebarDrawerOpen"
      side="left"
      size="sm"
      @close="sidebarDrawerOpen = false"
    >
      <AppSidebar />
    </SDrawer>

    <main
      id="main-content"
      tabindex="-1"
      class="app-shell__content"
      :class="{ 'app-shell__content--no-pad': noPadding }"
    >
      <slot />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: grid;
  grid-template-columns: var(--sidebar-width) 1fr;
  grid-template-rows: var(--topbar-height) 1fr;
  height: 100vh;
  overflow: hidden;
}

.app-shell--sidebar-collapsed {
  grid-template-columns: 0 1fr;
}

.app-shell__topbar {
  grid-column: 1 / -1;
  grid-row: 1;
}

.app-shell__sidebar {
  grid-column: 1;
  grid-row: 2;
  overflow-y: auto;
  overflow-x: hidden;
  background: var(--color-sidebar-bg);
  border-right: 1px solid var(--color-border);
  transition: width var(--transition-slow);
}

.app-shell__content {
  grid-column: 2;
  grid-row: 2;
  overflow-y: auto;
  background: var(--color-bg);
  padding: 24px;
}

.app-shell__content--no-pad {
  padding: 0;
}

/* The content region is a skip-link target (tabindex="-1"); it should not
   show a focus ring when focused programmatically. */
.app-shell__content:focus-visible {
  box-shadow: none;
  outline: none;
}

@media (max-width: 1023px) {
  .app-shell {
    grid-template-columns: 0 1fr;
  }

  .app-shell__content {
    padding: 16px;
  }

  .app-shell__content--no-pad {
    padding: 0;
  }
}

@media (max-width: 479px) {
  .app-shell__content {
    padding: 8px;
  }

  .app-shell__content--no-pad {
    padding: 0;
  }
}
</style>
