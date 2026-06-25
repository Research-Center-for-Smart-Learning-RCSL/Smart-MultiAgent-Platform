import type { App } from 'vue'
import { toast } from 'vue-sonner'
import { AuthError, PermissionError, ValidationError, RateLimitError, NetworkError } from '@shared/errors'
import { TOAST_DURATION_MS } from '@shared/composables'
import { router } from './router'

function handleError(err: unknown): boolean {
  if (err instanceof AuthError) {
    router.push({ name: 'identity.login' })
    return true
  }
  if (err instanceof PermissionError) {
    toast.error(err.detail ?? err.title, { duration: TOAST_DURATION_MS.error })
    return true
  }
  if (err instanceof ValidationError) {
    return true
  }
  if (err instanceof RateLimitError) {
    const seconds = Math.ceil(err.retryAfterMs / 1000)
    toast.warning(`Rate limited. Please retry in ${seconds}s.`, {
      duration: TOAST_DURATION_MS.warning,
    })
    return true
  }
  if (err instanceof NetworkError) {
    // A connection drop is a *persistent* state, so the global SNetworkBanner
    // (driven by markConnectionLost in the transport layer) owns the feedback.
    // A transient toast for a persistent state would contradict §9, so swallow.
    return true
  }
  return false
}

export function installErrorHandler(app: App): void {
  app.config.errorHandler = (err) => {
    if (handleError(err)) return

    toast.error('An unexpected error occurred. Please try again.', {
      duration: TOAST_DURATION_MS.error,
    })

    if (import.meta.env.PROD) {
      reportError(err)
    } else {
      console.error(err)
    }
  }

  window.addEventListener('unhandledrejection', (event) => {
    if (handleError(event.reason)) {
      event.preventDefault()
    }
  })
}

export function reportError(err: unknown): void {
  try {
    const payload = {
      message: err instanceof Error ? err.message : String(err),
      stack: err instanceof Error ? err.stack : undefined,
      url: window.location.href,
      timestamp: new Date().toISOString(),
    }
    navigator.sendBeacon?.('/api/frontend-errors', JSON.stringify(payload))
  } catch {
    // Best-effort; don't throw from the error handler.
  }
}
