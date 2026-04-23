import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
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
      '/api': { target: 'http://localhost:8000', changeOrigin: false },
      '/ws': { target: 'ws://localhost:8000', ws: true, changeOrigin: false },
    },
  },
  build: {
    sourcemap: false,
    rollupOptions: {
      output: {
        // Initial bundle ≤250 KB gzip; per-view ≤200 KB gzip (R24.48).
        // Hot-split heavy libs so they never land in the initial chunk.
        manualChunks: (id) => {
          if (id.includes('@vue-flow/core')) return 'vue-flow'
          if (id.includes('mermaid')) return 'mermaid'
          if (id.includes('katex')) return 'katex'
          if (id.includes('highlight.js')) return 'hljs'
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    coverage: {
      reporter: ['text', 'lcov'],
    },
  },
})
