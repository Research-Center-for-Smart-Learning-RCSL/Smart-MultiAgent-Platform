import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, type MaybeRefOrGetter, toValue } from 'vue'

import { promptStudioApi } from '../api'
import type {
  AssistantConfigPutInput,
  ConfigScopeRef,
  TemplateCreateInput,
  TemplatePatchInput,
} from '../types'

function scopeKey(scope: ConfigScopeRef): string {
  return scope.kind === 'org' ? `org:${scope.orgId}` : scope.kind
}

export const promptStudioKeys = {
  config: (scope: ConfigScopeRef) => ['prompt-studio', 'config', scopeKey(scope)] as const,
  templates: (scope: ConfigScopeRef) => ['prompt-studio', 'templates', scopeKey(scope)] as const,
  resolved: (projectId: string) => ['prompt-studio', 'resolved', projectId] as const,
  projectTemplates: (projectId: string) =>
    ['prompt-studio', 'projectTemplates', projectId] as const,
}

// --- read hooks ---

export function useConfigQuery(scope: ConfigScopeRef) {
  return useQuery({
    queryKey: promptStudioKeys.config(scope),
    queryFn: async () => (await promptStudioApi.getConfig(scope)).data,
  })
}

export function useTemplatesQuery(scope: ConfigScopeRef) {
  return useQuery({
    queryKey: promptStudioKeys.templates(scope),
    queryFn: async () => (await promptStudioApi.listTemplates(scope)).data,
  })
}

export function useResolvedAssistantQuery(projectId: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => promptStudioKeys.resolved(toValue(projectId))),
    enabled: computed(() => !!toValue(projectId)),
    queryFn: async () => (await promptStudioApi.resolvedForProject(toValue(projectId))).data,
  })
}

export function useProjectTemplatesQuery(projectId: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => promptStudioKeys.projectTemplates(toValue(projectId))),
    enabled: computed(() => !!toValue(projectId)),
    queryFn: async () => (await promptStudioApi.mergedTemplates(toValue(projectId))).data,
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

export function useCreateTemplateMutation(scope: ConfigScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: TemplateCreateInput) => promptStudioApi.createTemplate(scope, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: promptStudioKeys.templates(scope) }),
  })
}

export function usePatchTemplateMutation(scope: ConfigScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { id: string; version: number; payload: TemplatePatchInput }) =>
      promptStudioApi.patchTemplate(scope, vars.id, vars.version, vars.payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: promptStudioKeys.templates(scope) }),
  })
}

export function useDeleteTemplateMutation(scope: ConfigScopeRef) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => promptStudioApi.deleteTemplate(scope, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: promptStudioKeys.templates(scope) }),
  })
}
