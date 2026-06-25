// Resolve the agents belonging to a workspace's project, as `{ id, name }`
// pairs for config-form selectors and backstage label maps.
//
// Cross-slice access goes through the public barrels of the `conversation`
// and `agents` slices (the allowed SoC boundary). Lives here so the workflow
// editor and the backstage view share one definition of the
// workspace -> project -> agents resolution instead of duplicating it.

export interface ProjectAgent {
  id: string
  name: string
}

export async function fetchProjectAgents(
  workspaceId: string,
): Promise<ProjectAgent[]> {
  const { getWorkspace } = await import('@slices/conversation')
  const ws = await getWorkspace(workspaceId)
  const { agentsApi } = await import('@slices/agents')
  const res = await agentsApi.list(ws.project_id)
  return res.data.map((a: { id: string; name: string }) => ({ id: a.id, name: a.name }))
}
