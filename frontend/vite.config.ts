import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const api = { '/api': 'http://127.0.0.1:8000' }

// In development the FastAPI backend runs on :8000; in production it serves this build
// from the same origin, so the app always calls relative /api URLs (no CORS needed).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: api },
  preview: { proxy: api },
  // Unit tests live next to the code; e2e/*.spec.ts belong to Playwright (npm run e2e).
  test: { include: ['src/**/*.test.ts'] },
})
