// WsManager singleton — central WebSocket management (R24.14).
//
// Usage:  const ch = wsManager.channel('/chatroom/abc')
//         ch.subscribe('message.created', handler)
//         ch.close()
//
// Handles reconnect with exponential backoff, ticket-based handshake auth,
// and in-socket token refresh before JWT expiry.
//
// Auth (FE-7): the handshake never carries the JWT. `Sec-WebSocket-Protocol`
// is recorded by proxies and access logs, so instead the channel fetches a
// short-lived, single-use ticket over HTTPS and offers `ticket.<id>` as the
// subprotocol. The in-socket `refresh` frame *does* carry the JWT, but a frame
// body is not logged the way a handshake header is — and it is refreshed over
// HTTPS first so a long-backgrounded tab never sends an expired token.

import {
  decodeJwtClaims,
  fetchWsTicket,
  getAccessToken,
  refreshAccessToken,
} from './axios'

export interface ChannelEvent {
  type: string
  [k: string]: unknown
}

type EventHandler = (event: ChannelEvent) => void
type StatusHandler = (connected: boolean) => void
type DegradedHandler = (degraded: boolean) => void
type CapReachedHandler = (capReached: boolean) => void

const INITIAL_BACKOFF_MS = 1_000
const MAX_BACKOFF_MS = 30_000
// F-1: the server reaps a socket that sends nothing for 120s
// (`connection.py:_IDLE_TIMEOUT_SECONDS`) — it treats silence as a half-open
// connection. Four intervals fit inside that window, so three consecutive lost
// or delayed pings are survivable; a hidden tab throttled to ~60s still clears
// it. Any change here must also stay under the presence conns TTL of 150s
// (`presence.py:_CONN_TTL_SECONDS`), which is sized against this heartbeat.
const HEARTBEAT_INTERVAL_MS = 30_000
// F-4: a socket rejected after `accept` (per-user cap, slow consumer, revoked
// auth) completes the HTTP upgrade first, so `onopen` fires for a connection
// the server never intended to keep. Treating that as success reset the backoff
// and the failure counter on every rejection, producing an unbounded 1Hz retry
// that never reached the degraded state. A connection counts as successful only
// once it has survived this window — far longer than a rejection's
// accept-then-close round trip, far shorter than MAX_BACKOFF_MS.
const STABLE_CONNECTION_MS = 5_000
// App-level close code for the per-user connection cap (R19.03,
// `connection.py:_CLOSE_CAP_REACHED`). Distinct from 1013, which the idle
// reaper and the slow-consumer path also use, so the client can tell a
// self-inflicted, self-curable condition from a network problem.
const CAP_CLOSE_CODE = 4429
// After this many consecutive failed connect attempts the channel is declared
// "degraded" (§12 Shared Patterns §7.1) so consumers can fall back to REST
// polling. The socket keeps retrying underneath; degraded is purely advisory.
const DEGRADED_THRESHOLD = 3
// Refresh the in-socket token this long before the access JWT's `exp`.
const REFRESH_MARGIN_MS = 60_000
// Floor for the refresh timer — a token already inside (or past) the margin
// still schedules a near-immediate refresh rather than a zero/negative delay.
const MIN_REFRESH_DELAY_MS = 5_000
// Used when the token carries no decodable `exp` — fall back to a fixed cadence.
const FALLBACK_REFRESH_MS = 60_000

/** Expiry of the current access token in epoch-ms, or `null` if undecodable. */
function accessTokenExpiryMs(): number | null {
  const token = getAccessToken()
  if (!token) return null
  const exp = decodeJwtClaims(token)?.exp
  return typeof exp === 'number' ? exp * 1000 : null
}

export class Channel {
  private socket: WebSocket | null = null
  private handlers = new Map<string, Set<EventHandler>>()
  private statusHandlers = new Set<StatusHandler>()
  private degradedHandlers = new Set<DegradedHandler>()
  private capReachedHandlers = new Set<CapReachedHandler>()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private refreshTimer: ReturnType<typeof setTimeout> | null = null
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null
  private stableTimer: ReturnType<typeof setTimeout> | null = null
  private backoff = INITIAL_BACKOFF_MS
  private consecutiveFailures = 0
  private degraded = false
  private capReached = false
  private closed = false
  private paused = false
  private connecting = false

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

