import type { RouteRecordRaw } from 'vue-router'

export const conversationRoutes: RouteRecordRaw[] = [
  {
    path: '/projects/:projectId/workspaces',
    name: 'conversation.workspaces',
    component: () => import('./views/WorkspaceListView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
  {
    path: '/workspaces/:workspaceId/chatrooms',
    name: 'conversation.chatrooms',
    component: () => import('./views/ChatroomListView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/chatrooms/:chatroomId',
    name: 'conversation.chatroom',
    component: () => import('./views/ChatroomView.vue'),
    meta: { requiresAuth: true, sidebarCollapsed: true, contentPadding: 'none' },
  },
  {
    path: '/chatrooms/:chatroomId/settings',
    name: 'conversation.chatroom.settings',
    component: () => import('./views/ChatroomSettingsView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/g/:chatroomId/:guestToken',
    name: 'conversation.guest',
    component: () => import('./views/GuestLandingView.vue'),
    // requiresAuth: false so the page loads for unauthenticated visitors.
    // TODO(backend): POST /guest/:chatroomId/:guestToken/enroll currently
    // requires JWT via current_principal. It should authenticate via the
    // guest token path parameter instead so truly unauthenticated guests
    // can complete enrollment without a prior login round-trip.
    meta: { requiresAuth: false, layout: 'auth' },
  },
  {
    path: '/c/:chatroomId',
    redirect: (to) => ({
      name: 'conversation.chatroom',
      params: { chatroomId: to.params.chatroomId },
    }),
  },
]
