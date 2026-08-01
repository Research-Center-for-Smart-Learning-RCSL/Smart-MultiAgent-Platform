// Resolve the agents belonging to a workspace's project, as `{ id, name }`
// pairs for config-form selectors and backstage label maps.
//
// Cross-slice access goes through the public barrel of the `agents` slice
// (the allowed SoC boundary); the workspace read uses workflow's own thin
// wrapper because conversation sits above workflow in the dependency
// direction. Lives here so the workflow editor and the backstage view share
// one definition of the workspace -> project -> agents resolution instead of
// duplicating it.

export interface ProjectAgent {
  id: string
  name: string
  // Read from workflow_capabilities so role pickers can flag an agent that
  // lacks the capability its role requires (advisory only — the runtime gate
  // lives server-side; see workflow-capability-enforcement spec §7.4).
  canInstruct: boolean
  canApprove: boolean
  canCreateSubagent: boolean
}

export async function fetchProjectAgents(
  workspaceId: string,
): Promise<ProjectAgent[]> {
  const { getWorkspace } = await import('../api')
  const ws = await getWorkspace(workspaceId)
  const { agentsApi } = await import('@slices/agents')
  const res = await agentsApi.list(ws.project_id)
  return res.map((a) => ({
    id: a.id,
    name: a.name,
    canInstruct: Boolean(a.workflow_capabilities?.can_instruct),
    canApprove: Boolean(a.workflow_capabilities?.can_approve),
    canCreateSubagent: Boolean(a.workflow_capabilities?.can_create_subagent),
  }))
}
