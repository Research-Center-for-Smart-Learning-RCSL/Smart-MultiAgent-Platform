import { http } from '@shared/transport'

export interface LoginRequest {
  email: string
  password: string
  captcha_token?: string
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

  verifyEmail: (token: string) =>
    http.post('/auth/verify-email', { token }),

  login: (body: LoginRequest) => http.post<TokenPair>('/auth/login', body),

  refresh: () =>
    http.post<TokenPair>('/auth/refresh', {}),

  logout: () => http.post('/auth/logout'),

  requestPasswordReset: (email: string) =>
    http.post('/auth/request-password-reset', { email }),

  resetPassword: (body: { token: string; new_password: string }) =>
    http.post('/auth/reset-password', body),

  changePassword: (body: { current: string; new: string }) =>
    http.post('/auth/change-password', body),

  changeEmail: (body: { new_email: string; password: string }) =>
    http.post('/auth/change-email', body),

  me: () => http.get<Me>('/auth/me'),

  listSessions: () => http.get<Session[]>('/auth/sessions'),

  revokeSession: (id: string) => http.delete(`/auth/sessions/${id}`),
}
