<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'

import { SPageHeader } from '@shared/ui'

import SkillWorkbench from '../components/SkillWorkbench.vue'
import type { SkillScopeRef } from '../types'

const route = useRoute()
const { t } = useI18n()

const orgId = computed(() => String(route.params.orgId))
const scope = computed<SkillScopeRef>(() => ({ kind: 'org', orgId: orgId.value }))
</script>

<template>
  <div class="mx-auto max-w-6xl p-6">
    <SPageHeader
      :title="t('skills.org.title')"
      :subtitle="t('skills.org.subtitle')"
    />
    <SkillWorkbench
      :key="orgId"
      :scope="scope"
      class="mt-6"
    />
  </div>
</template>
