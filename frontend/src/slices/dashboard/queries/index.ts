export const dashboardKeys = {
  all: ['dashboard'] as const,
  summary: (workspaceId: string) => ['dashboard', 'summary', workspaceId] as const,
  timeseries: (workspaceId: string, window: string, bucket: string) =>
    ['dashboard', 'timeseries', workspaceId, window, bucket] as const,
  watchlist: (workspaceId: string) => ['dashboard', 'watchlist', workspaceId] as const,
}
