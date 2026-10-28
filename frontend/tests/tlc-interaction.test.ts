import { describe, expect, it } from 'vitest'
import { clampRf, curveRange, estimateLabel, estimateRatio, pointerYFromRf, rfFromPointer } from '@/shared/tlc/interaction'

describe('TLC interaction helpers', () => {
  it('keeps a dragged spot inside the evidence range', () => {
    const range = { min: 0.2, max: 0.7 }
    expect(rfFromPointer(-20, 20, 300, range)).toBe(0.7)
    expect(rfFromPointer(400, 20, 300, range)).toBe(0.2)
    expect(pointerYFromRf(0.45, 20, 300, range)).toBeCloseTo(174)
    expect(clampRf(Number.NaN, range)).toBe(0.2)
  })

  it('does not interpolate across failed scan points', () => {
    const result = estimateRatio(0.5, [
      { x: 0, ratio: 0, rf: 0.2 },
      { x: 0.5, ratio: 1, rf: null },
      { x: 1, ratio: 2, rf: 0.8 },
    ])
    expect(result.status).toBe('unreachable')
  })

  it('marks multiple crossings as ambiguous instead of silently choosing one', () => {
    const result = estimateRatio(0.5, [
      { x: 0, ratio: 0, rf: 0.2 },
      { x: 0.5, ratio: 1, rf: 0.8 },
      { x: 1, ratio: 2, rf: 0.2 },
    ])
    expect(result.status).toBe('ambiguous')
    expect(result.candidates).toHaveLength(2)
    expect(estimateLabel(result)).toContain('2 个可能交点')
  })

  it('keeps intersections from different solvent systems when they share the same ratio', () => {
    const result = estimateRatio(0.5, [
      { x: 0.5, ratio: 0.5, rf: 0.5, series: 'hexane/ethyl_acetate' },
      { x: 0.5, ratio: 0.5, rf: 0.5, series: 'dichloromethane/methanol' },
    ])
    expect(result.status).toBe('ambiguous')
    expect(result.candidates).toHaveLength(2)
  })

  it('reports valid range and a labeled sampled point', () => {
    const points = [{ x: 0, ratio: null, rf: 0.3, label: '纯 B' }, { x: 1, ratio: 2, rf: 0.7 }]
    expect(curveRange(points)).toEqual({ min: 0.3, max: 0.7 })
    const result = estimateRatio(0.3, points)
    expect(result.status).toBe('sampled')
    expect(estimateLabel(result)).toBe('纯 B（模型采样点）')
  })

  it('does not treat malformed or out-of-range Rf as model evidence', () => {
    expect(curveRange([
      { x: 0, ratio: 0, rf: Number.NaN },
      { x: 1, ratio: 1, rf: 1.2 },
    ])).toBeNull()
    expect(estimateRatio(0.5, [
      { x: 0, ratio: 0, rf: Number.NaN },
      { x: 1, ratio: 1, rf: null },
    ]).status).toBe('insufficient_data')
  })

  it('can report a direct sampled point without inventing a ratio', () => {
    const result = estimateRatio(0.4, [{ x: 0, ratio: null, rf: 0.4 }])
    expect(result.status).toBe('sampled')
    expect(result.candidates[0]?.ratio).toBeNull()
    expect(estimateLabel(result)).toContain('正式反推任务')
  })

  it('keeps malformed range bounds safe while dragging', () => {
    expect(clampRf(0.5, { min: Number.NaN, max: Number.NaN })).toBe(0.5)
    expect(rfFromPointer(100, 20, 300, { min: 0.8, max: 0.2 })).toBe(0.8)
  })

  it('does not coerce an invalid target into a sampled model value', () => {
    expect(estimateRatio(1.2, [{ x: 0, ratio: 0, rf: 1 }]).status).toBe('unreachable')
  })
})

