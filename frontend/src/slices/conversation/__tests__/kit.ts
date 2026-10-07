// Shared harness for the ChatroomView guest suites: a socket the test drives by
// hand, an unsigned guest token, and the settle the view's async reads need.

import { nextTick } from 'vue'
import type { renderView } from '../../../../tests/utils'
import ChatroomComposer from '../components/ChatroomComposer.vue'

export class FakeWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  static instances: FakeWebSocket[] = []
  readyState = FakeWebSocket.CONNECTING
  closedWith: number | null = null
  onopen: (() => void) | null = null
  onclose: ((ev: { code: number; reason: string }) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  constructor() {
    FakeWebSocket.instances.push(this)
  }
  send(): void {}
  close(code?: number): void {
    this.closedWith = code ?? 1000
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
  frame(event: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(event) })
  }
}

export function unsignedToken(claims: Record<string, unknown>): string {
  return `h.${btoa(JSON.stringify(claims)).replace(/=+$/, '')}.s`
}

export async function settle(): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await nextTick()
}

export function composerDisabled(wrapper: Awaited<ReturnType<typeof renderView>>): boolean {
  return wrapper.findComponent(ChatroomComposer).props('disabled') === true
}
