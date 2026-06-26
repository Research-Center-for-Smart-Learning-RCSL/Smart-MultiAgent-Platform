<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery } from '@tanstack/vue-query'
import {
  ChevronDownIcon,
  CheckIcon,
  PlusIcon,
} from '@heroicons/vue/24/outline'
import { SAvatar } from '@shared/ui'
import { useWorkspaceStore } from '@shared/stores/workspace'
import { tenancyKeys, orgsApi, projectsApi, type Org, type Project } from '@slices/tenancy'

defineProps<{
  compact?: boolean
}>()

const { t } = useI18n()
const router = useRouter()
const workspace = useWorkspaceStore()

const isOpen = ref(false)
const panelRef = ref<HTMLElement | null>(null)
const triggerRef = ref<HTMLElement | null>(null)

const orgsQuery = useQuery({
  queryKey: tenancyKeys.orgs(),
  queryFn: () => orgsApi.list().then((r) => r.data),
})

const orgs = computed(() => orgsQuery.data.value ?? [])

const projectsEnabled = computed(() => !!workspace.orgId)

const projectsQuery = useQuery({
  queryKey: computed(() =>
    tenancyKeys.projects('org', workspace.orgId ?? ''),
  ),
  queryFn: () =>
    projectsApi.list('org', workspace.orgId!).then((r) => r.data),
  enabled: projectsEnabled,
})

const projects = computed(() => projectsQuery.data.value ?? [])

const displayText = computed(() => {
  if (!workspace.hasOrg) return ''
  if (!workspace.hasProject) return workspace.orgName
  return `${workspace.orgName} / ${workspace.projectName}`
})

function toggle() {
  isOpen.value = !isOpen.value
}

function close() {
  isOpen.value = false
}

function selectOrg(org: Org) {
  workspace.selectOrg(org.id, org.name)
}

function selectProject(project: Project) {
  workspace.selectProject(project.id, project.name)
  close()
}

function goCreateOrg() {
  close()
  router.push('/orgs')
}

function goCreateProject() {
  close()
  router.push({
    name: 'tenancy.projectList',
    query: {
      ...(workspace.orgId ? { scope: workspace.orgId } : {}),
      create: '1',
    },
  })
}

function onClickOutside(e: MouseEvent) {
  const target = e.target as Node
  if (
    triggerRef.value &&
    !triggerRef.value.contains(target) &&
    panelRef.value &&
    !panelRef.value.contains(target)
  ) {
    close()
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    close()
    triggerRef.value?.focus()
  }
}

watch(isOpen, async (open) => {
  if (open) {
    document.addEventListener('click', onClickOutside, { capture: true })
    await nextTick()
    panelRef.value?.focus()
  } else {
    document.removeEventListener('click', onClickOutside, { capture: true })
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onClickOutside, { capture: true })
})
</script>

