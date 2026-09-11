import { http } from '@shared/transport'
import type {
  Canvas,
  CanvasComment,
  CanvasObject,
  CanvasObjectCreate,
  CanvasObjectPatch,
  CanvasSnapshot,
  CanvasSnapshotDetail,
  BatchOp,
} from '../types'

const base = (chatroomId: string) => `/api/chatrooms/${chatroomId}/canvas`

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
