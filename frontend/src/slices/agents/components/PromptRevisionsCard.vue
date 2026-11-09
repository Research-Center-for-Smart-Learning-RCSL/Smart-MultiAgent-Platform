<script setup lang="ts">
// [R34.07]: every change of the system prompt, newest first, with restore. A
// restore is itself a revision, so it can be undone the same way.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { ClockIcon } from '@heroicons/vue/24/outline'
import { SBadge, SButton, SCard, SEmptyState, SQueryError, SRelativeTime, SSkeleton } from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'
import type { PromptRevisionOut } from '@shared/api-client'
import { agentsApi } from '../api'
import { agentKeys } from '../queries'

const props = defineProps<{
  agentId: string
  version: number
  currentPrompt: string
  // Unsaved edits in the prompt editor, which a restore replaces.
  dirty: boolean
}>()

const { t } = useI18n()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()

const query = useQuery({
  queryKey: computed(() => agentKeys.promptRevisions(props.agentId)),
  queryFn: () => agentsApi.listPromptRevisions(props.agentId),
})
const revisions = computed<PromptRevisionOut[]>(() => query.data.value ?? [])

const sourceVariant = (source: PromptRevisionOut['source']): 'info' | 'success' | 'warning' | 'neutral' =>
  ({ initial: 'neutral', manual: 'info', design: 'success', restore: 'warning' } as const)[source]

const restoreMutation = useMutation({
  mutationFn: (revisionId: string) => agentsApi.restorePromptRevision(props.agentId, revisionId, props.version),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.agent(props.agentId) })
    qc.invalidateQueries({ queryKey: agentKeys.promptRevisions(props.agentId) })
    toast.success(t('agents.revisions.restored'))
  },
  onError: (err) => {
    if (isProblemWithType(err, '/agents/version-mismatch')) {
      toast.error(t('agents.revisions.conflict'))
      qc.invalidateQueries({ queryKey: agentKeys.agent(props.agentId) })
      return
    }
    toast.error(t('agents.revisions.restoreFailed'))
  },
})

async function onRestore(revision: PromptRevisionOut): Promise<void> {
  const ok = await confirm({
    title: t('agents.revisions.confirmTitle'),
    message: props.dirty ? t('agents.revisions.confirmDirty') : t('agents.revisions.confirmMessage'),
    confirmLabel: t('agents.revisions.restore'),
    variant: 'warning',
  })
  if (ok) restoreMutation.mutate(revision.id)
}
</script>

<template>
  <SCard class="p-4">
    <h3 class="mb-1 text-base font-semibold text-[var(--color-fg)]">
      {{ t('agents.revisions.title') }}
    </h3>
    <p class="mb-3 text-sm text-[var(--color-muted)]">
      {{ t('agents.revisions.description') }}
    </p>
    <SSkeleton
      v-if="query.isLoading.value"
      class="h-16"
    />
    <SQueryError
      v-else-if="query.error.value"
      :message="query.error.value.message"
      :retry-label="t('agents.revisions.retry')"
      @retry="query.refetch()"
    />
    <SEmptyState
      v-else-if="!revisions.length"
      :icon="ClockIcon"
      :text="t('agents.revisions.empty')"
    />
    <ul
      v-else
      class="divide-y divide-[var(--color-border-subtle)]"
    >
      <li
        v-for="revision in revisions"
        :key="revision.id"
        class="py-2 text-sm"
        data-testid="prompt-revision"
      >
        <div class="flex flex-wrap items-center gap-2">
          <SBadge
            :variant="sourceVariant(revision.source)"
            size="sm"
          >
            {{ t(`agents.revisions.source.${revision.source}`) }}
          </SBadge>
          <span class="text-xs text-[var(--color-muted)]">
            {{ t('agents.revisions.version', { version: revision.agent_version }) }}
          </span>
          <SRelativeTime
            class="text-xs text-[var(--color-muted)]"
            :value="revision.created_at"
          />
          <span
            v-if="revision.system_prompt === currentPrompt"
            class="text-xs text-[var(--color-muted)]"
            data-testid="prompt-revision-current"
          >
            {{ t('agents.revisions.current') }}
          </span>
          <SButton
            v-else
            class="ml-auto"
            size="sm"
            variant="secondary"
            type="button"
            :loading="restoreMutation.isPending.value && restoreMutation.variables.value === revision.id"
            :disabled="restoreMutation.isPending.value"
            data-testid="prompt-revision-restore"
            @click="onRestore(revision)"
          >
            {{ t('agents.revisions.restore') }}
          </SButton>
        </div>
        <details class="mt-1">
          <summary class="cursor-pointer text-xs text-[var(--color-accent)]">
            {{ t('agents.revisions.show') }}
          </summary>
          <pre
            class="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-[var(--color-surface-hover)] p-2 text-xs text-[var(--color-fg)] [overflow-wrap:anywhere]"
          >{{ revision.system_prompt }}</pre>
        </details>
      </li>
    </ul>
  </SCard>
</template>
