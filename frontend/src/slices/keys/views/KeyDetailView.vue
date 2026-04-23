<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useMyKeys } from '../composables/useMyKeys'
import CapabilityChip from '../components/CapabilityChip.vue'

const route = useRoute()
const keyId = computed(() => route.params.id as string)
const { keys, reload, retest, remove } = useMyKeys()

const current = computed(() => keys.value.find((k) => k.id === keyId.value))
onMounted(reload)
</script>

<template>
  <main class="key-detail-view">
    <h1>{{ $t('keys.detail.title') }}</h1>
    <p v-if="!current">{{ $t('keys.detail.notFound') }}</p>
    <section v-else>
      <dl>
        <dt>{{ $t('keys.detail.provider') }}</dt>
        <dd><CapabilityChip :provider="current.provider" /></dd>
        <dt>{{ $t('keys.detail.name') }}</dt>
        <dd>{{ current.name }}</dd>
        <dt>{{ $t('keys.detail.preview') }}</dt>
        <dd><code>{{ current.masked_preview }}</code></dd>
        <dt>{{ $t('keys.detail.status') }}</dt>
        <dd :class="`status-${current.test_status}`">{{ current.test_status }}</dd>
        <dt>{{ $t('keys.detail.lastTest') }}</dt>
        <dd>{{ current.last_test_at ?? '—' }}</dd>
      </dl>
      <button @click="retest(current.id)">{{ $t('keys.detail.retest') }}</button>
      <button @click="remove(current.id)">{{ $t('keys.detail.delete') }}</button>
    </section>
  </main>
</template>
