export const agentGroupKeys = {
  groups: (projectId: string) => ['agent-groups', 'list', projectId] as const,
  group: (groupId: string) => ['agent-groups', 'detail', groupId] as const,
  members: (groupId: string) => ['agent-groups', 'members', groupId] as const,
}
