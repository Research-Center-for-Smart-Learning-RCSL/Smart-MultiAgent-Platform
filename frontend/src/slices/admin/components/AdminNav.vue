<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  HomeIcon,
  UsersIcon,
  ShieldCheckIcon,
  NoSymbolIcon,
  BuildingOffice2Icon,
  FolderIcon,
  ClipboardDocumentListIcon,
  WrenchScrewdriverIcon,
  AdjustmentsHorizontalIcon,
  ChartBarIcon,
} from '@heroicons/vue/24/outline'
import { useBreakpoint } from '@shared/composables'
import { SSelect } from '@shared/ui'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const { isTablet, isDesktop } = useBreakpoint()

const navItems = [
  { name: 'admin.home', label: 'admin.nav.home', icon: HomeIcon },
  { name: 'admin.users', label: 'admin.nav.users', icon: UsersIcon },
  { name: 'admin.admins', label: 'admin.nav.admins', icon: ShieldCheckIcon },
  { name: 'admin.ipBans', label: 'admin.nav.ipBans', icon: NoSymbolIcon },
  { name: 'admin.orgs', label: 'admin.nav.orgs', icon: BuildingOffice2Icon },
  { name: 'admin.projects', label: 'admin.nav.projects', icon: FolderIcon },
  { name: 'admin.audit', label: 'admin.nav.audit', icon: ClipboardDocumentListIcon },
  { name: 'admin.ops', label: 'admin.nav.ops', icon: WrenchScrewdriverIcon },
  { name: 'admin.rateLimits', label: 'admin.nav.rateLimits', icon: AdjustmentsHorizontalIcon },
  { name: 'admin.metrics', label: 'admin.nav.metrics', icon: ChartBarIcon },
] as const

// >=lg: vertical sidebar · md: horizontal tabs · <md: dropdown selector.
const layout = computed<'sidebar' | 'tabs' | 'dropdown'>(() => {
  if (isDesktop.value) return 'sidebar'
  if (isTablet.value) return 'tabs'
  return 'dropdown'
})

// The user-detail page lives under the Users section.
const activeName = computed(() => {
  const name = route.name as string
  return name === 'admin.userDetail' ? 'admin.users' : name
})

function isActive(name: string): boolean {
  return activeName.value === name
}

const navOptions = computed(() =>
  navItems.map((item) => ({ value: item.name, label: t(item.label) })),
)

function onSelect(value: string | number): void {
  const name = String(value)
  if (name && name !== (route.name as string)) {
    void router.push({ name })
  }
}
</script>

<template>
  <!-- Mobile: dropdown selector -->
  <div
    v-if="layout === 'dropdown'"
    class="admin-nav admin-nav--dropdown"
  >
    <label
      class="sr-only"
      for="admin-nav-select"
    >{{ t('admin.nav.sectionLabel') }}</label>
    <SSelect
      id="admin-nav-select"
      :model-value="activeName"
      :options="navOptions"
      @update:model-value="onSelect"
    />
  </div>

  <!-- Tablet: horizontal tabs · Desktop: vertical sidebar -->
  <nav
    v-else
    class="admin-nav"
    :class="`admin-nav--${layout}`"
    :aria-label="t('admin.nav.label')"
  >
    <router-link
      v-for="item in navItems"
      :key="item.name"
      :to="{ name: item.name }"
      class="admin-nav__link"
      :class="{ 'admin-nav__link--active': isActive(item.name) }"
      :aria-current="isActive(item.name) ? 'page' : undefined"
    >
      <component
        :is="item.icon"
        class="admin-nav__icon"
        aria-hidden="true"
      />
      <span>{{ t(item.label) }}</span>
    </router-link>
  </nav>
</template>

<style scoped>
.admin-nav__link {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  min-height: 40px;
  border-radius: var(--radius-md);
  color: var(--color-fg);
  text-decoration: none;
  white-space: nowrap;
  transition: background var(--transition-fast), color var(--transition-fast);
}

.admin-nav__link:hover {
  background: var(--color-sidebar-hover);
  text-decoration: none;
}

.admin-nav__link--active {
  background: var(--color-sidebar-active-bg);
  color: var(--color-sidebar-active-text);
  font-weight: 600;
}

.admin-nav__icon {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
}

/* Desktop: vertical sidebar */
.admin-nav--sidebar {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.admin-nav--sidebar .admin-nav__link {
  width: 100%;
}

/* Tablet: horizontal scrollable tab row */
.admin-nav--tabs {
  display: flex;
  gap: 0.25rem;
  overflow-x: auto;
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 0.25rem;
}

.admin-nav--tabs .admin-nav__link {
  flex-shrink: 0;
}
</style>
