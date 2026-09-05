// Auth API (identity).
//
// Wraps the generated AuthService over the one instrumented axios singleton. The
// generated client resolves through the bare `axios` default instance, which carries
// the same interceptor references as `http` (bearer inject, silent 401-refresh, typed
// errors) — so this conversion changes the transport, not the auth behaviour. The
// 401-refresh interceptor uses its own uninstrumented instance and never calls authApi,
// so routing refresh/login here cannot recurse.
//
// TokenPairOut and SessionOut are directly assignable to the hand-rolled types; UserOut
// and CaptchaConfigOut are not (optional display_name / widened unions), so they cross
// the boundary through the toMe / toCaptchaConfig bridges.

import { AuthService, OrgsService } from '@shared/api-client'
import type { CaptchaConfigOut, UserOut } from '@shared/api-client'

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
  token_type: string
  expires_in: number
}

export interface Me {
  id: string
  email: string
  email_verified: boolean
  is_admin: boolean
  status: 'active' | 'pending' | 'banned' | 'deleted'
  display_name: string | null
}

export interface Session {
  id: string
  created_at: string
  last_used_at: string
  user_agent: string | null
  ip_inet: string | null
  expires_at: string
}

export interface Identity {
  provider: string
  email: string | null
  created_at: string
}

// UserOut types display_name optional; the server always sends it, so `?? null` keeps the
// hand-rolled required `string | null` truthful without churning consumers.
function toMe(u: UserOut): Me {
  return {
    id: u.id,
    email: u.email,
    email_verified: u.email_verified,
    is_admin: u.is_admin,
    status: u.status,
    display_name: u.display_name ?? null,
  }
}

const CAPTCHA_PROVIDERS: readonly string[] = ['hcaptcha', 'turnstile', 'off']

// CaptchaConfigOut widens mode/provider to `string`; validate rather than blind-cast so an
// unexpected value can't leak past the narrow unions the RegisterView widget switches on. An
// unrecognised provider (a backend addition the widget can't render) falls back to 'off',
// matching the fail-open behaviour used when the captcha config is unreachable.
function toCaptchaConfig(c: CaptchaConfigOut): CaptchaConfig {
  return {
    mode: c.mode === 'on' ? 'on' : 'off',
    provider: CAPTCHA_PROVIDERS.includes(c.provider)
      ? (c.provider as CaptchaConfig['provider'])
      : 'off',
    sitekey: c.sitekey,
  }
}

export const authApi = {
  register: (body: { email: string; password: string; captcha_token: string }) =>
    AuthService.registerApiAuthRegisterPost({ requestBody: body }),

  captchaConfig: (): Promise<CaptchaConfig> =>
    AuthService.captchaConfigApiAuthCaptchaConfigGet().then(toCaptchaConfig),

  verifyEmail: (token: string) =>
    AuthService.verifyEmailApiAuthVerifyEmailPost({ requestBody: { token } }),

  login: (body: LoginRequest): Promise<TokenPair> =>
    AuthService.loginApiAuthLoginPost({ requestBody: body }),

  refresh: (): Promise<TokenPair> => AuthService.refreshApiAuthRefreshPost({ requestBody: {} }),

  // The server extracts the refresh token from the httpOnly `smap_refresh` cookie (sent
  // automatically by the browser). The empty body lets FastAPI parse the optional
  // LogoutIn schema without a 422.
  logout: () => AuthService.logoutApiAuthLogoutPost({ requestBody: {} }),

  requestPasswordReset: (email: string) =>
    AuthService.requestPasswordResetApiAuthRequestPasswordResetPost({ requestBody: { email } }),

  resetPassword: (body: { token: string; new_password: string }) =>
    AuthService.resetPasswordApiAuthResetPasswordPost({ requestBody: body }),

  changePassword: (body: { current_password: string; new_password: string }) =>
    AuthService.changePasswordApiAuthChangePasswordPost({ requestBody: body }),

  changeEmail: (body: { new_email: string; password: string }) =>
    AuthService.changeEmailApiAuthChangeEmailPost({ requestBody: body }),

  me: (): Promise<Me> => AuthService.meApiAuthMeGet().then(toMe),

  // Set or clear the optional display name. `null` (or blank) clears it; the server
  // normalises and echoes back the stored value as the fresh `Me`.
  updateProfile: (body: { display_name: string | null }): Promise<Me> =>
    AuthService.updateMeApiAuthMePatch({ requestBody: body }).then(toMe),

  // Self-service account deletion (R6.07). The re-auth password rides the DELETE body as
  // the generated `requestBody`. A 409 means the caller is the Original Creator of an Org
  // with other members; the `blocked_org_ids` problem extra lists them.
  deleteAccount: (password: string) =>
    AuthService.deleteAccountApiAuthMeDelete({ requestBody: { password } }),

  // Deletion can 409 with blocked_org_ids; resolving those to display names is
  // an identity concern. identity is a leaf slice (tenancy imports identity, so
  // importing tenancy back would be a cycle) — hence this local read-only
  // wrapper instead of tenancy's orgsApi.list.
  listMyOrgs: (): Promise<{ id: string; name: string }[]> =>
    OrgsService.listOrgsApiOrgsGet({}),

  listSessions: (): Promise<Session[]> => AuthService.listSessionsApiAuthSessionsGet({}),

  revokeSession: (id: string) =>
    AuthService.revokeSessionApiAuthSessionsSessionIdDelete({ sessionId: id }),

  // Google account linking (R6.17). `googleLinkStart` returns the Google authorize
  // URL to which the caller navigates the browser (a top-level navigation cannot
  // carry the bearer header, so the URL is fetched over XHR here first).
  googleLinkStart: (): Promise<{ authorize_url: string }> =>
    AuthService.googleLinkStartApiAuthGoogleLinkStartPost(),

  googleUnlink: () => AuthService.googleUnlinkApiAuthGoogleLinkDelete(),

  listIdentities: (): Promise<Identity[]> => AuthService.listIdentitiesApiAuthIdentitiesGet(),
}
