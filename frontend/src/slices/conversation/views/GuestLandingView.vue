<template>
  <section class="guest-landing">
    <p v-if="state === 'enrolling'">
      {{ $t('conversation.guest.enrolling') }}
    </p>
    <p v-if="state === 'done'">
      {{ $t('conversation.guest.done') }}
    </p>
    <p
      v-if="state === 'error'"
      class="error"
    >
      {{ $t('conversation.guest.error') }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { enrollGuest } from '../api'

const route = useRoute()
const router = useRouter()
const state = ref<'enrolling' | 'done' | 'error'>('enrolling')

onMounted(async () => {
  const chatroomId = route.params.chatroomId as string
  const token = route.params.guestToken as string
  try {
    await enrollGuest(chatroomId, token)
    // Strip the token from history (R24.43) before landing on the room.
    history.replaceState(null, '', `/c/${chatroomId}`)
    router.replace({
      name: 'conversation.chatroom',
      params: { chatroomId },
    })
    state.value = 'done'
  } catch {
    state.value = 'error'
  }
})
</script>
