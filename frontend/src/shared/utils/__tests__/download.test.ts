import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@shared/errors'
import { downloadFresh, triggerDownload } from '../download'

describe('triggerDownload', () => {
  afterEach(() => vi.restoreAllMocks())

  it('clicks an attached same-origin anchor with the download attribute, then removes it', () => {
    const clicked: HTMLAnchorElement[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.isConnected).toBe(true) // Firefox ignores a click on a detached anchor
      clicked.push(this)
    })

    triggerDownload('/api/files/abc.def')

    expect(clicked).toHaveLength(1)
    expect(clicked[0].getAttribute('href')).toBe('/api/files/abc.def')
    expect(clicked[0].hasAttribute('download')).toBe(true)
    expect(clicked[0].isConnected).toBe(false)
  })

  it('passes a filename through to the download attribute', () => {
    const clicked: HTMLAnchorElement[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this)
    })

    triggerDownload('/api/files/abc.def', 'room.md')

    expect(clicked[0].download).toBe('room.md')
  })

  it('downloads a Blob through a revoked object URL', () => {
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x')
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})

    triggerDownload(new Blob(['x']), 'canvas.png')

    expect(create).toHaveBeenCalledOnce()
    expect(revoke).toHaveBeenCalledWith('blob:x')
  })
})

describe('downloadFresh', () => {
  afterEach(() => vi.restoreAllMocks())

  const clicks = () => {
    const hrefs: string[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      hrefs.push(this.getAttribute('href') ?? '')
    })
    return hrefs
  }

  it('downloads the link the status call just minted', async () => {
    const hrefs = clicks()
    await expect(downloadFresh(async () => ({ status: 'ready', url: '/api/files/f.t' }))).resolves.toBe('ok')
    expect(hrefs).toEqual(['/api/files/f.t'])
  })

  it('reports a job the server no longer has as expired', async () => {
    const hrefs = clicks()
    const gone = new ApiError({ type: 'https://smap.local/problems/not-found', title: 'Not Found', status: 404 })
    await expect(downloadFresh(() => Promise.reject(gone))).resolves.toBe('expired')
    expect(hrefs).toEqual([])
  })

  it('reports a failed job or any other error as failed, not expired', async () => {
    clicks()
    await expect(downloadFresh(async () => ({ status: 'failed', url: null }))).resolves.toBe('failed')
    await expect(downloadFresh(() => Promise.reject(new Error('network')))).resolves.toBe('failed')
  })
})
