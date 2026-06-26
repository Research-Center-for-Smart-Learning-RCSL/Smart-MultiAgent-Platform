<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { LanguageIcon } from '@heroicons/vue/24/outline'
import { useLocale } from '@shared/composables/useLocale'

const { t } = useI18n()
const { locale, cycleLocale } = useLocale()

const labels: Record<string, string> = { en: 'EN', 'zh-TW': 'TW' }
</script>

<template>
  <button
    type="button"
    class="locale-toggle"
    :aria-label="t('app.localeLabel', { locale })"
    @click="cycleLocale"
  >
    <LanguageIcon
      class="locale-toggle__icon"
      aria-hidden="true"
    />
    <span class="locale-toggle__label">{{ labels[locale] ?? locale }}</span>
  </button>
</template>

<style scoped>
.locale-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 2px;
  height: 36px;
  padding: 0 8px;
  border-radius: var(--radius-md);
  border: none;
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
  transition: color var(--transition-fast), background var(--transition-fast);
}

.locale-toggle:hover {
  background: var(--color-surface);
  color: var(--color-fg);
}

.locale-toggle:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}

.locale-toggle__icon {
  width: 20px;
  height: 20px;
}

.locale-toggle__label {
  font-size: 0.6875rem;
  font-weight: 600;
  line-height: 1;
}
</style>
