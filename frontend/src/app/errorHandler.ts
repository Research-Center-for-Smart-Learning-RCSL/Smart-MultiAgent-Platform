import type { App } from 'vue'
import { ElMessage } from 'element-plus'
import { AuthError, PermissionError, ValidationError, RateLimitError, NetworkError } from '@shared/errors'
import { router } from './router'

function handleError(err: unknown): boolean {
  if (err instanceof AuthError) {
    router.push({ name: 'identity.login' })
    return true
  }
  if (err instanceof PermissionError) {
    ElMessage.error(err.detail ?? err.title)
    return true
  }
  if (err instanceof ValidationError) {
    return true
  }
  if (err instanceof RateLimitError) {
    const seconds = Math.ceil(err.retryAfterMs / 1000)
    ElMessage.warning(`Rate limited. Please retry in ${seconds}s.`)
    return true
  }
  if (err instanceof NetworkError) {
    ElMessage.error(err.detail ?? 'Network error. Please check your connection.')
    return true
  }
  return false
}

export function installErrorHandler(app: App): void {
  app.config.errorHandler = (err) => {
    if (handleError(err)) return

    ElMessage.error('An unexpected error occurred. Please try again.')

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
