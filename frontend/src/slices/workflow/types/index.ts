// Workflow + Orchestration DTOs — mirrors backend Pydantic schemas.

// ---------------------------------------------------------------------------
// Workflow engine types (H.7)
// ---------------------------------------------------------------------------

export type NodeType =
  | 'trigger'
  | 'agent_invocation'
  | 'approval_gate'
  | 'condition'
  | 'instruct'
  | 'subagent_spawn'
  | 'wait_for_event'
  | 'parallel'
  | 'join'
  | 'set_variable'
  | 'end'

export type TriggerType =
  | 'manual'
  | 'cron'
  | 'message_received'
  | 'a2a_event'
  | 'wakeup_signal'

export type RunState = 'running' | 'waiting' | 'succeeded' | 'failed' | 'cancelled'

export type StepState =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'cancelled'

export type OnErrorStrategy = 'fail' | 'continue' | 'retry' | 'fallback'

export interface OnErrorConfig {
  strategy: OnErrorStrategy
  retry_max?: number
  retry_backoff_ms?: number
  fallback_node_id?: string | null
}

export interface WorkflowNode {
  id: string
  type: NodeType
  label?: string
  config: Record<string, unknown>
  position?: { x: number; y: number }
}

export interface WorkflowEdge {
  id: string
  from: string
  to: string
  from_port?: string
  guard?: string | null
}

export interface WorkflowDefinition {
  entry_node_id: string
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  variables?: Record<string, { type: string; default?: unknown }>
  timeouts?: {
    run_max_seconds?: number
    idle_max_seconds?: number
  }
  loop_guard?: {
    max_visits_per_node?: number
  }
}

export interface Workflow {
  id: string
  workspace_id: string
  name: string
  definition: WorkflowDefinition
  version: number
  created_at: string
  deleted_at: string | null
}

export interface WorkflowRun {
  id: string
  workflow_id: string
  trigger_type: string
  started_by_user_id: string | null
  state: RunState
  variables?: Record<string, unknown>
  started_at: string
  ended_at: string | null
  archived?: boolean
}

export interface WorkflowStep {
  id: string
  run_id: string
  node_id: string
  state: StepState
  started_at: string
  ended_at: string | null
  input: Record<string, unknown>
  output: Record<string, unknown>
  error: string | null
}

export interface LintIssue {
  rule: number
  level: 'error' | 'warning'
  message: string
  node_id?: string | null
  edge_id?: string | null
}

export interface ValidationResult {
  valid: boolean
  errors: LintIssue[]
  warnings: LintIssue[]
}

export interface WorkflowRunEvent {
  type:
    | 'workflow.run_started'
    | 'workflow.run_finished'
    | 'workflow.run_cancelled'
    | 'workflow.step_started'
    | 'workflow.step_finished'
    | 'workflow.step_failed'
    | 'approval.requested'
    | 'approval.resolved'
  [key: string]: unknown
}

// ---------------------------------------------------------------------------
// Orchestration DTOs (G.6–G.8)
// ---------------------------------------------------------------------------

export type ApprovalMode = 'single' | 'majority' | 'consensus'
export type ApprovalState = 'pending' | 'approved' | 'rejected' | 'timeout_leader'

export interface Approval {
  id: string
  workflow_run_id: string
  mode: ApprovalMode
  leader_agent_id: string
  approver_agent_ids: string[]
  timeout_seconds: number
  state: ApprovalState
  started_at: string
  ended_at: string | null
}

export interface ApprovalVote {
  approval_id: string
  voter_agent_id: string
  vote: boolean
  rationale: string | null
  cast_at: string
}

export interface ApprovalWithVotes extends Approval {
  votes: ApprovalVote[]
}

export type InstructionState =
  | 'issued'
  | 'delivered'
  | 'completed'
  | 'rejected_loop'
  | 'timeout'

export interface Instruction {
  id: string
  chain_id: string
  path: string[]
  depth: number
  issuer_agent_id: string
  target_agent_id: string
  payload: Record<string, unknown>
  state: InstructionState
  issued_at: string
  resolved_at: string | null
}

export interface AgentInstance {
  id: string
  agent_id: string
  parent_id: string | null
  chatroom_id: string | null
  run_context: Record<string, unknown>
  task_description: string | null
  state: string
  spawned_at: string
  destroyed_at: string | null
}

export interface WakeupTriggerConfig {
  enabled: boolean
  [k: string]: unknown
}

export interface WakeupConfig {
  triggers: {
    every_n_messages: WakeupTriggerConfig & { n: number }
    silence_minutes: WakeupTriggerConfig & {
      t_minutes: number
      autostop_rounds: number
      autostop_max_default: number
    }
    call_only: WakeupTriggerConfig
  }
  allow_self_open: boolean
  refresh_every_hours: number
}

export interface DlqEntry {
  stream_entry_id: string
  stream_id: string
  envelope: string
  attempt_count: number
  last_error: string
  moved_at: string
}
