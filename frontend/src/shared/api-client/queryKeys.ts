// Centralised query-key factory re-exports for cross-slice invalidation.
// Each slice exports its own keys from index.ts; this file aggregates them
// into a single import point for callers that need multiple slices at once.
//
// Located in shared/api-client/ per §24.3.

// NOTE: Slices that export query-key factories do so from their index.ts.
// Import from @slices/<name> — never deep-import from queries/.

export { identityKeys } from '../../slices/identity/queries'
export { tenancyKeys } from '../../slices/tenancy/queries'
export { keysKeys } from '../../slices/keys/queries'
export { agentKeys } from '../../slices/agents/queries'
export { convKeys } from '../../slices/conversation/queries'
export { wfKeys } from '../../slices/workflow/queries'
export { adminKeys } from '../../slices/admin/queries'
