import { describe, it, expect, beforeEach, vi } from 'vitest'

const toastMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('vue-sonner', () => ({ toast: toastMocks }))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      params ? `${key}:${JSON.stringify(params)}` : key,
  }),
}))

import { ApiError, RateLimitError, ValidationError } from '../../errors'
import { useServerErrors } from '../useServerErrors'

const PROBLEM = {
  type: 'https://smap.local/problems/rate-limited',
  title: 'Too Many Requests',
  status: 429,
}

beforeEach(() => {
  toastMocks.warning.mockClear()
  toastMocks.error.mockClear()
})

describe('useServerErrors', () => {
  it('maps server field errors onto the form and claims them', () => {
    const setErrors = vi.fn()
    const { applyServerErrors } = useServerErrors(setErrors)

    const handled = applyServerErrors(
      new ValidationError({
        type: 'https://smap.local/problems/validation',
        title: 'Validation Error',
        status: 422,
        field_errors: [{ path: 'config.reference', message: 'Not a valid URL.' }],
      }),
    )

    expect(handled).toBe(true)
    expect(setErrors).toHaveBeenCalledWith({ 'config.reference': 'Not a valid URL.' })
    expect(toastMocks.warning).not.toHaveBeenCalled()
  })

  // A 429 is not a fault in the submitted data. Letting it fall through to the
  // caller means the user is told the feature failed ("Failed to add MCP
  // server") instead of that they are over budget and for how long — and only
  // the transport layer knows the retry window.
  it('reports a rate limit with its retry window and claims it', () => {
    const setErrors = vi.fn()
    const { applyServerErrors } = useServerErrors(setErrors)

    const handled = applyServerErrors(new RateLimitError(PROBLEM, 30_000))

    expect(handled).toBe(true)
    expect(setErrors).not.toHaveBeenCalled()
    expect(toastMocks.warning).toHaveBeenCalledWith(
      'shared.errors.rateLimited:{"seconds":30}',
      expect.anything(),
    )
  })

  it('never tells the user to retry in 0s', () => {
    const { applyServerErrors } = useServerErrors(vi.fn())
    applyServerErrors(new RateLimitError(PROBLEM, 200))
    expect(toastMocks.warning).toHaveBeenCalledWith(
      'shared.errors.rateLimited:{"seconds":1}',
      expect.anything(),
    )
  })

  it('leaves anything it cannot explain to the caller', () => {
    const { applyServerErrors } = useServerErrors(vi.fn())

    expect(applyServerErrors(new Error('boom'))).toBe(false)
    expect(
      applyServerErrors(
        new ApiError({ type: 'about:blank', title: 'Server Error', status: 500 }),
      ),
    ).toBe(false)
    // A 422 with no field_errors has nothing to attach; the caller's domain
    // message is the only useful thing left to say.
    expect(
      applyServerErrors(
        new ValidationError({
          type: 'https://smap.local/problems/validation',
          title: 'Validation Error',
          status: 422,
        }),
      ),
    ).toBe(false)
  })
})
