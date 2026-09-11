export interface Canvas {
  id: string
  chatroom_id: string
  expose_to_agents: boolean
  created_at: string
}

export type CanvasObjectKind = 'note' | 'text' | 'image' | 'shape' | 'drawing' | 'connector'

export interface CanvasObject {
  id: string
  canvas_id: string
  kind: CanvasObjectKind
  content: string | null
  minio_path: string | null
  position_x: number
  position_y: number
  width: number
  height: number
  z_index: number
  style: Record<string, unknown>
  created_by_user_id: string | null
  created_by_guest_id: string | null
  created_by_agent_id: string | null
  created_at: string
  updated_at: string
  image_url?: string | null
}

export interface CanvasSnapshot {
  id: string
  canvas_id: string
  agent_digest: string | null
  created_by_user_id: string | null
  label: string | null
  created_at: string
}

export interface CanvasSnapshotDetail extends CanvasSnapshot {
  snapshot_data: Record<string, unknown>
}

export interface CanvasObjectCreate {
  kind: CanvasObjectKind
  position_x: number
  position_y: number
  width: number
  height: number
  z_index?: number
  content?: string | null
  style?: Record<string, unknown>
}

export interface CanvasObjectPatch {
  position_x?: number
  position_y?: number
  width?: number
  height?: number
  z_index?: number
  content?: string | null
  style?: Record<string, unknown>
}

export interface BatchOp {
  creates?: CanvasObjectCreate[]
  updates?: Array<{ id: string } & CanvasObjectPatch>
  deletes?: string[]
}

export interface CanvasSearchResult {
  object_id: string
  kind: CanvasObjectKind
  snippet: string
  rank: number
  position_x: number
  position_y: number
}

export interface CanvasComment {
  id: string
  object_id: string
  content: string
  created_by_user_id: string | null
  created_by_guest_id: string | null
  created_at: string
  updated_at: string
}

export interface CanvasTemplate {
  id: string
  scope: 'platform' | 'project'
  project_id: string | null
  name: string
  description: string | null
  created_by_user_id: string | null
  created_at: string
}

export interface CanvasTemplateDetail extends CanvasTemplate {
  template_data: { objects: CanvasObjectCreate[] }
}
