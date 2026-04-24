import { ValidationError } from '@shared/errors'

export function useServerErrors(setErrors: (fields: Record<string, string>) => void) {
  function applyServerErrors(err: unknown): boolean {
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
