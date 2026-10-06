// docs/tasks/2026-10-05-guest-sender-marking (AC-1, AC-2): a typing guest is named
// as a guest, in the text a screen reader announces.

import { describe, it, expect, afterEach } from 'vitest'
import { renderView } from '../../../../tests/utils'
import { i18n } from '@shared/i18n'
import ChatroomTypingIndicator from '../components/ChatroomTypingIndicator.vue'
import conversationEn from '../locales/en.json'

describe('ChatroomTypingIndicator', () => {
  afterEach(() => {
    i18n.global.setLocaleMessage('en', {})
  })

  it('marks a guest and leaves a member bare', async () => {
    i18n.global.mergeLocaleMessage('en', conversationEn)
    const wrapper = await renderView(ChatroomTypingIndicator, {
      props: {
        typers: [
          { name: 'Ms Lin', isGuest: true },
          { name: 'Ms Lin', isGuest: false },
        ],
      },
    })
    expect(wrapper.text()).toContain('Ms Lin (guest) and Ms Lin are typing')
  })

  it('renders nothing while nobody types', async () => {
    const wrapper = await renderView(ChatroomTypingIndicator, { props: { typers: [] } })
    expect(wrapper.find('.typing--visible').exists()).toBe(false)
    expect(wrapper.text()).toBe('')
  })
})
