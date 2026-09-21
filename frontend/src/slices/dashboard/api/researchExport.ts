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

export async function getResearchExportStatus(
  jobId: string,
): Promise<ResearchExportStatusOut> {
  const { data } = await http.get<ResearchExportStatusOut>(
    `/exports/research/${jobId}`,
  )
  return data
}
