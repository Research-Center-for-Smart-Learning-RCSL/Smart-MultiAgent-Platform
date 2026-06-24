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
    meta: { requiresAuth: true },
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
    meta: { requiresAuth: true, layout: 'auth' },
  },
  {
    path: '/c/:chatroomId',
    redirect: (to) => ({
      name: 'conversation.chatroom',
      params: { chatroomId: to.params.chatroomId },
    }),
  },
]
