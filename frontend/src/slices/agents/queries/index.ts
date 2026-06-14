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
  graphragConfigs: (projectId: string) =>
    ['agents', 'graphragConfigs', projectId] as const,
  graphragConfig: (configId: string) =>
    ['agents', 'graphragConfig', configId] as const,
  mcpBindings: (agentId: string) =>
    ['agents', 'mcpBindings', agentId] as const,
  egressAllowlist: (projectId: string) =>
    ['agents', 'egressAllowlist', projectId] as const,
}
