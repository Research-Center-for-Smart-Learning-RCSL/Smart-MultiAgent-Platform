// Inactivity auto-logout (R6.03-adjacent). A singleton, app-wide guard that
// watches real user input and, after the configured idle window, signs the user
// out. It is the client half of a two-layer defence: the backend enforces the
// same window on refresh (a stolen cookie cannot be rotated forever without
// activity), while this composable gives the user a visible warning + countdown
// before the session actually ends.
//
// Lives in shared/ — like useBanKickGuard — so any consumer reaches it through
// @shared without depending on the identity slice directly.

import { ref, watch, type Ref } from 'vue'
import { useRouter, type Router } from 'vue-router'
import { http, refreshAccessToken } from '@shared/transport'
import { useSessionStore } from '@shared/stores/session'
import { i18n } from '@shared/i18n'
import { useToast } from './useToast'

interface SessionPolicy {
  idle_timeout_seconds: number
  idle_warning_seconds: number
}

// Defaults mirror the backend's JwtSection so the guard still works if the
// policy fetch fails. Kept in lockstep with backend/app/config/settings.py.
const DEFAULT_POLICY: SessionPolicy = {
  idle_timeout_seconds: 45 * 60,
  idle_warning_seconds: 60,
}

// User gestures that count as "still here". scroll/wheel/touch cover reading and
// mobile; mousemove/keydown cover the desktop common case. All passive.
const ACTIVITY_EVENTS = [
  'mousedown',
  'mousemove',
  'keydown',
  'wheel',
  'scroll',
  'touchstart',
] as const

// --- Module-scoped singleton state -----------------------------------------
const warningActive = ref(false)
const remainingSeconds = ref(0)

let installed = false
let started = false
let policy: SessionPolicy = DEFAULT_POLICY
let policyLoaded = false

let lastActivity = 0
let activityThrottleUntil = 0
let tickHandle: number | null = null
let loggingOut = false

// Bound once at install time inside a component setup (SIdleDialog), where the
// router/toast/store composables are valid to call.
let router: Router | null = null
let toast: ReturnType<typeof useToast> | null = null
let session: ReturnType<typeof useSessionStore> | null = null

async function loadPolicy(): Promise<void> {
  if (policyLoaded) return
  try {
    const { data } = await http.get<SessionPolicy>('/auth/session-policy')
    if (
      data &&
      typeof data.idle_timeout_seconds === 'number' &&
      typeof data.idle_warning_seconds === 'number' &&
      data.idle_timeout_seconds > 0
    ) {
      policy = data
    }
  } catch {
    // Keep defaults — a missing policy must not leave the session un-guarded.
  } finally {
    policyLoaded = true
  }
}

function markActivity(): void {
  // While the warning is up, only an explicit "Stay signed in" click keeps the
  // session alive — passive movement must not silently cancel the countdown.
  if (warningActive.value) return
  const t = Date.now()
  if (t < activityThrottleUntil) return
  activityThrottleUntil = t + 1000
  lastActivity = t
}

function tick(): void {
  if (!session?.isAuthenticated) return
  const t = Date.now()
  const timeoutMs = policy.idle_timeout_seconds * 1000
  const warnLeadMs = policy.idle_warning_seconds * 1000

  if (!warningActive.value) {
    if (t - lastActivity >= Math.max(0, timeoutMs - warnLeadMs)) {
      warningActive.value = true
      remainingSeconds.value = policy.idle_warning_seconds
    }
    return
  }

  // Countdown is derived from timestamps, not decremented, so a backgrounded
  // tab (whose timers are throttled) still lands on the correct deadline when
  // it wakes — even if that means logging out immediately on return.
  const remainingMs = lastActivity + timeoutMs - t
  remainingSeconds.value = Math.max(0, Math.ceil(remainingMs / 1000))
  if (remainingMs <= 0) {
    void timeoutLogout()
  }
}

function onVisibility(): void {
  // Re-evaluate the moment the tab regains focus to catch a deadline that
  // elapsed while it was hidden.
  if (document.visibilityState === 'visible') tick()
}

function start(): void {
  if (started) return
  started = true
  lastActivity = Date.now()
  warningActive.value = false
  for (const ev of ACTIVITY_EVENTS) {
    window.addEventListener(ev, markActivity, { passive: true })
  }
  document.addEventListener('visibilitychange', onVisibility)
  tickHandle = window.setInterval(tick, 1000)
  void loadPolicy()
}

function stop(): void {
  if (!started) return
  started = false
  for (const ev of ACTIVITY_EVENTS) {
    window.removeEventListener(ev, markActivity)
  }
  document.removeEventListener('visibilitychange', onVisibility)
  if (tickHandle !== null) {
    clearInterval(tickHandle)
    tickHandle = null
  }
  warningActive.value = false
}

async function stayActive(): Promise<void> {
  warningActive.value = false
  lastActivity = Date.now()
  activityThrottleUntil = 0
  // Prove activity to the server so its sliding idle window resets too. If the
  // refresh fails the session is already gone server-side — fall through to a
  // clean logout rather than leaving a dead session on screen.
  const token = await refreshAccessToken()
  if (token === null) await timeoutLogout()
}

async function timeoutLogout(): Promise<void> {
  if (loggingOut) return
  loggingOut = true
  warningActive.value = false
  try {
    await session?.logout()
  } finally {
    toast?.info(i18n.global.t('app.idle.signedOut'))
    if (router) await router.push({ name: 'identity.login' }).catch(() => {})
    loggingOut = false
  }
}

export interface IdleLogout {
  warningActive: Ref<boolean>
  remainingSeconds: Ref<number>
  stayActive: () => Promise<void>
  logoutNow: () => Promise<void>
}

/**
 * Activate (once) the inactivity guard and expose its warning state. Must be
 * called from a component setup mounted inside the router — `SIdleDialog` is the
 * single caller. Repeated calls return the same singleton handle.
 */
export function useIdleLogout(): IdleLogout {
  if (!installed) {
    installed = true
    router = useRouter()
    toast = useToast()
    session = useSessionStore()
    watch(
      () => session?.isAuthenticated ?? false,
      (authed) => (authed ? start() : stop()),
      { immediate: true },
    )
  }
  return { warningActive, remainingSeconds, stayActive, logoutNow: timeoutLogout }
}
