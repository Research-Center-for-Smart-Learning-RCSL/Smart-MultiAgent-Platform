// Workflow + Orchestration API — H.7 / G.10.
//
// Wraps the generated @shared/api-client services (WorkflowsService,
// WorkflowRunsService, OrchestrationService, AgentsService) over the one
// instrumented axios singleton. The functions already returned bare bodies, so
// this conversion is signature-preserving — consumers are untouched. Where a
// generated return is loosely typed (the orchestration routes resolve
// `Record<string, any>`; validate/trigger/cancel resolve `Record<string, string>`),
// the wrapper casts to the hand-rolled slice type — the same unchecked assertion
// the previous `http.get<T>()` calls already made, relocated to this boundary.

import {
  AgentsService,
  OrchestrationService,
  WorkflowRunsService,
  WorkflowsService,
} from '@shared/api-client'
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
  return (await WorkflowsService.listWorkflowsApiWorkspacesWidWorkflowsGet({
    wid: workspaceId,
  })) as Workflow[]
}

export async function createWorkflow(
  workspaceId: string,
  payload: { name: string; definition: WorkflowDefinition },
): Promise<Workflow> {
  return (await WorkflowsService.createWorkflowApiWorkspacesWidWorkflowsPost({
    wid: workspaceId,
    requestBody: payload,
  })) as Workflow
}

export async function patchWorkflow(
  workflowId: string,
  version: number,
  payload: { name?: string; definition?: WorkflowDefinition },
): Promise<Workflow> {
  return (await WorkflowsService.patchWorkflowApiWorkflowsWorkflowIdPatch({
    workflowId,
    ifMatch: String(version),
    requestBody: payload,
  })) as Workflow
}

export async function deleteWorkflow(workflowId: string): Promise<void> {
  await WorkflowsService.deleteWorkflowApiWorkflowsWorkflowIdDelete({ workflowId })
}

export async function validateWorkflow(
  workspaceId: string,
  definition: WorkflowDefinition,
): Promise<ValidationResult> {
  return (await WorkflowsService.validateWorkflowApiWorkspacesWidWorkflowsValidatePost({
    wid: workspaceId,
    requestBody: { definition },
  })) as ValidationResult
}

// ---- Runs (H.4) ------------------------------------------------------------

export async function triggerRun(
  workflowId: string,
  triggerPayload: Record<string, unknown> = {},
): Promise<{ run_id: string }> {
  return (await WorkflowsService.triggerRunApiWorkflowsWorkflowIdRunsPost({
    workflowId,
    requestBody: { trigger_payload: triggerPayload },
  })) as { run_id: string }
}

export async function dryRun(
  workflowId: string,
  triggerPayload: Record<string, unknown> = {},
): Promise<{ run_id: string }> {
  return (await WorkflowsService.dryRunApiWorkflowsWorkflowIdDryRunPost({
    workflowId,
    requestBody: { trigger_payload: triggerPayload },
  })) as { run_id: string }
}

export async function listRuns(
  workflowId: string,
  opts: { limit?: number; offset?: number; includeArchive?: boolean } = {},
): Promise<WorkflowRun[]> {
  return (await WorkflowsService.listRunsApiWorkflowsWorkflowIdRunsGet({
    workflowId,
    limit: opts.limit ?? 50,
    offset: opts.offset ?? 0,
    includeArchive: opts.includeArchive ?? false,
  })) as WorkflowRun[]
}

export function getRun(runId: string): Promise<WorkflowRun> {
  return WorkflowRunsService.getRunApiWorkflowRunsRunIdGet({ runId })
}

export async function cancelRun(runId: string): Promise<{ status: string }> {
  return (await WorkflowRunsService.cancelRunApiWorkflowRunsRunIdCancelPost({
    runId,
  })) as { status: string }
}

export function listSteps(runId: string): Promise<WorkflowStep[]> {
  return WorkflowRunsService.listStepsApiWorkflowRunsRunIdStepsGet({ runId })
}

// ---- Approvals (G.6) -------------------------------------------------------

export function getApproval(approvalId: string): Promise<ApprovalWithVotes> {
  return OrchestrationService.getApprovalApiOrchestrationApprovalsApprovalIdGet({ approvalId })
}

export function listApprovalsForRun(workflowRunId: string): Promise<Approval[]> {
  return OrchestrationService.listApprovalsForRunApiOrchestrationWorkflowRunsWorkflowRunIdApprovalsGet(
    { workflowRunId },
  )
}

// ---- Instruct chains (G.7 — admin only) ------------------------------------

export function getInstruction(instructionId: string): Promise<Instruction> {
  return OrchestrationService.getInstructionApiOrchestrationInstructionsInstructionIdGet({
    instructionId,
  })
}

export function listInstructionsForChain(chainId: string): Promise<Instruction[]> {
  return OrchestrationService.listInstructionsForChainApiOrchestrationChainsChainIdInstructionsGet(
    { chainId },
  )
}

// ---- Sub-agents (G.8) -------------------------------------------------------

// All sub-agents spawned during a workflow run (alive + destroyed), for the
// admin backstage trace where the run has usually finished and its sub-agents
// are already torn down. (The alive-only `/instances/{id}/children` endpoint
// exists server-side — OrchestrationService.listSubagentChildren… — but has no
// frontend consumer.)
export function listRunSubagents(runId: string): Promise<AgentInstance[]> {
  return OrchestrationService.listRunSubagentsApiOrchestrationWorkflowRunsWorkflowRunIdSubagentsGet(
    { workflowRunId: runId },
  )
}

// ---- DLQ viewer (G.10) -----------------------------------------------------

export function listDlq(agentId: string): Promise<DlqEntry[]> {
  return OrchestrationService.getAgentDlqApiOrchestrationAgentsAgentIdDlqGet({ agentId })
}

// ---- Agent wakeup config (Phase H) -----------------------------------------

// The agent's stored wakeup_config defaults to `{}` (server_default), so callers
// must default-fill before handing it to the structured editor. The `?? {}` is a
// deliberate boundary guard against a null body (kept though the generated AgentOut
// types the field required — see the covering test). The version is returned too:
// the agent PATCH requires an `If-Match` precondition.
export async function getAgentWakeupConfig(
  agentId: string,
): Promise<{ wakeupConfig: unknown; version: number }> {
  const agent = await AgentsService.readAgentApiAgentsAgentIdGet({ agentId })
  return { wakeupConfig: agent.wakeup_config ?? {}, version: agent.version }
}

// Returns the bumped version so the caller can save again without a refetch.
export async function patchAgentWakeupConfig(
  agentId: string,
  wakeupConfig: WakeupConfig,
  version: number,
): Promise<number> {
  const agent = await AgentsService.patchAgentApiAgentsAgentIdPatch({
    agentId,
    ifMatch: String(version),
    requestBody: { wakeup_config: wakeupConfig },
  })
  return agent.version
}
