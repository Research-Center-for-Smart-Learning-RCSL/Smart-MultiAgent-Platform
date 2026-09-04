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
import { identityRoutes, useSessionStore } from '@slices/identity'
import { keysRoutes } from '@slices/keys'
import { notificationsRoutes } from '@slices/notifications'
import { promptStudioRoutes } from '@slices/prompt-studio'
import { skillsRoutes } from '@slices/skills'
import { tenancyRoutes } from '@slices/tenancy'
import { workflowRoutes } from '@slices/workflow'
import { onUnauthorizedRedirect, isGuestSession, clearGuestContext, getGuestChatroomId } from '@shared/transport'
import { useGuestSessionStore } from '@slices/conversation'

import { runGuards, type GuardContext, type RouteMeta } from './guards'

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

router.beforeEach((to: RouteLocationNormalized) => {
  const session = useSessionStore()
  const isAdmin = session.me?.is_admin ?? false
  const roles: string[] = []
  if (isAdmin) roles.push('admin')
  const ctx: GuardContext = {
    isAuthenticated: session.isAuthenticated,
    isVerified: session.isVerified,
    isAdmin,
    roles,
    hasGuestSession: isGuestSession.value,
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
})

onUnauthorizedRedirect(() => {
  // attemptRefresh clears the access token before this fires, so
  // isGuestSession is already false. Check the guest context ref instead.
  if (getGuestChatroomId()) {
    const guestStore = useGuestSessionStore()
    guestStore.markExpired()
    const rejoinUrl = guestStore.rejoinUrl
    clearGuestContext()
    if (rejoinUrl) {
      router.push(rejoinUrl)
    }
    return
  }
  const session = useSessionStore()
  session.clear()
  if (router.currentRoute.value.meta.requiresAuth) {
    router.push({ name: 'identity.login' })
  }
})
