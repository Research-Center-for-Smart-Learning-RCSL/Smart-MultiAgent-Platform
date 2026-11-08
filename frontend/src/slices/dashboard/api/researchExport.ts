import { ApiError } from '@shared/errors'
import { http } from '@shared/transport'

interface ResearchExportCreateIn {
  created_after?: string | null
  created_before?: string | null
}

interface ResearchExportCreateOut {
  job_id: string
  status: string
}

interface ResearchExportStatusOut {
  job_id: string
  workspace_id: string
  status: string
  url: string | null
  error: string | null
}

export async function createResearchExport(
  workspaceId: string,
  body?: ResearchExportCreateIn,
): Promise<ResearchExportCreateOut> {
  const { data } = await http.post<ResearchExportCreateOut>(
    `/workspaces/${workspaceId}/export/research-data`,
    body ?? {},
  )
  return data
}

interface ResearchExportLatestOut {
  job_id: string
  status: string
}

// The owner's latest export of the workspace, or null when there is none.
export async function getLatestResearchExport(
  workspaceId: string,
): Promise<ResearchExportLatestOut | null> {
  try {
    const { data } = await http.get<ResearchExportLatestOut>('/exports/research/latest', {
      params: { workspace_id: workspaceId },
    })
    return data
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

export async function getResearchExportStatus(
  jobId: string,
): Promise<ResearchExportStatusOut> {
  const { data } = await http.get<ResearchExportStatusOut>(
    `/exports/research/${jobId}`,
  )
  return data
}
