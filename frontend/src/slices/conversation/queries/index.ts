// Stable query-key factory so invalidation from any call-site hits the
// correct cache entry. Conventions:
//   ['conversation', 'workspaces', projectId]
//   ['conversation', 'chatrooms', workspaceId]
//   ['conversation', 'chatrooms', 'recent', projectId]
//   ['conversation', 'messages', chatroomId]
//   ['conversation', 'search', chatroomId, q]
//
// `recentChatrooms` deliberately nests under the 'chatrooms' prefix so the
// broad `invalidateQueries(['conversation','chatrooms'])` (rename/delete in
// useChatroomSettings) also refreshes the project-wide recent list.

export const convKeys = {
  workspaces: (projectId: string) => ['conversation', 'workspaces', projectId] as const,
  workspace: (workspaceId: string) => ['conversation', 'workspace', workspaceId] as const,
  chatrooms: (workspaceId: string) => ['conversation', 'chatrooms', workspaceId] as const,
  recentChatrooms: (projectId: string) =>
    ['conversation', 'chatrooms', 'recent', projectId] as const,
  chatroom: (chatroomId: string) => ['conversation', 'chatroom', chatroomId] as const,
  messages: (chatroomId: string) => ['conversation', 'messages', chatroomId] as const,
  search: (chatroomId: string, q: string) =>
    ['conversation', 'search', chatroomId, q] as const,
  export: (jobId: string) => ['conversation', 'export', jobId] as const,
}