  // Advisory "the socket has failed to reconnect repeatedly" signal (§7.1).
  // Pushes the current value on subscribe so a late subscriber is not stuck
  // until the next transition.
  onDegraded(handler: DegradedHandler): () => void {
    this.degradedHandlers.add(handler)
    handler(this.degraded)
    return () => this.degradedHandlers.delete(handler)
  }

  // The last close was the server refusing this user's excess connection
  // (R19.03). Unlike `degraded` this names a condition the user can cure —
  // close a tab — so consumers surface it distinctly. Pushes the current value
  // on subscribe, for the same reason `onDegraded` does.
  onCapReached(handler: CapReachedHandler): () => void {
    this.capReachedHandlers.add(handler)
    handler(this.capReached)
    return () => this.capReachedHandlers.delete(handler)
  }

  send(payload: Record<string, unknown>): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload))
    }
  }

  // Sync entry point — callers (onMounted/onActivated hooks) need not await.
  // The handshake itself is async (it fetches a ticket over HTTP), so the
  // real work runs in `openSocket`.
  connect(): void {
    this.paused = false
    if (this.closed) return
    if (
      this.socket &&
      (this.socket.readyState === WebSocket.CONNECTING ||
        this.socket.readyState === WebSocket.OPEN)
    )
      return
    // The ticket fetch awaits an HTTP round-trip, so the socket-state guard
    // above cannot catch a second call arriving during that await — this
    // re-entrancy flag does.
    if (this.connecting) return
    this.connecting = true
    void this.openSocket()
  }

  private async openSocket(): Promise<void> {
    try {
      // Authenticate with a short-lived, single-use ticket — never the raw
      // JWT (FE-7). The ticket fetch goes over HTTPS and silently refreshes an
      // expired access token, so a long-backgrounded tab reconnects cleanly.
      const ticket = await fetchWsTicket()
      // A close()/disconnect() may have landed while the ticket was in flight.
      if (this.closed || this.paused) return

      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const url = `${proto}://${window.location.host}/ws${this.path}`
      const socket = new WebSocket(url, [`ticket.${ticket}`])
      this.socket = socket

      socket.onopen = () => {
        this.emitStatus(true)
        this.scheduleTokenRefresh()
        this.startHeartbeat()
        // Recovery state is deliberately NOT reset here — see
        // STABLE_CONNECTION_MS. `emitStatus(true)` still fires immediately, so
        // the pill reads live at once; only the backoff/failure bookkeeping
        // waits for proof that the server kept the connection.
        this.armStableTimer()
      }

      socket.onmessage = (msg) => {
        try {
          const event = JSON.parse(msg.data as string) as ChannelEvent
          this.dispatch(event)
        } catch {
          // skip malformed frames
        }
      }

      socket.onclose = (ev) => {
        this.emitStatus(false)
        this.clearRefreshTimer()
        this.clearHeartbeatTimer()
        // Before scheduleReconnect, so a socket that died inside the stability
        // window is counted as one more consecutive failure.
        this.clearStableTimer()
        if (ev.code === CAP_CLOSE_CODE) this.setCapReached(true)
        if (!this.closed && !this.paused) this.scheduleReconnect()
      }

      socket.onerror = () => {
        // onclose fires immediately after
      }
    } catch {
      // No ticket (offline, or the access token could not be refreshed) or the
      // socket constructor threw — back off and retry.
      this.scheduleReconnect()
    } finally {
      this.connecting = false
    }
  }

  // Soft-disconnect: closes the socket but keeps handlers and does not mark
  // the channel closed, so connect() can be called again (used by KeepAlive).
  disconnect(): void {
    this.paused = true
    // A deliberate pause (KeepAlive deactivation) is not a connection failure —
    // reset the failure run so reactivation starts clean and not mid-degrade.
    this.consecutiveFailures = 0
    this.setDegraded(false)
    // A deactivated channel releases its socket, so whatever cap pressure it
    // was reporting no longer applies.
    this.setCapReached(false)
    this.clearReconnectTimer()
    this.clearRefreshTimer()
    this.clearHeartbeatTimer()
    this.clearStableTimer()
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) {
      this.socket.close(1000, 'channel deactivated')
    }
    this.socket = null
  }

  close(): void {
    this.closed = true
    this.clearReconnectTimer()
    this.clearRefreshTimer()
    this.clearHeartbeatTimer()
    this.clearStableTimer()
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) {
      this.socket.close(1000, 'channel closed')
    }
    this.socket = null
    this.handlers.clear()
    this.statusHandlers.clear()
    this.degradedHandlers.clear()
    this.capReachedHandlers.clear()
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

  private setDegraded(value: boolean): void {
    if (this.degraded === value) return
    this.degraded = value
    this.degradedHandlers.forEach((h) => h(value))
  }

  private setCapReached(value: boolean): void {
    if (this.capReached === value) return
    this.capReached = value
    this.capReachedHandlers.forEach((h) => h(value))
  }

  // F-1: the server's idle reaper is written around a client that sends
  // periodic frames; `send` already no-ops unless the socket is OPEN.
  private startHeartbeat(): void {
    this.clearHeartbeatTimer()
    this.heartbeatTimer = setInterval(() => {
      this.send({ type: 'ping' })
    }, HEARTBEAT_INTERVAL_MS)
  }

  private clearHeartbeatTimer(): void {
    if (this.heartbeatTimer !== null) {
      clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }

  // F-4: the recovery bookkeeping that used to run at `onopen`. It runs here
  // instead, so only a connection the server actually kept clears the backoff,
  // the failure run, and the degraded/cap signals.
  private armStableTimer(): void {
    this.clearStableTimer()
    this.stableTimer = setTimeout(() => {
      this.stableTimer = null
      this.backoff = INITIAL_BACKOFF_MS
      this.consecutiveFailures = 0
      this.setDegraded(false)
      this.setCapReached(false)
    }, STABLE_CONNECTION_MS)
  }

  private clearStableTimer(): void {
    if (this.stableTimer !== null) {
      clearTimeout(this.stableTimer)
      this.stableTimer = null
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null || this.closed) return
    // Each scheduled retry without an intervening successful open is one more
    // consecutive failure; crossing the threshold flips the channel degraded.
    this.consecutiveFailures += 1
    if (this.consecutiveFailures >= DEGRADED_THRESHOLD) {
      this.setDegraded(true)
    }
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

  // Schedule the next in-socket refresh from the token's real `exp` — not a
  // fixed interval — so it fires once per token lifetime, just before expiry
  // (FE-7). An undecodable token falls back to a fixed cadence.
  private scheduleTokenRefresh(): void {
    this.clearRefreshTimer()
    if (this.closed || this.paused) return
    const expiryMs = accessTokenExpiryMs()
    const delay =
      expiryMs !== null
        ? Math.max(
            MIN_REFRESH_DELAY_MS,
            expiryMs - Date.now() - REFRESH_MARGIN_MS,
          )
        : FALLBACK_REFRESH_MS
    this.refreshTimer = setTimeout(() => {
      void this.runTokenRefresh()
    }, delay)
  }

  private async runTokenRefresh(): Promise<void> {
    this.refreshTimer = null
    if (this.closed || this.paused) return

    let token = getAccessToken()
    const expiryMs = accessTokenExpiryMs()
    // Only pay for an HTTP refresh when the token is genuinely near expiry —
    // a sibling channel sharing this access token may have refreshed it
    // already, in which case we just resend the fresh one.
    const needsRefresh =
      expiryMs === null || expiryMs - Date.now() <= REFRESH_MARGIN_MS
    if (needsRefresh) {
      try {
        token = (await refreshAccessToken()) ?? token
      } catch {
        // Refresh failed — resend whatever token we still hold; the server
        // closes the socket (4401) if it has truly expired and we reconnect.
      }
    }

    if (this.closed || this.paused) return
    // Socket dropped while we were refreshing — `onclose` already queued a
    // reconnect, and the fresh socket's `onopen` re-arms this loop. Returning
    // here avoids a stray no-op refresh timer running alongside the reconnect.
    const socket = this.socket
    if (!socket || socket.readyState !== WebSocket.OPEN) return
    if (token) {
      try {
        socket.send(JSON.stringify({ type: 'refresh', access_token: token }))
      } catch {
        // Socket became unwritable; let reconnect re-authenticate.
        this.scheduleReconnect()
        return
      }
    }
    this.scheduleTokenRefresh()
  }

  private clearRefreshTimer(): void {
    if (this.refreshTimer !== null) {
      clearTimeout(this.refreshTimer)
      this.refreshTimer = null
    }
  }
}

const MAX_CHANNELS = 20

class WsManager {
  private channels = new Map<string, Channel>()

  channel(path: string): Channel {
    let ch = this.channels.get(path)
    if (!ch) {
      if (this.channels.size >= MAX_CHANNELS) {
        if (import.meta.env.DEV) console.warn(`[WsManager] ${this.channels.size} open channels — possible leak. Check that all channels are closed on unmount.`)
      }
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
