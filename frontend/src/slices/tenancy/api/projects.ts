// Project + membership API (tenancy).
//
// Wraps the generated ProjectsService over the one instrumented axios singleton.
// ProjectOut/ProjectMemberOut are directly assignable to the hand-rolled types
// (they omit the optional `owner_name`/`is_inherited`, which stays assignable
// and passes through at runtime when the backend sends it), so no bridge is
// needed. `restore` resolves void (empty backend body; consumers only await it).

import { ProjectsService } from '@shared/api-client'
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
  // scope+id scopes the listing to one owner; the API requires them together,
  // so pass both or neither (never `scope` alone).
  list: (scope?: ProjectOwnerType, id?: string, params?: PaginationParams): Promise<Project[]> =>
    ProjectsService.listProjectsApiProjectsGet({
      ...(params ?? {}),
      ...(scope && id ? { scope, id } : {}),
    }),
  create: (owner_type: ProjectOwnerType, owner_id: string, name: string): Promise<Project> =>
    ProjectsService.createProjectApiProjectsPost({ requestBody: { owner_type, owner_id, name } }),
  get: (id: string): Promise<Project> =>
    ProjectsService.readProjectApiProjectsProjectIdGet({ projectId: id }),
  remove: (id: string): Promise<void> =>
    ProjectsService.deleteProjectApiProjectsProjectIdDelete({ projectId: id }),
  restore: (id: string): Promise<void> =>
    ProjectsService.restoreProjectApiProjectsProjectIdRestorePost({ projectId: id }),
  rename: (id: string, name: string, version: number): Promise<Project> =>
    ProjectsService.renameProjectApiProjectsProjectIdPatch({
      projectId: id,
      ifMatch: String(version),
      requestBody: { name },
    }),
  listMembers: (id: string, params?: PaginationParams): Promise<ProjectMember[]> =>
    ProjectsService.listMembersApiProjectsProjectIdMembersGet({
      projectId: id,
      ...(params ?? {}),
    }),
  removeMember: (id: string, uid: string) =>
    ProjectsService.removeProjectMemberApiProjectsProjectIdMembersUserIdDelete({
      projectId: id,
      userId: uid,
    }),
  setRole: (id: string, uid: string, role: 'owner' | 'member') =>
    ProjectsService.patchProjectMemberApiProjectsProjectIdMembersUserIdPatch({
      projectId: id,
      userId: uid,
      requestBody: { role },
    }),
  invite: (id: string, email: string, role: 'owner' | 'member') =>
    ProjectsService.createProjectInviteApiProjectsProjectIdInvitesPost({
      projectId: id,
      requestBody: { email, role },
    }),
}
