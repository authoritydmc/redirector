import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev proxy: the v3 API on :8123 (see backend/README run instructions).
// `base /` matches production serving (nginx serves the SPA shell at /
// and client routes, with /{pattern} resolving on the API hot path).
// Dev opens at http://localhost:5173/.
export default defineConfig({
  base: '/',
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
