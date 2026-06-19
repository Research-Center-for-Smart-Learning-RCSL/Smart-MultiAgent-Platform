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
          <!-- Add Node dropdown -->
          <div class="relative">
            <button
              class="btn btn-sm"
              @click="paletteOpen = !paletteOpen"
            >
              + {{ $t('workflow.palette.addNode') }}
            </button>
            <div
              v-if="paletteOpen"
              class="absolute top-full left-0 mt-1 bg-bg border rounded shadow-lg z-50 w-52 py-1"
            >
              <template
                v-for="group in NODE_PALETTE_GROUPS"
                :key="group.label"
              >
                <div class="px-3 py-1 text-[10px] font-semibold text-gray-400 uppercase tracking-wide">
                  {{ $t(group.label) }}
                </div>
                <button
                  v-for="nt in group.types"
                  :key="nt"
                  class="block w-full text-left px-3 py-1.5 text-sm hover:bg-blue-50 dark:hover:bg-blue-900/30"
                  @click="addNode(nt)"
                >
                  {{ $t(NODE_TYPE_LABELS[nt]) }}
                </button>
              </template>
            </div>
          </div>

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
            disabled
            :title="$t('workflow.editor.dryRunComingSoon')"
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
          :node-types="nodeTypes"
          :default-viewport="{ x: 50, y: 50, zoom: 0.85 }"
          fit-view-on-init
          @nodes-change="onNodesChange"
          @edges-change="onEdgesChange"
          @node-click="onNodeClick"
          @edge-click="onEdgeClick"
          @connect="onConnect"
          @pane-click="onPaneClick"
          @keydown="onCanvasKeydown"
        >
          <Background />
          <Controls />
        </VueFlow>
      </div>

      <!-- Side panel: node config editor -->
      <aside
        v-if="selectedNode && isDesktop"
        class="w-80 border-l bg-surface p-4 overflow-y-auto shrink-0"
      >
        <NodeConfigPanel
          :node="selectedNode"
          :agents="agents"
          :chatrooms="chatrooms"
          :all-node-ids="allNodeIds"
          @update:config="onConfigUpdate"
          @update:label="onLabelUpdate"
          @delete="onDeleteNode"
        />
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { VueFlow, type Node as FlowNode, type Edge as FlowEdge, type GraphNode, type Connection } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { useMutation, useQueryClient } from '@tanstack/vue-query'
import { computed, markRaw, ref } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'

