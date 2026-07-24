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
