import type { ApiClient } from './lib/http.ts'
import type { Activation, Proposal, RoomMessage, RoomSource } from './observe/room.ts'
import type { AuditSource, TranscriptSource } from './evidence/collect.ts'

/** The facilitator's view of one room: every SMAP route the run reads or acts on directly. */
export interface RoomApi extends RoomSource, TranscriptSource {
  releaseLatestObservation(): Promise<void>
}

export function roomApi(api: ApiClient, roomId: string): RoomApi {
  const base = `/api/chatrooms/${roomId}`
  return {
    latestMessages: (limit) => api.get<RoomMessage[]>(`${base}/messages?limit=${limit}`),
    messagesPage: (before) => api.get<RoomMessage[]>(`${base}/messages?limit=200${before ? `&before=${before}` : ''}`),
    members: () => api.get(`${base}/members`),
    agents: () => api.get(`${base}/agents?limit=500`),
    activeActivation: () => api.get<Activation | null>(`${base}/activity-activations/active`),
    proposals: async (activationId) => (await api.get<{ items: Proposal[] }>(`${base}/activity-proposals?activation_id=${activationId}`)).items,
    proposal: (id) => api.get<Proposal>(`${base}/activity-proposals/${id}`),
    releaseLatestObservation: async () => {
      const obs = await api.get<Array<{ id: string; released_at: string | null }>>(`${base}/observations?limit=100`)
      const latest = [...obs].reverse().find((o) => !o.released_at)
      if (!latest) throw new Error('no unreleased observation to release')
      await api.post(`${base}/observations/${latest.id}/release`, { target: 'room' })
    },
  }
}

/** The admin's audit log, filtered by exact action and resource. */
export function auditApi(admin: ApiClient): AuditSource {
  return {
    auditPage: ({ action, resourceId, from, to, cursor }) => {
      const q = new URLSearchParams({ action, resource_type: 'agent', resource_id: resourceId, from, to, limit: '200' })
      if (cursor !== null) q.set('cursor', String(cursor))
      return admin.get(`/api/admin/audit?${q.toString()}`)
    },
  }
}
