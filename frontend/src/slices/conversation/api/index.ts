// Thin use-case-shaped wrappers over the generated @shared/api-client services
// (R24.13). The request/response shapes come from the generated client; auth and
// session behavior (bearer token, silent 401 refresh, problem+json -> typed
// ApiError/ValidationError) come from shared/transport/axios.ts's instrumentation
// of the bare axios singleton the generated client calls into — callers still
// branch on `err instanceof ApiError`, never on a raw AxiosError. Every function
// resolves to the response body directly and keeps its slice-local return type
// (the slice's hand-rolled types encode refinements the generated client cannot
// express: the discriminated ReleaseTarget, RagSource[] in Message.metadata).

import {
  AgentsService,
  AttachmentsService,
  ChatroomsService,
  ExportsService,
  GuestsService,
  MessagesService,
  ObservationsService,
  SearchService,
  WorkspacesService,
} from '@shared/api-client'
import { asBinaryFormField } from '@shared/transport'
import type { MessageOut, ObservationOut } from '@shared/api-client'
import type { Agent } from '@slices/agents'
import type { ApprovalWithVotes } from '@shared/types/workflow'
import type {
  Attachment,
  AttachmentDownload,
  Chatroom,
  ChatroomAgentRole,
  ExportStatus,
  Message,
  Observation,
  ReleaseTarget,
  SearchResponse,
  Workspace,
} from '../types'

// ---- workspaces ----------------------------------------------------------

export async function listWorkspaces(projectId: string): Promise<Workspace[]> {
  return WorkspacesService.listWorkspacesApiProjectsProjectIdWorkspacesGet({ projectId })
}

export async function createWorkspace(
  projectId: string,
  payload: { name: string },
): Promise<Workspace> {
  // Backend returns WorkspaceCreatedOut (Workspace + default_chatroom_id); the
  // extra field is unused by callers, so the narrower Workspace return stands.
  return WorkspacesService.createWorkspaceApiProjectsProjectIdWorkspacesPost({
    projectId,
    requestBody: payload,
  })
}

export async function getWorkspace(workspaceId: string): Promise<Workspace> {
  return WorkspacesService.readWorkspaceApiWorkspacesWorkspaceIdGet({ workspaceId })
}

export async function deleteWorkspace(workspaceId: string): Promise<void> {
  await WorkspacesService.deleteWorkspaceApiWorkspacesWorkspaceIdDelete({ workspaceId })
}

// Phase 4α (R11.10) — Project-Owner-gated wide-layer Concept Map privacy opt-in.
export async function setWorkspaceConceptMapEnabled(
  workspaceId: string,
  enabled: boolean,
): Promise<{ workspace_id: string; concept_map_enabled: boolean }> {
  return WorkspacesService.setConceptMapEnabledApiWorkspacesWorkspaceIdConceptMapEnabledPut({
    workspaceId,
    requestBody: { enabled },
  })
}

// ---- chatrooms -----------------------------------------------------------

export async function listChatrooms(workspaceId: string): Promise<Chatroom[]> {
  return ChatroomsService.listChatroomsApiWorkspacesWorkspaceIdChatroomsGet({ workspaceId })
}

export async function createChatroom(
  workspaceId: string,
  payload: Partial<Chatroom> & { name: string },
): Promise<Chatroom> {
  return ChatroomsService.createChatroomApiWorkspacesWorkspaceIdChatroomsPost({
    workspaceId,
    requestBody: payload,
  })
}

export async function patchChatroom(
  chatroomId: string,
  version: number,
  patch: Partial<Chatroom>,
): Promise<Chatroom> {
  return ChatroomsService.patchChatroomApiChatroomsChatroomIdPatch({
    chatroomId,
    ifMatch: String(version),
    requestBody: patch,
  })
}

export async function getChatroom(chatroomId: string): Promise<Chatroom> {
  return ChatroomsService.readChatroomApiChatroomsChatroomIdGet({ chatroomId })
}

export async function deleteChatroom(chatroomId: string): Promise<void> {
  await ChatroomsService.deleteChatroomApiChatroomsChatroomIdDelete({ chatroomId })
}

export async function getGuestLink(
  chatroomId: string,
): Promise<{ url: string }> {
  return ChatroomsService.readGuestLinkApiChatroomsChatroomIdGuestLinkGet({ chatroomId })
}

