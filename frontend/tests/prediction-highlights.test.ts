import { describe, expect, it } from 'vitest'
import type { ContractPrediction } from '@/core/contract-types'
import { predictionHighlightsData } from '@/modules/prediction/prediction-highlights'

function prediction(type: string, result: Record<string, unknown>, status = 'SUCCEEDED', includeError = true): ContractPrediction {
  const defaults: Record<string, unknown> = {
    schema_version: '1.0',
    applicability: { supported_scope: `scope-${type}` },
    rows: [],
  }
  if (type === 'tlc') Object.assign(defaults, { model_id: 'm', model_version: '1', artifact_sha256: 'a'.repeat(64), mobile_phase_original: {}, mobile_phase_normalized: {}, ensemble_weights: {} })
  if (type === 'hplc') defaults.rows = [{ candidate_id: 'A' }, { candidate_id: 'B' }]
  if (type === 'gc') defaults.rows = [{ retention_time_min: 0, quantiles: { q10: 0, q50: 0, q90: 0 } }]
  return { chromatography_type: type, result: { ...defaults, ...result }, status, ...(includeError ? { error: null } : {}) } as unknown as ContractPrediction
}

const metric = (key: string, value: unknown) => ({ key, value })

describe('prediction headline extraction', () => {
  it('uses TLC integrated Rf and preserves raw precision', () => {
    const data = predictionHighlightsData(prediction('tlc', {
      task_type: 'tlc', rows: [{ rf: 0.45 }], metrics: [metric('ensemble_rf', 0.456789)],
    }))
    expect(data).toEqual({ type: 'tlc', rf: 0.456789 })
  })

  it('uses CC q50 volume and returned formal time without rounding or reverse calculation', () => {
    const data = predictionHighlightsData(prediction('cc', {
      task_type: 'cc', metrics: [
        metric('structure_1_V1_volume_q50', 12.34567), metric('structure_1_V1_time_q50', 31.23456),
        metric('structure_1_V2_volume_q50', 22.22222), metric('structure_1_V2_time_q50', 55.55555),
      ],
    }))
    expect(data).toEqual({ type: 'cc', structures: [{ structure: '1', windows: [
      { window: 'V1', volumeMl: 12.34567, timeSeconds: 31.23456 }, { window: 'V2', volumeMl: 22.22222, timeSeconds: 55.55555 },
    ] }] })
  })

  it('keeps the returned HPLC recommendation even when another Sp is larger and drops invalid intervals', () => {
    const data = predictionHighlightsData(prediction('hplc', {
      task_type: 'hplc', rows: [{ candidate_id: 'A', separation: 0.98 }, { candidate_id: 'B', separation: 0.5 }], metrics: [
        metric('recommended_column', 'B'), metric('A.compound_1.rt_q50_min', 5.5555), metric('A.compound_2.rt_q50_min', 8.8888),
        metric('A.compound_1.rt_q10_min', 6), metric('A.compound_1.rt_q90_min', 5),
        metric('B.compound_1.rt_q10_min', 4), metric('B.compound_1.rt_q90_min', 6),
      ],
    }))
    expect(data).toMatchObject({ type: 'hplc', recommendedColumn: 'B' })
    expect(data?.type === 'hplc' && data.rows[0]).toMatchObject({ column: 'A', separation: 0.98, structure1Min: 5.5555, range1: null })
    expect(data?.type === 'hplc' && data.rows[1]).toMatchObject({ column: 'B', separation: 0.5, range1: [4, 6] })
  })

  it('uses GC q50 and exposes a range only when both raw quantiles are valid', () => {
    const data = predictionHighlightsData(prediction('gc', { task_type: 'gc', rows: [{ retention_time_min: 3.45678, quantiles: { q10: 4, q50: 3.45678, q90: 2 } }], metrics: [metric('retention_time_q50_min', 3.45678)] }))
    expect(data).toEqual({ type: 'gc', retentionTimeMin: 3.45678, range: null })
  })

  it('hides malformed results when hideInvalid is enabled', () => {
    const invalid = prediction('gc', { task_type: 'gc', rows: [{ retention_time_min: 3.5, quantiles: { q10: 2, q90: 4 } }] })
    expect(predictionHighlightsData(invalid, true)).toBeNull()
  })

  it('requires succeeded status and no error while allowing a missing nullable error field', () => {
    const valid = prediction('gc', { task_type: 'gc', rows: [{ retention_time_min: 3.5, quantiles: { q10: 2, q50: 3.5, q90: 4 } }], metrics: [metric('retention_time_q50_min', 3.5)] }, 'SUCCEEDED', false)
    expect(predictionHighlightsData(valid)).toEqual({ type: 'gc', retentionTimeMin: 3.5, range: [2, 4] })
    expect(predictionHighlightsData(prediction('gc', { task_type: 'gc', rows: [{ retention_time_min: 3.5, quantiles: { q10: 2, q50: 3.5, q90: 4 } }] }, 'FAILED'))).toBeNull()
    expect(predictionHighlightsData(prediction('gc', { task_type: 'gc', rows: [{ retention_time_min: 3.5, quantiles: { q10: 2, q50: 3.5, q90: 4 } }] }, 'QUEUED'))).toBeNull()
    const errored = valid as unknown as Record<string, unknown>
    expect(predictionHighlightsData({ ...errored, error: { code: 'FAILED' } } as unknown as ContractPrediction)).toBeNull()
    expect(predictionHighlightsData(prediction('gc', { task_type: 'gc', rows: [{ retention_time_min: 3.5 }] , applicability: undefined }))).toBeNull()
  })
})
