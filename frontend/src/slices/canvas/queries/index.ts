export const canvasKeys = {
  all: ['canvas'] as const,
  canvas: (chatroomId: string) => [...canvasKeys.all, 'detail', chatroomId] as const,
  snapshots: (chatroomId: string) => [...canvasKeys.all, 'snapshots', chatroomId] as const,
  snapshotDetail: (chatroomId: string, snapshotId: string) =>
    [...canvasKeys.all, 'snapshot-detail', chatroomId, snapshotId] as const,
  search: (chatroomId: string, query: string) =>
    [...canvasKeys.all, 'search', chatroomId, query] as const,
}