<template>
  <div
    class="switcher"
    :class="{ 'switcher--compact': compact }"
    role="none"
    @keydown="onKeydown"
  >
    <button
      ref="triggerRef"
      class="switcher__trigger"
      :class="{
        'switcher__trigger--empty': !workspace.hasOrg,
        'switcher__trigger--compact': compact,
      }"
      type="button"
      :aria-expanded="isOpen"
      :aria-label="t('app.switcher.placeholder')"
      @click.stop="toggle"
    >
      <template v-if="compact">
        <SAvatar
          :name="workspace.orgName || '?'"
          size="sm"
        />
      </template>
      <template v-else>
        <span class="switcher__text">
          {{ workspace.hasOrg ? displayText : t('app.switcher.placeholder') }}
        </span>
        <ChevronDownIcon
          class="switcher__chevron"
          :class="{ 'switcher__chevron--open': isOpen }"
        />
      </template>
    </button>

    <Transition name="switcher-panel">
      <div
        v-if="isOpen"
        ref="panelRef"
        class="switcher__panel"
        tabindex="-1"
      >
        <!-- Organizations section -->
        <div class="switcher__section-header">
          {{ t('app.switcher.orgs') }}
        </div>
        <div
          v-if="orgsQuery.isError.value"
          class="switcher__error"
        >
          {{ t('app.switcher.loadError') }}
        </div>
        <ul
          v-else
          class="switcher__list"
          role="listbox"
        >
          <li
            v-for="org in orgs"
            :key="org.id"
            class="switcher__item"
            :class="{ 'switcher__item--active': org.id === workspace.orgId }"
            role="option"
            tabindex="0"
            :aria-selected="org.id === workspace.orgId"
            @click="selectOrg(org)"
            @keydown.enter="selectOrg(org)"
            @keydown.space.prevent="selectOrg(org)"
          >
            <span class="switcher__item-label">{{ org.name }}</span>
            <CheckIcon
              v-if="org.id === workspace.orgId"
              class="switcher__check"
            />
          </li>
        </ul>
        <button
          class="switcher__action"
          type="button"
          @click="goCreateOrg"
        >
          <PlusIcon class="switcher__action-icon" />
          {{ t('app.switcher.createOrg') }}
        </button>

        <!-- Projects section (shown when org selected) -->
        <template v-if="workspace.hasOrg">
          <div class="switcher__divider" />
          <div class="switcher__section-header">
            {{ t('app.switcher.projects') }}
          </div>
          <div
            v-if="projectsQuery.isError.value"
            class="switcher__error"
          >
            {{ t('app.switcher.loadError') }}
          </div>
          <ul
            v-else
            class="switcher__list"
            role="listbox"
          >
            <li
              v-for="project in projects"
              :key="project.id"
              class="switcher__item"
              :class="{
                'switcher__item--active':
                  project.id === workspace.projectId,
              }"
              role="option"
              tabindex="0"
              :aria-selected="project.id === workspace.projectId"
              @click="selectProject(project)"
              @keydown.enter="selectProject(project)"
              @keydown.space.prevent="selectProject(project)"
            >
              <span class="switcher__item-label">{{ project.name }}</span>
              <CheckIcon
                v-if="project.id === workspace.projectId"
                class="switcher__check"
              />
            </li>
          </ul>
          <button
            class="switcher__action"
            type="button"
            @click="goCreateProject"
          >
            <PlusIcon class="switcher__action-icon" />
            {{ t('app.switcher.createProject') }}
          </button>
        </template>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.switcher {
  position: relative;
  display: inline-flex;
}

.switcher__trigger {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: none;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-fg);
  font-size: 0.875rem;
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
  white-space: nowrap;
  max-width: 280px;
  transition:
    background var(--transition-fast),
    border-color var(--transition-fast);
}

.switcher__trigger:hover {
  background: var(--color-surface);
}

.switcher__trigger:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.switcher__trigger--empty {
  color: var(--color-muted);
}

.switcher__trigger--compact {
  padding: 4px;
  border: none;
  border-radius: var(--radius-full);
}

.switcher--compact .switcher__panel {
  left: auto;
  right: 0;
}

.switcher__text {
  overflow: hidden;
  text-overflow: ellipsis;
}

.switcher__chevron {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--color-muted);
  transition: transform var(--transition-fast);
}

.switcher__chevron--open {
  transform: rotate(180deg);
}

.switcher__panel {
  position: absolute;
  top: 100%;
  left: 0;
  margin-top: 4px;
  min-width: 280px;
  max-height: 400px;
  overflow-y: auto;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-dropdown);
  padding: 4px 0;
}

.switcher__panel:focus {
  outline: none;
}

.switcher__section-header {
  padding: 8px 16px 4px;
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-muted);
  user-select: none;
}

.switcher__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.switcher__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 36px;
  padding: 0 16px;
  font-size: 0.875rem;
  color: var(--color-fg);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.switcher__item:hover {
  background: var(--color-surface);
}

.switcher__item--active {
  color: var(--color-accent);
  font-weight: 500;
}

.switcher__item-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.switcher__check {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--color-accent);
}

.switcher__action {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 36px;
  padding: 0 16px;
  background: none;
  border: none;
  color: var(--color-muted);
  font-size: 0.8125rem;
  cursor: pointer;
  transition:
    background var(--transition-fast),
    color var(--transition-fast);
}

.switcher__action:hover {
  background: var(--color-surface);
  color: var(--color-fg);
}

.switcher__action-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.switcher__error {
  padding: 8px 16px;
  font-size: 0.8125rem;
  color: var(--color-danger);
}

.switcher__divider {
  height: 1px;
  margin: 4px 0;
  background: var(--color-border);
}

/* -- Enter/Leave transitions -- */
.switcher-panel-enter-active,
.switcher-panel-leave-active {
  transition:
    opacity var(--transition-fast) ease,
    transform var(--transition-fast) ease;
}

.switcher-panel-enter-from,
.switcher-panel-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
