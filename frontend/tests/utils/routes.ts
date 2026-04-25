import type { RouteRecordRaw } from 'vue-router'
import { adminRoutes } from '@slices/admin'
import { agentsRoutes } from '@slices/agents'
import { conversationRoutes } from '@slices/conversation'
import { identityRoutes } from '@slices/identity'
import { keysRoutes } from '@slices/keys'
import { tenancyRoutes } from '@slices/tenancy'
import { workflowRoutes } from '@slices/workflow'

export const appRoutes: RouteRecordRaw[] = [
  { path: '/', name: 'root', component: { template: '<div />' } },
  ...identityRoutes,
  ...tenancyRoutes,
  ...keysRoutes,
  ...agentsRoutes,
  ...conversationRoutes,
  ...workflowRoutes,
  ...adminRoutes,
  { path: '/:pathMatch(.*)*', name: 'not-found', component: { template: '<div />' } },
]
