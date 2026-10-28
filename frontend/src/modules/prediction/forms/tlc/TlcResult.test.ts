import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { ContractPrediction } from '@/core/contract-types'
import { resultMetadataError, type TlcConditionComparisonResult, type TlcReverseResult, type TlcSingleResult } from '../../result'
import { usePredictionStore } from '../../store'

const singleResult: TlcSingleResult = {
  task_type: 'tlc', schema_version: 'tlc-result-1', model_id: 'tlc-model', model_version: 'v1', artifact_sha256: 'a'.repeat(64),
  ensemble_weights: { rf: 1 }, mobile_phase_original: { hexane: 0.5, ethyl_acetate: 0.5 }, mobile_phase_normalized: { hexane: 0.5, ethyl_acetate: 0.5 },
  rows: [{ rf: 0.22 }], applicability: { supported_scope: 'author ensemble', warnings: [] }, metrics: [], visualizations: [],
}

const comparisonResult: TlcConditionComparisonResult = {
  task_type: 'tlc_condition_comparison', schema_version: 'tlc-result-1', structure_ref: 'str-1', model_id: 'tlc-model', model_version: 'v1', artifact_sha256: 'a'.repeat(64),
  results: [{ condition_index: 0, predicted_rf: 0.22 }],
}

const reverseResult: TlcReverseResult = {
  task_type: 'tlc_reverse_solvent', schema_version: 'tlc-result-1', structure_ref: 'str-1', model_id: 'tlc-model', model_version: 'v1', artifact_sha256: 'a'.repeat(64),
  candidates: [], evaluated: 3, failed: 0, target_reachable: false, warnings: ['TARGET_UNREACHABLE: no directly predicted solvent point reaches target Rf'],
}

describe('TLC result discrimination and restoration', () => {
  beforeEach(() => {
    setActivePinia(createPinia()); vi.restoreAllMocks()
    const values = new Map<string, string>()
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) } })
  })

  it('accepts the three server result discriminators', () => {
    expect(resultMetadataError(singleResult, 'tlc')).toBeNull()
    expect(resultMetadataError(comparisonResult, 'tlc')).toBeNull()
    expect(resultMetadataError(reverseResult, 'tlc')).toBeNull()
  })

  it('keeps a valid unreachable reverse result without inventing candidates', () => {
    expect(reverseResult.target_reachable).toBe(false)
    expect(reverseResult.candidates).toEqual([])
    expect(reverseResult.warnings).toContain('TARGET_UNREACHABLE: no directly predicted solvent point reaches target Rf')
  })

  it('fails closed for a mismatched or unknown discriminator', () => {
    expect(resultMetadataError({ ...singleResult, task_type: 'cc' } as never, 'tlc')).toContain('不匹配')
    expect(resultMetadataError({ ...singleResult, task_type: 'tlc_future' } as never, 'tlc')).toContain('不匹配')
  })

  it('restores a workspace-bound single result with a read-only GET', async () => {
    const prediction = { id: 'pred-1', project_id: 'project-1', chromatography_type: 'tlc', tool: 'single', status: 'SUCCEEDED', result: singleResult } as unknown as ContractPrediction
    const requests: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => { requests.push(String(input)); return new Response(JSON.stringify({ data: prediction, request_id: 'test', warnings: [] }), { status: 200, headers: { 'content-type': 'application/json' } }) })
    const store = usePredictionStore()
    await expect(store.getPrediction('pred-1', () => true, 'project-1', 'tlc', (loaded) => loaded.id === 'pred-1' && loaded.tool === 'single')).resolves.toMatchObject({ id: 'pred-1', status: 'SUCCEEDED' })
    expect(store.prediction?.result?.task_type).toBe('tlc')
    expect(requests).toEqual(['/api/v1/predictions/pred-1'])
  })

})
