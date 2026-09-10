import { api } from '@shared/transport/axios'
import type {
  Canvas,
  CanvasObject,
  CanvasObjectCreate,
  CanvasObjectPatch,
  CanvasSnapshot,
  BatchOp,
} from '../types'

const base = (chatroomId: string) => `/api/chatrooms/${chatroomId}/canvas`

export async function getCanvas(chatroomId: string): Promise<Canvas> {
  const { data } = await api.get<Canvas>(base(chatroomId))
  return data
}

export async function updateCanvasSettings(
  chatroomId: string,
  body: { expose_to_agents: boolean },
): Promise<Canvas> {
  const { data } = await api.patch<Canvas>(base(chatroomId), body)
  return data
}

export async function deleteCanvas(chatroomId: string): Promise<void> {
  await api.delete(base(chatroomId))
}

export async function listObjects(
  chatroomId: string,
  params?: { limit?: number; offset?: number },
): Promise<CanvasObject[]> {
  const { data } = await api.get<CanvasObject[]>(`${base(chatroomId)}/objects`, { params })
  return data
}

export async function createObject(
  chatroomId: string,
  body: CanvasObjectCreate,
): Promise<CanvasObject> {
  const { data } = await api.post<CanvasObject>(`${base(chatroomId)}/objects`, body)
  return data
}

export async function updateObject(
  chatroomId: string,
  objectId: string,
  body: CanvasObjectPatch,
): Promise<CanvasObject> {
  const { data } = await api.patch<CanvasObject>(
    `${base(chatroomId)}/objects/${objectId}`,
    body,
  )
  return data
}

export async function deleteObject(chatroomId: string, objectId: string): Promise<void> {
  await api.delete(`${base(chatroomId)}/objects/${objectId}`)
}

export async function batchOperate(
  chatroomId: string,
  body: BatchOp,
): Promise<{ created: CanvasObject[]; updated: string[]; deleted: number }> {
  const { data } = await api.post(`${base(chatroomId)}/objects/batch`, body)
  return data
}

export async function uploadImage(
  chatroomId: string,
  file: File,
): Promise<CanvasObject> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<CanvasObject>(`${base(chatroomId)}/images`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function listSnapshots(
  chatroomId: string,
  params?: { limit?: number; offset?: number },
): Promise<CanvasSnapshot[]> {
  const { data } = await api.get<CanvasSnapshot[]>(`${base(chatroomId)}/snapshots`, { params })
  return data
}

export async function createSnapshot(chatroomId: string): Promise<CanvasSnapshot> {
  const { data } = await api.post<CanvasSnapshot>(`${base(chatroomId)}/snapshots`)
  return data
}
