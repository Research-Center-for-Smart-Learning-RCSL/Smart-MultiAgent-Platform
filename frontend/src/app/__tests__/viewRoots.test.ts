import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve, sep } from 'node:path'

// F-3/F-40/F-44 are three shapes of one defect: a view root that re-declares
// something the shell already owns. `main.app-shell__content` is the single
// owner of content padding (02-layout-shell.md §3.3, implemented as the
// breakpoint ladder in AppShell.vue) and the single `<main>` landmark, so a
// view root may carry neither. This sweep reads the tree from disk rather than
// importing it, so it crosses no slice boundary; it lives under `app/` because
// `app/` owns the shell contract it enforces.
//
// Standing in for a lint rule on purpose — see the dossier's FU-6. If a
// legitimate exemption ever appears, that is the moment to build the rule
// rather than to widen this assertion.

// vitest.config's root is `frontend/`; `import.meta.url` is not a file URL
// under the jsdom environment, so resolve from the run root instead.
const SRC = resolve(process.cwd(), 'src')

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) walk(full, out)
    else if (entry.endsWith('.vue')) out.push(full)
  }
  return out
}

const allVueFiles = walk(SRC)
const viewFiles = allVueFiles.filter((f) => f.includes(`${sep}views${sep}`))
const sliceFiles = allVueFiles.filter((f) => f.includes(`${sep}slices${sep}`))

/**
 * The opening tag of a single-file component's template root, verbatim.
 *
 * Scans past the first column-anchored `<template>` (a nested `<template v-if>`
 * is indented) and returns everything up to the first `>` that is not inside a
 * quoted attribute value — `:class="a > b ? …"` would otherwise truncate it.
 *
 * Leading comments are skipped rather than returned as the root: a comment has
 * no class attribute, so treating it as the root would make this gate pass a
 * padded view silently, which is the one failure mode a gate must not have.
 */
function rootTag(source: string): string {
  const open = source.match(/^<template>\r?$/m)
  if (open?.index === undefined) return ''
  let i = source.indexOf('<', open.index + open[0].length)
  while (i >= 0 && source.startsWith('<!--', i)) {
    const end = source.indexOf('-->', i)
    if (end < 0) return ''
    i = source.indexOf('<', end + 3)
  }
  if (i < 0) return ''
  let quote: string | null = null
  for (let j = i; j < source.length; j++) {
    const ch = source[j]
    if (quote) {
      if (ch === quote) quote = null
    } else if (ch === '"' || ch === "'") {
      quote = ch
    } else if (ch === '>') {
      return source.slice(i, j + 1)
    }
  }
  return source.slice(i)
}

// p-6, px-4, sm:p-6, lg:pt-2 — every Tailwind padding utility, plain or
// responsive. Margin is untouched: a view root may still space itself from a
// sibling, it just may not re-declare the page gutter.
const PADDING_UTILITY = /^(?:sm:|md:|lg:|xl:|2xl:)?p[trblxyse]?-/

function paddingUtilities(tag: string): string[] {
  const cls = tag.match(/\sclass="([^"]*)"/)
  if (!cls) return []
  return cls[1].split(/\s+/).filter((token) => PADDING_UTILITY.test(token))
}

describe('view template roots', () => {
  it('finds the views it is meant to sweep', () => {
    expect(viewFiles.length).toBeGreaterThan(70)
  })

  it('leaves content padding to the shell', () => {
    const offenders = viewFiles
      .map((file) => ({ file, found: paddingUtilities(rootTag(readFileSync(file, 'utf8'))) }))
      .filter((r) => r.found.length > 0)
      .map((r) => `${relative(SRC, r.file)} (${r.found.join(' ')})`)

    expect(offenders).toEqual([])
  })

  it('leaves the main landmark to the shell', () => {
    const offenders = sliceFiles
      .filter((file) => readFileSync(file, 'utf8').includes('<main'))
      .map((file) => relative(SRC, file))

    expect(offenders).toEqual([])
  })
})
