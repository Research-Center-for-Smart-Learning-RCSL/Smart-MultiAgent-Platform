import { http } from '@shared/transport'

export type ProjectOwnerType = 'user' | 'org'

export interface Project {
  id: string
  name: string
  owner_type: ProjectOwnerType
  owner_id: string
  version: number
  created_at: string
}

export interface ProjectMember {
  user_id: string
  email: string
  role: 'owner' | 'member'
  joined_at: string
}

export const projectsApi = {
  list: (scope: ProjectOwnerType, id: string) =>
    http.get<Project[]>(`/projects`, { params: { scope, id } }),
  create: (owner_type: ProjectOwnerType, owner_id: string, name: string) =>
    http.post<Project>(`/projects`, { owner_type, owner_id, name }),
  get: (id: string) => http.get<Project>(`/projects/${id}`),
  remove: (id: string) => http.delete(`/projects/${id}`),
  listMembers: (id: string) => http.get<ProjectMember[]>(`/projects/${id}/members`),
  removeMember: (id: string, uid: string) =>
    http.delete(`/projects/${id}/members/${uid}`),
  invite: (id: string, email: string, role: 'owner' | 'member') =>
    http.post(`/projects/${id}/invites`, { invitee_email: email, role }),
}
