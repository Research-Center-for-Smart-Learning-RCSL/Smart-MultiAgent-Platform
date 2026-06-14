import { http } from '@shared/transport'

// Mirrors backend `NotificationOut` (backend/app/api/v1/notifications.py). R18:
// in-app only, persisted + pushed over /ws/user/{id}; the bell badge reads
// unread-count.
export interface Notification {
  id: string
  kind: string
  title: string
  body: string | null
  metadata: Record<string, unknown>
  read_at: string | null
  created_at: string
}

export interface UnreadCount {
  count: number
}

export interface MarkReadResult {
  marked: number
}

export const notificationsApi = {
  // Cursor pagination (R18 / §22.12): omit `cursor` for the first page; pass the
  // last item's id to page back. The backend caps `limit` at 200.
  list: (cursor?: string, limit = 50) =>
    http.get<Notification[]>('/notifications', {
      params: { ...(cursor ? { cursor } : {}), limit },
    }),

  markRead: (ids: string[]) =>
    http.post<MarkReadResult>('/notifications/read', { ids }),

  unreadCount: () => http.get<UnreadCount>('/notifications/unread-count'),
}
