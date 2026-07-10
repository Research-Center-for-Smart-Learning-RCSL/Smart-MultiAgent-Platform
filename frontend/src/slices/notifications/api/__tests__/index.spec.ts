import { describe, expect, it } from 'vitest'
import { http as mswHttp, HttpResponse } from 'msw'
import { server } from '../../../../../tests/mocks/server'
import { notificationsApi } from '..'

// unreadCount() has no other test coverage anywhere in the slice. Pinned here
// before docs/tasks/2026-07-10-openapi-resync-generated-client-auth/spec.md's
// conversion to the generated NotificationsService (step 8) — this must keep
// passing unmodified after that change.
describe('notificationsApi.unreadCount', () => {
  it('GETs /notifications/unread-count and unwraps the response body', async () => {
    server.use(
      mswHttp.get('/api/notifications/unread-count', () => HttpResponse.json({ count: 7 })),
    )

    const result = await notificationsApi.unreadCount()

    expect(result).toEqual({ count: 7 })
  })
})
