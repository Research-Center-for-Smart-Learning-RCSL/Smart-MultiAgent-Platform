// Conversation slice — shared DTO shapes. Mirrors backend pydantic schemas
// in `app/api/v1/{workspaces,chatrooms,messages,attachments,search,exports}.py`.

export interface Workspace {
  id: string
  project_id: string
  name: string
  // Phase 4α (R11.10) — wide-layer Concept Map privacy opt-in for the workspace.
  concept_map_enabled: boolean
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
  allow_member_groups: boolean
  version: number
  created_at: string
  created_by_user_id: string | null
  disclose_observers: boolean
  // "You are notified that observers are enabled" — false whenever disclosure
  // is off, regardless of actual bindings (R28.09).
  observers_present: boolean
  // §32 ([R32.05]). `disclose_drafts` is the room's setting; `drafts_readable` is
  // what a participant is actually told, and is false whenever disclosure is off
  // regardless of any live grant — so the client never combines the two itself.
  disclose_drafts: boolean
  drafts_readable: boolean
  // Advisory only (R5.05): true when the caller reached the room via a guest
  // link and holds no org/project role. Lets the UI hide guest-forbidden
  // controls; the server still enforces every action.
  viewer_is_guest?: boolean
  // R13.21/R13.23 — may this viewer edit and delete other people's messages
  // here? Serialized by the server because the client cannot derive it: an org
  // owner moderates without any `project_members` row (R5.03). Absent means
  // "no", so an older response fails closed.
  is_moderator?: boolean
}

export type SenderType = 'user' | 'agent' | 'system'

// R28.01 — binding role. `role` is present on bound-agent rows only for the
// room creator (R28.10); everyone else receives a shape identical to the
// pre-observer API.
export type ChatroomAgentRole = 'normal' | 'observer'

export type ReleaseTarget =
  | { kind: 'room'; message_id?: string }
  | { kind: 'agents'; agent_ids: string[]; woken: boolean }

// R28.19 — which platform-authored sentence a block renders as its footnote.
// The agent picks one; it never writes one, and no field suppresses it.
export type ObservationBasis = 'server_facts' | 'recent_window' | 'transcript'

// R28.18 — one declared schema field and how many counted submissions answered
// it. `title` is owner-authored; neither field ever carries a submission value.
export interface ObservationCoverageCell {
  name: string
  title: string
  filled: number
}

export interface ObservationAttemptRow {
  subject_code: string
  attempts: number
  submissions: number
  latest_outcome: 'valid' | 'invalid' | 'pending' | 'error' | string
  latest_error_class: string | null
}

interface ObservationBlockBase {
  title?: string
  caveat?: string
}

// R28.17 — the server measured every value on these; the agent supplied only
// the selection and the framing.
interface ObservationComputedBlock extends ObservationBlockBase {
  basis: ObservationBasis
  type_key?: string
  type_name?: string
  submissions_counted: number
}

export interface ObservationProseBlock {
  kind: 'prose'
  text: string
}

export interface ObservationKeyPointsBlock extends ObservationBlockBase {
  kind: 'key_points'
  basis: ObservationBasis
  points: { text: string; evidence?: string }[]
  next_step?: string
}

export interface ObservationTimelineBlock extends ObservationBlockBase {
  kind: 'timeline'
  basis: ObservationBasis
  entries: { label: string; detail?: string }[]
}

export interface ObservationFieldCoverageBlock extends ObservationComputedBlock {
  kind: 'field_coverage'
  cells: ObservationCoverageCell[]
}

export interface ObservationMandalaGridBlock extends ObservationComputedBlock {
  kind: 'mandala_grid'
  rows: ObservationCoverageCell[][]
}

export interface ObservationAttemptTableBlock extends ObservationComputedBlock {
  kind: 'attempt_table'
  rows: ObservationAttemptRow[]
  truncated: boolean
}

// R28.15 — the block kinds this build knows. Deliberately closed: the renderer
// switches on it exhaustively and falls back for anything else, because a stored
// observation must survive a rollback of this frontend past the release that
// introduced its kinds.
export type ObservationBlock =
  | ObservationProseBlock
  | ObservationKeyPointsBlock
  | ObservationTimelineBlock
  | ObservationFieldCoverageBlock
  | ObservationMandalaGridBlock
  | ObservationAttemptTableBlock

// R28.03 — observer output. Never a Message; delivered creator-only.
export interface Observation {
  id: string
  chatroom_id: string
  agent_id: string
  content_md: string
  metadata: Record<string, unknown>
  // R28.15. Empty for every observation recorded before presentation blocks
  // existed and for any turn that did not call `present_observation`; the card
  // renders `content_md` in that case, exactly as it always did.
  blocks: ObservationBlock[]
  trigger: 'every_n_messages' | 'silence_minutes' | string
  trigger_message_id: string | null
  released_at: string | null
  release_target: ReleaseTarget | null
  released_by_user_id: string | null
  created_at: string | null
}

// R28.13 — creator-only events on the /user/{id} channel.
export type ObservationEventType =
  | 'observation.started'
  | 'observation.created'
  | 'observation.failed'
  | 'observation.skipped'
  | 'observation.released'
  | 'observation.deleted'

// One retrieved RAG chunk cited on an agent reply. Lives in
// `Message.metadata.rag_sources` (populated by the backend turn engine).
// `filename` is null when the source document was deleted after retrieval.
export interface RagSource {
  document_id: string
  filename: string | null
  chunk_idx: number
  score: number
}

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

/** A message plus client-only optimistic state. `_status` is present on an
 *  unsent (optimistic) message and absent on every persisted message, so a
 *  truthy `_status` is the canonical "this is a local placeholder" test. */
export interface DisplayMessage extends Message {
  _status?: 'sending'
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
  | 'agent.progress'
  | 'agent.finished'
  | 'presence.joined'
  | 'presence.left'
  | 'typing.start'
  | 'typing.stop'
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
