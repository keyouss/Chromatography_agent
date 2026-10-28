import { describe, expect, it } from 'vitest'
import { projectSelectionState, resolveProjectId, requiresProjectSelection } from '@/core/project-context'
import { assistantRegistrationDraftPath, assistantToolGroup } from '@/modules/assistant/store'
import { areDistinctStructureRefs } from '@/modules/prediction/input-validation'
import { firstTlcReverseRatioStep, isTlcReverseRatioStepAllowed, isTlcSolventId, parseTlcFraction } from '@/modules/prediction/forms/tlc/types'
import { gcModelSnapshotError, gcPredictionModelSnapshotError, isPredictionTerminalStatus, resultMetadataError } from '@/modules/prediction/result'
import type { AccessContextView } from '@/core/types'
import type { FrozenComponents } from '@/core/contract-types'

type IsOptional<T, K extends keyof T> = {} extends Pick<T, K> ? true : false
type Assert<T extends true> = T
type AssertFalse<T extends false> = T

// Keep generated TypeScript optionality aligned with the formal OpenAPI
// `required` arrays, including properties that also declare a default.
export type FrozenContractRequirednessChecks = [
  Assert<IsOptional<FrozenComponents['SfcCandidateScopeCreate'], 'max_candidates'>>,
  AssertFalse<IsOptional<FrozenComponents['SfcCandidateScopeCreate'], 'axes'>>,
  Assert<IsOptional<FrozenComponents['SfcPredictionResult'], 'score_semantics'>>,
  AssertFalse<IsOptional<FrozenComponents['PredictionDraftView'], 'id'>>,
  AssertFalse<IsOptional<FrozenComponents['CcResultRow'], 'time_seconds'>>,
  AssertFalse<IsOptional<FrozenComponents['ApprovalDecision'], 'expected_revision'>>,
  Assert<IsOptional<FrozenComponents['ApprovalRequest'], 'revision'>>,
  Assert<IsOptional<FrozenComponents['ApprovalRequest'], 'etag'>>,
  Assert<IsOptional<FrozenComponents['DeploymentDraft'], 'etag'>>,
]

const context = (selection: AccessContextView['project_selection'], projects: string[], defaultProjectId?: string): AccessContextView => ({
  actor: { id: 'actor', auth_mode: 'deployment_default', policy_version: '1' },
  actor_id: 'actor', auth_mode: 'deployment_default', allowed_actions: [], is_multi_user_authenticated: false,
  permissions: [], project_selection: selection, accessible_projects: projects.map((id) => ({ id, name: id })), default_project_id: defaultProjectId,
})

