/* eslint-disable vue/one-component-per-file -- Each createApp root isolates one handler invocation. */
import { createApp } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PermissionError, RateLimitError } from '@shared/errors'
import { i18n } from '@shared/i18n'
import en from '@shared/locales/en.json'
import zhTW from '@shared/locales/zh-TW.json'

const toast = vi.hoisted(() => ({
  error: vi.fn(),
  warning: vi.fn(),
  success: vi.fn(),
  info: vi.fn(),
}))

vi.mock('@shared/composables', () => ({ useToast: () => toast }))
vi.mock('../router', () => ({ router: { push: vi.fn() } }))

import { installErrorHandler } from '../errorHandler'

describe('global error feedback', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    i18n.global.mergeLocaleMessage('en', en)
    i18n.global.mergeLocaleMessage('zh-TW', zhTW)
  })

  it.each([
    ['en', 'You do not have permission to do that.'],
    ['zh-TW', '您沒有執行此操作的權限。'],
  ] as const)('uses a fixed localized permission message in %s', (locale, expected) => {
    i18n.global.locale.value = locale
    const app = createApp({})
    installErrorHandler(app)

    app.config.errorHandler?.(new PermissionError({
      type: 'https://smap.local/problems/forbidden',
      title: 'Forbidden',
      status: 403,
      detail: 'attacker-controlled backend detail',
    }), null, 'test')

    expect(toast.error).toHaveBeenCalledOnce()
    expect(toast.error).toHaveBeenCalledWith(expected)
    expect(toast.error).not.toHaveBeenCalledWith(expect.stringContaining('attacker-controlled'))
  })

  it('localizes rate limits and unexpected errors', () => {
    i18n.global.locale.value = 'en'
    const app = createApp({})
    installErrorHandler(app)

    app.config.errorHandler?.(new RateLimitError({
      type: 'https://smap.local/problems/rate-limit',
      title: 'Rate limited',
      status: 429,
    }, 1_500), null, 'test')
    expect(toast.warning).toHaveBeenCalledWith('Too many requests. Please retry in 2s.')

    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    app.config.errorHandler?.(new Error('boom'), null, 'test')
    expect(toast.error).toHaveBeenCalledWith('An unexpected error occurred. Please try again.')
  })
})
