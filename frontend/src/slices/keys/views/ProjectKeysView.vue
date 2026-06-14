<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { useMyKeys } from '../composables/useMyKeys'
import { useProjectKeys } from '../composables/useProjectKeys'
import { projectKeysApi, type KeyUsage, type UsageWindow } from '../api/project-keys'
import CapabilityChip from '../components/CapabilityChip.vue'

const { t } = useI18n()
const route = useRoute()
const projectId = computed(() => route.params.projectId as string)

const { keys: myKeys, reload: reloadMine } = useMyKeys()
const { carried, error, reload, carry, withdraw } = useProjectKeys(
  () => projectId.value,
)

const carriable = computed(() =>
  myKeys.value.filter((m) => !carried.value.some((c) => c.id === m.id)),
)

// Per-key usage panel (R7.05): a window selector + the aggregate counts. One map
// owns both the selected window and its loaded result so they can't drift; the
// counts are loaded on demand and cleared when the window changes (so stale
// numbers are never shown against a different window).
const WINDOWS: UsageWindow[] = ['1h', '24h', '7d', '30d']
interface UsageState {
  window: UsageWindow
  usage?: KeyUsage
}
const usageState = ref<Record<string, UsageState>>({})

function windowOf(keyId: string): UsageWindow {
  return usageState.value[keyId]?.window ?? '1h'
}

function onWindowChange(keyId: string, ev: Event): void {
  const w = (ev.target as HTMLSelectElement).value as UsageWindow
  // Drop any previously-loaded counts so they aren't shown against the new window.
  usageState.value = { ...usageState.value, [keyId]: { window: w } }
}

async function loadUsage(keyId: string): Promise<void> {
  const w = windowOf(keyId)
  try {
    const { data } = await projectKeysApi.usage(projectId.value, keyId, w)
    usageState.value = { ...usageState.value, [keyId]: { window: w, usage: data } }
  } catch {
    ElMessage.error(t('keys.project.usageError'))
  }
}

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
          <span class="usage-controls">
            <select
              :value="windowOf(k.id)"
              :aria-label="$t('keys.project.usageWindow')"
              @change="onWindowChange(k.id, $event)"
            >
              <option
                v-for="w in WINDOWS"
                :key="w"
                :value="w"
              >
                {{ w }}
              </option>
            </select>
            <button
              data-testid="usage"
              @click="loadUsage(k.id)"
            >
              {{ $t('keys.project.usage') }}
            </button>
          </span>
          <span
            v-if="usageState[k.id]?.usage"
            class="usage"
          >
            {{ $t('keys.project.usageReq') }}: {{ usageState[k.id]!.usage!.requests }} ·
            {{ $t('keys.project.usageIn') }}: {{ usageState[k.id]!.usage!.input_tokens }} ·
            {{ $t('keys.project.usageOut') }}: {{ usageState[k.id]!.usage!.output_tokens }} ·
            {{ $t('keys.project.usageErr') }}: {{ usageState[k.id]!.usage!.errors }}
          </span>
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
