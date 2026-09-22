<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  BuildingOffice2Icon,
  FolderIcon,
  InboxArrowDownIcon,
  CpuChipIcon,
  UserGroupIcon,
  DocumentTextIcon,
  CircleStackIcon,
  FolderOpenIcon,
  KeyIcon,
  MagnifyingGlassIcon,
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
  listWorkspaces,
} from '@slices/conversation'
import { useQuery } from '@tanstack/vue-query'
import SidebarChatroomList from './SidebarChatroomList.vue'
import SidebarGroup from './SidebarGroup.vue'
import SidebarNavItem from './SidebarNavItem.vue'
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

// Resolve the first workspace for the current project so the New Chat button
// has a target. Most projects have a single workspace; multi-workspace projects
// get the first one (the user navigates to the full list to pick another).
const workspacesQuery = useQuery({
  queryKey: computed(() => ['workspaces', workspace.projectId ?? '']),
  queryFn: () => listWorkspaces(workspace.projectId!),
  enabled: computed(() => !!workspace.projectId),
  staleTime: 60_000,
})

const defaultWorkspaceId = computed<string | null>(
  () => workspacesQuery.data.value?.[0]?.id ?? null,
)

const {
  showCreate,
  createName,
  createError,
  createFlags,
  openCreate,
  submitCreate,
  isCreating,
} = useChatroomCreate(defaultWorkspaceId)

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

      <!-- Project context -->
      <template v-if="workspace.hasProject">
        <!-- New Chat CTA -->
        <div class="sidebar__section sidebar__section--cta">
          <SButton
            variant="primary"
            size="sm"
            class="new-chat-btn"
            :disabled="!defaultWorkspaceId"
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

        <!-- Workspaces -->
        <div class="sidebar__section">
          <SidebarNavItem
            :icon="Square3Stack3DIcon"
            :label="t('app.sidebar.workspaces')"
            :to="`/projects/${workspace.projectId}/workspaces`"
          />
        </div>

        <div class="sidebar__divider" />

        <!-- Agents -->
        <div class="sidebar__section">
          <SidebarNavItem
            v-for="item in agentNav"
            :key="item.route"
            :icon="item.icon"
            :label="item.label"
            :to="item.route"
          />
        </div>

        <div class="sidebar__divider" />

        <!-- Project Settings (single collapsible group) -->
        <SidebarGroup
          :label="t('app.sidebar.groupProjectSettings')"
          storage-key="project-settings"
        >
          <!-- Knowledge sub-section -->
          <div class="section-header">
            {{ t('app.sidebar.sectionKnowledge') }}
          </div>
          <SidebarNavItem
            v-for="item in knowledgeSettingsNav"
            :key="item.route"
            :icon="item.icon"
            :label="item.label"
            :to="item.route"
          />

          <!-- Keys sub-section -->
          <div class="section-header">
            {{ t('app.sidebar.sectionKeys') }}
          </div>
          <SidebarNavItem
            v-for="item in keysSettingsNav"
            :key="item.route"
            :icon="item.icon"
            :label="item.label"
            :to="item.route"
          />

          <!-- Manage sub-section (admin-gated) -->
          <template v-if="decided && isAuthorized">
            <div class="section-header">
              {{ t('app.sidebar.sectionManage') }}
            </div>
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

      <div class="sidebar__divider" />

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

.new-chat-btn {
  width: 100%;
}

.sidebar__divider {
  height: 1px;
  background-color: var(--color-border-subtle);
  margin: var(--space-2) var(--space-4);
}

.section-header {
  text-transform: uppercase;
  font-size: 11px;
  font-weight: var(--weight-semibold);
  color: var(--color-sidebar-section-text);
  padding: var(--space-4) var(--space-4) var(--space-2);
  letter-spacing: 0.05em;
}
</style>
