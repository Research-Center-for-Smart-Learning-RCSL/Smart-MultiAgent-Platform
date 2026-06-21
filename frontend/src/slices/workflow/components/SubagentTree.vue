<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { listSubagents } from '../api'
import type { AgentInstance } from '../types'

const { t } = useI18n()

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
    case 'running': return 'text-accent'
    case 'completed': return 'text-success'
    case 'error': return 'text-danger'
    default: return 'text-muted'
  }
}

function agentName(id: string): string {
  return props.agentNames[id] ?? id.slice(0, 8)
}
</script>

<template>
  <div class="subagent-tree ml-4 border-l-2 border-border pl-3 my-2">
    <div class="text-xs text-muted mb-1 font-medium">
      {{ t('workflow.subagent.title') }}
    </div>

    <div
      v-if="loading"
      class="text-xs text-muted"
    >
      {{ t('workflow.subagent.loading') }}
    </div>
    <div
      v-else-if="children.length === 0"
      class="text-xs text-muted"
    >
      {{ t('workflow.subagent.empty') }}
    </div>

    <div
      v-for="child in children"
      :key="child.id"
      class="flex items-start gap-2 py-1 text-sm"
    >
      <span
        :class="stateClass(child.state)"
        class="text-xs font-mono mt-0.5"
      >
        [{{ stateIcon(child.state) }}]
      </span>
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-1">
          <span class="font-mono text-xs">{{ agentName(child.agent_id) }}</span>
          <span class="text-xs text-muted">{{ child.id.slice(0, 8) }}</span>
        </div>
        <div
          v-if="child.task_description"
          class="text-xs text-muted truncate"
        >
          {{ child.task_description }}
        </div>
        <div class="text-xs text-muted">
          {{ t('workflow.subagent.spawned', { time: child.spawned_at }) }}
          <template v-if="child.destroyed_at">
            &middot; {{ t('workflow.subagent.destroyed', { time: child.destroyed_at }) }}
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
