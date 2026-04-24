<script setup lang="ts">
import { computed } from 'vue'
import { useSessionStore } from '@slices/identity'

const props = defineProps<{
  cap: string
  scope?: Record<string, string>
  mode?: 'hidden' | 'disabled'
}>()

const session = useSessionStore()

const allowed = computed(() => {
  if (session.me?.is_admin) return true

  // Capability checks are server-authoritative (R5.05).
  // PermissionGate is a UI convenience that mirrors the server decision.
  // The backend enforces the real check on every request.
  // For v1, admin-only capabilities are gated by is_admin.
  // Future: call a capabilities resolver that mirrors the backend matrix.
  return false
})
</script>

<template>
  <template v-if="props.mode === 'disabled'">
    <div :class="{ 'opacity-50 pointer-events-none': !allowed }">
      <slot />
    </div>
  </template>
  <template v-else>
    <slot v-if="allowed" />
  </template>
</template>
