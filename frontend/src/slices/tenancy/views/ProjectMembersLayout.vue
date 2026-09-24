<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery } from '@tanstack/vue-query'
import { SPageHeader } from '@shared/ui'
import { projectsApi } from '../api/projects'
import { tenancyKeys } from '../queries'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const projectId = computed(() => route.params.id as string)

const { data: project } = useQuery({
  queryKey: computed(() => tenancyKeys.project(projectId.value)),
  queryFn: () => projectsApi.get(projectId.value),
})

const breadcrumbs = computed(() => [
  { label: t('tenancy.breadcrumb.home'), to: { name: 'tenancy.orgList' } },
  { label: t('tenancy.breadcrumb.projects'), to: { name: 'tenancy.projectList' } },
  {
    label: project.value?.name ?? '...',
    to: { name: 'tenancy.projectDetail', params: { id: projectId.value } },
  },
  { label: t('tenancy.breadcrumb.members') },
])

const tabs = computed(() => [
  {
    key: 'members',
    label: t('tenancy.member.tabMembers'),
    route: { name: 'tenancy.projectMembers', params: { id: projectId.value } },
  },
  {
    key: 'groups',
    label: t('tenancy.member.tabGroups'),
    route: { name: 'tenancy.projectMemberGroups', params: { id: projectId.value } },
  },
])

const activeTab = computed(() =>
  route.name === 'tenancy.projectMemberGroups' ? 'groups' : 'members',
)

function onTab(key: string) {
  const tab = tabs.value.find(item => item.key === key)
  if (tab) router.push(tab.route)
}
</script>

<template>
  <div>
    <SPageHeader
      :title="t('tenancy.breadcrumb.members')"
      :breadcrumbs="breadcrumbs"
    />

    <div class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab"
        :class="{ 'tab--active': activeTab === tab.key }"
        @click="onTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </div>

    <RouterView />
  </div>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: var(--space-1);
  border-bottom: 1px solid var(--color-border);
  margin-bottom: var(--space-4);
}

.tab {
  padding: var(--space-2) var(--space-4);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  font-size: var(--font-size-sm);
  font-weight: var(--weight-medium);
  color: var(--color-muted);
  cursor: pointer;
  transition:
    color var(--transition-fast),
    border-color var(--transition-fast);
}

.tab:hover {
  color: var(--color-fg);
}

.tab--active {
  color: var(--color-accent);
  border-bottom-color: var(--color-accent);
}
</style>
