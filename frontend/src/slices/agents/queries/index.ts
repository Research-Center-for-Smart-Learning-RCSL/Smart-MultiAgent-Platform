export const agentKeys = {
  agents: (projectId: string) =>
    ['agents', 'list', projectId] as const,
  agent: (agentId: string) =>
    ['agents', 'detail', agentId] as const,
  ragConfigs: (projectId: string) =>
    ['agents', 'ragConfigs', projectId] as const,
}
