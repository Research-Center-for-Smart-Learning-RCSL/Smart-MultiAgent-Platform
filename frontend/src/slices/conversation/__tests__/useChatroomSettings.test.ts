import { describe, it, expect } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useChatroomSettings } from '../composables/useChatroomSettings'
import type { Chatroom } from '../types'

function makeChatroom(overrides: Partial<Chatroom> = {}): Chatroom {
  return {
    id: 'cr_1',
    workspace_id: 'ws_1',
    name: 'Room One',
    allow_org_members: false,
    allow_project_members: true,
    allow_project_owners_only: false,
    allow_guest_links: false,
    guest_token: 'tok_1',
    version: 1,
    created_at: new Date().toISOString(),
    created_by_user_id: 'user_1',
    disclose_observers: true,
    observers_present: false,
    ...overrides,
  }
}

// A minimal Options-API host so the composable's returned refs/functions are
// exposed on `wrapper.vm` (a <script setup> SFC would not expose internals).
const Host = defineComponent({
  props: { chatroomId: { type: String, required: true } },
  setup(props) {
    return useChatroomSettings(props.chatroomId)
  },
  render: () => h('div'),
})

describe('useChatroomSettings.saveDisclosure', () => {
  it('reverts the optimistic toggle on a generic save failure', async () => {
    server.use(
      http.get('/api/chatrooms/:id', () => HttpResponse.json(makeChatroom())),
      http.patch('/api/chatrooms/:id', () =>
        HttpResponse.json(
          { type: 'https://smap.local/problems/internal', title: 'Internal', status: 500 },
          { status: 500 },
        ),
      ),
    )
    const wrapper = await renderView(Host, { props: { chatroomId: 'cr_1' } })
    await wrapper.vm.loadRoom()
    expect(wrapper.vm.flags.disclose_observers).toBe(true)

    await wrapper.vm.saveDisclosure(false)
    await flushPromises()

    // The PATCH failed — the toggle must not stay flipped to a value the
    // server never accepted.
    expect(wrapper.vm.flags.disclose_observers).toBe(true)
    expect(wrapper.vm.saveError).toBe('conversation.settings.saveFailed')
  })

  it('resyncs flags from the refetched room on a 409 version conflict', async () => {
    // The server's authoritative state disagrees with what the optimistic
    // toggle assumed — the refetch must win, not the stale optimistic value.
    server.use(
      http.get('/api/chatrooms/:id', () => HttpResponse.json(makeChatroom({ disclose_observers: true }))),
      http.patch('/api/chatrooms/:id', () =>
        HttpResponse.json(
          {
            type: 'https://smap.local/problems/conversation/version-mismatch',
            title: 'Version mismatch',
            status: 409,
          },
          { status: 409 },
        ),
      ),
    )
    const wrapper = await renderView(Host, { props: { chatroomId: 'cr_1' } })
    await wrapper.vm.loadRoom()

    server.use(
      http.get('/api/chatrooms/:id', () =>
        HttpResponse.json(makeChatroom({ disclose_observers: true, version: 2 })),
      ),
    )
    await wrapper.vm.saveDisclosure(false)
    await flushPromises()

    // Refetched room says disclosure is still true — flags must reflect that,
    // not the optimistic `false` the toggle click set.
    expect(wrapper.vm.flags.disclose_observers).toBe(true)
    expect(wrapper.vm.room?.version).toBe(2)
    expect(wrapper.vm.saveError).toBe('conversation.settings.versionConflict')
  })

  it('applies the new value once the server confirms it', async () => {
    server.use(
      http.get('/api/chatrooms/:id', () => HttpResponse.json(makeChatroom({ disclose_observers: true }))),
      http.patch('/api/chatrooms/:id', () =>
        HttpResponse.json(makeChatroom({ disclose_observers: false, version: 2 })),
      ),
    )
    const wrapper = await renderView(Host, { props: { chatroomId: 'cr_1' } })
    await wrapper.vm.loadRoom()

    await wrapper.vm.saveDisclosure(false)
    await flushPromises()

    expect(wrapper.vm.flags.disclose_observers).toBe(false)
    expect(wrapper.vm.saveError).toBe(null)
  })
})
