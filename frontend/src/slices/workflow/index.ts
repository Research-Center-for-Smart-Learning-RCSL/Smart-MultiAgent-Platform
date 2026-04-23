// Public surface of the workflow slice. Only exports listed here are
// importable from other slices (enforced in Phase J).

import { registerMessages } from '@shared/i18n'
import en from './locales/en.json'
import zhTW from './locales/zh-TW.json'

export { workflowRoutes } from './routes'
export { patchAgentWakeupConfig } from './api'
export { useOrchestrationStore } from './stores/orchestration'
export { useWorkflowStore } from './stores/workflow'
export { useWorkflowRunSocket } from './composables/useWorkflowRunSocket'
export { wfKeys } from './queries'
export { default as ApprovalCard } from './components/ApprovalCard.vue'
export { default as DlqViewer } from './components/DlqViewer.vue'
export { default as WakeupConfigEditor } from './components/WakeupConfigEditor.vue'

export type {
  Approval,
  ApprovalMode,
  ApprovalState,
  ApprovalVote,
  ApprovalWithVotes,
  AgentInstance,
  DlqEntry,
  Instruction,
  InstructionState,
  LintIssue,
  NodeType,
  OnErrorStrategy,
  RunState,
  StepState,
  TriggerType,
  ValidationResult,
  WakeupConfig,
  Workflow,
  WorkflowDefinition,
  WorkflowEdge,
  WorkflowNode,
  WorkflowRun,
  WorkflowRunEvent,
  WorkflowStep,
} from './types'

export function installWorkflowSlice(): void {
  registerMessages('en', en)
  registerMessages('zh-TW', zhTW)
}
