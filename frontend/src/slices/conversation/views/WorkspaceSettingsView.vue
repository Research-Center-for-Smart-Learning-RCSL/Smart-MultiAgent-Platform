<script setup lang="ts">
// Workspace settings (Phase 4α WS3/WS4). Hosts the workspace-owned Concept Map
// panel plus the wide-layer privacy opt-in (R11.10), Project-Owner-gated.
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { SPageHeader, SCard, SToggle, SAlert, SLoadingSpinner } from '@shared/ui'
import { useToast } from '@shared/composables'
import { useProjectRole } from '@slices/tenancy'
import { ConceptMapPanel } from '@slices/agents'
import { getWorkspace, setWorkspaceConceptMapEnabled } from '../api'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const toast = useToast()
const workspaceId = route.params.workspaceId as string

const workspaceKey = computed(() => ['conversation', 'workspace', workspaceId])
const workspaceQuery = useQuery({
  queryKey: workspaceKey,
  queryFn: () => getWorkspace(workspaceId),
})
const workspace = computed(() => workspaceQuery.data.value ?? null)
const projectId = computed(() => workspace.value?.project_id ?? '')

const { isAuthorized, decided } = useProjectRole(() => projectId.value)

// The toggle always renders (never v-if-hidden) but stays disabled until
// authorization resolves true — avoids both a real owner's control flashing
// hidden-then-shown (spec §8) and exposing a live, clickable control to a
// non-owner during the loading window. The read-only note still waits for
// `decided` so it never flashes to a confirmed owner/admin.
const showReadonlyNote = computed(() => !isAuthorized.value && decided.value)

const privacyMutation = useMutation({
  mutationFn: (enabled: boolean) => setWorkspaceConceptMapEnabled(workspaceId, enabled),
  onSuccess: () => qc.invalidateQueries({ queryKey: workspaceKey.value }),
  onError: () => toast.error(t('conversation.conceptMap.privacyToggleFailed')),
})
</script>

<template>
  <main class="p-6">
    <div
      v-if="workspaceQuery.isLoading.value"
      class="flex justify-center py-16"
    >
      <SLoadingSpinner />
    </div>
    <SAlert
      v-else-if="!workspace"
      variant="danger"
      class="mt-4"
    >
      {{ t('conversation.conceptMap.workspaceNotFound') }}
    </SAlert>

    <template v-else>
      <SPageHeader :title="workspace.name" />

      <div class="mt-6 space-y-6 max-w-2xl">
        <!-- Privacy opt-in (wide layer) -->
        <SCard>
          <h2 class="text-lg font-semibold mb-1">
            {{ t('conversation.conceptMap.privacyTitle') }}
          </h2>
          <div class="flex items-center justify-between gap-4 mt-3">
            <div class="min-w-0">
              <p class="font-medium">
                {{ t('conversation.conceptMap.workspacePrivacyLabel') }}
              </p>
              <p class="text-sm text-[var(--color-muted)]">
                {{ t('conversation.conceptMap.workspacePrivacyHelp') }}
              </p>
            </div>
            <SToggle
              :model-value="workspace.concept_map_enabled"
              :disabled="!isAuthorized || privacyMutation.isPending.value"
              @update:model-value="(v: boolean) => privacyMutation.mutate(v)"
            />
          </div>
          <p
            v-if="showReadonlyNote"
            class="text-sm text-[var(--color-muted)] mt-2"
          >
            {{ t('conversation.conceptMap.privacyOwnerOnly') }}
          </p>
        </SCard>

        <!-- Workspace-owned Concept Map (shared panel) -->
        <ConceptMapPanel
          v-if="projectId"
          :project-id="projectId"
          owner-kind="workspace"
          :owner-id="workspaceId"
        />
      </div>
    </template>
  </main>
</template>
