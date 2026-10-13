import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve, sep } from 'node:path'

// Matrix row 28 (dossier 2026-10-13-agent-config-visibility-and-project-reads):
// `GET /projects/{pid}/agents` returns every agent's configuration and the server
// refuses it to anyone who may not edit agents. A call site that fires for a
// non-editor is a failed request on a page that used to load, so every caller is
// listed here with the gate that keeps it from firing. A new caller fails this
// sweep until it is given a gate and added; a label needs `/agents/names` instead.
//
// Reads the tree from disk rather than importing it, so it crosses no slice
// boundary, like `viewRoots.test.ts` beside it.

const SRC = resolve(process.cwd(), 'src')

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      if (entry !== '__tests__' && entry !== 'api-client') walk(full, out)
    } else if (entry.endsWith('.vue') || entry.endsWith('.ts')) {
      out.push(full)
    }
  }
  return out
}

const rel = (file: string): string => relative(SRC, file).split(sep).join('/')

// Calls of the full listing, through every wrapper that exists for it.
const LISTING_CALL = /\bagentsApi\.list\(|\blistProjectAgents\(|\bfetchProjectAgents\(/

// file -> [file holding the gate, text proving the gate]
const GATED: Record<string, [string, string]> = {
  'slices/agents/views/AgentListView.vue': ['slices/agents/views/AgentListView.vue', 'enabled: canRead'],
  'slices/agents/composables/useRagDocuments.ts': [
    'slices/agents/composables/useRagDocuments.ts',
    'enabled: canEdit',
  ],
  'slices/agents/composables/useKnowmapDocuments.ts': [
    'slices/agents/composables/useKnowmapDocuments.ts',
    'enabled: canEdit',
  ],
  'slices/conversation/composables/useChatroomBindings.ts': [
    'slices/conversation/composables/useChatroomBindings.ts',
    'editor ? listProjectAgents(',
  ],
  // The form is a modal only the owner-gated types page opens.
  'slices/activities/components/ActivityTypeForm.vue': [
    'slices/activities/views/ActivityTypesView.vue',
    'decided && isAuthorized',
  ],
  // The wrapper itself; its callers are listed below.
  'slices/workflow/utils/projectAgents.ts': ['slices/workflow/utils/projectAgents.ts', 'agentsApi.list('],
  'slices/workflow/views/WorkflowEditorView.vue': [
    'slices/workflow/views/WorkflowEditorView.vue',
    'useBackstageGuard(',
  ],
  'slices/workflow/views/WorkflowBackstageView.vue': [
    'slices/workflow/views/WorkflowBackstageView.vue',
    'useBackstageGuard(',
  ],
}

const DEFINITIONS = new Set([
  // `export async function listProjectAgents(` is the wrapper, not a call.
  'slices/conversation/api/index.ts',
])

describe('full agent listing call sites (matrix row 28)', () => {
  const callers = walk(SRC)
    .filter((file) => LISTING_CALL.test(readFileSync(file, 'utf8')))
    .map(rel)
    .filter((file) => !DEFINITIONS.has(file))

  it('finds the call sites it is meant to police', () => {
    // A sweep that matches nothing passes whatever the tree holds.
    expect(callers).toContain('slices/agents/views/AgentListView.vue')
  })

  it('has no caller without a recorded editor gate', () => {
    expect(callers.filter((file) => !(file in GATED))).toEqual([])
  })

  it.each(Object.entries(GATED))('%s is gated', (_caller, [gateFile, marker]) => {
    expect(readFileSync(join(SRC, gateFile), 'utf8')).toContain(marker)
  })
})
