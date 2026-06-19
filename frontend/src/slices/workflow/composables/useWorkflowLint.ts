// Composable: debounced workflow validation scheduling and lint result management.
// Extracted from WorkflowEditorView.vue (H20 SoC fix).

import { validateWorkflow } from '../api'
import { useWorkflowStore } from '../stores/workflow'
import type { WorkflowDefinition } from '../types'

export function useWorkflowLint(
  /** Reactive getter for the workspace id (may change on route update). */
  getWorkspaceId: () => string,
  /** Callback that serializes the current flow graph to a WorkflowDefinition. */
  flowToDef: () => WorkflowDefinition,
) {
  const store = useWorkflowStore()
  let lintTimer: number | null = null

  function scheduleLint(): void {
    if (lintTimer) clearTimeout(lintTimer)
    lintTimer = window.setTimeout(async () => {
      const wsId = getWorkspaceId()
      if (!wsId) return
      const def = flowToDef()
      try {
        const result = await validateWorkflow(wsId, def)
        store.setLintResult(result.errors, result.warnings)
      } catch {
        // Validation endpoint unavailable — skip.
      }
    }, 500)
  }

  return {
    scheduleLint,
  }
}
