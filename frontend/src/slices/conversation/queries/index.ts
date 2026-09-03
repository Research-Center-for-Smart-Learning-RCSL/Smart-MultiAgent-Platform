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
  // --- prefixes, for invalidation only ------------------------------------
  // These name a *branch* of the cache, not an entry. Never pass one to
  // `useQuery` or `setQueryData`: that would create a real cache entry no
  // `queryFn` feeds, sitting under the same prefix as the entries it was meant
  // to refresh. They exist because the alternative is hand-writing the prefix at
  // every call site, which is the hazard the `chatroomAgents` note below records.
  //
  // `chatroomsAll` covers both `chatrooms(wsId)` and `recentChatrooms(pid)`, per
  // the nesting described above. A room create/delete has to reach both: the
  // workspace's list and the project-wide recent rail, which is the one query in
  // the slice with a staleTime and lives in a sidebar that never unmounts, so it
  // has neither a mount refetch nor a focus refetch inside 60s to fall back on.
  chatroomsAll: () => ['conversation', 'chatrooms'] as const,
  // `projectAgentsAll` is a prefix rather than `projectAgents(projectId)` because
  // its only invalidator is the room-scoped `chatroom.updated` handler, which has
  // the room id and not the project id. It also matches the transient
  // `['conversation','project-agents', undefined]` entry the call site's computed
  // key produces before the workspace read lands, which an exact key would miss.
  projectAgentsAll: () => ['conversation', 'project-agents'] as const,
  chatroom: (chatroomId: string) => ['conversation', 'chatroom', chatroomId] as const,
  // Added with F-1's `chatroom.updated` handler. This key had exactly one
  // reference in the whole frontend and no invalidator, which is how a
  // hand-written literal hides: it looks right at the one call site that has it.
  chatroomAgents: (chatroomId: string) =>
    ['conversation', 'chatroom-agents', chatroomId] as const,
  messages: (chatroomId: string) => ['conversation', 'messages', chatroomId] as const,
  search: (chatroomId: string, q: string) =>
    ['conversation', 'search', chatroomId, q] as const,
  export: (jobId: string) => ['conversation', 'export', jobId] as const,
  observations: (chatroomId: string) =>
    ['conversation', 'observations', chatroomId] as const,
}
