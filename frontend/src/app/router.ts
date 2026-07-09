import {
  createRouter,
  createWebHistory,
  type RouteLocationNormalized,
  type RouteRecordRaw,
} from 'vue-router'

import { adminRoutes } from '@slices/admin'
import { agentGroupsRoutes } from '@slices/agent-groups'
import { agentsRoutes } from '@slices/agents'
import { conversationRoutes } from '@slices/conversation'
import { identityRoutes, useSessionStore } from '@slices/identity'
import { keysRoutes } from '@slices/keys'
import { notificationsRoutes } from '@slices/notifications'
import { promptStudioRoutes } from '@slices/prompt-studio'
import { tenancyRoutes } from '@slices/tenancy'
import { workflowRoutes } from '@slices/workflow'
import { onUnauthorizedRedirect } from '@shared/transport'

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
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@app/views/NotFound.vue'),
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
  }
  const metaRequiresAuth = to.meta.requiresAuth as boolean | undefined
  const metaRequiresVerifiedEmail = to.meta.requiresVerifiedEmail as boolean | undefined
  const metaRequiredRoles = to.meta.requiredRoles as string[] | undefined
  const meta: RouteMeta = {
    ...(metaRequiresAuth !== undefined && { requiresAuth: metaRequiresAuth }),
    ...(metaRequiresVerifiedEmail !== undefined && { requiresVerifiedEmail: metaRequiresVerifiedEmail }),
    ...(metaRequiredRoles !== undefined && { requiredRoles: metaRequiredRoles }),
  }
  return runGuards(meta, ctx, to.fullPath)
})

onUnauthorizedRedirect(() => {
  const session = useSessionStore()
  session.clear()
  if (router.currentRoute.value.meta.requiresAuth) {
    router.push({ name: 'identity.login' })
  }
})
