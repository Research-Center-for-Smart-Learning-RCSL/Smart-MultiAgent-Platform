<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { SAuthCard, SLoadingSpinner } from '@shared/ui'
import { safeRedirect } from '@shared/composables'
import { useSessionStore } from '../stores/session'

const router = useRouter()
const session = useSessionStore()

// The backend Google callback already minted the session and set the smap_refresh
// cookie, then 302'd here. Reuse the boot-time hydrate() (silent refresh from that
// cookie) — identical downstream lifecycle to a password login — then redirect on.
onMounted(async () => {
  await session.hydrate()
  if (session.isAuthenticated) {
    router.replace(safeRedirect(''))
  } else {
    router.replace({ name: 'identity.login', query: { oauth_error: 'oauth-failed' } })
  }
})
</script>

<template>
  <SAuthCard>
    <div
      class="complete-state"
      role="status"
    >
      <SLoadingSpinner />
      <p>{{ $t('identity.googleComplete.signingIn') }}</p>
    </div>
  </SAuthCard>
</template>

<style scoped>
.complete-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-6) 0;
  color: var(--color-fg);
}
</style>
