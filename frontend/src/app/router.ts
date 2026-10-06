import {
  createRouter,
  createWebHistory,
  type RouteLocationNormalized,
  type RouteRecordRaw,
} from 'vue-router'

import { activitiesRoutes } from '@slices/activities'
import { adminRoutes } from '@slices/admin'
import { agentGroupsRoutes } from '@slices/agent-groups'
import { agentsRoutes } from '@slices/agents'
import { conversationRoutes } from '@slices/conversation'
import { dashboardRoutes } from '@slices/dashboard'
import { identityRoutes, useSessionStore } from '@slices/identity'
import { keysRoutes } from '@slices/keys'
import { notificationsRoutes } from '@slices/notifications'
import { promptStudioRoutes } from '@slices/prompt-studio'
import { skillsRoutes } from '@slices/skills'
import { tenancyRoutes } from '@slices/tenancy'
import { workflowRoutes } from '@slices/workflow'
import { onUnauthorizedRedirect, isGuestSession, canonicalRoomId, getGuestChatroomId } from '@shared/transport'

import { runGuards, type GuardContext, type GuardResult, type RouteMeta } from './guards'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'root',
    component: () => import('@app/views/Landing.vue'),
    meta: { layout: 'public' },
  },
  ...identityRoutes,
  ...tenancyRoutes,
  ...keysRoutes,
  ...agentsRoutes,
  ...agentGroupsRoutes,
  ...conversationRoutes,
  ...workflowRoutes,
  ...adminRoutes,
  ...notificationsRoutes,
  ...promptStudioRoutes,
  ...skillsRoutes,
  ...activitiesRoutes,
  ...dashboardRoutes,
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@app/views/NotFound.vue'),
    // Not requiresAuth: that would redirect a mistyped URL to /login. 'auto'
    // lets App.vue pick the layout from the session, per 02-layout-shell.md:351.
    meta: { layout: 'auto' },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

// A guest whose session ended, or whose boot restore found no network, holds a
// context but no token. It still reaches its own room, where the banner or the
// reconnecting state says what happened; any other room stays closed to it.
function holdsGuestSessionFor(to: RouteLocationNormalized): boolean {
  if (isGuestSession.value) return true
  const guestRoom = getGuestChatroomId()
  const target = to.params.chatroomId
  return guestRoom !== null && typeof target === 'string' && canonicalRoomId(target) === guestRoom
}

export function guardRoute(to: RouteLocationNormalized): GuardResult {
  const session = useSessionStore()
  const isAdmin = session.me?.is_admin ?? false
  const roles: string[] = []
  if (isAdmin) roles.push('admin')
  const ctx: GuardContext = {
    isAuthenticated: session.isAuthenticated,
    isVerified: session.isVerified,
    isAdmin,
    roles,
    hasGuestSession: holdsGuestSessionFor(to),
  }
  const metaRequiresAuth = to.meta.requiresAuth as boolean | undefined
  const metaRequiresVerifiedEmail = to.meta.requiresVerifiedEmail as boolean | undefined
  const metaRequiredRoles = to.meta.requiredRoles as string[] | undefined
  const metaAllowGuestSession = to.meta.allowGuestSession as boolean | undefined
  const meta: RouteMeta = {
    ...(metaRequiresAuth !== undefined && { requiresAuth: metaRequiresAuth }),
    ...(metaRequiresVerifiedEmail !== undefined && { requiresVerifiedEmail: metaRequiresVerifiedEmail }),
    ...(metaRequiredRoles !== undefined && { requiredRoles: metaRequiredRoles }),
    ...(metaAllowGuestSession !== undefined && { allowGuestSession: metaAllowGuestSession }),
  }
  return runGuards(meta, ctx, to.fullPath)
}

router.beforeEach((to: RouteLocationNormalized) => guardRoute(to))

onUnauthorizedRedirect(() => {
  // A guest session's end, if it ended, is already recorded by the refresh
  // that failed; the room renders it. A refresh that failed for want of a
  // network ended nothing (Q-6).
  if (getGuestChatroomId()) return
  const session = useSessionStore()
  session.clear()
  if (router.currentRoute.value.meta.requiresAuth) {
    router.push({ name: 'identity.login' })
  }
})
