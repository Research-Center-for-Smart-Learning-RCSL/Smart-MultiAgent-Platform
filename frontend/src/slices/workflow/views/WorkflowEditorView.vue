<template>
  <section class="workflow-editor flex flex-col h-full">
    <!-- Toolbar -->
    <header class="flex items-center gap-3 px-4 py-2 border-b bg-bg shrink-0">
      <router-link
        :to="{ name: 'workflow.list', params: { workspaceId } }"
        class="text-sm text-gray-500 hover:underline"
      >
        &larr; {{ $t('workflow.editor.backToList') }}
      </router-link>

      <h2
        v-if="workflow"
        class="font-semibold truncate max-w-[300px]"
      >
        {{ workflow.name }}
      </h2>

      <span
        v-if="store.dirty"
        class="text-xs text-orange-500"
      >
        {{ $t('workflow.editor.unsaved') }}
      </span>

      <div class="ml-auto flex items-center gap-2">
        <template v-if="isDesktop">
          <button
            class="btn btn-sm"
            :disabled="!store.canUndo"
            :title="$t('workflow.editor.undo')"
            :aria-label="$t('workflow.editor.undo')"
            @click="onUndo"
          >
            <span aria-hidden="true">↶</span>
          </button>
          <button
            class="btn btn-sm"
            :disabled="!store.canRedo"
            :title="$t('workflow.editor.redo')"
            :aria-label="$t('workflow.editor.redo')"
            @click="onRedo"
          >
            <span aria-hidden="true">↷</span>
          </button>
        </template>

        <button
          class="btn btn-sm"
          @click="onValidate"
        >
          {{ $t('workflow.editor.validate') }}
        </button>

        <template v-if="isDesktop">
          <button
            class="btn btn-primary btn-sm"
            :disabled="saveMutation.isPending.value || !store.dirty"
            @click="onSave"
          >
            {{ $t('workflow.editor.save') }}
          </button>

          <button
            class="btn btn-sm"
            :disabled="store.hasErrors || dryRunning"
            @click="onDryRun"
          >
            {{ $t('workflow.editor.dryRun') }}
          </button>
        </template>
      </div>
    </header>

    <!-- Load error banner -->
    <div
      v-if="loadError"
      role="alert"
      class="px-4 py-2 bg-red-50 text-red-700 text-sm border-b"
    >
      {{ loadError }}
      <button
        class="ml-2 underline"
        @click="loadWorkflow()"
      >
        {{ $t('workflow.editor.retry') }}
      </button>
    </div>

    <!-- Lint status bar -->
    <div
      v-if="store.lintErrors.length || store.lintWarnings.length"
      class="px-4 py-1 text-xs border-b"
      :class="store.lintErrors.length ? 'bg-red-50 text-red-700' : 'bg-yellow-50 text-yellow-700'"
    >
      <span v-if="store.lintErrors.length">
        {{ store.lintErrors.length }} {{ $t('workflow.editor.errors') }}
      </span>
      <span v-if="store.lintErrors.length && store.lintWarnings.length"> · </span>
      <span v-if="store.lintWarnings.length">
        {{ store.lintWarnings.length }} {{ $t('workflow.editor.warnings') }}
      </span>
    </div>

    <!-- Read-only notice on small screens (R24.33) -->
    <div
      v-if="!isDesktop"
      class="px-4 py-3 bg-blue-50 text-blue-700 text-sm border-b"
    >
      {{ $t('workflow.editor.readOnlyNotice') }}
    </div>

    <!-- Canvas + node inspector sidebar -->
    <div class="flex flex-1 min-h-0">
      <!-- Vue Flow canvas -->
      <div
        ref="canvasEl"
        class="flex-1 relative"
      >
        <div
          v-if="!workflow && !loadError"
          class="absolute inset-0 flex items-center justify-center text-sm text-gray-500"
          role="status"
        >
          {{ $t('workflow.editor.loading') }}
        </div>
        <div
          v-else-if="workflow && !flowNodes.length"
          class="absolute inset-0 flex items-center justify-center text-sm text-gray-500"
        >
          {{ $t('workflow.editor.emptyCanvas') }}
        </div>
        <VueFlow
          v-if="flowNodes.length"
          v-model:nodes="flowNodes"
          v-model:edges="flowEdges"
          :default-viewport="{ x: 50, y: 50, zoom: 0.85 }"
          fit-view-on-init
          @nodes-change="onNodesChange"
          @edges-change="onEdgesChange"
          @node-click="onNodeClick"
          @pane-click="store.selectNode(null)"
        >
          <template #node-default="{ data, id }">
            <div
              class="node-box rounded border px-3 py-2 text-xs min-w-[120px]"
              :class="nodeClass(data.nodeType, id)"
            >
              <div class="font-semibold">
                {{ data.label || id }}
              </div>
              <div class="text-gray-400">
                {{ data.nodeType }}
              </div>
            </div>
          </template>
        </VueFlow>
      </div>

      <!-- Side panel: node config inspector -->
      <aside
        v-if="selectedNode && isDesktop"
        class="w-80 border-l bg-surface p-4 overflow-y-auto shrink-0"
      >
        <h3 class="font-semibold mb-2">
          {{ selectedNode.id }}
        </h3>
        <dl class="text-xs space-y-2">
          <div>
            <dt class="text-gray-500">
              {{ $t('workflow.editor.nodeType') }}
            </dt>
            <dd>{{ selectedNode.type }}</dd>
          </div>
          <div>
            <dt class="text-gray-500">
              {{ $t('workflow.editor.nodeConfig') }}
            </dt>
            <dd>
              <pre class="bg-bg border rounded p-2 overflow-x-auto text-[11px]">{{
                JSON.stringify(selectedNode.config, null, 2)
              }}</pre>
            </dd>
          </div>
        </dl>
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { VueFlow, type Node as FlowNode, type Edge as FlowEdge, type GraphNode } from '@vue-flow/core'
import { useMutation, useQueryClient } from '@tanstack/vue-query'
import { computed, ref } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'

