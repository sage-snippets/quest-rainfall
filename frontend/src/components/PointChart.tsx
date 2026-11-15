import { useState } from 'react'
import { fmtIST, fmtMM, fmtUTC, type Timeline } from '../api'

// Single-series bar chart: precipitation per native forecast interval at the nearest grid cell.
export default function PointChart({ data, activeEnd, onPickEnd }: { data: Timeline; activeEnd: number; onPickEnd: (e: number) => void }) {
  const [hover, setHover] = useState<number | null>(null)
  const s = data.series
  const W = 320, H = 120, PL = 30, PB = 18, PT = 6
  const max = Math.max(1, ...s.map((p) => p.interval_mm ?? 0))
  const nice = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000].find((v) => v >= max) ?? max
  const bw = (W - PL) / s.length
  const y = (v: number) => PT + (H - PT - PB) * (1 - v / nice)
  const total = s.length ? s[s.length - 1].cumulative_mm : null
  const hv = hover !== null ? s[hover] : null

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-xs">
        <span className="text-ink-2">mm per forecast interval</span>
        <span className="num text-ink-2">Total T+{s.at(-1)?.end_step ?? 0}h: <span className="text-ink">{fmtMM(total)} mm</span></span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Precipitation per interval at the selected point">
        {[0, nice / 2, nice].map((v) => (
          <g key={v}>
            <line x1={PL} x2={W} y1={y(v)} y2={y(v)} stroke="var(--color-line)" strokeWidth={1} />
            <text x={PL - 4} y={y(v) + 3} textAnchor="end" fontSize={9} fill="var(--color-ink-3)" className="num">{v}</text>
          </g>
        ))}
        {s.map((p, i) => {
          const v = p.interval_mm ?? 0
          const x = PL + i * bw + 1, w = Math.max(1, bw - 2), top = y(v), h = H - PB - top
          const active = p.end_step === activeEnd
          return (
            <g key={p.end_step} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onClick={() => onPickEnd(p.end_step)} className="cursor-pointer">
              <rect x={PL + i * bw} y={PT} width={bw} height={H - PT - PB} fill="transparent" />
              {h > 0 && <path d={roundedTop(x, top, w, h, Math.min(2, w / 2))} fill={active ? '#9fd0ff' : 'var(--color-accent)'} opacity={hover === null || hover === i ? 1 : 0.55} />}
              {active && <line x1={x + w / 2} x2={x + w / 2} y1={PT} y2={H - PB} stroke="#9fd0ff" strokeWidth={1} strokeDasharray="2 2" opacity={0.6} />}
            </g>
          )
        })}
        {[0, Math.floor(s.length / 2), s.length - 1].filter((i) => s[i]).map((i) => (
          <text key={i} x={PL + i * bw + bw / 2} y={H - 5} textAnchor="middle" fontSize={9} fill="var(--color-ink-3)" className="num">T+{s[i].end_step}h</text>
        ))}
      </svg>
      <div className="num min-h-[2.5rem] text-[11px] text-ink-2" aria-live="polite">
        {hv ? (<>
          <div><span className="text-ink">{fmtMM(hv.interval_mm, 2)} mm</span> in T+{hv.start_step}–{hv.end_step}h · cumulative {fmtMM(hv.cumulative_mm)} mm</div>
          <div className="text-ink-3">{fmtUTC(hv.valid_from)} → {fmtUTC(hv.valid_to).replace(/^\w+, /, '')} · {fmtIST(hv.valid_to)}</div>
        </>) : <span className="text-ink-3">Hover a bar for details; click to show that step on the map.</span>}
      </div>
    </div>
  )
}

function roundedTop(x: number, y: number, w: number, h: number, r: number) {
  r = Math.min(r, h)
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`
}