import { ElMessageBox } from 'element-plus'
import { useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { useBreakpoint } from '@shared/composables'
import {
  listWorkflows,
  patchWorkflow,
  validateWorkflow,
} from '../api'
import { wfKeys } from '../queries'
import { useWorkflowStore } from '../stores/workflow'
import { NODE_DEFAULTS, NODE_TYPE_LABELS, NODE_PALETTE_GROUPS } from '../constants'
import NodeConfigPanel from '../components/NodeConfigPanel.vue'
import WorkflowNodeComponent from '../components/WorkflowNodeComponent.vue'
import type { NodeType, Workflow, WorkflowDefinition, WorkflowNode } from '../types'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'

const { t } = useI18n()
const toast = useToast()
const route = useRoute()
const qc = useQueryClient()
const store = useWorkflowStore()

const { isDesktop } = useBreakpoint()
const workflowId = route.params.workflowId as string
const workspaceId = ref((route.params.workspaceId as string) || '')
const paletteOpen = ref(false)
const selectedEdgeId = ref<string | null>(null)
let nodeCounter = 0

function seedNodeCounter(nodes: FlowNode[]): void {
  let max = 0
  for (const n of nodes) {
    const m = n.id.match(/_(\d+)$/)
    if (m) max = Math.max(max, Number(m[1]))
  }
  nodeCounter = max
}

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

// Data for config forms (agents + chatrooms in the project)
const agents = ref<Array<{ id: string; name: string }>>([])
const chatrooms = ref<Array<{ id: string; name: string }>>([])
const allNodeIds = computed(() => flowNodes.value.map((n) => n.id))

// Custom node type registration — markRaw prevents Vue from making the
// component definition reactive (VueFlow requirement).
const nodeTypes = { 'workflow-node': markRaw(WorkflowNodeComponent) }

function defToFlow(def: WorkflowDefinition): void {
  flowNodes.value = def.nodes.map((n, i) => ({
    id: n.id,
    type: 'workflow-node',
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
    seedNodeCounter(flowNodes.value)
    loadError.value = null
  } catch {
    loadError.value = t('workflow.editor.loadFailed')
  }
}

async function loadContextData(): Promise<void> {
  try {
    const { getWorkspace } = await import('@slices/conversation')
    const ws = await getWorkspace(workspaceId.value)
    const [{ agentsApi }, { listChatrooms }] = await Promise.all([
      import('@slices/agents'),
      import('@slices/conversation'),
    ])
    const [agentRes, chatroomList] = await Promise.all([
      agentsApi.list(ws.project_id),
      listChatrooms(workspaceId.value),
    ])
    agents.value = agentRes.data.map((a: { id: string; name: string }) => ({ id: a.id, name: a.name }))
    chatrooms.value = chatroomList.map((c: { id: string; name: string }) => ({ id: c.id, name: c.name }))
  } catch {
    // Non-fatal — config pickers will just be empty
  }
}

void loadWorkflow().then(() => {
  if (workspaceId.value) void loadContextData()
})

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

// Selected node — derived from flowNodes (the live source of truth) rather than
// definition.value (only updated on load/save/undo).
const selectedNode = computed<WorkflowNode | null>(() => {
  if (!store.selectedNodeId) return null
  const fn = flowNodes.value.find((n) => n.id === store.selectedNodeId)
  if (!fn) return null
  return {
    id: fn.id,
    type: fn.data.nodeType as NodeType,
    label: fn.data.label,
    config: fn.data.config ?? {},
    position: fn.position,
  }
})

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
  selectedEdgeId.value = null
}

function onEdgeClick({ edge }: { edge: FlowEdge }): void {
  selectedEdgeId.value = edge.id
  store.selectNode(null)
}

function onPaneClick(): void {
  store.selectNode(null)
  selectedEdgeId.value = null
  paletteOpen.value = false
}

// ---------------------------------------------------------------------------
// Add node
// ---------------------------------------------------------------------------

function addNode(type: NodeType): void {
  paletteOpen.value = false
  store.pushUndo(flowToDef())
  nodeCounter++
  const id = `${type}_${nodeCounter}`
  const pos = { x: 250 + nodeCounter * 30, y: 200 + nodeCounter * 30 }
  flowNodes.value = [...flowNodes.value, {
    id,
    type: 'workflow-node',
    position: pos,
    data: { label: id, nodeType: type, config: JSON.parse(JSON.stringify(NODE_DEFAULTS[type])) },
  }]
  store.selectNode(id)
  store.markDirty()
  scheduleLint()
  toast.success(t('workflow.config.nodeAdded'))
}

// ---------------------------------------------------------------------------
// Delete node / edge
// ---------------------------------------------------------------------------

async function onDeleteNode(): Promise<void> {
  const nodeId = store.selectedNodeId
  if (!nodeId) return
  const node = flowNodes.value.find((n) => n.id === nodeId)
  if (node?.data.nodeType === 'trigger') {
    toast.error(t('workflow.config.cannotDeleteTrigger'))
    return
  }
  try {
    await ElMessageBox.confirm(
      t('workflow.config.deleteConfirm'),
      t('workflow.config.deleteConfirmTitle'),
      { confirmButtonText: t('workflow.config.deleteNode'), type: 'warning' },
    )
  } catch { return }
  store.pushUndo(flowToDef())
  flowNodes.value = flowNodes.value.filter((n) => n.id !== nodeId)
  flowEdges.value = flowEdges.value.filter((e) => e.source !== nodeId && e.target !== nodeId)
  store.selectNode(null)
  store.markDirty()
  scheduleLint()
  toast.success(t('workflow.config.nodeDeleted'))
}

function deleteSelectedEdge(): void {
  if (!selectedEdgeId.value) return
  store.pushUndo(flowToDef())
  flowEdges.value = flowEdges.value.filter((e) => e.id !== selectedEdgeId.value)
  selectedEdgeId.value = null
  store.markDirty()
  scheduleLint()
}

function onCanvasKeydown(event: KeyboardEvent): void {
  if (event.key === 'Delete' || event.key === 'Backspace') {
    const tag = (event.target as HTMLElement)?.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
    if (selectedEdgeId.value) deleteSelectedEdge()
    else if (store.selectedNodeId) void onDeleteNode()
  }
}

// ---------------------------------------------------------------------------
// Connect edges
// ---------------------------------------------------------------------------

function onConnect(connection: Connection): void {
  if (!connection.source || !connection.target) return
  const srcNode = flowNodes.value.find((n) => n.id === connection.source)
  if (srcNode?.data.nodeType === 'end') return
  const tgtNode = flowNodes.value.find((n) => n.id === connection.target)
  if (tgtNode?.data.nodeType === 'trigger') return
  const handle = connection.sourceHandle ?? 'default'
  const edgeId = `e_${connection.source}_${handle}_${connection.target}`
  if (flowEdges.value.some((e) => e.source === connection.source && e.target === connection.target && e.sourceHandle === handle)) return
  store.pushUndo(flowToDef())
  flowEdges.value = [...flowEdges.value, {
    id: edgeId,
    source: connection.source,
    target: connection.target,
    sourceHandle: handle,
    label: handle !== 'default' ? handle : undefined,
    animated: false,
  }]
  store.markDirty()
  scheduleLint()
}

// ---------------------------------------------------------------------------
// Config / label update from sidebar — debounced undo so each keystroke
// doesn't push a separate snapshot.
// ---------------------------------------------------------------------------

let undoTimer: number | null = null
let undoSnapshotPending = false

function pushUndoDebounced(): void {
  if (!undoSnapshotPending) {
    undoSnapshotPending = true
    store.pushUndo(flowToDef())
  }
  if (undoTimer) clearTimeout(undoTimer)
  undoTimer = window.setTimeout(() => { undoSnapshotPending = false }, 600)
}

function onConfigUpdate(config: Record<string, unknown>): void {
  const nodeId = store.selectedNodeId
  if (!nodeId) return
  pushUndoDebounced()
  flowNodes.value = flowNodes.value.map((n) =>
    n.id === nodeId ? { ...n, data: { ...n.data, config } } : n,
  )
  store.markDirty()
  scheduleLint()
}

function onLabelUpdate(label: string): void {
  const nodeId = store.selectedNodeId
  if (!nodeId) return
  pushUndoDebounced()
  flowNodes.value = flowNodes.value.map((n) =>
    n.id === nodeId ? { ...n, data: { ...n.data, label } } : n,
  )
  store.markDirty()
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

</script>

<style scoped>
.workflow-editor {
  height: 100%;
}
</style>
