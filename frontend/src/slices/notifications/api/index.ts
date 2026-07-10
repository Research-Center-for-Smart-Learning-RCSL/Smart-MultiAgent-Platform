import { NotificationsService } from '@shared/api-client'
import type { NotificationOut, MarkReadOut, UnreadCountOut } from '@shared/api-client'

// R18: in-app only, persisted + pushed over /ws/user/{id}; the bell badge
// reads unread-count. Re-exported under slice-local names so call sites
// don't need to know these come from the generated client.
export type Notification = NotificationOut
export type UnreadCount = UnreadCountOut
export type MarkReadResult = MarkReadOut

// Thin use-case-shaped wrapper over the generated NotificationsService
// (R24.13) — the request/response shapes and the auth/session behavior
// (bearer token, silent 401 refresh, problem+json error typing) come from
// shared/transport/axios.ts's instrumentation of the bare axios singleton
// the generated client calls into.
export const notificationsApi = {
  // Cursor pagination (R18 / §22.12): omit `cursor` for the first page; pass the
  // last item's id to page back. The backend caps `limit` at 200.
  list: (cursor?: string, limit = 50): Promise<Notification[]> =>
    NotificationsService.listNotificationsApiNotificationsGet({
      cursor: cursor ?? null,
      limit,
    }),

  markRead: (ids: string[]): Promise<MarkReadResult> =>
    NotificationsService.markReadApiNotificationsReadPost({ requestBody: { ids } }),

  unreadCount: (): Promise<UnreadCount> =>
    NotificationsService.unreadCountApiNotificationsUnreadCountGet(),
}
