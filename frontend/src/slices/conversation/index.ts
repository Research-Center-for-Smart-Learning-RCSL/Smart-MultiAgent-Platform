// Public surface of the conversation slice. Only exports listed here are
// importable from other slices (enforced in Phase J via eslint-plugin-
// boundaries).

export { conversationRoutes } from './routes'
export { convKeys } from './queries'
export { useConversationStore } from './stores/conversation'
export { useChatroomSocket } from './composables/useChatroomSocket'
export { renderMarkdown, enhanceRenderedMarkdown } from './lib/renderMarkdown'
export { getWorkspace, listChatrooms } from './api'
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

import { registerMessages } from '@shared/i18n'
import enMessages from './locales/en.json'
import zhMessages from './locales/zh-TW.json'

export function installConversationSlice(): void {
  registerMessages('en', enMessages)
  registerMessages('zh-TW', zhMessages)
}
