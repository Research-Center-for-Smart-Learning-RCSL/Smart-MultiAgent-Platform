import { http } from '@shared/transport'
import type {
  Canvas,
  CanvasComment,
  CanvasObject,
  CanvasObjectCreate,
  CanvasObjectPatch,
  CanvasSearchResult,
  CanvasSnapshot,
  CanvasSnapshotDetail,
  CanvasTemplate,
  CanvasTemplateDetail,
  BatchOp,
} from '../types'

const base = (chatroomId: string) => `/chatrooms/${chatroomId}/canvas`

export async function getCanvas(chatroomId: string): Promise<Canvas> {
  const { data } = await http.get<Canvas>(base(chatroomId))
  return data
}

export async function updateCanvasSettings(
  chatroomId: string,
  body: { expose_to_agents: boolean },
): Promise<Canvas> {
  const { data } = await http.patch<Canvas>(base(chatroomId), body)
  return data
}

export async function deleteCanvas(chatroomId: string): Promise<void> {
  await http.delete(base(chatroomId))
}

export async function listObjects(
  chatroomId: string,
  params?: { limit?: number; offset?: number },
): Promise<CanvasObject[]> {
  const { data } = await http.get<CanvasObject[]>(`${base(chatroomId)}/objects`, { params })
  return data
}

export async function createObject(
  chatroomId: string,
  body: CanvasObjectCreate,
): Promise<CanvasObject> {
  const { data } = await http.post<CanvasObject>(`${base(chatroomId)}/objects`, body)
  return data
}

export async function updateObject(
  chatroomId: string,
  objectId: string,
  body: CanvasObjectPatch,
): Promise<CanvasObject> {
  const { data } = await http.patch<CanvasObject>(
    `${base(chatroomId)}/objects/${objectId}`,
    body,
  )
  return data
}

export async function deleteObject(chatroomId: string, objectId: string): Promise<void> {
  await http.delete(`${base(chatroomId)}/objects/${objectId}`)
}

export async function batchOperate(
  chatroomId: string,
  body: BatchOp,
): Promise<{ created: CanvasObject[]; updated: string[]; deleted: number }> {
  const { data } = await http.post(`${base(chatroomId)}/objects/batch`, body)
  return data
}

export async function uploadImage(
  chatroomId: string,
  file: File,
): Promise<CanvasObject> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await http.post<CanvasObject>(`${base(chatroomId)}/images`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function listSnapshots(
  chatroomId: string,
  params?: { limit?: number; offset?: number },
): Promise<CanvasSnapshot[]> {
  const { data } = await http.get<CanvasSnapshot[]>(`${base(chatroomId)}/snapshots`, { params })
  return data
}

export async function getSnapshot(
  chatroomId: string,
  snapshotId: string,
): Promise<CanvasSnapshotDetail> {
  const { data } = await http.get<CanvasSnapshotDetail>(
    `${base(chatroomId)}/snapshots/${snapshotId}`,
  )
  return data
}

export async function createSnapshot(
  chatroomId: string,
  body?: { label?: string },
): Promise<CanvasSnapshot> {
  const { data } = await http.post<CanvasSnapshot>(
    `${base(chatroomId)}/snapshots`,
    body ?? {},
  )
  return data
}

export async function restoreSnapshot(
  chatroomId: string,
  snapshotId: string,
): Promise<CanvasSnapshot> {
  const { data } = await http.post<CanvasSnapshot>(
    `${base(chatroomId)}/snapshots/${snapshotId}/restore`,
  )
  return data
}

export async function searchCanvas(
  chatroomId: string,
  query: string,
  params?: { limit?: number },
): Promise<CanvasSearchResult[]> {
  const { data } = await http.get<CanvasSearchResult[]>(
    `${base(chatroomId)}/search`,
    { params: { q: query, ...params } },
  )
  return data
}

export async function listComments(
  chatroomId: string,
  objectId: string,
  params?: { limit?: number; offset?: number },
): Promise<CanvasComment[]> {
  const { data } = await http.get<CanvasComment[]>(
    `${base(chatroomId)}/objects/${objectId}/comments`,
    { params },
  )
  return data
}

export async function createComment(
  chatroomId: string,
  objectId: string,
  body: { content: string },
): Promise<CanvasComment> {
  const { data } = await http.post<CanvasComment>(
    `${base(chatroomId)}/objects/${objectId}/comments`,
    body,
  )
  return data
}

export async function updateComment(
  chatroomId: string,
  commentId: string,
  body: { content: string },
): Promise<CanvasComment> {
  const { data } = await http.patch<CanvasComment>(
    `${base(chatroomId)}/comments/${commentId}`,
    body,
  )
  return data
}

export async function deleteComment(
  chatroomId: string,
  commentId: string,
): Promise<void> {
  await http.delete(`${base(chatroomId)}/comments/${commentId}`)
}

export async function getCommentCounts(
  chatroomId: string,
): Promise<Record<string, number>> {
  const { data } = await http.get<Record<string, number>>(
    `${base(chatroomId)}/objects/comment-counts`,
  )
  return data
}

// ---- Templates ---------------------------------------------------------------

export async function listTemplates(
  params?: { scope?: string; project_id?: string | undefined },
): Promise<CanvasTemplate[]> {
  const { data } = await http.get<CanvasTemplate[]>('/canvas-templates', { params })
  return data
}

export async function getTemplate(templateId: string): Promise<CanvasTemplateDetail> {
  const { data } = await http.get<CanvasTemplateDetail>(
    `/canvas-templates/${templateId}`,
  )
  return data
}

export async function createTemplate(body: {
  name: string
  description?: string | null
  project_id: string
  template_data: { objects: Array<Record<string, unknown>> }
}): Promise<CanvasTemplate> {
  const { data } = await http.post<CanvasTemplate>('/canvas-templates', body)
  return data
}

export async function deleteTemplate(templateId: string): Promise<void> {
  await http.delete(`/canvas-templates/${templateId}`)
}

export async function applyTemplate(
  chatroomId: string,
  templateId: string,
): Promise<{ created: CanvasObject[]; updated: string[]; deleted: number }> {
  const { data } = await http.post(`${base(chatroomId)}/apply-template`, {
    template_id: templateId,
  })
  return data
}

export async function saveAsTemplate(
  chatroomId: string,
  body: { name: string; description?: string | null | undefined },
): Promise<CanvasTemplate> {
  const { data } = await http.post<CanvasTemplate>(
    `${base(chatroomId)}/save-as-template`,
    body,
  )
  return data
}
