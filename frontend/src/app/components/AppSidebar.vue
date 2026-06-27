<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  BuildingOffice2Icon,
  FolderIcon,
  KeyIcon,
  BellIcon,
  InboxArrowDownIcon,
  CpuChipIcon,
  DocumentTextIcon,
  CircleStackIcon,
  RectangleGroupIcon,
  MagnifyingGlassIcon,
  Square3Stack3DIcon,
  ShieldCheckIcon,
  ShieldExclamationIcon,
} from '@heroicons/vue/24/outline'
import { useSessionStore } from '@shared/stores/session'
import { useWorkspaceStore } from '@shared/stores/workspace'
import SidebarChatroomList from './SidebarChatroomList.vue'
import SidebarGroup from './SidebarGroup.vue'

const { t } = useI18n()
const route = useRoute()
const session = useSessionStore()
const workspace = useWorkspaceStore()

interface NavItem {
  icon: typeof BuildingOffice2Icon
  label: string
  route: string
}

const workspaceNav = computed<NavItem[]>(() => [
  { icon: BuildingOffice2Icon, label: t('app.sidebar.orgs'), route: '/orgs' },
  { icon: FolderIcon, label: t('app.sidebar.projects'), route: '/projects' },
])

const personalNav = computed<NavItem[]>(() => [
  { icon: KeyIcon, label: t('app.sidebar.keys'), route: '/keys' },
  { icon: BellIcon, label: t('app.sidebar.notifications'), route: '/notifications' },
  { icon: InboxArrowDownIcon, label: t('app.sidebar.invites'), route: '/invites' },
])

const agentItem = computed<NavItem | null>(() => {
  const pid = workspace.projectId
  if (!pid) return null
  return { icon: CpuChipIcon, label: t('app.sidebar.agents'), route: `/projects/${pid}/agents` }
})

const knowledgeNav = computed<NavItem[]>(() => {
  const pid = workspace.projectId
  if (!pid) return []
  return [
    { icon: DocumentTextIcon, label: t('app.sidebar.ragConfigs'), route: `/projects/${pid}/rag-configs` },
    { icon: CircleStackIcon, label: t('app.sidebar.graphrag'), route: `/projects/${pid}/graphrag-configs` },
  ]
})

const projectKeysNav = computed<NavItem[]>(() => {
  const pid = workspace.projectId
  if (!pid) return []
  return [
    { icon: KeyIcon, label: t('app.sidebar.projectKeys'), route: `/projects/${pid}/keys` },
    { icon: RectangleGroupIcon, label: t('app.sidebar.keyGroups'), route: `/projects/${pid}/key-groups` },
    { icon: MagnifyingGlassIcon, label: t('app.sidebar.searchKeys'), route: `/projects/${pid}/search-keys` },
  ]
})

const infraNav = computed<NavItem[]>(() => {
  const pid = workspace.projectId
  if (!pid) return []
  return [
    { icon: Square3Stack3DIcon, label: t('app.sidebar.workspaces'), route: `/projects/${pid}/workspaces` },
    { icon: ShieldCheckIcon, label: t('app.sidebar.mcpAllowlist'), route: `/projects/${pid}/mcp/egress-allowlist` },
  ]
})

function isActive(path: string): boolean {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}
</script>

