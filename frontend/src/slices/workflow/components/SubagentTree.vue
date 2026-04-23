<script setup lang="ts">
// Sub-agent tree — nested message thread under parent invocation (G.10).
// Renders a parent agent instance and its sub-agent children as a tree
// with task descriptions and state indicators.

import { ref, watch } from 'vue'
import { listSubagents } from '../api'
import type { AgentInstance } from '../types'

const props = defineProps<{
  parentInstanceId: string
  agentNames: Record<string, string>
}>()

const children = ref<AgentInstance[]>([])
const loading = ref(false)

async function refresh(): Promise<void> {
  loading.value = true
  try {
    children.value = await listSubagents(props.parentInstanceId)
  } catch {
    children.value = []
  } finally {
    loading.value = false
  }
}

watch(() => props.parentInstanceId, () => void refresh(), { immediate: true })

function stateIcon(state: string): string {
  switch (state) {
    case 'running': return '...'
    case 'completed': return 'done'
    case 'error': return 'err'
    default: return state
  }
}

function stateClass(state: string): string {
  switch (state) {
    case 'running': return 'text-blue-600'
    case 'completed': return 'text-green-600'
    case 'error': return 'text-red-600'
    default: return 'text-gray-500'
  }
}

function agentName(id: string): string {
  return props.agentNames[id] ?? id.slice(0, 8)
}
</script>

<template>
  <div class="subagent-tree ml-4 border-l-2 border-gray-200 pl-3 my-2">
    <div class="text-xs text-gray-500 mb-1 font-medium">Sub-agents</div>

    <div v-if="loading" class="text-xs text-gray-400">Loading...</div>
    <div v-else-if="children.length === 0" class="text-xs text-gray-400">
      No sub-agents spawned.
    </div>

    <div
      v-for="child in children"
      :key="child.id"
      class="flex items-start gap-2 py-1 text-sm"
    >
      <span :class="stateClass(child.state)" class="text-xs font-mono mt-0.5">
        [{{ stateIcon(child.state) }}]
      </span>
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-1">
          <span class="font-mono text-xs">{{ agentName(child.agent_id) }}</span>
          <span class="text-xs text-gray-400">{{ child.id.slice(0, 8) }}</span>
        </div>
        <div
          v-if="child.task_description"
          class="text-xs text-gray-600 truncate"
        >
          {{ child.task_description }}
        </div>
        <div class="text-xs text-gray-400">
          Spawned {{ child.spawned_at }}
          <template v-if="child.destroyed_at">
            &middot; Destroyed {{ child.destroyed_at }}
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
