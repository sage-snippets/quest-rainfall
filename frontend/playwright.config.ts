import { defineConfig } from '@playwright/test'

// Runs against a live stack: backend on :8000 with an ingested run, frontend on BASE_URL
// (Vite dev server by default, or the deployed site). Uses the system's Microsoft Edge.
export default defineConfig({
  testDir: 'e2e',
  timeout: 60_000,
  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:5173',
    // Locally: the system's Microsoft Edge. CI sets PW_CHANNEL='' to use Playwright's Chromium.
    channel: process.env.PW_CHANNEL === undefined ? 'msedge' : process.env.PW_CHANNEL || undefined,
    viewport: { width: 1440, height: 900 },
    launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] },
  },
  reporter: [['list']],
})
