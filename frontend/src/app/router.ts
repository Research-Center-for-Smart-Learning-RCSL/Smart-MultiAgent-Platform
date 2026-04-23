import {
  createRouter,
  createWebHistory,
  type RouteLocationNormalized,
  type RouteRecordRaw,
} from 'vue-router'

import { adminRoutes } from '@slices/admin'
import { conversationRoutes } from '@slices/conversation'
import { identityRoutes, useSessionStore } from '@slices/identity'
import { keysRoutes } from '@slices/keys'
import { tenancyRoutes } from '@slices/tenancy'
import { workflowRoutes } from '@slices/workflow'
import { onUnauthorizedRedirect } from '@shared/transport'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'root',
    component: () => import('@app/views/Landing.vue'),
  },
  ...identityRoutes,
  ...tenancyRoutes,
  ...keysRoutes,
  ...conversationRoutes,
  ...workflowRoutes,
  ...adminRoutes,
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to: RouteLocationNormalized) => {
  const session = useSessionStore()
  if (to.meta.requiresAuth && !session.isAuthenticated) {
    return {
      name: 'identity.login',
      query: { redirect: to.fullPath },
    }
  }
  if (to.meta.requiresVerifiedEmail && !session.isVerified) {
    return { name: 'identity.verifyEmail' }
  }
  const requiredRoles = to.meta.requiredRoles as string[] | undefined
  if (requiredRoles?.includes('admin') && !session.me?.is_admin) {
    return { name: 'root' }
  }
  return true
})

onUnauthorizedRedirect(() => {
  const session = useSessionStore()
  session.clear()
  if (router.currentRoute.value.meta.requiresAuth) {
    router.push({ name: 'identity.login' })
  }
})
