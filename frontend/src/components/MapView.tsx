import { Map as MLMap, Marker, NavigationControl, ScaleControl, setWorkerUrl, type MapMouseEvent } from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import { useEffect, useRef } from 'react'
import type { BBox } from '../api'

// MapLibre derives its worker URL from import.meta.url at runtime, which bundlers cannot
// see; import the (self-contained) worker as an emitted asset and register it explicitly.
setWorkerUrl(workerUrl)

const BASEMAP = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'
const INDIA_CENTER: [number, number] = [80.5, 22.5]

interface Props {
  tiles: string | null           // XYZ template for the selected run + window; null = nothing to draw
  fitTo: { bbox: BBox; key: number } | null
  point: [number, number] | null // [lat, lon]
  onPick: (lat: number, lon: number) => void
  onTileError: (msg: string | null) => void
}

export default function MapView({ tiles, fitTo, point, onPick, onTileError }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<MLMap | null>(null)
  const marker = useRef<Marker | null>(null)
  const ready = useRef(false)
  const pending = useRef<string | null>(tiles)
  const pickRef = useRef(onPick)
  const errRef = useRef(onTileError)
  useEffect(() => { pickRef.current = onPick; errRef.current = onTileError }, [onPick, onTileError])

  // ---- create once
  useEffect(() => {
    const m = new MLMap({
      container: el.current!, style: BASEMAP, center: INDIA_CENTER, zoom: 3.6,
      minZoom: 1, maxZoom: 11, attributionControl: { compact: true }, renderWorldCopies: true,
    })
    m.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    m.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-right')
    m.on('load', () => {
      ready.current = true
      if (pending.current) setTiles(m, pending.current)
    })
    m.on('click', (e: MapMouseEvent) => pickRef.current(e.lngLat.lat, e.lngLat.wrap().lng))
    m.on('error', (e: unknown) => {
      // Raster tile failures surface here; basemap glitches are ignored.
      const src = (e as { sourceId?: string }).sourceId
      if (src === 'precip') errRef.current('Some precipitation tiles failed to load.')
    })
    map.current = m
    return () => { m.remove(); map.current = null; ready.current = false }
  }, [])

  // ---- swap tiles when run/window changes
  useEffect(() => {
    pending.current = tiles
    const m = map.current
    if (m && ready.current) setTiles(m, tiles)
    errRef.current(null)
  }, [tiles])

  // ---- fit to region
  useEffect(() => {
    if (!fitTo || !map.current) return
    const [w, s, e, n] = fitTo.bbox
    if (e - w >= 359) map.current.flyTo({ center: [INDIA_CENTER[0], 15], zoom: 1.2 })
    else map.current.fitBounds([[w, s], [e, n]], { padding: 40, maxZoom: 8.5, duration: 900 })
  }, [fitTo])

  // ---- point marker
  useEffect(() => {
    const m = map.current
    if (!m) return
    marker.current?.remove()
    marker.current = null
    if (point) {
      const dot = document.createElement('div')
      dot.className = 'h-3.5 w-3.5 rounded-full border-2 border-white bg-accent shadow-[0_0_0_3px_rgba(64,160,240,0.35)]'
      marker.current = new Marker({ element: dot }).setLngLat([point[1], point[0]]).addTo(m)
    }
  }, [point])

  return <div ref={el} className="h-full w-full" aria-label="Precipitation map" role="region" />
}

function setTiles(m: MLMap, tiles: string | null) {
  if (m.getLayer('precip')) m.removeLayer('precip')
  if (m.getSource('precip')) m.removeSource('precip')
  if (!tiles) return
  m.addSource('precip', {
    type: 'raster', tiles: [tiles], tileSize: 256, minzoom: 0, maxzoom: 10,
    attribution: 'Forecast: <a href="https://www.ecmwf.int/en/forecasts/datasets/open-data">ECMWF Open Data</a> (CC BY 4.0)',
  })
  // Draw under the basemap's labels so place names stay readable.
  const firstSymbol = m.getStyle().layers.find((l) => l.type === 'symbol')?.id
  m.addLayer({
    id: 'precip', type: 'raster', source: 'precip',
    paint: { 'raster-opacity': 0.88, 'raster-resampling': 'nearest', 'raster-fade-duration': 0 },
  }, firstSymbol)
}
