import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev proxy: the v3 API on :8123 (see backend/README run instructions).
// EPIC-08 dual-serve will point this at the nginx front door instead.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8123',
      '/healthz': 'http://127.0.0.1:8123',
      '/readyz': 'http://127.0.0.1:8123',
    },
  },
  build: {
    outDir: 'dist',
  },
})
