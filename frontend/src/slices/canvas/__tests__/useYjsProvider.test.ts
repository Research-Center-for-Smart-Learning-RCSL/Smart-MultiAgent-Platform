// docs/tasks/2026-10-05-guest-session-backend-hardening (AC-3, §7.3): the canvas
// socket closes 4403 (access lost) or 4404 (room gone). Neither is cured by a
// retry, so the provider stops reconnecting and leaves the explanation to the room.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, ref, type Ref } from 'vue'
import { mount } from '@vue/test-utils'
import { setAccessToken } from '@shared/transport'
import { useYjsProvider, type YjsProviderState } from '../composables/useYjsProvider'

class FakeWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  static instances: FakeWebSocket[] = []
  readyState = FakeWebSocket.CONNECTING
  onopen: (() => void) | null = null
  onclose: ((ev: { code: number; reason: string }) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  constructor() {
    FakeWebSocket.instances.push(this)
  }
  send(): void {}
  close(): void {
    this.readyState = FakeWebSocket.CLOSED
  }
  open(): void {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }
  serverClose(code: number): void {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({ code, reason: '' })
  }
}

async function settle(ms = 50): Promise<void> {
  await new Promise((r) => setTimeout(r, ms))
}

function mountProvider(displayName: Ref<string | null> = ref(null)) {
  let state: YjsProviderState | null = null
  const wrapper = mount(
    defineComponent({
      setup() {
        state = useYjsProvider(ref('cv_1'), displayName)
        return () => h('div')
      },
    }),
  )
  return { wrapper, state: state! }
}

function unsignedToken(claims: Record<string, unknown>): string {
  return `h.${btoa(JSON.stringify(claims)).replace(/=+$/, '')}.s`
}

function localName(state: YjsProviderState): unknown {
  return (state.awareness.value.getLocalState() as { user?: { name?: unknown } } | null)?.user?.name
}

describe('useYjsProvider close codes', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it.each([4403, 4404])('stops reconnecting after a %s close', async (code) => {
    const { wrapper } = mountProvider()
    await settle()
    const socket = FakeWebSocket.instances.at(-1)!
    socket.open()

    socket.serverClose(code)
    await settle(1200)

    expect(FakeWebSocket.instances).toHaveLength(1)
    wrapper.unmount()
  })

  it('still reconnects after an ordinary drop', async () => {
    const { wrapper } = mountProvider()
    await settle()
    FakeWebSocket.instances.at(-1)!.open()

    FakeWebSocket.instances.at(-1)!.serverClose(1006)
    await settle(1200)

    expect(FakeWebSocket.instances.length).toBeGreaterThan(1)
    wrapper.unmount()
  })
})

// docs/tasks/2026-10-07-canvas-awareness-names (AC-1..AC-4): the cursor name came
// from token claims, which a member token does not carry, so it was the full user
// id; and it was set only on connect, so a rename never reached an open canvas.
describe('useYjsProvider awareness name', () => {
  const USER = '3f2a9c1e-0000-4000-8000-000000000001'

  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    setAccessToken(unsignedToken({ sub: USER, token_use: 'access' }))
  })

  afterEach(() => {
    setAccessToken(null)
    vi.unstubAllGlobals()
  })

  async function connected(displayName: Ref<string | null>) {
    const mounted = mountProvider(displayName)
    await settle()
    FakeWebSocket.instances.at(-1)!.open()
    await settle()
    return mounted
  }

  it('broadcasts the display name it is given, not the token subject', async () => {
    const { wrapper, state } = await connected(ref('Alice'))

    expect(localName(state)).toBe('Alice')
    wrapper.unmount()
  })

  it('re-broadcasts when the display name changes, without reconnecting', async () => {
    const name = ref<string | null>('Alice')
    const { wrapper, state } = await connected(name)

    name.value = 'Alicia'
    await settle()

    expect(localName(state)).toBe('Alicia')
    expect(FakeWebSocket.instances).toHaveLength(1)
    wrapper.unmount()
  })

  it('falls back to the eight-character id, never to an email', async () => {
    setAccessToken(unsignedToken({ sub: USER, token_use: 'access', email: 'alice@school.test' }))
    const { wrapper, state } = await connected(ref(null))

    expect(localName(state)).toBe(USER.slice(0, 8))
    expect(String(localName(state))).not.toContain('alice')
    wrapper.unmount()
  })
})
