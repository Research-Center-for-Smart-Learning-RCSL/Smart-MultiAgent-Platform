// The per-room hint this browser keeps after entering a room as an anonymous
// guest. It holds no token ([R24.43]): only the browser id that lets the server
// resume the same session, and the name for the welcome-back screen. It is the
// one durable sign that this browser was a guest in a room, which is why boot
// restore keys on it (Q-1).

import { canonicalRoomId, guestSessionEnd, resumeGuestSession } from '@shared/transport'
import { GUEST_STORAGE_PREFIX } from '../stores/guestSession'

export interface GuestHint {
  browser_id: string
  guest_session_id: string
  display_name: string
}

// Hints written before ids were canonicalised are keyed by the id as the link
// spelled it, so a read falls back to that spelling.
function keysFor(chatroomId: string): string[] {
  const canonical = `${GUEST_STORAGE_PREFIX}${canonicalRoomId(chatroomId)}`
  const asGiven = `${GUEST_STORAGE_PREFIX}${chatroomId}`
  return canonical === asGiven ? [canonical] : [canonical, asGiven]
}

export function readGuestHint(chatroomId: string): GuestHint | null {
  for (const key of keysFor(chatroomId)) {
    try {
      const raw = localStorage.getItem(key)
      if (!raw) continue
      const parsed = JSON.parse(raw) as Partial<GuestHint>
      if (parsed.browser_id && parsed.display_name) return parsed as GuestHint
    } catch {
      // localStorage unavailable or the entry is malformed: no hint
    }
  }
  return null
}

export function writeGuestHint(chatroomId: string, hint: GuestHint): void {
  try {
    localStorage.setItem(`${GUEST_STORAGE_PREFIX}${canonicalRoomId(chatroomId)}`, JSON.stringify(hint))
  } catch {
    // localStorage unavailable -- non-fatal
  }
}

export function removeGuestHint(chatroomId: string): void {
  for (const key of keysFor(chatroomId)) {
    try {
      localStorage.removeItem(key)
    } catch {
      // localStorage unavailable -- non-fatal
    }
  }
}

const ROOM_PATH = /^\/(?:chatrooms|c)\/([^/]+)\/?$/

/**
 * Boot-time restore of the guest session for the room in `pathname`, when this
 * browser holds that room's hint. Call only once the account refresh has
 * failed: the account always wins on a shared device (Q-1).
 */
export async function restoreGuestSession(pathname: string): Promise<void> {
  const match = ROOM_PATH.exec(pathname)
  if (!match?.[1]) return
  let roomId: string
  try {
    roomId = decodeURIComponent(match[1])
  } catch {
    return
  }
  if (!readGuestHint(roomId)) return
  const outcome = await resumeGuestSession(roomId)
  // Shown once on the room's banner; a later reload falls through to the
  // ordinary sign-in path instead of re-presenting a dead cookie.
  if (outcome === 'ended' && (guestSessionEnd.value === 'expired' || guestSessionEnd.value === 'gone')) {
    removeGuestHint(roomId)
  }
}