describe('rev04 contract migration', () => {
  it('uses explicit then server default and blocks needs-selection/no-access', () => {
    expect(resolveProjectId(context('resolved', ['p1', 'p2'], 'p2'), 'p1')).toBe('p1')
    expect(resolveProjectId(context('resolved', ['p1', 'p2'], 'p2'))).toBe('p2')
    expect(resolveProjectId(context('needs_selection', ['p1', 'p2'], 'p2'))).toBeNull()
    expect(resolveProjectId(context('needs_selection', ['p1', 'p2']), 'p1')).toBe('p1')
    expect(requiresProjectSelection(context('needs_selection', ['p1', 'p2']))).toBe(true)
    expect(projectSelectionState(context('resolved', ['p1'], 'p1'))).toBe('resolved')
    expect(projectSelectionState(context('needs_selection', ['p1', 'p2'], 'p2'))).toBe('needs_selection')
    expect(projectSelectionState(context('needs_selection', ['p1', 'p2']), 'p2')).toBe('resolved')
    expect(projectSelectionState(context('no_accessible_projects', []))).toBe('blocked')
    expect(resolveProjectId(context('no_accessible_projects', []))).toBeNull()
    expect(resolveProjectId(null)).toBeNull()
  })

  it('requires two distinct CC structures', () => {
    expect(areDistinctStructureRefs(['s1', 's2'])).toBe(true)
    expect(areDistinctStructureRefs(['s1', 's1'])).toBe(false)
    expect(areDistinctStructureRefs(['s1', null])).toBe(false)
  })

  it('normalizes UI ratios explicitly and rejects frozen-invalid solvents', () => {
    expect(parseTlcFraction('8:2')).toBeCloseTo(0.8)
    expect(parseTlcFraction('80:20')).toBeCloseTo(0.8)
    expect(isTlcSolventId('hexane')).toBe(true)
    expect(isTlcSolventId('petroleum_ether')).toBe(false)
  })

  it('chooses a legal non-empty TLC reverse step when zero is exclusive', () => {
    const range = { min: 0, max: 1, min_inclusive: false, max_inclusive: true }
    const value = firstTlcReverseRatioStep(range)
    expect(value).toBe(0.5)
    expect(isTlcReverseRatioStepAllowed(value, range)).toBe(true)
  })


  it('checks base metadata for CC, HPLC and GC results', () => {
    const applicability: FrozenComponents['Applicability'] = { supported_scope: 'single' }
    const cc: FrozenComponents['CcPredictionResult'] = { task_type: 'cc', schema_version: 'cc-v1', rows: [], applicability }
    const hplc: FrozenComponents['HplcPredictionResult'] = { task_type: 'hplc', schema_version: 'hplc-v1', rows: [], applicability }
    const gc: FrozenComponents['GcPredictionResult'] = { task_type: 'gc', schema_version: 'gc-v1', rows: [], applicability }
    expect(resultMetadataError({ ...cc, schema_version: '' }, 'cc')).not.toBeNull()
    expect(resultMetadataError({ ...hplc, applicability: { supported_scope: '' } }, 'hplc')).toContain('applicability')
    expect(resultMetadataError({ ...gc, schema_version: '' }, 'gc')).not.toBeNull()
  })

  it('validates HPLC result DTO fields without requiring duplicated model identity', () => {
    const result = (rows: unknown[], applicability: unknown = { supported_scope: 'HPLC' }) => ({
      task_type: 'hplc', schema_version: 'hplc-geognn-1', rows, applicability,
    })
    expect(resultMetadataError(result([{ candidate_id: 'ADH', separation: 1 }]) as never, 'hplc')).toBeNull()
    expect(resultMetadataError(result([{ candidate_id: 'IA', separation: null }]) as never, 'hplc')).toBeNull()
    expect(resultMetadataError(result([{ candidate_id: '' }]) as never, 'hplc')).toContain('HPLC 结果')
    expect(resultMetadataError(result([{ candidate_id: 'ADH', separation: Number.POSITIVE_INFINITY }]) as never, 'hplc')).toContain('HPLC 结果')
    expect(resultMetadataError(result([{ candidate_id: 'ADH' }], { supported_scope: '' }) as never, 'hplc')).toContain('HPLC 结果')
  })

  it('validates GC rows against the public DTO without requiring invented model metadata or quantiles', () => {
    const result = (rows: unknown[], applicability: unknown = { supported_scope: 'GC' }) => ({
      task_type: 'gc', schema_version: 'gc-retention-time-1', rows, applicability,
    })
    expect(resultMetadataError(result([{ retention_time_min: 0, quantiles: { q10: 0, q50: 1.2, q90: 2.4 } }]) as never, 'gc')).toBeNull()
    expect(resultMetadataError(result([{ retention_time_min: 5 }]) as never, 'gc')).toBeNull()
    expect(resultMetadataError(result([{ quantiles: { q50: 1 } }]) as never, 'gc')).toContain('GC 结果')
    expect(resultMetadataError(result([{ retention_time_min: -0.1 }]) as never, 'gc')).toContain('GC 结果')
    expect(resultMetadataError(result([{ retention_time_min: Number.POSITIVE_INFINITY }]) as never, 'gc')).toContain('GC 结果')
    expect(resultMetadataError(result([{ retention_time_min: 1, quantiles: { q50: Number.NaN } }]) as never, 'gc')).toContain('GC 结果')
    expect(resultMetadataError(result([{ retention_time_min: 1, quantiles: [] }]) as never, 'gc')).toContain('GC 结果')
    expect(resultMetadataError(result([{ retention_time_min: 1, model_id: 'invented' }]) as never, 'gc')).toContain('GC 结果')
  })

  it('validates GC historical model snapshots including only complete optional profile pairs', () => {
    const snapshot = {
      id: 'gc-model', version: 'gc-v1', business_scope_id: 'gc-default', artifact_sha256: 'a'.repeat(64),
      input_schema_version: '1.0', output_schema_version: 'gc-retention-time-1',
    }
    expect(gcModelSnapshotError(snapshot)).toBeNull()
    expect(gcModelSnapshotError({ ...snapshot, preprocessing_profile_id: null, preprocessing_profile_sha256: null })).toBeNull()
    expect(gcModelSnapshotError({ ...snapshot, preprocessing_profile_id: 'gc-profile', preprocessing_profile_sha256: 'b'.repeat(64) })).toBeNull()
    expect(gcModelSnapshotError({ ...snapshot, preprocessing_profile_id: 'gc-profile' })).toContain('预处理版本身份不完整')
    expect(gcModelSnapshotError({ ...snapshot, preprocessing_profile_id: null, preprocessing_profile_sha256: 'b'.repeat(64) })).toContain('预处理版本身份格式无效')
    expect(gcModelSnapshotError({ ...snapshot, id: '' })).toContain('身份字段')
    expect(gcModelSnapshotError({ ...snapshot, artifact_sha256: 'A'.repeat(64) })).toContain('身份字段')
    expect(gcModelSnapshotError({ ...snapshot, business_scope_id: ' gc-default' })).toContain('身份字段')
    expect(gcPredictionModelSnapshotError({ ...snapshot, preprocessing_profile_id: null, preprocessing_profile_sha256: null }, '1.0')).toBeNull()
    expect(gcPredictionModelSnapshotError(snapshot, '1.1')).toContain('输入 schema')
    expect(gcPredictionModelSnapshotError({ ...snapshot, input_schema_version: '1.1' }, '1.1')).toContain('预处理版本')
    expect(gcPredictionModelSnapshotError({ ...snapshot, input_schema_version: '1.1', preprocessing_profile_id: 'gc-profile', preprocessing_profile_sha256: 'b'.repeat(64) }, '1.1')).toBeNull()
    expect(gcPredictionModelSnapshotError({ ...snapshot, input_schema_version: '1.1', preprocessing_profile_id: 'gc-profile', preprocessing_profile_sha256: 'B'.repeat(64) }, '1.1')).toContain('预处理版本')
  })

  it('validates CC result rows against the frozen three-field and derived-time contract', () => {
    const result = (rows: unknown[]) => ({ task_type: 'cc', schema_version: 'cc-v1', rows, applicability: { supported_scope: 'column chromatography' } })
    const check = (rows: unknown[]) => resultMetadataError(result(rows) as never, 'cc', 'cc-v1')
    const validRow = { volume_ml: 2, flow_rate_ml_min: 3, time_seconds: 40 }
    expect(check([validRow])).toBeNull()
    expect(check([])).toBeNull()
    expect(check([{ ...validRow, time_seconds: 40.00001 }])).toBeNull()
    expect(check([{}])).toContain('CC 结果行')
    expect(check([{ volume_ml: 2, flow_rate_ml_min: 3 }])).toContain('CC 结果行')
    expect(check([{ volume_ml: 2, flow_rate_ml_min: 3, time_seconds: 40, old_prediction: 0.91, absolute_path: 'C:/private/model.pth' }])).toContain('CC 结果行')
    expect(check([{ ...validRow, volume_ml: Number.NaN }])).toContain('CC 结果行')
    expect(check([{ ...validRow, volume_ml: Number.POSITIVE_INFINITY }])).toContain('CC 结果行')
    expect(check([{ ...validRow, volume_ml: 0 }])).toContain('CC 结果行')
    expect(check([{ ...validRow, flow_rate_ml_min: Number.NEGATIVE_INFINITY }])).toContain('CC 结果行')
    expect(check([{ ...validRow, flow_rate_ml_min: 0 }])).toContain('CC 结果行')
    expect(check([{ ...validRow, time_seconds: Number.NaN }])).toContain('CC 结果行')
    expect(check([{ ...validRow, time_seconds: Number.POSITIVE_INFINITY }])).toContain('CC 结果行')
    expect(check([{ ...validRow, time_seconds: 41 }])).toContain('CC 结果行')
  })

  it('checks TLC/SFC specialized result metadata', () => {
    const applicability: FrozenComponents['Applicability'] = { supported_scope: 'single' }
    const tlc: FrozenComponents['TlcPredictionResult'] = {
      task_type: 'tlc', schema_version: 'tlc-v1', rows: [], applicability, model_id: 'm', model_version: 'v', artifact_sha256: 'sha',
      mobile_phase_original: { hexane: 0.8 }, mobile_phase_normalized: { hexane: 0.8 }, ensemble_weights: { rf: 1 },
    }
    const sfc: FrozenComponents['SfcPredictionResult'] = { task_type: 'sfc', schema_version: 'sfc-v1', rows: [], applicability, score_semantics: 'positive_class_probability' }
    expect(resultMetadataError({ ...tlc, artifact_sha256: '' }, 'tlc')).toContain('TLC')
    expect(resultMetadataError({ ...sfc, score_semantics: '' as 'positive_class_probability' }, 'sfc')).toContain('SFC')
  })

  it('blocks missing result and recognizes every frozen prediction terminal status', () => {
    expect(resultMetadataError(null, 'tlc')).toContain('未返回正式结果')
    const terminalStatuses = ['SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED'] as const
    expect(terminalStatuses.every((status) => isPredictionTerminalStatus(status))).toBe(true)
    expect(isPredictionTerminalStatus('RUNNING')).toBe(false)
    expect(assistantToolGroup('get_prediction_record_summary')).toBe('data')
    expect(assistantToolGroup('get_registration_draft')).toBe('model')
    expect(assistantToolGroup('get_data_summary')).toBeNull()
    expect(assistantRegistrationDraftPath('reg/1', 'project 1')).toBe('/assistant/priority/model-registration-drafts/reg%2F1?project_id=project%201')
  })
})
