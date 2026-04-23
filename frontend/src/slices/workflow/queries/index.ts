export const wfKeys = {
  workflows: (workspaceId: string) =>
    ['workflow', 'list', workspaceId] as const,
  workflow: (workflowId: string) =>
    ['workflow', 'detail', workflowId] as const,
  runs: (workflowId: string) =>
    ['workflow', 'runs', workflowId] as const,
  run: (runId: string) =>
    ['workflow', 'run', runId] as const,
  steps: (runId: string) =>
    ['workflow', 'steps', runId] as const,
  approvals: (runId: string) =>
    ['workflow', 'approvals', runId] as const,
  validation: (workspaceId: string) =>
    ['workflow', 'validation', workspaceId] as const,
}
