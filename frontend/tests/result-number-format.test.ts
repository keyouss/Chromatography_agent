import { describe, expect, it } from 'vitest'
import { formatResultCell, toDisplayRow } from '@/shared/prediction/result'

describe('scientific result display precision', () => {
  it('shows two decimals while preserving rank, identifiers, units and missing values', () => {
    expect(formatResultCell(0.222364, 'predicted_rf')).toBe('0.22')
    expect(formatResultCell(35, 'volume_ml')).toBe('35.00')
    expect(formatResultCell(0.5, 'value')).toBe('0.50')
    expect(formatResultCell(2, 'rank')).toBe('2')
    expect(formatResultCell(8, 'candidate_count')).toBe('8')
    expect(formatResultCell('0.001-model', 'model_id')).toBe('0.001-model')
    expect(formatResultCell(null)).toBe('—')
    expect(formatResultCell(NaN)).toBe('—')
    expect(formatResultCell(-0.00001)).toBe('0.00')
    expect(formatResultCell({ predicted_rf: 0.222364, condition_index: 1 })).toBe('{"predicted_rf":"0.22","condition_index":"1"}')
  })

  it('does not change the scientific result retained for exports and subsequent operations', () => {
    const row = { predicted_rf: 0.222364, rank: 1 }
    const displayRow = toDisplayRow(row)
    expect(formatResultCell(displayRow.predicted_rf, 'predicted_rf')).toBe('0.22')
    expect(row.predicted_rf).toBe(0.222364)
    expect(displayRow.predicted_rf).toBe(0.222364)
  })
})
