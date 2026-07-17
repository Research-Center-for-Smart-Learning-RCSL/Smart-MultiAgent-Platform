<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'
import { SBadge, SButton, SCard, SLoadingSpinner } from '@shared/ui'

import {
  useBindSkillMutation,
  useBindingsQuery,
  useSkillsQuery,
  useUnbindSkillMutation,
} from '../queries'
import type { SkillScopeRef, SkillSummaryOut } from '../types'
import SkillWorkbench from './SkillWorkbench.vue'

const props = defineProps<{ agentId: string; projectId: string }>()

const { t } = useI18n()
const toast = useToast()

const agentScope = computed<SkillScopeRef>(() => ({ kind: 'agent', agentId: props.agentId }))
const projectScope = computed<SkillScopeRef>(() => ({ kind: 'project', projectId: props.projectId }))

const bindingsQuery = useBindingsQuery(() => props.agentId)
// Candidates the binder can actually browse: this agent's own skills and its project's.
// Org and platform skills bind by the same endpoint but have no member-reachable listing,
// so they are bound by their managers, not offered here.
const agentSkillsQuery = useSkillsQuery(agentScope.value)
const projectSkillsQuery = useSkillsQuery(projectScope.value)

const bindMutation = useBindSkillMutation(() => props.agentId)
const unbindMutation = useUnbindSkillMutation(() => props.agentId)

const bindings = computed(() => bindingsQuery.data.value ?? [])
const boundIds = computed(() => new Set(bindings.value.map((b) => b.skill_id)))

const candidates = computed<SkillSummaryOut[]>(() => {
  const all = [...(agentSkillsQuery.data.value?.items ?? []), ...(projectSkillsQuery.data.value?.items ?? [])]
  return all.filter((s) => !s.deleted_at && !boundIds.value.has(s.id))
})

async function bind(skillId: string): Promise<void> {
  try {
    await bindMutation.mutateAsync(skillId)
    toast.success(t('skills.bindings.bound'))
  } catch (err) {
    if (isProblemWithType(err, 'skills/requires-tool-missing')) {
      toast.error(t('skills.bindings.requiresMissing'))
    } else if (isProblemWithType(err, 'skills/index-budget-exceeded')) {
      toast.error(t('skills.bindings.indexBudget'))
    } else if (isProblemWithType(err, 'skills/name-taken')) {
      toast.error(t('skills.bindings.nameTaken'))
    } else if (isProblemWithType(err, 'skills/not-found')) {
      toast.error(t('skills.bindings.notBindable'))
    } else {
      toast.error(t('skills.bindings.bindFailed'))
    }
  }
}

async function unbind(skillId: string): Promise<void> {
  try {
    await unbindMutation.mutateAsync(skillId)
    toast.success(t('skills.bindings.unbound'))
  } catch {
    toast.error(t('skills.bindings.unbindFailed'))
  }
}

const showAuthoring = ref(false)
</script>

<template>
  <div class="space-y-6">
    <SCard variant="bordered">
      <h3 class="mb-1 text-lg font-semibold text-[var(--color-fg)]">
        {{ t('skills.bindings.heading') }}
      </h3>
      <p class="mb-4 text-sm text-[var(--color-muted)]">
        {{ t('skills.bindings.subtitle') }}
      </p>

      <div
        v-if="bindingsQuery.isLoading.value"
        class="flex justify-center p-4"
      >
        <SLoadingSpinner size="sm" />
      </div>
      <template v-else>
        <!-- Bound -->
        <h4 class="mb-2 text-sm font-semibold text-[var(--color-fg)]">
          {{ t('skills.bindings.boundHeading') }}
        </h4>
        <p
          v-if="!bindings.length"
          class="mb-4 text-sm text-[var(--color-muted)]"
        >
          {{ t('skills.bindings.noneBound') }}
        </p>
        <ul
          v-else
          class="mb-4 divide-y divide-[var(--color-border)] rounded-md border border-[var(--color-border)]"
        >
          <li
            v-for="b in bindings"
            :key="b.skill_id"
            class="flex items-center gap-2 px-3 py-2 text-sm"
          >
            <span class="flex-1 truncate font-mono">{{ b.name }}</span>
            <SButton
              variant="ghost"
              size="sm"
              :loading="unbindMutation.isPending.value"
              @click="unbind(b.skill_id)"
            >
              {{ t('skills.bindings.unbind') }}
            </SButton>
          </li>
        </ul>

        <!-- Available -->
        <h4 class="mb-2 text-sm font-semibold text-[var(--color-fg)]">
          {{ t('skills.bindings.availableHeading') }}
        </h4>
        <p
          v-if="!candidates.length"
          class="text-sm text-[var(--color-muted)]"
        >
          {{ t('skills.bindings.noneAvailable') }}
        </p>
        <ul
          v-else
          class="divide-y divide-[var(--color-border)] rounded-md border border-[var(--color-border)]"
        >
          <li
            v-for="s in candidates"
            :key="s.id"
            class="flex items-center gap-2 px-3 py-2 text-sm"
          >
            <span class="min-w-0 flex-1">
              <span class="flex items-center gap-2">
                <span class="truncate font-mono">{{ s.name }}</span>
                <SBadge
                  variant="info"
                  size="sm"
                >{{ t(`skills.scope.${s.scope}`) }}</SBadge>
              </span>
              <span class="block truncate text-xs text-[var(--color-muted)]">{{ s.description }}</span>
            </span>
            <SButton
              variant="secondary"
              size="sm"
              :loading="bindMutation.isPending.value"
              @click="bind(s.id)"
            >
              {{ t('skills.bindings.bind') }}
            </SButton>
          </li>
        </ul>
      </template>
    </SCard>

    <!-- Agent-private skill authoring (deferred until opened) -->
    <SCard variant="bordered">
      <button
        type="button"
        class="flex w-full items-center justify-between text-left"
        @click="showAuthoring = !showAuthoring"
      >
        <span class="text-lg font-semibold text-[var(--color-fg)]">
          {{ t('skills.bindings.authoringHeading') }}
        </span>
        <span class="text-sm text-[var(--color-muted)]">
          {{ showAuthoring ? t('skills.actions.collapse') : t('skills.actions.expand') }}
        </span>
      </button>
      <p class="mt-1 text-sm text-[var(--color-muted)]">
        {{ t('skills.bindings.authoringSubtitle') }}
      </p>
      <div
        v-if="showAuthoring"
        class="mt-4"
      >
        <SkillWorkbench :scope="agentScope" />
      </div>
    </SCard>
  </div>
</template>
