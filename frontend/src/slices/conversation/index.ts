// Public surface of the conversation slice. Only exports listed here are
// importable from other slices (enforced in Phase J via eslint-plugin-
// boundaries).

export { conversationRoutes } from './routes'
export { useConversationStore } from './stores/conversation'
export { useChatroomSocket } from './composables/useChatroomSocket'
export { renderMarkdown, enhanceRenderedMarkdown } from './lib/renderMarkdown'
export type {
  Attachment,
  Chatroom,
  ChatroomEvent,
  ChatroomEventType,
  ExportStatus,
  Message,
  SearchHit,
  SearchResponse,
  SenderType,
  Workspace,
} from './types'

export function installConversationSlice(): void {
  // Parity with other slices; no install-time side effects needed — the
  // Pinia store is lazily registered on first use.
}