// ---- chatroom agent bindings ---------------------------------------------
// A chatroom binds agents from its parent project. Chatrooms carry only a
// `workspace_id`, so the settings UI resolves workspace → project via
// `getWorkspace`, lists that project's agents, and toggles bindings here.

export async function listProjectAgents(projectId: string): Promise<Agent[]> {
  return AgentsService.listProjectAgentsApiProjectsProjectIdAgentsGet({ projectId })
}

// `role` is present only for the room creator (R28.10) — never default it
// client-side; its absence means "you are not told".
export interface BoundAgentRef {
  agent_id: string
  role?: ChatroomAgentRole
}

export async function listChatroomAgents(
  chatroomId: string,
): Promise<BoundAgentRef[]> {
  const refs = await ChatroomsService.listChatroomAgentsApiChatroomsChatroomIdAgentsGet({
    chatroomId,
  })
  // Generated AgentRef.role is `... | null`; BoundAgentRef.role is optional and
  // never null (absence, not null, is the "you are not the creator" signal).
  return refs.map((r) => ({
    agent_id: r.agent_id,
    ...(r.role ? { role: r.role } : {}),
  }))
}

// Human participants (message authors + guests) with their resolved display
// names. Used to label user messages; `display_name` is null when unset, in
// which case the UI falls back to a truncated id. Never includes email.
export interface ChatroomMember {
  user_id: string
  display_name: string | null
}

export async function listChatroomMembers(
  chatroomId: string,
): Promise<ChatroomMember[]> {
  return ChatroomsService.listChatroomMembersApiChatroomsChatroomIdMembersGet({ chatroomId })
}

export async function addChatroomAgent(
  chatroomId: string,
  agentId: string,
  role?: ChatroomAgentRole,
): Promise<void> {
  await ChatroomsService.addChatroomAgentApiChatroomsChatroomIdAgentsPost({
    chatroomId,
    requestBody: { agent_id: agentId, ...(role ? { role } : {}) },
  })
}

export async function setChatroomAgentRole(
  chatroomId: string,
  agentId: string,
  role: ChatroomAgentRole,
): Promise<void> {
  await ChatroomsService.patchChatroomAgentRoleApiChatroomsChatroomIdAgentsAgentIdPatch({
    chatroomId,
    agentId,
    requestBody: { role },
  })
}

export async function removeChatroomAgent(
  chatroomId: string,
  agentId: string,
): Promise<void> {
  await ChatroomsService.removeChatroomAgentApiChatroomsChatroomIdAgentsAgentIdDelete({
    chatroomId,
    agentId,
  })
}

// ---- observations (creator-only, R28.03) -----------------------------------

// Bridge B1: ObservationOut.release_target is typed `Record<string, any> | null`
// (the backend response model is still an untyped dict — enum-sweep FU-13). The
// runtime value is the discriminated ReleaseTarget the backend emits, so we
// re-type that one field. Remove this helper when FU-13 gives the field a nested
// model.
function toObservation(o: ObservationOut): Observation {
  return { ...o, release_target: o.release_target as ReleaseTarget | null }
}

export async function listObservations(
  chatroomId: string,
  params: { before?: string; limit?: number } = {},
): Promise<Observation[]> {
  const rows = await ObservationsService.listObservationsApiChatroomsChatroomIdObservationsGet({
    chatroomId,
    ...params,
  })
  return rows.map(toObservation)
}

export type ReleaseBody =
  | { target: 'room'; content_override?: string }
  | { target: 'agents'; agent_ids: string[]; wake: boolean; content_override?: string }

export async function releaseObservation(
  chatroomId: string,
  observationId: string,
  body: ReleaseBody,
): Promise<Observation> {
  const released =
    await ObservationsService.releaseObservationApiChatroomsChatroomIdObservationsObservationIdReleasePost(
      { chatroomId, observationId, requestBody: body },
    )
  return toObservation(released)
}

export async function deleteObservation(
  chatroomId: string,
  observationId: string,
): Promise<void> {
  await ObservationsService.deleteObservationApiChatroomsChatroomIdObservationsObservationIdDelete(
    { chatroomId, observationId },
  )
}

// ---- messages ------------------------------------------------------------

