import { ClassroomDesignService } from '@shared/api-client'
import type { DesignApplyOut, DesignRunIn, DesignRunOut } from '@shared/api-client'

export const classroomDesignApi = {
  /** Newest first; the panel follows the first one. */
  latest: async (projectId: string): Promise<DesignRunOut | null> => {
    const runs = await ClassroomDesignService.listDesignRunsApiProjectsProjectIdClassroomDesignRunsGet({
      projectId,
      limit: 1,
    })
    return runs[0] ?? null
  },

  create: (projectId: string, body: DesignRunIn): Promise<DesignRunOut> =>
    ClassroomDesignService.createDesignRunApiProjectsProjectIdClassroomDesignRunsPost({
      projectId,
      requestBody: body,
    }),

  apply: (projectId: string, runId: string): Promise<DesignApplyOut> =>
    ClassroomDesignService.applyDesignRunApiProjectsProjectIdClassroomDesignRunsRunIdApplyPost({
      projectId,
      runId,
    }),
}
