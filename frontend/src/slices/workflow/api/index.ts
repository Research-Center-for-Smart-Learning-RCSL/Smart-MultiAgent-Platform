// Workflow + Orchestration API — H.7 / G.10.

import { http } from '@shared/transport'
import type {
  Approval,
  ApprovalWithVotes,
  DlqEntry,
  Instruction,
  AgentInstance,
  ValidationResult,
  Workflow,
  WorkflowRun,
  WorkflowStep,
  WorkflowDefinition,
  WakeupConfig,
} from '../types'

// ---- Workflow CRUD (H.1 / §22.11) -----------------------------------------

export async function listWorkflows(workspaceId: string): Promise<Workflow[]> {
  const { data } = await http.get<Workflow[]>(
    `/workspaces/${workspaceId}/workflows`,
  )
  return data
}

export async function createWorkflow(
  workspaceId: string,
  payload: { name: string; definition: WorkflowDefinition },
): Promise<Workflow> {
  const { data } = await http.post<Workflow>(
    `/workspaces/${workspaceId}/workflows`,
    payload,
  )
  return data
}

export async function patchWorkflow(
  workflowId: string,
  version: number,
  payload: { name?: string; definition?: WorkflowDefinition },
): Promise<Workflow> {
  const { data } = await http.patch<Workflow>(
    `/workflows/${workflowId}`,
    payload,
    { headers: { 'If-Match': String(version) } },
  )
  return data
}

export async function deleteWorkflow(workflowId: string): Promise<void> {
  await http.delete(`/workflows/${workflowId}`)
}

export async function validateWorkflow(
  workspaceId: string,
  definition: WorkflowDefinition,
): Promise<ValidationResult> {
  const { data } = await http.post<ValidationResult>(
    `/workspaces/${workspaceId}/workflows/validate`,
    { definition },
  )
  return data
}

// ---- Runs (H.4) ------------------------------------------------------------

export async function triggerRun(
  workflowId: string,
  triggerPayload: Record<string, unknown> = {},
): Promise<{ run_id: string }> {
  const { data } = await http.post<{ run_id: string }>(
    `/workflows/${workflowId}/runs`,
    { trigger_payload: triggerPayload },
  )
  return data
}

export async function listRuns(
  workflowId: string,
  opts: { limit?: number; offset?: number; includeArchive?: boolean } = {},
): Promise<WorkflowRun[]> {
  const { data } = await http.get<WorkflowRun[]>(
    `/workflows/${workflowId}/runs`,
    {
      params: {
        limit: opts.limit ?? 50,
        offset: opts.offset ?? 0,
        include_archive: opts.includeArchive ?? false,
      },
    },
  )
  return data
}

export async function getRun(runId: string): Promise<WorkflowRun> {
  const { data } = await http.get<WorkflowRun>(`/workflow-runs/${runId}`)
  return data
}

export async function cancelRun(
  runId: string,
): Promise<{ status: string }> {
  const { data } = await http.post<{ status: string }>(
    `/workflow-runs/${runId}/cancel`,
  )
  return data
}

export async function listSteps(runId: string): Promise<WorkflowStep[]> {
  const { data } = await http.get<WorkflowStep[]>(
    `/workflow-runs/${runId}/steps`,
  )
  return data
}

// ---- Approvals (G.6) -------------------------------------------------------

export async function getApproval(approvalId: string): Promise<ApprovalWithVotes> {
  const { data } = await http.get<ApprovalWithVotes>(
    `/orchestration/approvals/${approvalId}`,
  )
  return data
}

export async function listApprovalsForRun(
  workflowRunId: string,
): Promise<Approval[]> {
  const { data } = await http.get<Approval[]>(
    `/orchestration/workflow-runs/${workflowRunId}/approvals`,
  )
  return data
}

// ---- Instruct chains (G.7 — admin only) ------------------------------------

export async function getInstruction(
  instructionId: string,
): Promise<Instruction> {
  const { data } = await http.get<Instruction>(
    `/orchestration/instructions/${instructionId}`,
  )
  return data
}

export async function listInstructionsForChain(
  chainId: string,
): Promise<Instruction[]> {
  const { data } = await http.get<Instruction[]>(
    `/orchestration/chains/${chainId}/instructions`,
  )
  return data
}

// ---- Sub-agents (G.8) -------------------------------------------------------

export async function listSubagents(
  parentInstanceId: string,
): Promise<AgentInstance[]> {
  const { data } = await http.get<AgentInstance[]>(
    `/orchestration/instances/${parentInstanceId}/children`,
  )
  return data
}

// ---- DLQ viewer (G.10) -----------------------------------------------------

export async function listDlq(agentId: string): Promise<DlqEntry[]> {
  const { data } = await http.get<DlqEntry[]>(
    `/orchestration/agents/${agentId}/dlq`,
  )
  return data
}

// ---- Agent wakeup config (Phase H) -----------------------------------------

export async function patchAgentWakeupConfig(
  agentId: string,
  wakeupConfig: WakeupConfig,
): Promise<void> {
  await http.patch(`/agents/${agentId}`, { wakeup_config: wakeupConfig })
}
