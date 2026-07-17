import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, type MaybeRefOrGetter, toValue } from 'vue'

import { skillsApi } from '../api'
import type {
  SkillCopyIn,
  SkillCreateIn,
  SkillFileCreateIn,
  SkillFilePatchIn,
  SkillPatchIn,
  SkillScopeRef,
} from '../types'

// Serialize the scope into the cache key so each scope caches independently. Views
// remount on scope change via `:key` (prompt-studio's pattern), so a plain scope is a
// stable key input; skill/file ids that change within one mount are reactive instead.
function scopeKey(scope: SkillScopeRef): string {
  switch (scope.kind) {
    case 'agent':
      return `agent:${scope.agentId}`
    case 'project':
      return `project:${scope.projectId}`
    case 'org':
      return `org:${scope.orgId}`
    case 'platform':
      return 'platform'
  }
}

export const skillsKeys = {
  list: (scope: SkillScopeRef, includeDeleted: boolean) =>
    ['skills', 'list', scopeKey(scope), includeDeleted] as const,
  listRoot: (scope: SkillScopeRef) => ['skills', 'list', scopeKey(scope)] as const,
  detail: (scope: SkillScopeRef, skillId: string) =>
    ['skills', 'detail', scopeKey(scope), skillId] as const,
  files: (scope: SkillScopeRef, skillId: string) =>
    ['skills', 'files', scopeKey(scope), skillId] as const,
  bindings: (agentId: string) => ['skills', 'bindings', agentId] as const,
  metrics: () => ['skills', 'metrics'] as const,
  importJob: (taskId: string) => ['skills', 'import-job', taskId] as const,
  exportJob: (taskId: string) => ['skills', 'export-job', taskId] as const,
}

// --- read hooks ---

export function useSkillsQuery(
  scope: SkillScopeRef,
  includeDeleted: MaybeRefOrGetter<boolean> = false,
) {
  return useQuery({
    queryKey: computed(() => skillsKeys.list(scope, toValue(includeDeleted))),
    queryFn: () => skillsApi.list(scope, { includeDeleted: toValue(includeDeleted), limit: 500 }),
  })
}

export function useSkillQuery(scope: SkillScopeRef, skillId: MaybeRefOrGetter<string | null>) {
  return useQuery({
    queryKey: computed(() => skillsKeys.detail(scope, toValue(skillId) ?? '')),
    enabled: computed(() => !!toValue(skillId)),
    queryFn: () => skillsApi.get(scope, toValue(skillId) as string),
  })
}

export function useSkillFilesQuery(scope: SkillScopeRef, skillId: MaybeRefOrGetter<string | null>) {
  return useQuery({
    queryKey: computed(() => skillsKeys.files(scope, toValue(skillId) ?? '')),
    enabled: computed(() => !!toValue(skillId)),
    queryFn: () => skillsApi.listFiles(scope, toValue(skillId) as string),
  })
}

export function useBindingsQuery(agentId: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => skillsKeys.bindings(toValue(agentId))),
    enabled: computed(() => !!toValue(agentId)),
    queryFn: () => skillsApi.listBindings(toValue(agentId)),
  })
}

export function useMetricsQuery() {
  return useQuery({ queryKey: skillsKeys.metrics(), queryFn: () => skillsApi.metrics() })
}

// Poll a bundle import/export job until it reaches a terminal state. The interval stops
// once `ready`/`failed` so a finished job is not re-polled forever.
export function useImportStatusQuery(taskId: MaybeRefOrGetter<string | null>) {
  return useQuery({
    queryKey: computed(() => skillsKeys.importJob(toValue(taskId) ?? '')),
    enabled: computed(() => !!toValue(taskId)),
    queryFn: () => skillsApi.importStatus(toValue(taskId) as string),
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s === 'ready' || s === 'failed' ? false : 1500
    },
  })
}

export function useExportStatusQuery(taskId: MaybeRefOrGetter<string | null>) {
  return useQuery({
    queryKey: computed(() => skillsKeys.exportJob(toValue(taskId) ?? '')),
    enabled: computed(() => !!toValue(taskId)),
    queryFn: () => skillsApi.exportStatus(toValue(taskId) as string),
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s === 'ready' || s === 'failed' ? false : 1500
    },
  })
}

