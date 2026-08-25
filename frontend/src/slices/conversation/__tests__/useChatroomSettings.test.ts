import { describe, it, expect } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { QueryClient } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { deferred, renderView } from '../../../../tests/utils'
import { useConfirmDialog } from '@shared/composables'
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

  it('keeps an unsubmitted rename when a toggle succeeds', async () => {
    // The successful PATCH returns the room with its *old* name, because the
    // toggle deliberately does not send one. Adopting that response wholesale
    // would delete the draft the user is still typing — the mirror image of
    // the rename bleed this task removed, and just as silent.
    server.use(
      http.get('/api/chatrooms/:id', () => HttpResponse.json(makeChatroom({ name: 'Room One' }))),
      http.patch('/api/chatrooms/:id', () =>
        HttpResponse.json(
          makeChatroom({ name: 'Room One', allow_org_members: true, version: 2 }),
        ),
      ),
    )
    const wrapper = await renderView(Host, { props: { chatroomId: 'cr_1' } })
    await wrapper.vm.loadRoom()

    wrapper.vm.name = 'Half-typed re'
    await wrapper.vm.setFlag('allow_org_members', true)
    await flushPromises()

    expect(wrapper.vm.name).toBe('Half-typed re')
    expect(wrapper.vm.flags.allow_org_members).toBe(true)
    expect(wrapper.vm.room?.version).toBe(2)
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

  // R13.04's exclusive pair has to move together in BOTH directions. Switching
  // the group tier on already cleared `allow_project_members`, so switching it
  // off on its own leaves the room with no member tier at all — every
  // non-moderator silently loses read and send on a room they were in.
  async function setUpGroupTierOff(
    room: Partial<Chatroom>,
  ): Promise<Record<string, unknown> | null> {
    let body: Record<string, unknown> | null = null
    server.use(
      http.get('/api/chatrooms/:id', () =>
        HttpResponse.json(
          makeChatroom({ allow_project_members: false, allow_member_groups: true, ...room }),
        ),
      ),
      http.patch('/api/chatrooms/:id', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(makeChatroom({ ...room, version: 2 }))
      }),
    )
    const wrapper = await renderView(Host, { props: { chatroomId: 'cr_1' } })
    await wrapper.vm.loadRoom()
    await wrapper.vm.setFlag('allow_member_groups', false)
    await flushPromises()
    return body
  }

  it('restores the project tier when the group tier is switched off', async () => {
    expect(await setUpGroupTierOff({})).toEqual({
      allow_member_groups: false,
      allow_project_members: true,
    })
  })

  it('does not widen a room that another tier still admits members to', async () => {
    // Org-wide and owners-only rooms are narrower on purpose; opening them to
    // the whole project would be its own change, and one nobody asked for.
    expect(await setUpGroupTierOff({ allow_org_members: true })).toEqual({
      allow_member_groups: false,
    })
    expect(await setUpGroupTierOff({ allow_project_owners_only: true })).toEqual({
      allow_member_groups: false,
    })
  })

  // AC-18. Enabling the group tier is not a submission setting, it is a change
  // to who can enter the room: R13.04 makes the pair exclusive, so every project
  // member in no bound group loses access the moment this lands — guests
  // permanently, since they cannot belong to a group at all (OQ-1). The server
  // refusal it mirrors is `chatroom_service.py::_assert_flag_exclusivity`.
  async function enableGroupTier(
    room: Partial<Chatroom> = {},
  ): Promise<{
    body: Record<string, unknown> | null
    dialog: ReturnType<typeof useConfirmDialog>
    start: () => Promise<void>
    flags: () => Record<string, boolean>
  }> {
    const captured: { body: Record<string, unknown> | null } = { body: null }
    server.use(
      http.get('/api/chatrooms/:id', () =>
        HttpResponse.json(makeChatroom({ allow_project_members: true, ...room })),
      ),
      http.patch('/api/chatrooms/:id', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(
          makeChatroom({ allow_project_members: false, allow_member_groups: true, version: 2 }),
        )
      }),
    )
    const wrapper = await renderView(Host, { props: { chatroomId: 'cr_1' } })
    await wrapper.vm.loadRoom()
    return {
      get body() {
        return captured.body
      },
      dialog: useConfirmDialog(),
      start: () => wrapper.vm.setFlag('allow_member_groups', true),
      flags: () => wrapper.vm.flags as Record<string, boolean>,
    }
  }

  it('warns before switching the room to group access, not after', async () => {
    const ctx = await enableGroupTier()

    const pending = ctx.start()
    await flushPromises()

    // Asked BEFORE the patch: after it, the students are already locked out.
    expect(ctx.dialog.state.open).toBe(true)
    expect(ctx.dialog.state.message).toBe(
      'conversation.settings.memberGroupsExclusiveWarning',
    )
    expect(ctx.body).toBeNull()

    ctx.dialog.handleConfirm()
    await pending
    await flushPromises()

    expect(ctx.body).toEqual({ allow_member_groups: true, allow_project_members: false })
  })

  it('changes nothing when the warning is declined', async () => {
    const ctx = await enableGroupTier()

    const pending = ctx.start()
    await flushPromises()
    ctx.dialog.handleCancel()
    await pending
    await flushPromises()

    expect(ctx.body).toBeNull()
    expect(ctx.flags().allow_member_groups).toBe(false)
    expect(ctx.flags().allow_project_members).toBe(true)
  })

  it('does not warn when there is no project-member access to remove', async () => {
    // An org-wide or owners-only room loses nothing here, and a warning about a
    // tier that is already off is the kind of prompt people learn to click past.
    const ctx = await enableGroupTier({ allow_project_members: false, allow_org_members: true })

    await ctx.start()
    await flushPromises()

    expect(ctx.dialog.state.open).toBe(false)
    expect(ctx.body).toEqual({ allow_member_groups: true, allow_project_members: false })
  })

  it('does not count a guest link as a member tier', async () => {
    // A guest link admits whoever holds it, not the project's members, so a room
    // left on guests alone is still closed to everyone it was built for.
    expect(await setUpGroupTierOff({ allow_guest_links: true })).toEqual({
      allow_member_groups: false,
      allow_project_members: true,
    })
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
    const held = deferred<void>()
    let firstGet = true
    server.use(
      http.get('/api/chatrooms/:id', async () => {
        if (firstGet) {
          firstGet = false
          await held.promise
        }
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

    held.resolve()
    await flushPromises()

    expect(wrapper.vm.room?.version).toBe(2)
    expect(wrapper.vm.flags.allow_org_members).toBe(true)
  })
})
