<script setup lang="ts">
// Instruct chain backstage — Admin-only per R14.10 (not surfaced in chat UI).
// Renders in slices/workflow/WorkflowRunView.

import { ref, watch } from 'vue'
import { listInstructionsForChain } from '../api'
import type { Instruction } from '../types'

const props = defineProps<{
  chainId: string
  agentNames: Record<string, string>
}>()

const steps = ref<Instruction[]>([])
const loading = ref(false)

async function refresh(): Promise<void> {
  loading.value = true
  try {
    steps.value = await listInstructionsForChain(props.chainId)
  } catch {
    steps.value = []
  } finally {
    loading.value = false
  }
}

watch(() => props.chainId, () => void refresh(), { immediate: true })

function agentName(id: string): string {
  return props.agentNames[id] ?? id.slice(0, 8)
}

function stateClass(state: string): string {
  switch (state) {
    case 'completed': return 'text-green-600'
    case 'rejected_loop': return 'text-red-600'
    case 'timeout': return 'text-orange-600'
    case 'delivered': return 'text-blue-600'
    default: return 'text-gray-500'
  }
}
</script>

<template>
  <div class="instruct-chain-view space-y-1">
    <div class="text-xs font-medium text-gray-500 mb-1">
      Instruct Chain {{ chainId.slice(0, 8) }}
    </div>

    <div v-if="loading" class="text-xs text-gray-400">Loading...</div>
    <div v-else-if="steps.length === 0" class="text-xs text-gray-400">
      No instructions in chain.
    </div>

    <div
      v-for="(step, idx) in steps"
      :key="step.id"
      class="flex items-start gap-2 text-xs border-l-2 border-gray-200 pl-2 py-1"
      :style="{ marginLeft: `${step.depth * 12}px` }"
    >
      <span class="text-gray-400 font-mono w-4">{{ idx + 1 }}</span>
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-1">
          <span class="font-mono">{{ agentName(step.issuer_agent_id) }}</span>
          <span class="text-gray-400">&rarr;</span>
          <span class="font-mono">{{ agentName(step.target_agent_id) }}</span>
          <span :class="stateClass(step.state)" class="font-medium ml-1">
            {{ step.state }}
          </span>
        </div>
        <div class="text-gray-400">
          depth {{ step.depth }} &middot; {{ step.issued_at }}
        </div>
      </div>
    </div>
  </div>
</template>
