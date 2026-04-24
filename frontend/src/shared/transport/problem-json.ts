import {
  ApiError,
  AuthError,
  NetworkError,
  PermissionError,
  RateLimitError,
  ValidationError,
} from '@shared/errors'

export interface ProblemJson {
  type: string
  title: string
  status: number
  detail?: string
  instance?: string
  field_errors?: Array<{ path: string; message: string }>
  retry_after_seconds?: number
  [k: string]: unknown
}

const AUTH_TYPES = [
  '/auth/required',
  '/auth/token-expired',
  '/auth/token-revoked',
  '/auth/invalid-credentials',
  '/auth/banned',
  '/auth/lockout',
]

export function parseProblem(
  problem: ProblemJson,
  retryAfterHeader?: string | null,
): ApiError {
  const t = problem.type ?? ''

  if (problem.status === 429 || t.endsWith('/rate-limited')) {
    let retryMs = 5_000
    if (retryAfterHeader) {
      const secs = Number(retryAfterHeader)
      retryMs = Number.isFinite(secs) ? secs * 1000 : 5_000
    } else if (problem.retry_after_seconds) {
      retryMs = problem.retry_after_seconds * 1000
    }
    return new RateLimitError(problem, retryMs)
  }

  if (problem.status === 422 || t.endsWith('/validation')) {
    return new ValidationError(problem)
  }

  if (AUTH_TYPES.some((s) => t.endsWith(s)) || problem.status === 401) {
    return new AuthError(problem)
  }

  if (t.endsWith('/forbidden') || problem.status === 403) {
    return new PermissionError(problem)
  }

  return new ApiError(problem)
}

export function isNetworkError(err: unknown): err is NetworkError {
  return err instanceof NetworkError
}

export function isProblemWithType(err: unknown, suffix: string): boolean {
  if (err instanceof ApiError) {
    return err.type.endsWith(suffix)
  }
  return false
}
