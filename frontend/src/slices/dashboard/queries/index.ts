export const dashboardKeys = {
  all: ['dashboard'] as const,
  summary: (projectId: string) => ['dashboard', 'summary', projectId] as const,
  timeseries: (projectId: string, window: string, bucket: string) =>
    ['dashboard', 'timeseries', projectId, window, bucket] as const,
  watchlist: (projectId: string) => ['dashboard', 'watchlist', projectId] as const,
}
