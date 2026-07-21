import { useI18n } from 'vue-i18n'

import { RateLimitError, ValidationError } from '@shared/errors'

import { useToast } from './useToast'

/**
 * Turns an API error into feedback the user can act on, for the common
 * `onError: (err) => { if (!applyServerErrors(err)) toast.error(<domain msg>) }`
 * shape.
 *
 * `applyServerErrors` returns true when the user has been told what happened —
 * either inline on the offending field, or by a toast it raised itself — and
 * the caller should NOT add its own generic message.
 */
export function useServerErrors(setErrors: (fields: Record<string, string>) => void) {
  const { t } = useI18n()
  const toast = useToast()

  function applyServerErrors(err: unknown): boolean {
    // A 429 is not a fault in what the user submitted, and the caller's message
    // ("Failed to add MCP server") tells them nothing they can act on — it reads
    // as a broken feature and invites an immediate retry that is guaranteed to
    // fail again. Only the transport layer knows the retry window, so answer
    // here rather than letting the domain message win.
    if (err instanceof RateLimitError) {
      const seconds = Math.max(1, Math.ceil(err.retryAfterMs / 1000))
      toast.warning(t('shared.errors.rateLimited', { seconds }))
      return true
    }

    if (!(err instanceof ValidationError)) return false
    if (err.fieldErrors.length === 0) return false

    const mapped: Record<string, string> = {}
    for (const fe of err.fieldErrors) {
      mapped[fe.path] = fe.message
    }
    setErrors(mapped)
    return true
  }

  return { applyServerErrors }
}
