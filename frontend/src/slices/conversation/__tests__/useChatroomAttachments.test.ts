// Chat attachments no longer wait for a project id
// (docs/tasks/2026-10-05-guest-identity-foreign-keys, AC-8).
//
// The backend derives a chat attachment's project from the room
// (backend/app/api/v1/tus.py, `chat_attachment` branch) and ignores the client's.
// Requiring one client-side stopped every anonymous guest, who can never resolve
// it, with an "attachments aren't ready" toast before any request was sent.

import { describe, it, expect, vi, beforeEach } from 'vitest'

const mockToast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }))
const tusUpload = vi.hoisted(() => vi.fn())

vi.mock('@shared/composables', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return { ...actual, useToast: () => mockToast }
})
vi.mock('vue-i18n', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return { ...actual, useI18n: () => ({ t: (k: string) => k }) }
})
vi.mock('@shared/transport', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  tusUpload,
}))

import { useChatroomAttachments } from '../composables/useChatroomAttachments'

const ATTACHMENT_ID = '11111111-2222-3333-4444-555555555555'

describe('useChatroomAttachments', () => {
  beforeEach(() => {
    mockToast.error.mockReset()
    tusUpload.mockReset()
    tusUpload.mockResolvedValue({ uploadId: 'u', resourceHeader: `/api/attachments/${ATTACHMENT_ID}` })
  })

  it('uploads with only the room, so a viewer with no project still can', async () => {
    const { uploadFiles, pendingUploads, attachmentIds } = useChatroomAttachments('room-1')

    await uploadFiles([new File(['hello'], 'notes.txt', { type: 'text/plain' })])

    expect(mockToast.error).not.toHaveBeenCalled()
    expect(tusUpload).toHaveBeenCalledTimes(1)
    const opts = tusUpload.mock.calls[0]?.[0] as Record<string, unknown>
    expect(opts).toMatchObject({ purpose: 'chat_attachment', chatroomId: 'room-1' })
    expect(opts.projectId).toBeUndefined()
    expect(pendingUploads.value[0]?.status).toBe('ready')
    expect(attachmentIds()).toEqual([ATTACHMENT_ID])
  })
})
