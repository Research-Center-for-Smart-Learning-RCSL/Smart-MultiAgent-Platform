<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { SAlert, SCard, SPageHeader } from '@shared/ui'

import SkillWorkbench from '../components/SkillWorkbench.vue'
import { useMetricsQuery } from '../queries'
import type { SkillScopeRef } from '../types'

const { t } = useI18n()

const scope: SkillScopeRef = { kind: 'platform' }

// R31.11 / AC-15: the ratio of agent-private to shared skills. If most skills end up
// agent-scoped, Skills has degraded toward the prompt-strategy feature it replaced; this
// panel makes that observable rather than a matter of opinion.
const metricsQuery = useMetricsQuery()
const counts = computed<Record<string, number>>(() => metricsQuery.data.value?.counts ?? {})
const total = computed(() => metricsQuery.data.value?.total ?? 0)

const scopeOrder = ['agent', 'project', 'org', 'platform'] as const
</script>

<template>
  <div>
    <SPageHeader
      :title="t('skills.admin.title')"
      :subtitle="t('skills.admin.subtitle')"
    />

    <SAlert
      v-if="metricsQuery.isError.value"
      variant="warning"
      class="mt-6"
    >
      {{ t('skills.admin.metricsFailed') }}
    </SAlert>
    <template v-else>
      <div class="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <SCard
          v-for="s in scopeOrder"
          :key="s"
          variant="bordered"
        >
          <div class="text-sm text-[var(--color-muted)]">
            {{ t(`skills.scope.${s}`) }}
          </div>
          <div class="text-2xl font-semibold text-[var(--color-fg)]">
            {{ counts[s] ?? 0 }}
          </div>
        </SCard>
      </div>
      <p class="mt-2 text-sm text-[var(--color-muted)]">
        {{ t('skills.admin.total', { total }) }}
      </p>
    </template>

    <SkillWorkbench
      :scope="scope"
      class="mt-6"
    />
  </div>
</template>