<template>
  <aside
    v-if="session.isAuthenticated"
    class="sidebar"
  >
    <nav class="sidebar__nav">
      <!-- Global — Workspace -->
      <SidebarGroup
        :label="t('app.sidebar.groupWorkspace')"
        storage-key="workspace"
      >
        <RouterLink
          v-for="item in workspaceNav"
          :key="item.route"
          :to="item.route"
          class="nav-item"
          :class="{ 'nav-item--active': isActive(item.route) }"
        >
          <component
            :is="item.icon"
            class="nav-icon"
          />
          <span class="nav-label">{{ item.label }}</span>
        </RouterLink>
      </SidebarGroup>

      <!-- Global — Personal -->
      <SidebarGroup
        :label="t('app.sidebar.groupPersonal')"
        storage-key="personal"
      >
        <RouterLink
          v-for="item in personalNav"
          :key="item.route"
          :to="item.route"
          class="nav-item"
          :class="{ 'nav-item--active': isActive(item.route) }"
        >
          <component
            :is="item.icon"
            class="nav-icon"
          />
          <span class="nav-label">{{ item.label }}</span>
        </RouterLink>
      </SidebarGroup>

      <!-- Project Context -->
      <template v-if="workspace.hasProject">
        <div class="sidebar__divider" />

        <div class="section-header">
          {{ t('app.sidebar.projectContext') }}
        </div>

        <!-- Agents — standalone -->
        <div
          v-if="agentItem"
          class="sidebar__section"
        >
          <RouterLink
            :to="agentItem.route"
            class="nav-item"
            :class="{ 'nav-item--active': isActive(agentItem.route) }"
          >
            <component
              :is="agentItem.icon"
              class="nav-icon"
            />
            <span class="nav-label">{{ agentItem.label }}</span>
          </RouterLink>
        </div>

        <!-- Knowledge -->
        <SidebarGroup
          :label="t('app.sidebar.groupKnowledge')"
          storage-key="knowledge"
        >
          <RouterLink
            v-for="item in knowledgeNav"
            :key="item.route"
            :to="item.route"
            class="nav-item"
            :class="{ 'nav-item--active': isActive(item.route) }"
          >
            <component
              :is="item.icon"
              class="nav-icon"
            />
            <span class="nav-label">{{ item.label }}</span>
          </RouterLink>
        </SidebarGroup>

        <!-- Keys -->
        <SidebarGroup
          :label="t('app.sidebar.groupKeys')"
          storage-key="project-keys"
        >
          <RouterLink
            v-for="item in projectKeysNav"
            :key="item.route"
            :to="item.route"
            class="nav-item"
            :class="{ 'nav-item--active': isActive(item.route) }"
          >
            <component
              :is="item.icon"
              class="nav-icon"
            />
            <span class="nav-label">{{ item.label }}</span>
          </RouterLink>
        </SidebarGroup>

        <!-- Infrastructure (default collapsed) -->
        <SidebarGroup
          :label="t('app.sidebar.groupInfra')"
          storage-key="infra"
          :default-collapsed="true"
        >
          <RouterLink
            v-for="item in infraNav"
            :key="item.route"
            :to="item.route"
            class="nav-item"
            :class="{ 'nav-item--active': isActive(item.route) }"
          >
            <component
              :is="item.icon"
              class="nav-icon"
            />
            <span class="nav-label">{{ item.label }}</span>
          </RouterLink>
        </SidebarGroup>

        <!-- Recent Chatrooms -->
        <div class="sidebar__divider" />
        <SidebarChatroomList />
      </template>

      <!-- Admin -->
      <template v-if="session.me?.is_admin">
        <div class="sidebar__divider" />
        <div class="sidebar__section">
          <RouterLink
            to="/admin"
            class="nav-item"
            :class="{ 'nav-item--active': isActive('/admin') }"
          >
            <ShieldExclamationIcon
              class="nav-icon"
            />
            <span class="nav-label">{{ t('app.sidebar.admin') }}</span>
          </RouterLink>
        </div>
      </template>
    </nav>
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  height: 100%;
  overflow-y: auto;
  background-color: var(--color-sidebar-bg);
  border-right: 1px solid var(--color-border);
  z-index: var(--z-sidebar);
  flex-shrink: 0;
}

.sidebar__nav {
  display: flex;
  flex-direction: column;
  padding: 8px 0;
}

.sidebar__section {
  display: flex;
  flex-direction: column;
}

.sidebar__divider {
  height: 1px;
  background-color: var(--color-border);
  margin: 8px 16px;
}

.section-header {
  text-transform: uppercase;
  font-size: 11px;
  font-weight: 600;
  color: var(--color-sidebar-section-text);
  padding: 16px 16px 8px;
  letter-spacing: 0.05em;
}

.nav-item {
  display: flex;
  align-items: center;
  height: 40px;
  padding: 0 16px;
  gap: 12px;
  font-size: 14px;
  font-weight: 400;
  color: var(--color-sidebar-text);
  text-decoration: none;
  transition: background-color var(--transition-fast);
}

.nav-item:hover {
  background-color: var(--color-sidebar-hover);
}

.nav-item--active {
  background-color: var(--color-sidebar-active-bg);
  color: var(--color-sidebar-active-text);
  border-left: 3px solid var(--color-sidebar-active-text);
  padding-left: 13px;
}

.nav-icon {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  color: inherit;
}

.nav-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
