// Thin API modules per resource. Every call returns parsed JSON; RFC 7807
// errors surface as AxiosError with `response.data` typed as ProblemJson.

import { http } from '@shared/transport'
import type {
  Attachment,
  AttachmentDownload,
  Chatroom,
  ExportStatus,
  Message,
  SearchResponse,
  Workspace,
} from '../types'

// ---- workspaces ----------------------------------------------------------

export async function listWorkspaces(projectId: string): Promise<Workspace[]> {
  const { data } = await http.get<Workspace[]>(`/projects/${projectId}/workspaces`)
  return data
}

export async function createWorkspace(
  projectId: string,
  payload: { name: string },
): Promise<Workspace> {
  const { data } = await http.post<Workspace>(
    `/projects/${projectId}/workspaces`,
    payload,
  )
  return data
}

export async function deleteWorkspace(workspaceId: string): Promise<void> {
  await http.delete(`/workspaces/${workspaceId}`)
}

// ---- chatrooms -----------------------------------------------------------

export async function listChatrooms(workspaceId: string): Promise<Chatroom[]> {
  const { data } = await http.get<Chatroom[]>(
    `/workspaces/${workspaceId}/chatrooms`,
  )
  return data
}

export async function createChatroom(
  workspaceId: string,
  payload: Partial<Chatroom> & { name: string },
): Promise<Chatroom> {
  const { data } = await http.post<Chatroom>(
    `/workspaces/${workspaceId}/chatrooms`,
    payload,
  )
  return data
}

export async function patchChatroom(
  chatroomId: string,
  version: number,
  patch: Partial<Chatroom>,
): Promise<Chatroom> {
  const { data } = await http.patch<Chatroom>(
    `/chatrooms/${chatroomId}`,
    patch,
    { headers: { 'If-Match': String(version) } },
  )
  return data
}

export async function deleteChatroom(chatroomId: string): Promise<void> {
  await http.delete(`/chatrooms/${chatroomId}`)
}

export async function getGuestLink(
  chatroomId: string,
): Promise<{ url: string }> {
  const { data } = await http.get<{ url: string }>(
    `/chatrooms/${chatroomId}/guest-link`,
  )
  return data
}

// ---- messages ------------------------------------------------------------

export async function listMessages(
  chatroomId: string,
  params: { before?: string; since?: string; limit?: number } = {},
): Promise<Message[]> {
  const { data } = await http.get<Message[]>(
    `/chatrooms/${chatroomId}/messages`,
    { params },
  )
  return data
}

export async function sendMessage(
  chatroomId: string,
  payload: { content_md: string; attachment_ids?: string[] },
): Promise<Message> {
  const { data } = await http.post<Message>(
    `/chatrooms/${chatroomId}/messages`,
    payload,
  )
  return data
}

export async function editMessage(
  messageId: string,
  version: number,
  content_md: string,
): Promise<Message> {
  const { data } = await http.patch<Message>(
    `/messages/${messageId}`,
    { content_md },
    { headers: { 'If-Match': String(version) } },
  )
  return data
}

export async function deleteMessage(messageId: string): Promise<void> {
  await http.delete(`/messages/${messageId}`)
}

// ---- attachments ---------------------------------------------------------

export async function uploadSingleShot(
  chatroomId: string,
  file: File,
): Promise<Attachment> {
  const form = new FormData()
  form.append('file', file)
  form.append('mime', file.type || 'application/octet-stream')
  const { data } = await http.post<Attachment>(
    `/chatrooms/${chatroomId}/attachments`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return data
}

export async function getAttachment(
  attachmentId: string,
): Promise<AttachmentDownload> {
  const { data } = await http.get<AttachmentDownload>(
    `/attachments/${attachmentId}`,
  )
  return data
}

// ---- search + export -----------------------------------------------------

export async function searchMessages(
  chatroomId: string,
  q: string,
  limit = 50,
): Promise<SearchResponse> {
  const { data } = await http.get<SearchResponse>(
    `/chatrooms/${chatroomId}/search`,
    { params: { q, limit } },
  )
  return data
}

export async function createExport(
  chatroomId: string,
): Promise<{ job_id: string; status: string }> {
  const { data } = await http.post<{ job_id: string; status: string }>(
    `/chatrooms/${chatroomId}/export`,
  )
  return data
}

export async function getExport(jobId: string): Promise<ExportStatus> {
  const { data } = await http.get<ExportStatus>(`/exports/${jobId}`)
  return data
}

// ---- guests --------------------------------------------------------------

export async function enrollGuest(
  chatroomId: string,
  guestToken: string,
): Promise<void> {
  await http.post(`/guest/${chatroomId}/${guestToken}/enroll`)
}

// ---- /compact slash command (G.10) ---------------------------------------

export async function compactChatroom(
  chatroomId: string,
): Promise<void> {
  await http.post(`/chatrooms/${chatroomId}/compact`)
}
