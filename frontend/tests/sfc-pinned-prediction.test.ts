import { describe, expect, it, vi } from 'vitest'
import { resultMetadataError, type PredictionResultPayload } from '@/shared/prediction/result'
import { isValidPinnedSfcCandidateAxes, isValidSfcCandidateAxes, SFC_CANDIDATE_KEYS, SFC_PINNED_REFERENCE } from '@/modules/prediction/forms/sfc/types'

vi.mock('@/app/generated/profile', () => ({
  activeProfile: { id: 'universal-v1', modules: ['prediction'], chromatographyTypes: ['sfc'], predictionTools: {}, utilityFeatures: [], assistantModes: [] },
}))

describe('SFC frozen result and axis contract checks', () => {
  it('accepts the result envelope without invented model/hash fields and rejects malformed metadata', () => {
    const dto = { task_type: 'sfc', schema_version: 'sfc-v1.1', applicability: { supported_scope: 'pinned' }, rows: [] } as const
    const check = (value: unknown) => resultMetadataError(value as PredictionResultPayload, 'sfc')
    expect(check(dto)).toBeNull()
    expect(check({ ...dto, schema_version: '' })).toContain('schema')
    expect(check({ ...dto, applicability: undefined })).toContain('applicability')
    expect(check({ ...dto, score_semantics: 'unknown' })).toContain('分数语义')
    expect(check({ ...dto, score_semantics: 'positive_class_probability' })).toBeNull()
  })

  it('validates candidate axis keys and values against the frozen SFC contract', () => {
    const axes: Record<string, unknown> = Object.fromEntries(SFC_CANDIDATE_KEYS.map((key) => [key, [SFC_PINNED_REFERENCE[key]]]))
    expect(isValidSfcCandidateAxes(axes)).toBe(true)
    expect(isValidPinnedSfcCandidateAxes(axes)).toBe(true)
    expect(isValidSfcCandidateAxes({ column: ['AD'] })).toBe(true)
    expect(isValidSfcCandidateAxes(Object.fromEntries(SFC_CANDIDATE_KEYS.slice(0, 7).map((key) => [key, ['x']] )))).toBe(true)
    expect(isValidPinnedSfcCandidateAxes(Object.fromEntries(SFC_CANDIDATE_KEYS.slice(0, 7).map((key) => [key, [SFC_PINNED_REFERENCE[key]]] )))).toBe(false)
    expect(isValidSfcCandidateAxes({ ...axes, experimental_axis: ['x'] })).toBe(false)
    expect(isValidSfcCandidateAxes({})).toBe(false)
    expect(isValidSfcCandidateAxes(Object.fromEntries([...SFC_CANDIDATE_KEYS, 'extra'].map((key) => [key, ['x']] )))).toBe(false)
    expect(isValidSfcCandidateAxes({ ...axes, length_mm: [] })).toBe(false)
    expect(isValidSfcCandidateAxes({ ...axes, length_mm: ['contract-string-value'] })).toBe(true)
    expect(isValidSfcCandidateAxes({ ...axes, flow_rate_ml_min: [0] })).toBe(true)
    expect(isValidSfcCandidateAxes({ ...axes, particle_size_um: [Number.POSITIVE_INFINITY] })).toBe(false)
    expect(isValidSfcCandidateAxes({ ...axes, column: [null] })).toBe(false)
  })

  it('requires candidate result rows to match public SFC row semantics and all condition fields', () => {
    const check = (rows: unknown[]) => resultMetadataError({ task_type: 'sfc', schema_version: 'sfc-v1.1', applicability: { supported_scope: 'pinned' }, rows } as unknown as PredictionResultPayload, 'sfc')
    const conditions = Object.fromEntries(SFC_CANDIDATE_KEYS.map((key) => [key, SFC_PINNED_REFERENCE[key]]))
    const validRow = { candidate_id: 'candidate-1', rank: 1, conditions, score_semantics: 'positive_class_probability' }
    expect(check([validRow])).toBeNull()
    expect(check([{}])).toContain('候选结果行')
    expect(check([{ ...validRow, rank: 1.5 }])).toContain('候选结果行')
    expect(check([{ ...validRow, candidate_id: '  ' }])).toContain('候选结果行')
    expect(check([{ ...validRow, score_semantics: undefined }])).toContain('候选结果行')
    expect(check([{ ...validRow, conditions: { ...conditions, flow_rate_ml_min: 0 } }])).toContain('候选结果行')
    expect(check([{ ...validRow, conditions: { ...conditions, column_temperature_c: Number.NaN } }])).toContain('候选结果行')
    expect(check([{ ...validRow, conditions: { column: 'AD' } }])).toContain('候选结果行')
    expect(check([{ ...validRow, separation: 'not-a-number' }])).toContain('候选结果行')
    expect(check([{ ...validRow, unexpected: true }])).toContain('候选结果行')
    expect(check([{ ...validRow, separation: null }])).toBeNull()
  })

  it('keeps legacy and universal result row contracts distinct', () => {
    const payload = (rows: unknown[]) => ({ task_type: 'sfc', schema_version: 'sfc-v1.1', applicability: { supported_scope: 'pinned' }, rows })
    const check = (rows: unknown[], profileId: 'sfc-v1' | 'universal-v1') => resultMetadataError(payload(rows) as unknown as PredictionResultPayload, 'sfc', profileId)
    const checkPayload = (value: unknown, profileId: 'sfc-v1' | 'universal-v1') => resultMetadataError(value as PredictionResultPayload, 'sfc', profileId)
    const legacyRow = { candidate_id: 'candidate-legacy', rank: 1, separation: null }
    expect(check([legacyRow], 'sfc-v1')).toBeNull()
    expect(check([{ candidate_id: 'candidate-legacy', rank: 1, separation: 0.25 }], 'sfc-v1')).toBeNull()
    expect(check([{ candidate_id: 'candidate-legacy', rank: 1, separation: Number.NaN }], 'sfc-v1')).toContain('候选结果行')
    expect(check([{ candidate_id: 'candidate-legacy', rank: 1, conditions: {} }], 'sfc-v1')).toContain('候选结果行')
    expect(check([{ candidate_id: 'candidate-legacy', rank: 1, score_semantics: 'positive_class_probability' }], 'sfc-v1')).toContain('候选结果行')
    expect(checkPayload({ ...payload([legacyRow]), score_semantics: 'positive_class_probability' }, 'sfc-v1')).toContain('顶层 score_semantics')
    expect(check([legacyRow], 'universal-v1')).toContain('候选结果行')
    const universalConditions = Object.fromEntries(SFC_CANDIDATE_KEYS.map((key) => [key, SFC_PINNED_REFERENCE[key]]))
    const universalRow = { ...legacyRow, conditions: universalConditions, score_semantics: 'positive_class_probability' }
    expect(check([universalRow], 'universal-v1')).toBeNull()
    expect(checkPayload({ ...payload([universalRow]), score_semantics: 'positive_class_probability' }, 'universal-v1')).toBeNull()
  })
})
