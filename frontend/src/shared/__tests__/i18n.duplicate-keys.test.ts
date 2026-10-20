// JSON.parse keeps the last of two equal keys, so a locale file that declares
// one key twice loses the first value without any error. The teacher dashboard
// shipped that way: `dashboard.reason` was both a column title and the object
// of reason texts, and the column rendered the raw key on staging.
import { readFileSync, readdirSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = join(__dirname, '..', '..')

function localeFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return entry.name === 'node_modules' ? [] : localeFiles(path)
    return dir.endsWith('locales') && entry.name.endsWith('.json') ? [path] : []
  })
}

/** Dotted paths of keys declared more than once within the same object. */
function duplicateKeys(text: string): string[] {
  const duplicates: string[] = []
  const stack: { keys: Set<string>; path: string }[] = []
  let pendingKey: string | null = null
  let i = 0
  while (i < text.length) {
    const ch = text[i]
    if (ch === '"') {
      let j = i + 1
      while (text[j] !== '"') j += text[j] === '\\' ? 2 : 1
      const value = JSON.parse(text.slice(i, j + 1)) as string
      i = j + 1
      while (/\s/.test(text[i] ?? '')) i++
      if (text[i] === ':' && stack.length > 0) {
        const frame = stack[stack.length - 1]
        const path = frame.path ? `${frame.path}.${value}` : value
        if (frame.keys.has(value)) duplicates.push(path)
        frame.keys.add(value)
        pendingKey = path
        i++
      }
      continue
    }
    if (ch === '{') {
      stack.push({ keys: new Set(), path: pendingKey ?? '' })
      pendingKey = null
    } else if (ch === '}') {
      stack.pop()
    } else if (ch === '[') {
      pendingKey = null
    }
    i++
  }
  return duplicates
}

describe('locale files', () => {
  const files = localeFiles(SRC)

  it('finds the locale files', () => {
    expect(files.length).toBeGreaterThan(10)
  })

  it('detects a duplicate key', () => {
    expect(duplicateKeys('{"a": {"b": "x", "b": {"c": "y"}}, "d": "z"}')).toEqual(['a.b'])
    expect(duplicateKeys('{"a": {"b": "x"}, "c": {"b": "y"}}')).toEqual([])
  })

  it.each(files.map((file) => [relative(SRC, file), file]))('%s declares each key once', (_, file) => {
    expect(duplicateKeys(readFileSync(file, 'utf-8'))).toEqual([])
  })
})
