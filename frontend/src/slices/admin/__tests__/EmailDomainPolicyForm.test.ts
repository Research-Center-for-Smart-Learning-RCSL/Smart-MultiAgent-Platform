// AC-3 and AC-10 at the UI: the form is read-only outside the `active` rollout
// phase and says why, If-Match carries the loaded version, a rollout fence and a
// stale version are told apart, and an entry that is not a bare domain is named
// before the request is made.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import EmailDomainPolicyForm from '../components/EmailDomainPolicyForm.vue'
import en from '../locales/en.json'
import zhTW from '../locales/zh-TW.json'

// The harness renders `$t` as the key, so assertions name the key rather than
// the copy; the copy itself is checked against the locale files below.

const toastSuccessMock = vi.hoisted(() => vi.fn())
const toastErrorMock = vi.hoisted(() => vi.fn())
vi.mock('vue-sonner', () => ({
  toast: {
    success: toastSuccessMock,
    error: toastErrorMock,
    warning: vi.fn(),
    info: vi.fn(),
  },
}))

beforeEach(() => {
  toastSuccessMock.mockReset()
  toastErrorMock.mockReset()
})

const ACTIVE = {
  mode: 'allow',
  allow: ['example.edu'],
  deny: [],
  version: 4,
  rollout_state: 'active',
  legacy_mirrored_version: null,
  updated_at: '2026-08-30T00:00:00Z',
  editable: true,
}

const COMPATIBILITY = {
  ...ACTIVE,
  rollout_state: 'compatibility',
  editable: false,
}

const FROZEN = {
  ...ACTIVE,
  rollout_state: 'rollback_frozen',
  legacy_mirrored_version: 4,
  editable: false,
}

const PROBLEM = 'application/problem+json'

function getPolicy(policy: unknown) {
  return http.get('/api/admin/email-domain-policy', () => HttpResponse.json(policy))
}

async function settled(ms = 60) {
  await new Promise((r) => setTimeout(r, ms))
}

