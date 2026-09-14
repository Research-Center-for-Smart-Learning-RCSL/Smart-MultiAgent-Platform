export const canvasKeys = {
  all: ['canvas'] as const,
  canvas: (chatroomId: string) => [...canvasKeys.all, 'detail', chatroomId] as const,
}