// MessageOut.created_at is `string | null` in the contract (the pydantic model
// declares it Optional), but a persisted message always carries a timestamp;
// the slice's Message keeps the non-null shape its consumers (sorting, display)
// rely on. This bridge re-types that single field. Message is assignable to
// MessageOut, so the assertion is checked, not a blind `unknown` cast.
function toMessage(m: MessageOut): Message {
  return m as Message
}

export async function listMessages(
  chatroomId: string,
  params: { before?: string; since?: string; limit?: number } = {},
): Promise<Message[]> {
  const rows = await MessagesService.listMessagesApiChatroomsChatroomIdMessagesGet({
    chatroomId,
    ...params,
  })
  return rows.map(toMessage)
}

export async function sendMessage(
  chatroomId: string,
  payload: { content_md: string; attachment_ids?: string[]; mention_agent_ids?: string[] },
): Promise<Message> {
  return toMessage(
    await MessagesService.sendMessageApiChatroomsChatroomIdMessagesPost({
      chatroomId,
      requestBody: payload,
    }),
  )
}

export async function getMessage(messageId: string): Promise<Message> {
  return toMessage(await MessagesService.readMessageApiMessagesMessageIdGet({ messageId }))
}

export async function getChatroomPresence(chatroomId: string): Promise<string[]> {
  const presence = await ChatroomsService.getChatroomPresenceApiChatroomsChatroomIdPresenceGet({
    chatroomId,
  })
  return presence.user_ids
}

// F-13: room-scoped approvals read side, so a client can discover a gate
// whose `approval.requested` frame was missed while disconnected.
export async function listChatroomApprovals(chatroomId: string): Promise<ApprovalWithVotes[]> {
  return ChatroomsService.listChatroomApprovalsApiChatroomsChatroomIdApprovalsGet({ chatroomId })
}

export async function editMessage(
  messageId: string,
  version: number,
  content_md: string,
): Promise<Message> {
  return toMessage(
    await MessagesService.editMessageApiMessagesMessageIdPatch({
      messageId,
      ifMatch: String(version),
      requestBody: { content_md },
    }),
  )
}

export async function deleteMessage(messageId: string): Promise<void> {
  await MessagesService.deleteMessageApiMessagesMessageIdDelete({ messageId })
}

// ---- attachments ---------------------------------------------------------

export async function uploadSingleShot(
  chatroomId: string,
  file: File,
): Promise<Attachment> {
  return AttachmentsService.createSingleShotApiChatroomsChatroomIdAttachmentsPost({
    chatroomId,
    formData: {
      file: asBinaryFormField(file),
      mime: file.type || 'application/octet-stream',
    },
  })
}

export async function getAttachment(
  attachmentId: string,
): Promise<AttachmentDownload> {
  return AttachmentsService.readAttachmentApiAttachmentsAttachmentIdGet({ attachmentId })
}

// ---- search + export -----------------------------------------------------

export async function searchMessages(
  chatroomId: string,
  q: string,
  limit = 50,
): Promise<SearchResponse> {
  return SearchService.searchMessagesApiChatroomsChatroomIdSearchGet({ chatroomId, q, limit })
}

export interface ExportOptions {
  format?: 'markdown' | 'json' | 'pdf'
  date_range?: 'all' | 'last_7_days' | 'last_30_days' | 'custom'
  start?: string
  end?: string
}

export async function createExport(
  chatroomId: string,
  opts: ExportOptions = {},
): Promise<{ job_id: string; status: string }> {
  return ExportsService.createExportApiChatroomsChatroomIdExportPost({
    chatroomId,
    requestBody: opts,
  })
}

export async function getExport(jobId: string): Promise<ExportStatus> {
  return ExportsService.getExportApiExportsJobIdGet({ jobId })
}

// ---- guests --------------------------------------------------------------

export async function enrollGuest(
  chatroomId: string,
  guestToken: string,
  displayName?: string,
): Promise<void> {
  await GuestsService.enrollGuestApiGuestChatroomIdGuestTokenEnrollPost({
    chatroomId,
    guestToken,
    ...(displayName ? { requestBody: { display_name: displayName } } : {}),
  })
}

// ---- /compact slash command (G.10) ---------------------------------------

export async function compactChatroom(
  chatroomId: string,
): Promise<void> {
  // The endpoint returns a 202 body (Record<string, string>); the caller only
  // awaits completion, so the wrapper stays Promise<void>.
  await ChatroomsService.compactChatroomApiChatroomsChatroomIdCompactPost({ chatroomId })
}
