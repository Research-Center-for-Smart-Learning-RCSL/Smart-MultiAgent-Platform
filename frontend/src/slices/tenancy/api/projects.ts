import { http } from '@shared/transport'
import type { PaginationParams } from '@shared/transport'

export type ProjectOwnerType = 'user' | 'org'

export interface Project {
  id: string
  name: string
  owner_type: ProjectOwnerType
  owner_id: string
  owner_name?: string
  created_by_user_id?: string
  version: number
  created_at: string
  deleted_at?: string | null
}

export interface ProjectMember {
  user_id: string
  email: string
  role: 'owner' | 'member'
  is_inherited?: boolean
  joined_at: string
}

export const projectsApi = {
  list: (scope?: ProjectOwnerType, id?: string, params?: PaginationParams) =>
    http.get<Project[]>(`/projects`, { params: { ...params, ...(scope && id ? { scope, id } : {}) } }),
  create: (owner_type: ProjectOwnerType, owner_id: string, name: string) =>
    http.post<Project>(`/projects`, { owner_type, owner_id, name }),
  get: (id: string) => http.get<Project>(`/projects/${id}`),
  remove: (id: string) => http.delete(`/projects/${id}`),
  restore: (id: string) => http.post<Project>(`/projects/${id}/restore`),
  rename: (id: string, name: string, version: number) =>
    http.patch<Project>(`/projects/${id}`, { name }, { headers: { 'If-Match': String(version) } }),
  listMembers: (id: string, params?: PaginationParams) =>
    http.get<ProjectMember[]>(`/projects/${id}/members`, { params }),
  removeMember: (id: string, uid: string) =>
    http.delete(`/projects/${id}/members/${uid}`),
  setRole: (id: string, uid: string, role: 'owner' | 'member') =>
    http.patch(`/projects/${id}/members/${uid}`, { role }),
  invite: (id: string, email: string, role: 'owner' | 'member') =>
    http.post(`/projects/${id}/invites`, { email, role }),
}
