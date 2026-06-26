/**
 * Reads entity IDs provisioned by global-setup.ts.
 * Falls back to process.env.E2E_* for backwards compatibility.
 */
import { readFileSync } from 'fs'
import { dirname, resolve } from 'path'
import { fileURLToPath } from 'url'

// This file runs as ESM ("type": "module"), where __dirname is undefined.
const HERE = dirname(fileURLToPath(import.meta.url))

let _cache: Record<string, string> | null = null

function load(): Record<string, string> {
  if (_cache) return _cache
  const path = resolve(HERE, '../.e2e-seed.json')
  try {
    _cache = JSON.parse(readFileSync(path, 'utf-8'))
  } catch {
    _cache = {}
  }
  return _cache!
}

export function env(key: string): string | undefined {
  return load()[key] || process.env[key]
}
