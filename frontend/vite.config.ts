import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev proxy: the v3 API on :8123 (see backend/README run instructions).
// EPIC-08 dual-serve will point this at the nginx front door instead.
// `base /app/` matches production serving (FastAPI StaticFiles at /app,
// nginx /app/ block): dev opens at http://localhost:5173/app/.
export default defineConfig({
  base: '/app/',
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
