import { afterEach, describe, expect, it, vi } from 'vitest'
import { triggerDownload } from '../download'

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
    const create = vi.fn(() => 'blob:x')
    const revoke = vi.fn()
    URL.createObjectURL = create
    URL.revokeObjectURL = revoke

    triggerDownload(new Blob(['x']), 'canvas.png')

    expect(create).toHaveBeenCalledOnce()
    expect(revoke).toHaveBeenCalledWith('blob:x')
  })
})
