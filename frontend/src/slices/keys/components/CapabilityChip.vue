<script setup lang="ts">
import { computed } from 'vue'
import { CAPABILITIES, type ApiKeyProvider } from '../api/keys'

const props = defineProps<{ provider: ApiKeyProvider }>()
const caps = computed(() => CAPABILITIES[props.provider])
</script>

<template>
  <span class="capability-chip">
    <span class="provider">{{ provider }}</span>
    <span v-for="c in caps" :key="c" :class="['cap', c]">{{ c }}</span>
  </span>
</template>
