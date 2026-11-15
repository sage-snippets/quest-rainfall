import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type Place } from '../api'

export default function Search({ onSelect }: { onSelect: (p: Place) => void }) {
  const [text, setText] = useState('')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  useEffect(() => { const t = setTimeout(() => setQ(text.trim()), 350); return () => clearTimeout(t) }, [text])

  const res = useQuery({ queryKey: ['geocode', q], queryFn: () => api.geocode(q), enabled: q.length >= 2, staleTime: Infinity })

  const pick = (p: Place) => { onSelect(p); setOpen(false); setText(p.name ?? '') }
  return (
    <div className="relative">
      <input value={text} onChange={(e) => { setText(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => { if (e.key === 'Enter' && res.data?.results[0]) pick(res.data.results[0]) }}
        placeholder="Search a place…" aria-label="Search location"
        className="w-full rounded-md border border-line bg-panel-2 px-3 py-2 text-sm text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none" />
      {open && q.length >= 2 && (
        <div className="absolute z-20 mt-1 w-full overflow-hidden rounded-md border border-line bg-panel shadow-xl">
          {res.isLoading && <div className="px-3 py-2 text-xs text-ink-2">Searching…</div>}
          {res.isError && <div className="px-3 py-2 text-xs text-danger">Search unavailable: {(res.error as Error).message}</div>}
          {res.data && res.data.results.length === 0 && <div className="px-3 py-2 text-xs text-ink-2">No places found.</div>}
          {res.data?.results.map((p, i) => (
            <button key={i} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(p)}
              className="block w-full px-3 py-2 text-left text-sm hover:bg-panel-2">
              <span className="text-ink">{p.name}</span>
              <span className="ml-1.5 text-xs text-ink-3">{[p.admin1, p.country].filter(Boolean).join(', ')}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
