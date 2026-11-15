// Accumulation-window helpers shared by the timeline and the app.
export type WindowMode = 'native' | 3 | 6 | 12 | 24 | 'total'
export const MODES: { id: WindowMode; label: string }[] = [
  { id: 'native', label: 'Step' }, { id: 6, label: '6 h' }, { id: 12, label: '12 h' },
  { id: 24, label: '24 h' }, { id: 'total', label: 'Since run start' },
]

/** Window start for a given end step, or null if the run has no step at that offset. */
export function windowStart(steps: number[], end: number, mode: WindowMode): number | null {
  const i = steps.indexOf(end)
  if (i <= 0) return null
  if (mode === 'native') return steps[i - 1]
  if (mode === 'total') return 0
  const s = end - mode
  return s >= 0 && steps.includes(s) ? s : null
}
