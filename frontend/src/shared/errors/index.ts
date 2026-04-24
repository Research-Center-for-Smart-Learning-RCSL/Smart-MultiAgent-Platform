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

    const extra = { ...problem }
    delete extra.type
    delete extra.title
    delete extra.status
    delete extra.detail
    delete extra.instance
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

export class ValidationError extends ApiError {
  readonly fieldErrors: Array<{ path: string; message: string }>

  constructor(problem: ConstructorParameters<typeof ApiError>[0] & {
    field_errors?: Array<{ path: string; message: string }>
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