// --- mutation hooks (invalidate the matching scope on success) ---

export function useCreateSkillMutation(scope: SkillScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: SkillCreateIn) => skillsApi.create(scope, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: skillsKeys.listRoot(scope) }),
  })
}

export function usePatchSkillMutation(scope: SkillScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { skillId: string; version: number | null; body: SkillPatchIn }) =>
      skillsApi.patch(scope, vars.skillId, vars.version, vars.body),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: skillsKeys.listRoot(scope) })
      qc.invalidateQueries({ queryKey: skillsKeys.detail(scope, vars.skillId) })
    },
  })
}

export function useDeleteSkillMutation(scope: SkillScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { skillId: string; version: number | null }) =>
      skillsApi.remove(scope, vars.skillId, vars.version),
    onSuccess: () => qc.invalidateQueries({ queryKey: skillsKeys.listRoot(scope) }),
  })
}

export function useRestoreSkillMutation(scope: SkillScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (skillId: string) => skillsApi.restore(scope, skillId),
    onSuccess: () => qc.invalidateQueries({ queryKey: skillsKeys.listRoot(scope) }),
  })
}

export function useCopySkillMutation(scope: SkillScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { skillId: string; body: SkillCopyIn }) =>
      skillsApi.copy(scope, vars.skillId, vars.body),
    // The copy lands in the *target* scope, which this hook does not key on; the target
    // scope's own list is invalidated when its view mounts/refetches, so only the source
    // list (unchanged) needs no invalidation. Kept explicit for the reader.
    onSuccess: () => qc.invalidateQueries({ queryKey: ['skills', 'list'] }),
  })
}

export function useCreateFileMutation(scope: SkillScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { skillId: string; body: SkillFileCreateIn }) =>
      skillsApi.createFile(scope, vars.skillId, vars.body),
    onSuccess: (_data, vars) => qc.invalidateQueries({ queryKey: skillsKeys.files(scope, vars.skillId) }),
  })
}

export function useUploadFileMutation(scope: SkillScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { skillId: string; path: string; file: File }) =>
      skillsApi.uploadFile(scope, vars.skillId, vars.path, vars.file),
    onSuccess: (_data, vars) => qc.invalidateQueries({ queryKey: skillsKeys.files(scope, vars.skillId) }),
  })
}

export function usePatchFileMutation(scope: SkillScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { skillId: string; fileId: string; body: SkillFilePatchIn }) =>
      skillsApi.patchFile(scope, vars.skillId, vars.fileId, vars.body),
    onSuccess: (_data, vars) => qc.invalidateQueries({ queryKey: skillsKeys.files(scope, vars.skillId) }),
  })
}

export function useDeleteFileMutation(scope: SkillScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { skillId: string; fileId: string }) =>
      skillsApi.deleteFile(scope, vars.skillId, vars.fileId),
    onSuccess: (_data, vars) => qc.invalidateQueries({ queryKey: skillsKeys.files(scope, vars.skillId) }),
  })
}

export function useImportBundleMutation(scope: SkillScopeRef) {
  return useMutation({ mutationFn: (file: File) => skillsApi.importBundle(scope, file) })
}

export function useExportBundleMutation(scope: SkillScopeRef) {
  return useMutation({ mutationFn: (skillId: string) => skillsApi.exportBundle(scope, skillId) })
}

// --- binding mutations (agent-scoped) ---

export function useBindSkillMutation(agentId: MaybeRefOrGetter<string>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (skillId: string) => skillsApi.bind(toValue(agentId), skillId),
    onSuccess: () => qc.invalidateQueries({ queryKey: skillsKeys.bindings(toValue(agentId)) }),
  })
}

export function useUnbindSkillMutation(agentId: MaybeRefOrGetter<string>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (skillId: string) => skillsApi.unbind(toValue(agentId), skillId),
    onSuccess: () => qc.invalidateQueries({ queryKey: skillsKeys.bindings(toValue(agentId)) }),
  })
}
