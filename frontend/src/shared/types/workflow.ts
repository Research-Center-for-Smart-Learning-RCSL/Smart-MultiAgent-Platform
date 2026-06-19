// Cross-cutting orchestration / wakeup types consumed by multiple slices
// (conversation, workflow, agents).  Moved here from the workflow slice to
// break the conversation -> workflow type coupling (H15).

// ---------------------------------------------------------------------------
// Approval DTOs (G.6–G.8)
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

// ---------------------------------------------------------------------------
// Wakeup / DLQ DTOs
// ---------------------------------------------------------------------------

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
