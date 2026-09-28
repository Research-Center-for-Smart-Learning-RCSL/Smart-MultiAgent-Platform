<script setup lang="ts">
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  BuildingOffice2Icon,
  ChartBarIcon,
  FolderIcon,
  InboxArrowDownIcon,
  CpuChipIcon,
  UserGroupIcon,
  DocumentTextIcon,
  CircleStackIcon,
  FolderOpenIcon,
  KeyIcon,
  MagnifyingGlassIcon,
  RectangleGroupIcon,
  Square3Stack3DIcon,
  ShieldCheckIcon,
  ShieldExclamationIcon,
  UsersIcon,
  PuzzlePieceIcon,
  ClipboardDocumentCheckIcon,
  PlusIcon,
} from '@heroicons/vue/24/outline'
import { SButton } from '@shared/ui'
import { useSessionStore } from '@shared/stores/session'
import { useWorkspaceStore } from '@shared/stores/workspace'
import { useBreakpoint } from '@shared/composables/useBreakpoint'
import { useProjectRole } from '@slices/tenancy'
import {
  useChatroomCreate,
  ChatroomCreateModal,
  convKeys,
  listWorkspaces,
} from '@slices/conversation'
import { useQuery } from '@tanstack/vue-query'
import SidebarChatroomList from './SidebarChatroomList.vue'
import SidebarGroup from './SidebarGroup.vue'
import SidebarNavItem from './SidebarNavItem.vue'
import SidebarSectionHeader from './SidebarSectionHeader.vue'
import OrgProjectSwitcher from './OrgProjectSwitcher.vue'

const { t } = useI18n()
const session = useSessionStore()
const workspace = useWorkspaceStore()
const { isDesktop } = useBreakpoint()

const { decided, isAuthorized } = useProjectRole(() => workspace.projectId ?? undefined)

interface NavItem {
  icon: typeof BuildingOffice2Icon
  label: string
  route: string
  exact?: boolean
}

const workspacesQuery = useQuery({
  queryKey: computed(() => convKeys.workspaces(workspace.projectId ?? '')),
  queryFn: () => listWorkspaces(workspace.projectId!),
  enabled: computed(() => !!workspace.projectId),
  staleTime: 60_000,
})

const workspaces = computed(() => workspacesQuery.data.value ?? [])

watch(
  workspaces,
  (list) => {
    const first = list[0]
    if (!first) return
    if (workspace.workspaceId && list.some(ws => ws.id === workspace.workspaceId)) return
    workspace.selectWorkspace(first.id, first.name)
  },
  { immediate: true },
)

function onWorkspaceSwitch(event: Event): void {
  const id = (event.target as HTMLSelectElement).value
  const ws = workspaces.value.find(w => w.id === id)
  if (ws) workspace.selectWorkspace(ws.id, ws.name)
}

const {
  showCreate,
  createName,
  createError,
  createFlags,
  openCreate,
  submitCreate,
  isCreating,
} = useChatroomCreate(() => workspace.workspaceId)

// ---- Nav item arrays -------------------------------------------------------

