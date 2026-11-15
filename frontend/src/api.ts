// Typed client for the FastAPI backend. All URLs are relative (same origin in production,
// Vite proxy in development).

export type BBox = [number, number, number, number] // west, south, east, north

export interface ModelInfo { id: string; name: string; status: 'operational' | 'pending'; description: string; runs_available: number }
export interface Interval { start_step: number; end_step: number; valid_from: string; valid_to: string; max_mm: number }
export interface RunMeta {
  model: string; run_id: string; run: string; steps: number[]; intervals: Interval[]
  variable: string; units: string; accumulation: string; source: string | null
  source_urls: string[]; retrieved_at: string | null; processed_at: string; attribution: string; note: string
}
export interface Window { start_step: number; end_step: number; valid_from: string; valid_to: string; hours: number }
export interface Stats {
  n_cells: number; n_valid: number; min_mm: number | null; max_mm: number | null
  mean_mm: number | null; wet_area_fraction: number | null; wet_threshold_mm: number | null
}
export interface Summary {
  model: string; run: string; window: Window; region: string | null; region_name: string
  bbox: BBox; stats: Stats; max_location: [number, number] | null; notes: string[]
}
export interface TimelinePoint { start_step: number; end_step: number; valid_from: string; valid_to: string; interval_mm: number | null; cumulative_mm: number | null }
export interface Timeline { model: string; run: string; requested: [number, number]; grid_cell: [number, number]; method: string; series: TimelinePoint[]; note: string }
export interface ColorBin { min_mm: number; max_mm: number | null; rgba: [number, number, number, number]; hex: string; label: string }
export interface ColorScale { units: string; transparent_below_mm: number; bins: ColorBin[] }
export interface Region { id: string; name: string; bbox: BBox }
export interface Place { name: string | null; country: string | null; admin1: string | null; lat: number; lon: number }

export class ApiError extends Error {
  status: number
  code?: string
  constructor(status: number, message: string, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params ?? {})) if (v !== undefined) q.set(k, String(v))
  const url = q.size ? `${path}?${q}` : path
  let res: Response
  try {
    res = await fetch(url)
  } catch {
    throw new ApiError(0, 'Cannot reach the forecast server. Check your connection.')
  }
  if (!res.ok) {
    let msg = `Request failed (${res.status})`, code: string | undefined
    try { const b = await res.json(); msg = b?.error?.message ?? msg; code = b?.error?.code } catch { /* non-JSON */ }
    throw new ApiError(res.status, msg, code)
  }
  return res.json() as Promise<T>
}

export const api = {
  models: () => get<ModelInfo[]>('/api/models'),
  latest: (model: string) => get<RunMeta>('/api/forecast/latest', { model }),
  colorscale: () => get<ColorScale>('/api/colorscale'),
  regions: () => get<Region[]>('/api/regions'),
  summary: (model: string, run: string, w: { start: number; end: number }, region: { id?: string; bbox?: BBox }) =>
    get<Summary>(`/api/forecast/${model}/${run}/summary`, {
      start_step: w.start, end_step: w.end,
      region: region.id, bbox: region.id ? undefined : region.bbox?.join(','),
    }),
  timeline: (model: string, run: string, lat: number, lon: number) =>
    get<Timeline>(`/api/forecast/${model}/${run}/timeline`, { lat: lat.toFixed(4), lon: lon.toFixed(4) }),
  geocode: (q: string) => get<{ query: string; cached: boolean; results: Place[] }>('/api/geocode', { q }),
}

export function tileUrl(model: string, run: string, w: { start: number; end: number }) {
  // MapLibre needs absolute tile URLs.
  return `${window.location.origin}/api/forecast/${model}/${run}/tile/{z}/{x}/{y}.png?start_step=${w.start}&end_step=${w.end}`
}

// ---------------------------------------------------------------- time formatting
const utcFmt = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
const istFmt = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
export const fmtUTC = (iso: string) => `${utcFmt.format(new Date(iso))} UTC`
export const fmtIST = (iso: string) => `${istFmt.format(new Date(iso))} IST`
export const fmtMM = (v: number | null | undefined, d = 1) => (v == null ? '—' : v < 0.05 && v > 0 ? '<0.1' : v.toFixed(d))
