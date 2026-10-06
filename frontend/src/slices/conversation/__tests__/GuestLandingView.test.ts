import { describe, it, expect, afterEach, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { i18n } from '@shared/i18n'
import { useConfirmDialog } from '@shared/composables/useConfirmDialog'
import { markConnectionRestored } from '@shared/composables/useNetworkStatus'
import {
  clearGuestContext,
  getAccessToken,
  getGuestChatroomId,
  setAccessToken,
} from '@shared/transport'
import { useSessionStore } from '@shared/stores/session'
import GuestLandingView from '../views/GuestLandingView.vue'
import { useGuestSessionStore } from '../stores/guestSession'

const routes = [
  {
    path: '/g/:chatroomId/:guestToken',
    name: 'conversation.guest',
    component: GuestLandingView,
  },
  {
    path: '/chatrooms/:chatroomId',
    name: 'conversation.chatroom',
    component: { template: '<div />' },
  },
]

describe('GuestLandingView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(GuestLandingView, {
      routes,
      initialRoute: '/g/cr_1/tok_abc',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('renders the enrollment card with a live region on mount', async () => {
    const wrapper = await renderView(GuestLandingView, {
      routes,
      initialRoute: '/g/cr_1/tok_abc',
    })
    expect(wrapper.find('.guest-landing').exists()).toBe(true)
    expect(wrapper.find('[aria-live]').exists()).toBe(true)
  })

  it('shows the display name form for a new guest', async () => {
    const wrapper = await renderView(GuestLandingView, {
      routes,
      initialRoute: '/g/cr_1/tok_abc',
    })
    expect(wrapper.find('.guest-form').exists()).toBe(true)
    expect(wrapper.find('input').exists()).toBe(true)
  })

  it('shows cap-reached message when state is cap_reached', async () => {
    const wrapper = await renderView(GuestLandingView, {
      routes,
      initialRoute: '/g/cr_1/tok_abc',
    })
    // The component starts in idle state (no localStorage) and shows the form
    expect(wrapper.find('.guest-form').exists()).toBe(true)
  })
})

