import { defineStore } from 'pinia'
import { computed, reactive, ref, shallowRef } from 'vue'
import type {
  LintIssue,
  WorkflowDefinition,
  WorkflowNode,
  WorkflowRunEvent,
  WorkflowStep,
} from '../types'

export const useWorkflowStore = defineStore('workflow', () => {
  // -- Editor state --
  const dirty = ref(false)
  const currentVersion = ref(0)
  const lintErrors = ref<LintIssue[]>([])
  const lintWarnings = ref<LintIssue[]>([])
  const selectedNodeId = ref<string | null>(null)
  const undoStack = shallowRef<WorkflowDefinition[]>([])
  const redoStack = shallowRef<WorkflowDefinition[]>([])

  function setLintResult(errors: LintIssue[], warnings: LintIssue[]): void {
    lintErrors.value = errors
    lintWarnings.value = warnings
  }

  function markDirty(): void {
    dirty.value = true
  }

  function markSaved(version: number): void {
    dirty.value = false
    currentVersion.value = version
  }

  function selectNode(nodeId: string | null): void {
    selectedNodeId.value = nodeId
  }

  function pushUndo(snapshot: WorkflowDefinition): void {
    undoStack.value = [...undoStack.value.slice(-49), snapshot]
    redoStack.value = []
  }

  function popUndo(): WorkflowDefinition | undefined {
    const stack = [...undoStack.value]
    const item = stack.pop()
    undoStack.value = stack
    return item
  }

  function pushRedo(snapshot: WorkflowDefinition): void {
    redoStack.value = [...redoStack.value, snapshot]
  }

  function popRedo(): WorkflowDefinition | undefined {
    const stack = [...redoStack.value]
    const item = stack.pop()
    redoStack.value = stack
    return item
  }

  const canUndo = computed(() => undoStack.value.length > 0)
  const canRedo = computed(() => redoStack.value.length > 0)
  const hasErrors = computed(() => lintErrors.value.length > 0)

  // -- Run inspector state --
  const liveSteps = reactive<Record<string, WorkflowStep>>({})
  const runEvents = ref<WorkflowRunEvent[]>([])

  function applyRunEvent(event: WorkflowRunEvent): void {
    runEvents.value = [...runEvents.value, event]
    if (event.type === 'workflow.step_started' || event.type === 'workflow.step_finished' || event.type === 'workflow.step_failed') {
      const stepId = event.step_id as string
      const nodeId = event.node_id as string
      liveSteps[nodeId] = {
        id: stepId,
        run_id: (event.run_id as string) ?? '',
        node_id: nodeId,
        state: (event.state as string) ?? 'running',
        started_at: new Date().toISOString(),
        ended_at: event.type !== 'workflow.step_started' ? new Date().toISOString() : null,
        input: {},
        output: {},
        error: null,
      } as WorkflowStep
    }
  }

  function clearRunState(): void {
    Object.keys(liveSteps).forEach((k) => delete liveSteps[k])
    runEvents.value = []
  }

  function clearAll(): void {
    clearRunState()
    dirty.value = false
    currentVersion.value = 0
    lintErrors.value = []
    lintWarnings.value = []
    selectedNodeId.value = null
    undoStack.value = []
    redoStack.value = []
  }

  return {
    dirty,
    currentVersion,
    lintErrors,
    lintWarnings,
    selectedNodeId,
    canUndo,
    canRedo,
    hasErrors,
    liveSteps,
    runEvents,
    setLintResult,
    markDirty,
    markSaved,
    selectNode,
    pushUndo,
    popUndo,
    pushRedo,
    popRedo,
    applyRunEvent,
    clearRunState,
    clearAll,
  }
})
