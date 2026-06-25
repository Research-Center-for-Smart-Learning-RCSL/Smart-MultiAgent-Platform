import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [tailwindcss(), vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@app': fileURLToPath(new URL('./src/app', import.meta.url)),
      '@shared': fileURLToPath(new URL('./src/shared', import.meta.url)),
      '@slices': fileURLToPath(new URL('./src/slices', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:28000', changeOrigin: false },
      '/ws': {
        target: 'ws://localhost:28000',
        ws: true,
        changeOrigin: false,
        configure: (proxy) => {
          // Suppress ECONNRESET noise when the backend WS isn't yet ready.
          proxy.on('error', () => { /* intentionally swallowed */ })
        },
      },
    },
  },
  build: {
    sourcemap: 'hidden',
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: (id) => {
          // NOTE: mermaid / katex / highlight.js are intentionally NOT given
          // manual chunks. They are reached only through dynamic import() in the
          // markdown pipeline, which Rollup already splits into their own lazy
          // chunks. Forcing them into named manual chunks made Rollup hoist the
          // side-effectful hljs chunk into a static entry import (loaded on every
          // page). Leaving them automatic keeps them strictly on-demand.
          if (id.includes('@vue-flow/')) return 'vue-flow'
          if (id.includes('node_modules/vue-i18n')) return 'vue-i18n'
          // Stable framework vendor — changes rarely, so a dedicated chunk
          // maximizes long-term browser caching across app deploys. (Only the
          // always-eager Vue ecosystem; feature libs stay in their own chunks.)
          if (
            id.includes('node_modules/@vue/') ||
            id.includes('node_modules/vue/') ||
            id.includes('node_modules/vue-router/') ||
            id.includes('node_modules/pinia/') ||
            id.includes('node_modules/@tanstack/') ||
            id.includes('node_modules/vue-sonner/')
          ) {
            return 'vendor'
          }
          // One chunk per language (all slices' bundles for a language merged),
          // so booting the active language is a single request and the inactive
          // language is never fetched (lazy-loaded via ensureLocaleLoaded).
          if (id.includes('/locales/') && id.endsWith('.json')) {
            const match = id.match(/\/locales\/([^/]+)\.json$/)
            if (match) return `locale-${match[1]}`
          }
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['src/**/*.{test,spec}.ts', 'src/**/__tests__/**/*.{test,spec}.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov', 'json-summary'],
      include: ['src/**/*.ts', 'src/**/*.vue'],
      exclude: [
        'src/shared/api-client/**',
        'src/**/__tests__/**',
        'src/**/*.d.ts',
      ],
      thresholds: {
        lines: 80,
        functions: 80,
        branches: 75,
        statements: 80,
      },
    },
  },
})
