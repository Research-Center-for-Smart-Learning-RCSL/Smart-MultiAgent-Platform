// Stable query-key factory so invalidation from any call-site hits the
// correct cache entry. Conventions:
//   ['conversation', 'workspaces', projectId]
//   ['conversation', 'chatrooms', workspaceId]
//   ['conversation', 'chatrooms', 'recent', projectId]
//   ['conversation', 'messages', chatroomId]
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
  // The producer. Its consumer is the prefix below rather than this entry,
  // because the only invalidator is the room-scoped `chatroom.updated` handler,
  // which has the room id and not the project id — but the *shape* has to be
  // defined once or the two drift apart invisibly in both directions, which is
  // F-1 exactly: a stale name renders as an 8-char id and `@mention` resolution
  // silently drops the wake.
  projectAgents: (projectId: string | undefined) =>
    ['conversation', 'project-agents', projectId] as const,
  // Matches every `projectAgents(...)` entry, including the transient
  // `[..., undefined]` one the call site holds before the workspace read lands.
  projectAgentsAll: () => ['conversation', 'project-agents'] as const,
  chatroom: (chatroomId: string) => ['conversation', 'chatroom', chatroomId] as const,
  // Added with F-1's `chatroom.updated` handler. This key had exactly one
  // reference in the whole frontend and no invalidator, which is how a
  // hand-written literal hides: it looks right at the one call site that has it.
  chatroomAgents: (chatroomId: string) =>
    ['conversation', 'chatroom-agents', chatroomId] as const,
  messages: (chatroomId: string) => ['conversation', 'messages', chatroomId] as const,
  observations: (chatroomId: string) =>
    ['conversation', 'observations', chatroomId] as const,
}
