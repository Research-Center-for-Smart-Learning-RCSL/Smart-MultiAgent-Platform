// Plugin SDK contract (R30.17 / R30.19) — fixed day one so an isolating iframe
// sandbox (R30.19, deferred FU-1) can be enabled later as a bridge swap, not a
// rearchitecture. A plugin sees ONLY the five `ctx` members below: it has no
// path to the session, axios, or the WebSocket. Server-side scoring authority
// (R30.03) means a plugin can never submit a score — it `emit`s a raw payload
// and the authoritative outcome is computed by the backend.
//
// The contract went from four members to five in §32, deliberately and with the
// exact-set assertion in `sdk.test.ts` moved with it. `draft` is the one member
// that carries text the participant has NOT chosen to send, so a plugin author
// reading this file should know: what it reports is readable by an agent the room
// creator granted, and by nothing else.

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
  /** Explicit render position, ascending ([R30.36]). Declared because the stored
   *  schema is `jsonb`, which normalises object keys rather than preserving the
   *  order they were authored in, so object order cannot carry this. A schema
   *  where no property declares it renders in stored order, as before. */
  'x-order'?: number
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

/** The complete surface handed to a plugin. Exactly these five members — a
 *  well-behaved plugin performs all I/O through `emit` and `draft` (AC-3). */
export interface ActivityRenderCtx {
  schema: JSONSchema
  session: ActivitySessionRef
  emit(payload: unknown): Promise<ActivitySubmissionResult>
  /** Report the worksheet's current, UNSENT contents ([R32.01]).
   *
   *  Fire-and-forget: no promise, no result, and no error path a plugin can
   *  react to. That is the point — a plugin must not be able to tell whether
   *  anyone is reading, because a plugin that could would be a channel for
   *  telling the participant something the room's disclosure chip already says
   *  properly, or for hiding it.
   *
   *  The host throttles; call it whenever the worksheet changes. Call it with an
   *  empty payload (or `emit` successfully) to retract. */
  draft(payload: unknown): void
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

export type PluginToHostMessage =
  | { kind: 'emit'; payload: unknown }
  // Added with the fifth ctx member so the deferred IframeBridge still has a
  // complete contract and stays a bridge swap rather than a rearchitecture. It
  // is one-way by construction: there is no `HostToPluginMessage` acknowledging
  // a draft, matching `ctx.draft` returning nothing.
  | { kind: 'draft'; payload: unknown }

export type HostToPluginMessage =
  | { kind: 'schema'; schema: JSONSchema }
  | { kind: 'result'; result: ActivitySubmissionResult }