import { ElMessageBox } from 'element-plus'
import { useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { useBreakpoint } from '@shared/composables'
import {
  listWorkflows,
  patchWorkflow,
  triggerRun,
  validateWorkflow,
} from '../api'
import { wfKeys } from '../queries'
import { useWorkflowStore } from '../stores/workflow'
import type { Workflow, WorkflowDefinition, WorkflowNode } from '../types'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'

const { t } = useI18n()
const toast = useToast()
const route = useRoute()
const qc = useQueryClient()
const store = useWorkflowStore()

const { isDesktop } = useBreakpoint()
const workflowId = route.params.workflowId as string
const workspaceId = ref('')
const dryRunning = ref(false)

const workflow = ref<Workflow | null>(null)
const loadError = ref<string | null>(null)
const definition = ref<WorkflowDefinition>({
  entry_node_id: '',
  nodes: [],
  edges: [],
})

// Vue Flow reactive models
const flowNodes = ref<FlowNode[]>([])
const flowEdges = ref<FlowEdge[]>([])

function defToFlow(def: WorkflowDefinition): void {
  flowNodes.value = def.nodes.map((n, i) => ({
    id: n.id,
    type: 'default',
    position: n.position ?? { x: 100 + (i % 4) * 200, y: 80 + Math.floor(i / 4) * 120 },
    data: { label: n.label || n.id, nodeType: n.type, config: n.config },
  }))
  flowEdges.value = def.edges.map((e) => ({
    id: e.id,
    source: e.from,
    target: e.to,
    sourceHandle: e.from_port ?? 'default',
    label: e.from_port && e.from_port !== 'default' ? e.from_port : undefined,
    animated: false,
  }))
}

function flowToDef(): WorkflowDefinition {
  const nodes: WorkflowNode[] = flowNodes.value.map((fn) => ({
    id: fn.id,
    type: fn.data.nodeType,
    label: fn.data.label !== fn.id ? fn.data.label : undefined,
    config: fn.data.config ?? {},
    position: fn.position,
  }))
  const edges = flowEdges.value.map((fe: FlowEdge) => ({
    id: fe.id,
    from: fe.source,
    to: fe.target,
    from_port: fe.sourceHandle ?? 'default',
  }))
  return {
    ...definition.value,
    nodes,
    edges,
  }
}

// Init: load the workflow from route context
async function loadWorkflow(): Promise<void> {
  // `useWorkflowStore` is a Pinia singleton holding per-workflow editor state
  // (undo/redo stacks, lint results, dirty flag). Reset it first so workflow B
  // never inherits workflow A's stacks — an "undo" on B would otherwise splice
  // A's nodes onto B.
  store.clearAll()
  const wsId = (route.params.workspaceId as string) || ''
  workspaceId.value = wsId
  if (!wsId) {
    loadError.value = t('workflow.editor.loadFailed')
    return
  }

  try {
    const all = await listWorkflows(wsId)
    const found = all.find((w) => w.id === workflowId)
    if (!found) {
      loadError.value = t('workflow.editor.notFound')
      return
    }

    workflow.value = found
    definition.value = found.definition
    store.markSaved(found.version)
    defToFlow(found.definition)
    loadError.value = null
  } catch {
    loadError.value = t('workflow.editor.loadFailed')
  }
}

void loadWorkflow()

// Warn before discarding unsaved edits. `onBeforeRouteLeave` covers leaving
// the editor entirely; `onBeforeRouteUpdate` covers switching to a different
// workflow in place — a :workflowId param change on the same route record,
// which would otherwise bypass the leave guard.
async function confirmUnsaved(): Promise<boolean> {
  if (!store.dirty) return true
  try {
    await ElMessageBox.confirm(
      t('workflow.editor.unsavedConfirm'),
      t('workflow.editor.unsavedConfirmTitle'),
      {
        confirmButtonText: t('workflow.editor.leaveAnyway'),
        cancelButtonText: t('app.cancel'),
        type: 'warning',
      },
    )
    return true
  } catch {
    return false
  }
}
onBeforeRouteLeave(confirmUnsaved)
onBeforeRouteUpdate(confirmUnsaved)

// Selected node from inspector
const selectedNode = computed(() => {
  if (!store.selectedNodeId) return null
  return definition.value.nodes.find((n) => n.id === store.selectedNodeId) ?? null
})

function nodeClass(nodeType: string, nodeId: string): string {
  const classes: string[] = []
  if (nodeId === store.selectedNodeId) classes.push('ring-2 ring-blue-400')
  const step = store.liveSteps[nodeId]
  if (step?.state === 'running') classes.push('border-blue-500 bg-blue-50')
  else if (step?.state === 'succeeded') classes.push('border-green-500 bg-green-50')
  else if (step?.state === 'failed') classes.push('border-red-500 bg-red-50')
  if (nodeType === 'trigger') classes.push('border-purple-400')
  if (nodeType === 'end') classes.push('border-gray-600')
  return classes.join(' ')
}

// Debounced lint on change
let lintTimer: number | null = null

function scheduleLint(): void {
  if (lintTimer) clearTimeout(lintTimer)
  lintTimer = window.setTimeout(async () => {
    if (!workspaceId.value) return
    const def = flowToDef()
    try {
      const result = await validateWorkflow(workspaceId.value, def)
      store.setLintResult(result.errors, result.warnings)
    } catch {
      // Validation endpoint unavailable — skip.
    }
  }, 500)
}

function onNodesChange(): void {
  store.markDirty()
  scheduleLint()
}

function onEdgesChange(): void {
  store.markDirty()
  scheduleLint()
}

function onNodeClick({ node }: { node: GraphNode }): void {
  store.selectNode(node.id)
}

// Undo / redo
function onUndo(): void {
  const prev = store.popUndo()
  if (!prev) return
  store.pushRedo(flowToDef())
  definition.value = prev
  defToFlow(prev)
}

function onRedo(): void {
  const next = store.popRedo()
  if (!next) return
  store.pushUndo(flowToDef())
  definition.value = next
  defToFlow(next)
}

// Save
const saveMutation = useMutation({
  mutationFn: async () => {
    if (!workflow.value) return
    const def = flowToDef()
    return patchWorkflow(workflowId, store.currentVersion, {
      definition: def,
    })
  },
  onSuccess: (wf) => {
    if (!wf) return
    workflow.value = wf
    definition.value = wf.definition
    store.markSaved(wf.version)
    qc.invalidateQueries({ queryKey: wfKeys.workflow(workflowId) })
  },
  onError: () => toast.error(t('workflow.editor.saveFailed')),
})

function onSave(): void {
  store.pushUndo(flowToDef())
  saveMutation.mutate()
}

// Validate
async function onValidate(): Promise<void> {
  if (!workspaceId.value) return
  const def = flowToDef()
  try {
    const result = await validateWorkflow(workspaceId.value, def)
    store.setLintResult(result.errors, result.warnings)
    if (!result.errors.length && !result.warnings.length) {
      toast.success(t('workflow.editor.valid'))
    }
  } catch {
    toast.error(t('workflow.editor.validateFailed'))
  }
}

// Dry-run simulator
async function onDryRun(): Promise<void> {
  if (!workflow.value) return
  dryRunning.value = true
  try {
    await triggerRun(workflowId, { __dry_run: true })
    toast.success(t('workflow.editor.dryRunQueued'))
  } catch {
    toast.error(t('workflow.editor.dryRunFailed'))
  } finally {
    dryRunning.value = false
  }
}
</script>

<style scoped>
.workflow-editor {
  height: 100%;
}
.node-box {
  cursor: pointer;
  transition: box-shadow 0.15s ease;
  background: var(--color-bg);
}
.node-box:hover {
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.3);
}
</style>
