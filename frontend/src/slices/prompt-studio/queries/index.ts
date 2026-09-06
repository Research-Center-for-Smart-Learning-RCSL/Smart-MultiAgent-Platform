import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, type MaybeRefOrGetter, toValue } from 'vue'

import { promptStudioApi } from '../api'
import type {
  AssistantConfigPresetInput,
  AssistantConfigPutInput,
  ConfigScopeRef,
  TemplateCreateInput,
  TemplatePatchInput,
  TemplateScopeRef,
} from '../types'

function scopeKey(scope: ConfigScopeRef | TemplateScopeRef): string {
  return scope.kind === 'org' ? `org:${scope.orgId}` : scope.kind
}

export const promptStudioKeys = {
  config: (scope: ConfigScopeRef) => ['prompt-studio', 'config', scopeKey(scope)] as const,
  templates: (scope: TemplateScopeRef) => ['prompt-studio', 'templates', scopeKey(scope)] as const,
  resolved: (projectId: string) => ['prompt-studio', 'resolved', projectId] as const,
  projectTemplates: (projectId: string) =>
    ['prompt-studio', 'projectTemplates', projectId] as const,
  presets: () => ['prompt-studio', 'presets'] as const,
}

// --- read hooks ---

export function useConfigQuery(scope: ConfigScopeRef) {
  return useQuery({
    queryKey: promptStudioKeys.config(scope),
    queryFn: () => promptStudioApi.getConfig(scope),
  })
}

export function useTemplatesQuery(scope: TemplateScopeRef) {
  return useQuery({
    queryKey: promptStudioKeys.templates(scope),
    queryFn: () => promptStudioApi.listTemplates(scope),
  })
}

export function useResolvedAssistantQuery(projectId: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => promptStudioKeys.resolved(toValue(projectId))),
    enabled: computed(() => !!toValue(projectId)),
    queryFn: () => promptStudioApi.resolvedForProject(toValue(projectId)),
  })
}

export function useProjectTemplatesQuery(projectId: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => promptStudioKeys.projectTemplates(toValue(projectId))),
    enabled: computed(() => !!toValue(projectId)),
    queryFn: () => promptStudioApi.mergedTemplates(toValue(projectId)),
  })
}

// --- mutation hooks (invalidate the matching scope on success) ---

export function useSaveConfigMutation(scope: ConfigScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { version: number | null; payload: AssistantConfigPutInput }) =>
      promptStudioApi.putConfig(scope, vars.version, vars.payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: promptStudioKeys.config(scope) }),
  })
}

export function useUploadFileMutation(scope: ConfigScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => promptStudioApi.uploadFile(scope, file),
    onSuccess: () => qc.invalidateQueries({ queryKey: promptStudioKeys.config(scope) }),
  })
}

export function useDeleteFileMutation(scope: ConfigScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (fileId: string) => promptStudioApi.deleteFile(scope, fileId),
    onSuccess: () => qc.invalidateQueries({ queryKey: promptStudioKeys.config(scope) }),
  })
}

export function useCreateTemplateMutation(scope: TemplateScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: TemplateCreateInput) => promptStudioApi.createTemplate(scope, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: promptStudioKeys.templates(scope) }),
  })
}

export function usePatchTemplateMutation(scope: TemplateScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { id: string; version: number; payload: TemplatePatchInput }) =>
      promptStudioApi.patchTemplate(scope, vars.id, vars.version, vars.payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: promptStudioKeys.templates(scope) }),
  })
}

export function useDeleteTemplateMutation(scope: TemplateScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => promptStudioApi.deleteTemplate(scope, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: promptStudioKeys.templates(scope) }),
  })
}

// --- platform presets (id-addressed) ---

export function usePresetsQuery() {
  return useQuery({
    queryKey: promptStudioKeys.presets(),
    queryFn: () => promptStudioApi.listPresets(),
  })
}

export function useCreatePresetMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: AssistantConfigPresetInput) => promptStudioApi.createPreset(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: promptStudioKeys.presets() }),
  })
}

export function useUpdatePresetMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { id: string; version: number; payload: AssistantConfigPresetInput }) =>
      promptStudioApi.updatePreset(vars.id, vars.version, vars.payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: promptStudioKeys.presets() }),
  })
}

export function useDeletePresetMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => promptStudioApi.deletePreset(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: promptStudioKeys.presets() }),
  })
}

export function useUploadPresetFileMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { id: string; file: File }) => promptStudioApi.uploadPresetFile(vars.id, vars.file),
    onSuccess: () => qc.invalidateQueries({ queryKey: promptStudioKeys.presets() }),
  })
}

export function useDeletePresetFileMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { id: string; fileId: string }) =>
      promptStudioApi.deletePresetFile(vars.id, vars.fileId),
    onSuccess: () => qc.invalidateQueries({ queryKey: promptStudioKeys.presets() }),
  })
}
