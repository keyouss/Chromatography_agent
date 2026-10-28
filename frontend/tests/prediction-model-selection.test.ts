import { describe, expect, it } from 'vitest'
import {
  CandidateModelSelectionError,
  createExactModelSelection,
  exactModelIdentityMatches,
  type PredictionCandidateDraftExactModelSelection,
  type PredictionCandidateDraftSourceModelSnapshot,
  type PredictionCandidateModelOption,
  type PredictionCandidateModelSnapshot,
} from '@/shared/prediction/model-selection'

const sha256 = 'a'.repeat(64)

function model(overrides: Record<string, unknown> = {}): PredictionCandidateModelOption {
  return {
    id: 'model-cc', version: 'v1', task_type: 'cc', scope: 'single',
    input_schema_version: '1.0', output_schema_version: '1.0',
    health: { status: 'READY', reason: null, checked_at: '2026-10-07T00:00:00Z' },
    ...overrides,
  } as PredictionCandidateModelOption
}

function snapshot(overrides: Record<string, unknown> = {}): PredictionCandidateModelSnapshot {
  return {
    id: 'model-cc', version: 'v1', business_scope_id: 'scope-cc', artifact_sha256: sha256,
    input_schema_version: '1.0', output_schema_version: '1.0',
    ...overrides,
  } as PredictionCandidateModelSnapshot
}

describe('rev12 exact model selection helper', () => {
  it('requires and exactly copies the paired profile for CC without trimming identity', () => {
    const option = model({
      id: ' model-cc ', version: ' v1 ',
      preprocessing_profile_id: ' profile-324 ', preprocessing_profile_sha256: sha256,
    })
    expect(createExactModelSelection(option, 'cc')).toEqual({
      mode: 'exact', model_id: ' model-cc ', model_version: ' v1 ',
      preprocessing_profile_id: ' profile-324 ', preprocessing_profile_sha256: sha256,
    })
  })

  it('rejects profileless CC execution with a safe recreate-draft message', () => {
    expect(() => createExactModelSelection(model(), 'cc')).toThrowError(
      '此 CC 草稿没有绑定预处理版本，请重新创建草稿后再提交。',
    )
  })

  it('keeps profileless non-CC identity unchanged and passes through a valid declared pair', () => {
    expect(createExactModelSelection(model(), 'hplc')).toEqual({
      mode: 'exact', model_id: 'model-cc', model_version: 'v1',
    })
    expect(createExactModelSelection(model({
      preprocessing_profile_id: 'other-profile', preprocessing_profile_sha256: sha256,
    }), 'hplc')).toEqual({
      mode: 'exact', model_id: 'model-cc', model_version: 'v1',
      preprocessing_profile_id: 'other-profile', preprocessing_profile_sha256: sha256,
    })
  })

  it.each([
    [{ preprocessing_profile_id: 'profile' }, 'INVALID_PROFILE_PAIR'],
    [{ preprocessing_profile_sha256: sha256 }, 'INVALID_PROFILE_PAIR'],
    [{ preprocessing_profile_id: 'profile', preprocessing_profile_sha256: 'A'.repeat(64) }, 'INVALID_PROFILE_PAIR'],
    [{ preprocessing_profile_id: 'p'.repeat(97), preprocessing_profile_sha256: sha256 }, 'INVALID_PROFILE_PAIR'],
    [{ id: 'm'.repeat(97), preprocessing_profile_id: 'profile', preprocessing_profile_sha256: sha256 }, 'INVALID_MODEL_IDENTITY'],
    [{ version: 'v'.repeat(129), preprocessing_profile_id: 'profile', preprocessing_profile_sha256: sha256 }, 'INVALID_MODEL_IDENTITY'],
  ])('rejects invalid OAS identity/profile fields %#', (overrides, code) => {
    try {
      createExactModelSelection(model(overrides), 'hplc')
      throw new Error('Expected invalid selection to be rejected')
    } catch (error) {
      expect(error).toBeInstanceOf(CandidateModelSelectionError)
      expect((error as CandidateModelSelectionError).code).toBe(code)
    }
  })

  it('compares complete model identity across live options and persisted snapshots', () => {
    const selection: PredictionCandidateDraftExactModelSelection = {
      mode: 'exact', model_id: 'model-cc', model_version: 'v1',
      preprocessing_profile_id: 'profile-324', preprocessing_profile_sha256: sha256,
    }
    const source: PredictionCandidateDraftSourceModelSnapshot = {
      id: 'model-cc', version: 'v1', business_scope_id: 'scope-cc', artifact_sha256: sha256,
      input_schema_version: '1.0', output_schema_version: '1.0',
      preprocessing_profile_id: 'profile-324', preprocessing_profile_sha256: sha256,
    }
    expect(exactModelIdentityMatches(selection, source)).toBe(true)
    expect(exactModelIdentityMatches(selection, snapshot({ preprocessing_profile_id: 'profile-other', preprocessing_profile_sha256: sha256 }))).toBe(false)
    expect(exactModelIdentityMatches(selection, model({ preprocessing_profile_id: 'profile-324', preprocessing_profile_sha256: 'b'.repeat(64) }))).toBe(false)
  })

  it('recognizes a legacy profileless identity for read-only matching but does not make it executable for CC', () => {
    const legacySelection: PredictionCandidateDraftExactModelSelection = {
      mode: 'exact', model_id: 'model-cc', model_version: 'v1',
    }
    expect(exactModelIdentityMatches(legacySelection, snapshot())).toBe(true)
    expect(() => createExactModelSelection(model(), 'cc')).toThrowError(CandidateModelSelectionError)
  })
})
