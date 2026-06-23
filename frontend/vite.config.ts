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
    rollupOptions: {
      output: {
        manualChunks: (id) => {
          if (id.includes('@vue-flow/')) return 'vue-flow'
          if (id.includes('mermaid')) return 'mermaid'
          if (id.includes('katex')) return 'katex'
          if (id.includes('highlight.js')) return 'hljs'
          if (id.includes('node_modules/vue-i18n')) return 'vue-i18n'
          if (id.includes('/locales/') && id.endsWith('.json')) {
            const match = id.match(/(?:slices|app)\/([^/]+)\/locales/)
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
