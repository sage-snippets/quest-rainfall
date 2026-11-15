# QUEST Rainfall frontend

React 19 + TypeScript + Vite + Tailwind v4 + TanStack Query + MapLibre GL JS 6.
See the [root README](../README.md) for the full project documentation.

```bash
npm ci
npm run dev        # http://localhost:5173; proxies /api to the backend on :8000
npm run typecheck  # tsc -b
npm run lint       # oxlint
npm test           # Vitest unit tests (src/**/*.test.ts)
npm run e2e        # Playwright e2e against BASE_URL (default http://localhost:5173)
npm run build      # production build in dist/ (served by FastAPI in production)
```

Notes:
- All API calls use relative `/api/...` URLs. In production, FastAPI serves `dist/` from the same origin.
- MapLibre 6 works out its worker URL at runtime, so `MapView.tsx` imports
  `maplibre-gl-worker.mjs?url` and calls `setWorkerUrl()`. Without this, the production build ships no worker.
- `e2e/` uses the system Microsoft Edge locally. Set `PW_CHANNEL=''` to use Playwright's bundled Chromium, as CI does.
- The legend is rendered from `/api/colorscale`, the same table the tile renderer uses.
