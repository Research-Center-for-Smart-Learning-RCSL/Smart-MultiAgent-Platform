<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery } from '@tanstack/vue-query'
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MagnifyingGlassIcon, ShareIcon } from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  SInput,
  SAlert,
  SLoadingSpinner,
  SEmptyState,
} from '@shared/ui'
import { agentsApi } from '../api'
import { agentKeys } from '../queries'
import { useProjectBreadcrumbs } from '../composables/useProjectBreadcrumbs'
import { computeGraphLayout } from '../composables/useGraphLayout'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'

const { t } = useI18n()
const route = useRoute()
const projectId = route.params.projectId as string
const configId = route.params.configId as string

// Entity-category palette (audit L1). Matches the backend ENTITY_TYPES set;
// unknown/'' falls back to neutral. Chosen to stay legible in light + dark.
const TYPE_COLORS: Record<string, string> = {
  person: '#3b82f6',
  organization: '#8b5cf6',
  location: '#10b981',
  concept: '#f59e0b',
  event: '#ef4444',
  product: '#14b8a6',
  other: '#94a3b8',
}
const UNKNOWN_COLOR = '#94a3b8'
const colorFor = (type: string): string => TYPE_COLORS[type] ?? UNKNOWN_COLOR

const { breadcrumbs } = useProjectBreadcrumbs(projectId, [
  { label: t('agents.breadcrumb.graphrag'), to: { name: 'agents.graphragConfigs', params: { projectId } } },
  { label: t('agents.graphragGraph.title') },
])

const graphQuery = useQuery({
  queryKey: agentKeys.graphragGraph(configId),
  queryFn: async () => (await agentsApi.getGraphragGraph(configId)).data,
})

const search = ref('')

// Layout depends only on the topology — compute it once per fetched graph so
// typing in the search box doesn't reshuffle the diagram.
const positions = computed(() => {
  const g = graphQuery.data.value
  if (!g) return new Map<string, { x: number; y: number }>()
  const placed = computeGraphLayout(g.nodes, g.edges)
  return new Map(placed.map((p) => [p.id, { x: p.x, y: p.y }]))
})

const nodeCount = computed(() => graphQuery.data.value?.nodes.length ?? 0)
const edgeCount = computed(() => graphQuery.data.value?.edges.length ?? 0)
const isEmpty = computed(() => !graphQuery.isLoading.value && nodeCount.value === 0)

const matches = (id: string): boolean => {
  const q = search.value.trim().toLowerCase()
  return q === '' || id.toLowerCase().includes(q)
}

const flowNodes = computed(() => {
  const g = graphQuery.data.value
  if (!g) return []
  return g.nodes.map((node) => {
    const p = positions.value.get(node.id) ?? { x: 0, y: 0 }
    const size = Math.min(28 + node.degree * 4, 72)
    const dim = search.value.trim() !== '' && !matches(node.id)
    const color = colorFor(node.type)
    return {
      id: node.id,
      position: p,
      data: { label: node.id },
      type: 'default',
      style: {
        width: `${size}px`,
        height: `${size}px`,
        fontSize: '10px',
        borderRadius: '9999px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        textAlign: 'center',
        opacity: dim ? 0.2 : 1,
        background: `${color}33`,
        borderColor: color,
        color: 'var(--color-text)',
      },
    }
  })
})

// Distinct entity categories actually present, for the legend.
const legendTypes = computed(() => {
  const g = graphQuery.data.value
  if (!g) return []
  const present = new Set<string>()
  for (const n of g.nodes) present.add(n.type || 'other')
  return [...present].sort()
})

const flowEdges = computed(() => {
  const g = graphQuery.data.value
  if (!g) return []
  return g.edges.map((edge, i) => ({
    id: `e${i}`,
    source: edge.source,
    target: edge.target,
    label: edge.relation,
    animated: false,
    style: { stroke: 'var(--color-border)' },
    labelStyle: { fontSize: '9px', fill: 'var(--color-muted)' },
  }))
})
</script>

<template>
  <main class="p-6 flex flex-col h-[calc(100vh-3.5rem)]">
    <SPageHeader
      :title="t('agents.graphragGraph.title')"
      :breadcrumbs="breadcrumbs"
    >
      <template #actions>
        <div class="w-64">
          <SInput
            v-model="search"
            :placeholder="t('agents.graphragGraph.searchPlaceholder')"
            :aria-label="t('agents.graphragGraph.searchPlaceholder')"
          >
            <template #icon-left>
              <MagnifyingGlassIcon class="w-4 h-4" />
            </template>
          </SInput>
        </div>
      </template>
    </SPageHeader>

    <p class="mt-2 text-sm text-[var(--color-muted)]">
      {{ t('agents.graphragGraph.summary', { nodes: nodeCount, edges: edgeCount }) }}
    </p>

    <SAlert
      v-if="graphQuery.data.value?.truncated"
      variant="warning"
      class="mt-3"
    >
      {{ t('agents.graphragGraph.truncated') }}
    </SAlert>

    <div
      v-if="graphQuery.isLoading.value"
      class="flex-1 flex items-center justify-center"
    >
      <SLoadingSpinner />
    </div>

    <SAlert
      v-else-if="graphQuery.isError.value"
      variant="danger"
      class="mt-4"
    >
      {{ t('agents.graphragGraph.loadFailed') }}
    </SAlert>

    <SEmptyState
      v-else-if="isEmpty"
      :icon="ShareIcon"
      :title="t('agents.graphragGraph.emptyTitle')"
      :text="t('agents.graphragGraph.emptyDescription')"
      class="flex-1"
    />

    <div
      v-else
      class="flex-1 mt-4 rounded-lg border border-[var(--color-border)] overflow-hidden"
    >
      <VueFlow
        :nodes="flowNodes"
        :edges="flowEdges"
        :min-zoom="0.1"
        :max-zoom="2"
        fit-view-on-init
      >
        <Background />
        <Controls />
      </VueFlow>
    </div>

    <div
      v-if="!graphQuery.isLoading.value && nodeCount > 0"
      class="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-[var(--color-muted)]"
    >
      <span
        v-for="ty in legendTypes"
        :key="ty"
        class="flex items-center gap-1.5"
      >
        <span
          class="inline-block w-3 h-3 rounded-full border"
          :style="{ background: `${colorFor(ty)}33`, borderColor: colorFor(ty) }"
        />
        {{ t(`agents.graphragGraph.types.${ty}`) }}
      </span>
      <span class="opacity-80">{{ t('agents.graphragGraph.legendHint') }}</span>
    </div>
  </main>
</template>
