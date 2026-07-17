// Types for the skills slice (§31). The generated api-client *Out models are already
// precise, so they are re-exported as the slice contract rather than re-hand-written;
// only `SkillScopeRef` (the 4-arm scope descriptor the api/queries layers dispatch on)
// and the narrowed `kind`/`scan_status` unions are added here.

import type {
  BundleExportStatusOut,
  BundleImportStatusOut,
  BundleJobOut,
  BundleJobStatus,
  SkillBindingOut,
  SkillCopyIn,
  SkillCreateIn,
  SkillFileCreateIn,
  SkillFileOut,
  SkillFilePatchIn,
  SkillOut,
  SkillPageOut,
  SkillPatchIn,
  SkillScope,
  SkillScopeCountsOut,
  SkillSummaryOut,
} from '@shared/api-client'

export type {
  BundleExportStatusOut,
  BundleImportStatusOut,
  BundleJobOut,
  BundleJobStatus,
  SkillBindingOut,
  SkillCopyIn,
  SkillCreateIn,
  SkillFileCreateIn,
  SkillFileOut,
  SkillFilePatchIn,
  SkillOut,
  SkillPageOut,
  SkillPatchIn,
  SkillScope,
  SkillScopeCountsOut,
  SkillSummaryOut,
}

// Scope descriptor threaded through the api + queries layers so views target the right
// endpoint family without duplicating URL logic. Four scopes, not five (Q-26).
export type SkillScopeRef =
  | { kind: 'agent'; agentId: string }
  | { kind: 'project'; projectId: string }
  | { kind: 'org'; orgId: string }
  | { kind: 'platform' }

// R31.18: derived from the file's top-level directory, never client-chosen. `asset`
// binaries are not editable (AC-17).
export type SkillFileKind = 'reference' | 'script' | 'asset'

// Whole-skill readability is gated on every file being `clean` (AC-34, Q-18); the
// backend sends this as a bare string, narrowed here for the UI's status logic.
export type SkillScanStatus = 'pending' | 'clean' | 'infected' | 'error'
