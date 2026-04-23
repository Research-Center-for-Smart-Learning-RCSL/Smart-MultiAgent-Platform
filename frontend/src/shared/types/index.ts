// Cross-slice ambient types. Actual Problem type mirrors backend RFC 7807.

export interface Problem {
  type: string
  title: string
  status: number
  detail?: string
  instance?: string
  field_errors?: Array<{ loc: (string | number)[]; msg: string; type: string }>
}
