import { describe, it, expect } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { QueryClient } from '@tanstack/vue-query'
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

const CONFLICT = {
  type: 'https://smap.local/problems/conversation/version-mismatch',
  title: 'Version mismatch',
  status: 409,
}
const BOOM = { type: 'https://smap.local/problems/internal', title: 'Internal', status: 500 }

describe('useChatroomSettings.setFlag', () => {
  it('leaves the flag in the server position when the PATCH is rejected (F-7)', async () => {
    server.use(
      http.get('/api/chatrooms/:id', () =>
        HttpResponse.json(makeChatroom({ allow_guest_links: false })),
      ),
      http.patch('/api/chatrooms/:id', () => HttpResponse.json(BOOM, { status: 500 })),
    )
    const wrapper = await renderView(Host, { props: { chatroomId: 'cr_1' } })
    await wrapper.vm.loadRoom()

    await wrapper.vm.setFlag('allow_guest_links', true)
    await flushPromises()

    // A security-relevant control must never show a state the server rejected.
    expect(wrapper.vm.flags.allow_guest_links).toBe(false)
    expect(wrapper.vm.saveError).toBe('conversation.settings.saveFailed')
  })

  it('resyncs every form field from the refetched room on a 409 (F-8)', async () => {
    server.use(
      http.get('/api/chatrooms/:id', () =>
        HttpResponse.json(makeChatroom({ name: 'Room One', version: 1 })),
      ),
      http.patch('/api/chatrooms/:id', () => HttpResponse.json(CONFLICT, { status: 409 })),
    )
    const wrapper = await renderView(Host, { props: { chatroomId: 'cr_1' } })
    await wrapper.vm.loadRoom()
    expect(wrapper.vm.name).toBe('Room One')

    // Another user renamed the room in the meantime.
    server.use(
      http.get('/api/chatrooms/:id', () =>
        HttpResponse.json(makeChatroom({ name: 'Renamed By B', version: 2 })),
      ),
    )
    await wrapper.vm.setFlag('allow_org_members', true)
    await flushPromises()

    // Refreshing the version without refreshing the content is what let the
    // next save launder the operator's stale name over B's rename.
    expect(wrapper.vm.name).toBe('Renamed By B')
    expect(wrapper.vm.room?.version).toBe(2)
    expect(wrapper.vm.saveError).toBe('conversation.settings.versionConflict')
  })

  it('sends only the toggled flag, never the name draft', async () => {
    let body: Record<string, unknown> | null = null
    server.use(
      http.get('/api/chatrooms/:id', () => HttpResponse.json(makeChatroom())),
      http.patch('/api/chatrooms/:id', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(makeChatroom({ allow_org_members: true, version: 2 }))
      }),
    )
    const wrapper = await renderView(Host, { props: { chatroomId: 'cr_1' } })
    await wrapper.vm.loadRoom()

    // A half-typed rename the user has not submitted must not ride along.
    wrapper.vm.name = 'Half-typed re'
    await wrapper.vm.setFlag('allow_org_members', true)
    await flushPromises()

    expect(body).toEqual({ allow_org_members: true })
  })

  it('applies the new flag once the server confirms it', async () => {
    server.use(
      http.get('/api/chatrooms/:id', () => HttpResponse.json(makeChatroom())),
      http.patch('/api/chatrooms/:id', () =>
        HttpResponse.json(makeChatroom({ allow_org_members: true, version: 2 })),
      ),
    )
    const wrapper = await renderView(Host, { props: { chatroomId: 'cr_1' } })
    await wrapper.vm.loadRoom()

    await wrapper.vm.setFlag('allow_org_members', true)
    await flushPromises()

    expect(wrapper.vm.flags.allow_org_members).toBe(true)
    expect(wrapper.vm.saveError).toBe(null)
  })
})

describe('useChatroomSettings.loadRoom', () => {
  it('revalidates a cache-painted room instead of trusting the cache (F-8)', async () => {
    server.use(
      http.get('/api/chatrooms/:id', () =>
        HttpResponse.json(makeChatroom({ name: 'Fresh Name', version: 2 })),
      ),
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    // The app-shell sidebar's recent-chatrooms entry carries a 60s staleTime,
    // so a cache hit here can be a minute behind the server.
    qc.setQueryData(
      ['conversation', 'chatrooms', 'ws_1'],
      [makeChatroom({ name: 'Stale Name', version: 1 })],
    )
    const wrapper = await renderView(Host, { props: { chatroomId: 'cr_1' }, queryClient: qc })

    await wrapper.vm.loadRoom()
    expect(wrapper.vm.name).toBe('Stale Name') // instant paint from cache
    await flushPromises()

    expect(wrapper.vm.name).toBe('Fresh Name')
    expect(wrapper.vm.room?.version).toBe(2)
  })

  it('does not let the revalidation overwrite a rename in progress', async () => {
    server.use(
      http.get('/api/chatrooms/:id', () =>
        HttpResponse.json(makeChatroom({ name: 'Fresh Name', version: 2 })),
      ),
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    qc.setQueryData(
      ['conversation', 'chatrooms', 'ws_1'],
      [makeChatroom({ name: 'Stale Name', version: 1 })],
    )
    const wrapper = await renderView(Host, { props: { chatroomId: 'cr_1' }, queryClient: qc })

    await wrapper.vm.loadRoom()
    wrapper.vm.name = 'User Is Typing'
    await flushPromises()

    // The version must still advance — only the draft the user owns is kept.
    expect(wrapper.vm.name).toBe('User Is Typing')
    expect(wrapper.vm.room?.version).toBe(2)
  })

  it('drops a revalidation that lands after a newer save', async () => {
    // The GET is issued at paint time but resolves after the toggle's PATCH.
    // Applying it would wind the form back to the pre-save version, whose
    // next save would 409 against the room the user just wrote.
    let releaseGet: (() => void) | null = null
    server.use(
      http.get('/api/chatrooms/:id', async () => {
        if (!releaseGet) await new Promise<void>((res) => (releaseGet = res))
        return HttpResponse.json(makeChatroom({ name: 'Room One', version: 1 }))
      }),
      http.patch('/api/chatrooms/:id', () =>
        HttpResponse.json(
          makeChatroom({ name: 'Room One', allow_org_members: true, version: 2 }),
        ),
      ),
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    qc.setQueryData(
      ['conversation', 'chatrooms', 'ws_1'],
      [makeChatroom({ name: 'Room One', version: 1 })],
    )
    const wrapper = await renderView(Host, { props: { chatroomId: 'cr_1' }, queryClient: qc })

    await wrapper.vm.loadRoom() // paints from cache, revalidation left hanging
    await wrapper.vm.setFlag('allow_org_members', true)
    await flushPromises()
    expect(wrapper.vm.room?.version).toBe(2)

    ;(releaseGet as unknown as () => void)()
    await flushPromises()

    expect(wrapper.vm.room?.version).toBe(2)
    expect(wrapper.vm.flags.allow_org_members).toBe(true)
  })
})
