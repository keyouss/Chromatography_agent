import { describe, expect, it } from 'vitest'
import { isSfcScopeProvenance, sameSfcScopeProvenance } from '@/shared/prediction/sfc-provenance'
import { resultMetadataError, toSfcDisplayRow, type PredictionResultPayload } from '@/shared/prediction/result'

// Synthetic protocol fixtures; no catalog, molecule experiment or model invocation.
const provenance = {
  scope_id: 'scope-synthetic',
  policy: { expansion_mode: 'coupled_discrete_conditions', policy_version: '1', candidate_source_sha256: 'a'.repeat(64) },
  candidate_count: 8, observed_count: 4, unobserved_count: 4,
}
const conditions = { column: 'AD', stationary_phase: 'amylose', mode: 'coated', length_mm: 150, inner_diameter_mm: 4.6, particle_size_um: 5, column_temperature_c: 35, flow_rate_ml_min: 2.5, observed_in_source: false }
const result = {
  task_type: 'sfc', schema_version: '1.1', applicability: { supported_scope: 'Synthetic fixture', warnings: [] },
  rows: [{ candidate_id: 'candidate-synthetic', rank: 1, separation: 0.7, score_semantics: 'positive_class_probability', conditions }],
  candidate_scope_provenance: provenance, evaluated_candidate_count: 8,
}
const metadataError = (value: unknown) => resultMetadataError(value as PredictionResultPayload, 'sfc', 'universal-v1')

describe('SFC immutable scope provenance', () => {
  it('checks the exact scope policy and sum of full-range counts', () => {
    expect(isSfcScopeProvenance(provenance)).toBe(true)
    expect(isSfcScopeProvenance({ ...provenance, unobserved_count: 3 })).toBe(false)
    expect(isSfcScopeProvenance({ ...provenance, candidate_count: 100001, unobserved_count: 99997 })).toBe(false)
    expect(isSfcScopeProvenance({ ...provenance, policy: { ...provenance.policy, candidate_source_sha256: '../source' } })).toBe(false)
    expect(isSfcScopeProvenance({ ...provenance, policy: { ...provenance.policy, expansion_mode: 'observed_distinct_rows' } })).toBe(false)
  })

  it('compares semantically rather than JSON key order and rejects changed immutable bindings', () => {
    const reordered = { unobserved_count: 4, observed_count: 4, candidate_count: 8, policy: { candidate_source_sha256: 'a'.repeat(64), policy_version: '1', expansion_mode: 'coupled_discrete_conditions' }, scope_id: 'scope-synthetic' }
    expect(sameSfcScopeProvenance(provenance, reordered)).toBe(true)
    expect(sameSfcScopeProvenance(provenance, { ...reordered, scope_id: 'another-scope' })).toBe(false)
    expect(sameSfcScopeProvenance(provenance, { ...reordered, policy: { ...reordered.policy, candidate_source_sha256: 'b'.repeat(64) } })).toBe(false)
    expect(sameSfcScopeProvenance(null, undefined)).toBe(true)
    expect(sameSfcScopeProvenance(null, provenance)).toBe(false)
  })

  it('keeps evaluated candidates, full scope count and returned rows separate', () => {
    expect(metadataError(result)).toBeNull()
    expect(metadataError({ ...result, evaluated_candidate_count: 9 })).not.toBeNull()
    expect(metadataError({ ...result, evaluated_candidate_count: 0 })).not.toBeNull()
    expect(metadataError({ ...result, candidate_scope_provenance: { ...provenance, unobserved_count: 0, observed_count: 8 } })).not.toBeNull()
    expect(metadataError({ ...result, candidate_scope_provenance: null })).not.toBeNull()
  })

  it('retains legacy results with absent provenance and does not invent observation flags', () => {
    const { observed_in_source: _flag, ...legacyConditions } = conditions
    const legacy = { ...result, candidate_scope_provenance: null, evaluated_candidate_count: null, rows: [{ ...result.rows[0]!, conditions: legacyConditions }] }
    expect(metadataError(legacy)).toBeNull()
    expect(toSfcDisplayRow(legacy.rows[0]!)).not.toHaveProperty('observed_in_source')
    expect(toSfcDisplayRow(result.rows[0]!).observed_in_source).toBe('否')
    expect(toSfcDisplayRow({ ...result.rows[0]!, conditions: { ...conditions, observed_in_source: true } }).observed_in_source).toBe('是')
    expect(toSfcDisplayRow({ ...result.rows[0]!, conditions: { ...conditions, observed_in_source: null } }).observed_in_source).toBe('未知')
    expect(metadataError({ ...result, rows: [{ ...result.rows[0]!, conditions: { ...conditions, observed_in_source: 'false' } }] })).not.toBeNull()
  })
})
