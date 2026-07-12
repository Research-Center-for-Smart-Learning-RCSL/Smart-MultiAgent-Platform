import { describe, expect, it } from 'vitest'
import { server } from '../../../../../tests/mocks/server'
import { createRequestCapture, type CapturedRequest } from '../../../../../tests/helpers/requestCapture'
import { authApi } from '../auth'

// Request-level characterization of the auth api wire contract, pinned as
// docs/tasks/2026-07-12-generated-client-wrap-identity converts the 16 authApi methods from
// @shared/transport's `http` to the generated AuthService. This is the agent-groups pattern
// (methods now resolve the bare body), on the security-critical auth surface — so the guard
// is: verb/path/body must not move, the DELETE /auth/me re-auth body must survive, and the
// two boundary bridges (toMe, toCaptchaConfig) must reshape correctly.

const tokenPairOut = {
  access_token: 'at_1',
  refresh_token: 'rt_1',
  token_type: 'Bearer',
  expires_in: 900,
}
const userOut = {
  id: 'u_1',
  email: 'a@x.io',
  email_verified: true,
  is_admin: false,
  status: 'active',
  display_name: 'Ada',
}
const sessionOut = {
  id: 's_1',
  created_at: 't',
  last_used_at: 't',
  user_agent: 'UA',
  ip_inet: '10.0.0.1',
  expires_at: 't',
}
const captchaOut = { mode: 'on', provider: 'hcaptcha', sitekey: 'sk_1' }

function captureAll(): { value: CapturedRequest | null } {
  const { cap, on } = createRequestCapture()
  server.use(
    on('post', '/api/auth/register', { user_id: 'u_1' }, 201),
    on('get', '/api/auth/captcha-config', captchaOut),
    on('post', '/api/auth/verify-email', { status: 'ok' }),
    on('post', '/api/auth/login', tokenPairOut),
    on('post', '/api/auth/refresh', tokenPairOut),
    on('post', '/api/auth/logout', null, 204),
    on('post', '/api/auth/request-password-reset', { status: 'ok' }),
    on('post', '/api/auth/reset-password', null, 204),
    on('post', '/api/auth/change-password', null, 204),
    on('post', '/api/auth/change-email', null, 204),
    on('get', '/api/auth/me', userOut),
    on('patch', '/api/auth/me', userOut),
    on('delete', '/api/auth/me', null, 204),
    on('get', '/api/auth/sessions', [sessionOut]),
    on('delete', '/api/auth/sessions/:sid', null, 204),
  )
  return cap
}

describe('auth api wire contract', () => {
  it('register POSTs { email, password, captcha_token }', async () => {
    const cap = captureAll()
    await authApi.register({ email: 'a@x.io', password: 'pw', captcha_token: 'ct' })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/auth/register',
      body: { email: 'a@x.io', password: 'pw', captcha_token: 'ct' },
    })
  })

  it('captchaConfig GETs the config and bridges to the narrow unions', async () => {
    const cap = captureAll()
    const cfg = await authApi.captchaConfig()
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/auth/captcha-config' })
    expect(cfg).toEqual({ mode: 'on', provider: 'hcaptcha', sitekey: 'sk_1' })
  })

  it('captchaConfig falls back to off for an unrecognised provider or mode', async () => {
    const { on } = createRequestCapture()
    server.use(on('get', '/api/auth/captcha-config', { mode: 'weird', provider: 'recaptcha', sitekey: 'sk_2' }))
    const cfg = await authApi.captchaConfig()
    expect(cfg).toEqual({ mode: 'off', provider: 'off', sitekey: 'sk_2' })
  })

  it('verifyEmail POSTs { token }', async () => {
    const cap = captureAll()
    await authApi.verifyEmail('tok_1')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/auth/verify-email',
      body: { token: 'tok_1' },
    })
  })

  it('login POSTs { email, password } and resolves the TokenPair', async () => {
    const cap = captureAll()
    const pair = await authApi.login({ email: 'a@x.io', password: 'pw' })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/auth/login',
      body: { email: 'a@x.io', password: 'pw' },
    })
    expect(pair).toMatchObject({ access_token: 'at_1', token_type: 'Bearer', expires_in: 900 })
  })

  it('refresh POSTs an empty body to /auth/refresh', async () => {
    const cap = captureAll()
    const pair = await authApi.refresh()
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/auth/refresh', body: {} })
    expect(pair).toMatchObject({ access_token: 'at_1' })
  })

  it('logout POSTs an empty body', async () => {
    const cap = captureAll()
    await authApi.logout()
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/auth/logout', body: {} })
  })

  it('requestPasswordReset POSTs { email }', async () => {
    const cap = captureAll()
    await authApi.requestPasswordReset('a@x.io')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/auth/request-password-reset',
      body: { email: 'a@x.io' },
    })
  })

  it('resetPassword POSTs { token, new_password }', async () => {
    const cap = captureAll()
    await authApi.resetPassword({ token: 'tok_1', new_password: 'pw2' })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/auth/reset-password',
      body: { token: 'tok_1', new_password: 'pw2' },
    })
  })

  it('changePassword POSTs { current_password, new_password }', async () => {
    const cap = captureAll()
    await authApi.changePassword({ current_password: 'pw', new_password: 'pw2' })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/auth/change-password',
      body: { current_password: 'pw', new_password: 'pw2' },
    })
  })

  it('changeEmail POSTs { new_email, password }', async () => {
    const cap = captureAll()
    await authApi.changeEmail({ new_email: 'b@x.io', password: 'pw' })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/auth/change-email',
      body: { new_email: 'b@x.io', password: 'pw' },
    })
  })

  it('me GETs /auth/me and bridges UserOut to Me', async () => {
    const cap = captureAll()
    const me = await authApi.me()
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/auth/me' })
    expect(me).toEqual({
      id: 'u_1',
      email: 'a@x.io',
      email_verified: true,
      is_admin: false,
      status: 'active',
      display_name: 'Ada',
    })
  })

  it('me defaults an absent display_name to null (toMe bridge)', async () => {
    const { on } = createRequestCapture()
    server.use(on('get', '/api/auth/me', {
      id: 'u_1',
      email: 'a@x.io',
      email_verified: true,
      is_admin: false,
      status: 'active',
    }))
    const me = await authApi.me()
    expect(me.display_name).toBeNull()
    expect(me.status).toBe('active')
  })

  it('updateProfile PATCHes { display_name } and bridges to Me', async () => {
    const cap = captureAll()
    const me = await authApi.updateProfile({ display_name: 'Grace' })
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/auth/me',
      body: { display_name: 'Grace' },
    })
    expect(me).toMatchObject({ id: 'u_1', display_name: 'Ada' })
  })

  it('deleteAccount DELETEs /auth/me with the re-auth password in the body', async () => {
    const cap = captureAll()
    await authApi.deleteAccount('pw')
    expect(cap.value).toMatchObject({
      method: 'DELETE',
      path: '/api/auth/me',
      body: { password: 'pw' },
    })
  })

  it('listSessions GETs /auth/sessions and resolves the bare array', async () => {
    const cap = captureAll()
    const sessions = await authApi.listSessions()
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/auth/sessions' })
    expect(sessions[0]).toMatchObject({ id: 's_1', ip_inet: '10.0.0.1' })
  })

  it('revokeSession DELETEs the session by id', async () => {
    const cap = captureAll()
    await authApi.revokeSession('s_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/auth/sessions/s_1' })
  })
})