const globalNav = computed<NavItem[]>(() => [
  { icon: BuildingOffice2Icon, label: t('app.sidebar.orgs'), route: '/orgs' },
  { icon: FolderIcon, label: t('app.sidebar.projects'), route: '/projects', exact: true },
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

const knowledgeSettingsNav = computed<NavItem[]>(() => {
  const pid = workspace.projectId
  if (!pid) return []
  return [
    { icon: DocumentTextIcon, label: t('app.sidebar.ragConfigs'), route: `/projects/${pid}/rag-configs` },
    { icon: CircleStackIcon, label: t('app.sidebar.conceptMaps'), route: `/projects/${pid}/graphrag-configs` },
    { icon: FolderOpenIcon, label: t('app.sidebar.knowledgeMaps'), route: `/projects/${pid}/knowmap-configs` },
  ]
})

const keysSettingsNav = computed<NavItem[]>(() => {
  const pid = workspace.projectId
  if (!pid) return []
  return [
    { icon: KeyIcon, label: t('app.sidebar.projectKeys'), route: `/projects/${pid}/keys` },
    { icon: RectangleGroupIcon, label: t('app.sidebar.keyGroups'), route: `/projects/${pid}/key-groups` },
    { icon: MagnifyingGlassIcon, label: t('app.sidebar.searchKeys'), route: `/projects/${pid}/search-keys` },
  ]
})

const manageSettingsNav = computed<NavItem[]>(() => {
  const pid = workspace.projectId
  if (!pid) return []
  return [
    { icon: UsersIcon, label: t('app.sidebar.members'), route: `/projects/${pid}/members` },
    { icon: PuzzlePieceIcon, label: t('app.sidebar.skills'), route: `/projects/${pid}/skills` },
    { icon: ClipboardDocumentCheckIcon, label: t('app.sidebar.activityTypes'), route: `/projects/${pid}/activity-types` },
    { icon: ShieldCheckIcon, label: t('app.sidebar.mcpAllowlist'), route: `/projects/${pid}/mcp/egress-allowlist` },
  ]
})
</script>

<template>
  <aside
    v-if="session.isAuthenticated"
    class="sidebar"
  >
    <nav
      class="sidebar__nav"
      :aria-label="t('app.sidebar.navLabel')"
    >
      <!-- Org/project switcher -- desktop only -->
      <div
        v-if="isDesktop"
        class="sidebar__switcher"
      >
        <OrgProjectSwitcher compact />
      </div>

      <!-- Global -->
      <div class="sidebar__section">
        <SidebarNavItem
          v-for="item in globalNav"
          :key="item.route"
          :icon="item.icon"
          :label="item.label"
          :to="item.route"
          :exact="!!item.exact"
        />
      </div>

      <!-- Project context -->
      <template v-if="workspace.hasProject">
        <div class="sidebar__divider" />

        <!-- Workspace switcher (multi-workspace projects only) -->
        <div
          v-if="workspaces.length > 1"
          class="sidebar__section sidebar__section--switcher"
        >
          <select
            :value="workspace.workspaceId ?? ''"
            :aria-label="t('app.sidebar.workspaceSwitcher')"
            class="workspace-select"
            @change="onWorkspaceSwitch($event)"
          >
            <option
              v-for="ws in workspaces"
              :key="ws.id"
              :value="ws.id"
            >
              {{ ws.name }}
            </option>
          </select>
        </div>

        <!-- New Chat CTA -->
        <div class="sidebar__section sidebar__section--cta">
          <SButton
            variant="primary"
            size="sm"
            class="new-chat-btn"
            :disabled="!workspace.workspaceId"
            @click="openCreate"
          >
            <template #icon-left>
              <PlusIcon class="w-4 h-4" />
            </template>
            {{ t('app.sidebar.newChat') }}
          </SButton>
        </div>

        <!-- Recent Chatrooms -->
        <SidebarChatroomList />

        <div class="sidebar__divider" />

        <!-- Dashboard, Agents & Workspaces -->
        <div class="sidebar__section">
          <SidebarNavItem
            v-if="workspace.workspaceId"
            :icon="ChartBarIcon"
            :label="t('app.sidebar.dashboard')"
            :to="`/workspaces/${workspace.workspaceId}/dashboard`"
          />
          <SidebarNavItem
            v-for="item in agentNav"
            :key="item.route"
            :icon="item.icon"
            :label="item.label"
            :to="item.route"
          />
          <SidebarNavItem
            :icon="Square3Stack3DIcon"
            :label="t('app.sidebar.workspaces')"
            :to="`/projects/${workspace.projectId}/workspaces`"
          />
        </div>

        <div class="sidebar__divider" />

        <!-- Project Settings (single collapsible group) -->
        <SidebarGroup
          :label="t('app.sidebar.groupProjectSettings')"
          storage-key="project-settings"
          :default-collapsed="true"
        >
          <!-- Knowledge sub-section -->
          <SidebarSectionHeader :label="t('app.sidebar.sectionKnowledge')" />
          <SidebarNavItem
            v-for="item in knowledgeSettingsNav"
            :key="item.route"
            :icon="item.icon"
            :label="item.label"
            :to="item.route"
          />

          <!-- Keys sub-section -->
          <SidebarSectionHeader :label="t('app.sidebar.sectionKeys')" />
          <SidebarNavItem
            v-for="item in keysSettingsNav"
            :key="item.route"
            :icon="item.icon"
            :label="item.label"
            :to="item.route"
          />

          <!-- Manage sub-section (admin-gated) -->
          <template v-if="decided && isAuthorized">
            <SidebarSectionHeader :label="t('app.sidebar.sectionManage')" />
            <SidebarNavItem
              v-for="item in manageSettingsNav"
              :key="item.route"
              :icon="item.icon"
              :label="item.label"
              :to="item.route"
            />
          </template>
        </SidebarGroup>
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

    <!-- Chatroom creation modal -->
    <ChatroomCreateModal
      :open="showCreate"
      :name="createName"
      :error="createError"
      :flags="createFlags"
      :is-pending="isCreating"
      @close="showCreate = false"
      @submit="submitCreate"
      @update:name="createName = $event"
      @update:flags="(key: string, val: boolean) => (createFlags as Record<string, boolean>)[key] = val"
    />
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

@media (max-width: 1023px) {
  .sidebar {
    max-width: 100%;
  }
}

.sidebar__nav {
  display: flex;
  flex-direction: column;
  padding: var(--space-2) 0;
}

.sidebar__switcher {
  padding: var(--space-1) var(--space-3) var(--space-2);
}

.sidebar__section {
  display: flex;
  flex-direction: column;
}

.sidebar__section--cta {
  padding: var(--space-2) var(--space-3);
}

.sidebar__section--switcher {
  padding: var(--space-1) var(--space-3);
}

.workspace-select {
  width: 100%;
  font-size: var(--font-size-xs);
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-sidebar-bg);
  color: var(--color-text);
  cursor: pointer;
  outline: none;
  transition: border-color var(--transition-fast);
}

.workspace-select:hover {
  border-color: var(--color-accent);
}

.workspace-select:focus-visible {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 1px var(--color-accent);
}

.new-chat-btn {
  width: 100%;
}

.sidebar__divider {
  height: 1px;
  background-color: var(--color-border-subtle);
  margin: var(--space-2) var(--space-4);
}

</style>
