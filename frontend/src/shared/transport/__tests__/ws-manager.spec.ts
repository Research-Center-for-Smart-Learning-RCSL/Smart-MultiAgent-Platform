import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Type-only, so it is erased before the runtime import below — the module must
// not load until the WebSocket global has been stubbed.
import type { Channel as ChannelClass } from '../ws-manager'

// Locks the Channel lifecycle contract from
// docs/tasks/2026-07-22-chatroom-socket-lifecycle/spec.md §8:
//   - the client half of the ping/pong heartbeat the server's idle reaper is
//     written around (F-1)
//   - "connected" meaning the server let the socket live, not that the HTTP
//     upgrade completed (F-4)
//   - the per-user cap surfacing as its own signal rather than as a generic
//     degraded channel (Q-5)
//
// Timing assertions here are load-bearing, not incidental: the ping interval
// must stay under the server's _IDLE_TIMEOUT_SECONDS = 120 and the stability
// window must exceed a rejection's accept-to-close round trip.

vi.mock('../axios', () => ({
  fetchWsTicket: vi.fn(async () => 'ticket-1'),
  getAccessToken: vi.fn(() => 'access-token'),
  // Far enough out that the token-refresh timer never fires inside a test's
  // advance window and pollutes the captured frames.
  decodeJwtClaims: vi.fn(() => ({ exp: Math.floor(Date.now() / 1000) + 3600 })),
  refreshAccessToken: vi.fn(async () => 'access-token'),
}))

class FakeWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  static instances: FakeWebSocket[] = []

  readyState = FakeWebSocket.CONNECTING
  sent: string[] = []
  closedWith: { code?: number; reason?: string } | null = null

  onopen: (() => void) | null = null
  onclose: ((ev: { code: number; reason: string }) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onerror: (() => void) | null = null

  constructor(
    readonly url: string,
    readonly protocols?: string | string[],
  ) {
    FakeWebSocket.instances.push(this)
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(code?: number, reason?: string): void {
    this.closedWith = { code, reason }
    this.readyState = FakeWebSocket.CLOSED
  }

  /** Manual trigger — the server accepted the upgrade. */
  triggerOpen(): void {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }

  /** Manual trigger — 1006 is the browser's code for an abnormal close. */
  triggerClose(code = 1006, reason = ''): void {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({ code, reason })
  }
}

/** The socket the channel most recently constructed. */
function latest(): FakeWebSocket {
  const socket = FakeWebSocket.instances.at(-1)
  if (!socket) throw new Error('no socket was constructed')
  return socket
}

/** Frames the channel sent on the current socket, parsed. */
function framesOf(socket: FakeWebSocket): Array<Record<string, unknown>> {
  return socket.sent.map((raw) => JSON.parse(raw) as Record<string, unknown>)
}

let Channel: typeof ChannelClass

beforeEach(async () => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  vi.useFakeTimers()
  // Imported after the global stub so the module closes over the fake.
  ;({ Channel } = await import('../ws-manager'))
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/**
 * Drive the channel to a constructed socket. `connect()` is sync but the
 * handshake awaits an HTTP ticket, so the socket only exists after the
 * microtask queue drains.
 */
async function openChannel(path = '/chatroom/room-1') {
  const channel = new Channel(path)
  channel.connect()
  await vi.advanceTimersByTimeAsync(0)
  return channel
}

/** One full accept-then-close cycle, as a cap rejection produces. */
async function rejectionCycle(code = 1006): Promise<void> {
  latest().triggerOpen()
  latest().triggerClose(code)
  await vi.advanceTimersByTimeAsync(0)
}

describe('Channel heartbeat (F-1)', () => {
  it('sends a ping within the server idle window while the socket is open', async () => {
    await openChannel()
    const socket = latest()
    socket.triggerOpen()

    await vi.advanceTimersByTimeAsync(30_000)
    expect(framesOf(socket)).toEqual([{ type: 'ping' }])

    await vi.advanceTimersByTimeAsync(30_000)
    expect(framesOf(socket)).toEqual([{ type: 'ping' }, { type: 'ping' }])

    // The property that actually matters: the server reaps at 120s, so the
    // socket must never be silent that long.
    expect(socket.sent.length).toBeGreaterThanOrEqual(2)
  })

  it('stops pinging once the socket closes', async () => {
    await openChannel()
    const socket = latest()
    socket.triggerOpen()
    await vi.advanceTimersByTimeAsync(30_000)
    const afterOpen = socket.sent.length

    socket.triggerClose()
    await vi.advanceTimersByTimeAsync(120_000)

    // A leaked interval would keep firing against the dead socket and, worse,
    // stack a second interval on every reconnect.
    expect(socket.sent.length).toBe(afterOpen)
  })

  it('stops pinging on disconnect() and on close()', async () => {
    const disconnected = await openChannel('/chatroom/room-disconnect')
    const a = latest()
    a.triggerOpen()
    disconnected.disconnect()
    await vi.advanceTimersByTimeAsync(120_000)
    expect(a.sent).toEqual([])

    const closed = await openChannel('/chatroom/room-close')
    const b = latest()
    b.triggerOpen()
    closed.close()
    await vi.advanceTimersByTimeAsync(120_000)
    expect(b.sent).toEqual([])
  })
})

describe('Channel connection stability (F-4)', () => {
  it('an accepted socket that closes immediately does not reset the backoff', async () => {
    await openChannel()

    await rejectionCycle()
    expect(FakeWebSocket.instances).toHaveLength(1)

    // First retry at the initial backoff of 1000ms.
    await vi.advanceTimersByTimeAsync(1_000)
    expect(FakeWebSocket.instances).toHaveLength(2)

    await rejectionCycle()

    // Second retry must be at 2000ms — the doubled backoff, not a reset one.
    await vi.advanceTimersByTimeAsync(1_999)
    expect(FakeWebSocket.instances).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(2)
    expect(FakeWebSocket.instances).toHaveLength(3)
  })

  it('declares the channel degraded after three accept-then-close cycles', async () => {
    const channel = await openChannel()
    const onDegraded = vi.fn()
    channel.onDegraded(onDegraded)
    // onDegraded pushes the current value on subscribe.
    expect(onDegraded).toHaveBeenCalledWith(false)

    await rejectionCycle()
    await vi.advanceTimersByTimeAsync(1_000)
    await rejectionCycle()
    await vi.advanceTimersByTimeAsync(2_000)
    await rejectionCycle()

    expect(onDegraded).toHaveBeenCalledWith(true)
  })

  it('a socket that stays open past the stability window resets the backoff', async () => {
    await openChannel()

    // Raise the backoff with two failures: retries are now at 4000ms.
    await rejectionCycle()
    await vi.advanceTimersByTimeAsync(1_000)
    await rejectionCycle()
    await vi.advanceTimersByTimeAsync(2_000)

    // A connection that survives the stability window is a real success.
    latest().triggerOpen()
    await vi.advanceTimersByTimeAsync(5_000)
    const beforeDrop = FakeWebSocket.instances.length
    latest().triggerClose()
    await vi.advanceTimersByTimeAsync(0)

    await vi.advanceTimersByTimeAsync(999)
    expect(FakeWebSocket.instances).toHaveLength(beforeDrop)
    await vi.advanceTimersByTimeAsync(2)
    expect(FakeWebSocket.instances).toHaveLength(beforeDrop + 1)
  })
})

describe('Channel per-user cap signal (Q-5)', () => {
  const CAP_CLOSE_CODE = 4429

  it('a close with the cap code raises the cap-reached signal', async () => {
    const channel = await openChannel()
    const onCapReached = vi.fn()
    channel.onCapReached(onCapReached)
    expect(onCapReached).toHaveBeenCalledWith(false)

    await rejectionCycle(CAP_CLOSE_CODE)

    expect(onCapReached).toHaveBeenCalledWith(true)
  })

  it('a cap-reached channel clears the signal once a connection stabilizes', async () => {
    const channel = await openChannel()
    const onCapReached = vi.fn()
    channel.onCapReached(onCapReached)

    await rejectionCycle(CAP_CLOSE_CODE)
    expect(onCapReached).toHaveBeenLastCalledWith(true)

    await vi.advanceTimersByTimeAsync(1_000)
    latest().triggerOpen()
    // Still set: an open socket is not yet proof the server kept it.
    expect(onCapReached).toHaveBeenLastCalledWith(true)

    await vi.advanceTimersByTimeAsync(5_000)
    expect(onCapReached).toHaveBeenLastCalledWith(false)
  })

  it('a close with a non-cap code does not raise the cap-reached signal', async () => {
    const channel = await openChannel()
    const onCapReached = vi.fn()
    channel.onCapReached(onCapReached)

    // 1013 is shared by the idle reaper and the slow-consumer path, so it must
    // not be read as the cap.
    await rejectionCycle(1013)

    expect(onCapReached).not.toHaveBeenCalledWith(true)
  })
})