describe('EmailDomainPolicyForm', () => {
  it('renders without errors', async () => {
    server.use(getPolicy(ACTIVE))
    const wrapper = await renderView(EmailDomainPolicyForm)
    expect(wrapper.exists()).toBe(true)
  })

  it('loads the stored policy into the form', async () => {
    server.use(getPolicy(ACTIVE))
    const wrapper = await renderView(EmailDomainPolicyForm)
    await settled()

    expect(wrapper.find('[data-testid="email-domain-allow"]').element).toHaveProperty(
      'value',
      'example.edu',
    )
    expect(wrapper.find('[data-testid="email-domain-state"]').exists()).toBe(true)
  })

  it('has the same emailDomain keys in both locales', () => {
    // A key present in one locale only is invisible until that locale is
    // selected, which is the wrong moment to find out.
    const flatten = (value: unknown, prefix = ''): string[] =>
      typeof value === 'object' && value !== null
        ? Object.entries(value).flatMap(([k, v]) => flatten(v, `${prefix}${k}.`))
        : [prefix.slice(0, -1)]

    expect(flatten(zhTW.admin.emailDomain).sort()).toEqual(
      flatten(en.admin.emailDomain).sort(),
    )
  })

  it('escapes the literal @ that vue-i18n would read as a linked message', () => {
    // An unescaped `@` only throws in a production build, so nothing else in
    // this suite would catch it.
    for (const locale of [en, zhTW]) {
      for (const value of Object.values(locale.admin.emailDomain)) {
        if (typeof value === 'string') {
          expect(value).not.toMatch(/(^|[^{'])@/)
        }
      }
    }
  })

  it('reports a load failure with a retry rather than an empty form', async () => {
    server.use(
      http.get('/api/admin/email-domain-policy', () =>
        HttpResponse.json({ type: 'x', title: 'boom', status: 503 }, { status: 503 }),
      ),
    )
    const wrapper = await renderView(EmailDomainPolicyForm)
    await settled(120)

    expect(wrapper.text()).toContain('admin.emailDomain.loadError')
    // A blank editable form would invite an admin to "restore" a policy they
    // never actually read.
    expect(wrapper.find('[data-testid="email-domain-save"]').exists()).toBe(false)
  })

  it.each([
    ['compatibility', COMPATIBILITY, 'admin.emailDomain.fencedCompatibility'],
    ['rollback_frozen', FROZEN, 'admin.emailDomain.fencedFrozen'],
  ])('is read-only in %s and explains which operator step lifts it', async (_name, policy, key) => {
    server.use(getPolicy(policy))
    const wrapper = await renderView(EmailDomainPolicyForm)
    await settled()

    // Two phases, two different operator steps: the message has to name which.
    expect(wrapper.find('[data-testid="email-domain-fenced"]').text()).toContain(key)
    const save = wrapper.find('[data-testid="email-domain-save"]')
    expect(save.attributes('disabled')).toBeDefined()
    expect(
      wrapper.find('[data-testid="email-domain-allow"]').attributes('disabled'),
    ).toBeDefined()
  })

  it('shows the verified rollback marker only when it matches the stored version', async () => {
    server.use(getPolicy(FROZEN))
    const frozen = await renderView(EmailDomainPolicyForm)
    await settled()
    expect(frozen.find('[data-testid="email-domain-rollback-marker"]').exists()).toBe(true)

    server.use(getPolicy(ACTIVE))
    const active = await renderView(EmailDomainPolicyForm)
    await settled()
    expect(active.find('[data-testid="email-domain-rollback-marker"]').exists()).toBe(false)
  })

  it('sends the loaded version as If-Match and the lists split by line', async () => {
    let seenMatch: string | null = null
    let seenBody: unknown = null
    server.use(
      getPolicy(ACTIVE),
      http.put('/api/admin/email-domain-policy', async ({ request }) => {
        seenMatch = request.headers.get('If-Match')
        seenBody = await request.json()
        return HttpResponse.json({ ...ACTIVE, version: 5 })
      }),
    )
    const wrapper = await renderView(EmailDomainPolicyForm)
    await settled()
    await wrapper
      .find('[data-testid="email-domain-allow"]')
      .setValue('example.edu\n\n  dept.example.edu  \n')
    await wrapper.find('form').trigger('submit')
    await settled(120)

    expect(seenMatch).toBe('4')
    // Blank lines dropped and each entry trimmed: an admin editing a textarea
    // leaves both behind constantly, and neither is a domain.
    expect(seenBody).toEqual({
      mode: 'allow',
      allow: ['example.edu', 'dept.example.edu'],
      deny: [],
    })
    expect(toastSuccessMock).toHaveBeenCalled()
  })

  it('names an entry that is not a bare domain and does not send the request', async () => {
    let requested = false
    server.use(
      getPolicy(ACTIVE),
      http.put('/api/admin/email-domain-policy', () => {
        requested = true
        return HttpResponse.json(ACTIVE)
      }),
    )
    const wrapper = await renderView(EmailDomainPolicyForm)
    await settled()
    await wrapper.find('[data-testid="email-domain-allow"]').setValue('user@example.edu')
    await settled()

    expect(wrapper.find('[data-testid="email-domain-invalid"]').exists()).toBe(true)
    await wrapper.find('form').trigger('submit')
    await settled(120)
    expect(requested).toBe(false)
  })

  it('tells a rollout fence apart from a stale version on a 409', async () => {
    // Same status, different recoveries: reloading fixes a stale version and
    // does nothing for a fence, so telling an operator to reload into a fence
    // would loop them forever.
    for (const [slug, key] of [
      ['admin/email-domain-policy-stale', 'admin.emailDomain.staleConflict'],
      ['admin/email-domain-policy-fenced', 'admin.emailDomain.fenceConflict'],
    ]) {
      toastErrorMock.mockReset()
      server.use(
        getPolicy(ACTIVE),
        http.put('/api/admin/email-domain-policy', () =>
          HttpResponse.json(
            { type: `https://smap.local/problems/${slug}`, title: 'conflict', status: 409 },
            { status: 409, headers: { 'content-type': PROBLEM } },
          ),
        ),
      )
      const wrapper = await renderView(EmailDomainPolicyForm)
      await settled()
      await wrapper.find('form').trigger('submit')
      await settled(120)

      expect(toastErrorMock.mock.calls.at(-1)?.[0]).toBe(key)
    }
  })

  it.each([
    [422, 'admin.emailDomain.invalidConflict'],
    [503, 'admin.emailDomain.unavailable'],
  ])('reports a %s distinctly', async (status, key) => {
    server.use(
      getPolicy(ACTIVE),
      http.put('/api/admin/email-domain-policy', () =>
        HttpResponse.json(
          { type: 'https://smap.local/problems/x', title: 'no', status },
          { status, headers: { 'content-type': PROBLEM } },
        ),
      ),
    )
    const wrapper = await renderView(EmailDomainPolicyForm)
    await settled()
    await wrapper.find('form').trigger('submit')
    await settled(120)

    expect(toastErrorMock.mock.calls.at(-1)?.[0]).toBe(key)
  })
})
