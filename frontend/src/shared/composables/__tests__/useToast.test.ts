import { describe, it, expect, beforeEach, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('vue-sonner', () => ({ toast: mocks }))

import { useToast, TOAST_DURATION_MS } from '../useToast'

beforeEach(() => {
  mocks.success.mockClear()
  mocks.error.mockClear()
  mocks.warning.mockClear()
  mocks.info.mockClear()
})

describe('useToast', () => {
  it('applies the per-type duration to each severity', () => {
    const toast = useToast()
    toast.success('s')
    toast.error('e')
    toast.warning('w')
    toast.info('i')

    expect(mocks.success).toHaveBeenCalledWith(
      's',
      expect.objectContaining({ duration: TOAST_DURATION_MS.success }),
    )
    expect(mocks.error).toHaveBeenCalledWith(
      'e',
      expect.objectContaining({ duration: TOAST_DURATION_MS.error }),
    )
    expect(mocks.warning).toHaveBeenCalledWith(
      'w',
      expect.objectContaining({ duration: TOAST_DURATION_MS.warning }),
    )
    expect(mocks.info).toHaveBeenCalledWith(
      'i',
      expect.objectContaining({ duration: TOAST_DURATION_MS.info }),
    )
  })

  it('errors linger longer than successes', () => {
    expect(TOAST_DURATION_MS.error).toBeGreaterThan(TOAST_DURATION_MS.success)
  })

  it('forwards an optional description', () => {
    useToast().error('boom', { description: 'the server said no' })
    expect(mocks.error).toHaveBeenCalledWith(
      'boom',
      expect.objectContaining({ description: 'the server said no' }),
    )
  })
})
