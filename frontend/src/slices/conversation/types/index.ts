// Conversation slice — shared DTO shapes. Mirrors backend pydantic schemas
// in `app/api/v1/{workspaces,chatrooms,messages,attachments,search,exports}.py`.

export interface Workspace {
  id: string
  project_id: string
  name: string
  created_at: string
}

export interface Chatroom {
  id: string
  workspace_id: string
  name: string
  allow_org_members: boolean
  allow_project_members: boolean
  allow_project_owners_only: boolean
  allow_guest_links: boolean
  guest_token: string
  version: number
  created_at: string
}

export type SenderType = 'user' | 'agent' | 'system'

export interface Message {
  id: string
  chatroom_id: string
  sender_type: SenderType
  sender_id: string | null
  content_md: string
  metadata: Record<string, unknown>
  version: number
  created_at: string
  edited_at: string | null
  deleted_at: string | null
  // Attachments bound to this message (R13.11). Includes expired/quarantined so
  // the UI can show a placeholder instead of a dead link.
  attachments?: Attachment[]
}

export interface Attachment {
  id: string
  chatroom_id: string | null
  message_id: string | null
  filename: string
  mime: string
  size_bytes: number
  status: 'active' | 'quarantined' | 'expired'
  scan_status: 'pending' | 'clean' | 'quarantined' | 'skipped'
}

export interface AttachmentDownload extends Attachment {
  url: string
}

export interface SearchHit {
  message_id: string
  sender_type: SenderType
  sender_id: string | null
  created_at: string
  snippet: string
  rank: number
}

export interface SearchResponse {
  query: string
  hits: SearchHit[]
}

export interface ExportStatus {
  job_id: string
  chatroom_id: string
  status: 'queued' | 'running' | 'ready' | 'failed'
  url: string | null
  error: string | null
}

// R13.19 chatroom event names as a closed union.
export type ChatroomEventType =
  | 'message.created'
  | 'message.updated'
  | 'message.deleted'
  | 'agent.thinking'
  | 'agent.token'
  | 'agent.finished'
  | 'presence.joined'
  | 'presence.left'
  | 'approval.requested'
  | 'approval.resolved'
  | 'workflow.state_changed'

export interface ChatroomEvent {
  type: ChatroomEventType | string
  [k: string]: unknown
}

export {
  chatroomCreateSchema,
  type ChatroomCreateInput,
  workspaceCreateSchema,
  type WorkspaceCreateInput,
} from './schemas'
