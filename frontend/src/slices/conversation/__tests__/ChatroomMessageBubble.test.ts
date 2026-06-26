import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import ChatroomMessageBubble from '../components/ChatroomMessageBubble.vue'
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
})
