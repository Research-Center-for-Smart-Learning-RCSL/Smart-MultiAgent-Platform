export const keysKeys = {
  myKeys: () => ['keys', 'myKeys'] as const,
  keyProjects: (keyId: string) =>
    ['keys', 'keyProjects', keyId] as const,
  keyGroups: (projectId: string) =>
    ['keys', 'keyGroups', projectId] as const,
  keyGroup: (groupId: string) =>
    ['keys', 'keyGroup', groupId] as const,
  projectKeys: (projectId: string) =>
    ['keys', 'projectKeys', projectId] as const,
  searchKeys: (projectId: string) =>
    ['keys', 'searchKeys', projectId] as const,
}
