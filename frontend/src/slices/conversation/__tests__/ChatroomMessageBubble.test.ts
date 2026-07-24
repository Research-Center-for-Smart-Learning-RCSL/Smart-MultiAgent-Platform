import { describe, it, expect, afterEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { i18n } from '@shared/i18n'
import ChatroomMessageBubble from '../components/ChatroomMessageBubble.vue'
import conversationEn from '../locales/en.json'
import type { Attachment, DisplayMessage } from '../types'

function agentMessage(metadata: Record<string, unknown>): DisplayMessage {
  return {
    id: 'm1',
    chatroom_id: 'c1',
    sender_type: 'agent',
    sender_id: 'a1',
    content_md: 'hi',
    metadata,
    version: 1,
    created_at: '2026-01-01T00:00:00Z',
    edited_at: null,
    deleted_at: null,
  }
}

const baseProps = {
  html: '<p>hi</p>',
  senderName: 'Bot',
  editing: false,
  editDraft: '',
  canEdit: false,
  canDelete: false,
}

describe('ChatroomMessageBubble RAG citations', () => {
  it('reveals the cited source documents when the sources block is expanded', async () => {
    const wrapper = await renderView(ChatroomMessageBubble, {
      props: {
        ...baseProps,
        message: agentMessage({
          rag_sources: [{ document_id: 'd1', filename: 'guide.pdf', chunk_idx: 3, score: 0.82 }],
        }),
      },
    })

    const toggle = wrapper.find('.bubble__sources-toggle')
    expect(toggle.exists()).toBe(true)
    // Collapsed by default — the filename is hidden until expanded.
    expect(wrapper.text()).not.toContain('guide.pdf')

    await toggle.trigger('click')
    expect(wrapper.text()).toContain('guide.pdf')
  })

  it('shows no sources block when the agent reply has no citations', async () => {
    const wrapper = await renderView(ChatroomMessageBubble, {
      props: { ...baseProps, message: agentMessage({}) },
    })
    expect(wrapper.find('.bubble__sources-toggle').exists()).toBe(false)
  })
})

function attachment(over: Partial<Attachment>): Attachment {
  return {
    id: 'att',
    chatroom_id: 'c1',
    message_id: 'm1',
    filename: 'f',
    mime: 'application/octet-stream',
    size_bytes: 10,
    status: 'active',
    scan_status: 'skipped',
    ...over,
  }
}

function systemMessage(metadata: Record<string, unknown>): DisplayMessage {
  return {
    id: 's1',
    chatroom_id: 'c1',
    sender_type: 'system',
    sender_id: null,
    content_md: 'released analysis',
    metadata,
    version: 1,
    created_at: '2026-01-01T00:00:00Z',
    edited_at: null,
    deleted_at: null,
  }
}

// The test i18n harness echoes keys, so assert on structure + the chosen key.
describe('ChatroomMessageBubble released observation (R28.06)', () => {
  it('renders a released observation as an owner-attributed card', async () => {
    const wrapper = await renderView(ChatroomMessageBubble, {
      props: {
        ...baseProps,
        html: '<p>released analysis</p>',
        message: systemMessage({ type: 'released_observation' }),
      },
    })
    expect(wrapper.find('.released').exists()).toBe(true)
    expect(wrapper.find('.released__head').text()).toBe('conversation.observers.releasedByOwner')
    expect(wrapper.find('.released__body').text()).toContain('released analysis')
  })

  it('uses the named attribution key only when disclosed (observer_agent_id present)', async () => {
    const wrapper = await renderView(ChatroomMessageBubble, {
      props: {
        ...baseProps,
        html: '<p>x</p>',
        message: systemMessage({ type: 'released_observation', observer_agent_id: 'abcdef12' }),
      },
    })
    // Named key selected; interpolation is not exercised under the key-echo harness.
    expect(wrapper.find('.released__head').text()).toBe('conversation.observers.releasedByOwnerNamed')
  })

  // These two load the real conversation locale bundle (the harness normally
  // echoes untranslated keys, which can't reveal whether the right *value*
  // was interpolated) so the resolved name is actually observable, then
  // restore the empty catalogue afterward so the key-echo tests above are
  // unaffected by test order.
  describe('with real i18n messages loaded', () => {
    afterEach(() => {
      i18n.global.setLocaleMessage('en', {})
    })

    it('resolves observer_agent_id against agentNames instead of showing a raw id', async () => {
      i18n.global.mergeLocaleMessage('en', conversationEn)
      const wrapper = await renderView(ChatroomMessageBubble, {
        props: {
          ...baseProps,
          html: '<p>x</p>',
          message: systemMessage({ type: 'released_observation', observer_agent_id: 'agent-123' }),
          agentNames: { 'agent-123': 'Research Bot' },
        },
      })
      expect(wrapper.find('.released__head').text()).toContain('Research Bot')
      expect(wrapper.find('.released__head').text()).not.toContain('agent-123')
    })

    it('falls back to a truncated id when the agent is not in agentNames', async () => {
      i18n.global.mergeLocaleMessage('en', conversationEn)
      const wrapper = await renderView(ChatroomMessageBubble, {
        props: {
          ...baseProps,
          html: '<p>x</p>',
          message: systemMessage({
            type: 'released_observation',
            observer_agent_id: 'abcdef1234567890',
          }),
        },
      })
      expect(wrapper.find('.released__head').text()).toContain('abcdef12')
    })
  })

  it('falls back to the plain system divider when metadata is not a released observation', async () => {
    const wrapper = await renderView(ChatroomMessageBubble, {
      props: { ...baseProps, html: '<p>x</p>', message: systemMessage({}) },
    })
    expect(wrapper.find('.released').exists()).toBe(false)
    expect(wrapper.find('.sys').exists()).toBe(true)
  })
})

describe('ChatroomMessageBubble attachments', () => {
  it('renders an image attachment inline (not as a download chip)', async () => {
    server.use(
      http.get('/api/attachments/img1', () =>
        HttpResponse.json({ ...attachment({ id: 'img1', filename: 'chart.png', mime: 'image/png' }), url: 'https://store/chart.png' }),
      ),
    )
    const message: DisplayMessage = {
      ...agentMessage({}),
      attachments: [
        attachment({ id: 'img1', filename: 'chart.png', mime: 'image/png' }),
        attachment({ id: 'csv1', filename: 'data.csv', mime: 'text/csv' }),
      ],
    }
    const wrapper = await renderView(ChatroomMessageBubble, { props: { ...baseProps, message } })
    const chips = wrapper.findAll('.attachment-link')
    // The csv is a download chip; the image is delegated to AttachmentImage.
    expect(chips.some((c) => c.text().includes('data.csv'))).toBe(true)
    expect(chips.some((c) => c.text().includes('chart.png'))).toBe(false)
  })

  // The inline-image predicate must mirror the backend's _INLINE_SAFE_MIME
  // allowlist, not the broader `image/*` category: a type the backend serves as
  // `application/octet-stream` with an attachment disposition cannot be decoded
  // by an <img>, and asking for it costs two failed presign round-trips before
  // the component collapses to an unlabelled fallback button.
  it('renders an svg attachment as a download chip, not an inline image', async () => {
    const message: DisplayMessage = {
      ...agentMessage({}),
      attachments: [attachment({ id: 'svg1', filename: 'chart.svg', mime: 'image/svg+xml' })],
    }
    const wrapper = await renderView(ChatroomMessageBubble, { props: { ...baseProps, message } })
    const chips = wrapper.findAll('.attachment-link')
    expect(chips.some((c) => c.text().includes('chart.svg'))).toBe(true)
  })

  it('treats a parameterised svg mime identically', async () => {
    // Mirrors the backend normalisation pinned in
    // test_attachment_download_disposition.py, so the two sides stay aligned on
    // parameter handling rather than only on the bare type.
    const message: DisplayMessage = {
      ...agentMessage({}),
      attachments: [
        attachment({ id: 'svg2', filename: 'plot.svg', mime: 'image/svg+xml; charset=utf-8' }),
      ],
    }
    const wrapper = await renderView(ChatroomMessageBubble, { props: { ...baseProps, message } })
    const chips = wrapper.findAll('.attachment-link')
    expect(chips.some((c) => c.text().includes('plot.svg'))).toBe(true)
  })

  // Until the expiry sweep existed nothing ever wrote `expired`, so this branch
  // was unreachable and the two placeholder states were indistinguishable in
  // practice. They must stay distinct: quarantine is a scan verdict and expiry
  // is not, and the sweep deliberately never overwrites one with the other.
  it('renders the expired placeholder for an expired attachment', async () => {
    const message: DisplayMessage = {
      ...agentMessage({}),
      attachments: [attachment({ id: 'e1', filename: 'old.pdf', status: 'expired' })],
    }
    const wrapper = await renderView(ChatroomMessageBubble, { props: { ...baseProps, message } })
    const gone = wrapper.find('.attachment-gone')
    expect(gone.exists()).toBe(true)
    expect(gone.text()).toContain('conversation.chatroom.attachmentExpired')
    expect(wrapper.findAll('.attachment-link')).toHaveLength(0)
  })

  it('renders the quarantined placeholder for a quarantined attachment', async () => {
    const message: DisplayMessage = {
      ...agentMessage({}),
      attachments: [attachment({ id: 'q1', filename: 'bad.exe', status: 'quarantined' })],
    }
    const wrapper = await renderView(ChatroomMessageBubble, { props: { ...baseProps, message } })
    expect(wrapper.find('.attachment-gone').text()).toContain(
      'conversation.chatroom.attachmentQuarantined',
    )
  })

  it('still renders a png inline', async () => {
    // A guard, not a failing test: pins that narrowing the predicate did not
    // take raster rendering with it.
    server.use(
      http.get('/api/attachments/img2', () =>
        HttpResponse.json({
          ...attachment({ id: 'img2', filename: 'raster.png', mime: 'image/png' }),
          url: 'https://store/raster.png',
        }),
      ),
    )
    const message: DisplayMessage = {
      ...agentMessage({}),
      attachments: [attachment({ id: 'img2', filename: 'raster.png', mime: 'image/png' })],
    }
    const wrapper = await renderView(ChatroomMessageBubble, { props: { ...baseProps, message } })
    const chips = wrapper.findAll('.attachment-link')
    expect(chips.some((c) => c.text().includes('raster.png'))).toBe(false)
  })
})
