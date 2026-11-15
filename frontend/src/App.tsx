import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api, ApiError, fmtUTC, tileUrl, type BBox, type Place } from './api'
import Legend from './components/Legend'
import MapView from './components/MapView'
import PointChart from './components/PointChart'
import Search from './components/Search'
import SummaryPanel from './components/SummaryPanel'
import Timeline from './components/Timeline'
import { windowStart, type WindowMode } from './window'

type RegionSel = { kind: 'preset'; id: string } | { kind: 'place'; name: string; bbox: BBox }

const MODEL_LABEL: Record<string, string> = { ifs: 'IFS', 'aifs-single': 'AIFS' }
const MODEL_LONG: Record<string, string> = { ifs: 'IFS HRES', 'aifs-single': 'AIFS Single' }

const placeBox = (lat: number, lon: number): BBox => [lon - 0.5, lat - 0.5, lon + 0.5, lat + 0.5]

export default function App() {
  const [model, setModel] = useState('ifs')
  const label = MODEL_LABEL[model] ?? model
  const models = useQuery({ queryKey: ['models'], queryFn: api.models })
  const latest = useQuery({ queryKey: ['latest', model], queryFn: () => api.latest(model), retry: (n, e) => (e as ApiError).status !== 404 && n < 2 })
  const scale = useQuery({ queryKey: ['colorscale'], queryFn: api.colorscale, staleTime: Infinity })
  const regions = useQuery({ queryKey: ['regions'], queryFn: api.regions, staleTime: Infinity })

  const meta = latest.data
  const [mode, setMode] = useState<WindowMode>(24)
  const [endSel, setEnd] = useState<number | null>(null)
  const [region, setRegion] = useState<RegionSel>({ kind: 'preset', id: 'india' })
  const [fitTo, setFitTo] = useState<{ bbox: BBox; key: number } | null>(null)
  const [point, setPoint] = useState<[number, number] | null>(null)
  const [tileError, setTileError] = useState<string | null>(null)

  // Default step: first full day if the run has it, else the first step.
  const end = !meta ? null : endSel !== null && meta.steps.includes(endSel) ? endSel : meta.steps.includes(24) ? 24 : meta.steps[1]

  const start = meta && end !== null ? windowStart(meta.steps, end, mode) : null
  const win = useMemo(() => (start !== null && end !== null ? { start, end } : null), [start, end])
  const runId = meta?.run_id
  const metaModel = meta?.model
  const tiles = useMemo(() => (runId && win && metaModel === model ? tileUrl(model, runId, win) : null), [runId, win, metaModel, model])

  const regionArg = region.kind === 'preset' ? { id: region.id } : { bbox: region.bbox }
  const summary = useQuery({
    queryKey: ['summary', model, meta?.run_id, win?.start, win?.end, region],
    queryFn: () => api.summary(model, meta!.run_id, win!, regionArg),
    enabled: !!meta && !!win && meta.model === model,
    // Keep showing the previous result while refetching, but never across models: that
    // would briefly display one model's statistics under the other's label.
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[1] === model ? keepPreviousData(prev) : undefined),
  })
  const timeline = useQuery({
    queryKey: ['timeline', model, meta?.run_id, point?.[0], point?.[1]],
    queryFn: () => api.timeline(model, meta!.run_id, point![0], point![1]),
    enabled: !!meta && !!point && meta.model === model,
  })

  const regionLabel = region.kind === 'preset' ? regions.data?.find((r) => r.id === region.id)?.name ?? region.id : region.name
  const choosePreset = (id: string) => {
    setRegion({ kind: 'preset', id })
    const r = regions.data?.find((x) => x.id === id)
    if (r) setFitTo({ bbox: r.bbox, key: Date.now() })
  }
  const choosePlace = (p: Place) => {
    const name = [p.name, p.country].filter(Boolean).join(', ')
    const bbox = placeBox(p.lat, p.lon)
    setRegion({ kind: 'place', name, bbox })
    setPoint([p.lat, p.lon])
    setFitTo({ bbox: [p.lon - 2.5, p.lat - 2.5, p.lon + 2.5, p.lat + 2.5], key: Date.now() })
  }

  const noData = latest.error instanceof ApiError && latest.error.status === 404

  return (
    <div className="flex min-h-full flex-col lg:h-full">
      {/* ---------------------------------------------------------------- header */}
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-panel px-4 py-2.5">
        <div className="flex items-center gap-2.5">
          <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden><path d="M16 4c4.5 6.5 9 11 9 16a9 9 0 0 1-18 0c0-5 4.5-9.5 9-16z" fill="#40a0f0" /></svg>
          <div>
            <h1 className="text-[15px] font-semibold leading-tight tracking-tight">QUEST Rainfall</h1>
            <p className="text-[11px] leading-tight text-ink-3">ECMWF precipitation forecast analysis</p>
          </div>
        </div>
        <div className="flex rounded-md border border-line bg-panel-2 p-0.5" role="radiogroup" aria-label="Forecast model">
          {(models.data ?? [{ id: 'ifs', name: 'IFS', status: 'operational' as const }]).map((m) => {
            const ok = m.status === 'operational'
            return (
              <button key={m.id} role="radio" aria-checked={m.id === model} disabled={!ok} title={'description' in m ? m.description : undefined}
                onClick={() => ok && setModel(m.id)}
                className={`rounded px-2.5 py-1 text-xs ${m.id === model ? 'bg-accent/20 text-ink ring-1 ring-accent/60' : 'text-ink-3'} disabled:cursor-not-allowed`}>
                {MODEL_LONG[m.id] ?? m.id}{!ok && <span className="ml-1.5 rounded bg-warn/15 px-1 text-[10px] text-warn">coming soon</span>}
              </button>
            )
          })}
        </div>
        <div className="num ml-auto text-right text-[11px] leading-tight text-ink-2">
          {meta ? (<>
            <div>Run <span className="text-ink">{fmtUTC(meta.run)}</span> · T+0–{meta.steps.at(-1)}h</div>
            <div className="text-ink-3">Source: {meta.source === 'ecmwf' ? 'data.ecmwf.int' : meta.source} · retrieved {meta.retrieved_at ? fmtUTC(meta.retrieved_at) : '—'}</div>
          </>) : latest.isLoading ? <div className="h-6 w-48 animate-pulse rounded bg-panel-2" /> : null}
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {/* ---------------------------------------------------------------- sidebar */}
        <aside className="order-2 flex flex-col gap-5 border-line bg-panel p-4 lg:order-1 lg:w-[360px] lg:shrink-0 lg:overflow-y-auto lg:border-r">
          <section aria-labelledby="loc-h">
            <h2 id="loc-h" className="mb-2 text-sm font-semibold">Location</h2>
            <Search onSelect={choosePlace} />
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {(regions.data ?? []).map((r) => {
                const on = region.kind === 'preset' && region.id === r.id
                return (
                  <button key={r.id} onClick={() => choosePreset(r.id)} aria-pressed={on}
                    className={`rounded-full border px-2.5 py-1 text-xs ${on ? 'border-accent/70 bg-accent/15 text-ink' : 'border-line bg-panel-2 text-ink-2 hover:text-ink'}`}>
                    {r.name}
                  </button>
                )
              })}
              {regions.isError && <span className="text-xs text-danger">Region presets unavailable.</span>}
            </div>
          </section>

          <SummaryPanel data={summary.data} loading={summary.isLoading || (!win && !!meta)} error={summary.error} regionLabel={regionLabel} />

          <section aria-labelledby="pt-h">
            <div className="mb-2 flex items-baseline justify-between">
              <h2 id="pt-h" className="text-sm font-semibold">Point forecast</h2>
              {timeline.data && <span className="num text-xs text-ink-2">grid cell {timeline.data.grid_cell[0].toFixed(2)}°, {timeline.data.grid_cell[1].toFixed(2)}°</span>}
            </div>
            {!point ? <p className="text-xs text-ink-3">Click the map or search a place to see the forecast time series at the nearest 0.25° grid cell.</p>
              : timeline.isLoading ? <div className="h-36 animate-pulse rounded bg-panel-2" />
              : timeline.error ? <p role="alert" className="text-xs text-danger">{(timeline.error as Error).message}</p>
              : timeline.data && end !== null ? <PointChart data={timeline.data} activeEnd={end} onPickEnd={setEnd} /> : null}
          </section>

          <section className="mt-auto rounded-md border border-line bg-panel-2/60 p-3 text-[11px] leading-relaxed text-ink-3">
            <p><span className="font-medium text-warn">Forecast, not observations.</span> Values are ECMWF {label} model output on a 0.25° (~28 km) grid{model === 'aifs-single' ? ' (AIFS is a data-driven, machine-learned model; its fields are typically smoother than IFS)' : ''}. Single-cell extremes in tropical convection are highly uncertain.</p>
            <p className="mt-1.5">Total precipitation (rain + snow, water equivalent). Windows are differences of ECMWF accumulations; packing-noise negatives are clipped to 0.</p>
            <p className="mt-1.5">Contains ECMWF Open Data, CC BY 4.0. Basemap © OpenStreetMap contributors, © CARTO.</p>
          </section>
        </aside>

        {/* ---------------------------------------------------------------- map */}
        <main className="relative order-1 flex flex-col lg:order-2 lg:min-h-0 lg:flex-1">
          <div className="relative h-[58vh] min-h-[320px] lg:h-auto lg:flex-1">
            <MapView tiles={tiles} fitTo={fitTo} point={point} onPick={(la, lo) => setPoint([la, lo])} onTileError={setTileError} />
            <div className="pointer-events-none absolute left-3 top-3 flex max-w-[calc(100%-4rem)] flex-col gap-2">
              {(noData || latest.isError) && (
                <div role="alert" className="pointer-events-auto rounded-md border border-warn/40 bg-panel/95 px-3 py-2 text-xs text-warn shadow-lg">
                  {noData ? 'No forecast has been ingested on the server yet. The map stays empty until real ECMWF data is available.'
                    : `Could not load forecast metadata: ${(latest.error as Error).message}`}
                </div>
              )}
              {tileError && <div role="alert" className="rounded-md border border-danger/40 bg-panel/95 px-3 py-2 text-xs text-danger">{tileError}</div>}
              {meta && <div className="w-fit rounded bg-panel/85 px-2 py-1 text-[11px] text-ink-2 backdrop-blur">ECMWF {label} forecast · model grid values, not observed rainfall</div>}
            </div>
          </div>
          <div className="border-t border-line bg-panel/95 p-3 backdrop-blur lg:absolute lg:bottom-6 lg:left-4 lg:w-[480px] lg:rounded-lg lg:border lg:shadow-2xl">
            {meta && end !== null ? (
              <div className="flex flex-col gap-3">
                <Timeline meta={meta} end={end} mode={mode} onEnd={setEnd} onMode={setMode} />
                <Legend scale={scale.data} hours={win ? win.end - win.start : null} />
              </div>
            ) : latest.isLoading ? <div className="h-28 animate-pulse rounded bg-panel-2" />
              : <p className="text-xs text-ink-3">Timeline unavailable until a forecast run is loaded.</p>}
          </div>
        </main>
      </div>
    </div>
  )
}
