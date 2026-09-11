export const canvasKeys = {
  all: ['canvas'] as const,
  canvas: (chatroomId: string) => [...canvasKeys.all, 'detail', chatroomId] as const,
  objects: (chatroomId: string) => [...canvasKeys.all, 'objects', chatroomId] as const,
  snapshots: (chatroomId: string) => [...canvasKeys.all, 'snapshots', chatroomId] as const,
  snapshotDetail: (chatroomId: string, snapshotId: string) =>
    [...canvasKeys.all, 'snapshot-detail', chatroomId, snapshotId] as const,
  comments: (chatroomId: string, objectId: string) =>
    [...canvasKeys.all, 'comments', chatroomId, objectId] as const,
  commentCounts: (chatroomId: string) =>
    [...canvasKeys.all, 'comment-counts', chatroomId] as const,
  search: (chatroomId: string, query: string) =>
    [...canvasKeys.all, 'search', chatroomId, query] as const,
}
