<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  BuildingOffice2Icon,
  FolderIcon,
  KeyIcon,
  BellIcon,
  InboxArrowDownIcon,
  CpuChipIcon,
  UserGroupIcon,
  DocumentTextIcon,
  CircleStackIcon,
  FolderOpenIcon,
  RectangleGroupIcon,
  MagnifyingGlassIcon,
  Square3Stack3DIcon,
  ShieldCheckIcon,
  ShieldExclamationIcon,
  UsersIcon,
  PuzzlePieceIcon,
  ClipboardDocumentCheckIcon,
} from '@heroicons/vue/24/outline'
import { useSessionStore } from '@shared/stores/session'
import { useWorkspaceStore } from '@shared/stores/workspace'
import { useBreakpoint } from '@shared/composables/useBreakpoint'
import { useProjectRole } from '@slices/tenancy'
import SidebarChatroomList from './SidebarChatroomList.vue'
import SidebarGroup from './SidebarGroup.vue'
import SidebarNavItem from './SidebarNavItem.vue'
import OrgProjectSwitcher from './OrgProjectSwitcher.vue'

const { t } = useI18n()
const session = useSessionStore()
const workspace = useWorkspaceStore()
const { isDesktop } = useBreakpoint()

// Owner/admin gate for the Manage group. Reactive projectId so the gate
// re-resolves when the active project changes. `decided` keeps an owner's group
// from flashing in mid role-resolution (R11.10); until decided it stays hidden,
// exactly like the non-owner case.
const { decided, isAuthorized } = useProjectRole(() => workspace.projectId ?? undefined)

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

const agentNav = computed<NavItem[]>(() => {
  const pid = workspace.projectId
  if (!pid) return []
  return [
    { icon: CpuChipIcon, label: t('app.sidebar.agents'), route: `/projects/${pid}/agents` },
    { icon: UserGroupIcon, label: t('app.sidebar.agentGroups'), route: `/projects/${pid}/agent-groups` },
  ]
})

const knowledgeNav = computed<NavItem[]>(() => {
  const pid = workspace.projectId
  if (!pid) return []
  return [
    { icon: DocumentTextIcon, label: t('app.sidebar.ragConfigs'), route: `/projects/${pid}/rag-configs` },
    { icon: CircleStackIcon, label: t('app.sidebar.conceptMaps'), route: `/projects/${pid}/graphrag-configs` },
    { icon: FolderOpenIcon, label: t('app.sidebar.knowledgeMaps'), route: `/projects/${pid}/knowmap-configs` },
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

// Owner-only project management surfaces (gated in the template). Path-string
// routes match the rest of the sidebar and sidestep the members route's `id`
// vs. `projectId` param-name difference.
const manageNav = computed<NavItem[]>(() => {
  const pid = workspace.projectId
  if (!pid) return []
  return [
    { icon: UsersIcon, label: t('app.sidebar.members'), route: `/projects/${pid}/members` },
    { icon: PuzzlePieceIcon, label: t('app.sidebar.skills'), route: `/projects/${pid}/skills` },
    { icon: ClipboardDocumentCheckIcon, label: t('app.sidebar.activityTypes'), route: `/projects/${pid}/activity-types` },
  ]
})

</script>

<template>
  <aside
    v-if="session.isAuthenticated"
    class="sidebar"
  >
    <nav class="sidebar__nav">
      <!-- Org/project switcher — desktop only; on mobile it stays in the top
           bar since the sidebar is a hidden drawer. -->
      <div
        v-if="isDesktop"
        class="sidebar__switcher"
      >
        <OrgProjectSwitcher compact />
      </div>

      <!-- Global — Workspace -->
      <div class="sidebar__section">
        <SidebarNavItem
          v-for="item in workspaceNav"
          :key="item.route"
          :icon="item.icon"
          :label="item.label"
          :to="item.route"
        />
      </div>

      <!-- Global — Personal -->
      <SidebarGroup
        :label="t('app.sidebar.groupPersonal')"
        storage-key="personal"
      >
        <SidebarNavItem
          v-for="item in personalNav"
          :key="item.route"
          :icon="item.icon"
          :label="item.label"
          :to="item.route"
        />
      </SidebarGroup>

      <!-- Project Context -->
      <template v-if="workspace.hasProject">
        <div class="sidebar__divider" />

        <div class="section-header">
          {{ t('app.sidebar.projectContext') }}
        </div>

        <!-- Agents + Agent Groups -->
        <div class="sidebar__section">
          <SidebarNavItem
            v-for="item in agentNav"
            :key="item.route"
            :icon="item.icon"
            :label="item.label"
            :to="item.route"
          />
        </div>

        <!-- Knowledge -->
        <SidebarGroup
          :label="t('app.sidebar.groupKnowledge')"
          storage-key="knowledge"
        >
          <SidebarNavItem
            v-for="item in knowledgeNav"
            :key="item.route"
            :icon="item.icon"
            :label="item.label"
            :to="item.route"
          />
        </SidebarGroup>

        <!-- Keys -->
        <SidebarGroup
          :label="t('app.sidebar.groupKeys')"
          storage-key="project-keys"
        >
          <SidebarNavItem
            v-for="item in projectKeysNav"
            :key="item.route"
            :icon="item.icon"
            :label="item.label"
            :to="item.route"
          />
        </SidebarGroup>

        <!-- Infrastructure (default collapsed) -->
        <SidebarGroup
          :label="t('app.sidebar.groupInfra')"
          storage-key="infra"
          :default-collapsed="true"
        >
          <SidebarNavItem
            v-for="item in infraNav"
            :key="item.route"
            :icon="item.icon"
            :label="item.label"
            :to="item.route"
          />
        </SidebarGroup>

        <!-- Manage (owner/admin only) -->
        <SidebarGroup
          v-if="decided && isAuthorized"
          :label="t('app.sidebar.groupManage')"
          storage-key="project-manage"
        >
          <SidebarNavItem
            v-for="item in manageNav"
            :key="item.route"
            :icon="item.icon"
            :label="item.label"
            :to="item.route"
          />
        </SidebarGroup>

        <!-- Recent Chatrooms -->
        <div class="sidebar__divider" />
        <SidebarChatroomList />
      </template>

      <!-- Admin -->
      <template v-if="session.me?.is_admin">
        <div class="sidebar__divider" />
        <div class="sidebar__section">
          <SidebarNavItem
            :icon="ShieldExclamationIcon"
            :label="t('app.sidebar.admin')"
            to="/admin"
          />
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

.sidebar__switcher {
  padding: 4px 12px 8px;
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
</style>
