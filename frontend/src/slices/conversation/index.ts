// Public surface of the conversation slice. Only exports listed here are
// importable from other slices (enforced in Phase J via eslint-plugin-
// boundaries).

export { conversationRoutes } from './routes'
export { convKeys } from './queries'
export { useConversationStore } from './stores/conversation'
export { useChatroomSocket } from './composables/useChatroomSocket'
export { getWorkspace, listChatrooms, listWorkspaces } from './api'
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

import { registerLocaleLoaders } from '@shared/i18n'

export function installConversationSlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}
