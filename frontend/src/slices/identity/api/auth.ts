import { http } from '@shared/transport'

export interface LoginRequest {
  email: string
  password: string
}

export interface CaptchaConfig {
  mode: 'on' | 'off'
  provider: 'hcaptcha' | 'turnstile' | 'off'
  sitekey: string
}

export interface TokenPair {
  access_token: string
  refresh_token?: string  // server now sets this via httpOnly cookie; field kept for compatibility
  token_type: 'Bearer'
  expires_in: number
}

export interface Me {
  id: string
  email: string
  email_verified: boolean
  is_admin: boolean
  status: 'active' | 'pending' | 'banned' | 'deleted'
}

export interface Session {
  id: string
  created_at: string
  last_used_at: string
  user_agent: string | null
  ip_inet: string | null
  is_current: boolean
}

export const authApi = {
  register: (body: { email: string; password: string; captcha_token: string }) =>
    http.post('/auth/register', body),

  captchaConfig: () => http.get<CaptchaConfig>('/auth/captcha-config'),

  verifyEmail: (token: string) =>
    http.post('/auth/verify-email', { token }),

  login: (body: LoginRequest) => http.post<TokenPair>('/auth/login', body),

  refresh: () =>
    http.post<TokenPair>('/auth/refresh', {}),

  // The server extracts the refresh token from the httpOnly `smap_refresh`
  // cookie (sent automatically by the browser).  The empty body ensures
  // FastAPI can parse the optional `LogoutIn` schema without a 422.
  logout: () => http.post('/auth/logout', {}),

  requestPasswordReset: (email: string) =>
    http.post('/auth/request-password-reset', { email }),

  resetPassword: (body: { token: string; new_password: string }) =>
    http.post('/auth/reset-password', body),

  changePassword: (body: { current: string; new: string }) =>
    http.post('/auth/change-password', body),

  changeEmail: (body: { new_email: string; password: string }) =>
    http.post('/auth/change-email', body),

  me: () => http.get<Me>('/auth/me'),

  // Self-service account deletion (R6.07). DELETE carries the re-auth password
  // in the body (`{ data }` — axios puts a DELETE payload there). A 409 means
  // the caller is the Original Creator of an Org with other members; the
  // `blocked_org_ids` problem extra lists them.
  deleteAccount: (password: string) =>
    http.delete('/auth/me', { data: { password } }),

  listSessions: () => http.get<Session[]>('/auth/sessions'),

  revokeSession: (id: string) => http.delete(`/auth/sessions/${id}`),
}
