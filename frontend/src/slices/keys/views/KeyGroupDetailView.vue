<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useInlineRename, useToast } from '@shared/composables'
import { useKeyGroupDetail } from '../composables/useKeyGroups'
import { useProjectKeys } from '../composables/useProjectKeys'
import { keyGroupsApi } from '../api/key-groups'

const { t } = useI18n()
const toast = useToast()
const route = useRoute()
const projectId = computed(() => route.params.projectId as string)
const groupId = computed(() => route.params.id as string)

const { detail, error, reload, addMember, removeMember, patchMember, reorder } =
  useKeyGroupDetail(() => groupId.value)
const { carried, reload: reloadCarried } = useProjectKeys(() => projectId.value)

const selectedKeyId = ref<string>('')

// Key-group rename takes no version (no If-Match), so refresh the detail after
// the PATCH; the reload is best-effort and must not mask a successful rename.
const rename = useInlineRename({
  current: () => detail.value?.group.name ?? '',
  save: async (name) => {
    try {
      await keyGroupsApi.rename(groupId.value, name)
    } catch (e) {
      toast.error(t('keys.groups.renameError'))
      throw e
    }
    void reload().catch(() => {})
  },
})

async function onAdd() {
  if (!selectedKeyId.value) return
  await addMember(selectedKeyId.value)
  selectedKeyId.value = ''
}

// Native HTML5 drag — `vuedraggable` isn't installed; dataTransfer carries
// the dragged `key_id`; drop target computes new priorities and submits.
let dragSource: string | null = null

function onDragStart(keyId: string) {
  dragSource = keyId
}
function onDrop(targetKeyId: string) {
  const src = dragSource
  dragSource = null
  if (!src || src === targetKeyId || !detail.value) return
  const order = detail.value.members.map((m) => m.key_id)
  const from = order.indexOf(src)
  const to = order.indexOf(targetKeyId)
  if (from < 0 || to < 0) return
  order.splice(to, 0, ...order.splice(from, 1))
  // Rebuild priorities as 1..N to keep the UNIQUE(group_id, priority) index happy.
  const priorities: Record<string, number> = {}
  order.forEach((kid, i) => {
    priorities[kid] = i + 1
  })
  reorder(priorities)
}

function maskedFor(keyId: string): string {
  return carried.value.find((k) => k.id === keyId)?.masked_preview ?? keyId
}

onMounted(async () => {
  await Promise.all([reload(), reloadCarried()])
})
watch([groupId, projectId], async () => {
  await Promise.all([reload(), reloadCarried()])
})
</script>

<template>
  <main class="key-group-detail-view">
    <h1 v-if="!rename.renaming.value">
      {{ detail?.group.name ?? '—' }}
      <button
        v-if="detail"
        @click="rename.start"
      >
        {{ $t('keys.groups.rename') }}
      </button>
    </h1>
    <form
      v-else
      @submit.prevent="rename.save"
    >
      <label>
        {{ $t('keys.groups.renameLabel') }}
        <input
          v-model="rename.nameDraft.value"
          required
        >
      </label>
      <button type="submit">
        {{ $t('app.save') }}
      </button>
      <button
        type="button"
        @click="rename.cancel"
      >
        {{ $t('app.cancel') }}
      </button>
    </form>
    <p
      v-if="error"
      class="error"
    >
      {{ error }}
    </p>

    <section v-if="detail">
      <h2>{{ $t('keys.groups.members') }}</h2>
      <ul
        data-testid="member-list"
        role="listbox"
        :aria-label="$t('keys.groups.members')"
      >
        <li
          v-for="m in detail.members"
          :key="m.key_id"
          draggable="true"
          role="option"
          tabindex="0"
          aria-selected="false"
          :data-testid="`member-${m.key_id}`"
          @dragstart="onDragStart(m.key_id)"
          @dragover.prevent
          @drop="onDrop(m.key_id)"
        >
          <span class="priority">#{{ m.priority }}</span>
          <code>{{ maskedFor(m.key_id) }}</code>
          <button
            data-testid="member-remove"
            @click="removeMember(m.key_id)"
          >
            {{ $t('keys.groups.remove') }}
          </button>
          <details>
            <summary>{{ $t('keys.groups.limits') }}</summary>
            <label>
              {{ $t('keys.groups.maxInputPerHour') }}
              <input
                type="number"
                :value="m.limits.max_input_tokens_per_hour ?? ''"
                @change="
                  patchMember(m.key_id, {
                    max_input_tokens_per_hour: Number(($event.target as HTMLInputElement).value) || null,
                  })
                "
              >
            </label>
            <label>
              {{ $t('keys.groups.maxOutputPerHour') }}
              <input
                type="number"
                :value="m.limits.max_output_tokens_per_hour ?? ''"
                @change="
                  patchMember(m.key_id, {
                    max_output_tokens_per_hour: Number(($event.target as HTMLInputElement).value) || null,
                  })
                "
              >
            </label>
            <label>
              {{ $t('keys.groups.maxReqPerHour') }}
              <input
                type="number"
                :value="m.limits.max_requests_per_hour ?? ''"
                @change="
                  patchMember(m.key_id, {
                    max_requests_per_hour: Number(($event.target as HTMLInputElement).value) || null,
                  })
                "
              >
            </label>
          </details>
        </li>
        <li
          v-if="detail.members.length === 0"
          class="empty"
        >
          {{ $t('keys.groups.noMembers') }}
        </li>
      </ul>

      <form @submit.prevent="onAdd">
        <select
          v-model="selectedKeyId"
          data-testid="add-member-select"
          :aria-label="$t('keys.groups.selectKey')"
        >
          <option
            value=""
            disabled
          >
            {{ $t('keys.groups.selectKey') }}
          </option>
          <option
            v-for="k in carried"
            :key="k.id"
            :value="k.id"
            :disabled="detail.members.some((m) => m.key_id === k.id)"
          >
            {{ k.name }} ({{ k.provider }})
          </option>
        </select>
        <button
          type="submit"
          data-testid="add-member"
        >
          {{ $t('keys.groups.add') }}
        </button>
      </form>
    </section>
  </main>
</template>
