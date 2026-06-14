// Minimal TUS 1.0.0 client — shared transport utility.
//
// This is intentionally NOT tus-js-client: we already speak the protocol from
// the backend side and the UI's needs are modest (linear append, progress
// callbacks, pause/resume via an AbortController). Keeping it in-repo avoids a
// third-party bundle and reuses the same axios transport (auth header + refresh
// interceptor) for creation.
//
// Lives in shared/ because more than one slice uploads via tus: conversation
// (chat_attachment) and agents (rag_source). The cross-slice dependency rule
// forbids agents → conversation, so the shared transport is the only place both
// may import from.

import { http } from './axios'

const CHUNK_SIZE = 8 * 1024 * 1024 // 8 MB; backend accepts up to 16 MB per PATCH
const TUS_VERSION = '1.0.0'

export interface TusUploadOptions {
  file: File
  purpose: 'chat_attachment' | 'rag_source'
  projectId: string
  chatroomId?: string
  ragConfigId?: string
  onProgress?: (bytesUploaded: number, bytesTotal: number) => void
  signal?: AbortSignal
}

export interface TusUploadResult {
  uploadId: string
  resourceHeader: string | null
}

function encodeMetadata(kv: Record<string, string | undefined>): string {
  const parts: string[] = []
  for (const [k, v] of Object.entries(kv)) {
    if (v === undefined || v === null) continue
    // base64 value per TUS spec. btoa requires binary string; we run the
    // UTF-8 bytes through unescape(encodeURIComponent(...)) to handle
    // non-ASCII filenames.
    const b64 = btoa(unescape(encodeURIComponent(v)))
    parts.push(`${k} ${b64}`)
  }
  return parts.join(',')
}

async function createUpload(opts: TusUploadOptions): Promise<string> {
  const metadata = encodeMetadata({
    purpose: opts.purpose,
    project_id: opts.projectId,
    chatroom_id: opts.chatroomId,
    rag_config_id: opts.ragConfigId,
    filename: opts.file.name,
    mime: opts.file.type || 'application/octet-stream',
  })
  const res = await http.post('/tus', null, {
    headers: {
      'Tus-Resumable': TUS_VERSION,
      'Upload-Length': String(opts.file.size),
      'Upload-Metadata': metadata,
    },
  })
  const location = (res.headers['location'] ?? '') as string
  const match = /\/api\/tus\/([0-9a-f-]{36})/i.exec(location)
  if (!match) throw new Error(`TUS creation missing Location header: ${location}`)
  return match[1]
}

async function patchChunk(
  uploadId: string,
  offset: number,
  chunk: Blob,
  signal?: AbortSignal,
): Promise<{ newOffset: number; resourceHeader: string | null }> {
  const res = await http.patch(`/tus/${uploadId}`, chunk, {
    headers: {
      'Tus-Resumable': TUS_VERSION,
      'Upload-Offset': String(offset),
      'Content-Type': 'application/offset+octet-stream',
    },
    signal,
    transformRequest: [(d) => d], // axios would JSON-stringify otherwise
  })
  return {
    newOffset: Number(res.headers['upload-offset'] ?? 0),
    resourceHeader: (res.headers['x-smap-resource'] ?? null) as string | null,
  }
}

export async function tusUpload(opts: TusUploadOptions): Promise<TusUploadResult> {
  const uploadId = await createUpload(opts)
  let offset = 0
  let resource: string | null = null
  while (offset < opts.file.size) {
    const end = Math.min(offset + CHUNK_SIZE, opts.file.size)
    const blob = opts.file.slice(offset, end)
    const r = await patchChunk(uploadId, offset, blob, opts.signal)
    offset = r.newOffset
    if (r.resourceHeader) resource = r.resourceHeader
    opts.onProgress?.(offset, opts.file.size)
  }
  return { uploadId, resourceHeader: resource }
}

export function resourceToAttachmentId(header: string | null): string | null {
  if (!header) return null
  const m = /\/api\/attachments\/([0-9a-f-]{36})/i.exec(header)
  return m ? m[1] : null
}

export function resourceToRagDocumentId(header: string | null): string | null {
  if (!header) return null
  const m = /\/api\/rag-documents\/([0-9a-f-]{36})/i.exec(header)
  return m ? m[1] : null
}
