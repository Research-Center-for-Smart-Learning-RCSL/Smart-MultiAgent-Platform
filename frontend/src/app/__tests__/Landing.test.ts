import { describe, it, expect, vi } from 'vitest'
import { nextTick } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
import Landing from '../views/Landing.vue'

function signIn(email = 'ada@smap.test'): void {
  const session = useSessionStore()
  session.me = {
    id: 'u_1',
    email,
    email_verified: true,
    is_admin: false,
    status: 'active',
  }
}

describe('Landing', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(Landing)
    expect(wrapper.exists()).toBe(true)
  })

  it('displays a headline and subtitle', async () => {
    const wrapper = await renderView(Landing)
    expect(wrapper.find('h1').exists()).toBe(true)
    expect(wrapper.find('p').exists()).toBe(true)
  })

  it('has links to register and login', async () => {
    const wrapper = await renderView(Landing)
    expect(wrapper.text()).toContain('app.landing.getStarted')
    expect(wrapper.text()).toContain('app.landing.logIn')
  })

  it('renders the hero visual and brand mark', async () => {
    const wrapper = await renderView(Landing)
    expect(wrapper.find('svg.constellation').exists()).toBe(true)
    expect(wrapper.find('.brand-mark').exists()).toBe(true)
  })

  it('renders the three capability cards and the trust strip', async () => {
    const wrapper = await renderView(Landing)
    expect(wrapper.findAll('.feature')).toHaveLength(3)
    expect(wrapper.findAll('.trust__item')).toHaveLength(4)
  })

  it('sets the document title for unauthenticated visitors', async () => {
    await renderView(Landing)
    expect(document.title).toContain('app.landing.metaTitle')
  })

  it('greets the user and drops the marketing sections when authenticated', async () => {
    const wrapper = await renderView(Landing)
    signIn()
    await nextTick()

    expect(wrapper.text()).toContain('app.landing.welcomeBack')
    // Marketing-only sections are for visitors, not returning users.
    expect(wrapper.findAll('.feature')).toHaveLength(0)
    expect(wrapper.findAll('.trust__item')).toHaveLength(0)
  })

  it('offers an enter-workspace action when authenticated', async () => {
    const wrapper = await renderView(Landing)
    signIn()
    await nextTick()

    expect(wrapper.text()).toContain('app.landing.enterWorkspace')
    // The visitor sign-up CTA gives way to the workspace action.
    expect(wrapper.text()).not.toContain('app.landing.getStarted')
  })

  it('forwards a logged-out deep-link visitor on to login with the return path', async () => {
    // Reduced motion skips the intro overlay, so the post-intro forward runs on
    // mount — letting us assert it without driving the animation timers.
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }))

    const wrapper = await renderView(Landing, {
      initialRoute: '/?next=/g/cr_1/tok_abc',
    })
    const router = wrapper.vm.$router
    // The login route is lazily imported, so the forward navigation settles
    // across a few macrotasks — flush until it lands (bounded retries).
    for (let i = 0; i < 10 && router.currentRoute.value.name !== 'identity.login'; i++) {
      await flushPromises()
      await new Promise((r) => setTimeout(r))
    }

    const current = router.currentRoute.value
    expect(current.name).toBe('identity.login')
    expect(String(current.query.redirect)).toContain('tok_abc')

    vi.unstubAllGlobals()
  })
})
