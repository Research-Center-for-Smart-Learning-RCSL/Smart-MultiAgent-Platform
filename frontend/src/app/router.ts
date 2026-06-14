import {
  createRouter,
  createWebHistory,
  type RouteLocationNormalized,
  type RouteRecordRaw,
} from 'vue-router'

import { adminRoutes } from '@slices/admin'
import { agentsRoutes } from '@slices/agents'
import { conversationRoutes } from '@slices/conversation'
import { identityRoutes, useSessionStore } from '@slices/identity'
import { keysRoutes } from '@slices/keys'
import { notificationsRoutes } from '@slices/notifications'
import { tenancyRoutes } from '@slices/tenancy'
import { workflowRoutes } from '@slices/workflow'
import { onUnauthorizedRedirect } from '@shared/transport'

import { runGuards, type GuardContext, type RouteMeta } from './guards'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'root',
    component: () => import('@app/views/Landing.vue'),
  },
  ...identityRoutes,
  ...tenancyRoutes,
  ...keysRoutes,
  ...agentsRoutes,
  ...conversationRoutes,
  ...workflowRoutes,
  ...adminRoutes,
  ...notificationsRoutes,
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
  const ctx: GuardContext = {
    isAuthenticated: session.isAuthenticated,
    isVerified: session.isVerified,
    isAdmin: session.me?.is_admin ?? false,
  }
  const meta: RouteMeta = {
    requiresAuth: to.meta.requiresAuth as boolean | undefined,
    requiresVerifiedEmail: to.meta.requiresVerifiedEmail as boolean | undefined,
    requiredRoles: to.meta.requiredRoles as string[] | undefined,
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
