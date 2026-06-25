<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Bars3Icon, XMarkIcon } from '@heroicons/vue/24/outline'
import { ThemeToggle } from '@shared/ui'
import { useSessionStore } from '@shared/stores/session'
import { useBreakpoint } from '@shared/composables/useBreakpoint'
import { NotificationBell } from '@slices/notifications'
import UserMenu from './UserMenu.vue'
import OrgProjectSwitcher from './OrgProjectSwitcher.vue'

defineProps<{
  sidebarOpen: boolean
}>()

const emit = defineEmits<{
  'toggle-sidebar': []
}>()

const { t } = useI18n()
const session = useSessionStore()
const { isMobile } = useBreakpoint()
</script>

<template>
  <header class="topbar">
    <!-- Left zone -->
    <div class="topbar__left">
      <button
        class="topbar__sidebar-toggle"
        type="button"
        :aria-label="t('app.topbar.toggleSidebar')"
        @click="emit('toggle-sidebar')"
      >
        <XMarkIcon
          v-if="sidebarOpen"
          class="topbar__toggle-icon"
        />
        <Bars3Icon
          v-else
          class="topbar__toggle-icon"
        />
      </button>

      <RouterLink
        :to="session.isAuthenticated ? '/orgs' : '/'"
        class="topbar__wordmark"
      >
        SMAP
      </RouterLink>
    </div>

    <!-- Center zone -->
    <div class="topbar__center">
      <OrgProjectSwitcher :compact="isMobile" />
    </div>

    <!-- Right zone -->
    <div class="topbar__right">
      <NotificationBell />
      <UserMenu />
      <ThemeToggle v-if="!isMobile" />
    </div>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  display: flex;
  align-items: center;
  height: var(--topbar-height);
  padding: 0 16px;
  background: var(--color-bg);
  border-bottom: 1px solid var(--color-border);
  z-index: var(--z-topbar);
}

.topbar__left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.topbar__sidebar-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  padding: 0;
  background: none;
  border: none;
  border-radius: var(--radius-md);
  color: var(--color-fg);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.topbar__sidebar-toggle:hover {
  background: var(--color-surface);
}

.topbar__sidebar-toggle:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.topbar__toggle-icon {
  width: 24px;
  height: 24px;
}

.topbar__wordmark {
  font-size: 1.125rem;
  font-weight: 700;
  color: var(--color-accent);
  text-decoration: none;
  line-height: 1;
  user-select: none;
}

.topbar__wordmark:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
  border-radius: var(--radius-md);
}

.topbar__center {
  flex: 1;
  display: flex;
  justify-content: center;
  min-width: 0;
}

.topbar__right {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
  flex-shrink: 0;
}
</style>
