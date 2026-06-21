<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import { computed } from 'vue'
import { useWorkflowStore } from '../stores/workflow'

const props = defineProps<{
  id: string
  data: {
    label: string
    nodeType: string
    config: Record<string, unknown>
  }
}>()

const store = useWorkflowStore()

const isSelected = computed(() => store.selectedNodeId === props.id)

const stepState = computed(() => store.liveSteps[props.id]?.state)

/* ---------- left border colour per node type ---------- */
const borderColorClass = computed(() => {
  const map: Record<string, string> = {
    trigger: 'border-l-purple-400',
    agent_invocation: 'border-l-blue-400',
    approval_gate: 'border-l-orange-400',
    condition: 'border-l-amber-400',
    instruct: 'border-l-cyan-400',
    subagent_spawn: 'border-l-emerald-400',
    wait_for_event: 'border-l-rose-400',
    parallel: 'border-l-teal-400',
    join: 'border-l-teal-400',
    set_variable: 'border-l-indigo-400',
    end: 'border-l-gray-500',
  }
  return map[props.data.nodeType] ?? 'border-l-gray-300'
})

/* ---------- live-step state background ---------- */
const stateBgClass = computed(() => {
  switch (stepState.value) {
    case 'running':
      return 'wf-node--running'
    case 'succeeded':
      return 'wf-node--succeeded'
    case 'failed':
      return 'wf-node--failed'
    default:
      return ''
  }
})

/* ---------- source port list per node type ---------- */
const sourcePorts = computed<string[]>(() => {
  switch (props.data.nodeType) {
    case 'trigger':
    case 'parallel':
    case 'set_variable':
      return ['default']

    case 'agent_invocation':
    case 'instruct':
    case 'subagent_spawn':
      return ['success', 'failure']

    case 'approval_gate':
      return ['approved', 'rejected', 'timeout']

    case 'condition': {
      const cfg = props.data.config
      const ports: string[] = []
      if (Array.isArray(cfg.branches)) {
        for (const branch of cfg.branches) {
          const name =
            typeof branch === 'string'
              ? branch
              : typeof branch === 'object' && branch !== null && typeof (branch as Record<string, unknown>).port === 'string'
                ? (branch as Record<string, unknown>).port as string
                : null
          if (name && !ports.includes(name)) ports.push(name)
        }
      }
      const defaultPort =
        typeof cfg.default_port === 'string' ? cfg.default_port : 'default'
      if (!ports.includes(defaultPort)) ports.push(defaultPort)
      return ports
    }

    case 'wait_for_event':
    case 'join':
      return ['default', 'timeout']

    case 'end':
      return []

    default:
      return ['default']
  }
})

/* ---------- horizontal position for multiple source handles ---------- */
function handleLeftPercent(index: number, total: number): string {
  return `${((index + 1) / (total + 1)) * 100}%`
}
</script>

<template>
  <!-- Target handle — every type except trigger -->
  <Handle
    v-if="data.nodeType !== 'trigger'"
    type="target"
    :position="Position.Top"
  />

  <!-- Node body -->
  <div
    class="wf-node rounded border-l-4 px-3 py-2 text-xs min-w-[140px]"
    :class="[
      borderColorClass,
      stateBgClass,
      isSelected ? 'ring-2 ring-accent' : '',
    ]"
  >
    <div class="font-semibold leading-tight truncate max-w-[180px]">
      {{ data.label || id }}
    </div>
    <div
      class="text-[11px] mt-0.5 text-muted"
    >
      {{ data.nodeType }}
    </div>
  </div>

  <!-- Source handles -->
  <div
    v-if="sourcePorts.length"
    class="wf-node__ports"
  >
    <Handle
      v-for="(port, idx) in sourcePorts"
      :id="port"
      :key="port"
      type="source"
      :position="Position.Bottom"
      :style="{
        left: handleLeftPercent(idx, sourcePorts.length),
      }"
    />
    <!-- Port labels -->
    <span
      v-for="(port, idx) in sourcePorts"
      :key="'lbl-' + port"
      class="wf-node__port-label"
      :style="{
        left: handleLeftPercent(idx, sourcePorts.length),
      }"
    >
      {{ port }}
    </span>
  </div>
</template>

<style scoped>
.wf-node {
  cursor: pointer;
  background: var(--color-bg);
  color: var(--color-fg);
  border-color: var(--color-border);
  transition: box-shadow 0.15s ease;
  position: relative;
}

.wf-node:hover {
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.3);
}

/* Live-step state backgrounds — semi-transparent so left border shows through */
.wf-node--running {
  background: color-mix(in srgb, #3b82f6 12%, var(--color-bg));
}

.wf-node--succeeded {
  background: color-mix(in srgb, #22c55e 12%, var(--color-bg));
}

.wf-node--failed {
  background: color-mix(in srgb, #ef4444 12%, var(--color-bg));
}

/* Source-handle port area */
.wf-node__ports {
  position: relative;
  height: 16px;
}

.wf-node__port-label {
  position: absolute;
  top: 10px;
  transform: translateX(-50%);
  font-size: 0.5625rem;
  color: var(--color-muted);
  white-space: nowrap;
  pointer-events: none;
}

/* VueFlow Handle overrides */
:deep(.vue-flow__handle) {
  width: 8px;
  height: 8px;
  border: 2px solid var(--color-accent);
  background: var(--color-bg);
  border-radius: 50%;
}
</style>
