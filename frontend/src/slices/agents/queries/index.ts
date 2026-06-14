export const agentKeys = {
  agents: (projectId: string) =>
    ['agents', 'list', projectId] as const,
  agent: (agentId: string) =>
    ['agents', 'detail', agentId] as const,
  ragConfigs: (projectId: string) =>
    ['agents', 'ragConfigs', projectId] as const,
  ragConfig: (configId: string) =>
    ['agents', 'ragConfig', configId] as const,
  ragDocuments: (configId: string) =>
    ['agents', 'ragDocuments', configId] as const,
}
