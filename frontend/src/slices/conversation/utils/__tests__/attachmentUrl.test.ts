import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getAttachment = vi.fn()
vi.mock('../../api', () => ({ getAttachment }))

const { fetchAttachmentUrl, invalidateAttachmentUrl, resetAttachmentUrlCache } = await import(
  '../attachmentUrl'
)

describe('attachment link memo', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    resetAttachmentUrlCache()
    getAttachment.mockReset()
  })
  afterEach(() => vi.useRealTimers())

  it('shares one fetch between bubbles while the link is fresh', async () => {
    getAttachment.mockResolvedValue({ url: '/api/files/a.1', expires_in: 900 })
    await expect(fetchAttachmentUrl('a')).resolves.toBe('/api/files/a.1')
    await expect(fetchAttachmentUrl('a')).resolves.toBe('/api/files/a.1')
    expect(getAttachment).toHaveBeenCalledTimes(1)
  })

  it('refetches once the link is within a minute of expiring', async () => {
    getAttachment
      .mockResolvedValueOnce({ url: '/api/files/a.1', expires_in: 900 })
      .mockResolvedValueOnce({ url: '/api/files/a.2', expires_in: 900 })
    await fetchAttachmentUrl('a')
    vi.advanceTimersByTime(841_000)
    await expect(fetchAttachmentUrl('a')).resolves.toBe('/api/files/a.2')
    expect(getAttachment).toHaveBeenCalledTimes(2)
  })

  it('does not remember a failed fetch', async () => {
    getAttachment
      .mockRejectedValueOnce(new Error('429'))
      .mockResolvedValueOnce({ url: '/api/files/a.1', expires_in: 900 })
    await expect(fetchAttachmentUrl('a')).resolves.toBeNull()
    await expect(fetchAttachmentUrl('a')).resolves.toBe('/api/files/a.1')
  })

  it('invalidate forces the next call to refetch', async () => {
    getAttachment.mockResolvedValue({ url: '/api/files/a.1', expires_in: 900 })
    await fetchAttachmentUrl('a')
    invalidateAttachmentUrl('a')
    await fetchAttachmentUrl('a')
    expect(getAttachment).toHaveBeenCalledTimes(2)
  })
})
