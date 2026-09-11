export const canvasKeys = {
  all: ['canvas'] as const,
  canvas: (chatroomId: string) => [...canvasKeys.all, 'detail', chatroomId] as const,
  objects: (chatroomId: string) => [...canvasKeys.all, 'objects', chatroomId] as const,
  snapshots: (chatroomId: string) => [...canvasKeys.all, 'snapshots', chatroomId] as const,
  comments: (chatroomId: string, objectId: string) =>
    [...canvasKeys.all, 'comments', chatroomId, objectId] as const,
  commentCounts: (chatroomId: string) =>
    [...canvasKeys.all, 'comment-counts', chatroomId] as const,
}
