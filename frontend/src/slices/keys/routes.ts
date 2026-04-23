import type { RouteRecordRaw } from 'vue-router'

// All keys routes require a verified, authenticated principal — every handler
// on the backend side sits behind `current_principal` + capability gates.
const meta = { requiresAuth: true, requiresVerifiedEmail: true }

export const keysRoutes: RouteRecordRaw[] = [
  {
    path: '/keys',
    name: 'keys.list',
    component: () => import('./views/KeyListView.vue'),
    meta,
  },
  {
    path: '/keys/:id',
    name: 'keys.detail',
    component: () => import('./views/KeyDetailView.vue'),
    meta,
  },
  {
    path: '/projects/:projectId/keys',
    name: 'keys.projectKeys',
    component: () => import('./views/ProjectKeysView.vue'),
    meta,
  },
  {
    path: '/projects/:projectId/key-groups',
    name: 'keys.groupList',
    component: () => import('./views/KeyGroupListView.vue'),
    meta,
  },
  {
    path: '/projects/:projectId/key-groups/:id',
    name: 'keys.groupDetail',
    component: () => import('./views/KeyGroupDetailView.vue'),
    meta,
  },
  {
    path: '/projects/:projectId/search-keys',
    name: 'keys.searchKeys',
    component: () => import('./views/SearchKeyView.vue'),
    meta,
  },
]
