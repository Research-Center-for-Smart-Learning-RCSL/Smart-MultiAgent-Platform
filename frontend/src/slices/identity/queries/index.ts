export const identityKeys = {
  me: () => ['identity', 'me'] as const,
  sessions: () => ['identity', 'sessions'] as const,
}
