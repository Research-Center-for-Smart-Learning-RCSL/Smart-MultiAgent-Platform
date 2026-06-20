// Composable: graph serialization, undo/redo, node/edge CRUD for the
// visual workflow editor. Extracted from WorkflowEditorView.vue (H20 SoC fix).

import type {
  Node as FlowNode,
  Edge as FlowEdge,
  GraphNode,
  Connection,
} from '@vue-flow/core'
import { computed, ref } from 'vue'

import { useConfirmDialog, useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { NODE_DEFAULTS } from '../constants'
import { useWorkflowStore } from '../stores/workflow'
import type { NodeType, WorkflowDefinition, WorkflowNode } from '../types'

export function useWorkflowEditor() {
  const { t } = useI18n()
  const toast = useToast()
  const { confirm } = useConfirmDialog()
  const store = useWorkflowStore()

  const definition = ref<WorkflowDefinition>({
    entry_node_id: '',
    nodes: [],
    edges: [],
  })

  const flowNodes = ref<FlowNode[]>([])
  const flowEdges = ref<FlowEdge[]>([])
  const selectedEdgeId = ref<string | null>(null)
  const paletteOpen = ref(false)
  let nodeCounter = 0

  const allNodeIds = computed(() => flowNodes.value.map((n) => n.id))

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

  // ---------- graph serialization ------------------------------------------

  function seedNodeCounter(nodes: FlowNode[]): void {
    let max = 0
    for (const n of nodes) {
      const m = n.id.match(/_(\d+)$/)
      if (m) max = Math.max(max, Number(m[1]))
    }
    nodeCounter = max
  }

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

  // ---------- node CRUD ----------------------------------------------------

  function addNode(type: NodeType): void {
    paletteOpen.value = false
    store.pushUndo(flowToDef())
    nodeCounter++
    const id = `${type}_${nodeCounter}`
    const pos = { x: 250 + nodeCounter * 30, y: 200 + nodeCounter * 30 }
    flowNodes.value = [
      ...flowNodes.value,
      {
        id,
        type: 'workflow-node',
        position: pos,
        data: { label: id, nodeType: type, config: JSON.parse(JSON.stringify(NODE_DEFAULTS[type])) },
      },
    ]
    store.selectNode(id)
    store.markDirty()
    toast.success(t('workflow.config.nodeAdded'))
  }

  async function onDeleteNode(): Promise<void> {
    const nodeId = store.selectedNodeId
    if (!nodeId) return
    const node = flowNodes.value.find((n) => n.id === nodeId)
    if (node?.data.nodeType === 'trigger') {
      toast.error(t('workflow.config.cannotDeleteTrigger'))
      return
    }
    const ok = await confirm({
      title: t('workflow.config.deleteConfirmTitle'),
      message: t('workflow.config.deleteConfirm'),
      confirmLabel: t('workflow.config.deleteNode'),
      variant: 'warning',
    })
    if (!ok) return
    store.pushUndo(flowToDef())
    flowNodes.value = flowNodes.value.filter((n) => n.id !== nodeId)
    flowEdges.value = flowEdges.value.filter((e) => e.source !== nodeId && e.target !== nodeId)
    store.selectNode(null)
    store.markDirty()
    toast.success(t('workflow.config.nodeDeleted'))
  }

  // ---------- edge CRUD ----------------------------------------------------

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
    flowEdges.value = [
      ...flowEdges.value,
      {
        id: edgeId,
        source: connection.source,
        target: connection.target,
        sourceHandle: handle,
        label: handle !== 'default' ? handle : undefined,
        animated: false,
      },
    ]
    store.markDirty()
  }

  function deleteSelectedEdge(): void {
    if (!selectedEdgeId.value) return
    store.pushUndo(flowToDef())
    flowEdges.value = flowEdges.value.filter((e) => e.id !== selectedEdgeId.value)
    selectedEdgeId.value = null
    store.markDirty()
  }

  // ---------- click handlers -----------------------------------------------

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

  function onCanvasKeydown(event: KeyboardEvent): void {
    if (event.key === 'Delete' || event.key === 'Backspace') {
      const tag = (event.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (selectedEdgeId.value) deleteSelectedEdge()
      else if (store.selectedNodeId) void onDeleteNode()
    }
  }

  // ---------- config / label update (debounced undo) -----------------------

  let undoTimer: number | null = null
  let undoSnapshotPending = false

  function pushUndoDebounced(): void {
    if (!undoSnapshotPending) {
      undoSnapshotPending = true
      store.pushUndo(flowToDef())
    }
    if (undoTimer) clearTimeout(undoTimer)
    undoTimer = window.setTimeout(() => {
      undoSnapshotPending = false
    }, 600)
  }

  function onConfigUpdate(config: Record<string, unknown>): void {
    const nodeId = store.selectedNodeId
    if (!nodeId) return
    pushUndoDebounced()
    flowNodes.value = flowNodes.value.map((n) =>
      n.id === nodeId ? { ...n, data: { ...n.data, config } } : n,
    )
    store.markDirty()
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

  // ---------- change handlers (to be called by the view + lint) ------------

  function onNodesChange(): void {
    store.markDirty()
  }

  function onEdgesChange(): void {
    store.markDirty()
  }

  // ---------- undo / redo --------------------------------------------------

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

  return {
    // state
    definition,
    flowNodes,
    flowEdges,
    selectedEdgeId,
    paletteOpen,
    allNodeIds,
    selectedNode,
    // graph serialization
    seedNodeCounter,
    defToFlow,
    flowToDef,
    // node CRUD
    addNode,
    onDeleteNode,
    // edge CRUD
    onConnect,
    deleteSelectedEdge,
    // click handlers
    onNodeClick,
    onEdgeClick,
    onPaneClick,
    onCanvasKeydown,
    // config / label update
    onConfigUpdate,
    onLabelUpdate,
    // change handlers
    onNodesChange,
    onEdgesChange,
    // undo / redo
    onUndo,
    onRedo,
  }
}
