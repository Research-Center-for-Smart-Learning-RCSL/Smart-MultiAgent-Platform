<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useMyKeys } from '../composables/useMyKeys'
import { useProjectKeys } from '../composables/useProjectKeys'
import CapabilityChip from '../components/CapabilityChip.vue'

const route = useRoute()
const projectId = computed(() => route.params.projectId as string)

const { keys: myKeys, reload: reloadMine } = useMyKeys()
const { carried, error, reload, carry, withdraw } = useProjectKeys(
  () => projectId.value,
)

const carriable = computed(() =>
  myKeys.value.filter((m) => !carried.value.some((c) => c.id === m.id)),
)

onMounted(async () => {
  await Promise.all([reloadMine(), reload()])
})
watch(projectId, async () => {
  await Promise.all([reloadMine(), reload()])
})
</script>

<template>
  <main class="project-keys-view">
    <h1>{{ $t('keys.project.title') }}</h1>
    <p
      v-if="error"
      class="error"
    >
      {{ error }}
    </p>

    <section>
      <h2>{{ $t('keys.project.carried') }}</h2>
      <ul data-testid="carried-list">
        <li
          v-for="k in carried"
          :key="k.id"
        >
          <CapabilityChip :provider="k.provider" />
          {{ k.name }}
          <code>{{ k.masked_preview }}</code>
          <button
            data-testid="withdraw"
            @click="withdraw(k.id)"
          >
            {{ $t('keys.project.withdraw') }}
          </button>
        </li>
        <li
          v-if="carried.length === 0"
          class="empty"
        >
          {{ $t('keys.project.noneCarried') }}
        </li>
      </ul>
    </section>

    <section>
      <h2>{{ $t('keys.project.carryNew') }}</h2>
      <ul data-testid="carriable-list">
        <li
          v-for="k in carriable"
          :key="k.id"
        >
          <CapabilityChip :provider="k.provider" />
          {{ k.name }}
          <button
            data-testid="carry"
            @click="carry(k.id)"
          >
            {{ $t('keys.project.carry') }}
          </button>
        </li>
        <li
          v-if="carriable.length === 0"
          class="empty"
        >
          {{ $t('keys.project.noneCarriable') }}
        </li>
      </ul>
    </section>
  </main>
</template>
