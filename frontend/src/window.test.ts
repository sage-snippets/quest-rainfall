import { describe, expect, it } from 'vitest'
import { windowStart } from './window'

const steps = [0, 3, 6, 9, 12, 15, 18, 21, 24, 30, 36]

describe('windowStart', () => {
  it('native interval = previous published step', () => {
    expect(windowStart(steps, 3, 'native')).toBe(0)
    expect(windowStart(steps, 30, 'native')).toBe(24)   // 6-hourly part of the run
  })
  it('fixed-length windows need an exact published start step', () => {
    expect(windowStart(steps, 24, 24)).toBe(0)
    expect(windowStart(steps, 30, 6)).toBe(24)
    expect(windowStart(steps, 30, 24)).toBe(6)
    expect(windowStart(steps, 36, 24)).toBe(12)
    expect(windowStart(steps, 12, 24)).toBeNull()        // would start before the run
    expect(windowStart([0, 6, 12, 18, 24, 30], 30, 12)).toBe(18)
    expect(windowStart([0, 6, 12, 24], 24, 6)).toBeNull() // 18 not published
  })
  it('since run start always starts at 0; step 0 or unknown steps have no window', () => {
    expect(windowStart(steps, 36, 'total')).toBe(0)
    expect(windowStart(steps, 0, 'native')).toBeNull()
    expect(windowStart(steps, 7, 'native')).toBeNull()
  })
})
