export class ApiError extends Error {
  readonly type: string
  readonly title: string
  readonly status: number
  readonly detail: string | undefined
  readonly instance: string | undefined
  readonly extra: Record<string, unknown>

  constructor(problem: {
    type: string
    title: string
    status: number
    detail?: string
    instance?: string
    [k: string]: unknown
  }) {
    super(problem.detail ?? problem.title)
    this.name = 'ApiError'
    this.type = problem.type
    this.title = problem.title
    this.status = problem.status
    this.detail = problem.detail
    this.instance = problem.instance

    const { type: _type, title: _title, status: _status, detail: _detail, instance: _instance, ...extra } = problem
    this.extra = extra
  }
}

export class AuthError extends ApiError {
  constructor(problem: ConstructorParameters<typeof ApiError>[0]) {
    super(problem)
    this.name = 'AuthError'
  }
}

export class PermissionError extends ApiError {
  constructor(problem: ConstructorParameters<typeof ApiError>[0]) {
    super(problem)
    this.name = 'PermissionError'
  }
}

/** Which part of the request a validation failure came from. `path` is relative
 *  to it, so a path parameter and a body field of the same name stay distinct —
 *  only `body` errors can be mapped onto a form field. */
export type ValidationLocation = 'body' | 'query' | 'path' | 'header' | 'cookie'

export interface ValidationFieldError {
  location: ValidationLocation
  path: string
  message: string
}

export class ValidationError extends ApiError {
  readonly fieldErrors: ValidationFieldError[]

  constructor(problem: ConstructorParameters<typeof ApiError>[0] & {
    field_errors?: ValidationFieldError[]
  }) {
    super(problem)
    this.name = 'ValidationError'
    this.fieldErrors = problem.field_errors ?? []
  }
}

export class RateLimitError extends ApiError {
  readonly retryAfterMs: number

  constructor(
    problem: ConstructorParameters<typeof ApiError>[0],
    retryAfterMs: number,
  ) {
    super(problem)
    this.name = 'RateLimitError'
    this.retryAfterMs = retryAfterMs
  }
}

export class NetworkError extends ApiError {
  constructor(message: string) {
    super({
      type: 'https://smap.local/problems/network',
      title: 'Network Error',
      status: 0,
      detail: message,
    })
    this.name = 'NetworkError'
  }
}

/**
 * Human-readable message for any error thrown by the HTTP transport.
 *
 * The axios response interceptor (`transport/axios.ts`) converts every
 * problem+json response into an `ApiError` subclass and throws *that*, so
 * callers branch on `ApiError`, not on `e.response`. For 422s the per-field
 * errors are more actionable than the generic `detail`, so they are surfaced
 * when present.
 *
 * A response that is *not* problem+json is the exception, and this function is
 * the reason it stays survivable: `axios.ts:201` rethrows it raw, and for a
 * generated-client call `core/request.ts` re-raises it as the generated
 * `ApiError` — a different class with a `message`, which the `instanceof Error`
 * arm below still turns into readable text. Do not add an `instanceof` on that
 * class to recover from it (gate #13); normalise it in the interceptor instead.
 */
export function errorMessage(e: unknown, fallback = 'request failed'): string {
  if (e instanceof ValidationError && e.fieldErrors.length > 0) {
    return e.fieldErrors.map((fe) => `${fe.path}: ${fe.message}`).join('; ')
  }
  if (e instanceof ApiError) {
    return e.detail ?? e.title
  }
  if (e instanceof Error) {
    return e.message
  }
  return fallback
}
