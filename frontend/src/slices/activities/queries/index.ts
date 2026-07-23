// Stable query-key factory so invalidation from any call-site hits the right
// cache entry. Conventions:
//   ['activities', 'types', projectId]
//   ['activities', 'submissions', chatroomId, sessionId|null]

export const activityKeys = {
  types: (projectId: string) => ['activities', 'types', projectId] as const,
  // Process-global first-party validators (GET /api/activity-validators); no
  // project scope — the registry is the same for every project.
  validators: () => ['activities', 'validators'] as const,
  activeActivation: (chatroomId: string) => ['activities', 'activation', chatroomId] as const,
  submissions: (chatroomId: string, sessionId?: string | null) =>
    ['activities', 'submissions', chatroomId, sessionId ?? null] as const,
}
