import type { NodeType } from './types'

export const NODE_DEFAULTS: Record<NodeType, Record<string, unknown>> = {
  trigger: { trigger_type: 'manual', allowed_roles: ['Admin'] },
  agent_invocation: { agent_id: '', input_template: '' },
  approval_gate: { mode: 'single', leader_agent_id: '', approvers: [], timeout_seconds: 3600, question_template: '' },
  condition: { branches: [{ when: '', port: 'branch_1' }], default_port: 'default' },
  instruct: { issuer_agent_id: '', target_agent_id: '', instruction_template: '' },
  subagent_spawn: { parent_agent_id: '', task_template: '' },
  wait_for_event: { event_type: 'timer', timeout_seconds: 300, delay_seconds: 60 },
  parallel: {},
  join: { mode: 'all', timeout_seconds: 600 },
  set_variable: { assignments: [{ variable: '', expression: '' }] },
  end: { status: 'success' },
}

export const NODE_TYPE_LABELS: Record<NodeType, string> = {
  trigger: 'workflow.nodeTypes.trigger',
  agent_invocation: 'workflow.nodeTypes.agentInvocation',
  approval_gate: 'workflow.nodeTypes.approvalGate',
  condition: 'workflow.nodeTypes.condition',
  instruct: 'workflow.nodeTypes.instruct',
  subagent_spawn: 'workflow.nodeTypes.subagentSpawn',
  wait_for_event: 'workflow.nodeTypes.waitForEvent',
  parallel: 'workflow.nodeTypes.parallel',
  join: 'workflow.nodeTypes.join',
  set_variable: 'workflow.nodeTypes.setVariable',
  end: 'workflow.nodeTypes.end',
}

// Palette groups for the "Add Node" dropdown (trigger excluded — only one allowed)
export const NODE_PALETTE_GROUPS = [
  {
    label: 'workflow.palette.agents',
    types: ['agent_invocation', 'instruct', 'subagent_spawn', 'approval_gate'] as NodeType[],
  },
  {
    label: 'workflow.palette.logic',
    types: ['condition', 'set_variable', 'parallel', 'join'] as NodeType[],
  },
  {
    label: 'workflow.palette.events',
    types: ['wait_for_event'] as NodeType[],
  },
  {
    label: 'workflow.palette.terminal',
    types: ['end'] as NodeType[],
  },
]
