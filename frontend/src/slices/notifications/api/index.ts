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

// Methods unwrap the transport response (`.then(r => r.data)`) so callers receive
// domain data directly — the raw response shape stays inside the api-client layer.
export const notificationsApi = {
  // Cursor pagination (R18 / §22.12): omit `cursor` for the first page; pass the
  // last item's id to page back. The backend caps `limit` at 200.
  list: (cursor?: string, limit = 50) =>
    http
      .get<Notification[]>('/notifications', {
        params: { ...(cursor ? { cursor } : {}), limit },
      })
      .then((r) => r.data),

  markRead: (ids: string[]) =>
    http.post<MarkReadResult>('/notifications/read', { ids }).then((r) => r.data),

  unreadCount: () => http.get<UnreadCount>('/notifications/unread-count').then((r) => r.data),
}
