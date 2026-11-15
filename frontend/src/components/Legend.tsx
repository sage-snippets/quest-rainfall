import type { ColorScale } from '../api'

// Rendered from /api/colorscale, the same table the tile renderer uses.
export default function Legend({ scale, hours }: { scale: ColorScale | undefined; hours: number | null }) {
  if (!scale) return <div className="h-12 animate-pulse rounded bg-panel-2" />
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between text-[11px] text-ink-2">
        <span className="font-medium text-ink">Precipitation</span>
        <span>mm {hours ? `in ${hours} h` : 'per window'} · forecast</span>
      </div>
      <div className="flex gap-[2px]" role="list" aria-label="Precipitation colour scale">
        {scale.bins.map((b) => (
          <div key={b.min_mm} role="listitem" className="flex-1" title={`${b.min_mm}${b.max_mm ? `–${b.max_mm}` : '+'} mm${b.label ? ` · ${b.label}` : ''}`}>
            <div className="h-2.5 rounded-[2px]" style={{ background: `rgba(${b.rgba.slice(0, 3).join(',')},${(b.rgba[3] / 255).toFixed(2)})` }} />
            <div className="num mt-1 text-center text-[9.5px] text-ink-3">{b.min_mm}</div>
          </div>
        ))}
      </div>
      <div className="mt-0.5 flex justify-between text-[10px] text-ink-3">
        <span>&lt;{scale.transparent_below_mm} mm: clear</span>
        <span>Light · Moderate · Heavy · Extreme</span>
      </div>
    </div>
  )
}
