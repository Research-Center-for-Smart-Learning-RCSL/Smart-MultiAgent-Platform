<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import BrandMark from './BrandMark.vue'

withDefaults(defineProps<{ size?: 'lg' | 'md' | 'sm' }>(), { size: 'md' })

const { t } = useI18n()
</script>

<template>
  <span
    class="brand-logo"
    :class="`brand-logo--${size}`"
  >
    <BrandMark class="brand-logo__mark" />
    {{ t('app.title') }}
  </span>
</template>

<style scoped>
.brand-logo {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  color: var(--color-accent);
  letter-spacing: 0.02em;
  user-select: none;
}

.brand-logo--lg {
  font-size: 26px;
}

.brand-logo--md {
  font-size: 22px;
}

.brand-logo--sm {
  font-size: 16px;
}

.brand-logo__mark {
  flex-shrink: 0;
}
</style>
