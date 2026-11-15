import { fmtIST, fmtUTC, type RunMeta } from '../api'
import { MODES, windowStart, type WindowMode } from '../window'

interface Props {
  meta: RunMeta
  end: number
  mode: WindowMode
  onEnd: (s: number) => void
  onMode: (m: WindowMode) => void
}

export default function Timeline({ meta, end, mode, onEnd, onMode }: Props) {
  const ends = meta.steps.slice(1)
  const idx = ends.indexOf(end)
  const start = windowStart(meta.steps, end, mode)
  const t0 = new Date(meta.run).getTime()
  const iso = (h: number) => new Date(t0 + h * 3600_000).toISOString()

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-md border border-line bg-panel-2 p-0.5" role="radiogroup" aria-label="Accumulation window">
          {MODES.map((m) => {
            const ok = ends.some((e) => windowStart(meta.steps, e, m.id) !== null)
            return (
              <button key={String(m.id)} role="radio" aria-checked={mode === m.id} disabled={!ok}
                onClick={() => onMode(m.id)}
                className={`rounded px-2.5 py-1 text-xs transition-colors disabled:opacity-30 ${mode === m.id ? 'bg-accent/20 text-ink ring-1 ring-accent/60' : 'text-ink-2 hover:text-ink'}`}>
                {m.label}
              </button>
            )
          })}
        </div>
        <div className="ml-auto flex items-center gap-1">
          <button aria-label="Previous step" disabled={idx <= 0} onClick={() => onEnd(ends[idx - 1])}
            className="rounded border border-line bg-panel-2 px-2 py-1 text-xs text-ink-2 hover:text-ink disabled:opacity-30">◀</button>
          <button aria-label="Next step" disabled={idx >= ends.length - 1} onClick={() => onEnd(ends[idx + 1])}
            className="rounded border border-line bg-panel-2 px-2 py-1 text-xs text-ink-2 hover:text-ink disabled:opacity-30">▶</button>
        </div>
      </div>

      <input type="range" min={0} max={ends.length - 1} step={1} value={Math.max(idx, 0)}
        onChange={(e) => onEnd(ends[Number(e.target.value)])}
        aria-label="Forecast lead time" aria-valuetext={`T+${end} h`} className="w-full" />
      <div className="num -mt-1 flex justify-between text-[10px] text-ink-3">
        <span>T+{ends[0]}h</span><span>T+{ends[Math.floor(ends.length / 2)]}h</span><span>T+{ends[ends.length - 1]}h</span>
      </div>

      {start === null ? (
        <p className="rounded border border-warn/40 bg-warn/10 px-2.5 py-1.5 text-xs text-warn">
          No {mode}-hour window ends at T+{end}h in this run. Pick another step or window.
        </p>
      ) : (
        <div className="num grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
          <span className="text-ink-3">Window</span>
          <span className="text-ink">T+{start}h → T+{end}h <span className="text-ink-2">({end - start} h accumulation)</span></span>
          <span className="text-ink-3">Valid</span>
          <span className="text-ink">{fmtUTC(iso(start))} → {fmtUTC(iso(end)).replace(/^\w+, /, '')}</span>
          <span className="text-ink-3" />
          <span className="text-ink-2">{fmtIST(iso(start))} → {fmtIST(iso(end)).replace(/^\w+, /, '')}</span>
        </div>
      )}
    </div>
  )
}
