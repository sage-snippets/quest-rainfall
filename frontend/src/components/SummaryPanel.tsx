import { fmtMM, type Summary } from '../api'

interface Props { data: Summary | undefined; loading: boolean; error: Error | null; regionLabel: string }

function Stat({ label, value, unit, hint }: { label: string; value: string; unit?: string; hint?: string }) {
  return (
    <div className="rounded-md border border-line bg-panel-2 px-3 py-2" title={hint}>
      <div className="text-[10.5px] uppercase tracking-wide text-ink-3">{label}</div>
      <div className="num mt-0.5 text-lg font-semibold leading-tight text-ink">
        {value}{unit && <span className="ml-1 text-xs font-normal text-ink-2">{unit}</span>}
      </div>
    </div>
  )
}

export default function SummaryPanel({ data, loading, error, regionLabel }: Props) {
  return (
    <section aria-labelledby="sum-h">
      <div className="mb-2 flex items-baseline justify-between">
        <h2 id="sum-h" className="text-sm font-semibold text-ink">Regional summary</h2>
        <span className="text-xs text-ink-2">{regionLabel}</span>
      </div>
      {error ? (
        <p role="alert" className="rounded border border-danger/40 bg-danger/10 px-2.5 py-2 text-xs text-danger">{error.message}</p>
      ) : loading || !data ? (
        <div className="grid grid-cols-2 gap-2">{[0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="h-[58px] animate-pulse rounded-md bg-panel-2" />)}</div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2">
            <Stat label="Mean (area-wtd)" value={fmtMM(data.stats.mean_mm, 2)} unit="mm" hint="Mean over the box, weighted by grid-cell area (cos latitude)" />
            <Stat label="Max cell" value={fmtMM(data.stats.max_mm)} unit="mm" />
            <Stat label="Min cell" value={fmtMM(data.stats.min_mm)} unit="mm" />
            <Stat label={`Wet area ≥${data.stats.wet_threshold_mm ?? 1} mm`}
              value={data.stats.wet_area_fraction == null ? '—' : (data.stats.wet_area_fraction * 100).toFixed(1)} unit="%" />
            <Stat label="Valid cells" value={`${data.stats.n_valid.toLocaleString()}`} unit={`/ ${data.stats.n_cells.toLocaleString()}`} />
            <Stat label="Window" value={`${data.window.hours}`} unit="h" />
          </div>
          <p className="num mt-2 text-xs text-ink-2">
            {data.max_location
              ? <>Heaviest grid cell: <span className="text-ink">{fmtMM(data.stats.max_mm)} mm</span> at {data.max_location[0].toFixed(2)}°, {data.max_location[1].toFixed(2)}°</>
              : <>No precipitation forecast anywhere in this box for the selected window.</>}
          </p>
          <ul className="mt-2 space-y-0.5 text-[11px] leading-snug text-ink-3">
            {data.notes.map((n) => <li key={n}>• {n}</li>)}
          </ul>
        </>
      )}
    </section>
  )
}
