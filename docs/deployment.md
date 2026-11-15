# Deployment

One Docker image serves the FastAPI API and the built React app from the same origin.
At startup it fetches the latest complete ECMWF IFS 00/12z run in the background, then
checks for a newer run every hour (`app/refresh.py`).

## Image

| Stage | Contents |
|---|---|
| `web` (node:24-slim) | `npm ci && npm run build` → `frontend/dist` |
| `runtime` (python:3.13-slim) | Runtime requirements only, without cfgrib, xarray or pandas. The ecCodes native library comes from the `eccodes` wheel. A build-time self-test decodes a real ECMWF GRIB2 `tp` message and fails the build if ecCodes is broken. Runs as a non-root user (uid 1000) with `HEALTHCHECK` on `/api/health`. |

## Runtime behaviour

- **The API starts at once.** `/api/health` reports `data_available` and the ingest state
  (`checking`, `ingesting`, `idle` or `error`, plus `last_error`). Forecast routes return 404
  until real data has been ingested. Nothing is ever synthesised.
- **Run discovery** probes ECMWF for the newest 00z or 12z run whose `tp` index exists at the
  configured horizon. 06z and 18z runs are a different stream that stops at 90 h, so they are
  skipped.
- **Atomic publishing.** A run is written to `<run>.partial` and renamed only when complete. A
  failed refresh keeps serving the last valid run and retries after 1, 5 and then 15 minutes.
- **Retention.** `QR_KEEP_RUNS` keeps the newest N runs and removes the rest.
- **Memory.** Grids are stored as `.npy` files and memory-mapped, and ingest decodes one step
  at a time. Measured locally at a 72 h horizon: ingest took 17 s, the store used 199 MB, and
  peak RSS was about 160 MB.

## Environment variables

See `.env.example`. None are secret. The main ones:

| Variable | Default in the image | Meaning |
|---|---|---|
| `PORT` | 8000 | Listen port (Render injects 10000) |
| `QR_DATA_ROOT` | `/data/store` | Forecast store; mount a volume here for persistence |
| `QR_REFRESH_ENABLED` | `true` | Ingest at startup, then refresh periodically |
| `QR_REFRESH_INTERVAL_S` | 3600 | Seconds between refresh checks |
| `QR_INGEST_MAX_STEP` | 72 | Forecast horizon in hours (IFS publishes to 360) |
| `QR_KEEP_RUNS` | 2 | Runs kept on disk |
| `QR_ARRAY_CACHE_SIZE` / `QR_TILE_CACHE_SIZE` | 48 / 4096 | In-memory caches |
| `QR_CORS_ORIGINS` | localhost dev origins | Only needed if the SPA is served from another origin |

## Free hosting comparison (checked 2026-11-15)

| Option | Docker | Free-tier limits | Persistence | Verdict |
|---|---|---|---|---|
| **Render (free web service)** | Yes, builds the repo's Dockerfile | 512 MB RAM, 0.1 CPU, sleeps after 15 min idle (about 1 min to wake), 750 h/month, 5 GB/month bandwidth, no card | Ephemeral disk | **Recommended.** Fits the memory budget, and re-ingesting at startup replaces a persistent disk. |
| Hugging Face Spaces | Docker SDK | CPU basic: 2 vCPU, 16 GB RAM | Ephemeral; storage is paid | New Docker Spaces reportedly need a paid plan in 2026; ruled out by the no-Pro constraint |
| Koyeb | Yes | One 512 MB, 0.1 vCPU instance; scales to zero | No | Now needs a card |
| Fly.io / Railway | Yes | No permanent free tier (trial credit only) | Paid volumes | Not free |
| Google Cloud Run | Yes | Generous free quota | No (needs GCS) | Needs a billing account and card |
| Oracle Cloud Always Free VM | Any | 4 ARM cores, 24 GB | Yes | Needs a card and VM administration; too much setup for this deadline |

Trade-offs on Render free: the first visit after 15 minutes idle waits about 1 minute for the
instance to wake, then about 20–60 s for the startup ingest on 0.1 CPU. The UI shows its
loading and empty states during this time. Bandwidth (5 GB/month) is ample, since tiles are
1–3 kB.

## Deploy on Render

1. Push this repository to GitHub as a public repo.
2. In Render, choose **New → Blueprint**, pick the repo, and Render reads `render.yaml`
   (Docker runtime, free plan, health check `/api/health`). You can instead choose
   **New → Web Service → Docker** with the same settings.
3. Wait for the build, then open `https://<service>.onrender.com/api/health` until
   `data_available` is `true`.

## Continuous integration

`.github/workflows/ci.yml` runs on every push:

1. Backend tests, including the live ECMWF retrieval test.
2. Frontend type check, lint, unit tests and build.
3. Container job: builds the image (including the ecCodes self-test) and runs it with
   `--memory=512m --cpus=0.5`. It waits for the real ingest, smoke-tests the SPA, MIME types,
   PNG tiles, summary and timeline, then runs the Playwright e2e suite against the container.
