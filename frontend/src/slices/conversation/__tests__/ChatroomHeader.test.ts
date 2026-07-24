import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ChatroomHeader from '../components/ChatroomHeader.vue'

function baseProps(over: Record<string, unknown> = {}) {
  return {
    roomName: 'general',
    connectionState: 'live' as const,
    isMobile: false,
    isDesktop: true,
    ...over,
  }
}

describe('ChatroomHeader export control (FU-2)', () => {
  it('shows the export button by default', async () => {
    const wrapper = await renderView(ChatroomHeader, { props: baseProps() })
    expect(wrapper.find('[data-testid="open-export"]').exists()).toBe(true)
  })

  it('shows the export button when the caller can export', async () => {
    const wrapper = await renderView(ChatroomHeader, { props: baseProps({ canExport: true }) })
    expect(wrapper.find('[data-testid="open-export"]').exists()).toBe(true)
  })

  it('hides the export button from a guest', async () => {
    const wrapper = await renderView(ChatroomHeader, { props: baseProps({ canExport: false }) })
    expect(wrapper.find('[data-testid="open-export"]').exists()).toBe(false)
  })

  it('drops export from the mobile overflow menu for a guest', async () => {
    const wrapper = await renderView(ChatroomHeader, {
      props: baseProps({ isMobile: true, isDesktop: false, canExport: false }),
    })
    // The overflow items are rendered by SDropdown; assert the export key is
    // absent from the component's rendered menu text.
    expect(wrapper.html()).not.toContain('conversation.chatroom.export')
  })
})

// Q-5 of docs/tasks/2026-07-22-chatroom-socket-lifecycle: a user over the
// per-user socket cap gets a state of their own, because unlike every other
// failure it names something they can act on (close a tab).
describe('ChatroomHeader connection pill', () => {
  // Slice locales are lazy-loaded, so an untranslated key renders as itself —
  // which is what the export test above relies on too.
  const labelKey = (state: string) => `conversation.chatroom.${state}`

  it('renders the connection-limit pill for the limited state', async () => {
    const wrapper = await renderView(ChatroomHeader, {
      props: baseProps({ connectionState: 'limited' }),
    })
    expect(wrapper.html()).toContain(labelKey('limited'))
    expect(wrapper.html()).not.toContain(labelKey('degraded'))
  })

  it.each([
    ['live', 'live'],
    ['reconnecting', 'reconnecting'],
    ['degraded', 'degraded'],
    // 'connecting' has never had a label of its own — it reuses Offline.
    ['connecting', 'offline'],
  ])('still renders the %s state unchanged', async (state, key) => {
    const wrapper = await renderView(ChatroomHeader, {
      props: baseProps({ connectionState: state }),
    })
    expect(wrapper.html()).toContain(labelKey(key))
    expect(wrapper.html()).not.toContain(labelKey('limited'))
  })
})
