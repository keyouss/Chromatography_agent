import { describe, expect, it } from 'vitest'
import { candidateCount, normalizedModifiers } from './candidates'

describe('SFC form calculations', () => {
  it('computes the eight-condition Cartesian product and preserves zero as empty', () => {
    expect(candidateCount({ column: ['AD', 'OD'], stationary_phase: ['p'], mode: ['coated'], length_mm: ['150'], inner_diameter_mm: ['4.6'], particle_size_um: ['5'], column_temperature_c: ['35', '40'], flow_rate_ml_min: ['2'] })).toBe(4)
    expect(candidateCount({ column: [] })).toBe(0)
  })

  it('normalizes Modifier composition without changing the total ratio', () => {
    expect(normalizedModifiers([
      { name: 'A', smiles: 'A', ratio: 1 },
      { name: 'B', smiles: 'B', ratio: 3 },
    ], true).map((item) => item.ratio)).toEqual([0.25, 0.75])
    expect(normalizedModifiers([{ name: 'A', smiles: 'A', ratio: 3 }], false)[0]?.ratio).toBe(3)
  })
})

