/**
 * Reads entity IDs provisioned by global-setup.ts, falling back to the
 * process.env.E2E_* set when global-setup produced nothing.
 */
import { readFileSync } from 'fs'
import { dirname, resolve } from 'path'
import { fileURLToPath } from 'url'

// This file runs as ESM ("type": "module"), where __dirname is undefined.
const HERE = dirname(fileURLToPath(import.meta.url))

let _cache: Record<string, string> | null = null

// Two independent seed routines can populate these IDs: global-setup.ts (via
// the public API, written to .e2e-seed.json) and the backend's
// app/bootstrap/seed.py (exported into process.env by the CI job). Each set is
// internally coherent, but they describe DIFFERENT entity graphs. Resolving
// per-key across both sources yields combinations that exist in neither — e.g.
// a workspace from the file plus a workflow from the environment that lives in
// another workspace, which makes the editor's listWorkflows() lookup miss and
// turns a would-be skip into a 20s locator timeout. Pick one source for the
// whole set instead.
function load(): Record<string, string> {
  if (_cache) return _cache
  const path = resolve(HERE, '../.e2e-seed.json')
  let fromFile: Record<string, string> = {}
  try {
    fromFile = JSON.parse(readFileSync(path, 'utf-8'))
  } catch {
    fromFile = {}
  }
  _cache = Object.keys(fromFile).length > 0 ? fromFile : collectFromEnv()
  return _cache
}

function collectFromEnv(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(process.env)) {
    if (key.startsWith('E2E_') && value) out[key] = value
  }
  return out
}

export function env(key: string): string | undefined {
  return load()[key]
}
