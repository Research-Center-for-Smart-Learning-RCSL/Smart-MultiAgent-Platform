<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'

import { SPageHeader } from '@shared/ui'

import SkillWorkbench from '../components/SkillWorkbench.vue'
import type { SkillScopeRef } from '../types'

const route = useRoute()
const { t } = useI18n()

const projectId = computed(() => String(route.params.projectId))
const scope = computed<SkillScopeRef>(() => ({ kind: 'project', projectId: projectId.value }))
</script>

<template>
  <div class="mx-auto max-w-6xl p-6">
    <SPageHeader
      :title="t('skills.project.title')"
      :subtitle="t('skills.project.subtitle')"
    />
    <!-- Remount on project change so the workbench re-seeds its scoped queries (§6). -->
    <SkillWorkbench
      :key="projectId"
      :scope="scope"
      class="mt-6"
    />
  </div>
</template>
