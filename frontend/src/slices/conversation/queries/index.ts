// Stable query-key factory so invalidation from any call-site hits the
// correct cache entry. Conventions:
//   ['conversation', 'workspaces', projectId]
//   ['conversation', 'chatrooms', workspaceId]
//   ['conversation', 'messages', chatroomId]
//   ['conversation', 'search', chatroomId, q]

export const convKeys = {
  workspaces: (projectId: string) => ['conversation', 'workspaces', projectId] as const,
  chatrooms: (workspaceId: string) => ['conversation', 'chatrooms', workspaceId] as const,
  messages: (chatroomId: string) => ['conversation', 'messages', chatroomId] as const,
  search: (chatroomId: string, q: string) =>
    ['conversation', 'search', chatroomId, q] as const,
  export: (jobId: string) => ['conversation', 'export', jobId] as const,
}