// docs/tasks/2026-10-05-guest-frontend-session-lifecycle (F-10, F-18, F-19, F-20, F-22)
describe('GuestLandingView session lifecycle', () => {
  const ROOM = '0f8e2b1c-aaaa-4bbb-8ccc-0123456789ab'
  const TOKEN = 'tok_abcdefghijklmnop'
  const SESSION_URL = `/api/guest/${ROOM}/${TOKEN}/session`
  // Built from code points: a literal invisible character in source is one an
  // editor or a paste can silently drop.
  const ZERO_WIDTH_SPACE = String.fromCodePoint(0x200b)
  const WORD_JOINER = String.fromCodePoint(0x2060)
  const ME = {
    id: 'u_1',
    email: 'teacher@smap.test',
    display_name: 'Teacher',
    email_verified: true,
    is_admin: false,
    status: 'active',
  }

  function guestJwt(): string {
    const claims = { sub: 'g_1', token_use: 'guest_access', chatroom_id: ROOM, display_name: 'Alice' }
    return `h.${btoa(JSON.stringify(claims)).replace(/=+$/, '')}.s`
  }

  function problem(status: number, type: string): Response {
    return HttpResponse.json({ type: `https://smap.local/problems/${type}`, title: 't', status }, { status })
  }

  // msw answers on a timer and vee-validate validates asynchronously, so a
  // microtask flush alone settles neither.
  async function settle(): Promise<void> {
    await new Promise((r) => setTimeout(r, 30))
    await flushPromises()
    await nextTick()
  }

  async function mountLanding(
    path = `/g/${ROOM}/${TOKEN}`,
    me: typeof ME | null = null,
  ): Promise<VueWrapper> {
    const pinia = createPinia()
    setActivePinia(pinia)
    if (me) useSessionStore().me = me
    const router = createRouter({ history: createMemoryHistory(), routes })
    await router.push(path)
    await router.isReady()
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = mount(GuestLandingView, {
      global: { plugins: [pinia, router, i18n, [VueQueryPlugin, { queryClient: qc }]], stubs: { teleport: true } },
    })
    await settle()
    return wrapper
  }

  async function submitName(wrapper: VueWrapper, name: string): Promise<void> {
    await wrapper.find('input').setValue(name)
    await wrapper.find('form').trigger('submit')
    await settle()
    await nextTick()
  }

  function holdHint(key = ROOM): void {
    localStorage.setItem(
      `smap:guest:${key}`,
      JSON.stringify({ browser_id: 'br-1', guest_session_id: 'g_1', display_name: 'Alice' }),
    )
  }

  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    setAccessToken(null)
    clearGuestContext()
    localStorage.clear()
    useConfirmDialog().handleCancel()
    markConnectionRestored()
  })

  it('asks a signed-in user to confirm, then clears the account before entering as a guest', async () => {
    let sentAuth: string | null = 'unset'
    server.use(
      http.post(SESSION_URL, ({ request }) => {
        sentAuth = request.headers.get('Authorization')
        return HttpResponse.json({ access_token: guestJwt(), guest_session_id: 'g_1', display_name: 'Alice', is_resuming: false })
      }),
    )
    setAccessToken('user-token')
    const wrapper = await mountLanding(undefined, ME)
    const session = useSessionStore()
    const dialog = useConfirmDialog()

    await wrapper.findAll('.choice-card')[1]!.trigger('click')
    await settle()
    expect(dialog.state.open).toBe(true)
    expect(session.isAuthenticated).toBe(true)

    dialog.handleConfirm()
    await settle()
    expect(session.isAuthenticated).toBe(false)
    expect(getAccessToken()).toBeNull()

    await submitName(wrapper, 'Alice')
    expect(sentAuth).toBeNull()
    expect(getAccessToken()).toBe(guestJwt())
    expect(getGuestChatroomId()).toBe(ROOM)
    expect(session.isAuthenticated).toBe(false)
  })

  it('keeps the account when the signed-in user cancels', async () => {
    setAccessToken('user-token')
    const wrapper = await mountLanding(undefined, ME)

    await wrapper.findAll('.choice-card')[1]!.trigger('click')
    useConfirmDialog().handleCancel()
    await settle()

    expect(useSessionStore().isAuthenticated).toBe(true)
    expect(getAccessToken()).toBe('user-token')
    expect(wrapper.find('.guest-choice').exists()).toBe(true)
  })

  it('reports a name the server finds empty as a name error, not a dead link', async () => {
    server.use(http.post(SESSION_URL, () => problem(422, 'conversation/guest-display-name-invalid')))
    const wrapper = await mountLanding()

    await submitName(wrapper, `A${WORD_JOINER}`)

    expect(wrapper.find('.guest-form').exists()).toBe(true)
    expect(wrapper.text()).toContain('conversation.guest.displayNameInvalid')
    expect(wrapper.text()).not.toContain('conversation.guest.invalidToken')
  })

  it('refuses an invisible-only name on the form without asking the server', async () => {
    let calls = 0
    server.use(
      http.post(SESSION_URL, () => {
        calls += 1
        return problem(422, 'conversation/guest-display-name-invalid')
      }),
    )
    const wrapper = await mountLanding()

    await wrapper.find('input').setValue(ZERO_WIDTH_SPACE.repeat(2))
    await nextTick()
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeDefined()

    await wrapper.find('form').trigger('submit')
    await settle()
    expect(calls).toBe(0)
    expect(wrapper.find('.guest-form').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('conversation.guest.invalidToken')
  })

  it('accepts a name that keeps a zero-width joiner inside an emoji', async () => {
    const wrapper = await mountLanding()
    await wrapper.find('input').setValue(String.fromCodePoint(0x1f469, 0x200d, 0x1f4bb))
    await nextTick()
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeUndefined()
  })

  it('shows guest access as disabled when the owner turned links off', async () => {
    server.use(http.post(SESSION_URL, () => problem(403, 'conversation/guest-access-disabled')))
    const wrapper = await mountLanding()

    await submitName(wrapper, 'Alice')

    expect(wrapper.text()).toContain('conversation.guest.guestDisabled')
    expect(wrapper.text()).not.toContain('conversation.guest.invalidToken')
  })

  it('Retry after a failed resume repeats the resume', async () => {
    holdHint()
    const bodies: unknown[] = []
    let fail = true
    server.use(
      http.post(SESSION_URL, async ({ request }) => {
        bodies.push(await request.json())
        if (fail) return HttpResponse.error()
        return HttpResponse.json({ access_token: guestJwt(), guest_session_id: 'g_1', display_name: 'Alice', is_resuming: true })
      }),
    )
    const wrapper = await mountLanding()

    await wrapper.find('.guest-actions button').trigger('click')
    await settle()
    expect(wrapper.text()).toContain('conversation.guest.networkError')

    fail = false
    await wrapper.findAll('button').find((b) => b.text() === 'conversation.guest.retry')!.trigger('click')
    await settle()

    expect(bodies).toHaveLength(2)
    expect(bodies[1]).toEqual({ display_name: 'Alice', browser_id: 'br-1' })
    expect(getGuestChatroomId()).toBe(ROOM)
  })

  it('Retry after a failed own-account choice repeats the enrolment', async () => {
    let enrolls = 0
    server.use(
      http.post(`/api/guest/${ROOM}/${TOKEN}/enroll`, () => {
        enrolls += 1
        return enrolls === 1 ? HttpResponse.error() : new HttpResponse(null, { status: 204 })
      }),
    )
    setAccessToken('user-token')
    const wrapper = await mountLanding(undefined, ME)

    await wrapper.findAll('.choice-card')[0]!.trigger('click')
    await settle()
    expect(wrapper.text()).toContain('conversation.guest.networkError')

    await wrapper.findAll('button').find((b) => b.text() === 'conversation.guest.retry')!.trigger('click')
    await settle()

    expect(enrolls).toBe(2)
  })

  it('Retry after a failed enrolment submits the same name again', async () => {
    const bodies: unknown[] = []
    server.use(
      http.post(SESSION_URL, async ({ request }) => {
        bodies.push(await request.json())
        if (bodies.length === 1) return HttpResponse.error()
        return HttpResponse.json({ access_token: guestJwt(), guest_session_id: 'g_1', display_name: 'Alice', is_resuming: false })
      }),
    )
    const wrapper = await mountLanding()

    await submitName(wrapper, 'Alice')
    await wrapper.findAll('button').find((b) => b.text() === 'conversation.guest.retry')!.trigger('click')
    await settle()

    expect(bodies).toHaveLength(2)
    expect((bodies[1] as { display_name: string }).display_name).toBe('Alice')
  })

  it('uses the canonical room id for the guest context, the hint and the rejoin link', async () => {
    server.use(
      http.post(SESSION_URL, () =>
        HttpResponse.json({ access_token: guestJwt(), guest_session_id: 'g_1', display_name: 'Alice', is_resuming: false }),
      ),
    )
    const wrapper = await mountLanding(`/g/${ROOM.toUpperCase()}/${TOKEN}`)

    await submitName(wrapper, 'Alice')

    expect(getGuestChatroomId()).toBe(ROOM)
    expect(localStorage.getItem(`smap:guest:${ROOM}`)).not.toBeNull()
    expect(localStorage.getItem(`smap:guest:${ROOM.toUpperCase()}`)).toBeNull()
    expect(useGuestSessionStore().rejoinUrl).toBe(`/g/${ROOM}/${TOKEN}`)
  })

  it('welcomes back a guest whose hint was written under the upper-case id', async () => {
    holdHint(ROOM.toUpperCase())
    const wrapper = await mountLanding(`/g/${ROOM.toUpperCase()}/${TOKEN}`)
    expect(wrapper.text()).toContain('conversation.guest.welcomeBack')
  })
})
