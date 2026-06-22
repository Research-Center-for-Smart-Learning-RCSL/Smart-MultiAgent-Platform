<template>
  <section class="workflow-editor flex flex-col h-full">
    <!-- Toolbar -->
    <header class="flex items-center gap-3 px-4 py-2 border-b bg-bg shrink-0">
      <router-link
        :to="{ name: 'workflow.list', params: { workspaceId } }"
        class="text-sm text-muted hover:underline"
      >
        &larr; {{ $t('workflow.editor.backToList') }}
      </router-link>

      <h2
        v-if="workflow"
        class="font-semibold truncate max-w-[180px] sm:max-w-[300px]"
      >
        {{ workflow.name }}
      </h2>

      <span
        v-if="store.dirty"
        class="text-xs text-warning"
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
                <div class="px-3 py-1 text-2xs font-semibold text-muted uppercase tracking-wide">
                  {{ $t(group.label) }}
                </div>
                <button
                  v-for="nt in group.types"
                  :key="nt"
                  class="block w-full text-left px-3 py-1.5 text-sm hover:bg-accent/10"
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
      class="px-4 py-2 bg-danger-tint text-danger-on text-sm border-b"
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
      :class="store.lintErrors.length ? 'bg-danger-tint text-danger-on' : 'bg-warning-tint text-warning-on'"
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
      class="px-4 py-3 bg-info-tint text-info-on text-sm border-b"
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
          class="absolute inset-0 flex items-center justify-center text-sm text-muted"
          role="status"
        >
          {{ $t('workflow.editor.loading') }}
        </div>
        <div
          v-else-if="workflow && !flowNodes.length"
          class="absolute inset-0 flex items-center justify-center text-sm text-muted"
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
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { useMutation, useQueryClient } from '@tanstack/vue-query'
import { markRaw, ref } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'

import { useConfirmDialog, useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { useBreakpoint } from '@shared/composables'
import {
  listWorkflows,
  patchWorkflow,
  validateWorkflow,
} from '../api'
import { wfKeys } from '../queries'
import { useWorkflowStore } from '../stores/workflow'
import { NODE_TYPE_LABELS, NODE_PALETTE_GROUPS } from '../constants'
import NodeConfigPanel from '../components/NodeConfigPanel.vue'
import WorkflowNodeComponent from '../components/WorkflowNodeComponent.vue'
import { useWorkflowEditor } from '../composables/useWorkflowEditor'
import { useWorkflowLint } from '../composables/useWorkflowLint'
import type { Workflow } from '../types'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirmDialog()
const route = useRoute()
const qc = useQueryClient()
const store = useWorkflowStore()

const { isDesktop } = useBreakpoint()
const workflowId = route.params.workflowId as string
const workspaceId = ref((route.params.workspaceId as string) || '')

const workflow = ref<Workflow | null>(null)
const loadError = ref<string | null>(null)

// Data for config forms (agents + chatrooms in the project)
const agents = ref<Array<{ id: string; name: string }>>([])
const chatrooms = ref<Array<{ id: string; name: string }>>([])

// Custom node type registration — markRaw prevents Vue from making the
// component definition reactive (VueFlow requirement).
const nodeTypes = { 'workflow-node': markRaw(WorkflowNodeComponent) }

// ---- composables ----------------------------------------------------------

const {
  definition,
  flowNodes,
  flowEdges,
  paletteOpen,
  allNodeIds,
  selectedNode,
  seedNodeCounter,
  defToFlow,
  flowToDef,
  addNode,
  onDeleteNode,
  onConnect,
  onNodeClick,
  onEdgeClick,
  onPaneClick,
  onCanvasKeydown,
  onConfigUpdate: onConfigUpdateBase,
  onLabelUpdate: onLabelUpdateBase,
  onNodesChange: onNodesChangeBase,
  onEdgesChange: onEdgesChangeBase,
  onUndo,
  onRedo,
} = useWorkflowEditor()

const { scheduleLint } = useWorkflowLint(
  () => workspaceId.value,
  flowToDef,
)

// Wire up change handlers to also trigger lint
function onNodesChange(): void {
  onNodesChangeBase()
  scheduleLint()
}

function onEdgesChange(): void {
  onEdgesChangeBase()
  scheduleLint()
}

function onConfigUpdate(config: Record<string, unknown>): void {
  onConfigUpdateBase(config)
  scheduleLint()
}

function onLabelUpdate(label: string): void {
  onLabelUpdateBase(label)
}

// ---- load / init ----------------------------------------------------------

async function loadWorkflow(): Promise<void> {
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

// ---- unsaved changes guard ------------------------------------------------

async function confirmUnsaved(): Promise<boolean> {
  if (!store.dirty) return true
  const ok = await confirm({
    title: t('workflow.editor.unsavedConfirmTitle'),
    message: t('workflow.editor.unsavedConfirm'),
    confirmLabel: t('workflow.editor.leaveAnyway'),
    cancelLabel: t('app.cancel'),
    variant: 'warning',
  })
  return ok
}
onBeforeRouteLeave(confirmUnsaved)
onBeforeRouteUpdate(confirmUnsaved)

// ---- save mutation --------------------------------------------------------

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

// ---- validate -------------------------------------------------------------

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
