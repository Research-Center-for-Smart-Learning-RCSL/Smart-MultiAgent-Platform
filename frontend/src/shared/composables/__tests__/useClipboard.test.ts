import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useClipboard } from '../useClipboard'

function stubClipboard(writeText: unknown): void {
  Object.defineProperty(navigator, 'clipboard', {
    value: writeText === undefined ? undefined : { writeText },
    configurable: true,
    writable: true,
  })
}

describe('useClipboard', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    stubClipboard(undefined)
  })

  it('writes the text and reports success', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    stubClipboard(writeText)

    const { copy, copied } = useClipboard()
    await expect(copy('https://example.invalid/#token=abc')).resolves.toBe(true)

    expect(writeText).toHaveBeenCalledWith('https://example.invalid/#token=abc')
    expect(copied.value).toBe(true)
  })

  it('clears the copied flag after the reset window', async () => {
    stubClipboard(vi.fn().mockResolvedValue(undefined))

    const { copy, copied } = useClipboard(50)
    await copy('x')
    expect(copied.value).toBe(true)

    vi.advanceTimersByTime(50)
    expect(copied.value).toBe(false)
  })

  // The three cases a bare `navigator.clipboard.writeText(...)` gets wrong.
  it('reports failure when the Clipboard API is absent', async () => {
    stubClipboard(undefined)

    const { copy, copied } = useClipboard()
    await expect(copy('x')).resolves.toBe(false)
    expect(copied.value).toBe(false)
  })

  it('reports failure when the write is refused', async () => {
    stubClipboard(vi.fn().mockRejectedValue(new Error('NotAllowedError')))

    const { copy, copied } = useClipboard()
    await expect(copy('x')).resolves.toBe(false)
    expect(copied.value).toBe(false)
  })

  it('does not leave a stale copied flag when a later copy fails', async () => {
    const writeText = vi.fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error('NotAllowedError'))
    stubClipboard(writeText)

    const { copy, copied } = useClipboard(1_000)
    await copy('first')
    expect(copied.value).toBe(true)

    await copy('second')
    expect(copied.value).toBe(false)

    // The first copy's pending reset timer must not fire against the second
    // call's state — it was cleared, not left to expire later.
    vi.advanceTimersByTime(2_000)
    expect(copied.value).toBe(false)
  })
})
