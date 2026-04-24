// WsManager singleton — central WebSocket management (R24.14).
//
// Usage:  const ch = wsManager.channel('/chatroom/abc')
//         ch.subscribe('message.created', handler)
//         ch.close()
//
// Handles reconnect with exponential backoff, bearer-subprotocol auth,
// and in-socket token refresh before JWT expiry.

import { getAccessToken } from './axios'

export interface ChannelEvent {
  type: string
  [k: string]: unknown
}

type EventHandler = (event: ChannelEvent) => void
type StatusHandler = (connected: boolean) => void

const INITIAL_BACKOFF_MS = 1_000
const MAX_BACKOFF_MS = 30_000
const REFRESH_MARGIN_MS = 60_000

export class Channel {
  private socket: WebSocket | null = null
  private handlers = new Map<string, Set<EventHandler>>()
  private statusHandlers = new Set<StatusHandler>()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private refreshTimer: ReturnType<typeof setTimeout> | null = null
  private backoff = INITIAL_BACKOFF_MS
  private closed = false

  constructor(private readonly path: string) {}

  subscribe(eventName: string, handler: EventHandler): () => void {
    let set = this.handlers.get(eventName)
    if (!set) {
      set = new Set()
      this.handlers.set(eventName, set)
    }
    set.add(handler)
    return () => set!.delete(handler)
  }

  onStatus(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler)
    return () => this.statusHandlers.delete(handler)
  }

  send(payload: Record<string, unknown>): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload))
    }
  }

  connect(): void {
    if (this.closed) return
    const token = getAccessToken()
    if (!token) {
      this.scheduleReconnect()
      return
    }

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${window.location.host}/ws${this.path}`

    try {
      this.socket = new WebSocket(url, [`bearer.${token}`])
    } catch {
      this.scheduleReconnect()
      return
    }

    this.socket.onopen = () => {
      this.backoff = INITIAL_BACKOFF_MS
      this.emitStatus(true)
      this.scheduleTokenRefresh()
    }

    this.socket.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data as string) as ChannelEvent
        this.dispatch(event)
      } catch {
        // skip malformed frames
      }
    }

    this.socket.onclose = () => {
      this.emitStatus(false)
      this.clearRefreshTimer()
      if (!this.closed) this.scheduleReconnect()
    }

    this.socket.onerror = () => {
      // onclose fires immediately after
    }
  }

  close(): void {
    this.closed = true
    this.clearReconnectTimer()
    this.clearRefreshTimer()
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) {
      this.socket.close(1000, 'channel closed')
    }
    this.socket = null
    this.handlers.clear()
    this.statusHandlers.clear()
  }

  private dispatch(event: ChannelEvent): void {
    const wildcard = this.handlers.get('*')
    if (wildcard) wildcard.forEach((h) => h(event))

    const typed = this.handlers.get(event.type)
    if (typed) typed.forEach((h) => h(event))
  }

  private emitStatus(connected: boolean): void {
    this.statusHandlers.forEach((h) => h(connected))
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null || this.closed) return
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, this.backoff)
    this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS)
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }

  private scheduleTokenRefresh(): void {
    this.clearRefreshTimer()
    this.refreshTimer = setTimeout(() => {
      const token = getAccessToken()
      if (token && this.socket?.readyState === WebSocket.OPEN) {
        this.socket.send(
          JSON.stringify({ type: 'refresh', access_token: token }),
        )
      }
      this.scheduleTokenRefresh()
    }, REFRESH_MARGIN_MS)
  }

  private clearRefreshTimer(): void {
    if (this.refreshTimer !== null) {
      clearTimeout(this.refreshTimer)
      this.refreshTimer = null
    }
  }
}

class WsManager {
  private channels = new Map<string, Channel>()

  channel(path: string): Channel {
    let ch = this.channels.get(path)
    if (!ch) {
      ch = new Channel(path)
      this.channels.set(path, ch)
    }
    return ch
  }

  close(path: string): void {
    const ch = this.channels.get(path)
    if (ch) {
      ch.close()
      this.channels.delete(path)
    }
  }

  closeAll(): void {
    this.channels.forEach((ch) => ch.close())
    this.channels.clear()
  }
}

export const wsManager = new WsManager()
