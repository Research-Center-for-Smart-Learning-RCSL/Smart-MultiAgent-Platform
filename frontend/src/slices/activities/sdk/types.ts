// Plugin SDK contract (R30.17 / R30.19) — fixed day one so an isolating iframe
// sandbox (R30.19, deferred FU-1) can be enabled later as a bridge swap, not a
// rearchitecture. A plugin sees ONLY the four `ctx` members below: it has no
// path to the session, axios, or the WebSocket. Server-side scoring authority
// (R30.03) means a plugin can never submit a score — it `emit`s a raw payload
// and the authoritative outcome is computed by the backend.

/** The three backend validation states (`ValidationStatus` in the core context). */
export type ActivityValidationStatus = 'pending' | 'validated' | 'error'

/** Minimal JSON-schema shape the host understands. `payload_schema` from an
 *  `ActivityType` is an untyped `Record`; this narrows the parts the schema
 *  form renderer reads without pretending to model the whole JSON-schema spec. */
export interface JSONSchema {
  type?: 'object' | 'string' | 'number' | 'integer' | 'boolean' | 'array'
  title?: string
  description?: string
  properties?: Record<string, JSONSchema>
  required?: string[]
  enum?: Array<string | number>
  items?: JSONSchema
  [key: string]: unknown
}

/** The outcome a plugin receives back from `emit` — the backend result, never
 *  a client-computed score. */
export interface ActivitySubmissionResult {
  submissionId: string
  status: ActivityValidationStatus
  isValid: boolean | null
  subScores: Record<string, unknown>
}

export type ActivityTranslate = (key: string, named?: Record<string, unknown>) => string

export interface ActivitySessionRef {
  activityTypeKey: string
  sessionId: string | null
}

/** The complete surface handed to a plugin. Exactly these four members — a
 *  well-behaved plugin performs all I/O through `emit` (AC-3). */
export interface ActivityRenderCtx {
  schema: JSONSchema
  session: ActivitySessionRef
  emit(payload: unknown): Promise<ActivitySubmissionResult>
  t: ActivityTranslate
}

export interface ActivityPluginManifest {
  key: string
  version: string
  title: string
}

export type ActivityTeardown = () => void

export interface ActivityPlugin {
  manifest: ActivityPluginManifest
  /** Optional client-side schema override; falls back to `ActivityType.payload_schema`. */
  schema?: JSONSchema
  /** Mount the plugin into `container`. Return a teardown to clean up on unmount. */
  render(container: HTMLElement, ctx: ActivityRenderCtx): void | ActivityTeardown
}

// ---- host <-> plugin postMessage contract (fixed for the deferred sandbox) ----
// v1 mounts plugins in-process and never serializes these, but the message kinds
// are typed now so the IframeBridge (FU-1) is a bridge swap. Direction is encoded
// in the type name.

export type PluginToHostMessage = { kind: 'emit'; payload: unknown }

export type HostToPluginMessage =
  | { kind: 'schema'; schema: JSONSchema }
  | { kind: 'result'; result: ActivitySubmissionResult }
