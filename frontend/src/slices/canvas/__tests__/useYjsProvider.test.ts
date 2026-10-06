// docs/tasks/2026-10-05-guest-session-backend-hardening (AC-3, §7.3): the canvas
// socket closes 4403 (access lost) or 4404 (room gone). Neither is cured by a
// retry, so the provider stops reconnecting and leaves the explanation to the room.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { useYjsProvider } from '../composables/useYjsProvider'

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

function mountProvider() {
  return mount(
    defineComponent({
      setup() {
        useYjsProvider(ref('cv_1'))
        return () => h('div')
      },
    }),
  )
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
    const wrapper = mountProvider()
    await settle()
    const socket = FakeWebSocket.instances.at(-1)!
    socket.open()

    socket.serverClose(code)
    await settle(1200)

    expect(FakeWebSocket.instances).toHaveLength(1)
    wrapper.unmount()
  })

  it('still reconnects after an ordinary drop', async () => {
    const wrapper = mountProvider()
    await settle()
    FakeWebSocket.instances.at(-1)!.open()

    FakeWebSocket.instances.at(-1)!.serverClose(1006)
    await settle(1200)

    expect(FakeWebSocket.instances.length).toBeGreaterThan(1)
    wrapper.unmount()
  })
})
